"""Alert rules, the delivery policy (calm moments, interruption, TTL) and the speech queue."""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path

from pit_wall_ai.analysis.laps import LapRecord, LapTracker
from pit_wall_ai.config import EngineerConfig
from pit_wall_ai.engineer import messages as M
from pit_wall_ai.engineer.alerts import Priority
from pit_wall_ai.engineer.engine import CalmDetector, Engineer
from pit_wall_ai.engineer.rules import RuleContext, build_rules
from pit_wall_ai.events import LapCompleted, SessionChanged
from pit_wall_ai.session.detector import SessionState
from pit_wall_ai.session.engine import SessionEngine
from pit_wall_ai.telemetry.recorder import load_native, snapshot_from_flat
from pit_wall_ai.telemetry.snapshot import StaticInfo
from pit_wall_ai.voice.speech import SpeechOutput
from pit_wall_ai.voice.tts import ConsoleTts, TtsUnavailable

from .helpers import snap

FIXTURES = Path(__file__).parent / "fixtures"
STATIC = StaticInfo(
    car_model="mx5", track="magione", tyre_radius=(0.3, 0.3, 0.3, 0.3), sector_count=2
)


_QUIET = io.StringIO()  # the console voice prints here instead of the test output


def cfg(**kw) -> EngineerConfig:
    return EngineerConfig.model_validate(kw)


def S(t=0.0, **kw):
    base = dict(static=STATIC, speed_kmh=100.0, status=2, fuel=30.0)
    base.update(kw)
    return snap(t=t, **base)


def state(session="practice", phase="flying"):
    return SessionState(session=session, status="live", phase=phase)


def run_rule(rule_id, samples, *, config=None, tracker=None, events_for=None, lang="es", st=None):
    rules = {r.id: r for r in build_rules(config or cfg())}
    rule = rules[rule_id]
    out = []
    for i, s in enumerate(samples):
        ev = events_for(i) if events_for else []
        out += rule.evaluate(RuleContext(s, st or state(), tracker or LapTracker(), ev, lang))
    return out


def wheels_kmh(speed, ratio):
    w = speed / 3.6 / 0.3 * ratio
    return (w, w, w, w)


# ── rules ────────────────────────────────────────────────────────────────────────────────────────────
def test_lockup_needs_braking_speed_and_a_sustained_slow_wheel():
    locked = (60.0, 120.0, 120.0, 120.0)  # FL at 50 % of the car speed, others free
    # 120 rad/s * 0.3 m * 3.6 = 129.6 km/h
    base = dict(speed_kmh=130.0, brake=0.8)
    ok = [S(t=i * 0.1, wheel_angular_speed=locked, **base) for i in range(6)]
    alerts = run_rule("lockup", ok)
    assert len(alerts) == 1
    a = alerts[0]
    assert (
        a.priority == Priority.HIGH
        and a.rule == "lockup"
        and "izquierdo" in a.text
        and "delantero" in a.text
    )
    assert a.values["wheels"] == "FL" and a.values["brake"] == 0.8 and a.values["speed_kmh"] == 130
    # not braking, too slow, or only a blip: nothing
    assert (
        run_rule(
            "lockup",
            [S(t=i * 0.1, wheel_angular_speed=locked, speed_kmh=130, brake=0.1) for i in range(6)],
        )
        == []
    )
    assert (
        run_rule(
            "lockup",
            [S(t=i * 0.1, wheel_angular_speed=locked, speed_kmh=30, brake=0.8) for i in range(6)],
        )
        == []
    )
    assert (
        run_rule("lockup", [S(t=i * 0.1, wheel_angular_speed=locked, **base) for i in range(2)])
        == []
    )


