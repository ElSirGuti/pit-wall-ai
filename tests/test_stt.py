"""Speech recognition: word error rate, vocabulary, config, stt-test with a fake recogniser, voice names."""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pytest

from pit_wall_ai import stt
from pit_wall_ai.audio.capture import Utterance, write_wav
from pit_wall_ai.config import AppConfig
from pit_wall_ai.stt import cli_stt
from pit_wall_ai.voice import tts as T


def test_word_error_rate_ignores_case_accents_and_punctuation():
    assert stt.word_error_rate("¿Cuánta gasolina me queda?", "cuanta gasolina me queda") == 0.0
    assert stt.word_error_rate("a b c d", "a x c") == pytest.approx(
        0.5
    )  # one substitution, one deletion
    assert stt.word_error_rate("", "") == 0.0 and stt.word_error_rate("", "algo") == 1.0
    assert stt.word_error_rate("la curva 6", "la curva seis") == pytest.approx(1 / 3)


def test_hotwords_keep_the_corner_names_first_without_duplicates():
    h = stt.build_hotwords(["Curvone", "Prima Esse", "Curvone"], extra=["psi", "Roggia"])
    parts = h.split(", ")
    assert parts[:2] == ["Curvone", "Prima Esse"] and parts.count("Curvone") == 1
    assert parts.count("psi") == 1 and "Roggia" in parts


def test_config_defaults_and_unknown_backend():
    cfg = AppConfig()
    assert cfg.stt.device == "auto" and cfg.stt.model == "small" and cfg.stt.beam_size == 1
    tr = stt.create_transcriber(cfg.stt, "es")
    assert tr.name == "faster-whisper" and tr.model_name == "small"
    bad = AppConfig.model_validate({"stt": {"backend": "nope"}})
    with pytest.raises(stt.SttUnavailable):
        stt.create_transcriber(bad.stt)


def test_transcribing_before_loading_is_an_error():
    tr = stt.FasterWhisperTranscriber()
    with pytest.raises(stt.SttUnavailable, match="not loaded"):
        tr.transcribe(np.zeros(1600, dtype=np.int16))


class FakeTranscriber:
    name = "fake"

    def __init__(self, device="cpu", text="cuanta gasolina me queda", fail=False):
        self.model_name, self.device, self.text, self.fail = "tiny", device, text, fail
        self.calls = 0

    def load(self):
        if self.fail:
            raise stt.SttUnavailable("cannot load model on cuda: cublas missing")
        return 0.5

    def transcribe(self, pcm, *, hotwords=""):
        self.calls += 1
        return stt.Transcript(self.text, 0.4, len(pcm) / 16000, "es", self.device, "tiny", 1)

    def close(self):
        pass


def ns(**kw):
    return argparse.Namespace(
        **{"files": [], "ref": None, "device": "cpu", "model": None, "hotword": None, **kw}
    )


def test_stt_test_reports_latency_and_word_error_rate(tmp_path, monkeypatch):
    p = tmp_path / "a.wav"
    write_wav(p, Utterance(np.zeros(16000, dtype=np.int16)))
    monkeypatch.setattr(cli_stt, "_transcriber", lambda *a, **k: FakeTranscriber())
    lines: list[str] = []
    rc = cli_stt.cmd_stt_test(
        AppConfig(), ns(files=[str(p)], ref=["¿Cuánta gasolina me queda?"]), out=lines.append
    )
    text = "\n".join(lines)
    assert rc == 0 and "WER 0 %" in text and "real-time factor 0.40" in text
    assert "word error rate: 0.0 %" in text


def test_stt_test_skips_cuda_when_it_is_not_there_and_reports_a_failed_load(tmp_path, monkeypatch):
    p = tmp_path / "a.wav"
    write_wav(p, Utterance(np.zeros(16000, dtype=np.int16)))
    lines: list[str] = []
    monkeypatch.setattr(cli_stt, "cuda_available", lambda: False)
    rc = cli_stt.cmd_stt_test(AppConfig(), ns(files=[str(p)], device="cuda"), out=lines.append)
    assert rc == 1 and any("skipped" in s for s in lines)
    monkeypatch.setattr(cli_stt, "cuda_available", lambda: True)
    monkeypatch.setattr(cli_stt, "_transcriber", lambda *a, **k: FakeTranscriber(fail=True))
    lines.clear()
    rc = cli_stt.cmd_stt_test(AppConfig(), ns(files=[str(p)], device="cuda"), out=lines.append)
    assert rc == 1 and any("cublas" in s for s in lines)


@pytest.mark.parametrize(
    ("full", "short"),
    [
        ("Microsoft Sabina Desktop - Spanish (Mexico)", "Sabina"),
        ("Microsoft Zira Desktop - English (United States)", "Zira"),
        ("Microsoft David Desktop", "David"),
        ("Helena", "Helena"),
    ],
)
def test_the_engineer_introduces_itself_with_the_voice_name(full, short):
    assert T.short_name(full) == short


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="SAPI")
def test_a_phrase_can_be_rendered_to_a_wav(tmp_path):
    from pit_wall_ai.audio.capture import read_wav

    p = tmp_path / "x.wav"
    T.synth_to_wav("Hola, quedan dos vueltas", p, language="es")
    u = read_wav(p)
    assert u.duration_s > 0.5 and u.peak > 0.05
