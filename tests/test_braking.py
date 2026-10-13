"""Braking per corner and lap-vs-reference comparison, on synthetic laps with known braking points."""

from __future__ import annotations

import numpy as np
import pytest

from pit_wall_ai.analysis.braking import (
    comparable,
    compare_lap,
    corner_metrics,
    lap_corner_metrics,
    reference_lap,
    worst_corner,
)
from pit_wall_ai.analysis.laps import LapRecord, LapTrace
from pit_wall_ai.analysis.track_geometry import Corner, RacingLine, SubApex, TrackGeometry
from pit_wall_ai.config import BrakingConfig

LENGTH = 3000.0
CFG = BrakingConfig()


def corner(number, start, apex, end, *, subs=None) -> Corner:
    return Corner(
        number=number,
        start_fraction=start / LENGTH,
        apex_fraction=apex / LENGTH,
        end_fraction=end / LENGTH,
        start_m=start,
        apex_m=apex,
        end_m=end,
        min_radius_m=50.0,
        direction="right",
        severity="slow",
        kind="single",
        sub_apexes=[SubApex(s / LENGTH, s, 50.0, "right") for s in (subs or [apex])],
    )


def geometry(*corners: Corner) -> TrackGeometry:
    line = RacingLine(
        x=np.array([0.0, LENGTH]),
        z=np.zeros(2),
        s=np.array([0.0, LENGTH]),
        closed=True,
        closure_gap_m=0.0,
    )
    return TrackGeometry(
        line=line,
        corners=list(corners),
        step_m=2.0,
        distance_m=np.zeros(1),
        curvature=np.zeros(1),
    )


def lap(
    number,
    brakes: dict[float, float] | None = None,
    *,
    vmax=200.0,
    vmin=60.0,
    taps=(),
    compound="",
    out=False,
    suspect=False,
    held=(),
    length=LENGTH,
) -> LapRecord:
    """A lap on a 3000 m track. ``brakes`` maps an apex (m) to the braking point (m); the speed falls
    linearly to ``vmin`` at the apex and recovers over 120 m. ``taps`` are (start, end, level) pedal taps;
    ``held`` are (start, end) intervals with the pedal down (a braking that runs on)."""
    n = 4000
    d = np.linspace(0.0, length, n)
    speed = np.full(n, vmax)
    brake = np.zeros(n)
    gas = np.ones(n)
    steer = np.zeros(n)
    for apex, bp in (brakes or {}).items():
        down = (d >= bp) & (d <= apex)
        speed = np.where(down, vmax - (vmax - vmin) * (d - bp) / (apex - bp), speed)
        up = (d > apex) & (d <= apex + 120)
        speed = np.where(up, vmin + (vmax - vmin) * (d - apex) / 120.0, speed)
        brake[down] = 0.8
        gas[down] = 0.0
        steer[(d >= apex - 30) & (d <= apex + 30)] = 0.5
    for a, b, level in taps:
        brake[(d >= a) & (d <= b)] = level
    for a, b in held:
        m = (d >= a) & (d <= b)
        brake[m] = np.maximum(brake[m], 0.4)
    dt = np.diff(d, prepend=0.0) / (np.maximum(speed, 5.0) / 3.6)
    t_ms = np.cumsum(dt) * 1000.0
    trace = LapTrace(d / length, t_ms, speed, gas, brake, steer)
    return LapRecord(
        number=number,
        time_ms=int(t_ms[-1]),
        trace=trace,
        coverage=1.0,
        out_lap=out,
        suspect=suspect,
        compound=compound,
    )


G = geometry(corner(1, 480, 500, 540), corner(2, 1480, 1500, 1560), corner(3, 2380, 2400, 2460))


def test_braking_point_peak_and_slowest_point_are_measured():
    m = corner_metrics(lap(1, {500: 400}).trace, LENGTH, G.corners[0], 0.0, CFG)
    assert m.braked and not m.clipped
    assert m.brake_point_m == pytest.approx(400, abs=1.5)
    assert m.peak_brake == pytest.approx(0.8)
    assert m.entry_speed_kmh == pytest.approx(200, abs=2)
    assert m.min_speed_kmh == pytest.approx(60, abs=3)
    assert m.min_speed_m == pytest.approx(500, abs=30)
    assert m.throttle_on_m is not None and m.throttle_on_m > 500
    assert m.trail_m is not None and m.trail_m >= 0


