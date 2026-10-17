from types import SimpleNamespace

from pit_wall_ai.analysis.debrief import Debrief
from pit_wall_ai.analysis.debrief_speech import facts_sheet, local_summary, spoken_summary

FACTS = {
    "result": {"session": "race", "laps": 8, "position_start": 11, "position_end": 10},
    "pace": {
        "best_ms": 77737,
        "best_lap": 5,
        "valid_laps": 6,
        "ideal_ms": 77300,
        "ideal_gap_ms": 437,
    },
    "time_loss": {
        "corners": [
            {
                "corner": 7,
                "apex": 7,
                "name": "Curva 7",
                "mean_loss_s": 0.56,
                "laps": 7,
                "brake_earlier_m": None,
            }
        ]
    },
    "penalties": {"max_penalty_s": 10.0, "penalties": 1, "contacts": 3, "worst_contact": 30.0},
}


class FakeClient:
    def __init__(self, text):
        self.text = text

    def ask(self, request, urgent=False):
        assert "10" in str(request.context)  # the facts reach the model
        return SimpleNamespace(text=self.text)


def test_the_template_has_the_main_facts_in_four_sentences_at_most():
    t = local_summary(Debrief(facts=FACTS), "es")
    assert "posición 10" in t and "ganaste 1 puesto." in t and "1:17,737" in t
    assert "0,4 segundos de tu vuelta ideal" in t and "la curva 7" in t and "Curva 7" not in t
    assert "una penalización de 10 segundos y 3 contactos" in t
    assert len([s for s in t.split(". ") if s]) <= 4


def test_the_template_exists_in_english_and_german():
    assert "Race finished in position 10" in local_summary(Debrief(facts=FACTS), "en")
    assert "Platz 10" in local_summary(Debrief(facts=FACTS), "de")


def test_a_grounded_model_phrasing_is_used():
    d = Debrief(facts=FACTS)
    text, source = spoken_summary(
        d,
        None,
        FakeClient(
            "Terminaste décimo; tu mejor vuelta fue 1:17,737 y perdiste 0,56 s en la curva 7."
        ),
        lang="es",
    )
    assert source == "model" and "1:17,737" in text


def test_a_number_outside_the_facts_makes_it_fall_back_to_the_template():
    d = Debrief(facts=FACTS)
    text, source = spoken_summary(
        d, None, FakeClient("Tu mejor vuelta fue 1:15,000 y perdiste 3,2 s."), lang="es"
    )
    assert source == "local" and "1:17,737" in text


def test_without_a_model_the_template_is_used():
    assert spoken_summary(Debrief(facts=FACTS), None, None, lang="es")[1] == "local"
    assert spoken_summary(Debrief(), None, FakeClient("x"), lang="es")[1] == "local"


def test_the_facts_sheet_carries_the_lap_times_as_seconds_and_milliseconds():
    sheet = facts_sheet(Debrief(facts=FACTS))
    f = sheet.by_key("pace.best_s")
    assert 77.737 in f.accepts() and 77737.0 in f.accepts()
    assert sheet.value("penalties.count") == 1
