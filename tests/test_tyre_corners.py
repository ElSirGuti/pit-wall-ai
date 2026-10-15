"""Tyre load by corner: windows, phases, the validation gate, incident laps."""

from __future__ import annotations

import math

import pytest

from pit_wall_ai.analysis import tyre_corners as TC
from pit_wall_ai.analysis.track_geometry import Corner, SubApex
from pit_wall_ai.telemetry.snapshot import Snapshot


def corner(n, start, apex, end):
    return Corner(
        number=n,
        start_fraction=0,
        apex_fraction=0,
        end_fraction=0,
        start_m=start,
        apex_m=apex,
        end_m=end,
        min_radius_m=50,
        direction="left",
        severity="medium",
        kind="single",
        sub_apexes=[SubApex(fraction=0, distance_m=apex, radius_m=50, direction="left")],
    )


CORNERS = [corner(1, 100, 150, 200), corner(2, 400, 450, 500)]


def test_a_position_belongs_to_the_corner_whose_window_contains_it():
    assert TC.corner_of(150, CORNERS) == 1
    assert TC.corner_of(90, CORNERS) == 1  # the braking zone before the corner
    assert TC.corner_of(205, CORNERS) == 1  # the run-out
    assert TC.corner_of(300, CORNERS) is None
    assert TC.corner_of(430, CORNERS) == 2


def test_phases():
    assert TC.phase_of(0.5, 0.0, 0.0) == "brake"
    assert TC.phase_of(0.0, 0.8, 0.2) == "throttle"
    assert TC.phase_of(0.0, 0.1, 0.3) == "turn"
    assert TC.phase_of(0.0, 0.1, 0.0) is None


def lap(n, f, r, drop):
    return TC.LapLoad(n, {}, f, r, (drop,) * 4, 1000)


def test_validation_refuses_few_laps_and_a_flat_wear_channel():
    v = TC.validate([lap(i, 100, 100, 0.0) for i in range(8)])
    assert not v.ok and "barely wears" in v.reason
    v = TC.validate([lap(i, 100, 100, 0.5) for i in range(3)])
    assert not v.ok and "complete laps" in v.reason


def test_validation_accepts_a_proxy_that_follows_the_wear():
    laps = [lap(i, 100 + 10 * i, 100, 0.2 + 0.03 * i) for i in range(8)]
    v = TC.validate(laps)
    assert v.ok and v.r is not None and v.r > 0.99


def test_validation_rejects_a_proxy_that_does_not_follow_the_wear():
    loads = [100, 140, 90, 150, 95, 130, 105, 125]
    drops = [0.5, 0.2, 0.55, 0.25, 0.5, 0.3, 0.2, 0.45]
    v = TC.validate([lap(i, f, 0, d) for i, (f, d) in enumerate(zip(loads, drops, strict=True))])
    assert not v.ok and v.r is not None and v.r < TC.MIN_R and math.isfinite(v.r)


def test_incident_laps_are_left_out():
    laps = [lap(i, 100, 100, 0.1) for i in range(6)] + [lap(9, 800, 900, 0.1)]
    keep, dropped = TC.drop_incident_laps(laps)
    assert dropped == [9] and len(keep) == 6


def test_ranking_shares_add_up_and_name_the_dominant_phase():
    cl1 = TC.CornerLoad(1)
    cl1.front.update(brake=6.0, turn=1.0, throttle=1.0)
    cl2 = TC.CornerLoad(2)
    cl2.front.update(brake=1.0, turn=1.0, throttle=1.0)
    rows = TC.ranking([TC.LapLoad(1, {1: cl1, 2: cl2}, 11, 0, (0,) * 4, 10)])
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][1] == pytest.approx(8 / 11) and rows[0][2] == "brake"
    assert sum(r[1] for r in rows) == pytest.approx(1.0)
    assert ("front", "brake") in TC.ADVICE_ES


def test_a_lap_from_snapshots_is_attributed_to_the_corner_and_phase():
    snaps = []
    t = 0.0
    for i in range(1200):
        pos = i / 1200
        m = pos * 1000
        braking = 60 <= m <= 140
        snaps.append(
            Snapshot(
                t=t,
                source="test",
                packet_id=i,
                completed_laps=1,
                normalized_pos=pos,
                speed_kmh=100.0,
                brake=0.8 if braking else 0.0,
                wheel_slip=(0.5,) * 4 if braking else (0.0,) * 4,
                wheel_load=(3000.0,) * 4,
                tyre_wear=(99.0,) * 4,
            )
        )
        t += 0.05
    laps = TC.lap_loads(snaps, CORNERS, 1000.0)
    assert len(laps) == 1
    cl = laps[0].corners[1]
    assert cl.dominant("front") == "brake" and cl.front["brake"] > 0
    assert 2 not in laps[0].corners


