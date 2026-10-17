"""The compact summary of the session that the chat works from (and the model receives).

Built from the same objects the dashboard and the engineer use. Only fields whose unit status is *verified* or
*plausible* in the README table are included; a sensor that does not move (brake temperature on some cars) is
left out and replaced by a note, so the model is never invited to reason about a number that means nothing.

Everything goes through :class:`FactSheet`, so each number keeps the identity it belongs to (see ``facts.py``).
"""

from __future__ import annotations

import math
import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..analysis import braking as B
from ..analysis.circuit_names import official_of
from ..analysis.laps import CurrentLap, LapRecord
from ..analysis.trackctx import TrackInfo
from ..config import AppConfig
from ..engineer.engine import AlertRecord
from ..engineer.rules import FuelRule
from ..session.detector import SessionState
from ..telemetry.snapshot import CORNERS, Snapshot
from .facts import Fact, FactSheet, Subject, norm

WHEEL_ALIASES = {
    "FL": (
        "fl",
        "delantero izquierdo",
        "delantera izquierda",
        "front left",
        "neumatico delantero izquierdo",
    ),
    "FR": (
        "fr",
        "delantero derecho",
        "delantera derecha",
        "front right",
        "neumatico delantero derecho",
    ),
    "RL": (
        "rl",
        "trasero izquierdo",
        "trasera izquierda",
        "rear left",
        "neumatico trasero izquierdo",
    ),
    "RR": ("rr", "trasero derecho", "trasera derecha", "rear right", "neumatico trasero derecho"),
}
RECENT_LAPS = 5
TOP_CORNERS = 24  # every corner of the circuit: a question about one corner must find it
RECENT_ALERTS = 5
BRAKE_ALIVE_RANGE_C = 8.0  # the same criterion as the brake-temperature rule


@dataclass
class BrakeWatch:
    """Remembers the range of each brake temperature, to tell a live sensor from a flat one."""

    window: int = 600
    _hist: dict[str, deque] = field(default_factory=dict)

    def update(self, snap: Snapshot) -> None:
        for i, w in enumerate(CORNERS):
            self._hist.setdefault(w, deque(maxlen=self.window)).append(snap.brake_temp[i])

    def alive(self, wheel: str) -> bool:
        h = self._hist.get(wheel)
        return bool(h) and (max(h) - min(h)) >= BRAKE_ALIVE_RANGE_C


@dataclass
class ChatInputs:
    snap: Snapshot | None
    state: SessionState
    laps: Sequence[LapRecord]
    current: CurrentLap | None
    best: LapRecord | None
    cfg: AppConfig
    alerts: Sequence[AlertRecord] = ()
    track: TrackInfo | None = None
    brakes: BrakeWatch | None = None
    tyre_watch: object | None = None  # TyreLoadWatch: the index of the laps of this set of tyres
    memory: object | None = None  # AlertMemory: what was said in earlier sessions
    now: float | None = None  # same clock as ``snap.t``
    rivals: object | None = (
        None  # RivalBoard from the CSP bridge: the other cars, gaps, their times
    )


def _r(v: float, nd: int) -> float:
    return round(float(v), nd)


def _time_numbers(ms: float) -> tuple[float, ...]:
    """The renderings of a lap time a correct answer may use: seconds and milliseconds."""
    return (round(ms / 1000.0, 3), float(ms))


def corner_aliases(number: int | None, name: str | None) -> list[str]:
    out = (
        [f"curva {number}", f"corner {number}", f"turn {number}", f"kurve {number}"]
        if number is not None
        else []
    )
    if name:
        out.append(name)
        out.append(re.sub(r"^curva\s+", "", name, flags=re.IGNORECASE))
    return out


def _lap_flags(lap: LapRecord) -> list[str]:
    return [
        n for n, on in (("out", lap.out_lap), ("in", lap.in_lap), ("suspect", lap.suspect)) if on
    ]


