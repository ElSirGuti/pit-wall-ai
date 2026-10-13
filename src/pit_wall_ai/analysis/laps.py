"""Lap and sector tracker, best lap, reference lap and the live delta.

How a lap is closed (the game's timing behaviour was only partly observed live, so several signals are used):

1. **Primary**: ``completed_laps`` increases. The lap time is the game's ``last_time_ms`` once it has
   changed (up to ``CONFIRM_WAIT_S`` is waited for it); otherwise the last value of the lap timer
   (``current_time_ms``) is used and flagged ``estimated``.
2. **Fallback**: ``normalized_pos`` wraps from the end of the lap to its start while ``completed_laps``
   stays still for ``WRAP_GRACE_S``: the lap is closed from the wrap (``estimated``) and the tracker
   stops waiting for ``completed_laps``.

A lap is only eligible for "best" when it is complete, not an out lap, not an in lap and not suspect.
There is no lap-validity field in the shared memory: ``suspect`` is a hint (all four wheels off track for
a while), not the game's verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..events import Event, LapCompleted, SectorCompleted
from ..telemetry.snapshot import Snapshot

TRACE_BINS = (
    4000  # ~1.7 m at Spa, ~0.6 m at Magione: finer than the 10 m the braking analysis reports
)
CONFIRM_WAIT_S = 1.5  # wait this long for the game's last-lap time after a crossing
WRAP_GRACE_S = 3.0  # wait this long for completed_laps after a position wrap
MIN_LAP_MS = 15_000  # shorter than this is not a lap (a glitch or a pit-lane crossing)
MIN_COVERAGE = 0.9  # share of the lap length the samples must cover to count as complete
RESTART_DROP_MS = 2000  # a lap timer going back by more than this, away from the line, is a restart
SUSPECT_OFFTRACK_S = 0.5  # all wheels off for this long marks a lap as suspect


@dataclass(frozen=True)
class LapTrace:
    """The lap resampled over ``TRACE_BINS`` equal steps of ``normalized_pos``. NaN = not covered."""

    pos: np.ndarray
    time_ms: np.ndarray
    speed: np.ndarray
    gas: np.ndarray
    brake: np.ndarray
    steer: np.ndarray

    @property
    def coverage(self) -> float:
        return float(np.isfinite(self.time_ms).mean())

    def time_at(self, pos: float) -> float:
        """Elapsed lap time (ms) when the car was at ``pos``; NaN if that part of the lap was not recorded."""
        if not 0.0 <= pos <= 1.0:
            return math.nan
        return float(np.interp(pos, self.pos, self.time_ms, left=math.nan, right=math.nan))


@dataclass
class LapRecord:
    number: int  # the game's lap counter after this lap (1 = first lap)
    time_ms: int = 0
    time_source: str = "estimated"  # "game" | "estimated"
    sectors_ms: tuple[int, ...] = ()
    out_lap: bool = False
    in_lap: bool = False
    suspect: bool = False
    fuel_start: float = 0.0
    fuel_end: float = 0.0
    max_speed_kmh: float = 0.0
    avg_speed_kmh: float = 0.0
    tyres_out_max: int = 0
    coverage: float = 0.0
    compound: str = ""  # tyre compound at the start of the lap ("" = not reported)
    trace: LapTrace | None = field(default=None, repr=False)

    @property
    def fuel_used(self) -> float:
        return max(0.0, self.fuel_start - self.fuel_end)

    @property
    def eligible_for_best(self) -> bool:
        return (
            self.time_ms >= MIN_LAP_MS
            and not (self.out_lap or self.in_lap or self.suspect)
            and self.coverage >= MIN_COVERAGE
        )


@dataclass(frozen=True)
class CurrentLap:
    """What the tracker knows about the lap in progress."""

    number: int
    elapsed_ms: int
    sector: int  # 1-based sector being driven
    out_lap: bool
    delta_ms: float | None  # vs the reference lap at this position; + = slower; None if unavailable
    reference_number: int | None


class _Draft:
    """A lap being recorded."""

    def __init__(self, number: int, snap: Snapshot, started_in_pit: bool):
        self.number = number
        self.started_in_pit = started_in_pit
        self.fuel_start = snap.fuel
        self.compound = snap.tyre_compound
        self.pos: list[float] = []
        self.elapsed: list[float] = []
        self.speed: list[float] = []
        self.gas: list[float] = []
        self.brake: list[float] = []
        self.steer: list[float] = []
        self.tyres_out_max = 0
        self.off_since: float | None = None
        self.suspect = False
        self.sectors: list[int] = []
        self.sector_start_elapsed = 0
        self.last_sector_value = snap.last_sector_time_ms
        self.ended_in_pit = False

    def add(self, s: Snapshot) -> None:
        self.pos.append(s.normalized_pos)
        self.elapsed.append(float(s.current_time_ms))
        self.speed.append(s.speed_kmh)
        self.gas.append(s.gas)
        self.brake.append(s.brake)
        self.steer.append(s.steer_angle)
        self.tyres_out_max = max(self.tyres_out_max, s.tyres_out)
        if s.tyres_out >= 4:
            self.off_since = s.t if self.off_since is None else self.off_since
            if s.t - self.off_since >= SUSPECT_OFFTRACK_S:
                self.suspect = True
        else:
            self.off_since = None


def _trace(d: _Draft) -> LapTrace | None:
    if len(d.pos) < 20:
        return None
    pos = np.maximum.accumulate(np.clip(np.asarray(d.pos, dtype=float), 0.0, 1.0))
    keep = np.concatenate(([True], np.diff(pos) > 0))  # strictly increasing for interp
    pos = pos[keep]
    if len(pos) < 10:
        return None
    grid = np.linspace(0.0, 1.0, TRACE_BINS)

    def res(values):
        v = np.asarray(values, dtype=float)[keep]
        return np.interp(grid, pos, v, left=math.nan, right=math.nan)

    return LapTrace(grid, res(d.elapsed), res(d.speed), res(d.gas), res(d.brake), res(d.steer))


class LapTracker:
    def __init__(self) -> None:
        self.reset()

    # -- state ------------------------------------------------------------------------------------
    def reset(self) -> None:
        self.laps: list[LapRecord] = []
        self.best: LapRecord | None = None
        self._reference_override: int | None = None
        self.best_sectors_ms: dict[int, int] = {}
        self._prev: Snapshot | None = None
        self._draft: _Draft | None = None
        self._pending: dict | None = None  # a closed lap waiting for the game's lap time
        self._wrap_old: dict | None = (
            None  # a lap split at a position wrap, waiting for the counter
        )
        self._wrap_mode = False  # the lap counter never moves: close laps from the wrap
        self._last_cross_t = -1e9
        self._counter_seen = False  # the game's lap counter has increased at least once
        self.current: CurrentLap | None = None

    @property
    def reference(self) -> LapRecord | None:
        if self._reference_override is not None:
            for lap in self.laps:
                if lap.number == self._reference_override and lap.trace is not None:
                    return lap
        return self.best if self.best and self.best.trace is not None else None

    def set_reference(self, lap_number: int | None) -> None:
        """Pin the reference lap (None = back to the best lap)."""
        self._reference_override = lap_number

    # -- the update -------------------------------------------------------------------------------
    def update(self, s: Snapshot) -> list[Event]:
        events: list[Event] = []
        prev = self._prev
        in_pit = s.is_in_pit_lane or s.is_in_pit
        if self._draft is None:
            self._draft = _Draft(s.completed_laps + 1, s, started_in_pit=in_pit)
        events += self._maybe_confirm(s)

        if prev is not None:
            wrapped_line = prev.normalized_pos > 0.85 and s.normalized_pos < 0.15
            # A restart puts the car somewhere else: the lap timer drops AND the position jumps. At a
            # normal line crossing the position is continuous (the timer resets a few samples after it).
            restarted = (
                s.completed_laps == prev.completed_laps
                and prev.current_time_ms - s.current_time_ms > RESTART_DROP_MS
                and abs(s.normalized_pos - prev.normalized_pos) > 0.1
                and not wrapped_line
            )
            if s.completed_laps < prev.completed_laps or restarted:
                self.reset()  # a new session or a race restart (the caller reports it too)
                self._draft = _Draft(s.completed_laps + 1, s, started_in_pit=in_pit)
            elif s.completed_laps > prev.completed_laps:
                events += self._cross_by_counter(prev, s, in_pit)
            else:
                events += self._cross_by_wrap(prev, s, in_pit)

        draft = self._draft
        if draft is not None:
            draft.add(s)
            events += self._sectors(draft, s)
        self._update_current(s)
        self._prev = s
        return events

    # -- crossings --------------------------------------------------------------------------------
    def _cross_by_counter(self, prev: Snapshot, s: Snapshot, in_pit: bool) -> list[Event]:
        self._last_cross_t = s.t
        self._counter_seen = True
        if self._wrap_old is not None:  # the wrap was seen first: close THAT lap
            old, self._wrap_old = self._wrap_old, None
            return self._close(old["draft"], old["prev"], s, old["in_pit"], renumber=False)
        events = self._close(self._draft, prev, s, in_pit, renumber=True)
        return events

    def _cross_by_wrap(self, prev: Snapshot, s: Snapshot, in_pit: bool) -> list[Event]:
        events: list[Event] = []
        wrapped = prev.normalized_pos > 0.85 and s.normalized_pos < 0.15
        if self._wrap_old is not None and s.t - self._wrap_old["t"] >= WRAP_GRACE_S:
            old, self._wrap_old = self._wrap_old, None
            if not self._counter_seen:  # the counter never came: trust the wrap from now on
                self._wrap_mode = True
                events += self._close(old["draft"], old["prev"], s, old["in_pit"], renumber=False)
            # else: the counter works and was just late or this was not a lap (pit exit): drop it
        if wrapped and s.t - self._last_cross_t > WRAP_GRACE_S and self._wrap_old is None:
            d = self._draft
            span = (max(d.pos) - min(d.pos)) if d is not None and d.pos else 0.0
            if span < MIN_COVERAGE * 0.66:
                # the line crossed on the grid or leaving the pits is not a lap: the lap in progress (an out
                # lap) starts here, without the samples taken before the line
                self._last_cross_t = s.t
                self._draft = _Draft(d.number, s, started_in_pit=d.started_in_pit)
                return events
            if self._wrap_mode:
                self._last_cross_t = s.t
                events += self._close(self._draft, prev, s, in_pit, renumber=False, bump=True)
            else:  # split now, decide when the counter answers (or not)
                old = self._draft
                self._wrap_old = {"draft": old, "prev": prev, "t": s.t, "in_pit": in_pit}
                self._draft = _Draft(old.number + 1, s, started_in_pit=in_pit)
                self._last_cross_t = s.t
        return events

    def _close(
        self,
        d: _Draft | None,
        prev: Snapshot,
        s: Snapshot,
        in_pit: bool,
        *,
        renumber: bool,
        bump: bool = False,
    ) -> list[Event]:
        events: list[Event] = []
        if d is not None and len(d.pos) >= 2:
            lap = LapRecord(
                number=d.number,
                out_lap=d.started_in_pit,
                in_lap=in_pit,
                suspect=d.suspect,
                fuel_start=d.fuel_start,
                fuel_end=prev.fuel,
                max_speed_kmh=max(d.speed) if d.speed else 0.0,
                avg_speed_kmh=float(np.mean(d.speed)) if d.speed else 0.0,
                tyres_out_max=d.tyres_out_max,
                compound=d.compound,
            )
            trace = _trace(d)
            lap.trace = trace
            lap.coverage = trace.coverage if trace else 0.0
            lap.sectors_ms = tuple(d.sectors)
            if self._pending is not None:  # the previous lap never got its confirmation
                events += self._emit(self._pending, s.t)
            self._pending = {
                "lap": lap,
                "t": s.t,
                "old_last": prev.last_time_ms,
                "estimate": int(prev.current_time_ms),
                "n_sectors": max(1, s.static.sector_count or 3),
            }
            events += self._maybe_confirm(s)
        if renumber:
            self._draft = _Draft(s.completed_laps + 1, s, started_in_pit=in_pit)
        elif bump:
            self._draft = _Draft((d.number if d else 0) + 1, s, started_in_pit=in_pit)
        return events

    def flush(self, t: float | None = None) -> list[Event]:
        """End of a recording or of the session: close a lap that is only waiting for the game's time,
        using the lap timer (``estimated``)."""
        p = self._pending
        if p is None:
            return []
        p["lap"].time_ms, p["lap"].time_source = p["estimate"], "estimated"
        return self._emit(p, t if t is not None else p["t"])

    # -- sectors ----------------------------------------------------------------------------------
    def _sectors(self, d: _Draft, s: Snapshot) -> list[Event]:
        out: list[Event] = []
        n_have = len(d.sectors)
        if n_have >= max(1, s.static.sector_count or 3) - 1:
            d.last_sector_value = s.last_sector_time_ms or d.last_sector_value
            return out
        idx_changed = (
            self._prev is not None
            and s.sector_index != self._prev.sector_index
            and s.sector_index == n_have + 1
        )
        value_changed = (
            s.last_sector_time_ms > 0
            and s.last_sector_time_ms != d.last_sector_value
            and s.current_time_ms - d.sector_start_elapsed > 3000
        )
        if idx_changed or value_changed:
            if value_changed:
                ms, src = int(s.last_sector_time_ms), "game"
            else:
                ms, src = int(s.current_time_ms - d.sector_start_elapsed), "estimated"
            if ms > 0:
                d.sectors.append(ms)
                d.sector_start_elapsed = s.current_time_ms
                n = len(d.sectors)
                best = self.best_sectors_ms.get(n)
                flying = not d.started_in_pit
                is_best = flying and (best is None or ms < best)
                if is_best:
                    self.best_sectors_ms[n] = ms
                out.append(SectorCompleted(s.t, d.number, n, ms, src, best, is_best))
        d.last_sector_value = s.last_sector_time_ms or d.last_sector_value
        return out

    # -- confirmation and emission ----------------------------------------------------------------
    def _maybe_confirm(self, s: Snapshot) -> list[Event]:
        p = self._pending
        if p is None:
            return []
        game_changed = s.last_time_ms > 0 and s.last_time_ms != p["old_last"]
        if game_changed:
            p["lap"].time_ms, p["lap"].time_source = int(s.last_time_ms), "game"
        elif s.t - p["t"] >= CONFIRM_WAIT_S:
            p["lap"].time_ms, p["lap"].time_source = p["estimate"], "estimated"
        else:
            return []
        return self._emit(p, s.t)

    def _emit(self, p: dict, t: float) -> list[Event]:
        self._pending = None
        lap: LapRecord = p["lap"]
        sectors = list(lap.sectors_ms)
        if lap.time_ms > 0 and len(sectors) == p["n_sectors"] - 1 and sum(sectors) < lap.time_ms:
            sectors.append(lap.time_ms - sum(sectors))
            lap.sectors_ms = tuple(sectors)
        before = self.best
        self.laps.append(lap)
        is_best = lap.eligible_for_best and (before is None or lap.time_ms < before.time_ms)
        if is_best:
            self.best = lap
        delta = lap.time_ms - before.time_ms if before is not None and lap.time_ms else None
        notes = tuple(
            n
            for n, on in (
                ("out_lap", lap.out_lap),
                ("in_lap", lap.in_lap),
                ("suspect", lap.suspect),
                ("estimated_time", lap.time_source == "estimated"),
            )
            if on
        )
        return [LapCompleted(t, lap, is_best, delta, notes)]

    def _update_current(self, s: Snapshot) -> None:
        d = self._draft
        if d is None:
            self.current = None
            return
        ref = self.reference
        delta = None
        if ref is not None and ref.trace is not None and not d.started_in_pit:
            at = ref.trace.time_at(s.normalized_pos)
            if math.isfinite(at):
                delta = float(s.current_time_ms) - at
        self.current = CurrentLap(
            number=d.number,
            elapsed_ms=int(s.current_time_ms),
            sector=len(d.sectors) + 1,
            out_lap=d.started_in_pit,
            delta_ms=delta,
            reference_number=ref.number if ref else None,
        )
