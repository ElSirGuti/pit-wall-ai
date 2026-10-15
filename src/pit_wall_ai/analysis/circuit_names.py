"""Corner names of the circuits that have an own table (``data/circuits.json``).

Names are attached to the apexes of the geometry by position, only when a table corner is within
``TOLERANCE`` of the apex. A corner without a name keeps its number only; nothing is
guessed. A track with an empty table (Magione, Monza...) gets no names at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from .track_geometry import TrackGeometry

TOLERANCE = 0.015  # fraction of the lap (~100 m at Spa, ~37 m at Magione) between a table apex and the geometric one


@lru_cache(maxsize=1)
def _table() -> list[dict]:
    text = resources.files("pit_wall_ai").joinpath("data/circuits.json").read_text(encoding="utf-8")
    return json.loads(text)["circuits"]


def numbering_verified(track: str, layout: str = "", length_m: float | None = None) -> bool:
    """The author checked this circuit's corner numbering against the real circuit (flag in the table)."""
    c = circuit_for_layout(track, layout, length_m)
    return bool(c and c.get("numbering_verified"))


def table_names(
    track: str, layout: str = "", length_m: float | None = None
) -> dict[int, str] | None:
    """{number: name} straight from the table, only for a circuit whose numbering was verified.

    It lets the chat answer "which corner is the Prima Esse?" on a machine without the game installed
    (no racing line). For an unverified circuit the table order is not known to be the circuit's numbering,
    so nothing is returned.
    """
    c = circuit_for_layout(track, layout, length_m)
    if not c or not c.get("numbering_verified"):
        return None
    out = {int(e["order"]): str(e["name"]) for e in c.get("corners", []) if "order" in e}
    return out or None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def circuit_for(track: str) -> dict | None:
    """The table entry whose id or aliases match the game's track folder name (``spa``, ``fn_imola``...)."""
    t = _norm(track)
    for c in _table():
        if t in {_norm(c["id"]), *(_norm(a) for a in c.get("aliases", []))}:
            return c
    return None


LAYOUT_LENGTH_TOLERANCE = 0.08  # a layout this far from the table's length is another layout


def is_reverse_layout(layout: str) -> bool:
    """AC layouts name the reverse direction in several ways: "reverse_x", "x_reverse", "r_x", "layout_r_gp"."""
    low = (layout or "").lower()
    return (
        "reverse" in low
        or low.endswith("_rev")
        or low.startswith("r_")
        or "_r_" in low
        or low.endswith("_r")
    )


def _mirror(c: dict) -> dict:
    """The same circuit driven the other way: same names, the lap fractions mirrored, the order reversed.

    Derived, never verified: the author confirms a reverse layout against its own map before it is trusted.
    """
    orders = [int(e["order"]) for e in c.get("corners", []) if e.get("order") is not None]
    top = max(orders) if orders else 0
    corners = []
    for e in reversed(c.get("corners", [])):
        m = dict(e)
        m["apex_fraction"] = round((1.0 - float(e["apex_fraction"])) % 1.0, 4)
        if e.get("order") is not None and top:
            m["order"] = top + 1 - int(e["order"])
        corners.append(m)
    nums = c.get("official_numbers") or []
    ntop = max((int(e["number"]) for e in nums), default=0)
    official = [
        {
            "number": ntop + 1 - int(e["number"]),
            "apex_fraction": round((1.0 - float(e["apex_fraction"])) % 1.0, 4),
        }
        for e in reversed(nums)
    ]
    straights = [
        {
            **s,
            "from_fraction": round((1.0 - float(s["to_fraction"])) % 1.0, 4),
            "to_fraction": round((1.0 - float(s["from_fraction"])) % 1.0, 4),
        }
        for s in c.get("straights", [])
    ]
    out = {k: v for k, v in c.items() if k not in ("layouts", "numbering_verified", "map_note")}
    out.update(
        corners=corners, official_numbers=official, straights=straights, numbering_verified=False
    )
    out["map_note"] = (
        "derived: the base layout driven the other way (names kept, order reversed); not verified"
    )
    return out


def circuit_for_layout(track: str, layout: str = "", length_m: float | None = None) -> dict | None:
    """The table entry that describes the layout actually driven, or ``None`` if it does not describe it.

    * an entry in the circuit's ``layouts`` ({"<layout folder>": {...own corners/official_numbers/...}})
      overrides the base table for that layout;
    * a reverse layout of the base one reuses its names with the order reversed (derived, unverified);
    * a layout whose length is far from the table's (Red Bull Ring *national* vs the GP table) gets nothing:
      the base corners' fractions do not apply to it, and a wrong name is worse than none.
    """
    c = circuit_for(track)
    if c is None:
        return None
    key = _norm(layout or "")
    for name, entry in (c.get("layouts") or {}).items():
        if key and _norm(name) == key:
            return {**{k: v for k, v in c.items() if k != "layouts"}, **entry}
    base_len = float(c.get("length_m") or 0.0)
    near = (
        not length_m
        or not base_len
        or abs(length_m - base_len) / base_len <= LAYOUT_LENGTH_TOLERANCE
    )
    if key and is_reverse_layout(layout):
        return _mirror(c) if near else None
    return c if near else None


