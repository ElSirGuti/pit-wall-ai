"""Recording a session in the native format, replaying it, and the dashboard with session and laps."""

from __future__ import annotations

import gzip
import io
from dataclasses import replace

import pytest
from rich.console import Console

from pit_wall_ai import cli
from pit_wall_ai.session.engine import SessionEngine
from pit_wall_ai.telemetry.console import render_dashboard
from pit_wall_ai.telemetry.poller import PollerStats
from pit_wall_ai.telemetry.recorder import (
    NativeReplaySource,
    Recorder,
    is_native,
    load_native,
    snapshot_from_flat,
)
from pit_wall_ai.telemetry.snapshot import StaticInfo
from pit_wall_ai.telemetry.source import SourceExhausted, SourceUnavailable

from .helpers import lap_channels, snap, write_ld
from .scenario import make_session


def rich_snapshot(t=1.0, **kw):
    base = dict(
        packet_id=7,
        gas=0.5,
        brake=0.25,
        clutch=1.0,
        steer_angle=-0.3,
        gear=4,
        rpm=6500,
        speed_kmh=187.5,
        fuel=42.5,
        velocity=(1.0, 2.0, 3.0),
        local_velocity=(0.1, 0.2, 40.0),
        acc_g=(-1.1, 0.0, 0.4),
        heading=2.7,
        tc=0.082,
        abs=0.12,
        brake_bias=0.74,
        pit_limiter_on=True,
        car_damage=(0.1, 0.2, 0.3, 0.4, 0.5),
        wheel_slip=(0.1, 0.2, 0.3, 0.4),
        tyre_pressure=(26.0, 26.5, 27.0, 27.5),
        tyre_core_temp=(80.0, 81.0, 82.0, 83.0),
        brake_temp=(300.0, 310.0, 320.0, 330.0),
        tyres_out=2,
        status=2,
        session=2,
        completed_laps=3,
        current_time_ms=61234,
        last_time_ms=118500,
        best_time_ms=117900,
        normalized_pos=0.4321,
        is_in_pit=True,
        is_in_pit_lane=False,
        tyre_compound="GT4 (GT4)",
        flags=2,
        ideal_line_on=True,
        static=StaticInfo(
            sm_version="1.7",
            car_model="lfm_supra",
            track="vhe_hockenheim",
            max_fuel=110.0,
            tyre_radius=(0.34, 0.34, 0.3525, 0.3525),
            has_extra_lap=True,
        ),
    )
    base.update(kw)
    return snap(t=t, **base)


def test_every_field_survives_the_recording_roundtrip(tmp_path):
    original = rich_snapshot()
    path = tmp_path / "s.pwrec.gz"
    rec = Recorder(path, hz=1000)
    rec.open()
    assert rec.write(original)
    rec.close()
    loaded = load_native(path)
    assert loaded.n == 1 and loaded.statics[0].track == "vhe_hockenheim"
    back = snapshot_from_flat(loaded.rows[0], loaded.statics[0], t=original.t)
    assert replace(back, source="test") == original  # exact, tuples, bools, ints and strings


def test_recording_is_decimated_and_records_static_changes(tmp_path):
    path = tmp_path / "d.pwrec.gz"
    rec = Recorder(path, hz=10)  # one sample per 0.1 s
    rec.open()
    written = 0
    for i in range(100):  # 100 samples at 100 Hz = 1 s
        s = rich_snapshot(t=i * 0.01, packet_id=i)
        if i >= 60:
            s = replace(s, static=replace(s.static, track="spa"))
        written += rec.write(s)
    rec.close()
    assert written == 10 and rec.samples == 10
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("#static ") == 2 and "vhe_hockenheim" in text and '"spa"' in text
    loaded = load_native(path)
    assert [s.track for s in loaded.statics][0] == "vhe_hockenheim" and loaded.statics[
        -1
    ].track == "spa"
    assert loaded.duration_s == pytest.approx(0.9, abs=0.01)


def test_the_player_name_is_never_written(tmp_path):
    s = rich_snapshot(
        static=StaticInfo(car_model="c", player_name="Secret", player_surname="Person")
    )
    path = tmp_path / "p.pwrec.gz"
    rec = Recorder(path, hz=100)
    rec.open()
    rec.write(s)
    rec.close()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        text = fh.read()
    assert "Secret" not in text and "Person" not in text
    assert text.splitlines()[1].split(",").count("source") == 1  # one source column, not two
    assert load_native(path).statics[0].player_name == ""