def test_lockup_both_fronts_are_reported_as_the_axle_and_cooldown_holds():
    both = (50.0, 50.0, 120.0, 120.0)
    samples = [
        S(t=i * 0.1, wheel_angular_speed=both, speed_kmh=130.0, brake=0.9) for i in range(200)
    ]
    alerts = run_rule("lockup", samples)
    assert alerts[0].text == "Bloqueo delanteros, suelta freno"
    assert len(alerts) == 2  # 20 s of lock, 12 s cooldown
    en = run_rule("lockup", samples[:8], lang="en")
    assert en[0].text == "front axle lock-up, ease the brake"


def test_spin_is_critical_and_uses_the_body_velocity():
    spinning = [
        S(t=i * 0.05, speed_kmh=90.0, local_velocity=(25.0, 0.0, 8.0)) for i in range(8)
    ]  # 72 deg
    a = run_rule("spin", spinning)
    assert len(a) == 1 and a[0].priority == Priority.CRITICAL and a[0].interrupts
    assert a[0].values["slip_deg"] == 72 and a[0].text == "Trompo, embrague y freno"
    normal = [S(t=i * 0.05, speed_kmh=90.0, local_velocity=(1.0, 0.0, 25.0)) for i in range(8)]
    assert run_rule("spin", normal) == []
    slow = [S(t=i * 0.05, speed_kmh=10.0, local_velocity=(25.0, 0.0, 8.0)) for i in range(8)]
    assert run_rule("spin", slow) == []


def test_offtrack_needs_three_wheels_out_for_a_second():
    out = [S(t=i * 0.25, tyres_out=3) for i in range(6)]
    assert len(run_rule("offtrack", out)) == 1
    assert run_rule("offtrack", [S(t=i * 0.25, tyres_out=2) for i in range(10)]) == []
    assert run_rule("offtrack", [S(t=i * 0.25, tyres_out=3) for i in range(2)]) == []


def tracker_with_fuel(per_lap, n=3):
    tr = LapTracker()
    for k in range(n):
        tr.laps.append(
            LapRecord(
                number=k + 2,
                time_ms=90000,
                fuel_start=40.0 - k * per_lap,
                fuel_end=40.0 - (k + 1) * per_lap,
                coverage=1.0,
            )
        )
    return tr


def test_fuel_warns_below_two_laps_and_is_critical_below_one():
    tr = tracker_with_fuel(2.0)
    warn = run_rule("fuel_low", [S(fuel=3.5)], tracker=tr)
    assert (
        warn[0].priority == Priority.HIGH
        and warn[0].text == "Gasolina para casi dos vueltas, ahorra"
    )
    assert warn[0].values["laps_left"] == 1.8 and warn[0].values["per_lap"] == 2.0
    crit = run_rule("fuel_low", [S(fuel=1.4)], tracker=tr)
    assert crit[0].priority == Priority.CRITICAL and "box" in crit[0].text
    assert run_rule("fuel_low", [S(fuel=3.0 + 4.0)], tracker=tr) == []  # 3.5 laps left
    assert (
        run_rule("fuel_low", [S(fuel=3.0)], tracker=LapTracker()) == []
    )  # no consumption data yet
    plural = run_rule(
        "fuel_low", [S(fuel=5.5)], tracker=tracker_with_fuel(3.0)
    )  # 1.83 laps -> "1 vuelta"
    assert plural[0].text.startswith("Gasolina para casi dos vueltas")
    two = run_rule("fuel_low", [S(fuel=6.0)], tracker=tracker_with_fuel(3.0))  # exactly 2.0
    assert two[0].text.startswith("Gasolina para dos vueltas")


def test_tyre_temperature_hysteresis_and_axle_naming():
    hot = (120.0, 118.0, 90.0, 90.0)
    cold = (100.0, 100.0, 90.0, 90.0)
    seq = [S(t=i, tyre_core_temp=hot) for i in range(5)]  # 5 s hot
    a = run_rule("tyre_hot", seq)
    assert (
        len(a) == 1
        and a[0].text == "Delanteros calientes, levanta"
        and a[0].values["wheels"] == "FL,FR"
    )
    # still hot afterwards: not re-armed, no second warning even after the cooldown
    again = run_rule("tyre_hot", seq + [S(t=200 + i, tyre_core_temp=hot) for i in range(6)])
    assert len(again) == 1
    # cools below the re-arm temperature, then heats again: warns again
    cycle = (
        seq
        + [S(t=100, tyre_core_temp=(95.0,) * 4)]
        + [S(t=300 + i, tyre_core_temp=hot) for i in range(5)]
    )
    assert len(run_rule("tyre_hot", cycle)) == 2
    assert run_rule("tyre_hot", [S(t=i, tyre_core_temp=cold) for i in range(10)]) == []


