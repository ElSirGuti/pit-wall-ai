"""What a pit stop costs, measured from the laps of a session, and a ranking of when to stop.

``measure`` pairs every in-lap with the out-lap that follows and compares the two against the typical flying lap: what the
stop took from the race, with its sample size. ``rank_stop_laps`` is the "what if" of the plan (stop one lap earlier or later)
for a stated tyre degradation and a pit loss, ordered by expected race time. Both are pure and say what they assumed.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .laps import LapRecord

MIN_REFERENCE_LAPS = 3  # fewer clean laps than this and there is no "typical lap" to compare with


@dataclass(frozen=True)
class PitStop:
    in_lap: int
    out_lap: int
    loss_s: float  # (in lap + out lap) - 2 x the typical lap


def measure(laps: list[LapRecord]) -> list[PitStop]:
    clean = [lap.time_ms for lap in laps if lap.eligible_for_best]
    if len(clean) < MIN_REFERENCE_LAPS:
        return []
    ref = statistics.median(clean)
    out: list[PitStop] = []
    for a, b in zip(laps, laps[1:], strict=False):
        if a.in_lap and b.out_lap and b.number == a.number + 1 and a.time_ms > 0 and b.time_ms > 0:
            out.append(
                PitStop(a.number, b.number, round((a.time_ms + b.time_ms - 2 * ref) / 1000.0, 2))
            )
    return out


def median_loss(stops: list[PitStop]) -> float | None:
    return round(statistics.median(s.loss_s for s in stops), 1) if stops else None


@dataclass(frozen=True)
class StopOption:
    stop_lap: int  # the lap on which the car comes in
    total_s: float  # expected race time
    delta_s: float  # against the best option


def rank_stop_laps(
    race_laps: int,
    base_lap_s: float,
    deg_s_per_lap: float,
    pit_loss_s: float,
    *,
    window: tuple[int, int] | None = None,
    max_stint: int | None = None,
) -> list[StopOption]:
    """One stop at each lap of the window: the time of the race with a lap getting slower by ``deg_s_per_lap`` for every
    lap on the tyres, and the tyres back to new after the stop. Best first. Empty when no lap is allowed."""
    lo, hi = window if window else (1, race_laps - 1)
    lo, hi = max(1, lo), min(race_laps - 1, hi)
    options: list[tuple[int, float]] = []
    for stop in range(lo, hi + 1):
        first, second = stop, race_laps - stop
        if max_stint and (first > max_stint or second > max_stint):
            continue

        def stint(n: int) -> float:
            return n * base_lap_s + deg_s_per_lap * n * (n - 1) / 2.0

        options.append((stop, stint(first) + stint(second) + pit_loss_s))
    if not options:
        return []
    best = min(t for _, t in options)
    ranked = sorted(options, key=lambda o: o[1])
    return [StopOption(s, round(t, 1), round(t - best, 1)) for s, t in ranked]
