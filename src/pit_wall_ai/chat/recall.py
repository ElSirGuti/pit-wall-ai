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

# topics: question words (folded) -> the rules whose alerts are about it, and how each language names it
TOPICS: dict[str, tuple[tuple[str, ...], frozenset[str], dict[str, str]]] = {
    "fuel": (
        ("gasolina", "combustible", "fuel", "sprit", "benzin", "kraftstoff"),
        frozenset({"fuel_low"}),
        {"es": "la gasolina", "en": "the fuel", "de": "den Sprit"},
    ),
    "tyres": (
        ("neumatico", "neumaticos", "gomas", "presion", "tyre", "tyres", "reifen", "reifendruck"),
        frozenset({"tyre_hot", "tyre_pressure"}),
        {"es": "los neumáticos", "en": "the tyres", "de": "die Reifen"},
    ),
    "brakes": (
        ("freno", "frenos", "brake", "brakes", "bremse", "bremsen"),
        frozenset({"brake_hot"}),
        {"es": "los frenos", "en": "the brakes", "de": "die Bremsen"},
    ),
    "braking": (
        ("frenada", "frenadas", "braking", "bremspunkt", "bremspunkte"),
        frozenset({"braking_hint", "lockup"}),
        {"es": "las frenadas", "en": "the braking", "de": "das Bremsen"},
    ),
    "laps": (
        ("vuelta", "vueltas", "tiempo", "tiempos", "lap", "laps", "runde", "runden"),
        frozenset({"lap_summary", "best_sector"}),
        {"es": "las vueltas", "en": "the laps", "de": "die Runden"},
    ),
    "penalty": (
        ("penalizacion", "penalizaciones", "sancion", "penalty", "strafe"),
        frozenset({"penalty"}),
        {"es": "las penalizaciones", "en": "the penalties", "de": "die Strafen"},
    ),
    "flags": (
        ("bandera", "banderas", "flag", "flags", "flagge", "flaggen"),
        frozenset({"flags"}),
        {"es": "las banderas", "en": "the flags", "de": "die Flaggen"},
    ),
    "damage": (
        ("dano", "danos", "damage", "schaden"),
        frozenset({"damage"}),
        {"es": "los daños", "en": "the damage", "de": "den Schaden"},
    ),
}
# the question is about an earlier session, not this one
_PREVIOUS = (
    "sesion anterior",
    "sesion pasada",
    "ultima sesion",
    "otra sesion",
    "la ultima vez",
    "la otra vez",
    "previous session",
    "last session",
    "last time",
    "letzte session",
    "letzten session",
    "vorherige session",
    "vorherigen session",
    "letztes mal",
)


def asks_about_a_previous_session(question: str) -> bool:
    q = _norm(question)
    return any(p in q for p in _PREVIOUS)


def find_topic(question: str) -> str | None:
    words = set(_norm(question).split())
    for key, (kw, _rules, _names) in TOPICS.items():
        if words & set(kw):
            return key
    return None


def _ago_wall(seconds: float, lang: str) -> str:
    s = max(0, int(round(seconds)))
    if s < 3600:
        m = max(1, round(s / 60))
        return {"es": f"hace {m} min", "en": f"{m} min ago", "de": f"vor {m} Min."}[lang]
    if s < 86400:
        h = round(s / 3600)
        return {"es": f"hace {h} h", "en": f"{h} h ago", "de": f"vor {h} Std."}[lang]
    d = round(s / 86400)
    return {"es": f"hace {d} días", "en": f"{d} days ago", "de": f"vor {d} Tagen"}[lang]


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


def _mentions(
    text: str, number: int | None, names: dict[int, str], apex: int | None = None
) -> bool:
    """``number`` is what was said (the map's number); ``apex`` keys the names (the line's number)."""
    t = _norm(text)
    if number is not None and re.search(
        r"\b(?:curva|corner|turn|kurve)\s+" + str(number) + r"\b", t
    ):
        return True
    name = names.get(apex if apex is not None else number)
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


