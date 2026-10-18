"""Wires what the chat needs around a session engine, for a live game or a replayed file.

* a :class:`SessionEngine` (state, laps, best, delta),
* an :class:`Engineer` whose speaker says nothing, only so that the recent alerts are remembered and
  "what was that?" can be answered,
* a :class:`BrakeWatch` (is the brake-temperature sensor alive),
* the shared :class:`TrackAnalysis` (the racing line of the track).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from ..analysis.offline import iter_snapshots
from ..analysis.trackctx import TrackAnalysis
from ..analysis.tyre_corners import TyreLoadWatch
from ..config import AppConfig
from ..engineer.engine import Engineer
from ..engineer.memory_store import AlertMemory
from ..engineer.memory_store import listener as memory_listener
from ..engineer.milestones import attach as attach_milestones
from ..engineer.rules import build_rules
from ..home import home_dir
from ..session.engine import SessionEngine
from .context import BrakeWatch, ChatInputs


class _SilentSpeaker:
    busy = False

    def say(self, text: str, *, interrupt: bool = False, meta: dict | None = None) -> None:
        return None


class ChatRuntime:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        live: bool,
        clock: Callable[[], float] = time.perf_counter,
        speaker=None,
    ):
        self.cfg = cfg
        self.live = live
        self._clock = clock
        self.engine = SessionEngine()
        self.brakes = BrakeWatch()
        self.tyre_watch = TyreLoadWatch()
        self.track = TrackAnalysis(cfg.analysis)
        self.engineer = Engineer(
            cfg.engineer,
            speaker or _SilentSpeaker(),
            rules=build_rules(cfg.engineer, cfg.analysis, self.track),
        )
        # only a live run is remembered: a replayed recording is not something the engineer said
        self.memory = None
        if live and cfg.engineer.memory.enabled:
            self.memory = AlertMemory(
                cfg.engineer.memory.path, max_records=cfg.engineer.memory.max_records
            )
            self.engineer.listeners.append(memory_listener(self.memory))
        self.engine.add_sample_hook(
            lambda snap, state, tracker, events: self.engineer.on_sample(
                snap, state, tracker, events
            )
        )
        self.engine.add_sample_hook(lambda snap, state, tracker, events: self.brakes.update(snap))
        # the debrief after a race / qualifying and on request; the client is set like the milestones' one
        from ..analysis.debrief_live import LiveDebrief

        self.live_debrief = LiveDebrief(
            cfg,
            speaker or _SilentSpeaker(),
            track_analysis=self.track,
            quiet=lambda: self.engineer.quiet,
            threaded=live,
            live=live,
        )
        self.engine.add_sample_hook(self.live_debrief.on_sample)
        # the practice checklist: it ticks itself when a lap is completed
        from ..analysis.checklist import Checklist

        self.checklist_path = (
            Path(cfg.engineer.checklist.path)
            if cfg.engineer.checklist.path
            else home_dir() / "checklist.json"
        )
        self.checklist = (
            Checklist.load(self.checklist_path) if cfg.engineer.checklist.enabled else None
        )
        if self.checklist is not None:
            self.engine.add_sample_hook(self._tick_checklist)
        # pole / grid / result messages; the client is set by whoever builds the chat (it may be absent)
        self.milestones = attach_milestones(
            self.engine,
            speaker or _SilentSpeaker(),
            cfg,
            quiet=lambda: self.engineer.quiet,
            threaded=live,
            on_milestone=self.live_debrief.on_milestone,
        )
        self.engine.add_sample_hook(lambda snap, state, tracker, events: self._tyre_load(snap))

    def _tyre_load(self, snap) -> None:
        info = self.track.for_static(snap.static)
        g = info.geometry if info is not None else None
        if g is not None:
            self.tyre_watch.update(snap, g.apexes, g.length_m)

    def feed_file(self, path: str | Path, *, session: str = "practice") -> int:
        """Run a whole recording through the engine; returns the number of samples."""
        _static, snaps = iter_snapshots(path, session=session)
        n = 0
        for s in snaps:
            self.engine.process(s)
            n += 1
        self.engine.flush()
        return n

    def _tick_checklist(self, snap, state, tracker, events) -> None:
        from ..events import LapCompleted

        if self.checklist is None or not any(isinstance(e, LapCompleted) for e in events):
            return
        if self.checklist.autotick(list(tracker.laps)) and self.live:
            self.checklist.save()

    def _rivals(self):
        if not self.live:
            return None
        from ..telemetry.csp_bridge import current_board

        return current_board()

    def inputs(self) -> ChatInputs:
        v = self.engine.view()
        snap = v.last_snapshot
        info = self.track.for_static(snap.static) if snap is not None else None
        # a replay is "now" at its last sample; a live game is the wall clock of the snapshots
        now = self._clock() if self.live else (snap.t if snap is not None else None)
        return ChatInputs(
            snap=snap,
            state=v.state,
            laps=v.laps,
            current=v.current,
            best=v.best,
            cfg=self.cfg,
            alerts=list(self.engineer.recent),
            track=info,
            brakes=self.brakes,
            tyre_watch=self.tyre_watch,
            memory=self.memory,
            now=now,
            debrief=self.live_debrief.request,
            checklist=self.checklist,
            rivals=self._rivals(),
        )
