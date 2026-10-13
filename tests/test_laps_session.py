"""Lap tracker, sector detection, best/reference lap, live delta, session detector and the engine thread."""

from __future__ import annotations

import time

import pytest

from pit_wall_ai.analysis.laps import LapTracker
from pit_wall_ai.events import (
    LapCompleted,
    PhaseChanged,
    PitEntered,
    PitExited,
    SectorCompleted,
    SessionChanged,
)
from pit_wall_ai.session.detector import SessionDetector
from pit_wall_ai.session.engine import SessionEngine
from pit_wall_ai.telemetry.poller import TelemetryPoller

from .helpers import ScriptedSource
from .scenario import make_session


def run_tracker(snaps, tracker=None):
    tr = tracker or LapTracker()
    events = []
    for s in snaps:
        events += tr.update(s)
    return tr, events


def laps_of(events):
    return [e for e in events if isinstance(e, LapCompleted)]


FLYING = [90.0, 88.5, 91.2, 87.9]


def test_laps_are_closed_with_the_games_times_and_the_out_lap_is_excluded():
    snaps = make_session([20.0] + FLYING)
    tr, events = run_tracker(snaps)
    laps = laps_of(events)
    assert [e.lap.number for e in laps] == [1, 2, 3, 4, 5]
    assert [e.lap.time_ms for e in laps[1:]] == [90000, 88500, 91200, 87900]
    assert all(e.lap.time_source == "game" for e in laps)
    assert laps[0].lap.out_lap and not any(e.lap.out_lap for e in laps[1:])
    assert tr.best.number == 5 and tr.best.time_ms == 87900  # the out lap never counts
    assert [e.is_best for e in laps] == [False, True, True, False, True]
    assert laps[2].delta_to_best_ms == 88500 - 90000  # vs the best before it
    assert laps[3].delta_to_best_ms == 91200 - 88500


def test_sectors_are_detected_and_add_up_to_the_lap_time():
    tr, events = run_tracker(make_session([20.0] + FLYING))
    sec = [e for e in events if isinstance(e, SectorCompleted)]
    assert {e.sector for e in sec} == {1, 2}
    flying = [e.lap for e in laps_of(events)][1:]
    for lap in flying:
        assert len(lap.sectors_ms) == 3
        assert sum(lap.sectors_ms) == lap.time_ms
        assert lap.sectors_ms[0] == pytest.approx(0.30 * lap.time_ms, abs=2)
    assert tr.best_sectors_ms[1] == pytest.approx(0.30 * 87900, abs=2)
    first_of_lap2 = [e for e in sec if e.lap_number == 2]
    assert [e.is_best for e in first_of_lap2] == [True, True]  # the first lap sets the sector bests


def test_sectors_still_found_from_the_sector_time_alone():
    tr, events = run_tracker(make_session([20.0] + FLYING, sector_index=False))
    assert all(len(e.lap.sectors_ms) == 3 for e in laps_of(events)[1:])


def test_the_games_lap_time_can_arrive_a_few_samples_late():
    snaps = make_session([20.0] + FLYING, confirm_delay_samples=10)  # 0.5 s later
    _, events = run_tracker(snaps)
    laps = laps_of(events)
    assert [e.lap.time_ms for e in laps[1:]] == [90000, 88500, 91200, 87900]
    assert all(e.lap.time_source == "game" for e in laps)


def test_without_a_game_time_the_lap_timer_is_used_and_flagged_estimated():
    _, events = run_tracker(make_session([20.0] + FLYING, report_last_time=False))
    laps = laps_of(events)[1:]
    assert all(e.lap.time_source == "estimated" for e in laps)
    for e, expected in zip(laps, [90000, 88500, 91200, 87900], strict=True):
        assert abs(e.lap.time_ms - expected) <= 60  # one 20 Hz sample of error
    assert all("estimated_time" in e.notes for e in laps)


def test_position_wrap_closes_laps_when_the_lap_counter_is_not_reported():
    tr, events = run_tracker(
        make_session([20.0] + FLYING, report_completed_laps=False, report_last_time=False)
    )
    laps = laps_of(events)
    assert len(laps) >= 4  # the first wrap needs the grace time
    flying = [e.lap for e in laps if not e.lap.out_lap]
    assert all(e.time_source == "estimated" for e in flying)
    assert sorted(x.time_ms for x in flying)[0] == pytest.approx(87900, abs=100)
    assert tr._wrap_mode