def build_facts(inp: ChatInputs) -> FactSheet:
    sheet = FactSheet(lang=inp.cfg.engineer.language)
    snap, st = inp.snap, inp.state
    add = sheet.add

    # -- session ---------------------------------------------------------------------------------------
    add(Fact("session.kind", st.session))
    add(Fact("session.phase", st.phase))
    if st.track:
        add(Fact("session.track", st.track))
    if st.car:
        add(Fact("session.car", st.car))
    if st.session_time_left_s is not None and math.isfinite(st.session_time_left_s):
        add(Fact("session.time_left_s", _r(st.session_time_left_s, 0), "s"))
    if st.position > 0:
        add(Fact("session.position", st.position))
    add(Fact("session.laps_completed", st.completed_laps))
    if snap is not None:
        add(Fact("session.timed", bool(snap.static.is_timed_race)))
    if snap is not None and snap.tyre_compound:
        add(Fact("session.compound", snap.tyre_compound))
    if snap is not None and inp.now is not None:
        add(Fact("data_age_s", _r(max(0.0, inp.now - snap.t), 1), "s"))

    # -- laps ------------------------------------------------------------------------------------------
    timed = [lap for lap in inp.laps if lap.time_ms > 0]
    best = inp.best
    if best is not None:
        sub = Subject("lap", str(best.number))
        add(Fact("laps.best.lap", best.number, subject=sub))
        add(
            Fact(
                "laps.best.time_s",
                _r(best.time_ms / 1000, 3),
                "s",
                sub,
                _time_numbers(best.time_ms),
            )
        )
    if timed:
        last = timed[-1]
        sub = Subject("lap", str(last.number))
        add(Fact("laps.last.lap", last.number, subject=sub))
        add(
            Fact(
                "laps.last.time_s",
                _r(last.time_ms / 1000, 3),
                "s",
                sub,
                _time_numbers(last.time_ms),
            )
        )
        if best is not None and best.number != last.number:
            d = last.time_ms - best.time_ms
            add(
                Fact(
                    "laps.last.delta_to_best_s",
                    _r(d / 1000, 3),
                    "s",
                    sub,
                    (round(d / 1000, 3), round(abs(d) / 1000, 3), float(d), abs(float(d))),
                )
            )
        flags = _lap_flags(last)
        if flags:
            add(Fact("laps.last.flags", ",".join(flags)))
    for lap in timed[-RECENT_LAPS:]:
        sub = Subject("lap", str(lap.number))
        k = f"laps.recent.L{lap.number}"
        add(Fact(f"{k}.time_s", _r(lap.time_ms / 1000, 3), "s", sub, _time_numbers(lap.time_ms)))
        if best is not None and best.number != lap.number:
            d = lap.time_ms - best.time_ms
            add(
                Fact(
                    f"{k}.delta_to_best_s",
                    _r(d / 1000, 3),
                    "s",
                    sub,
                    (round(d / 1000, 3), round(abs(d) / 1000, 3)),
                )
            )
        if _lap_flags(lap):
            add(Fact(f"{k}.flags", ",".join(_lap_flags(lap))))
        sheet.alias(sub, f"vuelta {lap.number}", f"lap {lap.number}")
    for lap in ([best] if best else []) + timed[-1:]:
        sheet.alias(Subject("lap", str(lap.number)), f"vuelta {lap.number}", f"lap {lap.number}")
    cur = inp.current
    if cur is not None:
        add(Fact("laps.current.lap", cur.number))
        if cur.delta_ms is not None:
            d = cur.delta_ms / 1000
            add(
                Fact(
                    "laps.current.live_delta_s",
                    _r(d, 2),
                    "s",
                    numbers=(round(d, 2), round(abs(d), 2)),
                )
            )

    # -- fuel ------------------------------------------------------------------------------------------
    if snap is not None and snap.fuel > 0:
        add(Fact("fuel.litres", _r(snap.fuel, 1), "L"))
        per_lap = _fuel_per_lap(inp)
        if per_lap:
            add(
                Fact(
                    "fuel.per_lap_l",
                    _r(per_lap, 2),
                    "L",
                    numbers=(round(per_lap, 2), round(per_lap, 1)),
                )
            )
            left = snap.fuel / per_lap
            add(Fact("fuel.laps_left", _r(left, 1), "laps"))

    # -- tyres and brakes ---------------------------------------------------------------------------------
    if snap is not None:
        for i, w in enumerate(CORNERS):
            sub = Subject("wheel", w)
            sheet.alias(sub, *WHEEL_ALIASES[w])
            if snap.tyre_core_temp[i] > 0:
                add(Fact(f"tyres.{w}.core_c", _r(snap.tyre_core_temp[i], 0), "C", sub))
            if snap.tyre_pressure[i] > 0:
                add(Fact(f"tyres.{w}.pressure_psi", _r(snap.tyre_pressure[i], 1), "psi", sub))
            if (inp.cfg.engineer.wear_channel or snap.wear_source == "csp") and snap.tyre_wear[
                i
            ] > 0:
                add(Fact(f"tyres.{w}.wear_pct", _r(snap.tyre_wear[i], 1), "%", sub))
        _add_axles(sheet, snap)
        if snap.air_temp:
            add(Fact("weather.air_c", _r(snap.air_temp, 0), "C"))
        if snap.road_temp:
            add(Fact("weather.road_c", _r(snap.road_temp, 0), "C"))
        any_alive = False
        for i, w in enumerate(CORNERS):
            if inp.brakes is not None and inp.brakes.alive(w):
                any_alive = True
                add(Fact(f"brakes.{w}.temp_c", _r(snap.brake_temp[i], 0), "C", Subject("wheel", w)))
        if not any_alive:
            add(
                Fact("brakes.note", "no reliable temperature: the sensor of this car does not move")
            )
        if snap.penalty_time:
            add(Fact("penalties.time_s", _r(snap.penalty_time, 1), "s"))
            # seen live (2026-10-17): 10.0 at the moment of a 10 s penalty, then counting down in real time
            add(Fact("penalties.unit_verified", True))

    _add_rivals(inp, sheet)

    # -- the last lap against the best, corner by corner ------------------------------------------------
    _add_analysis(inp, sheet)
    _add_tyre_corners(inp, sheet)

    # -- what the engineer said recently ------------------------------------------------------------------
    for j, rec in enumerate(list(inp.alerts)[-RECENT_ALERTS:][::-1]):
        k = f"alerts.A{j}"
        add(Fact(f"{k}.rule", rec.rule))
        add(Fact(f"{k}.text", rec.text))
        add(Fact(f"{k}.outcome", rec.outcome))
        if inp.now is not None:
            add(Fact(f"{k}.age_s", _r(max(0.0, inp.now - rec.t), 0), "s"))
        for name, v in rec.values.items():
            if isinstance(v, int | float) and not isinstance(v, bool):
                add(
                    Fact(
                        f"{k}.values.{name}",
                        _r(v, 2),
                        numbers=(round(float(v), 2), round(float(v), 1)),
                    )
                )
        sheet.extra_numbers += [
            float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", rec.text)
        ]
    return sheet