def test_a_corner_taken_without_braking_has_no_braking_point_but_has_a_time():
    flat = lap(1, {})
    m = corner_metrics(flat.trace, LENGTH, G.corners[1], 540.0, CFG)
    assert not m.braked and m.brake_point_m is None
    assert m.min_speed_kmh == pytest.approx(200, abs=1)


def test_a_light_touch_below_the_peak_is_not_the_braking_of_the_corner():
    touch = lap(1, {}, taps=[(1400, 1420, 0.2)])  # 0.2 < min_peak 0.3
    assert not corner_metrics(touch.trace, LENGTH, G.corners[1], 540.0, CFG).braked


def test_events_close_together_are_one_braking():
    two = lap(1, {1500: 1400}, taps=[(1380, 1390, 0.8)])  # 10 m gap < merge_gap 20 m
    m = corner_metrics(two.trace, LENGTH, G.corners[1], 540.0, CFG)
    assert m.brake_point_m == pytest.approx(1380, abs=2)


def test_comparison_reports_the_braking_point_difference_and_the_time_over_the_whole_corner():
    ref = lap(1, {500: 400, 1500: 1400, 2400: 2300})
    mine = lap(2, {500: 385, 1500: 1400, 2400: 2300})  # 15 m earlier at corner 1 only
    comp = {c.corner: c for c in compare_lap(G, mine, ref, CFG)}
    c1 = comp[1]
    assert c1.brake_earlier_m == pytest.approx(15.0, abs=2.0)
    assert c1.time_diff_s is not None and c1.time_diff_s > 0.1
    assert c1.min_speed_delta_kmh == pytest.approx(0.0, abs=3.0)
    assert c1.significant(CFG)
    assert comp[2].brake_earlier_m == pytest.approx(0.0, abs=2.0) and not comp[2].significant(CFG)
    # the span starts 10 m before the REFERENCE braking point and ends where the corner ends
    assert c1.span_m == pytest.approx((390.0, 540.0), abs=2.0)


def test_later_braking_is_negative_and_never_double_counted():
    ref = lap(1, {500: 400})
    mine = lap(2, {500: 415})
    c = compare_lap(G, mine, ref, CFG)[0]
    assert c.brake_earlier_m == pytest.approx(-15.0, abs=2.0)


def test_thresholds_are_minimums_both_must_be_reached():
    ref = lap(1, {500: 400})
    near = compare_lap(G, lap(2, {500: 394}), ref, CFG)[0]  # 6 m: under 10 m
    assert not near.significant(CFG)
    far = compare_lap(G, lap(3, {500: 380}), ref, CFG)[0]  # 20 m
    assert far.significant(CFG)
    strict = BrakingConfig(min_time_s=5.0)
    assert not far.significant(strict)  # the time loss is not enough on its own
    assert not compare_lap(G, lap(4, {500: 400}), ref, CFG)[0].significant(CFG)  # identical


def test_a_lap_is_never_compared_with_itself():
    a = lap(1, {500: 400})
    assert compare_lap(G, a, a, CFG) == []


def test_braking_that_runs_into_the_corner_from_the_previous_one_has_no_distance():
    ref = lap(1, {1500: 1400})
    # the pedal stays down from the end of corner 1 (540) to corner 2: the search window begins with it
    mine = lap(2, {1500: 1400}, held=[(520, 1400)])
    c = {x.corner: x for x in compare_lap(G, mine, ref, CFG)}[2]
    assert c.lap_metrics.clipped
    assert c.brake_earlier_m is None
    assert c.time_diff_s is not None  # the time is still measured


def test_a_complex_uses_its_first_apex_for_the_braking_point_not_the_last_tap():
    chic = corner(1, 700, 760, 900, subs=[720, 760])
    g = geometry(chic)
    # main braking at 600 for the first element, a second tap at 735 for the second one
    one = lap(1, {720: 600}, taps=[(735, 750, 0.5)])
    m = corner_metrics(one.trace, LENGTH, g.corners[0], 0.0, CFG)
    assert m.brake_point_m == pytest.approx(600, abs=2)


