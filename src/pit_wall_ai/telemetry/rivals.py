"""The other cars of the session, from the CSP bridge: who is ahead and behind, the gaps in seconds, their times.

Assetto Corsa does not give gaps. They are measured here the way a timing screen does: every car's distance along
the lap is recorded against time, and the gap from the car behind to the car ahead is how long ago the one ahead
was where the one behind is now. All of it is read from the car states CSP exposes (``ac.getCar(i)``), nothing is
sent anywhere, and nothing here writes to the game.
"""

from __future__ import annotations

import threading
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field

HISTORY_S = 240.0  # long enough for a gap of a few minutes; older samples are dropped
TREND_WINDOW_S = 30.0
MIN_SPEED_KMH = (
    30.0  # the speed assumed to turn a distance into seconds when no history covers the gap
)

SESSION_KINDS = {
    1: "practice",
    2: "qualify",
    3: "race",
    4: "hotlap",
    5: "time_attack",
    6: "drift",
    7: "drag",
}


@dataclass(frozen=True)
class RivalCar:
    index: int
    name: str = ""
    position: int = 0  # race position (classification), 1 = leader
    laps: int = 0  # completed laps
    spline: float = 0.0  # 0..1 along the lap
    speed_kmh: float = 0.0
    best_ms: int = 0
    last_ms: int = 0
    current_ms: int = 0
    sector: int = 0
    splits: tuple[int, ...] = ()  # the sectors of the lap in progress (ms, per sector)
    last_splits: tuple[int, ...] = ()
    best_splits: tuple[int, ...] = ()
    in_pit: bool = False
    retired: bool = False
    finished: bool = False
    valid: bool = True
    compound: str = ""


@dataclass(frozen=True)
class SessionInfo:
    kind: str = ""  # practice | qualify | race | hotlap ...
    laps: int = 0
    duration_min: float = 0.0
    timed: bool = False
    leader_laps: int = 0
    over: bool = False


def _ints(v) -> tuple[int, ...]:
    if not isinstance(v, list):
        return ()
    out = []
    for x in v:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            out.append(0)
    return tuple(out)


