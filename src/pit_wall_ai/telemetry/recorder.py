"""Record a live session in the project's own format and replay it later.

Why: the ACTI export is an approximation of the game's fields (it converts units), so it cannot stand in
for the real thing in the tests of later phases. A recording made here holds exactly what the game published.

Format ``.pwrec.gz``: a gzip text file. Lines starting with ``#static `` carry the static page as JSON (a new
one whenever it changes, e.g. another track); the first non-comment line is the CSV header (``t`` plus every
dynamic field of :class:`Snapshot`, flattened as in ``dump-shm``) and the rest are samples. Gzip is
flushed every second so a crash loses at most a second.
"""

from __future__ import annotations

import csv
import dataclasses
import gzip
import io
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .dump import PRIVATE_FIELDS, flatten
from .poller import TelemetryPoller
from .snapshot import CORNERS, Snapshot, StaticInfo
from .source import SourceExhausted, SourceUnavailable

log = logging.getLogger(__name__)

SUFFIXES = (".pwrec.gz", ".pwrec")
_SKIP = {"source", "static"}


def is_native(path: str | Path) -> bool:
    return str(path).lower().endswith(SUFFIXES)


def _dynamic_flat(s: Snapshot) -> dict:
    return {
        k: v
        for k, v in flatten(s, include_player=False).items()
        if not k.startswith("static.") and k != "source"
    }


class Recorder:
    def __init__(
        self, path: str | Path, *, hz: float = 20.0, clock: Callable[[], float] = time.monotonic
    ):
        if hz <= 0:
            raise ValueError("hz must be > 0")
        self.path = Path(path)
        self.period = 1.0 / hz
        self._clock = clock
        self._fh: io.TextIOBase | None = None
        self._writer: csv.writer | None = None
        self._header: list[str] | None = None
        self._static_json = ""
        self._next_t = -1e9
        self._last_flush = 0.0
        self.samples = 0

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        opener = gzip.open if self.path.suffix == ".gz" else open
        self._fh = opener(self.path, "wt", encoding="utf-8", newline="")  # type: ignore[arg-type]
        self._writer = csv.writer(self._fh)

    def write(self, s: Snapshot) -> bool:
        """Append a sample unless it is closer than the period to the previous one. True if written."""
        if self._fh is None or self._writer is None:
            raise RuntimeError("recorder not open")
        if s.t < self._next_t:
            return False
        # Scheduled, not "a period since the last one": with a ~15.6 ms timer tick a fixed gap fell to a
        # third of the requested rate (measured on a real race: 10 Hz recorded when 20 Hz was asked).
        if self._next_t < -1e8 or s.t - self._next_t > 5 * self.period:
            self._next_t = s.t
        self._next_t += self.period
        static = {k: v for k, v in dataclasses.asdict(s.static).items() if k not in PRIVATE_FIELDS}
        static_json = json.dumps(static, ensure_ascii=False, sort_keys=True)
        if static_json != self._static_json:
            self._fh.write("#static " + static_json + "\n")
            self._static_json = static_json
        flat = _dynamic_flat(s)
        if self._header is None:
            self._header = ["t", "source", *flat]
            self._writer.writerow(self._header)
        self._writer.writerow([repr(s.t), s.source] + [flat.get(k, "") for k in self._header[2:]])
        self.samples += 1
        now = self._clock()
        if now - self._last_flush >= 1.0:
            self._fh.flush()
            self._last_flush = now
        return True

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def record_from_poller(
    poller: TelemetryPoller,
    recorder: Recorder,
    stop: threading.Event,
    *,
    duration_s: float | None = None,
) -> int:
    """Write what the poller delivers until ``stop``/``duration_s``/the source ends. Returns the sample count."""
    sub = poller.subscribe(maxsize=4096)
    t0 = time.monotonic()
    recorder.open()
    try:
        while not stop.is_set():
            snap = sub.get(timeout=0.25)
            if snap is not None:
                recorder.write(snap)
            if poller.stats.status == "ended" and len(sub) == 0:
                break
            if duration_s is not None and time.monotonic() - t0 >= duration_s:
                break
    finally:
        poller.unsubscribe(sub)
        recorder.close()
    return recorder.samples


# ── reading ──────────────────────────────────────────────────────────────────────────────────────────
_FIELDS = {f.name: f for f in dataclasses.fields(Snapshot)}
_TUPLE4 = {k for k, f in _FIELDS.items() if "Quad" in str(f.type)}
_TUPLE3 = {k for k, f in _FIELDS.items() if "Vec3" in str(f.type)}
_BOOLS = {k for k, f in _FIELDS.items() if f.type in ("bool", bool)}
_INTS = {k for k, f in _FIELDS.items() if f.type in ("int", int)}
_STRS = {k for k, f in _FIELDS.items() if f.type in ("str", str)}


