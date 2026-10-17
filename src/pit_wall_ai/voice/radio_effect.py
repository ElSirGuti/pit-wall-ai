"""The sound of a team radio, applied to any synthesised voice (``docs/VOICES_PLAN.md`` section 4).

numpy only. A band-pass for the narrow channel, a soft compressor, a light bit-crush, low noise that fades in and out
with the transmission and a short squelch click at both ends. It also hides small synthesis artefacts. It works on
audio that has already been rendered, so it adds a few milliseconds, not a synthesis.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class RadioProfile:
    low_hz: float
    high_hz: float
    threshold: float  # compressor knee, 0..1 of full scale
    ratio: float
    bits: int  # bit-crush resolution
    noise_db: float  # level of the noise bed against full scale
    click_db: float  # level of the squelch bursts
    lead_ms: int = 70  # radio silence-with-noise before the first word
    tail_ms: int = 140  # and after the last


PROFILES = {
    "light": RadioProfile(250, 3800, 0.45, 2.5, 12, -52.0, -26.0),
    "strong": RadioProfile(380, 3000, 0.25, 6.0, 8, -38.0, -16.0),
}


def band_pass(x: np.ndarray, rate: int, low_hz: float, high_hz: float) -> np.ndarray:
    """A smooth (second order) band-pass done in the frequency domain: a clip is short, so it is cheap."""
    n = len(x)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / rate)
    f = np.maximum(f, 1e-6)
    gain = 1.0 / (1.0 + (low_hz / f) ** 4) / (1.0 + (f / high_hz) ** 4)
    return np.fft.irfft(spec * gain, n)


def compress(x: np.ndarray, threshold: float, ratio: float) -> np.ndarray:
    """Soft compression above the knee, then the peak is brought back to 0.9 of full scale."""
    a = np.abs(x)
    over = a > threshold
    y = np.where(over, np.sign(x) * (threshold + (a - threshold) / ratio), x)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    return y * (0.9 / peak) if peak > 1e-9 else y


def bit_crush(x: np.ndarray, bits: int) -> np.ndarray:
    q = 2.0 ** (bits - 1)
    return np.round(x * q) / q


def _db(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _noise(
    n: int, rng: np.random.Generator, rate: int, low_hz: float, high_hz: float
) -> np.ndarray:
    return band_pass(rng.standard_normal(n), rate, low_hz, high_hz)


def squelch(
    rate: int, ms: int, level: float, rng: np.random.Generator, prof: RadioProfile
) -> np.ndarray:
    """A short burst of band-limited noise with a fast decay: the click of keying the radio."""
    n = max(1, int(rate * ms / 1000))
    burst = _noise(n, rng, rate, prof.low_hz, prof.high_hz)
    burst = burst / (float(np.max(np.abs(burst))) or 1.0)
    return burst * np.exp(-np.linspace(0, 7, n)) * level


def apply_radio(
    pcm: np.ndarray, rate: int, strength: str = "light", *, seed: int | None = None
) -> np.ndarray:
    """``pcm`` (int16 or float) -> int16 with the radio sound; ``strength`` is "light" or "strong"."""
    prof = PROFILES.get(strength, PROFILES["light"])
    rng = np.random.default_rng(seed)
    x = pcm.astype(np.float64)
    if pcm.dtype == np.int16:
        x /= 32768.0
    x = band_pass(x, rate, prof.low_hz, prof.high_hz)
    x = compress(x, prof.threshold, prof.ratio)
    x = bit_crush(x, prof.bits)
    lead = int(rate * prof.lead_ms / 1000)
    tail = int(rate * prof.tail_ms / 1000)
    out = np.concatenate([np.zeros(lead), x, np.zeros(tail)])
    # the noise bed lives while the transmission is open: it fades in over the lead and out over the tail
    bed = _noise(len(out), rng, rate, prof.low_hz, prof.high_hz) * _db(prof.noise_db)
    env = np.ones(len(out))
    env[:lead] = np.linspace(0.0, 1.0, lead, endpoint=False) if lead else env[:0]
    env[len(out) - tail :] = np.linspace(1.0, 0.0, tail) if tail else env[:0]
    out = out + bed * env
    click = _db(prof.click_db)
    open_click = squelch(rate, 28, click, rng, prof)
    close_click = squelch(rate, 45, click * 0.8, rng, prof)
    out[: len(open_click)] += open_click[: len(out)]
    out[len(out) - len(close_click) :] += close_click
    peak = float(np.max(np.abs(out)))
    if peak > 0.98:
        out = out * (0.98 / peak)
    return np.round(out * 32767.0).astype(np.int16)


def apply_radio_wav(src: str | Path, dst: str | Path, strength: str = "light") -> float:
    """Read a mono 16-bit WAV, write the radio version; returns the seconds of audio written."""
    with wave.open(str(src), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("only 16-bit WAV files are supported")
    pcm = np.frombuffer(data, dtype=np.int16)
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    out = apply_radio(pcm, rate, strength)
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(out.tobytes())
    return len(out) / float(rate)