def _add_rivals(inp: ChatInputs, sheet: FactSheet) -> None:
    """The cars next to me, from the CSP bridge: who they are, the gap in seconds and how they are doing."""
    board = inp.rivals
    if board is None or board.me is None:
        return
    add = sheet.add
    me = board.me
    add(Fact("rivals.cars", len(board.cars)))
    if me.position:
        add(Fact("rivals.me.position", me.position))
    for side, gap in (("ahead", board.ahead()), ("behind", board.behind())):
        if gap is None:
            continue
        c = gap.car
        k = f"rivals.{side}"
        if c.name:
            add(Fact(f"{k}.name", c.name))
        if c.position:
            add(Fact(f"{k}.position", c.position))
        if gap.gap_s is not None:
            add(Fact(f"{k}.gap_s", _r(abs(gap.gap_s), 1), "s"))
        if gap.laps_diff:
            add(Fact(f"{k}.laps_diff", abs(gap.laps_diff), "laps"))
        if gap.closing_s is not None and abs(gap.closing_s) >= 0.1:
            add(Fact(f"{k}.closing_s", _r(abs(gap.closing_s), 1), "s"))
            add(Fact(f"{k}.is_closing", gap.closing_s > 0))
        if c.best_ms > 0:
            add(Fact(f"{k}.best_lap_ms", c.best_ms, "ms", numbers=_time_numbers(c.best_ms)))
        if c.last_ms > 0:
            add(Fact(f"{k}.last_lap_ms", c.last_ms, "ms", numbers=_time_numbers(c.last_ms)))
        if c.compound:
            add(Fact(f"{k}.compound", c.compound))
        if c.in_pit:
            add(Fact(f"{k}.in_pit", True))
        # the last lap's sectors against mine: positive = I was slower there
        a, b = me.last_splits, c.last_splits
        if a and len(a) == len(b) and all(x > 0 for x in a) and all(x > 0 for x in b):
            for n, (mine, theirs) in enumerate(zip(a, b), 1):
                add(Fact(f"{k}.sector{n}_delta_s", _r((mine - theirs) / 1000.0, 2), "s"))
    leader = next((c for c in board.cars if c.position == 1), None)
    if leader is not None and me.position == 1:
        add(Fact("rivals.i_lead", True))
    elif leader is not None:
        g = board.gaps.get(leader.index)
        add(Fact("rivals.leader.name", leader.name or f"#{leader.index}"))
        if g is not None and g.gap_s is not None:
            add(Fact("rivals.leader.gap_s", _r(abs(g.gap_s), 1), "s"))
        if g is not None and g.laps_diff:
            add(Fact("rivals.leader.laps_diff", abs(g.laps_diff), "laps"))
        if leader.best_ms > 0:
            add(
                Fact(
                    "rivals.leader.best_lap_ms",
                    leader.best_ms,
                    "ms",
                    numbers=_time_numbers(leader.best_ms),
                )
            )
    if board.session.over:
        add(Fact("rivals.session_over", True))


