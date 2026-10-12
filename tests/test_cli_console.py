"""Console dashboard, the field dump (unit confirmation tool) and the command line."""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from pit_wall_ai import cli
from pit_wall_ai.telemetry.console import bar, fmt_time, render_dashboard
from pit_wall_ai.telemetry.dump import (
    FieldRange,
    RangeTracker,
    flatten,
    format_dump,
    report_json,
    unit_hint,
)
from pit_wall_ai.telemetry.poller import PollerStats
from pit_wall_ai.telemetry.snapshot import StaticInfo
from pit_wall_ai.telemetry.source import SourceUnavailable

from .helpers import lap_channels, snap, write_ld


def text_of(renderable, width=130) -> str:
    console = Console(file=io.StringIO(), width=width, force_terminal=False, color_system=None)
    console.print(renderable)
    return console.file.getvalue()


@pytest.fixture()
def ld(tmp_path):
    return str(
        write_ld(
            tmp_path / "s.ld",
            lap_channels(n_laps=2),
            vehicle="ks_car",
            venue="spa",
            driver="Test Driver",
        )
    )


def full_snap():
    return snap(
        t=1.0,
        packet_id=5,
        gas=0.5,
        brake=0.25,
        speed_kmh=187.5,
        rpm=6500,
        gear=4,
        fuel=42.0,
        completed_laps=3,
        current_time_ms=61234,
        last_time_ms=118500,
        best_time_ms=117900,
        normalized_pos=0.4321,
        tyre_pressure=(26.0, 26.5, 27.0, 27.5),
        tyre_core_temp=(80.0, 81.0, 82.0, 83.0),
        brake_temp=(300.0, 310.0, 320.0, 330.0),
        static=StaticInfo(sm_version="1.7", car_model="ks_porsche", track="spa"),
        session=2,
        status=2,
    )


def test_bar_and_time_formatting():
    assert bar(0.5, 10).plain == "█" * 5 + "░" * 5
    assert (
        bar(5.0, 10).plain == "█" * 10
        and bar(-1, 10).plain == "░" * 10
        and bar(1, 10, 0).plain == "░" * 10
    )
    assert (
        fmt_time(0) == "--:--.---"
        and fmt_time(61234) == "1:01.234"
        and fmt_time(118500) == "1:58.500"
    )


def test_dashboard_shows_the_driving_state_tyres_and_stats():
    out = text_of(
        render_dashboard(
            full_snap(),
            PollerStats(status="running", measured_hz=59.8, samples=120),
            source="replay",
        )
    )
    for expected in (
        "187.5 km/h",
        "6500 / 4",
        "50.0%",
        "25.0%",
        "1:01.234",
        "1:58.500",
        "race (live)",
        "FL",
        "RR",
        "26.0",
        "330",
        "ks_porsche @ spa",
        "59.8 Hz",
        "samples 120",
        "replay",
    ):
        assert expected in out, expected


def test_dashboard_without_data_explains_what_it_waits_for():
    out = text_of(
        render_dashboard(None, PollerStats(status="waiting", last_error="no game"), source="ac")
    )
    assert "Waiting for telemetry" in out and "no game" in out and "waiting" in out


def test_flatten_labels_wheels_axes_and_hides_the_player():
    s = snap(
        t=0.0,
        tyre_pressure=(1.0, 2.0, 3.0, 4.0),
        acc_g=(0.1, 0.2, 0.3),
        static=StaticInfo(car_model="c", player_name="Secret", player_surname="Name"),
    )
    flat = flatten(s)
    assert (
        flat["tyre_pressure.RL"] == 3.0
        and flat["acc_g.z"] == 0.3
        and flat["static.car_model"] == "c"
    )
    assert "static.player_name" not in flat and "t" not in flat
    assert flatten(s, include_player=True)["static.player_name"] == "Secret"
    assert "Secret" not in format_dump(s) and "Secret" in format_dump(s, include_player=True)


