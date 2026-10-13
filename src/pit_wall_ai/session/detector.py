"""Session type and driving phase from the live snapshots.

Type (``kind``): the game's ``session`` field. Only *practice* has been seen live so far; the other codes are
the ones of the shared-memory enum (qualify, race, hotlap, time attack, drift, drag) and are unverified.

Phase, the part the alerts and the engineer's tone depend on:

``menu`` / ``replay`` / ``paused``  the game is not running the car
``pits``                            in the pit lane or the pit box
``out_lap``                         left the pits and has not completed a lap yet
``flying``                          on track, racing or on a hot lap
``stopped``                         on track and (almost) not moving for a few seconds (spin, stall, grid)

There is no field for "race lights out" or "formation lap" in the shared memory, so those are not
detected: a race start shows up as ``stopped`` and then ``flying``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from ..events import Event, PhaseChanged, PitEntered, PitExited, SessionChanged
from ..telemetry import shm_structs as S
from ..telemetry.snapshot import Snapshot

STOPPED_KMH = 2.0
STOPPED_AFTER_S = 3.0
TIME_JUMP_MS = 5_000  # session_time_left rising by more than this = a new session


@dataclass(frozen=True, slots=True)
class SessionState:
    session: str = (
        "unknown"  # practice | qualify | race | hotlap | time_attack | drift | drag | unknown
    )
    status: str = "off"  # off | replay | live | pause
    phase: str = "menu"
    track: str = ""
    track_configuration: str = ""
    car: str = ""
    in_pit: bool = False
    completed_laps: int = 0
    laps_since_pit_exit: int | None = None  # None until the car has left the pits once
    session_time_left_s: float | None = None
    session_elapsed_s: float | None = None  # races without a time limit count the clock negative
    position: int = 0
    kind_verified_live: bool = (
        False  # True only for the codes seen on a real game (practice so far)
    )


# Seen on a real game: practice (0), qualify (1), race (2) and hotlap (3).
_VERIFIED_LIVE = {
    int(S.SessionKind.PRACTICE),
    int(S.SessionKind.QUALIFY),
    int(S.SessionKind.RACE),
    int(S.SessionKind.HOTLAP),
}


class SessionDetector:
    def __init__(self) -> None:
        self.state = SessionState()
        self._prev: Snapshot | None = None
        self._since_pit_exit: int | None = None
        self._pit_exit_laps = 0
        self._stopped_since: float | None = None

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    def update(self, s: Snapshot) -> list[Event]:
        events: list[Event] = []
        prev = self._prev
        st = s.static

        if prev is not None and S.status_name(s.status) == "off":
            # The menu publishes zeros and session 0: keep what the session was, only the phase changes.
            old_phase = self.state.phase
            self.state = replace(self.state, status="off", phase="menu", in_pit=False)
            if old_phase != "menu":
                events.append(PhaseChanged(s.t, old_phase, "menu"))
            return events

        reason = None
        menu = (
            S.status_name(s.status) == "off"
        )  # the menu publishes session 0 and zeros: not a session
        if prev is None:
            reason = "first_sample"
        elif menu:
            reason = None
        elif s.session != prev.session:
            reason = "kind"
        elif (
            st.track != prev.static.track
            or st.track_configuration != prev.static.track_configuration
        ):
            reason = "track"
        elif st.car_model != prev.static.car_model:
            reason = "car"
        elif s.completed_laps < prev.completed_laps:
            reason = "laps_reset"
        elif math.isinf(s.session_time_left) and not math.isinf(prev.session_time_left):
            reason = "restart"  # observed: the clock reads +inf for a sample on (re)start
        elif (
            math.isfinite(s.session_time_left)
            and math.isfinite(prev.session_time_left)
            and s.session_time_left - prev.session_time_left > TIME_JUMP_MS
            and prev.session_time_left > 0
        ):
            reason = "time_reset"
        if reason is not None:
            self._since_pit_exit = None
            self._pit_exit_laps = s.completed_laps
            self._stopped_since = None
            events.append(
                SessionChanged(s.t, reason, S.session_name(s.session), st.track, st.car_model)
            )
        in_pit = s.is_in_pit_lane or s.is_in_pit
        if prev is not None and reason is None:
            was_in_pit = prev.is_in_pit_lane or prev.is_in_pit
            if in_pit and not was_in_pit:
                events.append(PitEntered(s.t))
            elif was_in_pit and not in_pit:
                events.append(PitExited(s.t))
                self._since_pit_exit = 0
                self._pit_exit_laps = s.completed_laps
        if self._since_pit_exit is not None:
            self._since_pit_exit = s.completed_laps - self._pit_exit_laps

        phase = self._phase(s, in_pit)
        old = self.state.phase
        self.state = SessionState(
            session=S.session_name(s.session),
            status=S.status_name(s.status),
            phase=phase,
            track=st.track,
            track_configuration=st.track_configuration,
            car=st.car_model,
            in_pit=in_pit,
            completed_laps=s.completed_laps,
            laps_since_pit_exit=self._since_pit_exit,
            session_time_left_s=(
                s.session_time_left / 1000.0
                if math.isfinite(s.session_time_left) and s.session_time_left > 0
                else None
            ),
            session_elapsed_s=(
                -s.session_time_left / 1000.0
                if math.isfinite(s.session_time_left) and s.session_time_left < 0
                else None
            ),
            position=s.position,
            kind_verified_live=s.session in _VERIFIED_LIVE,
        )
        if prev is not None and phase != old:
            events.append(PhaseChanged(s.t, old, phase))
        self._prev = s
        return events

    def _phase(self, s: Snapshot, in_pit: bool) -> str:
        status = S.status_name(s.status)
        if status == "off":
            return "menu"
        if status == "replay":
            return "replay"
        if status == "pause":
            return "paused"
        if in_pit:
            self._stopped_since = None
            return "pits"
        if s.speed_kmh < STOPPED_KMH:
            self._stopped_since = s.t if self._stopped_since is None else self._stopped_since
            if s.t - self._stopped_since >= STOPPED_AFTER_S:
                return "stopped"
        else:
            self._stopped_since = None
        if self._since_pit_exit == 0:
            return "out_lap"
        return "flying"
