"""Events the session detector and the lap tracker emit. Consumers (alerts, the chat context, the dashboard)
react to these instead of polling state, and everything here is plain immutable data."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Event:
    t: float  # monotonic time (the snapshot's ``t``) when it happened

    @property
    def kind(self) -> str:
        return type(self).__name__


@dataclass(frozen=True, slots=True)
class SessionChanged(Event):
    reason: str = ""  # "first_sample", "kind", "track", "car", "laps_reset", "time_reset"
    session: str = "unknown"
    track: str = ""
    car: str = ""


@dataclass(frozen=True, slots=True)
class PhaseChanged(Event):
    old: str = ""
    new: str = ""


@dataclass(frozen=True, slots=True)
class PitEntered(Event):
    pass


@dataclass(frozen=True, slots=True)
class PitExited(Event):
    pass


@dataclass(frozen=True, slots=True)
class SectorCompleted(Event):
    lap_number: int = 0
    sector: int = 0  # 1-based
    time_ms: int = 0
    time_source: str = "game"  # "game" (the game's own value) or "estimated" (from the lap timer)
    best_ms: int | None = None  # best time ever set in this sector, before this one
    is_best: bool = False


@dataclass(frozen=True, slots=True)
class LapCompleted(Event):
    lap: object = (
        None  # a ``analysis.laps.LapRecord`` (typed loosely to keep this module dependency-free)
    )
    is_best: bool = False
    delta_to_best_ms: int | None = (
        None  # this lap minus the best lap before it (None if there was none)
    )
    notes: tuple[str, ...] = field(default_factory=tuple)