def test_unit_hints_describe_the_observed_range_without_claiming_a_unit():
    def r(lo, hi, mean=None):
        f = FieldRange()
        f.add(lo)
        f.add(hi)
        if mean is not None:
            f.total, f.n = mean * 2, 2
        return f

    assert "degrees" in unit_hint("steer_angle", r(-270, 270))
    assert "radians" in unit_hint("steer_angle", r(-4, 4))
    assert "psi" in unit_hint("tyre_pressure.FL", r(24, 28))
    assert "bar" in unit_hint("tyre_pressure.FL", r(1.5, 1.9))
    assert "percent" in unit_hint("tyre_wear.FL", r(90, 100)) and "fraction" in unit_hint(
        "tyre_wear.FL", r(0.8, 1.0)
    )
    assert "0..100" in unit_hint("gas", r(0, 100)) and unit_hint("gas", r(0, 1)) == "0..1"
    assert "confirm" in unit_hint("gear", r(0, 6)) and unit_hint("unknown_field", r(0, 1)) == ""


def test_range_tracker_summarises_numbers_only():
    tr = RangeTracker()
    for v in (10.0, 20.0, 30.0):
        tr.add(snap(t=v, speed_kmh=v, is_in_pit=True, tyre_compound="SM"))
    rep = tr.report()
    assert rep["speed_kmh"] == {"min": 10.0, "max": 30.0, "mean": 20.0, "unit_hint": "km/h"}
    assert "is_in_pit" not in rep and "tyre_compound" not in rep and tr.samples == 3
    payload = json.loads(report_json(tr, snap(t=1.0), include_player=False))
    assert payload["samples"] == 3 and "verify against the in-game HUD" in payload["note"]


# ── command line ─────────────────────────────────────────────────────────────────────────────────────
def test_cli_run_plain_replay_prints_live_lines(ld, capsys):
    code = cli.main(
        [
            "run",
            "--source",
            "replay",
            "--file",
            ld,
            "--speed",
            "4",
            "--duration",
            "1.2",
            "--plain",
            "--hz",
            "100",
            "--log-level",
            "WARNING",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "[running]" in out and "km/h" in out and "Hz" in out


def test_cli_run_ends_by_itself_when_the_recording_ends(ld, capsys):
    code = cli.main(["run", "--file", ld, "--start", "119.5", "--plain", "--log-level", "ERROR"])
    assert code == 0 and "[ended]" in capsys.readouterr().out


def test_cli_configuration_errors_are_friendly_and_exit_2(tmp_path, capsys):
    assert cli.main(["run", "--source", "replay"]) == 2
    assert "no file was given" in capsys.readouterr().err
    assert cli.main(["run", "--file", str(tmp_path / "missing.ld")]) == 2
    assert "not found" in capsys.readouterr().err
    assert cli.main(["run", "--config", str(tmp_path / "nope.yaml")]) == 2
    with pytest.raises(SystemExit):
        cli.main(["run", "--hz", "abc"])


def test_cli_dump_once_hides_the_driver_and_the_flag_shows_it(ld, capsys):
    assert cli.main(["dump-shm", "--file", ld, "--log-level", "ERROR"]) == 0
    out = capsys.readouterr().out
    assert "speed_kmh" in out and "static.car_model" in out and "Test Driver" not in out
    assert cli.main(["dump-shm", "--file", ld, "--include-player", "--log-level", "ERROR"]) == 0
    assert "Test Driver" in capsys.readouterr().out


def test_cli_dump_watch_writes_a_json_report(ld, tmp_path, capsys):
    report = tmp_path / "r.json"
    code = cli.main(
        [
            "dump-shm",
            "--file",
            ld,
            "--speed",
            "5",
            "--watch",
            "0.6",
            "--json",
            str(report),
            "--hz",
            "100",
            "--log-level",
            "ERROR",
        ]
    )
    assert code == 0 and "report written" in capsys.readouterr().out
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["samples"] > 5 and "speed_kmh" in data["ranges"]
    assert data["ranges"]["speed_kmh"]["unit_hint"] == "km/h"
    assert "Test Driver" not in report.read_text(encoding="utf-8")


def test_cli_dump_reports_a_missing_game_with_exit_1(monkeypatch, capsys):
    class NoGame:
        name = "ac"

        def open(self):
            raise SourceUnavailable("no game")

        def read(self):
            raise AssertionError

        def close(self):
            pass

    monkeypatch.setattr(cli, "ACSharedMemorySource", NoGame)
    assert cli.main(["dump-shm", "--wait", "0.6", "--log-level", "ERROR"]) == 1
    assert "is Assetto Corsa running" in capsys.readouterr().err
