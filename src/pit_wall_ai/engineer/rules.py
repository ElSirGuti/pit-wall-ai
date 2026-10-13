"""Rule-based alerts: no language model, no network, microseconds per sample.

A rule looks at the current sample (plus a little memory of its own), the session state and the lap tracker,
and returns alerts. A rule NEVER decides when the voice speaks: it only says "this is true now", with the
numbers that made it true. Cooldown (per rule and per thing it is about) stops the same fact from being
repeated; the dispatcher decides the moment (see ``engine.py``).

Only fields verified against the live game are used for the enabled-by-default rules (see
``docs/AC_SHARED_MEMORY.md``): wheel speed against car speed (lock-up, wheelspin), body velocity (spin), the
wheels-off counter, fuel, tyre temperature and pressure. Flags and damage are implemented but off until a
real capture shows what the game publishes for them.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

from ..analysis import braking as B
from ..analysis.circuit_names import corner_names
from ..analysis.laps import LapTracker
from ..analysis.tracks import Resolution, resolve
from ..config import AnalysisConfig, EngineerConfig
from ..events import Event, LapCompleted, SectorCompleted, SessionChanged
from ..session.detector import SessionState
from ..telemetry.snapshot import CORNERS, Snapshot
from . import messages as M
from .alerts import Alert, Priority

log = logging.getLogger(__name__)
WHEEL_AXLE = {"FL": "front", "FR": "front", "RL": "rear", "RR": "rear"}


@dataclass
class RuleContext:
    snap: Snapshot
    state: SessionState
    tracker: LapTracker
    events: list[Event]
    lang: str


class Sustain:
    """True once a condition has held for ``hold_s`` of snapshot time."""

    def __init__(self, hold_s: float) -> None:
        self.hold_s = hold_s
        self.since: float | None = None

    def update(self, cond: bool, t: float) -> bool:
        if not cond:
            self.since = None
            return False
        if self.since is None:
            self.since = t
        return t - self.since >= self.hold_s


class Rule:
    id = "rule"
    priority = Priority.MEDIUM
    ttl_s = 8.0

    def __init__(self, cfg, cooldown_s: float | None = None) -> None:
        self.cfg = cfg
        self.cooldown_s = cooldown_s if cooldown_s is not None else getattr(cfg, "cooldown_s", 0.0)
        self._last: dict[str, float] = {}

    def evaluate(self, ctx: RuleContext) -> list[Alert]:  # pragma: no cover - interface
        raise NotImplementedError

    def _fire(
        self,
        ctx: RuleContext,
        key: str,
        text: str,
        *,
        priority: Priority | None = None,
        ttl_s: float | None = None,
        **values,
    ) -> Alert | None:
        full_key = f"{self.id}:{key}" if key else self.id
        last = self._last.get(full_key)
        if last is not None and ctx.snap.t - last < self.cooldown_s:
            return None
        self._last[full_key] = ctx.snap.t
        return Alert(
            rule=self.id,
            key=full_key,
            priority=priority or self.priority,
            text=text,
            values=values,
            t=ctx.snap.t,
            ttl_s=ttl_s if ttl_s is not None else self.ttl_s,
        )


def _wheel_kmh(s: Snapshot, i: int) -> float:
    return abs(s.wheel_angular_speed[i]) * s.static.tyre_radius[i] * 3.6


class LockUpRule(Rule):
    id = "lockup"
    priority = Priority.HIGH
    ttl_s = 6.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._sus = {w: Sustain(cfg.hold_s) for w in CORNERS}

    def evaluate(self, ctx):
        s, c = ctx.snap, self.cfg
        if min(s.static.tyre_radius) <= 0:
            return []
        braking = s.brake >= c.min_brake and s.speed_kmh >= c.min_speed_kmh
        locked = []
        ratios = {}
        for i, w in enumerate(CORNERS):
            ratio = _wheel_kmh(s, i) / s.speed_kmh if s.speed_kmh > 1 else 1.0
            ratios[w] = round(ratio, 2)
            if self._sus[w].update(braking and ratio < c.ratio, s.t):
                locked.append(w)
        if not locked:
            return []
        text = M.say(ctx.lang, "lockup", where=M.wheels_name(ctx.lang, locked))
        a = self._fire(
            ctx,
            "",
            text,
            wheels=",".join(locked),
            speed_kmh=round(s.speed_kmh),
            brake=round(s.brake, 2),
            ratios=ratios,
        )
        return [a] if a else []


class WheelspinRule(Rule):
    id = "wheelspin"
    priority = Priority.LOW
    ttl_s = 4.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._sus = {"front": Sustain(cfg.hold_s), "rear": Sustain(cfg.hold_s)}

    def evaluate(self, ctx):
        s, c = ctx.snap, self.cfg
        if min(s.static.tyre_radius) <= 0 or s.speed_kmh < c.min_speed_kmh or s.gas < c.min_gas:
            for sus in self._sus.values():
                sus.update(False, s.t)
            return []
        out = []
        for axle, idx in (("front", (0, 1)), ("rear", (2, 3))):
            ratio = (_wheel_kmh(s, idx[0]) + _wheel_kmh(s, idx[1])) / 2 / s.speed_kmh
            if self._sus[axle].update(ratio > c.ratio, s.t):
                a = self._fire(
                    ctx,
                    axle,
                    M.say(ctx.lang, "wheelspin", axle=M.axle_name(ctx.lang, axle)),
                    axle=axle,
                    ratio=round(ratio, 2),
                    gas=round(s.gas, 2),
                )
                if a:
                    out.append(a)
        return out


class SpinRule(Rule):
    id = "spin"
    priority = Priority.CRITICAL
    ttl_s = 3.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._sus = Sustain(cfg.hold_s)

    def evaluate(self, ctx):
        s, c = ctx.snap, self.cfg
        vx, _, vz = s.local_velocity
        slip = math.degrees(math.atan2(abs(vx), vz)) if (abs(vx) + abs(vz)) > 1.0 else 0.0
        if not self._sus.update(s.speed_kmh >= c.min_speed_kmh and slip >= c.slip_angle_deg, s.t):
            return []
        a = self._fire(
            ctx, "", M.say(ctx.lang, "spin"), slip_deg=round(slip), speed_kmh=round(s.speed_kmh)
        )
        return [a] if a else []


class OffTrackRule(Rule):
    id = "offtrack"
    priority = Priority.HIGH
    ttl_s = 6.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._sus = Sustain(cfg.hold_s)

    def evaluate(self, ctx):
        s, c = ctx.snap, self.cfg
        if not self._sus.update(
            s.tyres_out >= c.wheels_out and s.speed_kmh >= c.min_speed_kmh, s.t
        ):
            return []
        a = self._fire(
            ctx,
            "",
            M.say(ctx.lang, "offtrack"),
            wheels_out=s.tyres_out,
            speed_kmh=round(s.speed_kmh),
        )
        return [a] if a else []


class FuelRule(Rule):
    id = "fuel_low"
    priority = Priority.HIGH
    ttl_s = 30.0

    def per_lap(self, tracker: LapTracker) -> float | None:
        timed = [
            lap
            for lap in tracker.laps
            if not (lap.out_lap or lap.in_lap) and lap.fuel_used > 0 and lap.time_ms > 0
        ]
        timed = timed[-self.cfg.laps_for_estimate :]
        if not timed:
            return None
        return sum(lap.fuel_used for lap in timed) / len(timed)

    def evaluate(self, ctx):
        s, c = ctx.snap, self.cfg
        use = self.per_lap(ctx.tracker)
        if use is None or use <= 0 or s.fuel <= 0:
            return []
        remaining = s.fuel / use
        if remaining <= c.critical_laps:
            level, prio, key = "fuel_critical", Priority.CRITICAL, "critical"
        elif remaining <= c.warn_laps:
            level, prio, key = "fuel_warn", Priority.HIGH, "warn"
        else:
            return []
        a = self._fire(
            ctx,
            key,
            M.say(ctx.lang, level, amount=M.laps_phrase(ctx.lang, remaining)),
            priority=prio,
            fuel=round(s.fuel, 1),
            per_lap=round(use, 2),
            laps_left=round(remaining, 1),
        )
        return [a] if a else []


class TyreTempRule(Rule):
    id = "tyre_hot"
    priority = Priority.HIGH
    ttl_s = 30.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._sus = {w: Sustain(cfg.hold_s) for w in CORNERS}
        self._armed = {w: True for w in CORNERS}

    def evaluate(self, ctx):
        s, c = ctx.snap, self.cfg
        hot = []
        for i, w in enumerate(CORNERS):
            temp = s.tyre_core_temp[i]
            if temp < c.rearm_c:
                self._armed[w] = True
            if self._sus[w].update(self._armed[w] and temp >= c.hot_c, s.t):
                hot.append(w)
        if not hot:
            return []
        axle = max(
            ("front", "rear"), key=lambda a: sum(w in hot for w in CORNERS if WHEEL_AXLE[w] == a)
        )
        for w in hot:
            self._armed[w] = False
        a = self._fire(
            ctx,
            axle,
            M.say(ctx.lang, "tyre_hot", axle=M.axle_name(ctx.lang, axle).capitalize()),
            wheels=",".join(hot),
            core_c=[round(x) for x in s.tyre_core_temp],
            limit_c=c.hot_c,
        )
        return [a] if a else []


class TyrePressureRule(Rule):
    id = "tyre_pressure"
    priority = Priority.HIGH
    ttl_s = 20.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._hist = {w: deque() for w in CORNERS}

    def evaluate(self, ctx):
        s, c = ctx.snap, self.cfg
        out = []
        for i, w in enumerate(CORNERS):
            psi = s.tyre_pressure[i]
            h = self._hist[w]
            h.append((s.t, psi))
            while h and s.t - h[0][0] > c.puncture_window_s:
                h.popleft()
            peak = max(p for _, p in h)
            if s.speed_kmh >= 30 and peak - psi >= c.puncture_drop_psi and psi > 0:
                a = self._fire(
                    ctx,
                    f"puncture:{w}",
                    M.say(ctx.lang, "puncture", where=M.wheel_name(ctx.lang, w)),
                    priority=Priority.CRITICAL,
                    ttl_s=4.0,
                    wheel=w,
                    peak_psi=round(peak, 1),
                    psi=round(psi, 1),
                )
                if a:
                    out.append(a)
            elif psi >= c.high_psi:
                a = self._fire(
                    ctx,
                    f"high:{w}",
                    M.say(ctx.lang, "pressure_high", where=M.wheel_name(ctx.lang, w)),
                    wheel=w,
                    psi=round(psi, 1),
                    limit_psi=c.high_psi,
                )
                if a:
                    out.append(a)
        return out


class BrakeTempRule(Rule):
    id = "brake_hot"
    priority = Priority.HIGH
    ttl_s = 20.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._lo = {w: math.inf for w in CORNERS}
        self._hi = {w: -math.inf for w in CORNERS}
        self._driving_s = 0.0
        self._last_t: float | None = None
        self._noticed = False
        self._sus = Sustain(2.0)

    @property
    def alive(self) -> bool:
        return any(self._hi[w] - self._lo[w] >= self.cfg.alive_range_c for w in CORNERS)

    def evaluate(self, ctx):
        s, c = ctx.snap, self.cfg
        for i, w in enumerate(CORNERS):
            t = s.brake_temp[i]
            self._lo[w], self._hi[w] = min(self._lo[w], t), max(self._hi[w], t)
        if self._last_t is not None and s.speed_kmh > 50:
            self._driving_s += max(0.0, min(s.t - self._last_t, 1.0))
        self._last_t = s.t
        if not self.alive:
            if self._driving_s > 120 and not self._noticed:
                self._noticed = True
                log.info(
                    "brake temperature sensor does not move for this car: brake alerts disabled",
                    extra={
                        "car": s.static.car_model,
                        "range_c": [round(self._lo[CORNERS[0]]), round(self._hi[CORNERS[0]])],
                    },
                )
            return []
        hottest = max(s.brake_temp)
        if not self._sus.update(hottest >= c.hot_c, s.t):
            return []
        a = self._fire(ctx, "", M.say(ctx.lang, "brake_hot"), max_c=round(hottest), limit_c=c.hot_c)
        return [a] if a else []


class PenaltyRule(Rule):
    id = "penalty"
    priority = Priority.HIGH
    ttl_s = 20.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._prev = 0.0

    def evaluate(self, ctx):
        s = ctx.snap
        rose = s.penalty_time - self._prev >= 0.5
        self._prev = s.penalty_time
        if not rose:
            return []
        a = self._fire(
            ctx,
            "",
            M.say(ctx.lang, "penalty", seconds=int(round(s.penalty_time))),
            penalty_time=round(s.penalty_time, 1),
        )
        return [a] if a else []


class FlagsRule(Rule):
    id = "flags"
    priority = Priority.HIGH
    ttl_s = 6.0
    _NAMES = {
        1: ("flag_blue", Priority.HIGH),
        2: ("flag_yellow", Priority.HIGH),
        3: ("flag_black", Priority.CRITICAL),
        4: ("flag_white", Priority.MEDIUM),
    }

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._prev = 0

    def evaluate(self, ctx):
        flag, prev = ctx.snap.flags, self._prev
        self._prev = flag
        if flag == prev or flag not in self._NAMES:
            return []
        key, prio = self._NAMES[flag]
        a = self._fire(ctx, key, M.say(ctx.lang, key), priority=prio, flag=flag)
        return [a] if a else []


class DamageRule(Rule):
    id = "damage"
    priority = Priority.HIGH
    ttl_s = 20.0

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._prev: float | None = None

    def evaluate(self, ctx):
        total = sum(ctx.snap.car_damage)
        prev, self._prev = self._prev, total
        if prev is None or total - prev < self.cfg.min_increase:
            return []
        a = self._fire(
            ctx,
            "",
            M.say(ctx.lang, "damage"),
            increase=round(total - prev, 1),
            zones=[round(x, 1) for x in ctx.snap.car_damage],
        )
        return [a] if a else []


class LapSummaryRule(Rule):
    id = "lap_summary"
    priority = Priority.MEDIUM
    ttl_s = 25.0

    def evaluate(self, ctx):
        out = []
        for e in ctx.events:
            if not isinstance(e, LapCompleted):
                continue
            lap = e.lap
            if lap.out_lap or lap.in_lap or lap.time_ms <= 0:
                continue
            time = M.lap_time_words(lap.time_ms)
            if e.is_best:
                text = M.say(ctx.lang, "lap_best", time=time)
            else:
                text = M.say(
                    ctx.lang,
                    "lap",
                    n=lap.number,
                    time=time,
                    delta=self._delta(ctx.lang, e.delta_to_best_ms)
                    if self.cfg.include_delta
                    else "",
                )
                text = text.rstrip(", ")
            a = self._fire(
                ctx,
                f"lap{lap.number}",
                text,
                lap=lap.number,
                time_ms=lap.time_ms,
                delta_ms=e.delta_to_best_ms,
                best=e.is_best,
            )
            if a:
                out.append(a)
        return out

    @staticmethod
    def _delta(lang: str, delta_ms: int | None) -> str:
        if delta_ms is None:
            return ""
        tenths = round(abs(delta_ms) / 100)
        if tenths == 0:
            return M.say(lang, "delta_same")
        if delta_ms < 0:
            return M.say(lang, "delta_faster", tenths=tenths)
        if tenths >= 100:
            return M.say(lang, "delta_slower_s", seconds=round(tenths / 10))
        return M.say(lang, "delta_slower", tenths=tenths)


class BestSectorRule(Rule):
    id = "best_sector"
    priority = Priority.LOW
    ttl_s = 10.0

    def evaluate(self, ctx):
        out = []
        for e in ctx.events:
            if isinstance(e, SectorCompleted) and e.is_best and e.best_ms is not None:
                a = self._fire(
                    ctx,
                    f"s{e.sector}",
                    M.say(ctx.lang, "best_sector", n=e.sector),
                    sector=e.sector,
                    time_ms=e.time_ms,
                    previous_ms=e.best_ms,
                )
                if a:
                    out.append(a)
        return out


class BrakingHintRule(Rule):
    """After a lap: the corner where this lap braked differently from the best valid lap AND lost the most.

    One alert per lap at most, never in a race (the policy drops it), only when both thresholds of
    ``analysis.braking`` are reached. The text states the measured braking-point difference first and does
    not say why. It needs the racing line of the track in the local game install; without it the rule
    stays silent and says why once in the log.
    """

    id = "braking_hint"
    priority = Priority.MEDIUM
    ttl_s = 30.0

    def __init__(self, cfg, analysis: AnalysisConfig) -> None:
        super().__init__(cfg)
        self.analysis = analysis
        self._key: tuple | None = None
        self._res: Resolution | None = None
        self._names: dict[int, str] = {}

    def _geometry(self, snap: Snapshot):
        st = snap.static
        key = (st.track, st.track_configuration, round(st.track_length_m))
        if key != self._key:
            self._key = key
            self._res = resolve(
                st.track,
                st.track_configuration,
                length_m=st.track_length_m or None,
                configured=self.analysis.ac_dirs,
            )
            if self._res.ok:
                self._names = corner_names(self._res.geometry, st.track)
                log.info(
                    "braking analysis ready",
                    extra={
                        "track": st.track,
                        "layout": self._res.layout,
                        "corners": len(self._res.geometry.corners),
                        "source": str(self._res.candidate.path),
                    },
                )
            else:
                self._names = {}
                log.warning(
                    "braking analysis off for this track",
                    extra={
                        "track": st.track,
                        "layout": st.track_configuration,
                        "reason": self._res.reason,
                    },
                )
        return self._res.geometry if self._res and self._res.ok else None

    def evaluate(self, ctx):
        out = []
        if any(isinstance(e, SessionChanged) for e in ctx.events):
            self._key = None  # a new session may be another track
        for e in ctx.events:
            if not isinstance(e, LapCompleted) or e.lap is None:
                continue
            lap = e.lap
            if not B.comparable(lap):
                continue
            ref = B.reference_lap(ctx.tracker.laps, lap)
            if ref is None or not B.representative(lap, ref):
                continue
            geometry = self._geometry(ctx.snap)
            if geometry is None:
                continue
            b = self.analysis.braking
            worst = B.worst_corner(B.compare_lap(geometry, lap, ref, b), b)
            if worst is None:
                continue
            corner = next(c for c in geometry.corners if c.number == worst.corner)
            name = self._names.get(corner.number)
            verified = ctx.snap.static.track in self.analysis.verified_numbering
            where = M.where_phrase(
                ctx.lang,
                name=name,
                number=corner.number if verified else None,
                direction=corner.direction,
                at_m=corner.apex_m,
            )
            a = self._fire(
                ctx,
                f"lap{lap.number}",
                M.braking_hint(
                    ctx.lang,
                    where=where,
                    metres=worst.brake_earlier_m,
                    earlier=worst.brake_earlier_m > 0,
                    loss_s=worst.time_diff_s,
                ),
                lap=lap.number,
                reference=ref.number,
                corner=corner.number,
                corner_name=name,
                numbering_verified=verified,
                brake_point_m=round(worst.lap_metrics.brake_point_m, 1),
                ref_brake_point_m=round(worst.ref_metrics.brake_point_m, 1),
                brake_earlier_m=round(worst.brake_earlier_m, 1),
                time_diff_s=round(worst.time_diff_s, 3),
                min_speed_delta_kmh=None
                if worst.min_speed_delta_kmh is None
                else round(worst.min_speed_delta_kmh, 1),
                span_m=tuple(round(x) for x in worst.span_m),
            )
            if a:
                out.append(a)
        return out


def build_rules(cfg: EngineerConfig, analysis: AnalysisConfig | None = None) -> list[Rule]:
    r = cfg.rules
    analysis = analysis or AnalysisConfig()
    table = [
        (r.lockup, LockUpRule),
        (r.wheelspin, WheelspinRule),
        (r.spin, SpinRule),
        (r.offtrack, OffTrackRule),
        (r.fuel, FuelRule),
        (r.tyre_temp, TyreTempRule),
        (r.tyre_pressure, TyrePressureRule),
        (r.brake_temp, BrakeTempRule),
        (r.penalty, PenaltyRule),
        (r.flags, FlagsRule),
        (r.damage, DamageRule),
        (r.lap_summary, LapSummaryRule),
        (r.best_sector, BestSectorRule),
    ]
    built: list[Rule] = [cls(rc) for rc, cls in table if rc.enabled]
    if r.braking_hint.enabled:
        built.append(BrakingHintRule(r.braking_hint, analysis))
    return built
