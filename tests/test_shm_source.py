"""Shared-memory source: snapshot conversion, torn reads, stale pages, and real Win32 mappings."""

from __future__ import annotations

import ctypes
import os
import sys

import pytest

from pit_wall_ai.telemetry import shm_structs as S
from pit_wall_ai.telemetry.ac_shared_memory import ACSharedMemorySource, Win32Pages
from pit_wall_ai.telemetry.snapshot import CORNERS, snapshot_from_structs, static_from_struct
from pit_wall_ai.telemetry.source import SourceUnavailable


# ── a game double: three byte buffers ──────────────────────────────────────────────────────────────
class FakeView:
    def __init__(self, struct: ctypes.Structure):
        self.struct = struct
        self.closed = False
        self.on_read = None  # hook to simulate the game writing during our read

    def read_into(self, dest):
        if self.on_read:
            self.on_read(self)
        ctypes.memmove(ctypes.addressof(dest), ctypes.addressof(self.struct), ctypes.sizeof(dest))

    def close(self):
        self.closed = True


class FakePages:
    def __init__(self, present=True):
        self.present = present
        self.views = {k: FakeView(cls()) for k, cls in S.PAGES.items()}
        self.opens = 0

    def open_page(self, tag, min_size):
        self.opens += 1
        if not self.present:
            raise SourceUnavailable(f"{tag} not found")
        key = tag.removeprefix("acpmf_")
        v = self.views[key]
        v.closed = False
        return v

    @property
    def physics(self):
        return self.views["physics"].struct

    @property
    def graphics(self):
        return self.views["graphics"].struct

    @property
    def static(self):
        return self.views["static"].struct


def make_clock():
    box = {"t": 100.0}

    def clock():
        return box["t"]

    clock.box = box
    return clock


def test_layout_sizes_are_stable():
    # A change here means the binary layout changed: it must be re-verified against the game.
    sizes = {k: ctypes.sizeof(c) for k, c in S.PAGES.items()}
    assert sizes == {"physics": 580, "graphics": 296, "static": 684}


def test_snapshot_maps_every_group_of_fields():
    ph, gf, st = S.PhysicsPage(), S.GraphicsPage(), S.StaticPage()
    ph.packet_id, ph.gas, ph.brake, ph.fuel, ph.gear, ph.rpm = 7, 0.5, 0.25, 42.0, 3, 6500
    ph.steer_angle, ph.speed_kmh = -0.3, 187.5
    for i in range(4):
        ph.tyre_pressure[i] = 26.0 + i
        ph.tyre_core_temp[i] = 80.0 + i
        ph.tyre_temp_inner[i] = 90.0 + i
        ph.tyre_temp_middle[i] = 85.0 + i
        ph.tyre_temp_outer[i] = 80.0 + i
        ph.brake_temp[i] = 300.0 + 10 * i
        ph.wheel_slip[i] = 0.1 * i
        ph.tyre_wear[i] = 99.0 - i
    ph.local_velocity[0], ph.local_velocity[2] = 1.5, 51.0
    ph.brake_bias, ph.road_temp, ph.air_temp = 0.58, 31.0, 22.0
    gf.status, gf.session, gf.completed_laps = 2, 2, 5
    gf.current_time_ms, gf.last_time_ms, gf.best_time_ms = 61234, 118500, 117900
    gf.normalized_pos, gf.is_in_pit, gf.tyre_compound = 0.4321, 1, "SM"
    st.sm_version, st.car_model, st.track, st.max_fuel = "1.7", "ks_porsche_cayman", "spa", 120.0
    snap = snapshot_from_structs(ph, gf, static_from_struct(st), t=12.5)

    assert (snap.packet_id, snap.gear, snap.rpm, snap.fuel) == (7, 3, 6500, pytest.approx(42.0))
    assert snap.tyre_pressure == (26.0, 27.0, 28.0, 29.0)
    assert snap.tyre_temp_inner[3] == 93.0 and snap.brake_temp[1] == 310.0
    assert snap.local_velocity == (1.5, 0.0, 51.0)
    assert snap.status_name == "live" and snap.session_name == "race"
    assert snap.current_time_ms == 61234 and snap.normalized_pos == pytest.approx(0.4321)
    assert snap.is_in_pit is True and snap.tyre_compound == "SM"
    assert snap.static.car_model == "ks_porsche_cayman" and snap.static.connected
    assert CORNERS == ("FL", "FR", "RL", "RR")
    with pytest.raises(AttributeError):
        snap.gas = 1.0  # immutable


def test_unavailable_when_the_game_is_not_running():
    src = ACSharedMemorySource(FakePages(present=False))
    with pytest.raises(SourceUnavailable):
        src.open()
    with pytest.raises(SourceUnavailable):
        src.read()  # never opened


def test_read_delivers_only_new_packets():
    pages = FakePages()
    clock = make_clock()
    src = ACSharedMemorySource(pages, clock=clock)
    src.open()
    pages.physics.packet_id = 1
    pages.physics.speed_kmh = 120.0
    first = src.read()
    assert first is not None and first.speed_kmh == pytest.approx(120.0) and first.source == "ac"
    assert src.read() is None  # same packet: nothing new
    pages.physics.packet_id = 2
    assert src.read().packet_id == 2
    src.close()
    src.close()  # idempotent


