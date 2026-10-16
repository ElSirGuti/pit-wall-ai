"""The other cars from the ``acpmf_crewchief`` page, for a game without the CSP bridge.

Assetto Corsa's classic shared memory has the player's car only. The "CrewChiefEx" Python app that ships in
``apps/python`` publishes a second page, ``acpmf_crewchief``, with every car (name, position, laps, lap times,
distance along the lap, pit state). If that app is enabled in the game, this reader feeds the same rival tracker
the CSP bridge does, with fewer fields (no sectors, no tyre compound, no flags). When the bridge is sending, this
stays out of the way. It only reads the page; it never writes to it.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import threading
import time
from ctypes import c_char, c_float, c_int32

from .source import SourceUnavailable

log = logging.getLogger(__name__)

PAGE = "acpmf_crewchief"
MAX_VEHICLES = 64


class _Vec3(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("x", c_float), ("y", c_float), ("z", c_float)]


class VehicleInfo(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("carId", c_int32),
        ("driverName", c_char * 64),
        ("carModel", c_char * 64),
        ("speedMS", c_float),
        ("bestLapMS", c_int32),
        ("lapCount", c_int32),
        ("currentLapInvalid", c_int32),
        ("currentLapTimeMS", c_int32),
        ("lastLapTimeMS", c_int32),
        ("worldPosition", _Vec3),
        ("isCarInPitline", c_int32),
        ("isCarInPit", c_int32),
        ("carLeaderboardPosition", c_int32),
        ("carRealTimeLeaderboardPosition", c_int32),
        ("spLineLength", c_float),  # despite the name: the normalised position along the lap
        ("isConnected", c_int32),
        ("suspensionDamage", c_float * 4),
        ("engineLifeLeft", c_float),
        ("tyreInflation", c_float * 4),
    ]


class PageCrewChief(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("numVehicles", c_int32),
        ("focusVehicle", c_int32),
        ("serverName", c_char * 512),
        ("vehicleInfo", VehicleInfo * MAX_VEHICLES),
        ("acInstallPath", c_char * 512),
        ("isInternalMemoryModuleLoaded", c_int32),
        ("pluginVersion", c_char * 32),
    ]


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


def to_payload(page: PageCrewChief, t: float) -> dict | None:
    """The page as the message the CSP bridge sends (the fields it lacks are left out)."""
    n = max(0, min(int(page.numVehicles), MAX_VEHICLES))
    cars = []
    for i in range(n):
        v = page.vehicleInfo[i]
        if not v.isConnected:
            continue
        cars.append(
            {
                "i": int(v.carId),
                "n": _text(v.driverName),
                "p": int(v.carLeaderboardPosition),
                "l": int(v.lapCount),
                "s": float(v.spLineLength),
                "v": float(v.speedMS) * 3.6,
                "b": int(v.bestLapMS),
                "a": int(v.lastLapTimeMS),
                "c": int(v.currentLapTimeMS),
                "pit": int(v.isCarInPitline),
                "val": 0 if v.currentLapInvalid else 1,
            }
        )
    if not cars:
        return None
    return {"t": t, "me": int(page.focusVehicle), "cars": cars, "src": "crewchief"}


class CrewChiefFeeder:
    """Reads the page about ten times a second and feeds the receiver while the bridge is silent."""

    def __init__(self, receiver, *, backend=None, hz: float = 10.0, clock=time.monotonic) -> None:
        self.receiver, self.hz, self._clock = receiver, hz, clock
        self._backend = backend
        self._view = None
        self._page = PageCrewChief()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.fed = 0

    def _open(self) -> bool:
        if self._view is not None:
            return True
        if self._backend is None:
            from .ac_shared_memory import Win32Pages

            try:
                self._backend = Win32Pages()
            except SourceUnavailable:
                return False
        try:
            self._view = self._backend.open_page(PAGE, ctypes.sizeof(PageCrewChief))
        except SourceUnavailable:
            return False
        log.info("crewchief page opened: the rivals come from the CrewChiefEx app")
        return True

    def tick(self) -> bool:
        """One read. Does nothing while the CSP bridge is sending; True when something was fed."""
        latest = self.receiver.latest()
        if latest is not None and latest.get("src") != "crewchief":
            return False
        if not self._open():
            return False
        try:
            self._view.read_into(self._page)
        except SourceUnavailable:
            self._view = None
            return False
        payload = to_payload(self._page, self._clock())
        if payload is None:
            return False
        self.receiver.feed(payload)
        self.fed += 1
        return True

    def start(self) -> None:
        if self._thread is not None:
            return

        def run() -> None:
            while not self._stop.wait(1.0 / self.hz):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - a reader must never take the run down
                    log.exception("crewchief reader failed")

        self._thread = threading.Thread(target=run, daemon=True, name="crewchief")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._view is not None:
            with contextlib.suppress(Exception):
                self._view.close()
            self._view = None
