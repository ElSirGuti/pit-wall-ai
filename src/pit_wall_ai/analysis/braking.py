"""Braking per corner and the comparison of a lap against a reference lap.

Everything here works on the :class:`~pit_wall_ai.analysis.laps.LapTrace` of a lap (brake, gas, steering,
speed and elapsed time over the lap distance) and the corners of the track geometry. The numbers are
*measured facts* (where the brake went on, how hard, the slowest point, the time through the corner); the
comparison states differences and does not interpret them.

Definitions (the thresholds are in ``analysis.braking`` of the config):

* **Braking point**: where the brake pedal first goes above ``brake_on`` in the last braking event before
  the corner whose peak reaches ``min_peak``. Events separated by less than ``merge_gap_m`` are one event.
  The search starts at the last apex of the previous corner (at most ``lookback_m`` before the corner entry).
  A corner that is taken without that braking (flat out or a lift) has no braking point.
* **Span** of a corner, used for the time: from ``10 m`` before the *reference* braking point (or
  ``20 m`` before the corner start if the reference did not brake) to the end of the corner. The same span
  is used for both laps, so the time difference is "entry to exit" of the whole corner.
* When the pedal was already down at the start of the search window (the braking of the previous corner
  runs into this one) the braking point is not comparable: no distance is reported for that corner.
* A corner that crosses the start/finish line is skipped (a lap trace cannot hold both sides of it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..config import BrakingConfig
from .laps import LapRecord, LapTrace
from .track_geometry import Corner, TrackGeometry

SPAN_BEFORE_BRAKE_M = 10.0
SPAN_BEFORE_CORNER_M = 20.0
ENTRY_SLACK_M = 5.0  # a braking event may start this far past the entry and still be its braking
TRAIL_STEER = 0.10  # |steering| (fraction of the lock) above which the car is turning


@dataclass(frozen=True)
class CornerMetrics:
    corner: int
    brake_point_m: float | None  # position along the lap where braking began
    brake_end_m: float | None
    peak_brake: float | None
    entry_speed_kmh: float | None  # speed at the braking point
    min_speed_kmh: float | None
    min_speed_m: float | None
    throttle_on_m: (
        float | None
    )  # first position after the slowest point with the throttle above the limit
    trail_m: float | None  # metres of braking while already turning
    clipped: bool = False  # braking was already on at the start of the search window

    @property
    def braked(self) -> bool:
        return self.brake_point_m is not None


@dataclass(frozen=True)
class CornerComparison:
    corner: int
    lap: int
    reference: int
    brake_earlier_m: (
        float | None
    )  # + = this lap brakes earlier than the reference; None if either did not brake
    time_diff_s: float | None  # + = this lap is slower through the corner span
    min_speed_delta_kmh: float | None
    entry_speed_delta_kmh: float | None
    throttle_later_m: float | None  # + = throttle comes later
    span_m: tuple[float, float]
    lap_metrics: CornerMetrics
    ref_metrics: CornerMetrics

    def significant(self, cfg: BrakingConfig) -> bool:
        """Worth saying: it braked differently by the minimum distance AND it cost the minimum time."""
        return (
            self.brake_earlier_m is not None
            and self.time_diff_s is not None
            and abs(self.brake_earlier_m) >= cfg.min_distance_m
            and self.time_diff_s >= cfg.min_time_s
        )


def _dist(trace: LapTrace, length_m: float) -> np.ndarray:
    return trace.pos * length_m


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end] index pairs (inclusive) of consecutive True values."""
    if not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def usable(corner: Corner) -> bool:
    return corner.start_m <= corner.end_m  # a wrapped corner has start > end


def entry_m(corner: Corner) -> float:
    return min((s.distance_m for s in corner.sub_apexes), default=corner.apex_m)


def corner_metrics(
    trace: LapTrace,
    length_m: float,
    corner: Corner,
    prev_apex_m: float,
    cfg: BrakingConfig,
) -> CornerMetrics:
    d = _dist(trace, length_m)
    brake = np.nan_to_num(trace.brake, nan=0.0)
    gas = np.nan_to_num(trace.gas, nan=0.0)
    steer = np.nan_to_num(trace.steer, nan=0.0)
    speed = trace.speed
    target = entry_m(corner)
    # the search starts at the last apex of the previous corner: braking for this one can begin inside the
    # previous corner's extent (before its geometric end), but never before its own last apex
    lo = max(prev_apex_m, target - cfg.lookback_m, 0.0)
    hi = corner.apex_m
    window = (d >= lo) & (d <= hi)
    best: tuple[int, int] | None = None
    clipped = False
    if window.any():
        on = (brake >= cfg.brake_on) & window
        runs = _runs(on)
        merged: list[list[int]] = []
        step = float(np.median(np.diff(d))) if len(d) > 1 else 1.0
        gap = max(1, round(cfg.merge_gap_m / max(step, 1e-6)))
        for a, b in runs:
            if merged and a - merged[-1][1] <= gap:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        # the braking of the corner starts at or before its entry (the first apex of a complex)
        good = [
            (a, b)
            for a, b in merged
            if float(np.max(brake[a : b + 1])) >= cfg.min_peak and d[a] <= target + ENTRY_SLACK_M
        ]
        if good:
            best = good[-1]
            first_in_window = int(np.argmax(window))
            clipped = best[0] <= first_in_window
    in_corner = (d >= corner.start_m) & (d <= corner.end_m)
    seg = in_corner & np.isfinite(speed)
    min_v = min_at = None
    if seg.any():
        idx = np.flatnonzero(seg)
        j = idx[int(np.argmin(speed[idx]))]
        min_v, min_at = float(speed[j]), float(d[j])
    thr_on = None
    if min_at is not None:
        after = (d >= min_at) & (gas >= cfg.throttle_on)
        if after.any():
            thr_on = float(d[int(np.argmax(after))])
    if best is None:
        return CornerMetrics(corner.number, None, None, None, None, min_v, min_at, thr_on, None)
    a, b = best
    turning = np.abs(steer[a : b + 1]) >= TRAIL_STEER
    trail = float(turning.sum()) * float(np.median(np.diff(d))) if len(d) > 1 else 0.0
    sp = speed[a]
    return CornerMetrics(
        corner=corner.number,
        brake_point_m=float(d[a]),
        brake_end_m=float(d[b]),
        peak_brake=float(np.max(brake[a : b + 1])),
        entry_speed_kmh=float(sp) if math.isfinite(sp) else None,
        min_speed_kmh=min_v,
        min_speed_m=min_at,
        throttle_on_m=thr_on,
        trail_m=trail,
        clipped=clipped,
    )