def snapshot_from_flat(row: dict[str, str], static: StaticInfo, *, t: float) -> Snapshot:
    """Inverse of ``dump.flatten`` for one recorded row."""

    def num(name: str, default: float = 0.0) -> float:
        v = row.get(name, "")
        return float(v) if v not in ("", None) else default

    kw: dict = {"t": t, "source": row.get("source", "recorded"), "static": static}
    for name in _FIELDS:
        if name in ("t", "source", "static"):
            continue
        if name in _TUPLE4:
            kw[name] = tuple(num(f"{name}.{c}") for c in CORNERS)
        elif name in _TUPLE3:
            kw[name] = tuple(num(f"{name}.{a}") for a in "xyz")
        elif name == "car_damage":
            kw[name] = tuple(num(f"{name}.{i}") for i in range(5))
        elif name in _BOOLS:
            kw[name] = str(row.get(name, "")).lower() in ("true", "1")
        elif name in _INTS:
            kw[name] = int(num(name))
        elif name in _STRS:
            kw[name] = row.get(name, "")
        else:
            kw[name] = num(name)
    return Snapshot(**kw)


@dataclasses.dataclass
class NativeRecording:
    rows: list[dict[str, str]]
    statics: list[StaticInfo]  # per row
    times: list[float]  # seconds since the first row
    path: str = ""

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def duration_s(self) -> float:
        return self.times[-1] if self.times else 0.0


def load_native(path: str | Path) -> NativeRecording:
    p = Path(path)
    opener = gzip.open if p.suffix == ".gz" else open
    rows: list[dict[str, str]] = []
    statics: list[StaticInfo] = []
    times: list[float] = []
    static = StaticInfo()
    header: list[str] | None = None
    t0 = None
    try:
        with opener(p, "rt", encoding="utf-8", newline="") as fh:  # type: ignore[arg-type]
            for line in fh:
                if line.startswith("#static "):
                    data = json.loads(line[len("#static ") :])
                    names = {f.name for f in dataclasses.fields(StaticInfo)}
                    static = StaticInfo(
                        **{
                            k: (tuple(v) if isinstance(v, list) else v)
                            for k, v in data.items()
                            if k in names
                        }
                    )
                    continue
                cells = next(csv.reader([line]))
                if header is None:
                    header = cells
                    continue
                if len(cells) != len(header):
                    continue  # a half-written last line after a crash
                row = dict(zip(header, cells, strict=True))
                t = float(row["t"])
                t0 = t if t0 is None else t0
                rows.append(row)
                statics.append(static)
                times.append(t - t0)
    except (OSError, EOFError, ValueError) as exc:
        if not rows:
            raise SourceUnavailable(f"{p.name}: cannot read the recording ({exc})") from exc
        log.warning("recording ended early", extra={"file": p.name, "error": str(exc)})
    if not rows:
        raise SourceUnavailable(f"{p.name}: the recording has no samples")
    return NativeRecording(rows, statics, times, str(p))


class NativeReplaySource:
    """Replays a ``.pwrec`` recording with the original timing (``speed`` x), optionally in a loop."""

    name = "replay"

    def __init__(
        self,
        path: str | Path | NativeRecording,
        *,
        speed: float = 1.0,
        start_s: float = 0.0,
        loop: bool = False,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self._arg = path
        self.speed, self.start_s, self.loop = speed, max(0.0, start_s), loop
        self._clock = clock
        self.recording: NativeRecording | None = None
        self._t0 = 0.0
        self._last = -1

    def open(self) -> None:
        if self.recording is None:
            self.recording = (
                self._arg if isinstance(self._arg, NativeRecording) else load_native(self._arg)
            )
        self._t0 = self._clock()
        self._last = -1

    def close(self) -> None:
        pass

    def read(self) -> Snapshot | None:
        import bisect

        rec = self.recording
        if rec is None:
            raise SourceUnavailable("source not open")
        elapsed = (self._clock() - self._t0) * self.speed + self.start_s
        if elapsed > rec.duration_s:
            if not self.loop:
                raise SourceExhausted("end of recording")
            elapsed = elapsed % max(rec.duration_s, 1e-6)
        i = max(0, min(rec.n - 1, bisect.bisect_right(rec.times, elapsed + 1e-6) - 1))
        if i == self._last:
            return None
        self._last = i
        return snapshot_from_flat(rec.rows[i], rec.statics[i], t=self._clock())
