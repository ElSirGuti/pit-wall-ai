"""Idioma alemán: avisos, voz, órdenes, persona, memoria, vocabulario y el camino del modelo."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pit_wall_ai.chat import commands, recall
from pit_wall_ai.chat import grounding as G
from pit_wall_ai.chat.session import lap_times_as_clock
from pit_wall_ai.config import AppConfig
from pit_wall_ai.engineer import messages as M
from pit_wall_ai.engineer.persona import STYLES, Persona
from pit_wall_ai.stt import VOCABULARY, build_hotwords
from pit_wall_ai.voice import tts as T
from pit_wall_ai.voice.radio import PHRASES


def test_language_accepts_the_three_and_rejects_others():
    assert AppConfig.model_validate({"engineer": {"language": "de"}}).engineer.language == "de"
    with pytest.raises(ValueError):
        AppConfig.model_validate({"engineer": {"language": "fr"}})


def test_every_alert_text_exists_in_german_with_the_same_placeholders():
    import re

    es, de = M.TEXTS["es"], M.TEXTS["de"]
    assert set(es) == set(de)
    for k in es:
        assert set(re.findall(r"{(\w+)}", es[k])) == set(re.findall(r"{(\w+)}", de[k])), k


def test_german_messages_read_naturally():
    assert (
        M.say("de", "lockup", where=M.wheel_name("de", "FL"))
        == "vorne links blockiert, Bremse lösen"
    )
    assert M.wheels_name("de", ["FL", "FR"]) == "Vorderachse"
    assert M.laps_phrase("de", 1.9) == "fast zwei Runden"
    assert M.laps_phrase("de", 1.5) == "anderthalb Runden"
    assert M.laps_phrase("de", 3.5) == "dreieinhalb Runden"
    assert M.laps_phrase("de", 0.2) == "weniger als eine halbe Runde"
    assert M._tenths("de", 0.3) == "3 Zehntel" and M._tenths("de", 1.3) == "1 Komma 3 Sekunden"
    assert M.where_phrase("de", name=None, number=6, direction="left", at_m=0) == "Kurve 6"
    s = M.braking_hint("de", where="Kurve 6", metres=21, earlier=True, loss_s=0.25)
    assert "21 Meter früher" in s and "3 Zehntel" in s or "2 Zehntel" in s


def test_german_quiet_and_resume_commands():
    assert commands.parse("Nicht stören!") == "quiet"
    assert commands.parse("Jetzt nicht") == "quiet"
    assert commands.parse("Du kannst wieder sprechen") == "resume"
    assert commands.parse("Wie viel Sprit habe ich noch?") is None


def test_german_persona_and_voice_pieces():
    assert all("de" in v for v in STYLES.values())
    assert "Du heißt Max" in Persona(name="Max").system_suffix("de")
    assert Persona(name="Max").greeting("de").startswith("Hallo, ich bin Max")
    assert set(PHRASES["de"]) == set(PHRASES["es"])
    assert T._lang_of("407") == "de"


def test_recall_in_german():
    track = SimpleNamespace(names={4: "Tornantino"}, geometry=None)
    from pit_wall_ai.engineer.engine import AlertRecord

    a = AlertRecord(100.0, "braking_hint", "Kurve 4: du bremst 7 Meter später", "MEDIUM", {})
    r = recall.recall(
        "Was hast du mir über Kurve 4 gesagt?",
        alerts=[a],
        history=[],
        track=track,
        now=130.0,
        lang="de",
    )
    assert r is not None and "vor 30 s" in r.text and r.text.startswith("Das habe ich dir zu")


def test_german_lap_time_and_refusal_texts():
    assert (
        lap_times_as_clock("deine Bestzeit ist 84,913 Sekunden", "de")
        == "deine Bestzeit ist 1:24,913"
    )
    rep = G.GroundingReport(violations=[G.Violation("unsupported", "9", "Neun Grad.", "x")])
    assert "nicht bestätigen" in G.sanitize("Neun Grad.", rep, "de")


def test_the_recogniser_gets_german_words():
    assert "Bremspunkt" in VOCABULARY["de"]
    assert "Kurve" not in build_hotwords([], lang="es")
    assert build_hotwords(["Curvone"], lang="de").startswith("Curvone, psi, vorne links")


def test_decimals_use_the_comma_in_spanish_and_german_only():
    from pit_wall_ai.chat.session import decimal_comma

    assert decimal_comma("du hast 2.0 Liter, 71.5 °C", "de") == "du hast 2,0 Liter, 71,5 °C"
    assert decimal_comma("quedan 2.0 litros", "es") == "quedan 2,0 litros"
    assert decimal_comma("2.0 litres", "en") == "2.0 litres"
    assert decimal_comma("Runde 3. Dann 2.", "de") == "Runde 3. Dann 2."
