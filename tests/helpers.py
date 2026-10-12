"""Test helpers: write tiny MoTeC .ld / CSV recordings and a scripted telemetry source."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from pit_wall_ai.telemetry.snapshot import Snapshot
from pit_wall_ai.telemetry.source import SourceExhausted


def write_ld(
    path: Path,
    channels: dict[str, tuple[np.ndarray, float, str]],
    *,
    vehicle="car_x",
    venue="track_y",
    driver="Test Driver",
) -> Path:
    """channels: name -> (values, freq_hz, unit). Floats are stored as float32."""
    header = bytearray(0x200)
    struct.pack_into("<I", header, 0, 0x40)
    for off, text, size in (
        (0x5E, "01/01/2026", 16),
        (0x7E, "12:00", 16),
        (0x9E, driver, 64),
        (0xDE, vehicle, 64),
        (0x15E, venue, 64),
    ):
        header[off : off + size] = text.encode("latin-1").ljust(size, b"\0")[:size]
    meta_ptr = 0x200
    n_ch = len(channels)
    data_ptr = meta_ptr + 124 * n_ch
    struct.pack_into("<II", header, 8, meta_ptr, data_ptr)
    metas, blobs, cursor = bytearray(), bytearray(), data_ptr
    for k, (name, (values, freq, unit)) in enumerate(channels.items()):
        blob = np.asarray(values, dtype="<f4").tobytes()
        nxt = meta_ptr + 124 * (k + 1) if k + 1 < n_ch else 0
        meta = bytearray(124)
        struct.pack_into(
            "<IIIIHHHHhhhh", meta, 0, 0, nxt, cursor, len(values), 0, 7, 4, int(freq), 0, 1, 1, 0
        )
        meta[32:64] = name.encode().ljust(32, b"\0")
        meta[72:84] = unit.encode().ljust(12, b"\0")
        metas += meta
        blobs += blob
        cursor += len(blob)
    path.write_bytes(bytes(header) + bytes(metas) + bytes(blobs))
    return path


def lap_channels(n_laps=3, hz=20.0, lap_s=60.0) -> dict[str, tuple[np.ndarray, float, str]]:
    """A synthetic session: speed, pedals, tyre temps, fuel and lap counters over ``n_laps`` laps."""
    n = int(n_laps * lap_s * hz)
    t = np.arange(n) / hz
    pos = (t % lap_s) / lap_s
    speed = 120 + 60 * np.sin(2 * np.pi * pos * 3)
    brake = np.clip(-np.sin(2 * np.pi * pos * 3), 0, 1) * 80
    throttle = np.clip(np.sin(2 * np.pi * pos * 3), 0, 1) * 100
    ch = {
        "Ground Speed": (speed, hz, "km/h"),
        "Brake Pos": (brake, hz, "%"),
        "Throttle Pos": (throttle, hz, "%"),
        "Steering Angle": (30 * np.sin(2 * np.pi * pos * 3), hz, "deg"),
        "Gear": (np.full(n, 3.0), hz, ""),
        "Engine RPM": (5000 + 1500 * np.sin(2 * np.pi * pos * 3), hz, "rpm"),
        "Fuel Level": (60 - t * 0.02, hz, "l"),
        "Car Pos Norm": (pos, hz, ""),
        "Lap Time": (t % lap_s, hz, "s"),
        "Session Lap Count": (np.floor(t / lap_s), hz, ""),
        "In Pit": (np.zeros(n), hz, ""),
        "Max Fuel": (np.full(n, 100.0), hz, "l"),
    }
    for w, base in zip(("FL", "FR", "RL", "RR"), (80, 82, 78, 79), strict=True):
        ch[f"Tire Temp Core {w}"] = (base + 10 * pos, hz, "C")
        ch[f"Tire Temp Inner {w}"] = (base + 6 + 10 * pos, hz, "C")
        ch[f"Tire Temp Middle {w}"] = (base + 3 + 10 * pos, hz, "C")
        ch[f"Tire Temp Outer {w}"] = (base + 10 * pos, hz, "C")
        ch[f"Tire Pressure {w}"] = (np.full(n, 26.5), hz, "psi")
        ch[f"Brake Temp {w}"] = (300 + 200 * brake / 80, hz, "C")
    return ch


def snap(t: float, packet_id: int = 0, **kw) -> Snapshot:
    return Snapshot(t=t, source="test", packet_id=packet_id, **kw)


class ScriptedSource:
    """Yields the given snapshots (None = nothing new); raises ``end`` when exhausted."""

    name = "scripted"

    def __init__(self, items, end: Exception | None = None):
        self.items = list(items)
        self.end = end or SourceExhausted("done")
        self.opened = False
        self.reads = 0

    def open(self):
        self.opened = True

    def read(self):
        self.reads += 1
        if not self.items:
            raise self.end
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.opened = False
