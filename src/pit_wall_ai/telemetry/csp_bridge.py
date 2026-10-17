"""The data Assetto Corsa's classic shared memory does not carry, sent by a small CSP Lua app.

With Custom Shaders Patch the game simulates tyre wear in its Lua-visible car state, and the classic shared
memory keeps a stale 100 %: the engineer would say "intact" about a rear tyre at 18 %. ``tools/csp_bridge`` is a
two-file Lua app that posts the real values to ``127.0.0.1`` a couple of times per second; this module receives
them and overlays them on the snapshots of the shared-memory source. Nothing here reaches the network.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .rivals import RivalBoard, RivalTracker
from .snapshot import Snapshot
from .source import SourceUnavailable, TelemetrySource

log = logging.getLogger(__name__)

DEFAULT_PORT = 28271
MAX_BODY = 262144  # 40 cars with their sectors are about 12 KB

_CURRENT: BridgeReceiver | None = None


def current_board() -> RivalBoard | None:
    """The rivals the running bridge last saw, or ``None`` (no bridge, app off, nothing fresh)."""
    rx = _CURRENT
    return rx.board() if rx is not None else None


class _Server(ThreadingHTTPServer):
    # on Windows SO_REUSEADDR lets a second process bind the same port: that would split the messages between
    # two receivers. Without it the second bind fails and the second pit-wall follows the first.
    allow_reuse_address = False


class BridgeReceiver:
    """A tiny HTTP endpoint that keeps the newest payload the Lua app posted."""

    def __init__(
        self, port: int = DEFAULT_PORT, *, max_age_s: float = 3.0, clock=time.monotonic
    ) -> None:
        self.port, self.max_age_s, self._clock = port, max_age_s, clock
        self._lock = threading.Lock()
        self._data: dict | None = None
        self._at = 0.0
        self.received = 0
        self.rivals = RivalTracker()
        self._server: _Server | None = None
        self._following = False
        self._thread: threading.Thread | None = None

    # -- data --------------------------------------------------------------------------------------------------
    def feed(self, payload: dict) -> None:
        with self._lock:
            self._data, self._at = payload, self._clock()
            self.received += 1
        self.rivals.update(payload)

    def board(self) -> RivalBoard | None:
        """The rivals, only while the app is sending (a stale board would say who was ahead minutes ago)."""
        return self.rivals.board() if self.latest() is not None else None

    def latest(self) -> dict | None:
        """The newest payload, or ``None`` when the app is not sending (not installed, game closed)."""
        with self._lock:
            if self._data is None or self._clock() - self._at > self.max_age_s:
                return None
            return self._data

    # -- server ------------------------------------------------------------------------------------------------
    def start(self) -> bool:
        if (
            self._server is not None or self._following
        ):  # the poller reopens the source every second
            return True
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                try:
                    n = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                    receiver.feed(json.loads(self.rfile.read(n) or b"{}"))
                    self.send_response(204)
                except (ValueError, TypeError):
                    self.send_response(400)
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802 - a second pit-wall process follows the first one
                data = receiver.latest()
                if data is None:
                    self.send_response(204)
                    self.end_headers()
                    return
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # silence the per-request console line
                return

        try:
            self._server = _Server(("127.0.0.1", self.port), Handler)
        except OSError:
            # another pit-wall (a `record` next to a `voice`) already listens: read from it instead
            log.info(
                "CSP bridge port in use by another pit-wall: following it",
                extra={"port": self.port},
            )
            self._follow_start()
            return True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="csp-bridge"
        )
        self._thread.start()
        global _CURRENT
        _CURRENT = self
        return True

    def _follow_start(self) -> None:
        self._following = True
        global _CURRENT
        _CURRENT = self

        def run() -> None:
            last_t = None
            while self._following:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=1.0) as r:
                        if r.status == 200:
                            data = json.loads(r.read())
                            if data.get("t") != last_t:  # only a newer message counts as fresh
                                last_t = data.get("t")
                                self.feed(data)
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)

        self._thread = threading.Thread(target=run, daemon=True, name="csp-bridge-follow")
        self._thread.start()

    def stop(self) -> None:
        global _CURRENT
        self._following = False
        if _CURRENT is self:
            _CURRENT = None
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class BridgedSource:
    """Wraps a telemetry source: the wear (and nothing else) comes from the bridge while it is sending."""

    def __init__(
        self, inner: TelemetrySource, receiver: BridgeReceiver, *, crewchief: bool = False
    ) -> None:
        self.inner, self.receiver = inner, receiver
        self._crewchief = None
        if crewchief:
            from .crewchief import CrewChiefFeeder

            self._crewchief = CrewChiefFeeder(receiver)
        self.name = inner.name

    def open(self) -> None:
        self.receiver.start()
        if self._crewchief is not None:
            self._crewchief.start()
        self.inner.open()

    def read(self) -> Snapshot | None:
        snap = self.inner.read()
        if snap is None:
            return None
        length = getattr(snap.static, "track_length_m", 0.0)
        if length:  # lets a gap be estimated from distance and speed before the history covers it
            self.receiver.rivals.track_length_m = float(length)
        return overlay(snap, self.receiver.latest())

    def close(self) -> None:
        try:
            self.inner.close()
        finally:
            if self._crewchief is not None:
                self._crewchief.stop()
            self.receiver.stop()

    def __getattr__(self, item):  # whatever else the wrapped source offers
        return getattr(self.inner, item)


def overlay(snap: Snapshot, data: dict | None) -> Snapshot:
    """The snapshot with the bridge's tyre wear (remaining life in %, as the game's tyre apps show it)."""
    if not data:
        return snap
    wear = data.get("wear")
    if not isinstance(wear, list) or len(wear) != 4:
        return snap
    try:
        quad = tuple(float(x) for x in wear)
    except (TypeError, ValueError):
        return snap
    changes: dict = {"tyre_wear": quad, "wear_source": "csp"}
    brake = data.get("brake")
    if isinstance(brake, list) and len(brake) == 4:
        try:
            temps = tuple(float(x) for x in brake)
        except (TypeError, ValueError):
            temps = ()
        if temps and any(
            t > 0 for t in temps
        ):  # CSP knows the temperature of the discs of this car
            changes["brake_temp"] = temps
    return replace(snap, **changes)  # type: ignore[arg-type]


__all__ = ["BridgeReceiver", "BridgedSource", "SourceUnavailable", "overlay", "DEFAULT_PORT"]