def test_pressure_high_and_puncture():
    high = run_rule("tyre_pressure", [S(t=0.0, tyre_pressure=(41.0, 30.0, 30.0, 30.0))])
    assert high[0].priority == Priority.HIGH and "delantero izquierdo" in high[0].text
    samples = [S(t=i * 0.5, tyre_pressure=(28.0, 28.0, 28.0, 28.0)) for i in range(4)]
    samples += [S(t=2.0 + i * 0.5, tyre_pressure=(28.0, 28.0, 21.0, 28.0)) for i in range(3)]
    p = run_rule("tyre_pressure", samples)
    assert (
        p[0].priority == Priority.CRITICAL
        and "trasero izquierdo" in p[0].text
        and p[0].values["peak_psi"] == 28.0
    )
    slow_cool = [
        S(t=i * 2.0, tyre_pressure=(28.0 - i * 0.5,) * 4) for i in range(8)
    ]  # 0.25 psi/s: not a puncture
    assert run_rule("tyre_pressure", slow_cool) == []


def test_brake_temperature_is_ignored_when_the_sensor_does_not_move():
    flat = [S(t=i * 0.5, brake_temp=(15.0, 15.0, 15.0, 15.0), speed_kmh=150) for i in range(200)]
    assert run_rule("brake_hot", flat) == []
    # a sensor that moves and then exceeds the limit for 2 s
    moving = [S(t=i * 0.5, brake_temp=(100.0 + i * 8.0,) * 4, speed_kmh=150) for i in range(120)]
    a = run_rule("brake_hot", moving)
    assert len(a) == 1 and a[0].text == "Frenos calientes, frena antes"
    # a flat sensor above the limit is still "dead": no alert
    assert (
        run_rule(
            "brake_hot", [S(t=i * 0.5, brake_temp=(900.0,) * 4, speed_kmh=150) for i in range(20)]
        )
        == []
    )


def test_penalty_when_the_time_rises():
    seq = [
        S(t=0.0, penalty_time=0.0),
        S(t=1.0, penalty_time=0.0),
        S(t=2.0, penalty_time=5.0),
        S(t=3.0, penalty_time=5.0),
    ]
    a = run_rule("penalty", seq)
    assert len(a) == 1 and a[0].text == "Penalización de 5 segundos, cumple"


def test_flags_and_damage_are_off_by_default_and_work_when_enabled():
    flagged = [S(t=0.0, flags=0), S(t=1.0, flags=2), S(t=2.0, flags=2), S(t=15.0, flags=3)]
    assert "flags" not in {r.id for r in build_rules(cfg())}
    a = run_rule("flags", flagged, config=cfg(rules={"flags": {"enabled": True}}))
    assert [x.text for x in a] == ["Bandera amarilla, levanta", "Bandera negra, box"]
    assert a[1].priority == Priority.CRITICAL
    dmg = [S(t=0.0, car_damage=(0, 0, 0, 0, 0)), S(t=1.0, car_damage=(0, 9.0, 0, 0, 0))]
    d = run_rule("damage", dmg, config=cfg(rules={"damage": {"enabled": True}}))
    assert len(d) == 1 and d[0].values["increase"] == 9.0


def lap_event(number, ms, *, best=False, delta=None, out=False, in_lap=False):
    lap = LapRecord(number=number, time_ms=ms, out_lap=out, in_lap=in_lap, coverage=1.0)
    return LapCompleted(0.0, lap, best, delta)


