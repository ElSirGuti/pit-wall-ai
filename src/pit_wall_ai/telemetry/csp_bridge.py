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
        self._server: ThreadingHTTPServer | None = None
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

            def log_message(self, *args) -> None:  # silence the per-request console line
                return

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as exc:
            log.warning("CSP bridge port busy", extra={"port": self.port, "error": str(exc)})
            return False
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="csp-bridge"
        )
        self._thread.start()
        global _CURRENT
        _CURRENT = self
        return True

    def stop(self) -> None:
        global _CURRENT
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
    return replace(snap, tyre_wear=quad, wear_source="csp")  # type: ignore[arg-type]


__all__ = ["BridgeReceiver", "BridgedSource", "SourceUnavailable", "overlay", "DEFAULT_PORT"]
