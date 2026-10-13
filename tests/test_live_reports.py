"""Unit checks against a REAL capture (Assetto Corsa 1.16.4, LFM practice at Hockenheim, 2026-10-12).

The fixtures are the JSON reports written by ``pit-wall dump-shm --watch``: the last sample and the
min/max/mean of every field over 20 s standing in the pit and 60 s driving. The checks below are
independent physical relationships inside the capture, so a unit is confirmed without trusting the HUD or a
document. They also guard the structure layout: a shifted field would break them.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
WHEELS = ("FL", "FR", "RL", "RR")


@pytest.fixture(scope="module")
def pit():
    return json.loads((FIX / "live_ac_hockenheim_pit.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def track():
    return json.loads((FIX / "live_ac_hockenheim_track.json").read_text(encoding="utf-8"))


def norm(sample, prefix):
    return math.sqrt(sum(sample[f"{prefix}.{a}"] ** 2 for a in "xyz"))


def test_speed_is_kmh_and_velocities_are_ms(track):
    s = track["last_sample"]
    assert s["speed_kmh"] == pytest.approx(norm(s, "local_velocity") * 3.6, rel=1e-5)
    assert s["speed_kmh"] == pytest.approx(norm(s, "velocity") * 3.6, rel=1e-5)
    lv = {a: abs(s[f"local_velocity.{a}"]) for a in "xyz"}
    assert lv["z"] > 10 * lv["x"] and lv["z"] > 10 * lv["y"]  # z is the forward axis


def test_wheel_speed_is_rad_s_and_tyre_radius_is_metres(track):
    s = track["last_sample"]
    kmh = s["speed_kmh"]
    for w in ("FL", "FR"):  # the undriven axle follows the road
        wheel_kmh = s[f"wheel_angular_speed.{w}"] * s[f"static.tyre_radius.{w}"] * 3.6
        assert wheel_kmh == pytest.approx(kmh, rel=0.03)
    rear = s["wheel_angular_speed.RL"] * s["static.tyre_radius.RL"] * 3.6
    assert rear > kmh  # driven wheels spin faster on throttle
    assert 0.2 < s["static.tyre_radius.FL"] < 0.5


def test_times_are_milliseconds_and_distance_is_metres(pit, track):
    for rep, seconds in ((pit, 20.0), (track, 60.0)):
        r = rep["ranges"]
        assert r["session_time_left"]["max"] - r["session_time_left"]["min"] == pytest.approx(
            seconds * 1000, rel=0.01
        )
        assert r["current_time_ms"]["max"] - r["current_time_ms"]["min"] == pytest.approx(
            seconds * 1000, rel=0.01
        )
    r = track["ranges"]
    driven = r["distance_traveled"]["max"] - r["distance_traveled"]["min"]
    assert driven == pytest.approx(r["speed_kmh"]["mean"] / 3.6 * 60.0, rel=0.01)


def test_samples_arrive_at_the_polling_rate(pit, track):
    assert pit["samples"] == pytest.approx(60 * 20, rel=0.05)
    assert track["samples"] == pytest.approx(60 * 60, rel=0.05)


def test_pedals_are_0_to_1_and_heading_is_radians(track):
    r = track["ranges"]
    for f in ("gas", "brake"):
        assert r[f]["min"] >= 0.0 and r[f]["max"] <= 1.0 and r[f]["max"] == 1.0
    assert -math.pi - 0.01 <= r["heading"]["min"] and r["heading"]["max"] <= math.pi + 0.01
    assert r["heading"]["max"] - r["heading"]["min"] > 6.0  # the lap turned through a full circle
    assert 0.0 <= r["normalized_pos"]["min"] <= r["normalized_pos"]["max"] <= 1.0


def test_acceleration_is_in_g_with_longitudinal_on_z(track):
    r = track["ranges"]
    assert r["acc_g.x"]["max"] > 1.0 and r["acc_g.x"]["min"] < -1.0  # lateral, both directions
    assert r["acc_g.z"]["min"] < -1.5  # braking is the strongest
    assert r["acc_g.z"]["min"] < -r["acc_g.z"]["max"]  # ... stronger than acceleration
    assert abs(r["acc_g.y"]["mean"]) < 0.2  # vertical: gravity is excluded


def test_wheel_load_is_newtons(pit):
    total = sum(pit["last_sample"][f"wheel_load.{w}"] for w in WHEELS)
    kg = total / 9.81
    assert 1100 < kg < 1900  # a GT4 car with fuel and driver


def test_fuel_is_litres(pit, track):
    assert pit["last_sample"]["fuel"] == pytest.approx(45.0)
    assert pit["last_sample"]["static.max_fuel"] == pytest.approx(110.0)
    r = track["ranges"]
    used = r["fuel"]["max"] - r["fuel"]["min"]
    km = (r["distance_traveled"]["max"] - r["distance_traveled"]["min"]) / 1000.0
    assert 0.2 < used / km < 2.0  # litres per km, GT4-like


def test_tyre_pressures_and_temperatures_are_in_a_sane_range(pit, track):
    for w in WHEELS:
        p = track["ranges"][f"tyre_pressure.{w}"]
        assert p["min"] > 20.0 and p["max"] < 40.0  # psi-like (a bar value would be ~2)
        t = track["ranges"][f"tyre_core_temp.{w}"]
        assert t["min"] > 30.0 and t["max"] < 150.0  # degrees C
    assert pit["last_sample"]["tyre_wear.FL"] == 100.0  # new tyres read 100


def test_known_unreliable_fields_are_flagged_in_the_capture(track):
    """Documented as unusable: these checks fail loudly if a game update fixes them (then update the docs)."""
    r = track["ranges"]
    assert (
        r["brake_temp.FL"]["min"] == r["brake_temp.FL"]["max"] == 15.0
    )  # never heats, 244 km/h and braking
    assert r["brake"]["max"] == 1.0
    s = track["last_sample"]
    assert s["static.max_torque"] < 400.0  # the Supra GT4 makes far more


@pytest.fixture(scope="module")
def lap2():
    return json.loads((FIX / "live_ac_hockenheim_lap2.json").read_text(encoding="utf-8"))


def test_gear_range_matches_the_drivers_hud(track, lap2):
    """The driver reported, from the HUD: raw 7 = 6th gear and raw 1 = neutral (gear number = raw - 1)."""
    for rep in (track, lap2):
        assert rep["ranges"]["gear"]["max"] == 7 and rep["ranges"]["gear"]["min"] == 1
    # raw 1 appears at 131 km/h and 6651 rpm: a shift gap, not a stop
    s = lap2["last_sample"]
    assert s["gear"] == 1 and s["speed_kmh"] > 100 and s["rpm"] > 5000


def test_steer_angle_is_not_wheel_degrees_and_is_stable_between_laps(track, lap2):
    for rep in (track, lap2):
        r = rep["ranges"]["steer_angle"]
        assert -1.0 < r["min"] < 0.0 < r["max"] < 1.0  # far below any degree value
    assert lap2["ranges"]["steer_angle"]["max"] == pytest.approx(
        track["ranges"]["steer_angle"]["max"], abs=0.02
    )  # same corner, same peak


def test_clutch_is_constant_when_the_pedal_axis_is_inverted_in_the_controls(track, lap2):
    for rep in (track, lap2):
        assert rep["ranges"]["clutch"]["min"] == rep["ranges"]["clutch"]["max"] == 1.0


def test_steer_angle_is_a_fraction_of_the_lock_full_lock_reads_plus_minus_one():
    """Lock-to-lock test in the pit box (Alfa Romeo 4C): the value reaches exactly -1 and +1."""
    rep = json.loads((FIX / "live_ac_alfa4c_lock.json").read_text(encoding="utf-8"))
    r = rep["ranges"]["steer_angle"]
    assert r["min"] == pytest.approx(-1.0, abs=1e-4) and r["max"] == pytest.approx(1.0, abs=1e-4)
    assert rep["last_sample"]["speed_kmh"] < 1.0  # stationary: only the wheel moved
    assert rep["ranges"]["gear"]["min"] == rep["ranges"]["gear"]["max"] == 1  # neutral


def test_clutch_reads_zero_in_neutral_in_every_capture(pit):
    lock = json.loads((FIX / "live_ac_alfa4c_lock.json").read_text(encoding="utf-8"))
    for rep in (pit, lock):
        assert rep["ranges"]["gear"]["max"] == 1 and rep["ranges"]["clutch"]["max"] == 0.0


def test_one_full_wheel_turn_reads_a_linear_fraction_of_the_lock():
    """A 900-degree wheel (+-450) turned exactly once (360 deg) reads ~0.8: linear, not saturated."""
    rep = json.loads((FIX / "live_ac_alfa4c_turn.json").read_text(encoding="utf-8"))
    r = rep["ranges"]["steer_angle"]
    assert -1.0 < r["min"] < -0.6 and r["max"] < 0.1  # one direction only, below full lock
    degrees = abs(r["min"]) * 450.0
    assert degrees == pytest.approx(360.0, abs=30.0)  # one turn, hand-aligned
    assert rep["last_sample"]["speed_kmh"] < 1.0
