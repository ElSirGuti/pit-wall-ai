"""Persona (wording only) and the memory of what was said."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pit_wall_ai.chat import recall as R
from pit_wall_ai.config import AppConfig
from pit_wall_ai.engineer.engine import AlertRecord
from pit_wall_ai.engineer.persona import STYLES, Persona, from_config

NAMES = {2: "Curvone", 4: "Tornantino", 6: "Curva 6"}
TRACK = SimpleNamespace(names=NAMES, geometry=None)


def alert(t, text, rule="braking_hint"):
    return AlertRecord(t, rule, text, "MEDIUM", {})


def test_the_neutral_persona_adds_nothing_to_the_prompt():
    assert Persona().system_suffix("es") == ""


def test_name_driver_and_style_reach_the_prompt_and_the_data_rule_is_kept():
    p = Persona(name="Sabina", driver_name="Andrés", style="calm", custom="háblame de usted")
    s = p.system_suffix("es")
    assert "Te llamas Sabina" in s and "Andrés" in s and "sereno" in s and "de usted" in s
    assert "nunca cambia los datos" in s
    assert "Your name is Sabina" in p.system_suffix("en")


def test_every_style_has_both_languages_and_no_real_person_is_named():
    for key, texts in STYLES.items():
        assert set(texts) == {"es", "en", "de"}, key
    banned = ("bozzi", "bonnington", "lambiase", "hamilton", "verstappen")
    blob = " ".join(t for v in STYLES.values() for t in v.values()).lower()
    assert not any(b in blob for b in banned)


def test_the_greeting_uses_the_persona_name_over_the_voice_name():
    assert (
        Persona(name="Marta").greeting("es", "Sabina")
        == "Hola, soy Marta y seré tu ingeniero de pista."
    )
    assert "Sabina" in Persona().greeting("es", "Sabina")
    assert "Hello, I am Sabina" in Persona().greeting("en", "Sabina")


def test_config_round_trip_and_unknown_style_is_rejected():
    cfg = AppConfig.model_validate({"engineer": {"persona": {"name": "Marta", "style": "warm"}}})
    assert from_config(cfg.engineer.persona) == Persona("Marta", None, "warm", None)
    with pytest.raises(ValueError):
        AppConfig.model_validate({"engineer": {"persona": {"style": "pirate"}}})


# -- memory -----------------------------------------------------------------------------------------------------
def test_recall_quotes_the_alerts_about_that_corner_most_recent_first():
    alerts = [
        alert(100.0, "Frenaste 12 metros antes en la Curvone"),
        alert(150.0, "Mucho volante en Tornantino"),
        alert(300.0, "Frenaste tarde en la Curvone, pierdes 0,4 segundos"),
    ]
    r = R.recall(
        "¿Qué me habías dicho de la Curvone?", alerts=alerts, history=[], track=TRACK, now=330.0
    )
    assert r is not None and r.found == 2
    assert r.text.index("pierdes 0,4") < r.text.index("12 metros")  # the latest first
    assert "hace 30 s" in r.text and "Tornantino" not in r.text


def test_recall_by_corner_number_and_from_the_chat_history():
    r = R.recall(
        "¿qué me dijiste de la curva 6?",
        alerts=[],
        history=[
            ("¿cuánto perdí en la curva 6?", "En la vuelta 3, en la curva 6: pierdes 0,25 s.")
        ],
        track=TRACK,
        now=None,
    )
    assert r is not None and "pierdes 0,25 s" in r.text and r.found == 1


def test_nothing_on_record_is_said_plainly():
    r = R.recall("¿qué me dijiste de la curva 2?", alerts=[], history=[], track=TRACK, now=10.0)
    assert r is not None and r.found == 0 and "No tengo registrado" in r.text


def test_without_a_corner_the_question_is_left_to_the_router():
    assert (
        R.recall("¿qué me dijiste?", alerts=[alert(1, "x")], history=[], track=TRACK, now=2.0)
        is None
    )
    assert R.recall("¿cuánta gasolina queda?", alerts=[], history=[], track=TRACK, now=2.0) is None
