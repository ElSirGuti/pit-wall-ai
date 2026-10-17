import numpy as np

from pit_wall_ai.audio.capture import Utterance, read_audio, write_wav
from pit_wall_ai.voice.radio_effect import PROFILES, apply_radio, apply_radio_wav

RATE = 16000


def tones(freqs, seconds=1.0, amp=0.2):
    t = np.arange(int(RATE * seconds)) / RATE
    x = sum(np.sin(2 * np.pi * f * t) for f in freqs) * amp
    return (x * 32767).astype(np.int16)


def level_at(pcm, freq, rate=RATE):
    """The spectrum level (linear) of the strongest bin within 5 % of ``freq``."""
    spec = np.abs(np.fft.rfft(pcm.astype(np.float64)))
    f = np.fft.rfftfreq(len(pcm), 1.0 / rate)
    sel = (f > freq * 0.95) & (f < freq * 1.05)
    return float(spec[sel].max())


def test_the_channel_is_narrow_the_voice_band_stays_and_the_extremes_go():
    x = tones([100, 1000, 6500], seconds=1.0)
    y = apply_radio(x, RATE, "strong", seed=1)
    keep = level_at(y, 1000)
    assert level_at(y, 100) < keep * 0.12  # at least ~18 dB down
    assert level_at(y, 6500) < keep * 0.12


def test_the_transmission_is_longer_than_the_speech_and_never_clips():
    x = tones([500, 1500], seconds=0.5, amp=0.9)
    for strength in ("light", "strong"):
        y = apply_radio(x, RATE, strength, seed=2)
        p = PROFILES[strength]
        assert len(y) == len(x) + int(RATE * (p.lead_ms + p.tail_ms) / 1000)
        assert y.dtype == np.int16 and int(np.max(np.abs(y))) <= 32767


def test_the_squelch_and_the_noise_are_there_when_nothing_is_said():
    silence = np.zeros(RATE // 2, dtype=np.int16)
    light = apply_radio(silence, RATE, "light", seed=3)
    strong = apply_radio(silence, RATE, "strong", seed=3)
    start = slice(0, int(RATE * 0.03))
    middle = slice(len(light) // 2 - 200, len(light) // 2 + 200)
    assert np.abs(light[start]).max() > np.abs(light[middle]).max() * 3  # the click at the opening
    assert (
        np.abs(strong[middle]).mean() > np.abs(light[middle]).mean() * 3
    )  # the strong one hisses more


def test_a_wav_goes_in_and_a_radio_wav_comes_out(tmp_path):
    src, dst = tmp_path / "a.wav", tmp_path / "b.wav"
    write_wav(src, Utterance(pcm=tones([800], 0.4), rate=RATE))
    seconds = apply_radio_wav(src, dst, "light")
    out = read_audio(dst)
    assert abs(out.duration_s - seconds) < 0.01 and seconds > 0.4
