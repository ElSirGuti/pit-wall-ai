"""Corrections applied to what the recogniser heard, before the question reaches the chat.

Only things measured as recurring errors go here (``docs/PHASE6_PLAN.md`` 7.1): a generic recogniser does not
know the vocabulary of the pit wall. Each rule is a whole-word replacement, case and accents ignored, and the
list grows from the author's spoken phrases (``docs/FRASES_VOZ.md``), not from guesses.
"""

from __future__ import annotations

import re
import unicodedata

# heard (folded) -> what was said
_HEARD: dict[str, str] = {
    "sencai": "psi",
    "sen kai": "psi",
    "sencay": "psi",
    "pesi": "psi",
    "p s i": "psi",
    "ps i": "psi",
    "psy": "psi",
    "pisi": "psi",
    "pi esi": "psi",
    "pizza": "pits",
    "bit": "pits",
    "bits": "pits",
    "pit's": "pits",
    "maguione": "magione",
    "dejaste de los": "desgaste de los",
    "deje este": "desgaste",
    "mayones": "magione",
    "mahione": "magione",
    "majone": "magione",
    "magiones": "magione",
    "silver stone": "silverstone",
    "red bul ring": "red bull ring",
    "mugelo": "mugello",
    "spa francochamps": "spa francorchamps",
    "sai": "psi",
    "psai": "psi",
    "psay": "psi",
    "pe sai": "psi",
}


def _fold(text: str) -> str:
    """Lowercase without accents, one character per original character (so positions carry over)."""
    out = []
    for ch in text:
        base = "".join(
            c for c in unicodedata.normalize("NFKD", ch.lower()) if not unicodedata.combining(c)
        )
        out.append(base if len(base) == 1 else ch.lower())
    return "".join(out)


_RULES = [
    (re.compile(r"(?<![a-z0-9])" + r"\s+".join(map(re.escape, h.split())) + r"(?![a-z0-9])"), said)
    for h, said in _HEARD.items()
]


def fix(text: str) -> str:
    """The text with the known mis-hearings replaced; everything else untouched."""
    folded = _fold(text)
    spans: list[tuple[int, int, str]] = []
    for rx, said in _RULES:
        spans += [(m.start(), m.end(), said) for m in rx.finditer(folded)]
    out = text
    for a, b, said in sorted(spans, reverse=True):
        out = out[:a] + said + out[b:]
    return out
