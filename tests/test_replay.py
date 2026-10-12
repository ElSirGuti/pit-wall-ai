"""Recording readers (.ld, CSV) and the replay source."""

from __future__ import annotations

import os

import numpy as np
import pytest

from pit_wall_ai.telemetry.recording import (
    Recording,
    RecordingError,
    load_csv,
    load_ld,
    load_recording,
)
from pit_wall_ai.telemetry.replay import ReplaySource
from pit_wall_ai.telemetry.source import SourceExhausted, SourceUnavailable

from .helpers import lap_channels, write_ld


@pytest.fixture()
def ld_file(tmp_path):
    return write_ld(
        tmp_path / "session.ld", lap_channels(n_laps=3), vehicle="ks_cayman", venue="spa"
    )


def test_ld_roundtrip_values_rate_and_header(ld_file):
    rec = load_ld(ld_file)
    assert rec.hz == 20.0 and rec.n == 3 * 60 * 20
    assert rec.meta["vehicle"] == "ks_cayman" and rec.meta["venue"] == "spa"
    assert rec.units["Ground Speed"] == "km/h"
    ref = lap_channels(3)["Ground Speed"][0]
    np.testing.assert_allclose(rec.channels["Ground Speed"], ref, rtol=1e-5)
    assert rec.duration_s == pytest.approx(180.0)


def test_ld_slower_channels_are_held_to_the_common_rate(tmp_path):
    ch = {
        "Ground Speed": (np.arange(40.0), 20.0, "km/h"),
        "Air Temp": (np.array([20.0, 21.0]), 1.0, "C"),
    }
    rec = load_ld(write_ld(tmp_path / "mixed.ld", ch))
    assert rec.hz == 20.0 and rec.n == 40
    assert rec.channels["Air Temp"][0] == 20.0 and rec.channels["Air Temp"][39] == 21.0


def test_ld_rejects_garbage_and_truncated_files(tmp_path):
    bad = tmp_path / "bad.ld"
    bad.write_bytes(b"not a log" * 100)
    with pytest.raises(RecordingError):
        load_ld(bad)
    good = write_ld(tmp_path / "ok.ld", lap_channels(1))
    cut = tmp_path / "cut.ld"
    cut.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 2])  # data blocks cut off
    rec = load_ld(cut)  # readable channels survive
    assert rec.n > 0
    with pytest.raises(RecordingError):
        load_recording(tmp_path / "missing.ld")
    with pytest.raises(RecordingError):
        load_recording(tmp_path / "x.txt")


def _write_motec_csv(path, hz="20,000"):
    lines = [
        '"Format","MoTeC CSV File",,,"Workbook",""',
        '"Venue","fn_imola",,,"Worksheet",""',
        '"Vehicle","ks_cayman",,,"Vehicle Desc",""',
        '"Driver","Test Driver",,,"Engine ID",""',
        f'"Sample Rate","{hz}","Hz",,"End Time","1,000","s"',
        '"Comment","x",,,"Session","PRACTICE"',
        "",
        '"Ground Speed","Throttle Pos","Brake Pos","Car Pos Norm","Gear","Fuel Level","Extra"',
        '"km/h","%","%","","","l",""',
        "",
        '"100,5","100,0","0,0","0,000","3","50,0","1"',
        '"110,5","90,0","0,0","0,010","3","49,9","1"',
        '"120,0","0,0","50,5","0,020","3","49,8","1"',
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_csv_motec_export_decimal_comma_units_and_metadata(tmp_path):
    rec = load_csv(_write_motec_csv(tmp_path / "s.csv"))
    assert rec.hz == 20.0 and rec.n == 3
    assert rec.meta["venue"] == "fn_imola" and rec.meta["vehicle"] == "ks_cayman"
    assert rec.meta["session"] == "PRACTICE"
    assert rec.units["Ground Speed"] == "km/h"
    assert rec.channels["Ground Speed"].tolist() == [100.5, 110.5, 120.0]
    assert rec.channels["Brake Pos"][2] == 50.5


def test_csv_without_sample_rate_or_time_is_an_error(tmp_path):
    p = tmp_path / "norate.csv"
    p.write_text(
        '"Ground Speed","a","b","c","d","e","f","g"\n"km/h","","","","","","",""\n"1","1","1","1","1","1","1","1"\n',
        encoding="utf-8",
    )
    with pytest.raises(RecordingError):
        load_csv(p)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_replay_follows_the_clock_and_dedupes(ld_file):
    clock = Clock()
    src = ReplaySource(ld_file, clock=clock)
    src.open()
    first = src.read()
    assert first.packet_id == 0 and first.source == "replay"
    assert src.read() is None  # same sample (20 Hz data, polled faster)
    clock.t += 0.051  # 1.02 samples later
    assert src.read().packet_id == 1
    clock.t += 10.0
    snap = src.read()
    assert snap.packet_id == 201
    ref = lap_channels(3)
    assert snap.speed_kmh == pytest.approx(ref["Ground Speed"][0][201], rel=1e-4)
    assert snap.gas == pytest.approx(ref["Throttle Pos"][0][201] / 100.0, abs=1e-4)
    assert snap.normalized_pos == pytest.approx(ref["Car Pos Norm"][0][201], abs=1e-5)
    assert snap.tyre_temp_inner[1] == pytest.approx(ref["Tire Temp Inner FR"][0][201], rel=1e-4)
    assert snap.tyre_pressure == pytest.approx((26.5,) * 4)
    assert snap.static.car_model == "ks_cayman" and snap.static.track == "spa"
    assert snap.static.max_fuel == 100.0 and snap.status_name == "live"


def test_replay_speed_start_loop_and_end(ld_file):
    clock = Clock()
    fast = ReplaySource(ld_file, speed=4.0, start_s=100.0, clock=clock)
    fast.open()
    assert fast.read().packet_id == 2000
    clock.t += 1.0
    assert fast.read().packet_id == 2080  # 4 s of recording per wall second
    clock.t += 30.0
    with pytest.raises(SourceExhausted):
        fast.read()
    looping = ReplaySource(ld_file, speed=1.0, start_s=179.0, loop=True, clock=clock)
    looping.open()
    clock.t += 2.0
    assert looping.read().packet_id == 20  # wrapped around
    for bad in (0, -1):
        with pytest.raises(ValueError):
            ReplaySource(ld_file, speed=bad)


def test_replay_missing_file_and_unmapped_channels(tmp_path):
    with pytest.raises(SourceUnavailable):
        ReplaySource(tmp_path / "nope.ld").open()
    rec = Recording({"Ground Speed": np.arange(10.0)}, 20.0, {"vehicle": "v"})
    src = ReplaySource(rec)
    src.open()
    assert "Throttle Pos" in src.unmapped_fields and "Ground Speed" not in src.unmapped_fields
    assert src.read().gas == 0.0  # missing channels stay at zero


@pytest.mark.real_data
@pytest.mark.skipif(not os.environ.get("PIT_WALL_SAMPLE_LD"), reason="set PIT_WALL_SAMPLE_LD")
def test_real_acti_log_maps_every_used_channel():
    src = ReplaySource(os.environ["PIT_WALL_SAMPLE_LD"], start_s=60.0)
    src.open()
    assert src.unmapped_fields == []
    s = src.read()
    assert 0.0 <= s.normalized_pos <= 1.0 and s.speed_kmh >= 0.0