def _recall_topic_or_session(
    topic, earlier, *, alerts, previous, now, lang, corner=None, corner_spoken=None, names=None
) -> Recall:
    """Quotes by topic from this session, or from the earlier ones when the driver asked about them."""
    import time as _time

    rules = TOPICS[topic][1] if topic else None
    label = TOPICS[topic][2][lang] if topic else ""
    if corner is not None:
        label = (names or {}).get(corner) or {
            "es": f"la curva {corner}",
            "en": f"corner {corner}",
            "de": f"Kurve {corner}",
        }[lang]

    def wanted(a) -> bool:
        if corner is not None:
            return _mentions(a.text, corner_spoken, names or {}, corner)
        return rules is None or a.rule in rules

    quotes: list[tuple[str, str]] = []  # (when, text)
    if earlier:
        wall = _time.time()
        for a in previous:
            if wanted(a):
                quotes.append((_ago_wall(wall - a.ts, lang), a.text))
    else:
        for a in sorted(alerts, key=lambda r: r.t, reverse=True):
            if wanted(a):
                when = _ago(now - a.t, lang) if now is not None else ""
                quotes.append((when, a.text))
    scope = {
        "es": "en la sesión anterior" if earlier else "",
        "en": "in the previous session" if earlier else "",
        "de": "in der vorherigen Session" if earlier else "",
    }[lang]
    if not quotes:
        none = {
            "es": f"No tengo registrado ningún aviso {('sobre ' + label) if label else ''} {scope}".strip(),
            "en": f"I have no alert {('about ' + label) if label else ''} on record {scope}".strip(),
            "de": f"Ich habe keine Meldung {('zu ' + label) if label else ''} gespeichert {scope}".strip(),
        }[lang]
        return Recall(" ".join(none.split()) + ".", 0)
    quotes = quotes[:MAX_QUOTES]
    body = "; ".join(f"«{t.rstrip('.')}»" + (f" ({w})" if w else "") for w, t in quotes)
    head = {
        "es": f"Esto es lo que te dije {('sobre ' + label) if label else ''} {scope}: ",
        "en": f"This is what I told you {('about ' + label) if label else ''} {scope}: ",
        "de": f"Das habe ich dir {('zu ' + label) if label else ''} {scope} gesagt: ",
    }[lang]
    return Recall(" ".join(head.split()) + " " + body + ".", len(quotes))


def recall(
    question: str,
    *,
    alerts: list[AlertRecord],
    history: list[tuple[str, str]],
    track,
    now: float | None,
    lang: str = "es",
    previous=(),
) -> Recall | None:
    """The answer, or ``None`` when the question is not a recall question.

    By corner ("qué me dijiste de la curva 4") or by topic ("... de la gasolina"); with "la última vez" /
    "la sesión anterior" the earlier sessions kept in ``previous`` are searched instead of this one."""
    if not is_recall(question):
        return None
    earlier = asks_about_a_previous_session(question)
    names = track.names if track is not None else {}
    corners: list[int] = []
    if track is not None and track.geometry is not None:
        corners = find_corner(track.geometry, question, names)
    elif names:
        corners = find_in_names(question, names)
    topic = find_topic(question) if not corners else None
    if not corners and topic is None and not earlier:
        return None  # "what did you tell me?" alone is the last-alert explanation of the router
    if not corners and (topic is not None or earlier):
        return _recall_topic_or_session(
            topic, earlier, alerts=alerts, previous=previous, now=now, lang=lang
        )
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
        if track is not None and getattr(track, "geometry", None) is not None:
            from ..analysis.circuit_names import official_of

            spoken = official_of(track.geometry, n)  # what was said then: the map's number
        else:
            spoken = n
        if earlier:
            return _recall_topic_or_session(
                None,
                True,
                alerts=alerts,
                previous=previous,
                now=now,
                lang=lang,
                corner=n,
                names=names,
            )
        for a in alerts:
            if _mentions(a.text, spoken, names, n):
                ago = (now - a.t) if now is not None else None
                quotes.append((ago, a.text))
        for _q, ans in history:
            if _mentions(ans, spoken, names, n):
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