def time_between(trace: LapTrace, length_m: float, a_m: float, b_m: float) -> float | None:
    """Seconds the lap took between two positions (None if that part was not recorded)."""
    ta, tb = trace.time_at(a_m / length_m), trace.time_at(b_m / length_m)
    if not (math.isfinite(ta) and math.isfinite(tb)) or tb < ta:
        return None
    return (tb - ta) / 1000.0


def lap_corner_metrics(
    geometry: TrackGeometry, lap: LapRecord, cfg: BrakingConfig
) -> dict[int, CornerMetrics]:
    if lap.trace is None:
        return {}
    out: dict[int, CornerMetrics] = {}
    prev_apex = 0.0
    for c in geometry.corners:
        if not usable(c):
            continue
        out[c.number] = corner_metrics(lap.trace, geometry.length_m, c, prev_apex, cfg)
        prev_apex = max(prev_apex, *(sa.distance_m for sa in c.sub_apexes), c.apex_m)
    return out


def compare_lap(
    geometry: TrackGeometry, lap: LapRecord, ref: LapRecord, cfg: BrakingConfig
) -> list[CornerComparison]:
    """Every usable corner of ``lap`` against the same corner of ``ref`` (never the same lap)."""
    if lap.trace is None or ref.trace is None or lap.number == ref.number:
        return []
    L = geometry.length_m
    lm = lap_corner_metrics(geometry, lap, cfg)
    rm = lap_corner_metrics(geometry, ref, cfg)
    out: list[CornerComparison] = []
    for c in geometry.corners:
        if c.number not in lm or c.number not in rm:
            continue
        a, b = lm[c.number], rm[c.number]
        if b.braked:
            start = b.brake_point_m - SPAN_BEFORE_BRAKE_M  # type: ignore[operator]
        else:
            start = c.start_m - SPAN_BEFORE_CORNER_M
        start = max(0.0, start)
        t_lap = time_between(lap.trace, L, start, c.end_m)
        t_ref = time_between(ref.trace, L, start, c.end_m)
        diff = None if t_lap is None or t_ref is None else t_lap - t_ref

        def delta(x, y):
            return None if x is None or y is None else x - y

        out.append(
            CornerComparison(
                corner=c.number,
                lap=lap.number,
                reference=ref.number,
                brake_earlier_m=(
                    None
                    if not (a.braked and b.braked) or a.clipped or b.clipped
                    else b.brake_point_m - a.brake_point_m  # type: ignore[operator]
                ),
                time_diff_s=diff,
                min_speed_delta_kmh=delta(a.min_speed_kmh, b.min_speed_kmh),
                entry_speed_delta_kmh=delta(a.entry_speed_kmh, b.entry_speed_kmh),
                throttle_later_m=delta(a.throttle_on_m, b.throttle_on_m),
                span_m=(start, c.end_m),
                lap_metrics=a,
                ref_metrics=b,
            )
        )
    return out


def reference_lap(laps: list[LapRecord], lap: LapRecord) -> LapRecord | None:
    """The best eligible (valid) lap of the session with the same tyre compound, or None.

    None when ``lap`` is itself valid and the best of them (nothing to compare it with), or when no other
    valid lap has that compound. Unknown compounds (``""``) only match each other. A lap that is not
    valid (off track for a while) can still be compared with the best valid lap, even when it was faster.
    """
    pool = [
        x
        for x in laps
        if x.trace is not None
        and x.compound == lap.compound
        and x.eligible_for_best
        and x.number != lap.number
    ]
    if not pool:
        return None
    best = min(pool, key=lambda x: x.time_ms)
    if lap.eligible_for_best and lap.time_ms <= best.time_ms:
        return None
    return best


def comparable(lap: LapRecord) -> bool:
    """A lap worth comparing corner by corner: complete and neither an out nor an in lap.

    ``suspect`` laps (off the track for a while) are included: only the corner where it happened is
    wrong, and the reference is always a valid lap.
    """
    return lap.trace is not None and not (lap.out_lap or lap.in_lap) and lap.coverage >= 0.9


SLOW_LAP_RATIO = 1.25


def representative(lap: LapRecord, ref: LapRecord) -> bool:
    """A lap within 25 % of the reference time. Slower than that it is an incident, a pit stop or a lap
    the log does not mark as out/in (MoTeC logs carry no pit flag): its corners say nothing about braking."""
    return lap.time_ms > 0 and lap.time_ms <= ref.time_ms * SLOW_LAP_RATIO


def worst_corner(
    comparisons: list[CornerComparison], cfg: BrakingConfig
) -> CornerComparison | None:
    """The single corner worth mentioning for a lap: the significant one that cost the most time."""
    sig = [c for c in comparisons if c.significant(cfg)]
    return max(sig, key=lambda c: c.time_diff_s or 0.0) if sig else None