def test_static_page_is_cached_and_refreshed_rarely():
    pages = FakePages()
    src = ACSharedMemorySource(pages)
    src.open()
    pages.static.track = "monza"
    pages.physics.packet_id = 1
    assert src.read().static.track == "monza"
    pages.static.track = "spa"
    pages.physics.packet_id = 2
    assert src.read().static.track == "monza"  # cached
    for i in range(3, 130):
        pages.physics.packet_id = i
        last = src.read()
    assert last.static.track == "spa"  # refreshed after the interval


def test_torn_read_is_retried():
    pages = FakePages()
    src = ACSharedMemorySource(pages)
    src.open()
    pages.physics.packet_id = 10
    calls = {"n": 0}

    def writer(view):  # the game bumps the counter between our two copies
        calls["n"] += 1
        if calls["n"] == 2:
            view.struct.packet_id = 11

    pages.views["physics"].on_read = writer
    snap = src.read()
    assert snap.packet_id == 11 and calls["n"] >= 4  # first attempt discarded, second consistent


def test_game_exit_is_detected_through_stale_packets():
    pages = FakePages()
    clock = make_clock()
    src = ACSharedMemorySource(pages, clock=clock, stale_after_s=2.0)
    src.open()
    pages.physics.packet_id = 5
    assert src.read() is not None
    clock.box["t"] += 1.0
    assert src.read() is None  # paused / menu: no error yet
    pages.present = False  # the game is gone: re-opening fails
    clock.box["t"] += 5.0
    with pytest.raises(SourceUnavailable):
        src.read()


def test_stale_but_game_still_there_keeps_going():
    pages = FakePages()
    clock = make_clock()
    src = ACSharedMemorySource(pages, clock=clock, stale_after_s=2.0)
    src.open()
    pages.physics.packet_id = 5
    src.read()
    clock.box["t"] += 10.0
    assert src.read() is None and pages.opens == 6  # re-opened (3 pages twice), no exception
    pages.physics.packet_id = 6
    assert src.read().packet_id == 6


# ── real Win32 mappings (a fake game created with CreateFileMappingW) ──────────────────────────────
pytestmark_windows = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")


@pytest.fixture()
def win_game():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileMappingW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
    ]
    k.CreateFileMappingW.restype = ctypes.c_void_p
    k.MapViewOfFile.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_size_t,
    ]
    k.MapViewOfFile.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    prefix = f"pwtest{os.getpid()}_"
    held, structs = [], {}

    def start(sizes=None):
        for key, cls in S.PAGES.items():
            size = (sizes or {}).get(key, ctypes.sizeof(cls))
            h = k.CreateFileMappingW(ctypes.c_void_p(-1), None, 0x04, 0, size, prefix + key)
            assert h
            view = k.MapViewOfFile(h, 0x000F001F, 0, 0, 0)
            assert view
            held.append((h, view))
            structs[key] = cls.from_address(view)
        return structs

    def stop():
        while held:
            h, view = held.pop()
            k.UnmapViewOfFile(ctypes.c_void_p(view))
            k.CloseHandle(ctypes.c_void_p(h))
        structs.clear()

    yield prefix, start, stop, structs
    stop()


@pytestmark_windows
def test_real_win32_pages_roundtrip(win_game):
    prefix, start, stop, structs = win_game
    src = ACSharedMemorySource(Win32Pages(), tag_prefix=prefix)
    with pytest.raises(SourceUnavailable):
        src.open()  # nothing published yet: must NOT create the pages
    start()
    structs["static"].car_model = "ks_porsche_cayman_gt4_clubsport"
    structs["static"].sm_version = "1.7"
    structs["physics"].packet_id = 1
    structs["physics"].speed_kmh = 143.0
    structs["physics"].tyre_pressure[2] = 27.5
    structs["graphics"].normalized_pos = 0.25
    src.open()
    snap = src.read()
    assert snap.speed_kmh == pytest.approx(143.0) and snap.tyre_pressure[2] == pytest.approx(27.5)
    assert snap.normalized_pos == pytest.approx(0.25)
    assert snap.static.car_model == "ks_porsche_cayman_gt4_clubsport"
    structs["physics"].packet_id = 2
    structs["physics"].speed_kmh = 150.0
    assert src.read().speed_kmh == pytest.approx(150.0)
    src.close()


@pytestmark_windows
def test_real_win32_pages_game_exit_is_reported(win_game):
    prefix, start, stop, structs = win_game
    start()
    clock = make_clock()
    src = ACSharedMemorySource(Win32Pages(), tag_prefix=prefix, clock=clock, stale_after_s=1.0)
    structs["physics"].packet_id = 1
    src.open()
    assert src.read() is not None
    stop()  # the "game" exits; our handle keeps the page alive
    clock.box["t"] += 3.0
    with pytest.raises(SourceUnavailable):
        src.read()
