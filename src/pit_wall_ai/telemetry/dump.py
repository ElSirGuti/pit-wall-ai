"""``pit-wall dump-shm``: show every field live and summarise value ranges, to CONFIRM units.

Nothing here asserts a unit. It prints what the game publishes and, over a watch window, the observed
range of each field plus a hint of which unit that range suggests. A human then compares a few values
with the in-game HUD (tyre pressure, fuel, steering wheel angle) before the README marks a unit verified.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, field

from .snapshot import CORNERS, Snapshot

PRIVATE_FIELDS = {"player_name", "player_surname"}


def flatten(snap: Snapshot, *, include_player: bool = False) -> dict[str, float | int | str | bool]:
    """One flat ``name -> value`` mapping; per-wheel and per-axis tuples become ``name.FL`` / ``name.x``."""
    out: dict[str, float | int | str | bool] = {}

    def put(prefix: str, value):
        if dataclasses.is_dataclass(value):
            for f in dataclasses.fields(value):
                if f.name in PRIVATE_FIELDS and not include_player:
                    continue
                put(f"{prefix}{f.name}", getattr(value, f.name))
        elif isinstance(value, tuple):
            labels = CORNERS if len(value) == 4 else ("x", "y", "z") if len(value) == 3 else None
            for i, v in enumerate(value):
                out[f"{prefix}.{labels[i] if labels else i}"] = v
        else:
            out[prefix] = value

    for f in dataclasses.fields(snap):
        if f.name in {"t", "static"}:
            continue
        put(f.name, getattr(snap, f.name))
    put("static.", snap.static)
    return out


@dataclass
class FieldRange:
    lo: float = math.inf
    hi: float = -math.inf
    total: float = 0.0
    n: int = 0

    def add(self, v: float) -> None:
        self.lo, self.hi = min(self.lo, v), max(self.hi, v)
        self.total += v
        self.n += 1

    @property
    def mean(self) -> float:
        return self.total / self.n if self.n else 0.0


@dataclass
class RangeTracker:
    ranges: dict[str, FieldRange] = field(default_factory=dict)
    samples: int = 0

    def add(self, snap: Snapshot) -> None:
        self.samples += 1
        for name, value in flatten(snap).items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                continue
            self.ranges.setdefault(name, FieldRange()).add(float(value))

    def report(self) -> dict[str, dict]:
        return {
            name: {
                "min": r.lo,
                "max": r.hi,
                "mean": round(r.mean, 4),
                "unit_hint": unit_hint(name, r),
            }
            for name, r in sorted(self.ranges.items())
        }


def unit_hint(name: str, r: FieldRange) -> str:
    """What the OBSERVED range suggests. A hint to compare with the HUD, never a verdict."""
    base = name.split(".")[0]
    span = max(abs(r.lo), abs(r.hi))
    if base == "steer_angle":
        return "degrees (range reaches >20)" if span > 20 else "radians or normalised (range <= 20)"
    if base in ("tyre_pressure",):
        return "psi (15-45)" if 10 <= r.mean <= 50 else "bar/kPa? mean outside 10-50"
    if base in ("tyre_core_temp", "tyre_temp_inner", "tyre_temp_middle", "tyre_temp_outer"):
        return "degrees C (20-150)" if 0 < r.mean < 200 else "unexpected for degrees C"
    if base == "brake_temp":
        return "degrees C (up to ~1000)" if 0 <= span < 1500 else "unexpected for degrees C"
    if base == "fuel":
        return "litres (a few to ~150)" if 0 <= span < 400 else "unexpected for litres"
    if base == "tyre_wear":
        return "percent 0-100 (100 = new?)" if span > 1.5 else "fraction 0-1"
    if base in ("gas", "brake", "clutch"):
        return "0..1" if span <= 1.01 else "0..100 (percent)"
    if base == "speed_kmh":
        return "km/h"
    if base == "acc_g":
        return "g" if span < 8 else "m/s^2? (range > 8)"
    if base == "gear":
        return f"raw gear index (observed {r.lo:.0f}..{r.hi:.0f}); confirm 0=R/1=N against the HUD"
    if base in (
        "wheel_slip",
        "wheel_load",
        "wheel_angular_speed",
        "suspension_travel",
        "heading",
        "pitch",
        "roll",
    ):
        return "see observed range"
    return ""


def format_dump(snap: Snapshot, *, include_player: bool = False) -> str:
    rows = flatten(snap, include_player=include_player)
    width = max(len(k) for k in rows)
    lines = [f"{k:<{width}}  {v!r}" for k, v in rows.items()]
    return "\n".join(lines)


def report_json(
    tracker: RangeTracker, snap: Snapshot | None, *, include_player: bool = False
) -> str:
    payload = {
        "samples": tracker.samples,
        "last_sample": flatten(snap, include_player=include_player) if snap else None,
        "ranges": tracker.report(),
        "note": "unit_hint is a suggestion from the observed range; verify against the in-game HUD.",
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
