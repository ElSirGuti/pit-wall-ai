"""``pit-wall tyre-corners FILE``: which corners work the tyres hardest, and whether that can be trusted."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from ..config import AppConfig
from .circuit_names import corner_names
from .offline import iter_snapshots
from .tracks import resolve
from .tyre_corners import (
    ADVICE_ES,
    drop_incident_laps,
    lap_loads,
    ranking,
    validate,
    validate_reference,
)


def cmd_tyre_corners(
    cfg: AppConfig, a: argparse.Namespace, *, out: Callable[[str], None] = print
) -> int:
    try:
        static, snaps = iter_snapshots(a.file, session=a.session)
        snaps = list(snaps)
    except (OSError, ValueError) as exc:
        print(f"cannot read {a.file}: {exc}", file=sys.stderr)
        return 2
    res = resolve(
        static.track,
        static.track_configuration,
        length_m=static.track_length_m or None,
        configured=[*(a.ac_dir or []), *cfg.analysis.ac_dirs],
    )
    if not res.ok or res.geometry is None:
        print(f"no geometry for {static.track}: {res.reason}", file=sys.stderr)
        return 1
    g = res.geometry
    names = corner_names(g, static.track)
    laps = lap_loads(snaps, g.apexes, g.length_m)
    laps, dropped = drop_incident_laps(laps)
    out(f"{static.car_model} @ {static.track}: {len(laps)} complete laps analysed")
    if dropped:
        out(f"laps left out as incidents (load far above the median): {dropped}")
    if not laps:
        out("no complete lap in the recording")
        return 1
    v = validate(laps)
    if a.reference and not v.ok:
        try:
            start = [float(x) for x in (a.start_wear or "100,100,100,100").split(",")]
            end = [float(x) for x in a.reference.split(",")]
            assert len(start) == len(end) == 4
        except (ValueError, AssertionError):
            print("--reference and --start-wear need four numbers: FL,FR,RL,RR", file=sys.stderr)
            return 2
        ref = validate_reference(laps, tuple(s - e for s, e in zip(start, end, strict=True)))
        v = ref
        out("(the game's wear channel does not move: using the reference you gave)")
    out(("VALIDATED: " if v.ok else "NOT VALIDATED (an estimate, not to be voiced): ") + v.reason)
    for axle in ("front", "rear"):
        rows = ranking(laps, axle)[: a.top]
        out(f"\n{axle} axle, share of the slip x load index by corner:")
        for n, share, phase in rows:
            label = names.get(n, "")
            out(
                f"  corner {n:2d} {label:18s} {share * 100:5.1f} %  mostly {phase:8s}"
                f"  -> {ADVICE_ES[(axle, phase)]}"
            )
    return 0
