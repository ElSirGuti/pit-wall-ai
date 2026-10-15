"""What the engineer said, kept between sessions: a small local file the author can read and delete.

One JSON line per alert: when (wall clock), which run of the program, the track, the car, the session type,
the rule and the sentence. Only alerts of **live** runs are stored (a replay of a recording is not a memory).
It is local, never sent anywhere, and the numbers in it are the ones the engineer said at the time.

Used by ``chat/recall.py`` to answer "what did you tell me about corner 4 last time?".
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)
ENV_HOME = "PIT_WALL_AI_HOME"  # tests and portable installs point the memory somewhere else


def default_path() -> Path:
    base = os.environ.get(ENV_HOME)
    return (Path(base) if base else Path.home() / ".pit-wall-ai") / "memory.jsonl"


@dataclass(frozen=True)
class StoredAlert:
    ts: float  # wall clock, seconds since the epoch
    run: str
    track: str
    car: str
    session: str
    rule: str
    text: str


class AlertMemory:
    def __init__(self, path: str | Path | None = None, *, max_records: int = 500) -> None:
        self.path = Path(path) if path else default_path()
        self.max_records = max_records
        self.run = f"{int(time.time())}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._since_trim = 0

    # -- writing -----------------------------------------------------------------------------------------
    def record(self, *, rule: str, text: str, track: str, car: str, session: str) -> None:
        line = {
            "ts": time.time(),
            "run": self.run,
            "track": track,
            "car": car,
            "session": session,
            "rule": rule,
            "text": text,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            self._since_trim += 1
            if self._since_trim >= 50:
                self.trim()
        except OSError:
            log.warning("could not write the alert memory", exc_info=True)

    def trim(self) -> None:
        """Keep the last ``max_records`` lines."""
        self._since_trim = 0
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if len(lines) > self.max_records:
                self.path.write_text("\n".join(lines[-self.max_records :]) + "\n", encoding="utf-8")
        except OSError:
            log.warning("could not trim the alert memory", exc_info=True)

    # -- reading -----------------------------------------------------------------------------------------
    def _all(self) -> Iterable[StoredAlert]:
        try:
            raw = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[StoredAlert] = []
        for line in raw:
            try:
                d = json.loads(line)
                out.append(
                    StoredAlert(
                        float(d["ts"]),
                        str(d["run"]),
                        str(d.get("track", "")),
                        str(d.get("car", "")),
                        str(d.get("session", "")),
                        str(d.get("rule", "")),
                        str(d["text"]),
                    )
                )
            except (ValueError, KeyError, TypeError):
                continue  # a damaged line is skipped, never an error
        return out

    def previous(self, track: str | None = None) -> list[StoredAlert]:
        """Alerts of earlier runs (not this one), newest first, optionally only for one track."""
        rows = [a for a in self._all() if a.run != self.run and (not track or a.track == track)]
        rows.sort(key=lambda a: a.ts, reverse=True)
        return rows

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()


def listener(memory: AlertMemory):
    """The callback for ``Engineer.listeners``: stores every alert with the session it belongs to."""

    def on_alert(rec, snap, state) -> None:
        memory.record(
            rule=rec.rule,
            text=rec.text,
            track=getattr(snap.static, "track", "") or "",
            car=getattr(snap.static, "car_model", "") or "",
            session=getattr(state, "session", "") or "",
        )

    return on_alert
