"""Ring buffer and polling loop: ordering, back-pressure, reconnection, never blocking."""

from __future__ import annotations

import itertools
import threading
import time

import pytest

from pit_wall_ai.telemetry.buffer import RingBuffer
from pit_wall_ai.telemetry.poller import Subscription, TelemetryPoller
from pit_wall_ai.telemetry.source import SourceExhausted, SourceUnavailable

from .helpers import ScriptedSource, snap


def wait_for(cond, timeout=3.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_ring_buffer_keeps_only_the_window():
    buf = RingBuffer(window_s=2.0)
    for i in range(10):
        buf.append(snap(t=float(i), packet_id=i))
    assert [s.packet_id for s in buf.all()] == [7, 8, 9]  # t >= 9 - 2
    assert buf.latest().packet_id == 9
    assert [s.packet_id for s in buf.last(1.0)] == [8, 9]
    assert len(buf) == 3
    buf.clear()
    assert buf.latest() is None and buf.last(5) == []
    with pytest.raises(ValueError):
        RingBuffer(0)


def test_subscription_never_blocks_and_drops_the_oldest():
    sub = Subscription(maxsize=3)
    t0 = time.perf_counter()
    dropped = sum(sub.push(snap(t=float(i), packet_id=i)) for i in range(1000))
    assert time.perf_counter() - t0 < 0.5
    assert dropped == 997 and sub.dropped == 997 and len(sub) == 3
    assert [sub.get(0.1).packet_id for _ in range(3)] == [997, 998, 999]
    assert sub.get(0.01) is None


def test_poller_delivers_in_order_and_skips_empty_reads():
    items = [
        snap(t=0.0, packet_id=1),
        None,
        snap(t=0.1, packet_id=2),
        None,
        None,
        snap(t=0.2, packet_id=3),
    ]
    p = TelemetryPoller(ScriptedSource(items), hz=500)
    sub = p.subscribe()
    p.start()
    assert wait_for(lambda: p.stats.status == "ended")
    p.stop()
    got = []
    while (s := sub.get(0.01)) is not None:
        got.append(s.packet_id)
    assert got == [1, 2, 3]
    assert [s.packet_id for s in p.buffer.all()] == [1, 2, 3]
    assert p.stats.samples == 3 and p.stats.reads >= 6 and p.stats.dropped == 0


def test_poller_reconnects_after_the_source_goes_away():
    items = [snap(t=0.0, packet_id=1), SourceUnavailable("game closed")]
    src = ScriptedSource(items)
    opens = {"n": 0}
    real_open = src.open

    def flaky_open():
        opens["n"] += 1
        if opens["n"] == 2:
            raise SourceUnavailable("still closed")
        real_open()
        if opens["n"] == 3:
            src.items = [snap(t=1.0, packet_id=9)]

    src.open = flaky_open
    p = TelemetryPoller(src, hz=500, retry_s=0.01)
    p.start()
    assert wait_for(lambda: p.stats.status == "ended")
    p.stop()
    assert [s.packet_id for s in p.buffer.all()] == [1, 9]
    assert opens["n"] == 3


def test_poller_waits_while_unavailable_and_stops_promptly():
    class Never:
        name = "never"

        def open(self):
            raise SourceUnavailable("no game")

        def read(self):
            raise AssertionError

        def close(self):
            pass

    p = TelemetryPoller(Never(), hz=60, retry_s=5.0)  # long retry: stop must not wait for it
    p.start()
    assert wait_for(lambda: p.stats.status == "waiting")
    t0 = time.perf_counter()
    p.stop()
    assert time.perf_counter() - t0 < 1.0 and not p.running
    assert "no game" in p.stats.last_error


def test_unexpected_errors_are_counted_and_the_loop_survives():
    items = [
        RuntimeError("boom"),
        snap(t=0.0, packet_id=1),
        ValueError("bad"),
        snap(t=0.1, packet_id=2),
    ]
    p = TelemetryPoller(ScriptedSource(items), hz=500)
    p.start()
    assert wait_for(lambda: p.stats.status == "ended")
    p.stop()
    assert p.stats.errors == 2 and [s.packet_id for s in p.buffer.all()] == [1, 2]
    assert "ValueError" in p.stats.last_error


def test_a_stuck_subscriber_does_not_slow_the_loop():
    class Endless:
        name = "endless"

        def __init__(self):
            self.i = itertools.count()

        def open(self):
            pass

        def read(self):
            i = next(self.i)
            if i >= 400:
                raise SourceExhausted("done")
            return snap(t=time.perf_counter(), packet_id=i)

        def close(self):
            pass

    p = TelemetryPoller(Endless(), hz=2000)
    stuck = p.subscribe(maxsize=2)  # nobody ever reads it
    p.start()
    assert wait_for(lambda: p.stats.status == "ended", timeout=5.0)
    p.stop()
    assert p.stats.samples == 400 and stuck.dropped == 398 and p.stats.dropped == 398
    assert len(p.buffer) > 0


def test_poll_rate_is_close_to_the_requested_one():
    class Counter:
        name = "counter"

        def __init__(self):
            self.i = 0

        def open(self):
            pass

        def read(self):
            self.i += 1
            return snap(t=time.perf_counter(), packet_id=self.i)

        def close(self):
            pass

    p = TelemetryPoller(Counter(), hz=100)
    p.start()
    time.sleep(0.6)
    p.stop()
    assert 35 <= p.stats.samples <= 75  # ~60 expected; generous for CI timers
    assert 50 <= p.stats.measured_hz <= 150


def test_start_is_idempotent_and_bad_rate_rejected():
    class Waiting:
        name = "waiting"

        def open(self):
            raise SourceUnavailable("no game")

        def read(self):
            raise AssertionError

        def close(self):
            pass

    p = TelemetryPoller(Waiting(), hz=100, retry_s=0.01)
    p.start()
    first = p._thread
    p.start()
    assert p._thread is first and p.running
    p.stop()
    with pytest.raises(ValueError):
        TelemetryPoller(ScriptedSource([]), hz=0)
    assert threading.active_count() >= 1
