import json
import urllib.request
from dataclasses import replace

from pit_wall_ai.telemetry.csp_bridge import BridgedSource, BridgeReceiver, overlay
from pit_wall_ai.telemetry.snapshot import Snapshot


class _Src:
    name = "fake"

    def __init__(self, snap):
        self.snap, self.opened, self.closed = snap, False, False

    def open(self):
        self.opened = True

    def read(self):
        return self.snap

    def close(self):
        self.closed = True


def _snap(**kw):
    return replace(Snapshot(t=0.0, source="test", packet_id=1), **kw)


def test_overlay_replaces_only_the_wear():
    s = _snap(tyre_wear=(100, 100, 100, 100), fuel=12.0)
    o = overlay(s, {"wear": [94.0, 97.0, 0.0, 18.0]})
    assert o.tyre_wear == (94.0, 97.0, 0.0, 18.0) and o.fuel == 12.0 and o.wear_source == "csp"


def test_overlay_ignores_missing_or_malformed_data():
    s = _snap(tyre_wear=(100, 100, 100, 100))
    assert overlay(s, None) is s
    assert overlay(s, {"wear": [1, 2]}) is s
    assert overlay(s, {"wear": ["a", 1, 2, 3]}) is s


def test_stale_data_is_not_used():
    t = [0.0]
    rx = BridgeReceiver(clock=lambda: t[0], max_age_s=3.0)
    rx.feed({"wear": [90, 90, 90, 90]})
    assert rx.latest() is not None
    t[0] = 10.0
    assert rx.latest() is None  # the app stopped sending


def test_http_roundtrip_and_wrapped_source():
    rx = BridgeReceiver(port=28999)
    src = BridgedSource(_Src(_snap(tyre_wear=(100, 100, 100, 100))), rx)
    src.open()
    try:
        body = json.dumps({"wear": [80.0, 81.0, 70.0, 71.0], "flag": 0}).encode()
        req = urllib.request.Request("http://127.0.0.1:28999/", data=body, method="POST")
        assert urllib.request.urlopen(req, timeout=3).status == 204
        got = src.read()
        assert got.tyre_wear == (80.0, 81.0, 70.0, 71.0) and got.wear_source == "csp"
    finally:
        src.close()


def test_cars_posted_by_the_app_become_a_board():
    from pit_wall_ai.telemetry.csp_bridge import current_board

    rx = BridgeReceiver(port=28998)
    assert rx.start()
    try:
        for k in range(6):
            t = k * 0.5
            cars = [
                {"i": 0, "n": "Yo", "p": 2, "l": 0, "s": 0.50 + t / 100, "v": 180},
                {"i": 1, "n": "Delante", "p": 1, "l": 0, "s": 0.53 + t / 100, "v": 180},
            ]
            body = json.dumps(
                {"wear": [90, 90, 90, 90], "t": t, "me": 0, "cars": cars, "session": {"type": 3}}
            )
            req = urllib.request.Request(
                "http://127.0.0.1:28998/", data=body.encode(), method="POST"
            )
            assert urllib.request.urlopen(req, timeout=3).status == 204
        b = current_board()
        assert b is not None and b.ahead().car.name == "Delante"
        assert round(b.ahead().gap_s, 1) == 3.0
    finally:
        rx.stop()
    assert current_board() is None
