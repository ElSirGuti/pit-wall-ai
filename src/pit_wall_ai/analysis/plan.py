"""The race planner (``docs/PHASE8_PLAN.md`` 8a): fuel to finish, race length, where to stop. Pure functions.

Every number comes from here and every result carries the assumptions it used ("with 1.35 L per lap, from 6 laps,
spread 0.04"), so the driver can challenge it. Nothing here reads the game: it works on the laps already measured and on
the rules the driver stated.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from .laps import LapRecord

MIN_PROFILE_LAPS = 2  # fewer clean laps than this and the consumption is not known yet
DEFAULT_MARGIN_LAPS = 1.0 / 3.0  # a third of a lap of fuel on top, as the plan says


@dataclass(frozen=True)
class FuelProfile:
    per_lap_l: float  # weighted mean: the recent laps count more
    spread_l: float  # population deviation of the laps used (traffic, lifting)
    laps: int  # how many laps it was computed on

    def assumptions(self) -> str:
        return f"{self.per_lap_l:.2f} L per lap, from {self.laps} laps, spread {self.spread_l:.2f}"


@dataclass(frozen=True)
class FuelPlan:
    litres: float  # to have at the start (or in the tank now)
    race_laps: int
    per_lap_l: float
    margin_l: float
    assumptions: str


@dataclass(frozen=True)
class Stint:
    laps: int
    fuel_start_l: float
    ends_with_stop: bool


@dataclass(frozen=True)
class StopPlan:
    feasible: bool
    stints: list[Stint] = field(default_factory=list)
    stop_laps: list[int] = field(default_factory=list)  # the lap on which the car comes in
    reason: str = ""
    assumptions: str = ""


def fuel_profile(laps: Sequence[LapRecord], *, recent: int = 8) -> FuelProfile | None:
    """The consumption from the clean timed laps; the latest ``recent`` ones, newest weighted most."""
    clean = [
        lap.fuel_used
        for lap in laps
        if not (lap.out_lap or lap.in_lap) and lap.fuel_used > 0 and lap.time_ms > 0
    ][-recent:]
    if len(clean) < MIN_PROFILE_LAPS:
        return None
    weights = list(range(1, len(clean) + 1))
    mean = sum(w * x for w, x in zip(weights, clean)) / sum(weights)
    return FuelProfile(round(mean, 3), round(statistics.pstdev(clean), 3), len(clean))


def race_laps(
    *,
    laps: int | None = None,
    minutes: float | None = None,
    lap_time_s: float | None = None,
    extra_lap: bool = True,
) -> int | None:
    """How many laps the race has. By time: the laps that fit, plus the one after the time runs out (when the game
    gives the extra lap); ``None`` if there is no lap time to divide by."""
    if laps:
        return int(laps)
    if not minutes or not lap_time_s or lap_time_s <= 0:
        return None
    n = math.ceil(minutes * 60.0 / lap_time_s)
    return n + (1 if extra_lap else 0)


def fuel_to_finish(
    profile: FuelProfile,
    laps: int,
    *,
    margin_laps: float = DEFAULT_MARGIN_LAPS,
    spread_k: float = 1.0,
    laps_done: int = 0,
) -> FuelPlan:
    """The fuel needed for the laps still to drive: per lap x laps, plus the margin (a third of a lap and the spread
    of every lap still to go, so a noisy consumption asks for more)."""
    left = max(0, laps - laps_done)
    base = profile.per_lap_l * left
    margin = profile.per_lap_l * margin_laps + profile.spread_l * spread_k * math.sqrt(max(1, left))
    return FuelPlan(
        round(base + margin, 1), laps, profile.per_lap_l, round(margin, 1), profile.assumptions()
    )


def saving_per_lap(
    fuel_now: float, laps_left: float, profile: FuelProfile, *, margin_laps: float = 0.0
) -> float:
    """Litres per lap to save to arrive (positive = save that much, negative = room to push)."""
    if laps_left <= 0:
        return 0.0
    needed = profile.per_lap_l * (laps_left + margin_laps)
    return round((needed - fuel_now) / laps_left, 2)


def laps_in_tank(
    tank_l: float, profile: FuelProfile, *, reserve_laps: float = DEFAULT_MARGIN_LAPS
) -> int:
    """How many laps one tank gives, leaving the reserve."""
    if profile.per_lap_l <= 0:
        return 0
    return max(0, int(tank_l / profile.per_lap_l - reserve_laps))


def plan_stops(
    profile: FuelProfile,
    race_laps_total: int,
    tank_l: float,
    *,
    stops: int,
    window: tuple[int, int] | None = None,
    min_stint: int = 1,
    max_stint: int | None = None,
    fuel_only: bool = True,
) -> StopPlan:
    """Split a race into ``stops + 1`` stints of about equal length, within the tank, the window and the stint limits.

    ``fuel_only`` is not used by the maths (a splash of fuel and a tyre change cost different pit time, which is
    measured elsewhere); it is kept so the rule the driver stated travels with the plan.
    """
    a = profile.assumptions()
    cap = laps_in_tank(tank_l, profile)
    if max_stint:
        cap = min(cap, max_stint)
    n = stops + 1
    if cap <= 0:
        return StopPlan(
            False, reason="the tank does not give a lap with that consumption", assumptions=a
        )
    if cap * n < race_laps_total:
        need = math.ceil(race_laps_total / cap) - 1
        return StopPlan(
            False,
            reason=f"{stops} stop(s) are not enough: a tank gives {cap} laps, {need} stop(s) are needed",
            assumptions=a,
        )
    base, extra = divmod(race_laps_total, n)
    lengths = [base + (1 if i < extra else 0) for i in range(n)]
    if min(lengths) < min_stint:
        return StopPlan(
            False, reason=f"a stint would be shorter than {min_stint} lap(s)", assumptions=a
        )
    stop_laps, done = [], 0
    for ln in lengths[:-1]:
        done += ln
        stop_laps.append(done)
    if window:
        lo, hi = window
        if not all(lo <= s <= hi for s in stop_laps):
            return StopPlan(
                False,
                stints=[],
                stop_laps=stop_laps,
                reason=f"even stints would stop on laps {stop_laps}, outside the window {lo}-{hi}",
                assumptions=a,
            )
    stints = [
        Stint(
            ln,
            round(min(tank_l, profile.per_lap_l * ln + profile.per_lap_l * DEFAULT_MARGIN_LAPS), 1),
            i < n - 1,
        )
        for i, ln in enumerate(lengths)
    ]
    return StopPlan(True, stints, stop_laps, assumptions=a)