def _of_geometry(track: str | None, geometry) -> dict | None:
    return circuit_for_layout(
        track or getattr(geometry, "track", ""),
        getattr(geometry, "layout", "") or "",
        getattr(geometry, "length_m", None),
    )


@dataclass(frozen=True)
class Straight:
    """A named straight: from the exit of one corner to the entry of the next (fractions of the lap).

    ``to`` may be smaller than ``start`` when the straight crosses the start line (the pit straight)."""

    name: str
    start: float
    end: float
    role: str = "named"  # named | main | back

    def contains(self, fraction: float) -> bool:
        f = fraction % 1.0
        if self.start <= self.end:
            return self.start <= f <= self.end
        return f >= self.start or f <= self.end

    @property
    def fraction_of_lap(self) -> float:
        return (self.end - self.start) % 1.0


_GENERIC = {"straight", "recta", "gerade", "rectilineo", "the", "la", "el", "die", "der"}


def straights_for(track: str, geometry=None) -> list[Straight]:
    c = _of_geometry(track, geometry) if geometry is not None else circuit_for(track)
    if not c:
        return []
    return [
        Straight(
            str(s["name"]),
            float(s["from_fraction"]),
            float(s["to_fraction"]),
            str(s.get("role", "named")),
        )
        for s in c.get("straights", [])
    ]


def straight_at(track: str, fraction: float, geometry=None) -> Straight | None:
    """The named straight a position of the lap is on, if any."""
    return next((s for s in straights_for(track, geometry) if s.contains(fraction)), None)


# generic straights: any circuit has a main straight and most have a back straight, named or not
MIN_MAIN_STRAIGHT_M = 250.0
MIN_BACK_STRAIGHT_M = 400.0
GENERIC_NAMES = {
    "main": {"es": "la recta principal", "en": "the main straight", "de": "die Start-Ziel-Gerade"},
    "back": {"es": "la recta trasera", "en": "the back straight", "de": "die Gegengerade"},
}
_MAIN_WORDS = (
    "recta principal",
    "recta de meta",
    "recta de boxes",
    "recta de salida",
    "main straight",
    "front straight",
    "pit straight",
    "start finish straight",
    "start ziel gerade",
    "zielgerade",
    "startgerade",
    "boxengerade",
)
_BACK_WORDS = (
    "recta trasera",
    "contrarrecta",
    "recta opuesta",
    "back straight",
    "backstraight",
    "far straight",
    "gegengerade",
    "hintere gerade",
)


def generic_straights(geometry) -> list[Straight]:
    """The main straight (the one that holds the start line) and the back straight (the longest of the others).

    Straights are the stretches between the corners of the line; below ``MIN_MAIN_STRAIGHT_M`` there is
    no main straight to speak of, and the back straight must be at least ``MIN_BACK_STRAIGHT_M`` long."""
    corners = getattr(geometry, "corners", None)
    length = float(getattr(geometry, "length_m", 0) or 0)
    if not corners:
        return []
    cs = [c for c in corners if c.start_m <= c.end_m] or list(corners)
    if length <= 0 or len(cs) < 2:
        return []
    cs = sorted(cs, key=lambda c: c.start_m)
    gaps: list[
        tuple[float, float]
    ] = []  # (from_m, to_m) between the end of one corner and the start of the next
    for i, c in enumerate(cs):
        nxt = cs[(i + 1) % len(cs)]
        a, b = c.end_m, nxt.start_m
        size = (b - a) % length
        if size > 1.0:
            gaps.append((a, b))
    out: list[Straight] = []
    main = None
    for a, b in gaps:
        if (a > b) and (b - a) % length >= MIN_MAIN_STRAIGHT_M:
            main = (a, b)
    if main is not None:
        out.append(Straight("main", main[0] / length, main[1] / length, "main"))
    rest = [(a, b) for a, b in gaps if (a, b) != main]
    if rest:
        a, b = max(rest, key=lambda g: (g[1] - g[0]) % length)
        if (b - a) % length >= MIN_BACK_STRAIGHT_M:
            out.append(Straight("back", a / length, b / length, "back"))
    return out


def all_straights(track: str, geometry=None) -> list[Straight]:
    """The table's named straights first, then the generic main/back ones that do not overlap a named one."""
    named = straights_for(track, geometry)
    out = list(named)
    if geometry is not None:
        roles = {s.role for s in named}
        for g in generic_straights(geometry):
            mid = (g.start + g.fraction_of_lap / 2) % 1.0
            # a named straight that is the main or back one (Monza's Rettifilo Tribune) replaces the generic
            if g.role in roles or any(n.contains(mid) for n in named):
                continue
            out.append(g)
    return out


