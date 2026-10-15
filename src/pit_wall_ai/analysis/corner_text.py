"""Talking about a corner: how to describe it and how to find the one the driver asks about.

Used by the conversation of phase 5/6: the engineer says "pierdes tiempo en la Prima Esse", the driver asks
"¿cuál es esa?" and the answer is "la curva 11, la última". Deterministic, no model involved: the model
only receives the sentence.
"""

from __future__ import annotations

import re
import unicodedata

from .track_geometry import Corner, TrackGeometry

_ARTICLES = {
    "la",
    "el",
    "los",
    "las",
    "de",
    "del",
    "the",
    "a",
    "esa",
    "ese",
    "cual",
    "es",
    "que",
    "donde",
    "esta",
    "where",
    "is",
    "which",
    "what",
    "one",
    "un",
    "una",
}
LONG_STRAIGHT_M = (
    400.0  # a stretch without a corner at least this long counts as "the long straight"
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(
        r"[^a-z0-9]+", " ", "".join(ch for ch in s if not unicodedata.combining(ch))
    ).strip()


def _named(names: dict[int, str] | None, number: int) -> bool:
    """A real name, not the placeholder "Curva 7" the table uses for a corner the map does not name."""
    return bool(
        names and number in names and not re.fullmatch(r"(?i)curva\s+\d+", names[number].strip())
    )


def _gap_before(geometry: TrackGeometry, c: Corner) -> float:
    apexes = geometry.apexes
    prev = apexes[c.number - 2] if c.number > 1 else apexes[-1]
    wrap = geometry.length_m if c.number == 1 else 0.0
    return (c.start_m + wrap) - prev.end_m


def _gap_after(geometry: TrackGeometry, c: Corner) -> float:
    apexes = geometry.apexes
    nxt = apexes[c.number] if c.number < len(apexes) else apexes[0]
    wrap = geometry.length_m if c.number == len(apexes) else 0.0
    return (nxt.start_m + wrap) - c.end_m


def describe_corner(
    geometry: TrackGeometry, number: int, lang: str = "es", names: dict[int, str] | None = None
) -> str:
    """ "la curva 11, la última, a izquierdas" / "the corner 6, the first after the long straight, right-hand"."""
    from .circuit_names import official_of

    c = geometry.apexes[number - 1]
    n = len(geometry.apexes)
    shown = official_of(
        geometry, c.number
    )  # the number of the circuit's map; None = this apex has none
    after = _gap_before(geometry, c) >= LONG_STRAIGHT_M
    before = _gap_after(geometry, c) >= LONG_STRAIGHT_M
    es = lang == "es"
    parts: list[str] = []
    if es:
        head = f"la curva {shown}" if shown is not None else "esta curva"
        if _named(names, c.number):
            head = (
                f"{names[c.number]}, que es la curva {shown}"
                if shown is not None
                else f"{names[c.number]}"
            )
        if c.number == n:
            parts.append("la última")
        elif c.number == 1:
            parts.append("la primera")
        if after:
            parts.append("la primera tras la recta larga")
        if before:
            parts.append("la última antes de la recta larga")
        parts.append("a derechas" if c.direction == "right" else "a izquierdas")
        return ", ".join([head, *parts])
    head = f"corner {shown}" if shown is not None else "this corner"
    if _named(names, c.number):
        head = (
            f"{names[c.number]}, which is corner {shown}"
            if shown is not None
            else f"{names[c.number]}"
        )
    if c.number == n:
        parts.append("the last one")
    elif c.number == 1:
        parts.append("the first one")
    if after:
        parts.append("the first after the long straight")
    if before:
        parts.append("the last before the long straight")
    parts.append(f"a {c.direction}-hander")
    return ", ".join([head, *parts])


def find_corner(
    geometry: TrackGeometry, query: str, names: dict[int, str] | None = None
) -> list[int]:
    """Corner numbers that match a name or a number in free text ("la prima esse", "curva 6", "curvone").

    Several numbers come back when the text is ambiguous ("esse" matches three corners); the caller asks
    which one. Accents and case are ignored.
    """
    from .circuit_names import apex_of

    official = getattr(geometry, "official", None)
    if official:  # the driver says the numbers of the circuit's map: translate them to apexes
        count = max(official.values())
        return find_in_names(
            query, names or {}, count, number_to_apex=lambda k: apex_of(geometry, k)
        )
    return find_in_names(query, names or {}, len(geometry.apexes))


_NUMBER_WORDS = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8,
    "neun": 9, "zehn": 10, "elf": 11, "zwolf": 12, "dreizehn": 13, "vierzehn": 14, "funfzehn": 15,
}  # fmt: skip


def _digits(q: str) -> str:
    """ "curva seis" -> "curva 6": a recogniser writes the words, a keyboard the digits."""
    return re.sub(
        r"\b(curva|corner|turn|kurve)\s+([a-z]+)\b",
        lambda m: f"{m.group(1)} {_NUMBER_WORDS.get(m.group(2), m.group(2))}",
        q,
    )


def find_in_names(
    query: str, names: dict[int, str], count: int | None = None, number_to_apex=None
) -> list[int]:
    """The same lookup from the names alone (a circuit table), without a racing line."""
    n_corners = count if count is not None else (max(names) if names else 0)
    q = " ".join(w for w in _digits(_norm(query)).split() if w not in _ARTICLES)
    if not q:
        return []
    out: list[int] = []
    m = re.search(r"\b(?:curva|corner|turn|t)?\s*(\d{1,2})\b", q)
    if m and 1 <= int(m.group(1)) <= n_corners:
        target = int(m.group(1))
        if number_to_apex is not None:
            target = number_to_apex(target)
        if target is not None:
            out.append(target)
    for num, name in names.items():
        nm = _norm(name)
        generic = _norm(re.sub(r"^curva\s+", "", name))
        hit = q in (nm, generic) or nm in q or (len(q) >= 4 and q in nm)
        if hit and num not in out:
            out.append(num)
    ordinals = {"primera": 1, "first": 1, "ultima": n_corners, "last": n_corners}
    for word, num in ordinals.items():
        if num >= 1 and re.search(rf"\b{word}\b", q) and "curva" in q and num not in out:
            out.append(num)
    return out


def describe_from_names(number: int, count: int, names: dict[int, str], lang: str = "es") -> str:
    """ "Prima Esse, que es la curva 11, la última": what is known without the geometry."""
    es = lang == "es"
    if _named(names, number):
        head = (
            f"{names[number]}, que es la curva {number}"
            if es
            else f"{names[number]}, which is corner {number}"
        )
    else:
        head = f"la curva {number}" if es else f"corner {number}"
    if number == count:
        tail = "la última" if es else "the last one"
    elif number == 1:
        tail = "la primera" if es else "the first one"
    else:
        return head
    return f"{head}, {tail}"
