"""Run a whole recording through the session engine without the poller (no clock, nothing is skipped).

The live path samples the source at 60 Hz in real time; for analysing a file that would drop samples when
it is played faster than real time, so this feeds every sample straight to :class:`SessionEngine`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..events import Event, LapCompleted
from ..session.engine import SessionEngine
from ..telemetry import shm_structs as S
from ..telemetry.recorder import is_native, load_native, snapshot_from_flat
from ..telemetry.recording import load_recording
from ..telemetry.replay import snapshot_from_recording, static_from_recording
from ..telemetry.snapshot import Snapshot, StaticInfo
from .laps import LapRecord

_SESSION = {
    "practice": S.SessionKind.PRACTICE,
    "qualify": S.SessionKind.QUALIFY,
    "race": S.SessionKind.RACE,
    "hotlap": S.SessionKind.HOTLAP,
}


@dataclass
class OfflineResult:
    static: StaticInfo
    laps: list[LapRecord]
    events: list[Event] = field(default_factory=list)
    samples: int = 0


def first_known_static(statics: list[StaticInfo]) -> StaticInfo:
    """The first static info that names a track. A recording that starts in the menu (or before the game has
    published its static page) begins with an empty one, which must not be taken for the session's."""
    return next((s for s in statics if s.track), statics[0])


def iter_snapshots(
    path: str | Path, *, session: str = "practice"
) -> tuple[StaticInfo, Iterator[Snapshot]]:
    """(static info, every snapshot of the file in order) for a ``.pwrec.gz``, MoTeC ``.ld`` or ``.csv``."""
    if is_native(path):
        rec = load_native(path)

        def gen_native() -> Iterator[Snapshot]:
            for row, st, t in zip(rec.rows, rec.statics, rec.times, strict=True):
                yield snapshot_from_flat(row, st, t=t)

        return first_known_static(rec.statics), gen_native()
    base = load_recording(path)
    static = static_from_recording(base)
    kind = int(_SESSION[session])

    def gen_log() -> Iterator[Snapshot]:
        hz = base.hz or 10.0
        for i in range(base.n):
            yield snapshot_from_recording(base, i, static, t=i / hz, session=kind)

    return static, gen_log()


def analyse_file(path: str | Path, *, session: str = "practice") -> OfflineResult:
    static, snaps = iter_snapshots(path, session=session)
    engine = SessionEngine()
    events: list[Event] = []
    n = 0
    for snap in snaps:
        events += engine.process(snap)
        n += 1
    events += engine.flush()
    laps = [e.lap for e in events if isinstance(e, LapCompleted)]
    return OfflineResult(static, laps, events, n)
