"""Spoken commands to the engineer that are not questions: "no molestes ahorita", "ya puedes hablar".

Matched on short utterances only (a long sentence that happens to contain "silencio" is a question about the
session, not an order). Deterministic: no model is involved, so the order works with no network at all.
"""

from __future__ import annotations

import re
import unicodedata

MAX_WORDS = 8

QUIET = (
    "no molestes",
    "no me molestes",
    "no me hables",
    "no me interrumpas",
    "no me avises",
    "ahora no",
    "ahorita no",
    "callate",
    "calla",
    "silencio",
    "modo silencio",
    "dejame tranquilo",
    "dejame en paz",
    "dont bother me",
    "do not bother me",
    "be quiet",
    "quiet please",
    "stay quiet",
    "shut up",
)
RESUME = (
    "ya puedes hablar",
    "puedes hablar",
    "puedes avisarme",
    "ya puedes avisarme",
    "vuelve a hablar",
    "habla otra vez",
    "quita el silencio",
    "fin del silencio",
    "you can talk",
    "you can speak",
    "talk to me again",
    "back on",
)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower().replace("'", ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _has(q: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {q} "
    return any(f" {p} " in padded for p in phrases)


def parse(text: str) -> str | None:
    """``"quiet"``, ``"resume"`` or ``None`` (an ordinary question)."""
    q = _norm(text)
    if not q or len(q.split()) > MAX_WORDS:
        return None
    if _has(q, RESUME):
        return "resume"
    if _has(q, QUIET):
        return "quiet"
    return None
