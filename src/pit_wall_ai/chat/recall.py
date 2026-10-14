"""Memory of the conversation: "¿qué me dijiste de la curva 4?".

Answered locally from what was actually said: the alerts the engineer raised (``Engineer.recent``) and the
earlier answers of this chat. The reply quotes them; it does not paraphrase, so nothing is invented. The
numbers inside a quote were checked when they were first said, so the recall is not re-checked against the
current facts (they may have changed since: that is the point of "what did you tell me").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..analysis.corner_text import _norm, find_corner, find_in_names
from ..engineer.engine import AlertRecord

_TRIGGERS = (
    "que me dijiste",
    "que me habias dicho",
    "que me habias comentado",
    "que me comentaste",
    "que me avisaste",
    "que me habias avisado",
    "que me dijo",
    "que me aconsejaste",
    "what did you tell me",
    "what did you say",
    "what did you warn",
    "you told me",
    "was hast du mir gesagt",
    "was hattest du mir gesagt",
    "was hast du gesagt",
    "was hast du mir geraten",
    "was hast du mir gemeldet",
)
MAX_QUOTES = 2


@dataclass(frozen=True)
class Recall:
    text: str
    found: int


def is_recall(question: str) -> bool:
    q = _norm(question)
    if any(t in q for t in _TRIGGERS):
        return True
    # German puts the topic between the verb parts: "was hast du mir ueber Kurve 4 gesagt"
    return "was hast du mir" in q and any(w in q for w in ("gesagt", "geraten", "gemeldet"))


def _mentions(text: str, number: int, names: dict[int, str]) -> bool:
    t = _norm(text)
    if re.search(rf"\b(?:curva|corner|turn|kurve)\s+{number}\b", t):
        return True
    name = names.get(number)
    if name:
        nm = _norm(re.sub(r"^curva\s+", "", name, flags=re.IGNORECASE))
        return len(nm) >= 4 and nm in t
    return False


def _ago(seconds: float, lang: str) -> str:
    s = max(0, int(round(seconds)))
    if s < 90:
        return {"es": f"hace {s} s", "en": f"{s} s ago", "de": f"vor {s} s"}[lang]
    m = round(s / 60)
    return {"es": f"hace {m} min", "en": f"{m} min ago", "de": f"vor {m} Min."}[lang]


def recall(
    question: str,
    *,
    alerts: list[AlertRecord],
    history: list[tuple[str, str]],
    track,
    now: float | None,
    lang: str = "es",
) -> Recall | None:
    """The answer, or ``None`` when the question is not a recall question."""
    if not is_recall(question):
        return None
    names = track.names if track is not None else {}
    corners: list[int] = []
    if track is not None and track.geometry is not None:
        corners = find_corner(track.geometry, question, names)
    elif names:
        corners = find_in_names(question, names)
    if not corners:
        return None  # "what did you tell me?" alone is the last-alert explanation of the router
    if len(corners) > 1:
        opts = ", ".join(names.get(n) or f"curva {n}" for n in corners)
        which = {
            "es": f"Puede ser: {opts}. ¿Cuál?",
            "en": f"It could be: {opts}. Which one?",
            "de": f"Das kann sein: {opts}. Welche?",
        }[lang]
        return Recall(which, 0)
    quotes: list[tuple[float | None, str]] = []
    if corners:
        n = corners[0]
        for a in alerts:
            if _mentions(a.text, n, names):
                ago = (now - a.t) if now is not None else None
                quotes.append((ago, a.text))
        for _q, ans in history:
            if _mentions(ans, n, names):
                quotes.append((None, ans))
        label = (
            names.get(n) or {"es": f"la curva {n}", "en": f"corner {n}", "de": f"Kurve {n}"}[lang]
        )
        about_es, about_en, about_de = f"sobre {label}", f"about {label}", f"zu {label}"
    else:
        for a in alerts:
            quotes.append(((now - a.t) if now is not None else None, a.text))
        about_es, about_en, about_de = "", "", ""
    if not quotes:
        if lang == "de":
            return Recall(
                f"Ich habe keine Meldung und keine Antwort {about_de or 'in letzter Zeit'} gespeichert.",
                0,
            )
        if lang == "es":
            return Recall(
                f"No tengo registrado ningún aviso ni respuesta {about_es or 'reciente'}.".replace(
                    "  ", " "
                ),
                0,
            )
        return Recall(f"I have no alert or answer on record {about_en or 'recently'}.", 0)
    # the most recent first: alerts carry their age, answers of the chat are the oldest context
    quotes.sort(key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0.0))
    quotes = quotes[:MAX_QUOTES]
    parts = []
    for ago, text in quotes:
        when = f" ({_ago(ago, lang)})" if ago is not None else ""
        parts.append(f"«{text.rstrip('.')}»{when}")
    head = {
        "es": f"Esto es lo que te dije {about_es}: ",
        "en": f"This is what I told you {about_en}: ",
        "de": f"Das habe ich dir {about_de} gesagt: ",
    }[lang].replace("  ", " ")
    return Recall(head + "; ".join(parts) + ".", len(quotes))