# -- validation against the game's own tyre app (reference given by the driver) --------------------------
def wheel_lap(n, w):
    return TC.LapLoad(n, {}, w[0] + w[1], w[2] + w[3], (0.0,) * 4, 1000, tuple(w))


def test_reference_accepts_an_index_that_ranks_the_wheels_like_the_app():
    laps = [wheel_lap(i, (140, 93, 246, 197)) for i in range(8)]
    v = TC.validate_reference(laps, (17.0, 12.0, 30.0, 25.0))
    assert v.ok and v.r is not None and v.r > 0.95


def test_reference_rejects_a_different_order_few_laps_and_a_tiny_total_drop():
    laps = [wheel_lap(i, (140, 93, 246, 197)) for i in range(8)]
    assert not TC.validate_reference(laps, (30.0, 25.0, 12.0, 17.0)).ok  # front worn most: opposite
    assert "complete laps" in TC.validate_reference(laps[:3], (17.0, 12.0, 30.0, 25.0)).reason
    assert "in total" in TC.validate_reference(laps, (1.0, 0.0, 2.0, 1.0)).reason
    flat = [wheel_lap(i, (100, 100, 100, 100)) for i in range(8)]
    assert not TC.validate_reference(flat, (17.0, 12.0, 30.0, 25.0)).ok


# -- gathered while driving, and the chat answer -------------------------------------------------------------
def _lap_stream(lap, compound="V", n=1200, brake_zone=(60, 140)):
    out = []
    for i in range(n):
        pos = i / n
        m = pos * 1000
        braking = brake_zone[0] <= m <= brake_zone[1]
        out.append(
            Snapshot(
                t=lap * 100.0 + i * 0.05,
                source="test",
                packet_id=lap * 10000 + i,
                completed_laps=lap,
                normalized_pos=pos,
                speed_kmh=100.0,
                brake=0.8 if braking else 0.0,
                wheel_slip=(0.5,) * 4 if braking else (0.0,) * 4,
                wheel_load=(3000.0,) * 4,
                tyre_wear=(99.0,) * 4,
                tyre_compound=compound,
            )
        )
    return out


def test_the_watch_closes_a_lap_when_the_counter_changes_and_restarts_on_new_tyres():
    w = TC.TyreLoadWatch()
    for lap in (1, 2, 3):
        for s in _lap_stream(lap):
            w.update(s, CORNERS, 1000.0)
    assert [lp.lap for lp in w.laps] == [1, 2]  # lap 3 is still open
    for s in _lap_stream(4, compound="S"):  # a different set of tyres
        w.update(s, CORNERS, 1000.0)
    assert w.laps == [] and w.compound == "S"


def test_the_chat_says_how_many_laps_are_missing_and_then_where_the_tyres_wear():
    from pit_wall_ai.chat import local as L
    from pit_wall_ai.chat.context import build_facts
    from tests.test_chat import synthetic_inputs

    inp = synthetic_inputs()
    w = TC.TyreLoadWatch()
    for lap in range(1, 4):
        for s in _lap_stream(lap):
            w.update(s, CORNERS, 1000.0)
    inp.tyre_watch = w
    r = L.route("¿En qué curvas estoy gastando más los neumáticos?", None, "es")
    assert r is not None and r.intents[0].value == "tyre_corners"
    a = L.answer(r, build_facts(inp), lang="es")
    assert "Todavía no tengo suficientes vueltas" in a.text and "llevo 2" in a.text

    for lap in range(4, 10):
        for s in _lap_stream(lap):
            w.update(s, CORNERS, 1000.0)
    sheet = build_facts(inp)
    a = L.answer(r, sheet, lang="es")
    assert "estimación" in a.text and "curva 1" in a.text and "frenando" in a.text
    from pit_wall_ai.chat import grounding as G

    assert G.check(a.text, sheet, "").ok, a.text
    assert "tyre_corners.front.R0.share_pct" in a.keys


def test_the_german_router_knows_the_question():
    from pit_wall_ai.chat import local as L

    r = L.route("In welchen Kurven verschleiße ich die Reifen am meisten?", None, "de")
    assert r is not None and r.intents[0].value == "tyre_corners"
