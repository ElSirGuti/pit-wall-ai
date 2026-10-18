"""``pit-wall plan``: the race planner from the terminal, with the numbers you give it (no game needed)."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from ..config import AppConfig
from .pit_loss import rank_stop_laps
from .plan import FuelProfile, fuel_to_finish, plan_stops, race_laps


def _window(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    lo, hi = text.split("-", 1)
    return int(lo), int(hi)


def cmd_plan(cfg: AppConfig, a: argparse.Namespace, *, out: Callable[[str], None] = print) -> int:
    total = race_laps(laps=a.laps, minutes=a.minutes, lap_time_s=a.lap_time)
    if total is None:
        out("give --laps, or --minutes with --lap-time")
        return 2
    prof = FuelProfile(a.fuel_per_lap, a.spread, max(a.profile_laps, 1))
    out(
        f"race: {total} laps"
        + (
            f" ({a.minutes:g} min at {a.lap_time:g} s a lap, with the extra lap)"
            if a.minutes
            else ""
        )
    )
    fp = fuel_to_finish(prof, total)
    out(f"no stop: {fp.litres:.1f} L at the start ({fp.assumptions})")
    window = _window(a.window)
    if a.tank:
        stops_list = [a.stops] if a.stops is not None else [0, 1, 2]
        for k in stops_list:
            plan = plan_stops(prof, total, a.tank, stops=k, window=window)
            if not plan.feasible:
                out(f"{k} stop(s): not possible - {plan.reason}")
                continue
            stints = ", ".join(f"{s.laps} laps ({s.fuel_start_l:.1f} L)" for s in plan.stints)
            at = ", ".join(str(x) for x in plan.stop_laps) or "-"
            out(f"{k} stop(s): in on lap {at}; stints {stints}")
    if a.deg is not None and a.pit_loss is not None and a.lap_time:
        ranked = rank_stop_laps(total, a.lap_time, a.deg, a.pit_loss, window=window)
        if ranked:
            out(
                f"\none stop, best laps to come in (degradation {a.deg:g} s per lap, stop costs {a.pit_loss:g} s):"
            )
            for o in ranked[:5]:
                out(f"  lap {o.stop_lap:3d}   +{o.delta_s:5.1f} s   ({o.total_s:.1f} s)")
    elif a.deg is not None or a.pit_loss is not None:
        out("\nto rank the stop laps give --deg, --pit-loss and --lap-time")
    return 0