def test_a_corner_that_crosses_the_start_line_is_skipped():
    wrapped = corner(9, 2900, 20, 80)  # start > end: it wraps
    g = geometry(wrapped, corner(1, 480, 500, 540))
    got = lap_corner_metrics(g, lap(1, {500: 400}), CFG)
    assert set(got) == {1}


# -- which lap is the reference -------------------------------------------------------------------------
def with_time(rec: LapRecord, ms: int) -> LapRecord:
    rec.time_ms = ms
    return rec


def test_reference_is_the_best_valid_lap_of_the_same_compound():
    laps = [
        with_time(lap(1, {500: 400}, compound="soft"), 90_000),
        with_time(lap(2, {500: 400}, compound="soft"), 89_000),
        with_time(lap(3, {500: 400}, compound="medium"), 88_000),
        with_time(lap(4, {500: 400}, compound="soft", suspect=True), 87_000),
        with_time(lap(5, {500: 400}, compound="soft", out=True), 86_000),
    ]
    assert reference_lap(laps, laps[0]).number == 2
    assert reference_lap(laps, laps[1]) is None  # it is the best soft lap itself
    assert reference_lap(laps, laps[2]) is None  # the only medium lap
    # a faster lap that is not valid is still compared with the best valid one
    assert reference_lap(laps, laps[3]).number == 2
    assert [comparable(x) for x in laps] == [True, True, True, True, False]


def test_worst_corner_is_the_significant_one_that_cost_the_most():
    ref = lap(1, {500: 400, 1500: 1400, 2400: 2300})
    mine = lap(2, {500: 385, 1500: 1370, 2400: 2300})  # 15 m and 30 m earlier
    comps = compare_lap(G, mine, ref, CFG)
    w = worst_corner(comps, CFG)
    assert w is not None and w.corner == 2
    assert worst_corner(compare_lap(G, lap(3, {500: 400}), ref, CFG), CFG) is None


# -- offline analysis of a real recording ---------------------------------------------------------------
def test_offline_analysis_of_the_real_race_window_yields_laps_with_fine_traces():
    from pathlib import Path

    from pit_wall_ai.analysis.laps import TRACE_BINS
    from pit_wall_ai.analysis.offline import analyse_file

    path = Path(__file__).parent / "fixtures" / "live_lfm_magione_race_window.pwrec.gz"
    r = analyse_file(path)
    assert r.static.track == "magione" and r.samples > 4000
    timed = [x for x in r.laps if x.eligible_for_best]
    assert len(timed) >= 2
    assert all(x.trace is not None and len(x.trace.pos) == TRACE_BINS for x in timed)
    assert all(x.compound == timed[0].compound for x in timed)


# -- the spoken hint, through the real rule ---------------------------------------------------------------
def test_the_braking_hint_rule_speaks_the_measured_fact_once_per_lap(tmp_path, caplog):
    import logging

    from pit_wall_ai.analysis import tracks as tr
    from pit_wall_ai.analysis.laps import LapTracker
    from pit_wall_ai.config import AnalysisConfig, EngineerConfig
    from pit_wall_ai.engineer.engine import Engineer
    from pit_wall_ai.events import LapCompleted
    from pit_wall_ai.telemetry.snapshot import StaticInfo

    from .helpers import snap
    from .test_track_geometry import put_line

    libs = tmp_path / "tracks"
    put_line(libs, "stad")
    res = tr.resolve("stad", "", length_m=1428.0, dirs=[libs])
    assert res.ok
    L = res.geometry.length_m
    c1, c2 = res.geometry.corners
    ref = lap(1, {c1.apex_m: c1.apex_m - 90, c2.apex_m: c2.apex_m - 90}, length=L)
    mine = lap(2, {c1.apex_m: c1.apex_m - 105, c2.apex_m: c2.apex_m - 90}, length=L)  # 15 m earlier
    ref.time_ms, mine.time_ms = 80_000, 81_000
    tracker = LapTracker()
    tracker.laps = [ref, mine]
    tracker.best = ref

    said = []

    class Spk:
        busy = False

        def say(self, text, *, interrupt=False, meta=None):
            said.append((text, (meta or {}).get("rule")))

    static = StaticInfo(track="stad", track_configuration="", track_length_m=1428.0)
    cfg = EngineerConfig(startup_quiet_s=0)
    analysis = AnalysisConfig(ac_dirs=[str(libs)])
    eng = Engineer(cfg, Spk(), analysis=analysis)
    from pit_wall_ai.session.detector import SessionState

    state = SessionState(session="practice", status="live", phase="flying")
    ev = [LapCompleted(0.0, mine, False, 1000)]
    with caplog.at_level(logging.INFO, logger="pit_wall_ai.engineer.alerts"):
        fired = eng.on_sample(
            snap(t=0.0, static=static, brake=0.0, steer_angle=0.0), state, tracker, ev
        )
        for i in range(1, 60):  # then a calm stretch (lap time first, then the hint)
            eng.on_sample(
                snap(t=i * 0.1, static=static, brake=0.0, steer_angle=0.0), state, tracker, []
            )
    hints = [a for a in fired if a.rule == "braking_hint"]
    assert len(hints) == 1
    a = hints[0]
    assert a.values["corner"] == 1 and a.values["brake_earlier_m"] == pytest.approx(15.0, abs=2.5)
    assert a.text.startswith(
        "Curva de derechas a "
    )  # numbering not verified: it describes the stretch
    assert "frenas 15 metros antes que tu mejor vuelta" in a.text or "frenas 1" in a.text
    assert "pierdes" in a.text and "porque" not in a.text  # the fact, no cause
    assert [r for _, r in said].count("braking_hint") == 1
    # verified numbering: the number is said
    analysis2 = AnalysisConfig(ac_dirs=[str(libs)], verified_numbering=["stad"])
    eng2 = Engineer(cfg, Spk(), analysis=analysis2)
    fired2 = eng2.on_sample(snap(t=0.0, static=static), state, tracker, ev)
    assert [x for x in fired2 if x.rule == "braking_hint"][0].text.startswith("Curva 1:")


