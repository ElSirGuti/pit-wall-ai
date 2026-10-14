"""Microphone capture for push-to-talk: a short pre-roll, the held audio, a short tail.

The stream is opened once and stays open (so the first syllable is not lost to the device's start-up time);
only the last ``preroll_ms`` are kept while nobody is talking, and **nothing is stored or sent** until the
button goes down. Audio is 16 kHz mono 16-bit, what speech recognisers expect.

The audio library hides behind :class:`AudioBackend` so everything here is tested with fake frames.
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from .devices import AudioDevice, DeviceLister

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000


@dataclass(frozen=True)
class Utterance:
    pcm: np.ndarray  # int16, mono, SAMPLE_RATE
    rate: int = SAMPLE_RATE

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / self.rate

    @property
    def peak(self) -> float:
        """0..1 of full scale."""
        return float(np.max(np.abs(self.pcm.astype(np.int32)))) / 32768.0 if len(self.pcm) else 0.0

    @property
    def rms(self) -> float:
        if not len(self.pcm):
            return 0.0
        return float(np.sqrt(np.mean(np.square(self.pcm.astype(np.float64))))) / 32768.0


class AudioBackend(DeviceLister, Protocol):
    def open_input(
        self,
        device: AudioDevice | None,
        rate: int,
        block: int,
        callback: Callable[[np.ndarray], None],
    ) -> StreamHandle: ...


class StreamHandle(Protocol):
    def close(self) -> None: ...

    @property
    def rate(self) -> int: ...


class SoundDeviceBackend:
    """PortAudio through ``sounddevice`` (the optional ``voice`` extra)."""

    def __init__(self) -> None:
        import sounddevice as sd

        self._sd = sd

    def devices(self) -> list[AudioDevice]:
        sd = self._sd
        apis = {i: a["name"] for i, a in enumerate(sd.query_hostapis())}
        return [
            AudioDevice(
                index=i,
                name=d["name"],
                hostapi=apis.get(d["hostapi"], "?"),
                max_input=int(d["max_input_channels"]),
                max_output=int(d["max_output_channels"]),
                default_rate=float(d["default_samplerate"]),
            )
            for i, d in enumerate(sd.query_devices())
        ]

    def open_input(self, device, rate, block, callback):
        sd = self._sd
        idx = None if device is None else device.index
        tried = []

        def make_cb(src_rate: int):
            def cb(data, frames, t, status):
                mono = data[:, 0]
                callback(mono.copy() if src_rate == rate else _resample(mono, src_rate, rate))

            return cb

        for r in dict.fromkeys([rate, int(device.default_rate) if device else 44100]):
            try:
                stream = sd.InputStream(
                    device=idx,
                    samplerate=r,
                    channels=1,
                    dtype="int16",
                    blocksize=int(r * block / rate),
                    callback=make_cb(r),
                )
                stream.start()
                return _Handle(stream, r)
            except Exception as exc:  # noqa: BLE001 - try the next rate, report all of them if none works
                tried.append(f"{r} Hz: {exc}")
        raise OSError("could not open the microphone: " + "; ".join(tried))


class _Handle:
    def __init__(self, stream, rate: int) -> None:
        self._stream = stream
        self._rate = rate

    @property
    def rate(self) -> int:
        return self._rate

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            log.exception("closing the microphone failed")


def _resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear resampling with a short moving average first (enough for speech at 16 kHz)."""
    if src == dst or not len(x):
        return x.astype(np.int16)
    xf = x.astype(np.float32)
    if src > dst:
        k = max(1, round(src / dst))
        xf = np.convolve(xf, np.ones(k, dtype=np.float32) / k, mode="same")
    n = int(len(xf) * dst / src)
    out = np.interp(np.linspace(0, len(xf) - 1, n), np.arange(len(xf)), xf)
    return np.clip(out, -32768, 32767).astype(np.int16)