def _add_axles(sheet: FactSheet, snap: Snapshot) -> None:
    """Averages and differences by axle and side: a derived number is a fact too, or the grounding check
    would (rightly) remove it from an answer, and the model would refuse a question it could answer."""
    for name, vals, unit, nd in (
        ("pressure_psi", snap.tyre_pressure, "psi", 1),
        ("core_c", snap.tyre_core_temp, "C", 0),
    ):
        if not all(v > 0 for v in vals):
            continue
        fl, fr, rl, rr = vals
        nd += 1  # an extra decimal: an average of two values needs it to stay exact
        # the differences are taken from the rounded averages so that what is said adds up (27.2 - 26.9 = 0.3)
        groups = {
            "front": round((fl + fr) / 2, nd),
            "rear": round((rl + rr) / 2, nd),
            "left": round((fl + rl) / 2, nd),
            "right": round((fr + rr) / 2, nd),
        }
        for g, v in groups.items():
            sheet.add(
                Fact(
                    f"tyres.axles.{g}_{name}", _r(v, nd), unit, numbers=(round(v, nd), round(v, 0))
                )
            )
        for a, b in (("front", "rear"), ("left", "right")):
            d = round(groups[a] - groups[b], nd)
            sheet.add(
                Fact(
                    f"tyres.axles.{a}_minus_{b}_{name}",
                    _r(d, nd),
                    unit,
                    numbers=(round(d, nd), round(abs(d), nd), round(abs(d), 0)),
                )
            )


def _fuel_per_lap(inp: ChatInputs) -> float | None:
    class _T:  # FuelRule.per_lap only needs the tracker's lap list
        laps = list(inp.laps)

    return FuelRule(inp.cfg.engineer.rules.fuel).per_lap(_T())  # type: ignore[arg-type]


def _add_tyre_corners(inp: ChatInputs, sheet: FactSheet) -> None:
    """Where the tyres are worked hardest (an estimate from the slip x load index; see tyre_corners.py)."""
    from ..analysis import tyre_corners as TC

    watch = inp.tyre_watch
    if watch is None:
        return
    laps, _dropped = TC.drop_incident_laps(list(watch.laps))
    need = inp.cfg.analysis.tyre_corners_min_laps
    sheet.add(Fact("tyre_corners.laps", len(laps)))
    sheet.add(Fact("tyre_corners.laps_needed", need))
    if len(laps) < need:
        return
    for axle in ("front", "rear"):
        for j, (corner, share, phase) in enumerate(TC.ranking(laps, axle)[:3]):
            k = f"tyre_corners.{axle}.R{j}"
            sub = Subject("corner", str(corner))
            sheet.add(Fact(f"{k}.corner", corner, "", sub))
            sheet.add(
                Fact(
                    f"{k}.share_pct",
                    _r(share * 100, 0),
                    "%",
                    sub,
                    (round(share * 100, 0), round(share * 100, 1)),
                )
            )
            sheet.add(Fact(f"{k}.phase", phase))


