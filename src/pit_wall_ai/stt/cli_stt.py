"""``pit-wall stt-test``: latency, accuracy and CPU vs GPU of the recogniser on wav files (or a synthetic set)."""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Callable
from pathlib import Path

from ..audio.capture import read_wav
from ..config import AppConfig
from ..voice import tts as T
from . import (
    FasterWhisperTranscriber,
    SttUnavailable,
    Transcriber,
    build_hotwords,
    cuda_available,
    word_error_rate,
)

# questions the driver would ask; used when no wav is given (synthesised with a Windows voice, which is NOT
# the author's voice: it measures speed and gross errors, not the accuracy with a real voice and microphone)
SYNTH_PHRASES = [
    "¿Cuánta gasolina me queda?",
    "¿Cómo están los neumáticos delanteros?",
    "¿Cuánto perdí en la curva seis?",
    "¿Cuántas vueltas quedan?",
    "¿Cuál fue mi mejor vuelta?",
    "¿Cómo voy de frenos?",
    "¿Cuál es la presión de los neumáticos en psi?",
    "¿Qué tal fue la frenada de la curva cuatro?",
]


def _transcriber(cfg: AppConfig, device: str, model: str | None, language: str) -> Transcriber:
    s = cfg.stt
    return FasterWhisperTranscriber(
        model=model or s.model,
        device=device,
        compute_type=s.compute_type,
        threads=s.threads,
        beam_size=s.beam_size,
        language=s.language or language,
        model_dir=s.model_dir,
    )


def cmd_stt_test(
    cfg: AppConfig, a: argparse.Namespace, *, out: Callable[[str], None] = print
) -> int:
    import logging

    for noisy in ("faster_whisper", "httpx2", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    language = cfg.engineer.language
    samples: list[tuple[Path, str]] = []
    if a.files:
        refs = a.ref or []
        for i, f in enumerate(a.files):
            samples.append((Path(f), refs[i] if i < len(refs) else ""))
    else:
        d = Path(tempfile.mkdtemp(prefix="pitwall-stt-"))
        out("no files given: synthesising phrases with the Windows voice (not your voice)")
        try:
            for i, ph in enumerate(SYNTH_PHRASES):
                p = d / f"s{i}.wav"
                T.synth_to_wav(ph, p, voice_name=cfg.engineer.voice.name, language=language)
                samples.append((p, ph))
        except T.TtsUnavailable as exc:
            out(f"cannot synthesise: {exc}")
            return 1
    devices = ["cpu", "cuda"] if a.device == "both" else [a.device]
    hot = build_hotwords(extra=a.hotword or [])
    rc = 0
    for dev_name in devices:
        if dev_name == "cuda" and not cuda_available():
            out("cuda: no CUDA device visible to the recogniser; skipped")
            rc = 1
            continue
        tr = _transcriber(cfg, dev_name, a.model, language)
        try:
            load_s = tr.load()
        except SttUnavailable as exc:
            out(f"{dev_name}: {exc}")
            rc = 1
            continue
        out(f"\n[{dev_name}] model {tr.model_name}, loaded in {load_s:.1f} s")
        errs, words, lats, rtfs = [], 0, [], []
        for idx, (path, ref) in enumerate(samples):
            u = read_wav(path)
            if idx == 0:
                tr.transcribe(u.pcm, hotwords=hot)  # the first call pays for kernel set-up
            t = tr.transcribe(u.pcm, hotwords=hot)
            lats.append(t.latency_s)
            rtfs.append(t.realtime_factor)
            line = f"  {t.latency_s:5.2f} s  ({t.audio_s:4.1f} s audio)  {t.text!r}"
            if ref:
                w = word_error_rate(ref, t.text)
                n = len(ref.split())
                errs.append(w * n)
                words += n
                line += f"  WER {w * 100:.0f} %"
            out(line)
        if lats:
            srt = sorted(lats)
            out(
                f"  latency: mean {sum(lats) / len(lats):.2f} s, median {srt[len(srt) // 2]:.2f} s, "
                f"max {srt[-1]:.2f} s; real-time factor {sum(rtfs) / len(rtfs):.2f}"
            )
        if words:
            out(f"  word error rate: {sum(errs) / words * 100:.1f} % over {words} words")
        tr.close()
    return rc