def test_truncated_and_empty_recordings(tmp_path):
    path = tmp_path / "t.pwrec.gz"
    rec = Recorder(path, hz=1000)
    rec.open()
    for i in range(20):
        rec.write(rich_snapshot(t=i * 0.01, packet_id=i))
    rec.close()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = fh.read()
    cut = tmp_path / "cut.pwrec"
    cut.write_text(raw[: len(raw) - 40], encoding="utf-8")  # the last line is half written
    assert load_native(cut).n == 19
    empty = tmp_path / "e.pwrec"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        load_native(empty)
    assert is_native("x.pwrec.gz") and is_native("x.PWREC") and not is_native("x.ld")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_native_replay_follows_the_recorded_timing(tmp_path):
    path = tmp_path / "r.pwrec.gz"
    rec = Recorder(path, hz=1000)
    rec.open()
    for i in range(101):  # 0 .. 1.0 s at 100 Hz
        rec.write(rich_snapshot(t=i * 0.01, packet_id=i, speed_kmh=float(i)))
    rec.close()
    clock = Clock()
    src = NativeReplaySource(path, clock=clock)
    src.open()
    assert src.read().speed_kmh == 0.0 and src.read() is None
    clock.t += 0.255
    assert src.read().speed_kmh == 25.0
    clock.t += 5
    with pytest.raises(SourceExhausted):
        src.read()
    fast = NativeReplaySource(path, speed=2.0, start_s=0.4, clock=clock)
    fast.open()
    assert fast.read().speed_kmh == 40.0
    clock.t += 0.1
    assert fast.read().speed_kmh == 60.0  # 2x: 0.2 s of recording per 0.1 s
    looping = NativeReplaySource(path, loop=True, clock=clock)
    looping.open()
    clock.t += 1.5
    assert looping.read().speed_kmh == pytest.approx(50.0, abs=1.0)
    with pytest.raises(ValueError):
        NativeReplaySource(path, speed=0)


def test_a_recorded_session_gives_the_same_laps_when_replayed(tmp_path):
    snaps = make_session([20.0, 90.0, 88.5, 91.0], hz=20)
    path = tmp_path / "laps.pwrec.gz"
    rec = Recorder(path, hz=1000)
    rec.open()
    for s in snaps:
        rec.write(s)
    rec.close()
    direct = SessionEngine()
    for s in snaps:
        direct.process(s)
    direct.flush()
    loaded = load_native(path)
    replayed = SessionEngine()
    for i, row in enumerate(loaded.rows):
        replayed.process(snapshot_from_flat(row, loaded.statics[i], t=loaded.times[i]))
    replayed.flush()
    assert [lap.time_ms for lap in replayed.view().laps] == [
        lap.time_ms for lap in direct.view().laps
    ]
    assert replayed.view().best.time_ms == 88500


def text_of(renderable, width=140) -> str:
    console = Console(file=io.StringIO(), width=width, force_terminal=False, color_system=None)
    console.print(renderable)
    return console.file.getvalue()


def test_dashboard_shows_session_phase_laps_and_delta():
    eng = SessionEngine()
    last = None
    for s in make_session([20.0, 90.0, 88.5, 93.0], hz=20):
        eng.process(s)
        last = s
    view = eng.view()
    out = text_of(
        render_dashboard(
            last, PollerStats(status="running", measured_hz=60.0), source="ac", view=view
        )
    )
    for expected in (
        "Session",
        "practice",
        "hockenheim",
        "Laps",
        "1:28.500",
        "★",
        "Best",
        "out",
        "Delta",
    ):
        assert expected in out, expected
    assert "(code not verified live)" not in out  # practice has been seen live
    race = SessionEngine()
    for s in make_session([20.0, 90.0], session=4)[:60]:
        race.process(s)
    out = text_of(render_dashboard(snap(t=0.0), PollerStats(status="running"), view=race.view()))
    assert "time_attack (code not verified live)" in out


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────────
def test_cli_record_then_replay_the_recording(tmp_path, capsys):
    ld = write_ld(tmp_path / "s.ld", lap_channels(n_laps=2))
    out = tmp_path / "rec.pwrec.gz"
    code = cli.main(
        [
            "record",
            "--source",
            "replay",
            "--file",
            str(ld),
            "--speed",
            "4",
            "--out",
            str(out),
            "--duration",
            "1.0",
            "--rate",
            "20",
            "--hz",
            "100",
            "--log-level",
            "ERROR",
        ]
    )
    text = capsys.readouterr().out
    assert code == 0 and "samples written" in text and out.exists()
    assert load_native(out).n >= 10
    code = cli.main(
        [
            "run",
            "--file",
            str(out),
            "--loop",
            "--plain",
            "--duration",
            "1.5",
            "--hz",
            "100",
            "--log-level",
            "ERROR",
        ]
    )
    out_text = capsys.readouterr().out
    assert code == 0 and "[running]" in out_text and "practice/" in out_text


def test_cli_record_without_the_game_exits_with_1(monkeypatch, tmp_path, capsys):
    class NoGame:
        name = "ac"

        def open(self):
            raise SourceUnavailable("no game")

        def read(self):
            raise AssertionError

        def close(self):
            pass

    monkeypatch.setattr(cli, "ACSharedMemorySource", NoGame)
    code = cli.main(
        [
            "record",
            "--out",
            str(tmp_path / "x.pwrec.gz"),
            "--duration",
            "0.8",
            "--log-level",
            "ERROR",
        ]
    )
    assert code == 1 and "0 samples" in capsys.readouterr().out