def _add_analysis(inp: ChatInputs, sheet: FactSheet) -> None:
    info = inp.track
    if info is None or info.geometry is None:
        if info is not None and info.reason:
            sheet.add(Fact("analysis.unavailable", info.reason))
        return
    geometry, b = info.geometry, inp.cfg.analysis.braking
    # the corners are referred to by number and name whether or not there is a lap to compare yet
    sheet.official = getattr(geometry, "official", None)
    for c in geometry.apexes:
        sheet.alias(
            Subject("corner", str(c.number)),
            *corner_aliases(official_of(geometry, c.number), info.names.get(c.number)),
        )
    candidates = [lap for lap in inp.laps if B.comparable(lap) and lap.time_ms > 0]
    for lap in reversed(candidates):
        ref = B.reference_lap(list(inp.laps), lap)
        if ref is None or not B.representative(lap, ref):
            continue
        comps = B.compare_lap(geometry, lap, ref, b)
        if not comps:
            continue
        for n in (lap.number, ref.number):
            sheet.alias(Subject("lap", str(n)), f"vuelta {n}", f"lap {n}")
        sheet.add(Fact("analysis.lap", lap.number, subject=Subject("lap", str(lap.number))))
        sheet.add(
            Fact("analysis.reference_lap", ref.number, subject=Subject("lap", str(ref.number)))
        )
        sheet.add(Fact("analysis.thresholds.min_distance_m", b.min_distance_m, "m"))
        sheet.add(Fact("analysis.thresholds.min_time_s", b.min_time_s, "s"))
        for c in geometry.apexes:
            nm = info.names.get(c.number)
            sheet.alias(
                Subject("corner", str(c.number)),
                *corner_aliases(official_of(geometry, c.number), nm),
            )
        found = B.worst_issue(comps, b)
        if found is not None:
            kind, w = found
            sub = Subject("corner", str(w.corner))
            sheet.add(Fact("analysis.worst.corner", w.corner, subject=sub))
            if info.names.get(w.corner):
                sheet.add(Fact("analysis.worst.name", info.names[w.corner], subject=sub))
            sheet.add(Fact("analysis.worst.kind", kind))
            if w.brake_earlier_m is not None:
                v = w.brake_earlier_m
                sheet.add(
                    Fact(
                        "analysis.worst.brake_earlier_m",
                        _r(v, 0),
                        "m",
                        sub,
                        (round(v, 0), round(abs(v), 0), round(v, 1), round(abs(v), 1)),
                    )
                )
            if w.time_diff_s is not None:
                sheet.add(
                    Fact(
                        "analysis.worst.time_loss_s",
                        _r(w.time_diff_s, 2),
                        "s",
                        sub,
                        (round(w.time_diff_s, 2), round(w.time_diff_s, 1)),
                    )
                )
            if w.min_speed_delta_kmh is not None:
                v = w.min_speed_delta_kmh
                sheet.add(
                    Fact(
                        "analysis.worst.min_speed_delta_kmh",
                        _r(v, 0),
                        "km/h",
                        sub,
                        (round(v, 0), round(abs(v), 0)),
                    )
                )
        ranked = sorted(
            (c for c in comps if c.time_diff_s is not None),
            key=lambda c: c.time_diff_s,
            reverse=True,
        )[:TOP_CORNERS]
        for c in ranked:
            sub = Subject("corner", str(c.corner))
            k = f"analysis.corners.C{c.corner}"
            sheet.add(
                Fact(
                    f"{k}.time_diff_s",
                    _r(c.time_diff_s, 2),
                    "s",
                    sub,
                    (
                        round(c.time_diff_s, 2),
                        round(c.time_diff_s, 1),
                        round(abs(c.time_diff_s), 2),
                    ),
                )
            )
            if c.brake_earlier_m is not None:
                v = c.brake_earlier_m
                sheet.add(
                    Fact(
                        f"{k}.brake_earlier_m",
                        _r(v, 0),
                        "m",
                        sub,
                        (round(v, 0), round(abs(v), 0), round(v, 1), round(abs(v), 1)),
                    )
                )
            if c.min_speed_delta_kmh is not None:
                v = c.min_speed_delta_kmh
                sheet.add(
                    Fact(
                        f"{k}.min_speed_delta_kmh",
                        _r(v, 0),
                        "km/h",
                        sub,
                        (round(v, 0), round(abs(v), 0)),
                    )
                )
        return
    sheet.add(Fact("analysis.unavailable", "no valid reference lap of the same compound yet"))


def compact_context(sheet: FactSheet) -> dict:
    """The nested dict that goes to the model; ``size_estimate`` says how large it is in tokens."""
    return sheet.context()


def size_estimate(ctx: dict) -> int:
    """A rough token count (four characters per token) of the compact JSON."""
    import json

    return len(json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))) // 4


__all__ = ["BrakeWatch", "ChatInputs", "build_facts", "compact_context", "size_estimate", "norm"]
