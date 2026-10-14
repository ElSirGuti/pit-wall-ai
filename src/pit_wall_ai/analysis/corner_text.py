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
    c = geometry.apexes[number - 1]
    n = len(geometry.apexes)
    after = _gap_before(geometry, c) >= LONG_STRAIGHT_M
    before = _gap_after(geometry, c) >= LONG_STRAIGHT_M
    es = lang == "es"
    parts: list[str] = []
    if es:
        head = f"la curva {c.number}"
        if _named(names, c.number):
            head = f"{names[c.number]}, que es la curva {c.number}"
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
    head = f"corner {c.number}"
    if _named(names, c.number):
        head = f"{names[c.number]}, which is corner {c.number}"
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
    return find_in_names(query, names or {}, len(geometry.apexes))


def find_in_names(query: str, names: dict[int, str], count: int | None = None) -> list[int]:
    """The same lookup from the names alone (a circuit table), without a racing line."""
    n_corners = count if count is not None else (max(names) if names else 0)
    q = " ".join(w for w in _norm(query).split() if w not in _ARTICLES)
    if not q:
        return []
    out: list[int] = []
    m = re.search(r"\b(?:curva|corner|turn|t)?\s*(\d{1,2})\b", q)
    if m and 1 <= int(m.group(1)) <= n_corners:
        out.append(int(m.group(1)))
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
