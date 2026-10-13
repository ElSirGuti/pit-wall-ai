"""Corner names of the circuits that have an own table (``data/circuits.json``).

Names are attached to the geometric corners by position, only when a table corner falls inside the corner's
extent (or within ``TOLERANCE`` of its apex). A corner without a name keeps its number only; nothing is
guessed. A track with an empty table (Magione, Monza...) gets no names at all.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources

from .track_geometry import Corner, TrackGeometry

TOLERANCE = 0.012  # fraction of the lap (~85 m at Spa) between a table apex and the geometric apex


@lru_cache(maxsize=1)
def _table() -> list[dict]:
    text = resources.files("pit_wall_ai").joinpath("data/circuits.json").read_text(encoding="utf-8")
    return json.loads(text)["circuits"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def circuit_for(track: str) -> dict | None:
    """The table entry whose id or aliases match the game's track folder name (``spa``, ``fn_imola``...)."""
    t = _norm(track)
    for c in _table():
        if t in {_norm(c["id"]), *(_norm(a) for a in c.get("aliases", []))}:
            return c
    return None


def _inside(f: float, a: float, b: float, pad: float = 0.0) -> bool:
    a, b = (a - pad) % 1.0, (b + pad) % 1.0
    return (a <= f <= b) if a <= b else (f >= a or f <= b)


def corner_names(geometry: TrackGeometry, track: str | None = None) -> dict[int, str]:
    """{corner number: name} for the geometric corners that contain a table corner."""
    circuit = circuit_for(track or geometry.track)
    if circuit is None:
        return {}
    names: dict[int, list[str]] = {}
    for entry in circuit.get("corners", []):
        f = float(entry["apex_fraction"])
        best: tuple[float, Corner] | None = None
        for c in geometry.corners:
            if _inside(f, c.start_fraction, c.end_fraction, pad=TOLERANCE / 2):
                d = abs(((f - c.apex_fraction) + 0.5) % 1.0 - 0.5)
                if best is None or d < best[0]:
                    best = (d, c)
        if best is not None:
            names.setdefault(best[1].number, []).append(entry["name"])
    return {n: " / ".join(v) for n, v in names.items()}