def parse_car(d: dict) -> RivalCar | None:
    try:
        return RivalCar(
            index=int(d["i"]),
            name=str(d.get("n", "") or ""),
            position=int(d.get("p", 0) or 0),
            laps=int(d.get("l", 0) or 0),
            spline=float(d.get("s", 0.0) or 0.0),
            speed_kmh=float(d.get("v", 0.0) or 0.0),
            best_ms=int(d.get("b", 0) or 0),
            last_ms=int(d.get("a", 0) or 0),
            current_ms=int(d.get("c", 0) or 0),
            sector=int(d.get("sec", 0) or 0),
            splits=_ints(d.get("sp")),
            last_splits=_ints(d.get("ls")),
            best_splits=_ints(d.get("bs")),
            in_pit=bool(d.get("pit", 0)),
            retired=bool(d.get("ret", 0)),
            finished=bool(d.get("fin", 0)),
            valid=bool(d.get("val", 1)),
            compound=str(d.get("cmp", "") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse_session(d) -> SessionInfo:
    if not isinstance(d, dict):
        return SessionInfo()
    try:
        return SessionInfo(
            kind=SESSION_KINDS.get(int(d.get("type", 0) or 0), ""),
            laps=int(d.get("laps", 0) or 0),
            duration_min=float(d.get("dur", 0.0) or 0.0),
            timed=bool(d.get("timed", 0)),
            leader_laps=int(d.get("leader_laps", 0) or 0),
            over=bool(d.get("over", 0)),
        )
    except (TypeError, ValueError):
        return SessionInfo()


class _History:
    """Unwrapped distance (laps + spline) of one car against time."""

    def __init__(self) -> None:
        self.ts: deque[float] = deque()
        self.ds: deque[float] = deque()
        self._last_s: float | None = None
        self._base = 0.0

    def add(self, t: float, laps: int, spline: float) -> float:
        if self._last_s is None:
            self._base = float(laps)
        else:
            if spline < self._last_s - 0.5:  # crossed the line
                self._base += 1.0
            elif spline > self._last_s + 0.5:  # went back over it
                self._base -= 1.0
        self._last_s = spline
        d = self._base + spline
        if self.ts and (
            t <= self.ts[-1] or d < self.ds[-1] - 0.5
        ):  # same instant, or a teleport to the pits
            if t <= self.ts[-1]:
                return d
            self.ts.clear()
            self.ds.clear()
        self.ts.append(t)
        self.ds.append(d)
        while self.ts and self.ts[0] < t - HISTORY_S:
            self.ts.popleft()
            self.ds.popleft()
        return d

    @property
    def now(self) -> float | None:
        return self.ds[-1] if self.ds else None

    @property
    def t_now(self) -> float | None:
        return self.ts[-1] if self.ts else None

    def time_at(self, dist: float) -> float | None:
        """When this car was at ``dist`` (interpolated); ``None`` if it never was inside the history."""
        ds = list(self.ds)
        if len(ds) < 2 or dist < ds[0] or dist > ds[-1]:
            return None
        i = bisect_left(ds, dist)
        if i == 0:
            return self.ts[0]
        d0, d1 = ds[i - 1], ds[i]
        t0, t1 = self.ts[i - 1], self.ts[i]
        if d1 == d0:
            return t1
        return t0 + (t1 - t0) * (dist - d0) / (d1 - d0)

    def dist_at(self, t: float) -> float | None:
        ts = list(self.ts)
        if len(ts) < 2 or t < ts[0] or t > ts[-1]:
            return None
        i = bisect_left(ts, t)
        if i == 0:
            return self.ds[0]
        t0, t1 = ts[i - 1], ts[i]
        d0, d1 = self.ds[i - 1], self.ds[i]
        if t1 == t0:
            return d1
        return d0 + (d1 - d0) * (t - t0) / (t1 - t0)


def _gap_at(a: _History, b: _History, t: float) -> float | None:
    """Seconds between ``a`` and ``b`` at time ``t``: positive when ``b`` is ahead of ``a``."""
    da, db = a.dist_at(t), b.dist_at(t)
    if da is None or db is None:
        return None
    if db >= da:
        tb = b.time_at(da)
        return None if tb is None else t - tb
    ta = a.time_at(db)
    return None if ta is None else -(t - ta)


@dataclass(frozen=True)
class RivalGap:
    car: RivalCar
    gap_s: float | None  # positive = ahead of me by this many seconds, negative = behind me
    laps_diff: int  # whole laps ahead (+) or behind (-) me; the gap in seconds is meaningless then
    closing_s: (
        float | None
    )  # seconds the gap shrank over the last half minute (positive = they are closing in)

    @property
    def ahead(self) -> bool:
        return (self.gap_s or 0.0) > 0 if self.gap_s is not None else self.laps_diff > 0


@dataclass(frozen=True)
class RivalBoard:
    t: float
    me: RivalCar | None
    cars: tuple[RivalCar, ...]
    session: SessionInfo
    gaps: dict[int, RivalGap] = field(default_factory=dict)

    def others(self) -> list[RivalCar]:
        return [c for c in self.cars if self.me is None or c.index != self.me.index]

    def by_position(self, delta: int) -> RivalGap | None:
        """The classified car ``delta`` places from mine (-1 = the one ahead of me in the standings)."""
        if self.me is None or not self.me.position:
            return None
        want = self.me.position + delta
        for c in self.others():
            if c.position == want:
                return self.gaps.get(c.index)
        return None

    def on_track(self, ahead: bool) -> RivalGap | None:
        """The nearest car physically ahead of (or behind) me within a lap, in the pits or not."""
        best: RivalGap | None = None
        for g in self.gaps.values():
            if g.car.retired or g.gap_s is None or g.laps_diff:
                continue
            if (g.gap_s > 0) != ahead:
                continue
            if best is None or abs(g.gap_s) < abs(best.gap_s or 0.0):
                best = g
        return best

    def ahead(self) -> RivalGap | None:
        """In a race the car ahead in the standings; otherwise (and without standings) the nearest on track."""
        if self.session.kind == "race":
            g = self.by_position(-1)
            if g is not None:
                return g
        return self.on_track(True)

    def behind(self) -> RivalGap | None:
        if self.session.kind == "race":
            g = self.by_position(+1)
            if g is not None:
                return g
        return self.on_track(False)


class RivalTracker:
    """Feeds on the bridge payloads; ``board()`` answers from the newest one. Thread safe."""

    def __init__(self, *, track_length_m: float | None = None) -> None:
        self._lock = threading.Lock()
        self._hist: dict[int, _History] = {}
        self._cars: dict[int, RivalCar] = {}
        self._me: int | None = None
        self._session = SessionInfo()
        self._t = 0.0
        self.track_length_m = track_length_m

    def update(self, payload: dict) -> bool:
        raw = payload.get("cars")
        if not isinstance(raw, list):
            return False
        try:
            t = float(payload.get("t"))
        except (TypeError, ValueError):
            return False
        cars = [c for c in (parse_car(x) for x in raw if isinstance(x, dict)) if c is not None]
        if not cars:
            return False
        with self._lock:
            self._t = t
            self._cars = {c.index: c for c in cars}
            try:
                self._me = int(payload.get("me")) if payload.get("me") is not None else None
            except (TypeError, ValueError):
                self._me = None
            self._session = parse_session(payload.get("session"))
            for c in cars:
                self._hist.setdefault(c.index, _History()).add(t, c.laps, c.spline)
            for gone in set(self._hist) - set(self._cars):
                del self._hist[gone]
        return True

    def board(self) -> RivalBoard | None:
        with self._lock:
            if not self._cars:
                return None
            me = self._cars.get(self._me) if self._me is not None else None
            gaps: dict[int, RivalGap] = {}
            hme = self._hist.get(self._me) if self._me is not None else None
            if me is not None and hme is not None:
                for idx, c in self._cars.items():
                    if idx == me.index:
                        continue
                    gaps[idx] = self._gap(me, hme, c, self._hist.get(idx))
            return RivalBoard(self._t, me, tuple(self._cars.values()), self._session, gaps)

    def _gap(self, me: RivalCar, hme: _History, c: RivalCar, hc: _History | None) -> RivalGap:
        if hc is None or hme.now is None or hc.now is None:
            return RivalGap(c, None, 0, None)
        diff = hc.now - hme.now
        if abs(diff) >= 1.0:  # lapped, or a lap down: seconds would mislead
            return RivalGap(c, None, int(diff) if diff > 0 else -int(-diff), None)
        t = hme.t_now or self._t
        gap = _gap_at(hme, hc, t)
        if gap is None and self.track_length_m:  # no history covers it yet: distance over my speed
            gap = diff * self.track_length_m / (max(me.speed_kmh, MIN_SPEED_KMH) / 3.6)
        closing = None
        if gap is not None:
            before = _gap_at(hme, hc, t - TREND_WINDOW_S)
            if before is not None and (before > 0) == (gap > 0):
                closing = abs(before) - abs(gap)
        return RivalGap(c, gap, 0, closing)
