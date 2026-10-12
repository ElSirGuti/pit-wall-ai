"""The telemetry loop: reads a source at a fixed rate in its own thread and fans samples out.

Rules that keep it from ever stalling (network and audio live in other threads):

* The loop only calls ``source.read()`` (cheap, non-blocking) and appends to the ring buffer.
* Consumers get samples through bounded queues. When a consumer is slow its OLDEST sample is dropped
  and counted; the loop never waits for it.
* A source that fails is closed and re-opened every ``retry_s`` seconds; the loop survives it.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace

from .buffer import RingBuffer
from .snapshot import Snapshot
from .source import SourceExhausted, SourceUnavailable, TelemetrySource

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 10


@dataclass(frozen=True, slots=True)
class PollerStats:
    status: str = "starting"  # starting | waiting | running | ended | stopped
    reads: int = 0
    samples: int = 0
    errors: int = 0
    dropped: int = 0  # samples dropped for slow subscribers
    measured_hz: float = 0.0  # new samples per second (EMA)
    max_read_ms: float = 0.0
    last_error: str = ""


class Subscription:
    """A bounded queue of snapshots for one consumer."""

    def __init__(self, maxsize: int) -> None:
        self._q: queue.Queue[Snapshot] = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def push(self, snap: Snapshot) -> int:
        """Never blocks. Returns how many samples were dropped to make room (0 or 1)."""
        try:
            self._q.put_nowait(snap)
            return 0
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._q.get_nowait()
            self._q.put_nowait(snap)
            self.dropped += 1
            return 1

    def get(self, timeout: float | None = None) -> Snapshot | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def __len__(self) -> int:
        return self._q.qsize()


class TelemetryPoller:
    def __init__(
        self,
        source: TelemetrySource,
        *,
        hz: float = 60.0,
        buffer_seconds: float = 30.0,
        retry_s: float = 1.0,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if hz <= 0:
            raise ValueError("hz must be > 0")
        self.source = source
        self.period = 1.0 / hz
        self.retry_s = retry_s
        self.buffer = RingBuffer(buffer_seconds)
        self._clock = clock
        self._subs: list[Subscription] = []
        self._subs_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stats = PollerStats()
        self._stats_lock = threading.Lock()

    # -- public API --------------------------------------------------------------------------------
    def subscribe(self, maxsize: int = 256) -> Subscription:
        sub = Subscription(maxsize)
        with self._subs_lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._subs_lock:
            if sub in self._subs:
                self._subs.remove(sub)

    @property
    def stats(self) -> PollerStats:
        with self._stats_lock:
            return self._stats

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="telemetry-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._set(status="stopped")

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # -- internals ---------------------------------------------------------------------------------
    def _set(self, **changes) -> None:
        with self._stats_lock:
            self._stats = replace(self._stats, **changes)

    def _publish(self, snap: Snapshot) -> int:
        self.buffer.append(snap)
        dropped = 0
        with self._subs_lock:
            subs = list(self._subs)
        for sub in subs:
            dropped += sub.push(snap)
        return dropped

    def _connect(self) -> bool:
        """Open the source; wait ``retry_s`` between attempts. False only when asked to stop."""
        announced = False
        while not self._stop.is_set():
            try:
                self.source.open()
                log.info("telemetry source connected", extra={"source": self.source.name})
                return True
            except SourceUnavailable as exc:
                if not announced:
                    log.info(
                        "telemetry source unavailable, waiting",
                        extra={"source": self.source.name, "reason": str(exc)},
                    )
                    announced = True
                self._set(status="waiting", last_error=str(exc))
                self._stop.wait(self.retry_s)
        return False

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if not self._connect():
                    break
                if self._loop() == "ended":
                    break
        finally:
            try:
                self.source.close()
            except Exception:  # noqa: BLE001 - closing must not raise
                log.exception("closing the telemetry source failed")

    def _loop(self) -> str:
        """Poll until the source goes away ("lost"), ends ("ended") or we are stopped."""
        self._set(status="running")
        next_t = self._clock()
        errors_in_row = 0
        recent: deque[float] = deque(maxlen=120)  # publish times of the latest samples
        while not self._stop.is_set():
            t_read = self._clock()
            try:
                snap = self.source.read()
                errors_in_row = 0
            except SourceExhausted:
                self._set(status="ended")
                return "ended"
            except SourceUnavailable as exc:
                log.info("telemetry source lost", extra={"reason": str(exc)})
                self._set(status="waiting", last_error=str(exc))
                try:
                    self.source.close()
                except Exception:  # noqa: BLE001
                    log.exception("closing the telemetry source failed")
                return "lost"
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                errors_in_row += 1
                stats = self.stats
                self._set(errors=stats.errors + 1, last_error=f"{type(exc).__name__}: {exc}")
                log.exception("telemetry read failed")
                if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                    self._set(status="waiting")
                    with contextlib.suppress(Exception):
                        self.source.close()
                    return "lost"
                snap = None
            read_ms = (self._clock() - t_read) * 1000.0
            stats = self.stats
            changes = {"reads": stats.reads + 1, "max_read_ms": max(stats.max_read_ms, read_ms)}
            if snap is not None:
                dropped = self._publish(snap)
                changes["samples"] = stats.samples + 1
                changes["dropped"] = stats.dropped + dropped
                recent.append(self._clock())
                if len(recent) >= 2 and recent[-1] > recent[0]:
                    changes["measured_hz"] = (len(recent) - 1) / (recent[-1] - recent[0])
            self._set(**changes)
            next_t += self.period
            now = self._clock()
            if now - next_t > 5 * self.period:  # we fell behind: do not burst
                next_t = now
            self._stop.wait(max(0.0, next_t - now))
        return "stopped"
