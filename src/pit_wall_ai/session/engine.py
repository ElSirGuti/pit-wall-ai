"""Feeds snapshots to the session detector and the lap tracker in a thread of their own and offers the
result (state, laps, delta, events) to the rest of the app. It reads from a poller subscription: a slow
step here can only make this thread drop old samples, never delay the telemetry loop."""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ..analysis.laps import CurrentLap, LapRecord, LapTracker
from ..events import Event, LapCompleted, SessionChanged
from ..telemetry.poller import Subscription, TelemetryPoller
from ..telemetry.snapshot import Snapshot
from .detector import SessionDetector, SessionState

log = logging.getLogger(__name__)
EventListener = Callable[[Event], None]


@dataclass(frozen=True)
class EngineView:
    """A consistent copy of everything the dashboard or the engineer needs."""

    state: SessionState
    current: CurrentLap | None
    laps: tuple[LapRecord, ...]
    best: LapRecord | None
    reference: LapRecord | None
    best_sectors_ms: dict[int, int]
    last_snapshot: Snapshot | None
    recent_events: tuple[Event, ...]


class SessionEngine:
    def __init__(self, *, recent_events: int = 50) -> None:
        self.detector = SessionDetector()
        self.tracker = LapTracker()
        self._lock = threading.Lock()
        self._listeners: list[EventListener] = []
        self._recent: deque[Event] = deque(maxlen=recent_events)
        self._last: Snapshot | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sub: Subscription | None = None
        self._poller: TelemetryPoller | None = None
        self.errors = 0

    # -- feeding ----------------------------------------------------------------------------------
    def on_event(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def process(self, snap: Snapshot) -> list[Event]:
        """Synchronous entry point (also what the thread calls); safe to use directly in tests."""
        with self._lock:
            events = self.detector.update(snap)
            if any(isinstance(e, SessionChanged) and e.reason != "first_sample" for e in events):
                self.tracker.reset()
            events += self.tracker.update(snap)
            self._last = snap
            self._recent.extend(events)
        for e in events:
            for fn in list(self._listeners):
                try:
                    fn(e)
                except Exception:  # noqa: BLE001 - one listener must not stop the others
                    log.exception("event listener failed", extra={"event": e.kind})
        return events

    def flush(self) -> list[Event]:
        """Close what is still waiting for a confirmation (call at the end of a recording)."""
        with self._lock:
            events = self.tracker.flush(self._last.t if self._last else None)
            self._recent.extend(events)
        for e in events:
            for fn in list(self._listeners):
                try:
                    fn(e)
                except Exception:  # noqa: BLE001
                    log.exception("event listener failed", extra={"event": e.kind})
        return events

    def view(self) -> EngineView:
        with self._lock:
            return EngineView(
                state=self.detector.state,
                current=self.tracker.current,
                laps=tuple(self.tracker.laps),
                best=self.tracker.best,
                reference=self.tracker.reference,
                best_sectors_ms=dict(self.tracker.best_sectors_ms),
                last_snapshot=self._last,
                recent_events=tuple(self._recent),
            )

    # -- thread -----------------------------------------------------------------------------------
    def attach(self, poller: TelemetryPoller, *, queue_size: int = 512) -> None:
        self._poller = poller
        self._sub = poller.subscribe(maxsize=queue_size)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="session-engine", daemon=True)
        self._thread.start()

    def detach(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        if self._poller is not None and self._sub is not None:
            self._poller.unsubscribe(self._sub)

    def _run(self) -> None:
        assert self._sub is not None
        while not self._stop.is_set():
            try:
                snap = self._sub.get(timeout=0.1)
            except queue.Empty:  # pragma: no cover - get() already returns None on timeout
                continue
            if snap is None:
                continue
            try:
                self.process(snap)
            except Exception:  # noqa: BLE001 - keep consuming
                self.errors += 1
                log.exception("session engine failed on a sample")

    # -- convenience --------------------------------------------------------------------------------
    @property
    def laps_completed(self) -> list[LapRecord]:
        return [e.lap for e in self._recent if isinstance(e, LapCompleted)]
