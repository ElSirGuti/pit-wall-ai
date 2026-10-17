"""Voices that are rendered to a WAV first: the radio effect, Piper, any command, and a cache of the fixed phrases.

SAPI speaks straight to the output device and stays the default. When the radio effect is on, or the engine is
Piper or a command of the author's own, the sentence is rendered to a WAV, optionally given the radio sound, and
played through the chosen output device (``sounddevice``, like the microphone). The frequent phrases (the fixed
alert texts, "un momento", the milestone variants) are rendered once per voice and kept in a local cache, so the next
time they are a file play. See ``docs/VOICES_PLAN.md``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np

from .radio_effect import apply_radio
from .tts import TtsUnavailable

log = logging.getLogger(__name__)
DEFAULT_CACHE = Path.home() / ".pit-wall-ai" / "voice-cache"
PIPER_VOICES = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


# -- rendering a command ------------------------------------------------------------------------------------
def render_command(command: str, text: str, out: str, *, timeout_s: float = 15.0) -> None:
    """Run a synthesiser template (``{text}``, ``{stdin}``, ``{out}``) and leave a WAV in ``out``."""
    stdin = None
    argv: list[str] = []
    for part in shlex.split(command, posix=False):
        part = part.strip('"')
        if part == "{stdin}":
            stdin = text
            continue
        argv.append(part.replace("{out}", out).replace("{text}", text))
    try:
        subprocess.run(  # noqa: S603 - the command is the user's own configuration
            argv, input=stdin, text=True, check=True, capture_output=True, timeout=timeout_s
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TtsUnavailable(f"the voice command failed: {exc}") from exc


def piper_command(model: str, exe: str = "piper") -> str:
    """The command template that makes Piper write a WAV from the text on standard input."""
    return f'"{exe}" --model "{model}" --output_file {{out}} {{stdin}}'


def piper_model_urls(voice_id: str) -> tuple[str, str]:
    """(.onnx, .onnx.json) of a Piper voice such as ``es_ES-davefx-medium`` on the public voices repository."""
    try:
        lang_region, name, quality = voice_id.split("-", 2)
        lang = lang_region.split("_")[0]
    except ValueError as exc:
        raise ValueError(
            f"{voice_id!r} is not a Piper voice id (e.g. es_ES-davefx-medium)"
        ) from exc
    base = f"{PIPER_VOICES}/{lang}/{lang_region}/{name}/{quality}/{voice_id}"
    return base + ".onnx", base + ".onnx.json"


def download_piper_voice(voice_id: str, dest: str | Path, *, opener=urllib.request.urlopen) -> Path:
    """Fetch a Piper voice (two files) into ``dest``; returns the path of the model."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for url in piper_model_urls(voice_id):
        target = dest / url.rsplit("/", 1)[1]
        if target.exists() and target.stat().st_size > 0:
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        with opener(url, timeout=60) as r, tmp.open("wb") as fh:  # noqa: S310 - a fixed https host
            while chunk := r.read(1 << 16):
                fh.write(chunk)
        tmp.replace(target)
    return dest / f"{voice_id}.onnx"


# -- the cache -----------------------------------------------------------------------------------------------
class PhraseCache:
    def __init__(self, folder: str | Path | None = None) -> None:
        self.folder = Path(folder) if folder else DEFAULT_CACHE

    def path(self, key: str) -> Path:
        return self.folder / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:24] + ".wav")

    def get(self, key: str) -> Path | None:
        p = self.path(key)
        return p if p.is_file() and p.stat().st_size > 44 else None

    def slot(self, key: str) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        return self.path(key)


# -- the backend ---------------------------------------------------------------------------------------------
def read_pcm(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate, ch = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return data, rate


def write_pcm(path: str | Path, pcm: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())


class WavVoice:
    """A backend that renders to WAV (``render(text, path)``), adds the radio effect and plays it."""

    name = "wav"

    def __init__(
        self,
        render: Callable[[str, str], None],
        *,
        effect: str = "off",
        cache: PhraseCache | None = None,
        key_prefix: str = "",
        device: str | None = None,
        volume: int = 100,
        player: Callable[[np.ndarray, int], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.render, self.effect, self.cache = render, effect, cache
        self.key_prefix, self.device, self.volume = key_prefix, device, volume
        self._player = player or self._play_sounddevice
        self._clock = clock
        self._until = 0.0
        self._tmp = tempfile.mkdtemp(prefix="pitwall-voice-")
        self._n = 0
        self.rendered = 0  # how many sentences had to be synthesised (the cache hits do not count)

    def open(self) -> None:
        pass

    def _key(self, text: str) -> str:
        return f"{self.key_prefix}|{self.effect}|{text}"

    def wav_for(self, text: str) -> Path:
        """The finished WAV of a sentence (effect applied): from the cache when it is there."""
        if self.cache is not None:
            hit = self.cache.get(self._key(text))
            if hit is not None:
                return hit
        self._n += 1
        raw = os.path.join(self._tmp, f"r{self._n % 8}.wav")
        self.render(text, raw)
        self.rendered += 1
        pcm, rate = read_pcm(raw)
        if self.effect != "off":
            pcm = apply_radio(pcm, rate, self.effect)
        final = self.cache.slot(self._key(text)) if self.cache is not None else Path(raw)
        write_pcm(final, pcm, rate)
        return final

    def prerender(self, texts: Iterable[str]) -> tuple[int, int]:
        """Render the fixed phrases ahead of time; returns (rendered, already there)."""
        done = had = 0
        for t in dict.fromkeys(texts):
            if self.cache is not None and self.cache.get(self._key(t)) is not None:
                had += 1
                continue
            self.wav_for(t)
            done += 1
        return done, had

    def speak(self, text: str, *, interrupt: bool = False) -> None:
        if interrupt:
            self.stop()
        pcm, rate = read_pcm(self.wav_for(text))
        if self.volume < 100:
            pcm = (pcm.astype(np.float64) * self.volume / 100.0).astype(np.int16)
        self._until = self._clock() + len(pcm) / float(rate)
        self._player(pcm, rate)

    def is_speaking(self) -> bool:
        return self._clock() < self._until

    def stop(self) -> None:
        self._until = 0.0
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:  # noqa: BLE001 - no sounddevice, or nothing playing
            if sys.platform == "win32":
                try:
                    import winsound

                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:  # noqa: BLE001, S110
                    pass

    def close(self) -> None:
        self.stop()

    # -- playback -----------------------------------------------------------------------------------------
    def _play_sounddevice(self, pcm: np.ndarray, rate: int) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise TtsUnavailable(
                'sounddevice is not installed: pip install "pit-wall-ai[voice]"'
            ) from exc
        index = None
        info = None
        if self.device:
            q = self.device.lower()
            for i, d in enumerate(sd.query_devices()):
                if d["max_output_channels"] > 0 and q in d["name"].lower():
                    index, info = i, d
                    break
            else:
                raise TtsUnavailable(f"no audio output matches {self.device!r}")
        if info is None:
            info = sd.query_devices(index if index is not None else sd.default.device[1], "output")
        target = int(info["default_samplerate"])
        data = pcm.astype(np.float32) / 32768.0
        if target != rate:  # a shared-mode output may refuse 16 kHz: play at its own rate
            n = int(len(data) * target / rate)
            data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype(
                np.float32
            )
        sd.play(data, target, device=index)