@dataclass
class UtteranceRecorder:
    """Feed it blocks; ``press()`` / ``release()`` bracket an utterance; ``on_utterance`` receives it.

    * the ``preroll_ms`` before the press are included, the ``tail_ms`` after the release too;
    * an utterance shorter than ``min_ms`` or without energy is dropped (an accidental touch).
    """

    on_utterance: Callable[[Utterance], None]
    rate: int = SAMPLE_RATE
    preroll_ms: int = 200
    tail_ms: int = 150
    min_ms: int = 300
    silence_rms: float = 0.002  # below this (full-scale 0..1) the whole utterance counts as silence
    max_s: float = 30.0
    _pre: deque = field(default_factory=deque)
    _held: list = field(default_factory=list)
    _state: str = "idle"  # idle | holding | tail
    _tail_left: int = 0
    _pre_samples: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    dropped: int = 0
    delivered: int = 0

    def __post_init__(self) -> None:
        self._pre_max = int(self.rate * self.preroll_ms / 1000)

    # -- audio thread --------------------------------------------------------------------------------
    def feed(self, block: np.ndarray) -> None:
        deliver = None
        with self._lock:
            if self._state == "idle":
                self._pre.append(block)
                self._pre_samples += len(block)
                while self._pre_samples - len(self._pre[0]) >= self._pre_max and len(self._pre) > 1:
                    self._pre_samples -= len(self._pre.popleft())
                return
            self._held.append(block)
            if self._state == "tail":
                self._tail_left -= len(block)
                if self._tail_left <= 0:
                    deliver = self._finish()
            elif sum(len(b) for b in self._held) > self.rate * self.max_s:
                deliver = self._finish()  # a stuck button: do not record for ever
        if deliver is not None:
            self._deliver(deliver)

    # -- button thread -----------------------------------------------------------------------------
    def press(self) -> None:
        with self._lock:
            if self._state != "idle":
                return
            self._held = list(self._pre)
            self._pre.clear()
            self._pre_samples = 0
            self._state = "holding"

    def release(self) -> None:
        with self._lock:
            if self._state == "holding":
                self._state = "tail"
                self._tail_left = int(self.rate * self.tail_ms / 1000)

    def _finish(self) -> Utterance | None:
        pcm = np.concatenate(self._held) if self._held else np.zeros(0, dtype=np.int16)
        self._held, self._state = [], "idle"
        return Utterance(pcm.astype(np.int16), self.rate)

    def _deliver(self, u: Utterance) -> None:
        if u.duration_s * 1000 < self.min_ms or u.rms < self.silence_rms:
            self.dropped += 1
            return
        self.delivered += 1
        try:
            self.on_utterance(u)
        except Exception:  # noqa: BLE001
            log.exception("utterance consumer failed")


class Microphone:
    """An open input stream feeding an :class:`UtteranceRecorder`."""

    def __init__(
        self,
        backend: AudioBackend,
        device: AudioDevice | None,
        recorder: UtteranceRecorder,
        *,
        block_ms: int = 20,
    ) -> None:
        self.backend, self.device, self.recorder = backend, device, recorder
        self.block = int(recorder.rate * block_ms / 1000)
        self._handle: StreamHandle | None = None
        self.level = 0.0  # rms of the last block, 0..1, for meters

    def start(self) -> None:
        def on_block(b: np.ndarray) -> None:
            self.level = (
                float(np.sqrt(np.mean(np.square(b.astype(np.float64))))) / 32768.0
                if len(b)
                else 0.0
            )
            self.recorder.feed(b)

        self._handle = self.backend.open_input(
            self.device, self.recorder.rate, self.block, on_block
        )

    @property
    def opened_rate(self) -> int:
        return self._handle.rate if self._handle else 0

    def stop(self) -> None:
        if self._handle:
            self._handle.close()
            self._handle = None


def write_wav(path: str | Path, u: Utterance) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(u.rate)
        w.writeframes(u.pcm.astype("<i2").tobytes())


def read_wav(path: str | Path) -> Utterance:
    with wave.open(str(path), "rb") as w:
        n, ch, sw, rate = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError("only 16-bit wav files are supported")
    pcm = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if rate != SAMPLE_RATE:
        pcm = _resample(pcm, rate, SAMPLE_RATE)
        rate = SAMPLE_RATE
    return Utterance(pcm, rate)


def wall_clock() -> float:  # a seam for tests
    return time.monotonic()