def test_an_in_lap_and_a_suspect_lap_never_become_the_best():
    snaps = make_session([20.0, 90.0, 80.0, 70.0], pit_lane_at_end=True, offtrack_laps=(2,))
    tr, events = run_tracker(snaps)
    laps = {e.lap.number: e.lap for e in laps_of(events)}
    assert laps[3].suspect and not laps[3].eligible_for_best  # fastest-but-cut
    assert laps[4].in_lap and not laps[4].eligible_for_best  # crossed in the pit lane
    assert tr.best.number == 2 and tr.best.time_ms == 90000


def test_live_delta_against_the_reference_lap():
    tracker = LapTracker()
    snaps = make_session([20.0, 90.0, 93.0])
    deltas = []
    for s in snaps:
        tracker.update(s)
        c = tracker.current
        if c and c.number == 3 and c.delta_ms is not None and 0.45 < s.normalized_pos < 0.55:
            deltas.append(c.delta_ms)
    assert deltas and all(
        c == pytest.approx(1500, abs=150) for c in deltas
    )  # 3 s slower over the lap
    assert tracker.reference.number == 2
    tracker.set_reference(None)
    assert tracker.reference.number == 2


def test_no_delta_on_the_out_lap_or_without_a_reference():
    tracker = LapTracker()
    for s in make_session([20.0, 90.0]):
        tracker.update(s)
        if tracker.current and tracker.current.number == 1:
            assert tracker.current.delta_ms is None and tracker.current.out_lap


def test_reference_can_be_pinned_to_another_lap():
    tracker = LapTracker()
    for s in make_session([20.0, 90.0, 85.0, 95.0]):
        tracker.update(s)
    assert tracker.best.number == 3
    tracker.set_reference(2)
    assert tracker.reference.number == 2
    tracker.set_reference(99)  # unknown lap: falls back to best
    assert tracker.reference.number == 3


def test_fuel_use_and_speed_stats_are_recorded_per_lap():
    _, events = run_tracker(make_session([20.0, 90.0, 90.0]))
    lap = laps_of(events)[1].lap
    assert lap.fuel_used == pytest.approx(0.001 * 90 * 20, rel=0.1)
    assert lap.max_speed_kmh == 150.0 and lap.avg_speed_kmh == pytest.approx(150.0)
    assert lap.coverage > 0.95 and lap.trace is not None


# ── session detector ─────────────────────────────────────────────────────────────────────────────────
def test_detector_reports_kind_phases_and_pit_events():
    det = SessionDetector()
    events = []
    phases = []
    for s in make_session([10.0, 20.0, 20.0], pit_wait_s=3.0, session=0):
        events += det.update(s)
        if not phases or phases[-1] != det.state.phase:
            phases.append(det.state.phase)
    assert phases[0] == "pits" and "out_lap" in phases and phases[-1] == "flying"
    assert phases.index("out_lap") < phases.index("flying")
    first = events[0]
    assert (
        isinstance(first, SessionChanged)
        and first.reason == "first_sample"
        and first.session == "practice"
    )
    assert any(isinstance(e, PitExited) for e in events)
    assert det.state.kind_verified_live and det.state.track == "hockenheim"


def test_detector_non_practice_kinds_are_flagged_as_not_verified_live():
    det = SessionDetector()
    for s in make_session([10.0, 20.0], session=3)[:5]:
        det.update(s)
    assert det.state.session == "hotlap" and not det.state.kind_verified_live


def test_detector_stopped_menu_and_pause_phases():
    from dataclasses import replace

    base = make_session([10.0, 20.0, 20.0], pit_wait_s=1.0)
    det = SessionDetector()
    for s in base[:60]:
        det.update(s)
    on_track = base[60]
    for k in range(80):  # standing still for 4 s
        det.update(
            replace(
                on_track,
                t=on_track.t + k * 0.05,
                speed_kmh=0.0,
                is_in_pit=False,
                is_in_pit_lane=False,
            )
        )
    assert det.state.phase == "stopped"
    det.update(replace(on_track, t=on_track.t + 9, status=3))
    assert det.state.phase == "paused"
    det.update(replace(on_track, t=on_track.t + 10, status=0))
    assert det.state.phase == "menu"


@pytest.mark.parametrize(
    "change,reason",
    [({"track": "monza"}, "track"), ({"car": "other_car"}, "car"), ({"session": 2}, "kind")],
)
def test_detector_new_session_when_track_car_or_kind_change(change, reason):
    det = SessionDetector()
    a = make_session([10.0, 20.0], pit_wait_s=1.0)[:30]
    b = make_session([10.0, 20.0], pit_wait_s=1.0, t0=2000.0, **change)[:10]
    events = []
    for s in a + b:
        events += det.update(s)
    changes = [e for e in events if isinstance(e, SessionChanged)]
    assert [e.reason for e in changes] == ["first_sample", reason]