def test_lap_summary_texts():
    def say(e, lang="es"):
        out = run_rule("lap_summary", [S()], events_for=lambda i: [e], lang=lang)
        return out[0].text if out else None

    assert say(lap_event(3, 84913, best=True, delta=-531)) == "Mejor vuelta, 1 24 9"
    assert say(lap_event(4, 85444, delta=-531 + 531)) == "Vuelta 4, 1 25 4, mismo ritmo"
    assert say(lap_event(4, 86400, delta=900)) == "Vuelta 4, 1 26 4, más 9 décimas"
    assert say(lap_event(4, 95000, delta=10_100)) == "Vuelta 4, 1 35 0, más 10 segundos"
    assert say(lap_event(4, 94700, delta=9_900)) == "Vuelta 4, 1 34 7, más 10 segundos"
    assert say(lap_event(4, 86700, delta=1_900)) == "Vuelta 4, 1 26 7, más 19 décimas"
    assert say(lap_event(4, 85000, delta=-1300)) == "Vuelta 4, 1 25 0, menos 13 décimas"
    assert say(lap_event(2, 90000, delta=None)) == "Vuelta 2, 1 30 0"
    assert say(lap_event(4, 86400, delta=900), "en") == "Lap 4, 1 26 4, 9 tenths down"
    assert say(lap_event(1, 137000, out=True)) is None  # an out lap is not summarised
    assert M.lap_time_words(61234) == "1 1 2" and M.lap_time_words(118500) == "1 58 5"


# ── delivery ─────────────────────────────────────────────────────────────────────────────────────────
class FakeSpeaker:
    def __init__(self):
        self.said = []
        self.busy = False

    def say(self, text, *, interrupt=False, meta=None):
        self.said.append((text, interrupt, (meta or {}).get("rule")))


def drive(eng, samples, session="practice", phase="flying", tracker=None, events=None):
    tracker = tracker or LapTracker()
    fired = []
    for i, s in enumerate(samples):
        ev = events(i) if events else []
        fired += eng.on_sample(s, state(session, phase), tracker, ev)
    return fired


def test_calm_detector_needs_free_brake_and_steering_for_a_moment():
    cd = CalmDetector(cfg().calm)
    assert not cd.update(S(t=0.0, brake=0.0, steer_angle=0.0))  # just started: not yet 0.3 s
    assert cd.update(S(t=0.4, brake=0.0, steer_angle=0.0))
    assert not cd.update(S(t=0.5, brake=0.6, steer_angle=0.0))  # braking
    assert not cd.update(S(t=0.6, brake=0.0, steer_angle=0.5))  # cornering
    cd.update(S(t=2.0, brake=1.0, steer_angle=0.5, speed_kmh=0.0))
    assert cd.update(S(t=2.4, brake=1.0, steer_angle=0.5, speed_kmh=0.0))  # stopped counts as calm


LOCK = dict(wheel_angular_speed=(50.0, 120.0, 120.0, 120.0), speed_kmh=130.0)


def test_a_non_critical_alert_waits_for_the_straight_and_a_critical_one_does_not():
    sp = FakeSpeaker()
    eng = Engineer(cfg(startup_quiet_s=0), sp)
    # braking hard with a locked wheel, steering in: the lock-up fires but nothing is said
    braking = [S(t=i * 0.1, brake=0.9, steer_angle=0.4, **LOCK) for i in range(6)]
    fired = drive(eng, braking)
    assert [a.rule for a in fired] == ["lockup"] and sp.said == []
    assert eng.pending
    # brake and steering released: it is said after the calm hold, once, without interrupting
    straight = [S(t=0.6 + i * 0.1, brake=0.0, steer_angle=0.0, speed_kmh=140.0) for i in range(8)]
    drive(eng, straight)
    assert sp.said == [("Bloqueo delantero izquierdo, suelta freno", False, "lockup")]
    # a spin in the middle of a corner goes out at once and interrupts
    spin = [
        S(
            t=5.0 + i * 0.05,
            brake=0.9,
            steer_angle=0.8,
            speed_kmh=90.0,
            local_velocity=(25.0, 0.0, 8.0),
        )
        for i in range(6)
    ]
    drive(eng, spin)
    assert sp.said[-1] == ("Trompo, embrague y freno", True, "spin")


