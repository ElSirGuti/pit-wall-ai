"""Live source: reads Assetto Corsa's shared-memory pages (Windows).

Design notes (these are the reasons for the structure below):

* Pages are opened with ``OpenFileMappingW`` (read-only) instead of Python's ``mmap``. ``mmap`` with a tag
  name CREATES a zero-filled mapping when the name does not exist, which both hides "the game is not
  running" and could pre-empt the game's own mapping. ``OpenFileMappingW`` simply fails when AC is off.
* Each read copies the page into a ctypes structure and re-checks the packet counter, so a sample is
  never half old / half new (the game writes the page while we read it).
* A mapping stays alive while we hold a handle even after the game exits, so "no new packets for a
  while" triggers a close and re-open: if the name is gone the source reports :class:`SourceUnavailable`.
* The Win32 access is behind :class:`PageBackend`, so everything else is testable without the game.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections.abc import Callable
from typing import Protocol

from . import shm_structs as S
from .snapshot import Snapshot, StaticInfo, snapshot_from_structs, static_from_struct
from .source import SourceUnavailable

log = logging.getLogger(__name__)

DEFAULT_TAG_PREFIX = "acpmf_"
STALE_AFTER_S = 2.0  # no new physics packet for this long -> re-open to see if the game is gone
STATIC_REFRESH_EVERY = 120  # reads between re-reads of the (rarely changing) static page


class PageView(Protocol):
    """A read-only window onto one named page."""

    def read_into(self, dest: ctypes.Structure) -> None: ...
    def close(self) -> None: ...


class PageBackend(Protocol):
    def open_page(self, tag: str, min_size: int) -> PageView:
        """Open the named page. Raises :class:`SourceUnavailable` if it does not exist or is too small."""


# ── Win32 backend ──────────────────────────────────────────────────────────────────────────────────
_FILE_MAP_READ = 0x0004


class _MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_uint32),
        ("PartitionId", ctypes.c_uint16),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_uint32),
        ("Protect", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
    ]


class _Win32View:
    def __init__(self, k32, handle, view, region_size: int):
        self._k32, self._handle, self._view, self.region_size = k32, handle, view, region_size

    def read_into(self, dest: ctypes.Structure) -> None:
        if self._view is None:
            raise SourceUnavailable("page closed")
        ctypes.memmove(ctypes.addressof(dest), self._view, ctypes.sizeof(dest))

    def close(self) -> None:
        if self._view is not None:
            self._k32.UnmapViewOfFile(ctypes.c_void_p(self._view))
            self._view = None
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None


class Win32Pages:
    """Opens existing named file mappings read-only. Never creates one."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise SourceUnavailable("Assetto Corsa shared memory is only available on Windows")
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.OpenFileMappingW.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_size_t,
        ]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.UnmapViewOfFile.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int
        k.VirtualQuery.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_MemoryBasicInformation),
            ctypes.c_size_t,
        ]
        k.VirtualQuery.restype = ctypes.c_size_t
        self._k = k

    def open_page(self, tag: str, min_size: int) -> _Win32View:
        k = self._k
        handle = k.OpenFileMappingW(_FILE_MAP_READ, 0, tag)
        if not handle:
            raise SourceUnavailable(f"page {tag!r} not found (is Assetto Corsa running?)")
        view = k.MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, 0)
        if not view:
            k.CloseHandle(handle)
            raise SourceUnavailable(f"could not map page {tag!r}")
        mbi = _MemoryBasicInformation()
        if not k.VirtualQuery(ctypes.c_void_p(view), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            region = 0
        else:
            region = int(mbi.RegionSize)
        if region < min_size:
            # The structure is bigger than what the game mapped: reading would run past the page.
            k.UnmapViewOfFile(ctypes.c_void_p(view))
            k.CloseHandle(handle)
            raise SourceUnavailable(
                f"page {tag!r} is {region} bytes but the layout needs {min_size}: "
                "the game version does not match this layout"
            )
        return _Win32View(k, handle, view, region)


# ── The source ──────────────────────────────────────────────────────────────────────────────────────
class ACSharedMemorySource:
    name = "ac"

    def __init__(
        self,
        backend: PageBackend | None = None,
        *,
        tag_prefix: str = DEFAULT_TAG_PREFIX,
        clock: Callable[[], float] = time.perf_counter,
        stale_after_s: float = STALE_AFTER_S,
    ) -> None:
        self._backend = backend
        self._prefix = tag_prefix
        self._clock = clock
        self._stale_after = stale_after_s
        self._views: dict[str, PageView] = {}
        self._phys = S.PhysicsPage()
        self._gfx = S.GraphicsPage()
        self._stat = S.StaticPage()
        self._static: StaticInfo | None = None
        self._reads_since_static = 0
        self._last_packet = -1
        self._last_new_t = 0.0
        self._opened_before = False

    # -- lifecycle ---------------------------------------------------------------------------------
    def open(self) -> None:
        if self._views:
            return
        backend = self._backend or Win32Pages()
        self._backend = backend
        opened: dict[str, PageView] = {}
        try:
            for key, cls in S.PAGES.items():
                opened[key] = backend.open_page(self._prefix + key, ctypes.sizeof(cls))
        except SourceUnavailable:
            for v in opened.values():
                v.close()
            raise
        self._views = opened
        self._static = None
        self._last_packet = -1
        self._last_new_t = self._clock()
        # a paused or menu game stops the packets and the source is reopened every few seconds: say it once
        level = logging.DEBUG if self._opened_before else logging.INFO
        self._opened_before = True
        log.log(level, "shared memory opened", extra={"pages": list(opened)})

    def close(self) -> None:
        for v in self._views.values():
            v.close()
        self._views = {}

    # -- reading -----------------------------------------------------------------------------------
    def _read_physics(self) -> S.PhysicsPage:
        view = self._views["physics"]
        for _ in range(3):  # a torn copy shows a different counter at the end
            view.read_into(self._phys)
            first = self._phys.packet_id
            probe = S.PhysicsPage()
            view.read_into(probe)
            if probe.packet_id == first:
                return self._phys
        return self._phys

    def _refresh_static(self) -> StaticInfo:
        self._views["static"].read_into(self._stat)
        self._static = static_from_struct(self._stat)
        self._reads_since_static = 0
        return self._static

    def read(self) -> Snapshot | None:
        if not self._views:
            raise SourceUnavailable("source not open")
        now = self._clock()
        phys = self._read_physics()
        if phys.packet_id == self._last_packet:
            if now - self._last_new_t > self._stale_after:
                self._reconnect()
            return None
        self._last_packet = phys.packet_id
        self._last_new_t = now
        self._views["graphics"].read_into(self._gfx)
        self._reads_since_static += 1
        if self._static is None or self._reads_since_static >= STATIC_REFRESH_EVERY:
            self._refresh_static()
        return snapshot_from_structs(phys, self._gfx, self._static, t=now, source=self.name)

    def _reconnect(self) -> None:
        """No new packets for a while: the game may have exited while our handle kept the page alive."""
        self.close()
        try:
            self.open()
        except SourceUnavailable:
            raise SourceUnavailable("Assetto Corsa stopped publishing data") from None
        self._last_new_t = self._clock()
