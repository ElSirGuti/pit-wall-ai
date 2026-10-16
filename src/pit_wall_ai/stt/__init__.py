"""Speech to text behind one small interface (phase 6, step 3).

``faster-whisper`` runs locally (no network once the model is downloaded, audio never leaves the PC). The
recogniser is biased with the vocabulary of the session (corner names, the words of the chat), because a name
that is not in its vocabulary is what a generic recogniser gets wrong.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

# words of the chat that a generic recogniser tends to get wrong; extended with the corner names
BASE_VOCABULARY = (
    "psi",
    "delantero izquierdo",
    "delantero derecho",
    "trasero izquierdo",
    "trasero derecho",
    "gasolina",
    "neumáticos",
    "frenada",
    "vuelta rápida",
    "box",
)


VOCABULARY = {
    "es": BASE_VOCABULARY,
    "en": (
        "psi",
        "front left",
        "front right",
        "rear left",
        "rear right",
        "fuel",
        "tyres",
        "braking point",
        "fastest lap",
        "box",
    ),
    "de": (
        "psi",
        "vorne links",
        "vorne rechts",
        "hinten links",
        "hinten rechts",
        "Sprit",
        "Reifen",
        "Bremspunkt",
        "schnellste Runde",
        "Box",
    ),
}


class SttUnavailable(RuntimeError):
    """The recogniser cannot run here (library missing, model not downloaded, no CUDA)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    latency_s: float  # time spent recognising, model load not included
    audio_s: float
    language: str = ""
    device: str = ""
    model: str = ""
    segments: int = 0

    @property
    def realtime_factor(self) -> float:
        """Recognition time / audio time; below 1 it is faster than the speech."""
        return self.latency_s / self.audio_s if self.audio_s else 0.0


class Transcriber(Protocol):
    name: str
    model_name: str

    def load(self) -> float:
        """Load the model; returns the seconds it took."""

    def transcribe(self, pcm: np.ndarray, *, hotwords: str = "") -> Transcript: ...

    def close(self) -> None: ...


def build_hotwords(
    corner_names: list[str] | None = None, extra: list[str] | None = None, lang: str = "es"
) -> str:
    """One string of names to bias the recogniser; duplicates dropped, order kept."""
    seen: dict[str, None] = {}
    for w in [*(corner_names or []), *VOCABULARY.get(lang, BASE_VOCABULARY), *(extra or [])]:
        w = w.strip()
        if w:
            seen.setdefault(w, None)
    return ", ".join(seen)


# friendly sizes -> faster-whisper model names (anything else is passed as it is: a name, a path, a Hugging Face id)
MODEL_ALIASES = {
    "high": "large-v3",
    "large": "large-v3",
    "turbo": "large-v3-turbo",
    "fast": "base",
}


def resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name.strip().lower(), name.strip())


class FasterWhisperTranscriber:
    name = "faster-whisper"

    def __init__(
        self,
        *,
        model: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        threads: int = 4,
        beam_size: int = 1,
        language: str | None = "es",
        model_dir: str | None = None,
    ) -> None:
        self.model_name, self.device, self.compute_type = resolve_model(model), device, compute_type
        self.threads, self.beam_size, self.language = threads, beam_size, language
        self.model_dir = model_dir
        self._model = None

    def load(self) -> float:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SttUnavailable(
                'faster-whisper is not installed: pip install -e ".[stt]"'
            ) from exc
        add_nvidia_dll_dirs()
        t0 = time.perf_counter()
        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.threads,
                download_root=self.model_dir,
            )
        except Exception as exc:  # noqa: BLE001 - missing CUDA libraries, no network for the download...
            raise SttUnavailable(
                f"cannot load model {self.model_name!r} on {self.device}: {exc}"
            ) from exc
        # a model can "load" on cuda and still fail at the first call (a missing cuBLAS/cuDNN library):
        # warm it up here so the failure is reported at load, and the first real question is not slow
        self.transcribe(np.zeros(8000, dtype=np.int16))
        return time.perf_counter() - t0

    def transcribe(self, pcm: np.ndarray, *, hotwords: str = "") -> Transcript:
        if self._model is None:
            raise SttUnavailable("model not loaded")
        audio = pcm.astype(np.float32) / 32768.0
        t0 = time.perf_counter()
        try:
            segs, info = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=False,  # the push-to-talk button already delimits the speech
                condition_on_previous_text=False,
                hotwords=hotwords or None,
            )
            parts = [s.text for s in segs]  # a generator: the work happens here
        except Exception as exc:  # noqa: BLE001
            raise SttUnavailable(f"recognition failed on {self.device}: {exc}") from exc
        return Transcript(
            text=" ".join(p.strip() for p in parts).strip(),
            latency_s=time.perf_counter() - t0,
            audio_s=len(pcm) / 16000,
            language=info.language,
            device=self.device,
            model=self.model_name,
            segments=len(parts),
        )

    def close(self) -> None:
        self._model = None


def create_transcriber(cfg, language: str | None = None) -> Transcriber:
    """``cfg`` is :class:`pit_wall_ai.config.SttConfig`."""
    if cfg.backend == "faster-whisper":
        return FasterWhisperTranscriber(
            model=cfg.model,
            device=cfg.device,
            compute_type=cfg.compute_type,
            threads=cfg.threads,
            beam_size=cfg.beam_size,
            language=cfg.language or language,
            model_dir=cfg.model_dir,
        )
    raise SttUnavailable(f"unknown stt backend {cfg.backend!r}")


def add_nvidia_dll_dirs() -> list[str]:
    """Make the CUDA libraries installed by pip (``nvidia-cublas-cu12``, ``nvidia-cudnn-cu12``) loadable."""
    import os
    import sys
    from pathlib import Path

    added: list[str] = []
    if sys.platform != "win32":
        return added
    for base in map(Path, sys.path):
        root = base / "nvidia"
        if not root.is_dir():
            continue
        for bin_dir in root.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(bin_dir))
            except OSError:
                continue
    return added


def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


# -- word error rate ----------------------------------------------------------------------------------
def _norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+(?:[.,][0-9]+)?", t)


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over words / words in the reference; case, accents and punctuation ignored."""
    r, h = _norm(reference), _norm(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return prev[-1] / len(r)
