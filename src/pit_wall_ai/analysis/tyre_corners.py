"""Where the tyres are being worked hardest: a load index per corner and phase (phase 8c, section 3.4).

This is an **estimate**, not a measurement. The game gives wear per tyre and per lap, never per corner, so a
proxy is used: the time integral of ``|wheel slip| x wheel load`` while the car is in a corner window, split by
what the driver was doing (braking, turning, on the throttle). The proxy only becomes something the engineer
may say after :func:`validate` shows it follows the measured wear on recorded laps; until then the command
reports "not validated" and nothing is voiced.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

from ..telemetry.snapshot import Snapshot
from .track_geometry import Corner

PHASES = ("brake", "turn", "throttle")
BRAKE_ON = 0.10
THROTTLE_ON = 0.30
TURN_STEER = 0.05  # |steer| (normalised -1..1) above which the car counts as turning
MIN_SPEED_KMH = 30.0
MAX_DT_S = 0.2  # a gap in the recording is not integrated
LOOKBACK_M = 70.0  # the braking zone before the first apex belongs to the corner
RUN_OUT_M = 15.0
FRONT, REAR = (0, 1), (2, 3)


@dataclass
class CornerLoad:
    corner: int
    # index per axle and phase, summed over the laps given (arbitrary units, comparable only to each other)
    front: dict[str, float] = field(default_factory=lambda: dict.fromkeys(PHASES, 0.0))
    rear: dict[str, float] = field(default_factory=lambda: dict.fromkeys(PHASES, 0.0))

    @property
    def front_total(self) -> float:
        return sum(self.front.values())

    @property
    def rear_total(self) -> float:
        return sum(self.rear.values())

    def dominant(self, axle: str = "front") -> str:
        d = self.front if axle == "front" else self.rear
        return max(d, key=d.get)


@dataclass
class LapLoad:
    lap: int
    corners: dict[int, CornerLoad]
    total_front: float
    total_rear: float
    wear_drop: tuple[
        float, float, float, float
    ]  # percentage points lost per wheel (FL, FR, RL, RR)
    samples: int


def corner_of(m: float, corners: list[Corner]) -> int | None:
    """The corner a position (metres along the line) belongs to; the nearest apex when windows overlap."""
    best: tuple[float, int] | None = None
    for c in corners:
        if c.start_m > c.end_m:
            continue
        entry = min((s.distance_m for s in c.sub_apexes), default=c.apex_m)
        lo = min(c.start_m, entry - LOOKBACK_M)
        if lo <= m <= c.end_m + RUN_OUT_M:
            d = abs(m - c.apex_m)
            if best is None or d < best[0]:
                best = (d, c.number)
    return best[1] if best else None


def phase_of(brake: float, gas: float, steer: float) -> str | None:
    if brake >= BRAKE_ON:
        return "brake"
    if gas >= THROTTLE_ON:
        return "throttle"
    if abs(steer) >= TURN_STEER:
        return "turn"
    return None


def lap_loads(snaps: Iterable[Snapshot], corners: list[Corner], length_m: float) -> list[LapLoad]:
    """One :class:`LapLoad` per complete lap in the stream (pit laps and partial laps are left out)."""
    by_lap: dict[int, list[Snapshot]] = defaultdict(list)
    for s in snaps:
        if not s.is_in_pit:
            by_lap[s.completed_laps].append(s)
    out: list[LapLoad] = []
    for lap, ss in sorted(by_lap.items()):
        if len(ss) < 600 or lap == 0:  # an out lap or a fragment
            continue
        pos = np.array([s.normalized_pos for s in ss])
        if pos.min() > 0.1 or pos.max() < 0.9:
            continue
        loads: dict[int, CornerLoad] = {}
        tf = tr = 0.0
        prev_t = ss[0].t
        n = 0
        for s in ss:
            dt = min(max(s.t - prev_t, 0.0), MAX_DT_S)
            prev_t = s.t
            if s.speed_kmh < MIN_SPEED_KMH:
                continue
            ph = phase_of(s.brake, s.gas, s.steer_angle)
            if ph is None:
                continue
            slip = [abs(x) for x in s.wheel_slip]
            load = s.wheel_load
            f = dt * sum(slip[i] * load[i] for i in FRONT) / 1e3
            r = dt * sum(slip[i] * load[i] for i in REAR) / 1e3
            tf += f
            tr += r
            n += 1
            cn = corner_of(s.normalized_pos * length_m, corners)
            if cn is None:
                continue
            cl = loads.setdefault(cn, CornerLoad(cn))
            cl.front[ph] += f
            cl.rear[ph] += r
        a, b = ss[0].tyre_wear, ss[-1].tyre_wear
        drop = tuple(float(a[i] - b[i]) for i in range(4))
        out.append(LapLoad(lap, loads, tf, tr, drop, n))  # type: ignore[arg-type]
    return out


def drop_incident_laps(laps: list[LapLoad], factor: float = 2.0) -> tuple[list[LapLoad], list[int]]:
    """Laps whose total load is far above the median are spins or off-track laps, not driving style."""
    if len(laps) < 3:
        return laps, []
    tot = sorted(lp.total_front + lp.total_rear for lp in laps)
    median = tot[len(tot) // 2]
    keep = [lp for lp in laps if lp.total_front + lp.total_rear <= factor * median]
    return keep, [lp.lap for lp in laps if lp not in keep]


def ranking(laps: list[LapLoad], axle: str = "front") -> list[tuple[int, float, str]]:
    """[(corner, share of the axle's load 0..1, dominant phase)] from the highest, over all the laps."""
    agg: dict[int, CornerLoad] = {}
    for lp in laps:
        for n, cl in lp.corners.items():
            a = agg.setdefault(n, CornerLoad(n))
            for ph in PHASES:
                a.front[ph] += cl.front[ph]
                a.rear[ph] += cl.rear[ph]
    tot = sum((c.front_total if axle == "front" else c.rear_total) for c in agg.values())
    if tot <= 0:
        return []
    rows = [
        (n, (c.front_total if axle == "front" else c.rear_total) / tot, c.dominant(axle))
        for n, c in agg.items()
    ]
    return sorted(rows, key=lambda r: r[1], reverse=True)


@dataclass(frozen=True)
class Validation:
    ok: bool
    laps_used: int
    r: float | None
    reason: str


MIN_LAPS = 6
MIN_R = 0.6
MIN_WEAR_SPAN = 1.0  # percentage points the measured wear must move over the stint to mean anything


def validate(laps: list[LapLoad]) -> Validation:
    """Does the proxy follow the measured wear lap by lap? Only then may the engineer use it."""
    rows = [(lp.total_front + lp.total_rear, sum(max(0.0, d) for d in lp.wear_drop)) for lp in laps]
    span = sum(w for _, w in rows)
    if len(rows) < MIN_LAPS:
        return Validation(
            False, len(rows), None, f"only {len(rows)} complete laps (need {MIN_LAPS})"
        )
    if span < MIN_WEAR_SPAN:
        return Validation(
            False,
            len(rows),
            None,
            f"the measured wear moved only {span:.2f} points over {len(rows)} laps "
            f"(need {MIN_WEAR_SPAN:g}): this car or setting barely wears the tyres, so nothing can be checked",
        )
    x = np.array([a for a, _ in rows])
    y = np.array([b for _, b in rows])
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return Validation(False, len(rows), None, "no variation between laps")
    r = float(np.corrcoef(x, y)[0, 1])
    if not math.isfinite(r):
        return Validation(False, len(rows), None, "undefined correlation")
    if r < MIN_R:
        return Validation(
            False, len(rows), r, f"correlation with the measured wear is {r:.2f} (need {MIN_R:g})"
        )
    return Validation(True, len(rows), r, f"correlation {r:.2f} over {len(rows)} laps")


ADVICE_ES = {
    (
        "front",
        "brake",
    ): "bloqueas o castigas los delanteros al frenar: frena más recto y suelta antes de girar",
    ("front", "turn"): "metes mucho volante en el giro: entra un poco más lento",
    ("front", "throttle"): "los delanteros patinan al acelerar: abre el volante antes de dar gas",
    ("rear", "brake"): "los traseros se descargan al frenar: revisa el reparto de frenada",
    ("rear", "turn"): "los traseros se deslizan en el giro: menos gas a mitad de curva",
    ("rear", "throttle"): "patinan los traseros al acelerar: dosifica el gas en la salida",
}