def test_stale_alerts_are_dropped_not_said_late(caplog):
    sp = FakeSpeaker()
    eng = Engineer(cfg(startup_quiet_s=0), sp)
    with caplog.at_level(logging.INFO, logger="pit_wall_ai.engineer.alerts"):
        drive(eng, [S(t=i * 0.1, brake=0.9, steer_angle=0.4, **LOCK) for i in range(6)])
        late = [
            S(t=20.0 + i * 0.1, brake=0.0, steer_angle=0.0) for i in range(8)
        ]  # 20 s later, ttl is 6 s
        drive(eng, late)
    assert sp.said == [] and eng.stats.dropped_expired == 1
    dropped = [r for r in caplog.records if r.getMessage() == "alert dropped"]
    assert dropped and dropped[0].reason == "expired" and dropped[0].rule == "lockup"


def test_one_message_at_a_time_with_a_minimum_gap_and_priority_order():
    sp = FakeSpeaker()
    eng = Engineer(cfg(startup_quiet_s=0), sp)
    both = dict(wheel_angular_speed=(50.0, 120.0, 120.0, 120.0), speed_kmh=130.0, tyres_out=3)
    drive(eng, [S(t=i * 0.1, brake=0.9, steer_angle=0.4, **both) for i in range(12)])
    assert len(eng.pending) == 2  # lock-up and off-track, both HIGH
    calm = [S(t=1.3 + i * 0.1, brake=0.0, steer_angle=0.0, speed_kmh=140.0) for i in range(40)]
    drive(eng, calm)
    assert [x[2] for x in sp.said] == ["lockup", "offtrack"] or [x[2] for x in sp.said] == [
        "offtrack",
        "lockup",
    ]
    assert len(sp.said) == 2


def test_busy_voice_defers_the_next_message():
    sp = FakeSpeaker()
    sp.busy = True
    eng = Engineer(cfg(startup_quiet_s=0), sp)
    drive(eng, [S(t=i * 0.1, brake=0.9, steer_angle=0.4, **LOCK) for i in range(6)])
    drive(eng, [S(t=0.6 + i * 0.1, brake=0.0, steer_angle=0.0) for i in range(8)])
    assert sp.said == []
    sp.busy = False
    drive(eng, [S(t=1.5 + i * 0.1, brake=0.0, steer_angle=0.0) for i in range(3)])
    assert len(sp.said) == 1


def test_race_policy_keeps_only_critical_and_the_allowed_rules(caplog):
    sp = FakeSpeaker()
    eng = Engineer(cfg(startup_quiet_s=0), sp)
    tr = tracker_with_fuel(2.0)
    with caplog.at_level(logging.INFO, logger="pit_wall_ai.engineer.alerts"):
        drive(
            eng,
            [S(t=i * 0.1, brake=0.9, steer_angle=0.4, fuel=3.5, **LOCK) for i in range(6)],
            session="race",
            tracker=tr,
        )
        drive(
            eng,
            [S(t=0.6 + i * 0.1, brake=0.0, steer_angle=0.0, fuel=3.5) for i in range(8)],
            session="race",
            tracker=tr,
        )
    assert [x[2] for x in sp.said] == ["fuel_low"]  # lock-up is muted in a race
    assert eng.stats.dropped_policy == 1
    fired = [r for r in caplog.records if r.getMessage() == "alert fired"]
    assert {r.rule for r in fired} == {"lockup", "fuel_low"}
    assert all(hasattr(r, "key") and hasattr(r, "priority") and hasattr(r, "text") for r in fired)
    lock = next(r for r in fired if r.rule == "lockup")
    assert (
        lock.wheels == "FL" and lock.speed_kmh == 130 and lock.session == "race"
    )  # the values are logged


