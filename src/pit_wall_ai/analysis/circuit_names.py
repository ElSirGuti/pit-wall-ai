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


def numbering_verified(track: str) -> bool:
    """The author checked this circuit's corner numbering against the real circuit (flag in the table)."""
    c = circuit_for(track)
    return bool(c and c.get("numbering_verified"))


def table_names(track: str) -> dict[int, str] | None:
    """{number: name} straight from the table, only for a circuit whose numbering was verified.

    It lets the chat answer "which corner is the Prima Esse?" on a machine without the game installed
    (no racing line). For an unverified circuit the table order is not known to be the circuit's numbering,
    so nothing is returned.
    """
    c = circuit_for(track)
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


def straights_for(track: str) -> list[Straight]:
    c = circuit_for(track)
    if not c:
        return []
    return [
        Straight(str(s["name"]), float(s["from_fraction"]), float(s["to_fraction"]))
        for s in c.get("straights", [])
    ]


def straight_at(track: str, fraction: float) -> Straight | None:
    """The named straight a position of the lap is on, if any."""
    return next((s for s in straights_for(track) if s.contains(fraction)), None)


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
    named = straights_for(track)
    out = list(named)
    if geometry is not None:
        for g in generic_straights(geometry):
            mid = (g.start + g.fraction_of_lap / 2) % 1.0
            if not any(n.contains(mid) for n in named):
                out.append(g)
    return out


def find_straight(track: str, query: str, geometry=None) -> Straight | None:
    """A straight in free text: "la recta Rahal", "Rahal Straight", "la recta principal", "back straight"."""
    q = _norm_words(query)
    padded = f" {q} "
    for s in straights_for(track):
        words = [w for w in _norm_words(s.name).split() if w not in _GENERIC]
        if words and all(w in q.split() for w in words):
            return s
    if geometry is not None:
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
    if s.role in GENERIC_NAMES:
        return GENERIC_NAMES[s.role].get(lang, GENERIC_NAMES[s.role]["en"])
    return s.name


def _norm_words(s: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", s.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def corner_names(geometry: TrackGeometry, track: str | None = None) -> dict[int, str]:
    """{apex number: name}: each table corner is given to the nearest apex within ``TOLERANCE`` of the lap.

    Two table corners that fall on the same apex are joined ("Les Combes / Malmedy"); an apex that no
    table corner is near keeps its number alone.
    """
    circuit = circuit_for(track or geometry.track)
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
