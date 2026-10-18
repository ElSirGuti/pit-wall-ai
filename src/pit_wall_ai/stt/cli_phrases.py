"""``pit-wall phrases``: record the phrases of ``docs/FRASES_VOZ.md`` one by one and measure the recogniser on them.

``phrases record`` shows each phrase, records it from the microphone (Enter to start, Enter to stop) and saves
``frases/<set>/NN.wav``; ``phrases test`` transcribes everything recorded (also phone recordings in
``frases/telefono``) and reports, per phrase and in total, what was heard, the word error rate and the latency.
The numbers are the ones of the list in the document; a phrase without a file is simply skipped.
"""

from __future__ import annotations

import argparse
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..audio.capture import (
    Microphone,
    UtteranceRecorder,
    read_audio,
    write_wav,
)
from ..audio.devices import DeviceNotFound, find_device
from ..config import AppConfig
from . import SttUnavailable, build_hotwords, create_transcriber, word_error_rate
from .postfix import fix as fix_transcript

AUDIO_EXT = (".wav", ".m4a", ".aac", ".mp3", ".ogg", ".opus", ".3gp", ".flac")
_ITEM = re.compile(r"^\s*(\d+)\.\s+(.*?)\s*$")
_NOTE = re.compile(r"\s*\((?:función|dato que|acuse|trampa|Falle|no existe)[^)]*\)\s*$")
_SECTIONS = {"es": None, "de": "### Deutsch", "en": "### English"}


def _doc_path() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "docs" / "FRASES_VOZ.md"
        if cand.is_file():
            return cand
    return None


def parse_phrases(text: str, lang: str = "es") -> dict[int, tuple[str, bool]]:
    """{number: (phrase, important)} of one language of the document (the ★ marks the key ones)."""
    lines = text.splitlines()
    start = 0
    end = len(lines)
    marker = _SECTIONS.get(lang)
    if marker is None:  # Spanish: the first list, up to the "what is measured" part
        for i, ln in enumerate(lines):
            if ln.startswith("## Las frases"):
                start = i
            if ln.startswith("## Qué se mide"):
                end = i
                break
    else:
        for i, ln in enumerate(lines):
            if ln.strip() == marker:
                start = i + 1
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith("### ") or lines[j].startswith("## "):
                        end = j
                        break
                break
        else:
            return {}
    out: dict[int, tuple[str, bool]] = {}
    for ln in lines[start:end]:
        m = _ITEM.match(ln)
        if not m:
            continue
        phrase = _NOTE.sub("", m.group(2))
        important = "★" in phrase
        phrase = phrase.replace("★", "").strip()
        out[int(m.group(1))] = (phrase, important)
    return out


def load_phrases(lang: str = "es", path: Path | None = None) -> dict[int, tuple[str, bool]]:
    p = path or _doc_path()
    if p is None:
        return {}
    return parse_phrases(p.read_text(encoding="utf-8"), lang)


def recorded_files(folder: Path) -> dict[int, Path]:
    """{phrase number: file} for the files named by the number (``06.wav``, ``6.m4a``)."""
    out: dict[int, Path] = {}
    if not folder.is_dir():
        return out
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in AUDIO_EXT and f.stem.isdigit():
            out.setdefault(int(f.stem), f)
    return out


def _select(only: str | None, phrases: dict[int, tuple[str, bool]]) -> list[int]:
    if not only:
        return sorted(phrases)
    wanted: set[int] = set()
    for part in only.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        elif part:
            wanted.add(int(part))
    return [n for n in sorted(phrases) if n in wanted]


