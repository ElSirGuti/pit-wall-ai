"""Pole, grid and result messages: detection on a real recording, wording pool and the model's guard."""

from __future__ import annotations

import random
from dataclasses import dataclass
from types import SimpleNamespace

from pit_wall_ai.analysis.offline import iter_snapshots
from pit_wall_ai.engineer import milestones as MS
from pit_wall_ai.events import LapCompleted
from pit_wall_ai.session.engine import SessionEngine

FIXTURE = "tests/fixtures/live_lfm_magione_session.pwrec.gz"


class Spk:
    busy = False

    def __init__(self):
        self.said = []

    def say(self, text, *, interrupt=False, meta=None):
        self.said.append((text, meta or {}))


def replay(announcer):
    det = MS.MilestoneDetector()
    found = []
    eng = SessionEngine()

    def hook(snap, state, tracker, events):
        for m in det.update(snap, state, events):
            found.append(m)
            announcer.announce(m)

    eng.add_sample_hook(hook)
    _st, snaps = iter_snapshots(FIXTURE)
    for s in snaps:
        eng.process(s)
    eng.flush()
    return found


def test_the_real_qualifying_and_race_give_the_grid_and_the_comeback():
    spk = Spk()
    ann = MS.MilestoneAnnouncer(spk, language="es", threaded=False, rng=random.Random(1))
    found = replay(ann)
    kinds = [(m.kind, m.situation) for m in found]
    assert kinds == [("quali_final", "last"), ("race_finished", "gained")]
    q, r = found
    assert q.facts == {"position": 18, "num_cars": 18}
    assert r.facts["start_position"] == 18 and r.facts["position"] == 9
    assert r.facts["places_gained"] == 9 and r.facts.get("started_last") is True
    texts = [t for t, _ in spk.said]
    assert "últimos" in texts[0] and "9" in texts[1]


@dataclass
class Lap:
    time_ms: int = 85000
    out_lap: bool = False


def sample(session="qualify", pos=1, speed=100.0, laps=0, total=0, cars=18):
    return SimpleNamespace(
        position=pos,
        speed_kmh=speed,
        completed_laps=laps,
        number_of_laps=total,
        static=SimpleNamespace(num_cars=cars),
    )


def state(session="qualify", status="live"):
    return SimpleNamespace(session=session, status=status)


def test_a_best_lap_that_leaves_the_car_first_is_a_provisional_pole_once():
    det = MS.MilestoneDetector()
    ev = [LapCompleted(0.0, Lap(), True, None)]
    out = det.update(sample(pos=1), state(), ev)
    assert [(m.kind, m.situation) for m in out] == [("quali_provisional", "pole")]
    assert det.update(sample(pos=1), state(), ev) == []  # not twice
    det2 = MS.MilestoneDetector()
    assert det2.update(sample(pos=3), state(), ev) == []  # best lap but not first


def test_leaving_the_qualifying_by_the_menu_ends_it_with_the_grid():
    det = MS.MilestoneDetector()
    det.update(sample(pos=1), state(), [])
    out = det.update(sample(pos=0), state(status="off"), [])
    assert [(m.kind, m.situation) for m in out] == [("quali_final", "pole")]
    assert det.update(sample(pos=0), state(status="off"), []) == []


def test_race_results_win_from_the_back_podium_and_lost():
    def race(start, end, cars=18):
        det = MS.MilestoneDetector()
        det.update(sample("race", start, speed=0, total=3, cars=cars), state("race"), [])
        det.update(sample("race", start, speed=50, total=3, cars=cars), state("race"), [])
        return det.update(
            sample("race", end, speed=120, laps=3, total=3, cars=cars), state("race"), []
        )[0]

    w = race(18, 1)
    assert w.situation == "win" and MS.pick_variant_key(w) == "win_from_back"
    assert race(10, 3).situation == "podium"
    assert race(5, 12).situation == "lost" and race(5, 12).facts["places_gained"] == -7
    assert race(7, 7).situation == "held"


def test_the_variants_do_not_repeat_the_last_one_and_fill_the_numbers():
    spk = Spk()
    ann = MS.MilestoneAnnouncer(spk, language="es", threaded=False, rng=random.Random(0))
    m = MS.Milestone("quali_final", "other", {"position": 7, "num_cars": 18})
    for _ in range(4):
        ann.announce(m)
    texts = [t for t, _ in spk.said]
    assert all("7" in t for t in texts)
    assert all(a != b for a, b in zip(texts, texts[1:], strict=False))


def test_the_model_text_is_used_only_if_every_number_is_a_fact():
    m = MS.Milestone(
        "race_finished", "gained", {"position": 5, "start_position": 18, "places_gained": 13}
    )
    assert MS.text_is_grounded("Saliste el 18 y llegaste en el 5.º, remontaste 13 puestos.", m)
    assert not MS.text_is_grounded("Llegaste 5.º a 2,3 segundos del cuarto.", m)

    class Client:
        def __init__(self, text):
            self.text = text

        def ask(self, request, *, urgent=False):
            return SimpleNamespace(text=self.text)

    good = MS.MilestoneAnnouncer(
        Spk(), client=Client("Del 18 al 5, ¡qué remontada!"), threaded=False, rng=random.Random(1)
    )
    good.announce(m)
    assert good.said[0][2] == "model"
    bad = MS.MilestoneAnnouncer(
        Spk(), client=Client("Quedaste 5.º a 14 segundos."), threaded=False, rng=random.Random(1)
    )
    bad.announce(m)
    assert bad.said[0][2] == "variant"


def test_quiet_mode_keeps_the_congratulation_unsaid():
    spk = Spk()
    ann = MS.MilestoneAnnouncer(spk, threaded=False, quiet=lambda: True)
    ann.announce(MS.Milestone("quali_final", "pole", {"position": 1, "num_cars": 18}))
    assert spk.said == []


def test_three_languages_have_the_same_situations():
    es = {(k, s) for k, d in MS.VARIANTS["es"].items() for s in d}
    for lang in ("en", "de"):
        assert {(k, s) for k, d in MS.VARIANTS[lang].items() for s in d} == es
