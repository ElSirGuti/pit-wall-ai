from dataclasses import replace

from pit_wall_ai.config import CspFlagsRule as CspFlagsCfg
from pit_wall_ai.config import RivalCloseRule as RivalCloseCfg
from pit_wall_ai.config import RivalPitRule as RivalPitCfg
from pit_wall_ai.engineer.rules import CspFlagsRule, RivalCloseRule, RivalPitRule, RuleContext
from pit_wall_ai.telemetry.rivals import RivalTracker
from pit_wall_ai.telemetry.snapshot import Snapshot


def board(
    *,
    kind=3,
    behind_off=0.46,
    ahead_off=0.55,
    pace_me=1.0,
    seconds=60,
    extra=None,
    ahead_pit=0,
    me_finished=0,
    **payload,
):
    """A race in which I (car 0) am P2, car 1 is P1 ahead and car 2 is P3 behind; constant speeds."""
    tr = RivalTracker(track_length_m=5000)
    for k in range(int(seconds * 2) + 1):
        t = k * 0.5
        me = 0.50 + t * pace_me / 100.0
        cars = [
            {"i": 0, "n": "Yo", "p": 2, "l": 0, "s": me % 1.0, "v": 180, "fin": me_finished},
            {
                "i": 1,
                "n": "Delante",
                "p": 1,
                "l": 0,
                "s": (ahead_off + t / 100.0) % 1.0,
                "v": 180,
                "pit": ahead_pit if t > seconds - 1 else 0,
            },
            {"i": 2, "n": "Detras", "p": 3, "l": 0, "s": (behind_off + t / 100.0) % 1.0, "v": 180},
        ]
        msg = {"t": t, "me": 0, "cars": cars, "session": {"type": kind, "laps": 10}}
        msg.update(payload)
        tr.update(msg)
    return tr.board()


def ctx(b, lang="es"):
    snap = Snapshot(t=100.0, source="test", packet_id=1)
    return RuleContext(snap, None, None, [], lang, b)


def test_a_car_closing_in_behind_is_announced_once():
    # the car behind is 0.8 s back and I am slowing: it gains on me
    b = board(behind_off=0.492, pace_me=0.99)
    rule = RivalCloseRule(RivalCloseCfg(enabled=True))
    got = rule.evaluate(ctx(b))
    assert got and "de atrás" in got[0].text and "décimas" in got[0].text
    assert rule.evaluate(ctx(b)) == []  # the cooldown holds it


def test_no_alert_when_the_gap_is_large_or_it_is_not_a_race():
    rule = RivalCloseRule(RivalCloseCfg(enabled=True))
    assert rule.evaluate(ctx(board(behind_off=0.40, pace_me=0.99))) == []
    assert rule.evaluate(ctx(board(behind_off=0.492, pace_me=0.99, kind=1))) == []
    assert rule.evaluate(ctx(None)) == []


def test_the_car_ahead_going_into_the_pits_is_announced():
    rule = RivalPitRule(RivalPitCfg(enabled=True))
    assert rule.evaluate(ctx(board(ahead_pit=0))) == []
    got = rule.evaluate(ctx(board(ahead_pit=1)))
    assert got and "delante" in got[0].text


def test_a_blue_flag_names_the_car_it_is_for():
    rule = CspFlagsRule(CspFlagsCfg(enabled=True))
    assert rule.evaluate(ctx(board(flag=0))) == []
    got = rule.evaluate(ctx(board(flag=12, flag_cause=2)))
    assert got and "Detras" in got[0].text and "azul" in got[0].text
    got = CspFlagsRule(CspFlagsCfg(enabled=True)).evaluate(ctx(board(flag=2)))
    assert got and "amarilla" in got[0].text


def test_the_end_of_a_timed_race_comes_from_the_bridge():
    from pit_wall_ai.engineer.milestones import MilestoneDetector
    from pit_wall_ai.session.detector import SessionState

    det = MilestoneDetector()
    st = SessionState(status="live", session="race")
    base = Snapshot(t=1.0, source="test", packet_id=1)
    moving = replace(base, speed_kmh=120.0, position=2, number_of_laps=0, completed_laps=7)
    assert det.update(moving, st, [], board(me_finished=0)) == []
    done = det.update(moving, st, [], board(me_finished=1))
    assert done and done[0].kind == "race_finished"
