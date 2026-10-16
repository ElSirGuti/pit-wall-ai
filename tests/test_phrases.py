import argparse
from types import SimpleNamespace

import numpy as np

from pit_wall_ai.audio.capture import Utterance, write_wav
from pit_wall_ai.config import AppConfig
from pit_wall_ai.stt.cli_phrases import (
    cmd_phrases_record,
    load_phrases,
    parse_phrases,
    recorded_files,
    run_test,
)

from .test_audio import FakeAudio


def test_the_document_gives_forty_phrases_in_spanish_and_the_others_in_german_and_english():
    es = load_phrases("es")
    assert len(es) == 40 and es[1][0] == "¿Cuánta gasolina me queda?"
    assert es[3][1] is True and es[2][1] is False  # the star marks the important ones
    assert es[34][0] == "Entendido."  # the note about the reply is not part of the phrase
    assert load_phrases("de")[1][0].startswith("Wie viel Sprit")
    assert load_phrases("en")[3][0] == "How many laps are left?"


def test_parse_ignores_what_is_not_a_numbered_phrase():
    text = "## Las frases (x)\n\n### Uno\n1. Hola ★\nnota\n2. Adiós (función futura, ver x)\n\n## Qué se mide\n9. no\n"
    assert parse_phrases(text, "es") == {1: ("Hola", True), 2: ("Adiós", False)}


def test_recordings_are_found_by_their_number(tmp_path):
    for name in ("01.wav", "6.m4a", "notes.txt", "x.wav"):
        (tmp_path / name).write_bytes(b"")
    assert set(recorded_files(tmp_path)) == {1, 6}


class FakeTranscriber:
    def __init__(self, texts):
        self.texts = texts

    def transcribe(self, pcm, hotwords=""):
        return SimpleNamespace(text=self.texts.pop(0), latency_s=0.5)


def _wav(path):
    u = Utterance(pcm=(np.sin(np.arange(16000) / 20) * 8000).astype(np.int16), rate=16000)
    write_wav(path, u)


def test_the_measurement_counts_exact_phrases_and_the_word_error_rate(tmp_path):
    for n in (1, 3):
        _wav(tmp_path / f"{n:02d}.wav")
    lines: list[str] = []
    phrases = {1: ("¿Cuánta gasolina me queda?", False), 3: ("¿Cuántas vueltas quedan?", True)}
    res = run_test(
        FakeTranscriber(["¿Cuánta gasolina me queda?", "cuantas vueltas quedar"]),
        recorded_files(tmp_path),
        phrases,
        out=lines.append,
    )
    assert res["phrases"] == 2 and res["exact"] == 1 and res["wrong"] == [3]
    assert 0 < res["wer"] < 0.5 and res["mean_latency_s"] == 0.5
    assert any("XX" in s and "03" in s for s in lines)


def test_psi_misheard_as_sai_counts_as_right_after_the_corrections(tmp_path):
    _wav(tmp_path / "08.wav")
    phrases = {8: ("¿Cuál es la presión en psi?", True)}
    res = run_test(
        FakeTranscriber(["¿Cuál es la presión en sai?"]), recorded_files(tmp_path), phrases
    )
    assert res["exact"] == 1


def test_recording_saves_one_wav_per_phrase(tmp_path):
    lines: list[str] = []
    a = argparse.Namespace(
        lang="es", set="A", out=str(tmp_path), device="seiren", only="1-2", redo=False, seconds=2.0
    )
    rc = cmd_phrases_record(
        AppConfig(), a, backend=FakeAudio(), ask=lambda prompt: "", out=lines.append
    )
    assert rc == 0 and set(recorded_files(tmp_path / "A")) == {1, 2}
    # a second run only records what is missing
    lines.clear()
    cmd_phrases_record(
        AppConfig(), a, backend=FakeAudio(), ask=lambda prompt: "q", out=lines.append
    )
    assert any("0 phrase(s)" in s for s in lines)
