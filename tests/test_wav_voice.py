import io

import numpy as np
import pytest

from pit_wall_ai.voice.tts import TtsUnavailable, create_backend
from pit_wall_ai.voice.wav_voice import (
    PhraseCache,
    WavVoice,
    download_piper_voice,
    piper_command,
    piper_model_urls,
    read_pcm,
    write_pcm,
)

RATE = 16000


def tone_renderer(calls):
    def render(text, path):
        calls.append(text)
        t = np.arange(int(RATE * 0.4)) / RATE
        write_pcm(path, (np.sin(2 * np.pi * 800 * t) * 8000).astype(np.int16), RATE)

    return render


class Player:
    def __init__(self):
        self.played = []

    def __call__(self, pcm, rate):
        self.played.append((pcm, rate))


def voice(tmp_path, calls, **kw):
    player = Player()
    v = WavVoice(
        tone_renderer(calls),
        cache=PhraseCache(tmp_path / "cache"),
        key_prefix="test|v",
        player=player,
        **kw,
    )
    return v, player


def test_a_phrase_is_rendered_once_and_then_comes_from_the_cache(tmp_path):
    calls = []
    v, player = voice(tmp_path, calls)
    v.speak("Un momento.")
    v.speak("Un momento.")
    assert calls == ["Un momento."] and len(player.played) == 2 and v.rendered == 1
    # a new run of the program still has it
    v2, _ = voice(tmp_path, calls)
    v2.speak("Un momento.")
    assert calls == ["Un momento."]


def test_the_radio_effect_changes_the_audio_and_is_part_of_the_cache_key(tmp_path):
    calls = []
    plain, _ = voice(tmp_path, calls, effect="off")
    radio, _ = voice(tmp_path, calls, effect="strong")
    a, _ = read_pcm(plain.wav_for("Frenos"))
    b, _ = read_pcm(radio.wav_for("Frenos"))
    assert len(b) > len(a)  # the lead and the tail of the transmission
    assert len(calls) == 2  # the other effect was not taken from the first one's cache


def test_prerender_counts_what_was_made_and_what_was_there(tmp_path):
    calls = []
    v, _ = voice(tmp_path, calls)
    assert v.prerender(["uno", "dos", "uno"]) == (2, 0)
    assert v.prerender(["uno", "tres"]) == (1, 1)


def test_the_speaking_time_follows_the_audio_and_stop_ends_it(tmp_path):
    t = [0.0]
    player = Player()
    v = WavVoice(tone_renderer([]), player=player, clock=lambda: t[0])
    v.speak("hola")
    assert v.is_speaking() and abs(v._until - 0.4) < 0.02
    t[0] = 1.0
    assert not v.is_speaking()


def test_volume_scales_the_samples(tmp_path):
    calls = []
    loud, p1 = voice(tmp_path, calls, volume=100)
    quiet, p2 = voice(tmp_path / "q", calls, volume=50)
    loud.speak("x")
    quiet.speak("x")
    assert int(np.abs(p2.played[0][0]).max()) < int(np.abs(p1.played[0][0]).max()) * 0.6


def test_piper_urls_and_command():
    onnx, js = piper_model_urls("es_ES-davefx-medium")
    assert onnx.endswith("/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx") and js.endswith(
        ".onnx.json"
    )
    assert "--model" in piper_command("C:/v/x.onnx") and "{stdin}" in piper_command("C:/v/x.onnx")
    with pytest.raises(ValueError):
        piper_model_urls("nonsense")


def test_the_voice_download_fetches_both_files_and_skips_what_is_there(tmp_path):
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(url, timeout=0):
        seen.append(url)
        return Resp(b"data")

    model = download_piper_voice("es_ES-davefx-medium", tmp_path, opener=opener)
    assert model.name == "es_ES-davefx-medium.onnx" and model.exists() and len(seen) == 2
    download_piper_voice("es_ES-davefx-medium", tmp_path, opener=opener)
    assert len(seen) == 2


def test_the_backend_factory_chooses_the_wav_voice_for_piper_and_for_the_radio_effect():
    kw = dict(language="es", voice_name=None, rate=0, volume=100)
    assert isinstance(create_backend("piper", piper_model="m.onnx", **kw), WavVoice)
    assert isinstance(create_backend("sapi", radio_effect="light", **kw), WavVoice)
    with pytest.raises(TtsUnavailable):
        create_backend("piper", **kw)  # no model
    assert not isinstance(create_backend("console", **kw), WavVoice)