def find_straight(track: str, query: str, geometry=None) -> Straight | None:
    """A straight in free text: "la recta Rahal", "Rahal Straight", "la recta principal", "back straight"."""
    q = _norm_words(query)
    padded = f" {q} "
    for s in straights_for(track, geometry):
        words = [w for w in _norm_words(s.name).split() if w not in _GENERIC]
        if words and all(w in q.split() for w in words):
            return s
    wanted = None
    if any(f" {w} " in padded for w in _MAIN_WORDS):
        wanted = "main"
    elif any(f" {w} " in padded for w in _BACK_WORDS):
        wanted = "back"
    if wanted:
        return next((g for g in all_straights(track, geometry) if g.role == wanted), None)
    return None


def straight_label(s: Straight, lang: str) -> str:
    """The name a straight is spoken with: its own, or the generic one in the language."""
    if s.role in GENERIC_NAMES and s.name in (
        "main",
        "back",
    ):  # the generic ones have no name of their own
        return GENERIC_NAMES[s.role].get(lang, GENERIC_NAMES[s.role]["en"])
    return s.name


def _norm_words(s: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", s.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def official_map(geometry, track: str | None = None) -> dict[int, int] | None:
    """{apex number: the number of the circuit's own map}, or ``None`` when the table has no such list.

    The apexes of the game's line are numbered by the line; a published map numbers the turns its own way
    (it groups or skips some). The table lists the map's numbers with the lap fraction of each, and every one
    is given to the nearest apex within ``TOLERANCE``. An apex with no map number simply has none.
    """
    circuit = _of_geometry(track, geometry)
    entries = circuit.get("official_numbers") if circuit else None
    if not entries:
        return None
    out: dict[int, int] = {}
    for e in entries:
        f = float(e["apex_fraction"])
        best, best_d = None, TOLERANCE
        for c in geometry.apexes:
            d = abs(((f - c.apex_fraction) + 0.5) % 1.0 - 0.5)
            if d <= best_d:
                best, best_d = c, d
        if best is not None:
            out[best.number] = int(e["number"])
    return out


def _shared_numbers(geometry, track: str | None = None) -> dict[int, int]:
    """{map number: apex} for the numbers that lost their apex to a later number on the same one."""
    circuit = _of_geometry(track, geometry)
    entries = (circuit or {}).get("official_numbers") or []
    out: dict[int, int] = {}
    for e in entries:
        f = float(e["apex_fraction"])
        best, best_d = None, TOLERANCE
        for c in geometry.apexes:
            d = abs(((f - c.apex_fraction) + 0.5) % 1.0 - 0.5)
            if d <= best_d:
                best, best_d = c, d
        if best is not None:
            out.setdefault(int(e["number"]), best.number)
    return out


def attach_official(geometry, track: str | None = None) -> None:
    """Store the map numbering on the geometry (``geometry.official``) for everything that speaks numbers."""
    geometry.official = official_map(geometry, track)  # type: ignore[attr-defined]
    geometry.official_shared = _shared_numbers(geometry, track)  # type: ignore[attr-defined]


def official_of(geometry, apex_number: int) -> int | None:
    """The number to say for an apex: the map's, or the apex's own when the circuit has no map list."""
    m = getattr(geometry, "official", None)
    if m is None:
        return apex_number
    return m.get(apex_number)


def apex_of(geometry, number: int) -> int | None:
    """The apex a number typed or said by the driver refers to (the map's numbering when there is one)."""
    m = getattr(geometry, "official", None)
    if m is None:
        return number
    shared = getattr(geometry, "official_shared", None) or {}
    if number in shared:  # two map numbers on one apex (Maggotts 10-11 at Silverstone)
        return shared[number]
    return next((a for a, o in m.items() if o == number), None)


def corner_names(geometry: TrackGeometry, track: str | None = None) -> dict[int, str]:
    """{apex number: name}: each table corner is given to the nearest apex within ``TOLERANCE`` of the lap.

    Two table corners that fall on the same apex are joined ("Les Combes / Malmedy"); an apex that no
    table corner is near keeps its number alone.
    """
    circuit = _of_geometry(track, geometry)
    if circuit is None:
        return {}
    names: dict[int, list[str]] = {}
    apexes = geometry.apexes
    for entry in circuit.get("corners", []):
        f = float(entry["apex_fraction"])
        best, best_d = None, TOLERANCE
        for c in apexes:
            d = abs(((f - c.apex_fraction) + 0.5) % 1.0 - 0.5)
            if d <= best_d:
                best, best_d = c, d
        if best is not None:
            names.setdefault(best.number, []).append(entry["name"])
    return {n: " / ".join(v) for n, v in names.items()}
