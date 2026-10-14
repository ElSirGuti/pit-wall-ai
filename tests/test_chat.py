"""The chat's three deterministic layers: the compact context, the local answers and the grounding check."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pit_wall_ai.analysis.laps import LapRecord, LapTrace
from pit_wall_ai.analysis.track_geometry import Corner, RacingLine, SubApex, TrackGeometry
from pit_wall_ai.analysis.trackctx import TrackInfo
from pit_wall_ai.analysis.tracks import Resolution
from pit_wall_ai.chat import grounding as G
from pit_wall_ai.chat import local as L
from pit_wall_ai.chat.context import (
    WHEEL_ALIASES,
    BrakeWatch,
    ChatInputs,
    build_facts,
    size_estimate,
)
from pit_wall_ai.chat.facts import Fact, FactSheet, Subject
from pit_wall_ai.config import AppConfig
from pit_wall_ai.engineer.engine import AlertRecord
from pit_wall_ai.session.detector import SessionState
from pit_wall_ai.session.engine import SessionEngine
from pit_wall_ai.telemetry.recorder import load_native, snapshot_from_flat

from .helpers import snap as make_snap

FIXTURE = Path(__file__).parent / "fixtures" / "live_lfm_magione_race_window.pwrec.gz"
CFG = AppConfig()


# -- helpers ----------------------------------------------------------------------------------------------
def real_inputs(**kw) -> ChatInputs:
    rec = load_native(FIXTURE)
    eng = SessionEngine()
    watch = BrakeWatch()
    for row, st, t in zip(rec.rows, rec.statics, rec.times, strict=True):
        s = snapshot_from_flat(row, st, t=t)
        eng.process(s)
        watch.update(s)
    eng.flush()
    v = eng.view()
    return ChatInputs(
        snap=v.last_snapshot,
        state=v.state,
        laps=v.laps,
        current=v.current,
        best=v.best,
        cfg=CFG,
        brakes=watch,
        now=v.last_snapshot.t + 0.5,
        **kw,
    )


def corner(n, start, apex, end):
    return Corner(
        n,
        start / 3000,
        apex / 3000,
        end / 3000,
        start,
        apex,
        end,
        50.0,
        "right",
        "slow",
        "single",
        [SubApex(apex / 3000, apex, 50.0, "right", start, end)],
    )


def synthetic_track(names=None) -> TrackInfo:
    line = RacingLine(np.array([0.0, 3000.0]), np.zeros(2), np.array([0.0, 3000.0]), True, 0.0)
    g = TrackGeometry(
        line=line,
        corners=[
            corner(1, 480, 500, 540),
            corner(2, 1480, 1500, 1560),
            corner(3, 2380, 2400, 2460),
        ],
        step_m=2.0,
        distance_m=np.zeros(1),
        curvature=np.zeros(1),
        track="stad",
    )
    res = Resolution(track="stad", layout="", geometry=g)
    return TrackInfo("stad", "", res, names or {})


def lap(number, brakes=None, *, ms=None, vmin=60.0, compound="soft"):
    n = 4000
    d = np.linspace(0.0, 3000.0, n)
    speed, brake, gas = np.full(n, 200.0), np.zeros(n), np.ones(n)
    for apex, bp in (brakes or {}).items():
        down = (d >= bp) & (d <= apex)
        speed = np.where(down, 200 - (200 - vmin) * (d - bp) / (apex - bp), speed)
        up = (d > apex) & (d <= apex + 120)
        speed = np.where(up, vmin + (200 - vmin) * (d - apex) / 120.0, speed)
        brake[down], gas[down] = 0.8, 0.0
    t_ms = np.cumsum(np.diff(d, prepend=0.0) / (np.maximum(speed, 5.0) / 3.6)) * 1000.0
    trace = LapTrace(d / 3000.0, t_ms, speed, gas, brake, np.zeros(n))
    return LapRecord(
        number=number,
        time_ms=ms or int(t_ms[-1]),
        trace=trace,
        coverage=1.0,
        compound=compound,
        fuel_start=30 - number,
        fuel_end=29 - number,
    )


def synthetic_inputs(snap=None, **kw) -> ChatInputs:
    ref = lap(1, {500: 400, 1500: 1400, 2400: 2300}, ms=84_889)
    mine = lap(2, {500: 400, 1500: 1370, 2400: 2300}, ms=85_500)  # 30 m earlier at corner 2
    s = snap or make_snap(
        t=100.0,
        fuel=20.0,
        tyre_compound="soft",
        tyre_core_temp=(82.0, 98.0, 85.0, 91.0),
        tyre_pressure=(27.1, 27.4, 26.8, 27.0),
        tyre_wear=(97.0, 96.5, 98.0, 97.5),
    )
    return ChatInputs(
        snap=s,
        state=SessionState(
            session="practice",
            status="live",
            phase="flying",
            track="stad",
            car="mx5",
            completed_laps=2,
        ),
        laps=[ref, mine],
        current=None,
        best=ref,
        cfg=CFG,
        track=synthetic_track({2: "Curvone"}),
        now=100.5,
        **kw,
    )


# -- context ----------------------------------------------------------------------------------------------
def test_the_context_of_a_real_race_has_session_laps_fuel_and_tyres_and_fits_the_budget():
    sheet = build_facts(real_inputs())
    ctx = sheet.context()
    assert ctx["session"]["kind"] == "race" and ctx["session"]["track"] == "magione"
    assert ctx["laps"]["best"]["time_s"] > 80 and ctx["laps"]["last"]["time_s"] > 80
    assert {"FL", "FR", "RL", "RR"} <= set(ctx["tyres"])
    assert ctx["tyres"]["FL"]["core_c"] > 0 and "pressure_psi" in ctx["tyres"]["FL"]
    assert size_estimate(ctx) <= 1500  # the budget of the plan, in tokens


def test_a_flat_brake_temperature_sensor_is_left_out_and_noted():
    ctx = build_facts(real_inputs()).context()  # the MX-5 sensor does not move in this window
    assert "FL" not in ctx["brakes"]
    assert "does not move" in ctx["brakes"]["note"]


def test_a_live_brake_temperature_sensor_is_included():
    watch = BrakeWatch()
    for i in range(50):
        watch.update(make_snap(t=float(i), brake_temp=(100.0 + i * 5, 90.0 + i * 5, 80.0, 80.0)))
    inp = synthetic_inputs(
        snap=make_snap(t=100.0, fuel=10.0, brake_temp=(350.0, 340.0, 80.0, 80.0)), brakes=watch
    )
    ctx = build_facts(inp).context()
    assert ctx["brakes"]["FL"]["temp_c"] == 350 and "RL" not in ctx["brakes"]


def test_fuel_per_lap_and_laps_left_come_from_the_timed_laps():
    ctx = build_facts(synthetic_inputs()).context()
    assert ctx["fuel"]["litres"] == 20.0 and ctx["fuel"]["per_lap_l"] == 1.0
    assert ctx["fuel"]["laps_left"] == 20.0


def test_the_worst_corner_of_the_last_lap_is_in_the_context_with_its_name():
    ctx = build_facts(synthetic_inputs()).context()["analysis"]
    assert ctx["lap"] == 2 and ctx["reference_lap"] == 1
    assert ctx["worst"]["corner"] == 2 and ctx["worst"]["name"] == "Curvone"
    assert ctx["worst"]["brake_earlier_m"] == pytest.approx(30, abs=3)
    assert "C2" in ctx["corners"]


def test_without_geometry_the_context_says_why_instead_of_inventing_an_analysis():
    inp = synthetic_inputs()
    inp.track = TrackInfo("x", "", Resolution("x", "", reason="no fast_lane.ai for track 'x'"), {})
    ctx = build_facts(inp).context()
    assert "no fast_lane.ai" in ctx["analysis"]["unavailable"] and "worst" not in ctx["analysis"]


def test_recent_alerts_carry_their_rule_text_and_outcome():
    alerts = [
        AlertRecord(90.0, "lockup", "Bloqueo delantero izquierdo", "HIGH", {"brake": 0.8}, "said")
    ]
    ctx = build_facts(synthetic_inputs(alerts=alerts)).context()
    a = ctx["alerts"]["A0"]
    assert a["rule"] == "lockup" and a["outcome"] == "said" and a["age_s"] == 10


def test_an_unverified_penalty_unit_is_flagged_in_the_context():
    ctx = build_facts(synthetic_inputs(snap=make_snap(t=1.0, fuel=5.0, penalty_time=5.0))).context()
    assert ctx["penalties"] == {"time_s": 5.0, "unit_verified": False}


# -- router -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("¿cuánta gasolina me queda?", [L.Intent.FUEL]),
        ("how much fuel is left", [L.Intent.FUEL]),
        ("¿cómo están los neumáticos?", [L.Intent.TYRES]),
        ("how are my tyres and brakes", [L.Intent.TYRES, L.Intent.BRAKES]),
        ("temperatura de los frenos", [L.Intent.BRAKES]),
        ("¿cuál fue mi mejor vuelta?", [L.Intent.LAPS]),
        ("dime el delta", [L.Intent.LAPS]),
        ("¿en qué sesión estoy?", [L.Intent.SESSION]),
        ("¿tengo alguna penalización?", [L.Intent.PENALTY]),
        ("¿dónde pierdo tiempo?", [L.Intent.LOSS]),
        ("where am I losing time", [L.Intent.LOSS]),
        ("¿qué tal mis frenadas?", [L.Intent.LOSS]),
        ("¿por qué avisaste antes?", [L.Intent.LAST_ALERT]),
        ("what was that", [L.Intent.LAST_ALERT]),
        ("¿va a llover?", [L.Intent.UNKNOWN_DATA]),
        ("¿qué hacen los rivales?", [L.Intent.UNKNOWN_DATA]),
    ],
)
def test_the_router_picks_the_intents_of_a_question(question, expected):
    r = L.route(question)
    assert r is not None and list(r.intents) == expected


@pytest.mark.parametrize(
    "question",
    [
        "explícame cómo tomar bien la chicane",
        "¿qué opinas de mi ritmo?",
        "hola",
        "",
        "cuéntame algo",
    ],
)
def test_open_questions_go_to_the_model(question):
    assert L.route(question) is None


def test_a_corner_question_is_recognised_by_name_number_or_ordinal():
    t = synthetic_track({1: "Curva Traliccio", 2: "Curvone", 3: "Prima Esse"})
    cases = [
        ("¿cuál es la prima esse?", (3,)),
        ("cuál es la curva 2", (2,)),
        ("which corner is the curvone", (2,)),
        ("¿qué curva es la última?", (3,)),
    ]
    for q, want in cases:
        r = L.route(q, t)
        assert r is not None and r.intents == (L.Intent.CORNER,) and r.corners == want, q


def test_a_corner_name_alone_without_a_question_is_not_a_corner_intent():
    t = synthetic_track({2: "Curvone"})
    assert L.route("me caí en la curvone", t) is None


def test_unanswerable_is_dropped_when_something_answerable_was_also_asked():
    r = L.route("¿cuánta gasolina queda y va a llover?")
    assert r is not None and r.intents == (L.Intent.FUEL,)


# -- local answers ----------------------------------------------------------------------------------------
def ask(question, inp=None, lang="es"):
    inp = inp or synthetic_inputs()
    sheet = build_facts(inp)
    r = L.route(question, inp.track)
    assert r is not None, question
    return sheet, L.answer(r, sheet, lang=lang, track=inp.track), r


def test_fuel_answer_has_litres_per_lap_and_laps_phrase():
    _, a, _ = ask("¿cuánta gasolina queda?")
    assert "20,0 litros" in a.text and "1,00 litros por vuelta" in a.text and "20 vueltas" in a.text


def test_fuel_without_consumption_data_says_so():
    inp = synthetic_inputs(snap=make_snap(t=1.0, fuel=12.3))
    inp.laps = []
    inp.best = None
    _, a, _ = ask("gasolina", inp)
    assert "12,3 litros" in a.text and "todavía no tengo el consumo" in a.text


def test_tyre_answer_lists_each_wheel_with_its_own_numbers():
    _, a, _ = ask("¿cómo están los neumáticos?")
    t = a.text
    assert "delantero izquierdo: 82 °C, 27,1 psi, desgaste 97,0 %" in t
    assert "delantero derecho: 98 °C, 27,4 psi" in t
    assert "(soft)" in t


def test_brake_answer_refuses_a_flat_sensor():
    _, a, _ = ask("¿y los frenos?")
    assert "no es fiable" in a.text and "no la uso" in a.text


def test_laps_answer_has_best_last_and_delta():
    _, a, _ = ask("mejor vuelta y delta")
    assert "1:24,889" in a.text and "1:25,500" in a.text and "0,611 s más lenta" in a.text


def test_loss_answer_names_the_corner_and_the_measured_braking_difference():
    _, a, _ = ask("¿dónde pierdo tiempo?")
    assert "Curvone (la curva 2)" in a.text and "frenas 30 m antes" in a.text
    assert "vuelta 2" in a.text


def test_loss_answer_when_nothing_reaches_the_thresholds():
    inp = synthetic_inputs()
    inp.laps = [
        lap(1, {500: 400, 1500: 1400, 2400: 2300}, ms=84_889),
        lap(2, {500: 400, 1500: 1400, 2400: 2300}, ms=84_950),
    ]
    inp.best = inp.laps[0]
    _, a, _ = ask("¿dónde pierdo tiempo?", inp)
    assert "ninguna curva combina" in a.text and "10 m" in a.text and "0,1 s" in a.text


def test_corner_answer_is_the_glossary_sentence():
    _, a, _ = ask("¿cuál es la curvone?")
    assert a.text.startswith("Curvone, que es la curva 2")


def test_ambiguous_corner_asks_which_one():
    inp = synthetic_inputs()
    inp.track = synthetic_track({1: "Seconda Esse", 2: "Prima Esse", 3: "Curva Esse Tornantino"})
    _, a, r = ask("¿cuál es la esse?", inp)
    assert len(r.corners) == 3 and "¿Cuál?" in a.text


def test_last_alert_answer_quotes_the_text_and_the_rule():
    alerts = [AlertRecord(90.0, "lockup", "Bloqueo delantero izquierdo", "HIGH", {}, "said")]
    _, a, _ = ask("¿qué fue eso?", synthetic_inputs(alerts=alerts))
    assert "«Bloqueo delantero izquierdo»" in a.text and "lockup" in a.text and "10 s" in a.text


def test_unanswerable_data_is_refused_locally():
    _, a, _ = ask("¿va a llover?")
    assert a.text.startswith("No tengo ese dato")


def test_english_answers():
    _, a, _ = ask("how much fuel is left", lang="en")
    assert "20.0 litres left" in a.text and "1.00 litres per lap" in a.text


def test_every_local_answer_passes_its_own_grounding_check():
    alerts = [
        AlertRecord(95.0, "lockup", "Bloqueo trasero derecho", "HIGH", {"brake": 0.8}, "said")
    ]
    inp = synthetic_inputs(alerts=alerts)
    questions = (
        "¿cuánta gasolina queda?",
        "neumáticos",
        "frenos",
        "mejor vuelta y delta",
        "¿en qué sesión estoy?",
        "penalización",
        "¿dónde pierdo tiempo?",
        "¿qué fue eso?",
        "how are my tyres",
    )
    for q in questions:
        sheet = build_facts(inp)
        r = L.route(q, inp.track)
        assert r is not None, q
        a = L.answer(r, sheet, lang="en" if q.startswith("how") else "es", track=inp.track)
        rep = G.check(a.text, sheet, q)
        assert rep.ok, (q, a.text, rep.violations)


# -- grounding --------------------------------------------------------------------------------------------
def tyre_sheet() -> FactSheet:
    s = FactSheet()
    for w, c, p in (("FL", 82, 27.1), ("FR", 98, 27.4), ("RL", 85, 26.8), ("RR", 91, 27.0)):
        sub = Subject("wheel", w)
        s.alias(sub, *WHEEL_ALIASES[w])
        s.add(Fact(f"tyres.{w}.core_c", c, "C", sub))
        s.add(Fact(f"tyres.{w}.pressure_psi", p, "psi", sub))
    s.add(Fact("fuel.litres", 12.3, "L"))
    s.add(Fact("fuel.laps_left", 1.87, "laps"))
    return s


def corner_sheet() -> FactSheet:
    s = FactSheet()
    for n, name, loss, early in ((6, "Curvone", 0.62, 14.0), (3, "Esse Tornantino", 0.21, 3.0)):
        sub = Subject("corner", str(n))
        s.alias(sub, f"curva {n}", f"corner {n}", name)
        s.add(Fact(f"analysis.corners.C{n}.time_diff_s", loss, "s", sub, (loss, round(loss, 1))))
        s.add(
            Fact(f"analysis.corners.C{n}.brake_earlier_m", early, "m", sub, (early, round(early)))
        )
    for n, ms in ((5, 85200), (6, 84900)):
        sub = Subject("lap", str(n))
        s.alias(sub, f"vuelta {n}", f"lap {n}")
        s.add(Fact(f"laps.recent.L{n}.time_s", ms / 1000, "s", sub, (ms / 1000, float(ms))))
    return s


@pytest.mark.parametrize(
    "reply",
    [
        "Quedan 12,3 litros.",
        "Quedan 12.3 litros.",
        "Quedan 12 coma 3 litros.",
        "Te quedan 12,30 litros.",
        "Es decir 1,9 vueltas.",  # 1.87 rounded to one decimal
        "Alrededor de 2 vueltas.",  # rounded to the units
        "Son 1,87 vueltas.",
    ],
)
def test_equivalent_formats_and_roundings_are_accepted(reply):
    rep = G.check(reply, tyre_sheet())
    assert rep.ok, rep.violations


@pytest.mark.parametrize(
    "reply",
    ["Quedan 12,4 litros.", "Quedan 15 litros.", "Son 1,8 vueltas.", "Quedan 12,2 litros."],
)
def test_a_number_that_is_not_in_the_context_is_flagged(reply):
    rep = G.check(reply, tyre_sheet())
    assert [v.kind for v in rep.violations] == ["unsupported"]


def test_lap_times_are_accepted_as_clock_seconds_or_milliseconds():
    s = corner_sheet()
    for ok in (
        "La vuelta 6 fue 1:24,900.",
        "La vuelta 6 fue 1:24,9.",
        "La vuelta 6 fue 84,9 s.",
        "La vuelta 6 duró 84900 ms.",
        "La vuelta 6 fue 1:25.",
    ):
        assert G.check(ok, s).ok, ok
    assert not G.check("La vuelta 6 fue 1:23,0.", s).ok


def test_the_sign_of_a_delta_is_not_required():
    s = FactSheet()
    s.add(Fact("laps.last.delta_to_best_s", 0.611, "s", numbers=(0.611, 611.0)))
    assert G.check("Fue 0,611 s más lenta.", s).ok
    assert G.check("Delta de -0,611 s.", s).ok


# the conditions of the author: a correct number attached to the wrong place
def test_a_correct_tyre_temperature_on_the_wrong_wheel_is_flagged():
    rep = G.check("El neumático delantero izquierdo está a 98 °C.", tyre_sheet())
    assert [v.kind for v in rep.violations] == ["misattributed"]
    assert "tyres.FR.core_c" in rep.violations[0].detail


def test_the_same_numbers_on_the_right_wheels_pass_in_either_order():
    s = tyre_sheet()
    assert G.check("El delantero derecho está a 98 °C y el delantero izquierdo a 82 °C.", s).ok
    assert G.check("El delantero izquierdo está a 82 °C y el delantero derecho a 98 °C.", s).ok


def test_swapping_two_wheels_in_one_sentence_is_flagged():
    rep = G.check(
        "El delantero izquierdo está a 98 °C y el delantero derecho a 82 °C.", tyre_sheet()
    )
    assert [v.kind for v in rep.violations] == ["misattributed", "misattributed"]


def test_a_correct_loss_attributed_to_the_wrong_corner_is_flagged():
    s = corner_sheet()
    rep = G.check("En la Curvone pierdes 0,2 s.", s)  # 0.21 belongs to the Esse Tornantino
    assert [v.kind for v in rep.violations] == ["misattributed"]
    assert G.check("En la Curvone pierdes 0,6 s.", s).ok
    assert G.check("En la Curvone pierdes 0,6 s y en la curva 3 pierdes 0,2 s.", s).ok
    assert not G.check("En la curva 6 frenas 3 m antes.", s).ok  # 3 m is the Esse Tornantino's


def test_a_lap_time_attributed_to_another_lap_is_flagged():
    s = corner_sheet()
    assert G.check("La vuelta 5 fue 1:25,2.", s).ok
    rep = G.check("La vuelta 6 fue 1:25,2.", s)
    assert [v.kind for v in rep.violations] == ["misattributed"]


def test_a_correct_number_with_the_unit_of_another_quantity_is_flagged():
    rep = G.check("La presión es 98 psi.", tyre_sheet())  # 98 is a temperature in degrees
    assert [v.kind for v in rep.violations] == ["unit"]
    assert G.check("La temperatura es 98 °C en el delantero derecho.", tyre_sheet()).ok


def test_subject_numbers_are_references_not_claims():
    rep = G.check("En la curva 6 de la vuelta 6 pierdes 0,6 s.", corner_sheet())
    assert rep.ok and rep.checked == 1


def test_numbers_of_the_question_and_the_alert_texts_are_allowed():
    s = tyre_sheet()
    s.extra_numbers.append(14.0)
    assert G.check("Como decías, 40 grados no es nada.", s, question="¿40 grados es mucho?").ok
    assert G.check("El aviso decía 14 metros.", s).ok


def test_ordinals_are_not_numbers_to_check():
    assert G.check("Vas 3.º en la clasificación.", tyre_sheet()).ok


def test_sanitize_drops_only_the_offending_sentences():
    s = tyre_sheet()
    reply = "El delantero izquierdo está a 98 °C. El delantero derecho está a 98 °C."
    rep = G.check(reply, s)
    assert G.sanitize(reply, rep) == "El delantero derecho está a 98 °C."
    only_bad = "El delantero izquierdo está a 98 °C."
    assert "No puedo confirmar" in G.sanitize(only_bad, G.check(only_bad, s))


def test_the_nothing_reaches_the_thresholds_answer_passes_its_own_grounding_check():
    inp = synthetic_inputs()
    inp.laps = [
        lap(1, {500: 400, 1500: 1400, 2400: 2300}, ms=84_889),
        lap(2, {500: 400, 1500: 1400, 2400: 2300}, ms=84_950),
    ]
    inp.best = inp.laps[0]
    sheet = build_facts(inp)
    r = L.route("¿dónde pierdo tiempo?", inp.track)
    a = L.answer(r, sheet, lang="es", track=inp.track, thresholds=(10.0, 0.1))
    rep = G.check(a.text, sheet, "¿dónde pierdo tiempo?")
    assert "frente a la vuelta 1 (tu mejor)" in a.text and "10 m" in a.text and "0,1 s" in a.text
    assert rep.ok, rep.violations
    assert sheet.value("analysis.thresholds.min_distance_m") == 10.0


def test_the_threshold_answer_in_english_passes_too():
    inp = synthetic_inputs()
    inp.laps = [
        lap(1, {500: 400, 1500: 1400, 2400: 2300}, ms=84_889),
        lap(2, {500: 400, 1500: 1400, 2400: 2300}, ms=84_950),
    ]
    inp.best = inp.laps[0]
    sheet = build_facts(inp)
    r = L.route("where am I losing time", inp.track)
    a = L.answer(r, sheet, lang="en", track=inp.track, thresholds=(10.0, 0.1))
    assert G.check(a.text, sheet, "where am I losing time").ok, a.text


@pytest.mark.parametrize(
    "question",
    [
        "¿qué setup me recomiendas?",
        "¿cuándo debo parar a boxes?",
        "dame una estrategia de carrera",
        "what setup do you recommend",
        "when should I pit",
    ],
)
def test_setup_and_strategy_questions_get_a_clear_not_yet_without_the_model(question):
    r = L.route(question)
    assert r is not None and r.intents == (L.Intent.NOT_YET,)
    sheet = FactSheet()
    es = L.answer(r, sheet, lang="es").text
    assert "todavía no están disponibles" in es and "fase posterior" in es
    assert "not available yet" in L.answer(r, sheet, lang="en").text


# -- the findings of the first run with the real model on a PC without the game ---------------------------------
def no_game_track() -> TrackInfo:
    from pit_wall_ai.analysis import circuit_names

    reason = "no Assetto Corsa tracks folder found (set analysis.ac_dirs)"
    return TrackInfo(
        "magione",
        "",
        Resolution("magione", "", reason=reason),
        circuit_names.table_names("magione") or {},
    )


def test_the_table_names_corners_without_the_game_only_for_a_verified_circuit():
    from pit_wall_ai.analysis import circuit_names

    names = circuit_names.table_names("magione")
    assert names is not None and len(names) == 11 and names[6] == "Curvone"
    assert circuit_names.table_names("spa") is None  # its table order is not the circuit numbering
    assert circuit_names.table_names("some_mod_track") is None


@pytest.mark.parametrize(
    ("question", "number"),
    [
        ("¿cuál es la prima esse?", 11),
        ("¿cuál es la curva 6?", 6),
        ("¿qué curva es el curvone?", 6),
    ],
)
def test_a_corner_is_identified_from_the_table_when_the_game_is_not_installed(question, number):
    t = no_game_track()
    inp = synthetic_inputs()
    inp.track = t
    sheet = build_facts(inp)
    r = L.route(question, t)
    assert r is not None and r.corners == (number,)
    text = L.answer(r, sheet, lang="es", track=t).text
    assert f"curva {number}" in text and "no tengo ese dato" not in text.lower()
    assert "la última" in text if number == 11 else "la primera" not in text


def test_the_loss_answer_explains_in_spanish_why_there_is_no_analysis():
    inp = synthetic_inputs()
    inp.track = no_game_track()
    sheet = build_facts(inp)
    r = L.route("¿dónde pierdo tiempo?", inp.track)
    text = L.answer(r, sheet, lang="es", track=inp.track).text
    assert "carpeta de pistas de Assetto Corsa" in text and "analysis.ac_dirs" in text
    assert "no Assetto Corsa tracks folder" not in text


def test_summarise_and_explain_are_open_questions_even_with_a_keyword_inside():
    for q in (
        "resúmeme la sesión en dos frases",
        "explícame cómo van los neumáticos",
        "summarise my session",
        "¿qué opinas de mis frenadas?",
    ):
        assert L.route(q) is None, q
    assert L.route("¿en qué sesión estoy?").intents == (L.Intent.SESSION,)


def test_the_hottest_tyre_is_named_with_its_temperature():
    _, a, r = ask("¿cuál neumático está más caliente y a cuántos grados?")
    assert r.flags == ("hottest",)
    assert a.text.startswith("El más caliente es el delantero derecho, a 98 °C.")
    _, both, _ = ask("¿cuál es el más caliente y el más frío?")
    assert "El más caliente es el delantero derecho" in both.text
    assert "El más frío es el delantero izquierdo, a 82 °C." in both.text


def test_the_hottest_answer_passes_its_own_grounding_check():
    inp = synthetic_inputs()
    sheet = build_facts(inp)
    q = "¿cuál neumático está más caliente y a cuántos grados?"
    a = L.answer(L.route(q, inp.track), sheet, lang="es", track=inp.track)
    assert G.check(a.text, sheet, q).ok


def test_asking_for_the_best_lap_when_the_last_is_the_best_says_so():
    inp = synthetic_inputs()
    inp.laps = [lap(1, {500: 400}, ms=85_000), lap(2, {500: 400}, ms=84_000)]
    inp.best = inp.laps[1]
    _, a, _ = ask("compara mi mejor vuelta con la última", inp)
    assert "La última vuelta es tu mejor vuelta." in a.text


def test_the_live_delta_sentence_keeps_its_full_stop():
    from pit_wall_ai.analysis.laps import CurrentLap

    inp = synthetic_inputs()
    inp.current = CurrentLap(
        number=3, elapsed_ms=40_000, sector=1, out_lap=False, delta_ms=980.0, reference_number=1
    )
    _, a, _ = ask("¿cuál fue mi última vuelta?", inp)
    assert a.text.endswith("Delta en vivo: +0,98 s contra la referencia.")


# -- clause-aware attribution and elided references (false positives found in the first real evaluation) ----
def lap_corner_sheet() -> FactSheet:
    s = FactSheet()
    for n, loss in ((11, 0.38), (4, 0.27)):
        sub = Subject("corner", str(n))
        s.alias(sub, f"curva {n}", f"corner {n}")
        s.add(Fact(f"analysis.corners.C{n}.time_diff_s", loss, "s", sub, (loss, round(loss, 1))))
    for n, t in ((1, 96.324), (2, 85.444), (3, 84.913)):
        sub = Subject("lap", str(n))
        s.alias(sub, f"vuelta {n}", f"lap {n}")
        s.add(Fact(f"laps.recent.L{n}.time_s", t, "s", sub, (t, round(t * 1000))))
    return s


def test_each_number_is_read_in_its_own_clause():
    s = lap_corner_sheet()
    ok = "En la vuelta 2 perdiste 0.38 s en la curva 11 y 0.27 s en la curva 4."
    assert G.check(ok, s).ok, G.check(ok, s).violations
    swapped = "En la vuelta 2 perdiste 0.38 s en la curva 4 y 0.27 s en la curva 11."
    rep = G.check(swapped, s)
    assert [v.kind for v in rep.violations] == ["misattributed", "misattributed"]


def test_a_bare_la_2_after_a_lap_is_that_lap():
    s = lap_corner_sheet()
    ok = "Hiciste la vuelta 1 con 96,324 s, la 2 con 85,444 s y la 3 con 84,913 s."
    assert G.check(ok, s).ok, G.check(ok, s).violations
    wrong = "Hiciste la vuelta 1 con 96,324 s, la 2 con 84,913 s y la 3 con 85,444 s."
    rep = G.check(wrong, s)
    assert [v.kind for v in rep.violations] == ["misattributed", "misattributed"]


def test_the_elided_number_is_a_reference_not_a_claim():
    rep = G.check("La vuelta 1 y la 2 fueron lentas.", lap_corner_sheet())
    assert rep.ok and rep.checked == 0


def test_a_number_without_a_subject_in_its_clause_falls_back_to_the_sentence():
    s = lap_corner_sheet()
    assert G.check("En la curva 11 pierdes 0.38 s y frenas igual.", s).ok
    assert not G.check("En la curva 4 pierdes 0.38 s y frenas igual.", s).ok


def test_axle_and_side_aggregates_are_facts_so_a_comparison_is_grounded():
    ctx = build_facts(synthetic_inputs()).context()["tyres"]["axles"]
    # FL 82, FR 98, RL 85, RR 91 degrees; 27.1, 27.4, 26.8, 27.0 psi
    assert ctx["front_core_c"] == 90.0 and ctx["rear_core_c"] == 88.0
    assert ctx["front_minus_rear_core_c"] == 2.0
    assert ctx["front_pressure_psi"] == 27.25 and ctx["rear_pressure_psi"] == 26.9
    assert ctx["front_minus_rear_pressure_psi"] == 0.35
    assert ctx["left_core_c"] == 83.5 and ctx["right_core_c"] == 94.5
    assert ctx["left_minus_right_core_c"] == -11.0
    sheet = build_facts(synthetic_inputs())
    ok = "La presión media del eje delantero es 27,25 psi y la del trasero 26,9 psi: 0,35 psi más delante."
    assert G.check(ok, sheet).ok, G.check(ok, sheet).violations
    assert not G.check("El eje delantero tiene 1,5 psi más que el trasero.", sheet).ok
