import pytest

from pit_wall_ai.analysis.laps import LapRecord
from pit_wall_ai.analysis.plan import (
    FuelProfile,
    fuel_profile,
    fuel_to_finish,
    laps_in_tank,
    plan_stops,
    race_laps,
    saving_per_lap,
)


def lap(n, fuel, *, out=False, ms=80000):
    return LapRecord(
        number=n, time_ms=ms, fuel_start=20.0, fuel_end=20.0 - fuel, out_lap=out, coverage=1.0
    )


def test_the_profile_weights_the_recent_laps_and_skips_out_laps():
    p = fuel_profile([lap(1, 3.0, out=True), lap(2, 1.4), lap(3, 1.4), lap(4, 1.2)])
    assert p.laps == 3 and 1.29 < p.per_lap_l < 1.31  # (1.4*1 + 1.4*2 + 1.2*3) / 6
    assert p.spread_l > 0
    assert fuel_profile([lap(2, 1.4)]) is None  # one lap is not a consumption


def test_race_length_by_laps_or_by_time_with_the_extra_lap():
    assert race_laps(laps=8) == 8
    assert (
        race_laps(minutes=10, lap_time_s=78.0) == 9
    )  # 600 / 78 = 7.7 -> 8 laps, plus the extra lap
    assert race_laps(minutes=10, lap_time_s=78.0, extra_lap=False) == 8
    assert race_laps(minutes=10) is None


def test_fuel_to_finish_has_the_laps_the_margin_and_the_assumptions():
    prof = FuelProfile(1.35, 0.04, 6)
    plan = fuel_to_finish(prof, 8)
    # 8 x 1.35 = 10.8, plus a third of a lap (0.45) plus the spread (0.04 x sqrt(8) = 0.11)
    assert plan.litres == pytest.approx(11.4, abs=0.05) and plan.margin_l == pytest.approx(
        0.6, abs=0.05
    )
    assert "1.35 L per lap, from 6 laps" in plan.assumptions
    done = fuel_to_finish(prof, 8, laps_done=5)  # only the laps still to drive count
    assert done.litres < plan.litres / 2 + 1


def test_the_saving_per_lap_is_positive_when_short_and_negative_when_there_is_room():
    prof = FuelProfile(1.35, 0.04, 6)
    assert saving_per_lap(fuel_now=10.0, laps_left=8, profile=prof) == pytest.approx(0.1, abs=0.01)
    assert saving_per_lap(fuel_now=14.0, laps_left=8, profile=prof) < 0
    assert saving_per_lap(5.0, 0, prof) == 0.0


def test_a_tank_gives_so_many_laps():
    assert laps_in_tank(40.0, FuelProfile(1.35, 0.0, 5)) == 29


def test_one_stop_plan_splits_evenly_inside_the_window():
    prof = FuelProfile(2.0, 0.05, 6)
    plan = plan_stops(prof, 20, 25.0, stops=1, window=(8, 12))
    assert plan.feasible and plan.stop_laps == [10] and [s.laps for s in plan.stints] == [10, 10]
    assert plan.stints[0].ends_with_stop and not plan.stints[1].ends_with_stop
    assert plan.stints[0].fuel_start_l == pytest.approx(20.7, abs=0.1)


def test_a_plan_that_cannot_work_says_why():
    prof = FuelProfile(2.0, 0.05, 6)
    no_stop = plan_stops(prof, 20, 25.0, stops=0)
    assert not no_stop.feasible and "stop(s) are needed" in no_stop.reason
    late = plan_stops(prof, 20, 25.0, stops=1, window=(2, 4))
    assert not late.feasible and "outside the window" in late.reason
    assert not plan_stops(prof, 20, 1.0, stops=1).feasible
    free = plan_stops(prof, 20, 25.0, stops=0, window=None)
    assert not free.feasible
