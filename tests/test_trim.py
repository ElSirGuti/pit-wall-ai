"""pit-wall trim: a window of a recording at a lower rate."""

from __future__ import annotations

import os
from pathlib import Path

from pit_wall_ai.cli import main
from pit_wall_ai.telemetry.recorder import load_native
from pit_wall_ai.telemetry.trim import trim

FIXTURE = Path(__file__).parent / "fixtures" / "live_lfm_magione_race_window.pwrec.gz"


def test_trim_keeps_the_window_and_never_exceeds_the_requested_rate(tmp_path):
    # the fixture itself has gaps (menu, restart), so the count depends on the data: bound it, do not fix it
    out = tmp_path / "w.pwrec.gz"
    n = trim(FIXTURE, out, start_s=60.0, end_s=120.0, rate_hz=5.0, session="race")
    assert 50 <= n <= 5 * 60 + 5
    rec = load_native(out)
    assert rec.n == n and rec.duration_s <= 61.0
    assert rec.statics[0].track == "magione"


def test_a_lower_rate_keeps_fewer_samples(tmp_path):
    hi = trim(FIXTURE, tmp_path / "hi.pwrec.gz", start_s=0, end_s=200, rate_hz=10.0, session="race")
    lo = trim(FIXTURE, tmp_path / "lo.pwrec.gz", start_s=0, end_s=200, rate_hz=2.0, session="race")
    assert 0 < lo < hi


def test_trim_command_reports_size_and_stays_small(tmp_path, capsys):
    out = tmp_path / "small.pwrec.gz"
    rc = main(
        [
            "trim",
            "--file",
            str(FIXTURE),
            "--out",
            str(out),
            "--from",
            "0",
            "--to",
            "300",
            "--rate",
            "4",
        ]
    )
    text = capsys.readouterr().out
    assert rc == 0 and "samples written" in text and "KB" in text
    assert os.path.getsize(out) < os.path.getsize(FIXTURE)


def test_an_empty_window_is_an_error(tmp_path, capsys):
    rc = main(
        [
            "trim",
            "--file",
            str(FIXTURE),
            "--out",
            str(tmp_path / "x.pwrec.gz"),
            "--from",
            "100000",
            "--to",
            "100010",
        ]
    )
    assert rc == 1
