from dataclasses import replace
from types import SimpleNamespace

from pit_wall_ai.analysis.debrief import Debrief
from pit_wall_ai.analysis.debrief_live import LiveDebrief
from pit_wall_ai.analysis.laps import LapRecord
from pit_wall_ai.analysis.sessions_log import SessionStore, attach_history, summary_of
from pit_wall_ai.config import AppConfig
from pit_wall_ai.engineer.milestones import Milestone
from pit_wall_ai.telemetry.shm_structs import GameStatus
from pit_wall_ai.telemetry.snapshot import Snapshot, StaticInfo

STATIC = StaticInfo(track="magione", car_model="maserati", sm_version="1.7")


def snap(t, **kw):
    base = Snapshot(
        t=t, source="test", packet_id=int(t * 10), static=STATIC, status=int(GameStatus.LIVE)
    )
    return replace(base, **kw)


def lap(n, ms):
    return LapRecord(
        number=n,
        time_ms=ms,
        sectors_ms=(ms // 2, ms - ms // 2),
        coverage=1.0,
        compound="H",
        fuel_start=20 - n,
        fuel_end=19 - n,
    )


class Speaker:
    def __init__(self):
        self.said = []

    def say(self, text, interrupt=False, meta=None):
        self.said.append((text, meta))


def make(tmp_path, *, quiet=False):
    cfg = AppConfig.model_validate(
        {
            "engineer": {
                "debrief": {"dir": str(tmp_path / "d"), "sessions_path": str(tmp_path / "s.jsonl")}
            }
        }
    )
    sp = Speaker()
    ld = LiveDebrief(cfg, sp, track_analysis=None, quiet=lambda: quiet, threaded=False, delay_s=0)
    return ld, sp


def feed(ld, laps):
    tracker = SimpleNamespace(laps=laps)
    for i in range(30):
        ld.on_sample(snap(i * 0.1, position=3, speed_kmh=120.0, session=2), None, tracker, [])
    return tracker


def test_a_finished_race_writes_the_report_keeps_the_session_and_says_it(tmp_path):
    ld, sp = make(tmp_path)
    tracker = feed(ld, [lap(2, 78000), lap(3, 77800), lap(4, 78400), lap(5, 77900)])
    ld.on_milestone(Milestone("race_finished", "held"), snap(4.0), None, tracker)
    assert (
        ld.reports
        and ld.reports[0].exists()
        and "Resumen de la sesión" in ld.reports[0].read_text(encoding="utf-8")
    )
    assert sp.said and sp.said[0][1]["rule"] == "debrief" and "1:17,800" in sp.said[0][0]
    assert SessionStore(tmp_path / "s.jsonl").records()[0]["best_ms"] == 77800


def test_quiet_mode_keeps_the_report_but_does_not_speak(tmp_path):
    ld, sp = make(tmp_path, quiet=True)
    tracker = feed(ld, [lap(2, 78000), lap(3, 77800)])
    ld.on_milestone(Milestone("race_finished", "held"), snap(4.0), None, tracker)
    assert ld.reports and not sp.said


def test_other_milestones_do_not_trigger_and_one_session_triggers_once(tmp_path):
    ld, sp = make(tmp_path)
    tracker = feed(ld, [lap(2, 78000), lap(3, 77800)])
    ld.on_milestone(Milestone("quali_provisional", "pole"), snap(4.0), None, tracker)
    assert not ld.reports
    ld.on_milestone(Milestone("race_finished", "win"), snap(4.0), None, tracker)
    ld.on_milestone(Milestone("race_finished", "win"), snap(4.0), None, tracker)
    assert len(ld.reports) == 1 and len(sp.said) == 1


def test_a_qualifying_that_ends_with_the_next_session_still_has_its_laps(tmp_path):
    ld, sp = make(tmp_path)
    feed(ld, [lap(2, 78000), lap(3, 77800)])
    # the engine clears the tracker before the hooks of the sample that starts the next session
    empty = SimpleNamespace(laps=[])
    ld.on_milestone(Milestone("quali_final", "other"), snap(9.0), None, empty)
    assert ld.reports and sp.said


def test_on_request_it_answers_without_storing_the_unfinished_session(tmp_path):
    ld, _ = make(tmp_path)
    assert "Todavía no hay vueltas" in ld.request()
    feed(ld, [lap(2, 78000), lap(3, 77800), lap(4, 78100)])
    text = ld.request()
    assert "1:17,800" in text and not SessionStore(tmp_path / "s.jsonl").records()


def _debrief(best, kind="race", compounds=("H",)):
    return Debrief(
        facts={
            "result": {
                "session": kind,
                "track": "magione",
                "layout": "",
                "car": "maserati",
                "laps": 8,
            },
            "pace": {"best_ms": best, "valid_laps": 6, "consistency_ms": 800},
            "stints": {"stints": [{"compound": c, "laps": 4} for c in compounds]},
        }
    )


def test_against_last_time_needs_the_same_track_car_and_kind(tmp_path):
    from datetime import datetime

    store = SessionStore(tmp_path / "h.jsonl")
    attach_history(_debrief(78000), store, when=datetime(2026, 10, 1, 12, 0))
    attach_history(_debrief(79000, kind="practice"), store, when=datetime(2026, 10, 2, 12, 0))
    d = _debrief(77500)
    attach_history(d, store, when=datetime(2026, 10, 17, 12, 0))
    assert (
        d.facts["vs_last"]["best_delta_ms"] == -500
        and d.facts["vs_last"]["previous_date"] == "2026-10-01"
    )
    other = _debrief(77500, compounds=("S",))
    attach_history(other, store, save=False, when=datetime(2026, 10, 18, 12, 0))
    assert other.facts["vs_last"]["same_compound"] is False
    assert summary_of(Debrief()) is None