# -- record ---------------------------------------------------------------------------------------------
def cmd_phrases_record(
    cfg: AppConfig,
    a: argparse.Namespace,
    *,
    backend=None,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    from ..audio.cli_audio import _backend

    phrases = load_phrases(a.lang)
    if not phrases:
        out(
            "the list of phrases (docs/FRASES_VOZ.md) was not found: run this from a checkout of the repository"
        )
        return 1
    backend = backend or _backend()
    try:
        dev, _ = find_device(backend.devices(), a.device or cfg.audio.input_device, kind="input")
    except DeviceNotFound as exc:
        out(str(exc))
        return 2
    folder = Path(a.out) / a.set
    folder.mkdir(parents=True, exist_ok=True)
    got: list = []
    rec = UtteranceRecorder(
        on_utterance=got.append,
        preroll_ms=cfg.audio.preroll_ms,
        tail_ms=cfg.audio.tail_ms,
        min_ms=1,
        silence_rms=0.0,
    )
    mic = Microphone(backend, dev, rec)
    try:
        mic.start()
    except OSError as exc:
        out(f"cannot open the microphone: {exc}")
        return 1
    done = recorded_files(folder)
    todo = [n for n in _select(a.only, phrases) if a.redo or n not in done]
    out(
        f"set {a.set!r}: {len(todo)} phrase(s) to record in {folder}. "
        "Enter = start recording, Enter again = stop; s = skip; q = quit."
    )
    try:
        for k, n in enumerate(todo, 1):
            text, important = phrases[n]
            out(
                f"\n[{k}/{len(todo)}]  {n}.  {text}{'  (★ the important ones)' if important else ''}"
            )
            while True:
                cmd = ask("  Enter to record > ").strip().lower()
                if cmd == "q":
                    return 0
                if cmd == "s":
                    break
                got.clear()
                rec.press()
                t0 = time.monotonic()
                stop = threading.Event()

                def wait_for_enter(event=stop):
                    ask("  recording... Enter to stop > ")
                    event.set()

                threading.Thread(target=wait_for_enter, daemon=True).start()
                while not stop.wait(0.1) and time.monotonic() - t0 < a.seconds:
                    pass
                rec.release()
                time.sleep(cfg.audio.tail_ms / 1000 + 0.1)
                if not got:
                    out("  nothing was recorded; again")
                    continue
                u = got[0]
                note = ""
                if u.peak < 0.02:
                    note = "  (very low level: check the microphone)"
                elif u.peak > 0.98:
                    note = "  (clipping: lower the gain)"
                write_wav(folder / f"{n:02d}.wav", u)
                out(f"  saved {n:02d}.wav: {u.duration_s:.1f} s, peak {u.peak * 100:.0f} %{note}")
                again = ask("  Enter = next, r = record again > ").strip().lower()
                if again != "r":
                    break
    finally:
        mic.stop()
    out("\nDone. Measure them with: pit-wall phrases test --set " + a.set)
    return 0


# -- test -----------------------------------------------------------------------------------------------
def run_test(
    transcriber,
    files: dict[int, Path],
    phrases: dict[int, tuple[str, bool]],
    *,
    hotwords: str = "",
    out: Callable[[str], None] = print,
) -> dict:
    """Transcribe every recorded phrase; returns the totals (the per-phrase lines go to ``out``)."""
    errs = words = exact = 0.0
    lats: list[float] = []
    bad: list[int] = []
    shown = 0
    for n, path in sorted(files.items()):
        if n not in phrases:
            continue
        ref = phrases[n][0]
        u = read_audio(path)
        t = transcriber.transcribe(u.pcm, hotwords=hotwords)
        heard = fix_transcript(t.text)
        w = word_error_rate(ref, heard)
        count = len(ref.split())
        errs += w * count
        words += count
        lats.append(t.latency_s)
        ok = w == 0.0
        exact += ok
        if not ok:
            bad.append(n)
        mark = "ok " if ok else "XX "
        out(
            f"  {mark}{n:02d}  {t.latency_s:4.2f} s  WER {w * 100:3.0f} %  {heard!r}"
            + ("" if ok else f"   <- {ref!r}")
        )
        shown += 1
    return {
        "phrases": shown,
        "exact": int(exact),
        "wer": (errs / words) if words else 0.0,
        "mean_latency_s": (sum(lats) / len(lats)) if lats else 0.0,
        "wrong": bad,
    }


def cmd_phrases_test(
    cfg: AppConfig, a: argparse.Namespace, *, transcriber=None, out: Callable[[str], None] = print
) -> int:
    phrases = load_phrases(a.lang)
    if not phrases:
        out(
            "the list of phrases (docs/FRASES_VOZ.md) was not found: run this from a checkout of the repository"
        )
        return 1
    folder = Path(a.dir) if a.dir else Path(a.out) / a.set
    files = recorded_files(folder)
    if not files:
        out(f"no recordings in {folder} (named 01.wav, 02.wav... by the number of the phrase)")
        return 1
    s = cfg.stt.model_copy(
        update={k: v for k, v in (("device", a.device), ("model", a.model)) if v}
    )
    tr = transcriber or create_transcriber(s, a.lang if a.lang != "es" else cfg.engineer.language)
    try:
        if transcriber is None:
            load = tr.load()
            out(f"[{getattr(tr, 'device', '?')}] model {tr.model_name}, loaded in {load:.1f} s")
    except SttUnavailable as exc:
        out(str(exc))
        return 1
    names = [text for text, _ in phrases.values()]
    res = run_test(
        tr, files, phrases, hotwords=build_hotwords(extra=names[:0], lang=a.lang), out=out
    )
    out(
        f"\n{res['exact']} of {res['phrases']} phrases exact; word error rate {res['wer'] * 100:.1f} %; "
        f"mean latency {res['mean_latency_s']:.2f} s"
    )
    if res["wrong"]:
        out(
            "to improve: "
            + ", ".join(f"{n:02d}" for n in res["wrong"])
            + "  (see docs/FRASES_VOZ.md)"
        )
    if transcriber is None:
        tr.close()
    return 0
