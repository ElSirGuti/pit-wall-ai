"""A real session recorded with ``pit-wall record`` (Assetto Corsa 1.16.4, Mazda MX-5 Cup at Magione, 2026-10-12):
a qualifying (out lap + 2 timed laps), a race against AI with a pause and a RESTART, and the exit to the menu.

This is the first capture of the game across a start/finish line, so it is what the lap tracker and the
session detector are checked against. Every number below is what the game itself reported."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pit_wall_ai.events import LapCompleted, PhaseChanged, PitEntered, PitExited, SessionChanged
from pit_wall_ai.session.engine import SessionEngine
from pit_wall_ai.telemetry.recorder import load_native, snapshot_from_flat

FIXTURE = Path(__file__).parent / "fixtures" / "live_lfm_magione_session.pwrec.gz"


@pytest.fixture(scope="module")
def played():
    rec = load_native(FIXTURE)
    eng = SessionEngine()
    events, views = [], []
    for i, row in enumerate(rec.rows):
        events += eng.process(snapshot_from_flat(row, rec.statics[i], t=rec.times[i]))
        if i % 500 == 0:
            views.append(eng.view())
    events += eng.flush()
    return rec, eng, events, views


def test_the_recording_is_what_it_says_it_is(played):
    rec, *_ = played
    s = rec.statics[0]
    assert (s.car_model, s.track, s.sector_count, s.num_cars) == (
        "ks_mazda_mx5_cup",
        "magione",
        2,
        18,
    )
    assert s.player_name == "" and rec.n > 10_000 and 1300 < rec.duration_s < 1400


def test_session_codes_qualify_is_1_and_race_is_2(played):
    _, _, events, _ = played
    changes = [(e.reason, e.session) for e in events if isinstance(e, SessionChanged)]
    assert changes == [("first_sample", "qualify"), ("kind", "race"), ("restart", "race")]


def test_the_menu_at_the_end_is_not_reported_as_a_new_session(played):
    rec, eng, events, _ = played
    assert not any(isinstance(e, SessionChanged) and e.session == "practice" for e in events)
    last = eng.view()
    assert last.state.phase == "menu" and last.state.session == "race"


def test_lap_times_equal_the_games_own_times(played):
    _, _, events, _ = played
    laps = [e.lap for e in events if isinstance(e, LapCompleted)]
    assert [(lap.number, lap.time_ms) for lap in laps] == [
        (1, 137105),
        (2, 86932),
        (3, 86710),  # qualifying: out lap, two timed laps
        (1, 96324),
        (2, 85444),
        (3, 84913),  # race after the restart: numbered from 1 again
    ]
    assert all(lap.time_source == "game" for lap in laps)
    assert [lap.out_lap for lap in laps] == [True, False, False, False, False, False]
    assert not any(lap.in_lap or lap.suspect for lap in laps)


def test_a_two_sector_circuit_gives_two_sectors_that_add_up(played):
    _, _, events, _ = played
    for e in events:
        if isinstance(e, LapCompleted) and not e.lap.out_lap:
            assert len(e.lap.sectors_ms) == 2 and sum(e.lap.sectors_ms) == e.lap.time_ms


def test_best_laps_are_per_session_and_the_restart_discards_the_old_race_attempt(played):
    _, _, events, _ = played
    laps = [e for e in events if isinstance(e, LapCompleted)]
    qualy, race = laps[:3], laps[3:]
    assert [e.is_best for e in qualy] == [False, True, True]
    assert [e.is_best for e in race] == [True, True, True]
    assert race[0].delta_to_best_ms is None  # nothing to compare with after the restart


def test_the_final_view_is_the_race(played):
    _, eng, *_ = played
    v = eng.view()
    assert v.best.time_ms == 84913 and [lap.time_ms for lap in v.laps] == [96324, 85444, 84913]


def test_phases_and_pit_events_follow_the_session(played):
    _, _, events, _ = played
    phases = [(e.old, e.new) for e in events if isinstance(e, PhaseChanged)]
    seen = {new for _, new in phases}
    assert {"out_lap", "flying", "paused", "pits", "stopped", "menu"} <= seen
    kinds = [e.kind for e in events if isinstance(e, (PitEntered, PitExited))]
    assert kinds[0] == "PitExited" and "PitEntered" in kinds


def test_a_race_clock_that_counts_up_is_elapsed_time_not_time_left(played):
    _, _, _, views = played
    race_views = [v for v in views if v.state.session == "race" and v.last_snapshot is not None]
    assert race_views
    elapsed = [v.state.session_elapsed_s for v in race_views if v.state.session_elapsed_s]
    assert elapsed and all(math.isfinite(x) and x > 0 for x in elapsed)
    assert all(v.state.session_time_left_s is None for v in race_views if v.state.session_elapsed_s)
    assert all(v.state.kind_verified_live for v in views)


def test_infinity_on_the_session_clock_marks_a_restart(played):
    rec, *_ = played
    inf_rows = [i for i, r in enumerate(rec.rows) if math.isinf(float(r["session_time_left"]))]
    assert len(inf_rows) >= 2  # race start and the restart
