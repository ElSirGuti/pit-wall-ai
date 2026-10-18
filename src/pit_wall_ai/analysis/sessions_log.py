"""One summary line per session, kept locally: what "against last time" in the debrief reads.

``sessions.jsonl`` sits next to the alert memory (``~/.pit-wall-ai/``). It is local, deletable and optional
(``engineer.debrief.history``). A comparison is only made between the same track, layout and car in the same kind
of session; a different tyre compound is said, not hidden.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from ..home import home_dir
from .debrief import Debrief

log = logging.getLogger(__name__)
MAX_RECORDS = 300


def summary_of(d: Debrief, *, when: datetime | None = None) -> dict | None:
    """The line stored for a session; ``None`` when there is nothing worth comparing later."""
    r, p = d.facts.get("result", {}), d.facts.get("pace")
    if not p or not r:
        return None
    stints = d.facts.get("stints", {}).get("stints", [])
    return {
        "date": (when or datetime.now()).isoformat(timespec="seconds"),
        "track": r.get("track", ""),
        "layout": r.get("layout", ""),
        "car": r.get("car", ""),
        "session": r.get("session", ""),
        "laps": r.get("laps", 0),
        "best_ms": p["best_ms"],
        "ideal_ms": p.get("ideal_ms"),
        "consistency_ms": p.get("consistency_ms"),
        "valid_laps": p.get("valid_laps", 0),
        "compounds": sorted({s["compound"] for s in stints if s.get("compound")}),
        "pit_loss_s": d.facts.get("pit", {}).get("median_loss_s"),
    }


class SessionStore:
    def __init__(self, path: str | Path | None = None, max_records: int = MAX_RECORDS) -> None:
        self.path = Path(path) if path else home_dir() / "sessions.jsonl"
        self.max_records = max_records
        self._lock = threading.Lock()

    def records(self) -> list[dict]:
        out: list[dict] = []
        try:
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue  # a half-written line must not lose the others
        except OSError:
            return []
        return out

    def previous(self, summary: dict) -> dict | None:
        """The latest earlier session on the same track, layout and car, of the same kind."""
        for rec in reversed(self.records()):
            if rec.get("date", "") >= summary.get("date", "~"):
                continue
            if all(rec.get(k) == summary.get(k) for k in ("track", "layout", "car", "session")):
                return rec
        return None

    def add(self, summary: dict) -> None:
        with self._lock:
            recs = self.records()
            recs.append(summary)
            recs = recs[-self.max_records :]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                    encoding="utf-8",
                )
                tmp.replace(self.path)
            except OSError:
                log.exception("could not write the sessions file")


def pit_loss_for(store: SessionStore, track: str, car: str) -> float | None:
    """The cost of a stop at this circuit with this car, from the author's own earlier stops (median of the sessions)."""
    import statistics

    vals = [
        r["pit_loss_s"]
        for r in store.records()
        if r.get("track") == track and r.get("car") == car and r.get("pit_loss_s") is not None
    ]
    return round(statistics.median(vals), 1) if vals else None


def compare(summary: dict, prev: dict) -> dict:
    """How this session went against the previous one (negative = faster / tighter)."""
    out: dict = {
        "previous_date": prev.get("date", "")[:10],
        "best_delta_ms": summary["best_ms"] - prev["best_ms"],
        "same_compound": sorted(summary.get("compounds") or [])
        == sorted(prev.get("compounds") or []),
    }
    if summary.get("consistency_ms") and prev.get("consistency_ms"):
        out["consistency_delta_ms"] = summary["consistency_ms"] - prev["consistency_ms"]
    return out


def attach_history(
    d: Debrief, store: SessionStore, *, save: bool = True, when: datetime | None = None
) -> None:
    """Add the "against last time" block (when there is a comparable session) and keep this session's line."""
    s = summary_of(d, when=when)
    if s is None:
        return
    prev = store.previous(s)
    if prev is not None:
        d.facts["vs_last"] = compare(s, prev)
    if save:
        store.add(s)