def test_qualifying_mutes_low_priority_and_practice_allows_it():
    off = cfg(startup_quiet_s=0, rules={"wheelspin": {"enabled": True}})
    spin_state = dict(
        speed_kmh=60.0, gas=1.0, wheel_angular_speed=(66.0, 66.0, 110.0, 110.0)
    )  # rear 1.8x
    for session, expected in (("qualify", 0), ("practice", 1)):
        sp = FakeSpeaker()
        eng = Engineer(off, sp)
        drive(eng, [S(t=i * 0.1, **spin_state) for i in range(8)], session=session)
        drive(eng, [S(t=0.8 + i * 0.1, speed_kmh=60.0) for i in range(8)], session=session)
        assert len(sp.said) == expected, session


def test_nothing_is_said_in_the_menu_paused_or_right_after_a_session_starts():
    sp = FakeSpeaker()
    eng = Engineer(cfg(startup_quiet_s=5.0), sp)
    lock = [S(t=i * 0.1, brake=0.9, steer_angle=0.4, **LOCK) for i in range(6)]
    drive(eng, lock, phase="paused")
    assert eng.stats.dropped_muted == 1
    changed = lambda i: [SessionChanged(0.0, "kind", "race", "magione", "mx5")] if i == 0 else []  # noqa: E731
    drive(
        eng,
        [S(t=100 + i * 0.1, brake=0.9, steer_angle=0.4, **LOCK) for i in range(6)],
        events=changed,
    )
    assert sp.said == [] and eng.stats.dropped_muted == 2  # inside the 5 s of quiet
    assert eng.pending == {}


def test_a_failing_rule_does_not_silence_the_others(caplog):
    class Boom:
        id = "boom"
        _last = {}

        def evaluate(self, ctx):
            raise RuntimeError("bug")

    sp = FakeSpeaker()
    rules = [Boom(), *build_rules(cfg(startup_quiet_s=0))]
    eng = Engineer(cfg(startup_quiet_s=0), sp, rules=rules)
    with caplog.at_level(logging.ERROR):
        fired = drive(eng, [S(t=i * 0.1, brake=0.9, steer_angle=0.4, **LOCK) for i in range(6)])
    assert [a.rule for a in fired] == ["lockup"]
    assert any("rule failed" in r.getMessage() for r in caplog.records)


def test_end_to_end_software_latency_from_the_sample_to_the_voice_call():
    tts = ConsoleTts(stream=_QUIET)
    speech = SpeechOutput(lambda: tts)
    assert speech.start()
    eng_cfg = cfg(startup_quiet_s=0)
    engine = SessionEngine()
    eng = Engineer(eng_cfg, speech)
    engine.add_sample_hook(lambda s, st, tr, ev: eng.on_sample(s, st, tr, ev))
    t0 = time.perf_counter()
    for i in range(
        30
    ):  # 1.5 s of game time processed as fast as possible; the spin is sustained from i=0
        s = S(t=i * 0.05, speed_kmh=90.0, local_velocity=(25.0, 0.0, 8.0), status=2)
        engine.process(s)
    deadline = time.perf_counter() + 2
    while time.perf_counter() < deadline and not speech.records:
        time.sleep(0.001)
    speech.stop()
    assert speech.records, "the spin alert never reached the voice"
    rec = speech.records[0]
    assert rec.interrupt and rec.text.startswith("Trompo")
    assert (rec.called_t - t0) * 1000 < 100  # first sample to the backend call, ms (30 samples)
    assert rec.queue_ms < 20


