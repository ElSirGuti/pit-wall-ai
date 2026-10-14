"""Corner names of the circuits that have an own table (``data/circuits.json``).

Names are attached to the apexes of the geometry by position, only when a table corner is within
``TOLERANCE`` of the apex. A corner without a name keeps its number only; nothing is
guessed. A track with an empty table (Magione, Monza...) gets no names at all.
"""

from __future__ import annotations

import json
import re
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
