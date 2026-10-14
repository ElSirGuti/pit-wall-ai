"""Cut a window out of a recording and store it at a lower rate: a few hundred KB instead of tens of MB.

Used to turn a long session of the author into a small fixture (or to share a lap) without publishing the
whole recording. Works on ``.pwrec.gz``, MoTeC ``.ld`` and ``.csv``; the output is always ``.pwrec.gz``.
"""

from __future__ import annotations

from pathlib import Path

from ..analysis.offline import iter_snapshots
from .recorder import Recorder


def trim(
    src: str | Path,
    out: str | Path,
    *,
    start_s: float = 0.0,
    end_s: float | None = None,
    rate_hz: float = 10.0,
    session: str = "practice",
) -> int:
    """Samples written to ``out`` for the window [start_s, end_s] of ``src``."""
    _static, snaps = iter_snapshots(src, session=session)
    rec = Recorder(out, hz=rate_hz)
    rec.open()
    n = 0
    t0: float | None = None
    try:
        for s in snaps:
            t0 = s.t if t0 is None else t0
            rel = s.t - t0
            if rel < start_s:
                continue
            if end_s is not None and rel > end_s:
                break
            if rec.write(s):
                n += 1
    finally:
        rec.close()
    return n