def test_the_braking_hint_is_silent_when_the_track_is_not_installed(tmp_path, caplog):
    import logging

    from pit_wall_ai.analysis.laps import LapTracker
    from pit_wall_ai.config import AnalysisConfig, EngineerConfig
    from pit_wall_ai.engineer.rules import RuleContext, build_rules
    from pit_wall_ai.events import LapCompleted
    from pit_wall_ai.session.detector import SessionState
    from pit_wall_ai.telemetry.snapshot import StaticInfo

    from .helpers import snap

    ref, mine = lap(1, {500: 400}), lap(2, {500: 380})
    ref.time_ms, mine.time_ms = 80_000, 81_000
    tracker = LapTracker()
    tracker.laps = [ref, mine]
    (tmp_path / "tracks").mkdir()
    rule = next(
        r
        for r in build_rules(EngineerConfig(), AnalysisConfig(ac_dirs=[str(tmp_path / "tracks")]))
        if r.id == "braking_hint"
    )
    ctx = RuleContext(
        snap(t=0.0, static=StaticInfo(track="ghost", track_length_m=3000.0)),
        SessionState(session="practice", status="live", phase="flying"),
        tracker,
        [LapCompleted(0.0, mine, False, 1000)],
        "es",
    )
    with caplog.at_level(logging.WARNING):
        assert rule.evaluate(ctx) == []
        assert rule.evaluate(ctx) == []  # still silent, and the reason is logged only once
    reasons = [r for r in caplog.records if "braking analysis off" in r.getMessage()]
    assert len(reasons) == 1 and "ghost" in reasons[0].reason


def test_hint_wording_in_both_languages():
    from pit_wall_ai.engineer import messages as M

    assert (
        M.braking_hint("es", where="Pouhon", metres=14.2, earlier=True, loss_s=0.24)
        == "Pouhon: frenas 14 metros antes que tu mejor vuelta, pierdes 2 décimas"
    )
    assert M.braking_hint("es", where="Curva 4", metres=-12.0, earlier=False, loss_s=1.34).endswith(
        "frenas 12 metros más tarde que tu mejor vuelta, pierdes 1 coma 3 segundos"
    )
    assert M.braking_hint("en", where="Pouhon", metres=14, earlier=True, loss_s=0.1) == (
        "Pouhon: you brake 14 meters earlier than your best lap, losing 1 tenth"
    )
    assert M.where_phrase("es", name=None, number=None, direction="left", at_m=1734) == (
        "Curva de izquierdas a 1730 metros de meta"
    )
    assert M.where_phrase("es", name="Pouhon", number=6, direction="left", at_m=3850) == "Pouhon"
