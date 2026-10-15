"""Recall by topic and from earlier sessions, and the file that keeps them."""

from __future__ import annotations

import time
from types import SimpleNamespace

from pit_wall_ai.chat import recall as R
from pit_wall_ai.engineer.engine import AlertRecord
from pit_wall_ai.engineer.memory_store import AlertMemory, listener

TRACK = SimpleNamespace(names={4: "Tornantino"}, geometry=None, track="magione")


def rec(t, rule, text):
    return AlertRecord(t, rule, text, "MEDIUM", {})


def test_topic_recall_quotes_only_the_alerts_of_that_topic():
    alerts = [
        rec(100.0, "fuel_low", "Gasolina para casi dos vueltas, ahorra"),
        rec(160.0, "lap_summary", "Vuelta 3, 1 24 9"),
        rec(220.0, "fuel_low", "Gasolina para vuelta y media, box"),
    ]
    r = R.recall(
        "¿Qué me dijiste de la gasolina?", alerts=alerts, history=[], track=TRACK, now=250.0
    )
    assert r is not None and r.found == 2
    assert "box" in r.text and "ahorra" in r.text and "Vuelta 3" not in r.text
    assert r.text.index("box") < r.text.index("ahorra")  # the latest first
    assert "sobre la gasolina" in r.text


def test_a_topic_with_nothing_on_record_says_so():
    r = R.recall("¿qué me avisaste de las banderas?", alerts=[], history=[], track=TRACK, now=1.0)
    assert (
        r is not None and r.found == 0 and "No tengo registrado" in r.text and "banderas" in r.text
    )


def test_without_corner_or_topic_the_router_keeps_it():
    assert (
        R.recall(
            "¿qué me dijiste?", alerts=[rec(1, "fuel_low", "x")], history=[], track=TRACK, now=2.0
        )
        is None
    )


def test_the_previous_session_is_searched_when_the_driver_asks_about_it(tmp_path):
    mem = AlertMemory(tmp_path / "m.jsonl")
    old = AlertMemory(tmp_path / "m.jsonl")
    old.run = "older-run"
    old.record(
        rule="braking_hint",
        text="Tornantino: frenas 7 metros más tarde",
        track="magione",
        car="cobra",
        session="practice",
    )
    old.record(
        rule="fuel_low",
        text="Gasolina para dos vueltas",
        track="monza",
        car="cobra",
        session="race",
    )
    prev = mem.previous("magione")
    assert [a.rule for a in prev] == ["braking_hint"]  # only that track, only another run
    r = R.recall(
        "¿qué me dijiste de la curva 4 la última vez?",
        alerts=[],
        history=[],
        track=TRACK,
        now=0.0,
        previous=prev,
    )
    assert r is not None and r.found == 1 and "sesión anterior" in r.text and "7 metros" in r.text
    r2 = R.recall(
        "¿qué me dijiste en la sesión anterior?",
        alerts=[],
        history=[],
        track=TRACK,
        now=0.0,
        previous=prev,
    )
    assert r2 is not None and "7 metros" in r2.text


def test_the_memory_file_is_trimmed_skips_damaged_lines_and_is_written_by_the_listener(tmp_path):
    path = tmp_path / "m.jsonl"
    mem = AlertMemory(path, max_records=60)
    on_alert = listener(mem)
    snap = SimpleNamespace(static=SimpleNamespace(track="magione", car_model="cobra"))
    state = SimpleNamespace(session="race")
    for i in range(120):
        on_alert(rec(float(i), "lap_summary", f"Vuelta {i}"), snap, state)
    path.write_text(path.read_text(encoding="utf-8") + "this is not json\n", encoding="utf-8")
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 121
    mem.trim()
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 60
    other = AlertMemory(path)  # another run: it sees the stored ones and survives the damaged line
    assert len(other.previous("magione")) > 0
    assert other.previous("magione")[0].session == "race"
    time.sleep(0)