def test_detector_new_session_when_the_session_clock_jumps_up_or_laps_reset():
    det = SessionDetector()
    snaps = make_session([10.0, 20.0, 20.0], pit_wait_s=1.0)
    events = []
    for s in snaps[:200]:
        events += det.update(s)
    from dataclasses import replace

    events += det.update(replace(snaps[200], session_time_left=2_400_000.0 + 60_000))
    assert [e.reason for e in events if isinstance(e, SessionChanged)][-1] == "time_reset"
    events = []
    events += det.update(replace(snaps[201], completed_laps=0, session_time_left=2_399_000.0))
    assert [e.reason for e in events if isinstance(e, SessionChanged)] == ["laps_reset"]


# ── engine ───────────────────────────────────────────────────────────────────────────────────────────
def test_engine_resets_the_tracker_on_a_new_session_and_keeps_the_best_within_one():
    eng = SessionEngine()
    for s in make_session([20.0, 90.0, 88.0], track="hockenheim"):
        eng.process(s)
    v = eng.view()
    assert v.best.time_ms == 88000 and len(v.laps) == 3 and v.state.track == "hockenheim"
    for s in make_session([20.0, 95.0], track="monza", t0=5000.0)[:40]:
        eng.process(s)
    v = eng.view()
    assert v.state.track == "monza" and v.laps == () and v.best is None


def test_engine_listeners_get_events_and_a_broken_listener_does_not_stop_the_rest():
    eng = SessionEngine()
    seen = []

    def boom(_e):
        raise RuntimeError("listener bug")

    eng.on_event(boom)
    eng.on_event(seen.append)
    for s in make_session([20.0, 90.0]):
        eng.process(s)
    assert any(isinstance(e, LapCompleted) for e in seen)
    assert any(isinstance(e, (PitExited, PitEntered, PhaseChanged)) for e in seen)


def test_engine_thread_consumes_the_poller_end_to_end():
    snaps = make_session([20.0] + FLYING)
    poller = TelemetryPoller(ScriptedSource(snaps), hz=1_000_000)
    eng = SessionEngine()
    eng.attach(poller, queue_size=len(snaps) + 10)
    poller.start()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 5:
        if len(eng.view().laps) == 5:
            break
        time.sleep(0.01)
    poller.stop()
    eng.detach()
    v = eng.view()
    assert [lap.time_ms for lap in v.laps[1:]] == [90000, 88500, 91200, 87900]
    assert v.best.number == 5 and eng.errors == 0


def test_flush_closes_a_lap_that_is_only_waiting_for_the_games_time():
    snaps = make_session(
        [20.0, 90.0], confirm_delay_samples=10_000
    )  # the game's time never arrives
    tr = LapTracker()
    for s in snaps[: len(snaps) - 30]:  # a recording cut right after the line
        tr.update(s)
    before = len(tr.laps)
    events = tr.flush()
    assert len(tr.laps) >= before and tr.flush() == []
    assert all(e.lap.time_source == "estimated" for e in events)


def _ld_sample():
    import os

    return os.environ.get("PIT_WALL_SAMPLE_LD")


@pytest.mark.real_data
@pytest.mark.skipif(not _ld_sample(), reason="set PIT_WALL_SAMPLE_LD")
def test_real_acti_log_lap_times_equal_the_games_last_lap_times():
    from pit_wall_ai.telemetry.recording import load_recording
    from pit_wall_ai.telemetry.replay import snapshot_from_recording, static_from_recording

    rec = load_recording(_ld_sample())
    static = static_from_recording(rec)
    eng = SessionEngine()
    events = []
    for i in range(rec.n):
        events += eng.process(snapshot_from_recording(rec, i, static, t=i / rec.hz, session=0))
    events += eng.flush()
    last = rec.channels["Last Lap Time"]
    truth, prev = [], float(last[0])
    for v in last:
        if v > 0 and v != prev:
            truth.append(round(float(v) * 1000))
            prev = v
    got = [e.lap.time_ms for e in events if isinstance(e, LapCompleted)]
    assert truth and all(any(abs(t - g) <= 2 for g in got) for t in truth)
    assert len(got) == len(truth)  # nothing counted twice or invented