# ── speech queue ─────────────────────────────────────────────────────────────────────────────────────
def test_speech_plays_in_order_and_a_critical_message_cuts_and_clears_the_queue():
    tts = ConsoleTts(speaking_for_s=0.15, stream=_QUIET)
    speech = SpeechOutput(lambda: tts)
    assert speech.start()
    speech.say("uno")
    speech.say("dos")
    speech.say("tres")
    time.sleep(0.05)  # "uno" is being said
    speech.say("CRITICO", interrupt=True)  # drops "dos" and "tres"
    deadline = time.perf_counter() + 3
    while time.perf_counter() < deadline and speech.busy:
        time.sleep(0.01)
    speech.stop()
    assert tts.spoken == ["uno", "CRITICO"]
    assert speech.dropped == 2
    assert [r.text for r in speech.records] == ["uno", "CRITICO"]
    assert all(r.start_ms is not None for r in speech.records)


def test_a_missing_voice_is_reported_and_does_not_raise():
    class Broken:
        name = "broken"

        def open(self):
            raise TtsUnavailable("no pywin32")

    speech = SpeechOutput(lambda: Broken())
    assert speech.start() is False and "no pywin32" in speech.error
    rec = speech.say("hola")  # still safe to call
    assert rec.text == "hola"
    speech.stop()


def test_a_backend_error_while_speaking_does_not_kill_the_voice():
    class Flaky(ConsoleTts):
        def speak(self, text, *, interrupt=False):
            if text == "malo":
                raise RuntimeError("sapi hiccup")
            super().speak(text, interrupt=interrupt)

    tts = Flaky(stream=_QUIET)
    speech = SpeechOutput(lambda: tts)
    assert speech.start()
    speech.say("malo")
    speech.say("bueno")
    deadline = time.perf_counter() + 2
    while time.perf_counter() < deadline and speech.busy:
        time.sleep(0.01)
    speech.stop()
    assert tts.spoken == ["bueno"]


# ── a real race ──────────────────────────────────────────────────────────────────────────────────────
def test_the_real_race_window_speaks_the_spin_and_the_laps_and_nothing_else(caplog):
    rec = load_native(FIXTURES / "live_lfm_magione_race_window.pwrec.gz")
    sp = FakeSpeaker()
    eng = Engineer(cfg(), sp)
    session = SessionEngine()
    session.add_sample_hook(lambda s, st, tr, ev: eng.on_sample(s, st, tr, ev))
    with caplog.at_level(logging.INFO, logger="pit_wall_ai.engineer.alerts"):
        for i, row in enumerate(rec.rows):
            session.process(snapshot_from_flat(row, rec.statics[i], t=rec.times[i]))
    rules_said = [x[2] for x in sp.said]
    assert (
        rules_said.count("spin") == 1 and sp.said[rules_said.index("spin")][1] is True
    )  # interrupts
    assert [x[0] for x in sp.said if x[2] == "lap_summary"] == [
        "Mejor vuelta, 1 36 3",
        "Mejor vuelta, 1 25 4",
        "Mejor vuelta, 1 24 9",
    ]
    assert set(rules_said) <= {"spin", "lap_summary", "fuel_low"}  # conservative: no spam in a race
    assert len(sp.said) <= 6
    fired = {r.rule for r in caplog.records if r.getMessage() == "alert fired"}
    assert {"spin", "lap_summary"} <= fired
    dropped = [r for r in caplog.records if r.getMessage() == "alert dropped"]
    assert any(r.rule == "offtrack" and r.reason == "policy:race" for r in dropped)


def test_laps_of_fuel_are_said_the_way_a_person_says_them():
    es = lambda r: M.laps_phrase("es", r)  # noqa: E731
    assert (
        es(0.1) == "menos de media vuelta"
        and es(0.5) == "media vuelta"
        and es(0.8) == "casi una vuelta"
    )
    assert es(1.0) == "una vuelta" and es(1.5) == "vuelta y media" and es(1.9) == "casi dos vueltas"
    assert (
        es(2.0) == "dos vueltas" and es(2.4) == "dos vueltas" and es(2.6) == "dos vueltas y media"
    )
    assert es(12.3) == "12 vueltas"
    assert (
        M.laps_phrase("en", 1.9) == "almost two laps"
        and M.laps_phrase("en", 1.5) == "a lap and a half"
    )
