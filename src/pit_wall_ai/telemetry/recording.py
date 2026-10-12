"""Readers for recorded sessions, used by the replay source: MoTeC ``.ld`` and MoTeC-style CSV.

Both produce a :class:`Recording`: named channels resampled to one common rate. The channel names and
units are whatever the logger exported (ACTI for Assetto Corsa); mapping them to the live
:class:`~pit_wall_ai.telemetry.snapshot.Snapshot` is the replay source's job.

The ``.ld`` parser follows the public description of the format (the same one the sibling project
``motorsport-analytics-pipeline`` implements, see ``PROVENANCE.md``): a header with pointers, then a
linked list of 124-byte channel descriptors, each pointing at a contiguous block of samples.
"""

from __future__ import annotations

import csv
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_HEADER_MARKER = 0x40
_CHANNEL_META_SIZE = 124
_MAX_CHANNELS = 5000
_TEXT_FIELDS = {
    "date": (0x5E, 16),
    "time": (0x7E, 16),
    "driver": (0x9E, 64),
    "vehicle": (0xDE, 64),
    "venue": (0x15E, 64),
}


class RecordingError(ValueError):
    """The file is not a readable recording."""


@dataclass
class Recording:
    channels: dict[str, np.ndarray]
    hz: float
    meta: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    path: str = ""

    @property
    def n(self) -> int:
        return len(next(iter(self.channels.values()))) if self.channels else 0

    @property
    def duration_s(self) -> float:
        return self.n / self.hz if self.hz else 0.0

    def has(self, name: str) -> bool:
        return name in self.channels

    def value(self, name: str, i: int, default: float = 0.0) -> float:
        a = self.channels.get(name)
        if a is None:
            return default
        v = a[min(i, len(a) - 1)]
        return default if not np.isfinite(v) else float(v)


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1", errors="replace").strip()


def _resample_hold(a: np.ndarray, n_out: int) -> np.ndarray:
    """Zero-order hold to ``n_out`` samples (channels of a lower rate repeat their last value)."""
    if len(a) == n_out:
        return a
    idx = np.minimum((np.arange(n_out) * len(a)) // n_out, len(a) - 1)
    return a[idx]


# ── .ld ─────────────────────────────────────────────────────────────────────────────────────────────
def load_ld(path: str | Path) -> Recording:
    path = Path(path)
    data = np.memmap(path, dtype=np.uint8, mode="r")
    try:
        if len(data) < 0x200 or struct.unpack_from("<I", data, 0)[0] != _HEADER_MARKER:
            raise RecordingError(f"{path.name}: not a MoTeC .ld file")
        meta_ptr = struct.unpack_from("<I", data, 8)[0]
        meta = {k: _cstr(bytes(data[o : o + n])) for k, (o, n) in _TEXT_FIELDS.items()}
        raw: dict[str, tuple[np.ndarray, float, str]] = {}
        seen: set[int] = set()
        ptr = meta_ptr
        while ptr and len(raw) < _MAX_CHANNELS:
            if ptr in seen or ptr + _CHANNEL_META_SIZE > len(data):
                break
            seen.add(ptr)
            blk = bytes(data[ptr : ptr + _CHANNEL_META_SIZE])
            _prev, nxt, dptr, n, _cnt, dtype_a, dtype, freq, shift, mul, scale, dec = (
                struct.unpack_from("<IIIIHHHHhhhh", blk, 0)
            )
            name, unit = _cstr(blk[32:64]), _cstr(blk[72:84])
            ptr = nxt
            np_type = ({2: "<f2", 4: "<f4"} if dtype_a == 7 else {2: "<i2", 4: "<i4"}).get(dtype)
            if np_type is None or freq <= 0 or n <= 1 or name in raw:
                continue
            size = np.dtype(np_type).itemsize
            if dptr <= 0 or dptr + n * size > len(data):
                continue  # truncated channel: skip, keep the rest
            values = np.frombuffer(bytes(data[dptr : dptr + n * size]), dtype=np_type).astype(
                np.float64
            )
            if dtype_a != 7:
                values = (values / (scale or 1) * 10.0 ** (-dec) + shift) * (mul or 1)
            raw[name] = (values, float(freq), unit)
    finally:
        del data
    if not raw:
        raise RecordingError(f"{path.name}: no readable channels")
    hz = max(f for _, f, _ in raw.values())
    n_out = max(int(round(len(v) * hz / f)) for v, f, _ in raw.values())
    channels = {k: _resample_hold(v, n_out) for k, (v, _f, _u) in raw.items()}
    return Recording(channels, hz, meta, {k: u for k, (_v, _f, u) in raw.items()}, str(path))


# ── MoTeC CSV ───────────────────────────────────────────────────────────────────────────────────────
def _num(cell: str) -> float:
    try:
        return float(cell.replace(",", "."))
    except ValueError:
        return float("nan")


def load_csv(path: str | Path) -> Recording:
    """MoTeC CSV export: quoted metadata lines, a header row, a units row, then decimal-comma data."""
    path = Path(path)
    meta: dict[str, str] = {}
    header: list[str] | None = None
    units: list[str] = []
    rows: list[list[float]] = []
    hz = 0.0
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if not row or not any(c.strip() for c in row):
                continue
            if header is None:
                if (
                    sum(1 for c in row if c.strip()) >= 7
                ):  # metadata rows have at most 5 filled cells
                    header = [c.strip() for c in row]
                else:
                    for k_idx in (0, 4):  # rows hold up to two "key","value" pairs
                        if len(row) > k_idx + 1 and row[k_idx].strip():
                            meta[row[k_idx].strip()] = row[k_idx + 1].strip()
                    if row[0].strip() == "Sample Rate" and len(row) > 1:
                        hz = _num(row[1])
                continue
            if not units:
                units = [c.strip() for c in row]
                if not any(
                    c and not c.replace(",", ".").replace("-", "").replace(".", "").isdigit()
                    for c in row
                ):
                    rows.append([_num(c) for c in row])  # no units row: this was data
                    units = [""] * len(header)
                continue
            if len(row) == len(header):
                rows.append([_num(c) for c in row])
    if header is None or not rows:
        raise RecordingError(f"{path.name}: no data rows found")
    table = np.asarray(rows, dtype=np.float64)
    channels = {name: table[:, i] for i, name in enumerate(header) if name}
    if hz <= 0 and "Time" in channels:
        dt = np.diff(channels["Time"])
        dt = dt[dt > 0]
        hz = float(1.0 / np.median(dt)) if dt.size else 0.0
    if hz <= 0:
        raise RecordingError(f"{path.name}: cannot tell the sample rate")
    meta_norm = {k.lower(): v for k, v in meta.items()}
    out_meta = {k: meta_norm.get(k, "") for k in ("venue", "vehicle", "driver", "session")}
    return Recording(channels, hz, out_meta, dict(zip(header, units, strict=False)), str(path))


def load_recording(path: str | Path) -> Recording:
    p = Path(path)
    if not p.exists():
        raise RecordingError(f"{p} does not exist")
    suffix = p.suffix.lower()
    if suffix == ".ld":
        return load_ld(p)
    if suffix == ".csv":
        return load_csv(p)
    raise RecordingError(f"unsupported recording type {suffix!r} (use .ld or .csv)")
