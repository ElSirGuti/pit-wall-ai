"""'No molestes ahorita': spoken command, the engineer's quiet mode, and the wheel button."""

from __future__ import annotations

import numpy as np
import pytest

from pit_wall_ai.audio.capture import Utterance
from pit_wall_ai.chat.commands import parse
from pit_wall_ai.config import AppConfig
from pit_wall_ai.engineer.alerts import Alert, Priority
from pit_wall_ai.engineer.engine import Engineer
from pit_wall_ai.stt import Transcript
from pit_wall_ai.voice.radio import RadioChannel, VoiceSession


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("No molestes ahorita", "quiet"),
        ("¡Silencio!", "quiet"),
        ("cállate un momento", "quiet"),
        ("ahora no, déjame tranquilo", "quiet"),
        ("no me hables", "quiet"),
        ("Ya puedes hablar", "resume"),
        ("puedes hablar otra vez", "resume"),
        ("¿cuánta gasolina me queda?", None),
        ("¿cuál es el silencio de radio permitido en esta sesión de práctica larga?", None),
        ("", None),
    ],
)
def test_parse(text, expected):
    assert parse(text) == expected


class Speaker:
    busy = False

    def __init__(self):
        self.said = []

    def say(self, text, *, interrupt=False, meta=None):
        self.said.append((text, interrupt))


class FakeSpeech(Speaker):
    pass


class Stt:
    name = model_name = "fake"

    def __init__(self, text):
        self.text = text

    def load(self):
        return 0.0

    def transcribe(self, pcm, *, hotwords=""):
        return Transcript(self.text, 0.0, 1.0)

    def close(self):
        pass


class Reply:
    text = "respuesta"
    source = "local"


def utt():
    return Utterance(np.full(16000, 800, dtype=np.int16))


def session(text, engineer, ends_on_speech=True):
    speech = FakeSpeech()
    s = VoiceSession(
        radio=RadioChannel(speech),
        transcriber=Stt(text),
        ask=lambda q: Reply(),
        ack_after_s=0.0,
        engineer=engineer,
        quiet_ends_on_speech=ends_on_speech,
    )
    return speech, s


class FakeEngineer:
    def __init__(self):
        self.quiet = False

    def set_quiet(self, on):
        self.quiet = on


def test_the_spoken_order_switches_quiet_on_and_confirms_it():
    eng = FakeEngineer()
    speech, s = session("no molestes ahorita", eng)
    turn = s.process(utt())
    assert eng.quiet and turn.heard_command == "quiet" and turn.answer == ""
    assert "en silencio" in speech.said[0][0]


def test_speaking_again_ends_the_quiet_mode_and_is_answered():
    eng = FakeEngineer()
    eng.quiet = True
    speech, s = session("¿cuánta gasolina me queda?", eng)
    s.process(utt())
    assert not eng.quiet and speech.said[0][0] == "respuesta"


def test_with_quiet_not_ending_on_speech_only_the_order_does():
    eng = FakeEngineer()
    eng.quiet = True
    speech, s = session("¿cuánta gasolina me queda?", eng, ends_on_speech=False)
    s.process(utt())
    assert eng.quiet  # still quiet, and the question was answered
    speech2, s2 = session("ya puedes hablar", eng, ends_on_speech=False)
    s2.process(utt())
    assert not eng.quiet and "vuelvo a avisarte" in speech2.said[0][0]


def test_the_wheel_button_toggles_quiet():
    eng = FakeEngineer()
    _, s = session("x", eng)
    s.on_action("mute_engineer", "tap")
    assert eng.quiet
    s.on_action("mute_engineer", "tap")
    assert not eng.quiet


def alert(rule, prio, key):
    return Alert(rule=rule, key=key, text=f"aviso {rule}", priority=prio, t=0.0)


def test_the_engineer_drops_everything_below_critical_while_quiet():
    sp = Speaker()
    eng = Engineer(AppConfig().engineer, sp, rules=[])
    eng.pending["a"] = alert("fuel_low", Priority.HIGH, "a")
    eng.set_quiet(True)
    assert eng.quiet and eng.pending == {} and eng.stats.dropped_quiet == 1
    eng.set_quiet(False)
    assert not eng.quiet
