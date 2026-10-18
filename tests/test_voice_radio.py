"""The radio channel and the voice turn, with fakes: no microphone, no game, no speaker."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np

from pit_wall_ai.audio.capture import Utterance, write_wav
from pit_wall_ai.config import AppConfig
from pit_wall_ai.stt import SttUnavailable, Transcript
from pit_wall_ai.voice import cli_voice
from pit_wall_ai.voice.radio import DRIVER, IDLE, THINKING, RadioChannel, VoiceSession
from pit_wall_ai.voice.speech import SpeechOutput
from pit_wall_ai.voice.tts import ConsoleTts


class FakeSpeech:
    def __init__(self):
        self.busy = False
        self.said: list[tuple[str, bool, dict]] = []
        self.cancelled = 0

    def say(self, text, *, interrupt=False, meta=None):
        self.said.append((text, interrupt, meta or {}))

    def cancel(self):
        self.cancelled += 1
        self.busy = False


class FakeStt:
    name = "fake"
    model_name = "fake"

    def __init__(self, text="cuánta gasolina me queda", delay=0.0, fail=False):
        self.text, self.delay, self.fail = text, delay, fail
        self.hotwords_seen: list[str] = []

    def load(self):
        return 0.0

    def transcribe(self, pcm, *, hotwords=""):
        self.hotwords_seen.append(hotwords)
        if self.fail:
            raise SttUnavailable("cuda gone")
        time.sleep(self.delay)
        return Transcript(self.text, self.delay, len(pcm) / 16000)

    def close(self):
        pass


@dataclass
class Reply:
    text: str
    source: str = "local"


def utt(seconds=1.0):
    return Utterance(np.full(int(16000 * seconds), 1000, dtype=np.int16))


def make(stt=None, ask=None, priority="first_to_start", ack_after_s=0.0):
    speech = FakeSpeech()
    radio = RadioChannel(speech, priority=priority)
    s = VoiceSession(
        radio=radio,
        transcriber=stt or FakeStt(),
        ask=ask or (lambda q: Reply(f"respuesta a {q}")),
        ack_after_s=ack_after_s,
        hotwords=lambda: "Curvone, psi",
    )
    return speech, radio, s


def test_a_turn_transcribes_asks_and_speaks_the_answer():
    speech, radio, s = make()
    turn = s.process(utt())
    assert turn.heard == "cuánta gasolina me queda"
    assert turn.answer == "respuesta a cuánta gasolina me queda"
    assert speech.said == [(turn.answer, False, {"rule": "answer"})]
    assert radio.state == IDLE and turn.total_s >= 0


def test_the_vocabulary_reaches_the_recogniser():
    stt = FakeStt()
    _, _, s = make(stt=stt)
    s.process(utt())
    assert stt.hotwords_seen == ["Curvone, psi"]


def test_an_empty_transcript_asks_to_repeat_and_does_not_reach_the_chat():
    asked = []
    speech, _, s = make(stt=FakeStt(text="  ..."), ask=lambda q: asked.append(q) or Reply("x"))
    turn = s.process(utt())
    assert asked == [] and turn.answer == ""
    assert "repítelo" in speech.said[0][0]


def test_a_recogniser_failure_is_said_and_the_channel_is_released():
    speech, radio, s = make(stt=FakeStt(fail=True))
    turn = s.process(utt())
    assert "cuda gone" in turn.error and "No puedo entenderte" in speech.said[0][0]
    assert radio.state == IDLE


def test_a_slow_answer_gets_an_acknowledgement_first():
    def slow(q):
        time.sleep(0.35)
        return Reply("respuesta lenta")

    speech, _, s = make(ask=slow, ack_after_s=0.1)
    turn = s.process(utt())
    texts = [t for t, _, _ in speech.said]
    assert turn.ack_said and texts[0] == "Un momento." and texts[-1].startswith("respuesta")


def test_a_slow_recognition_alone_does_not_trigger_the_acknowledgement():
    speech, _, s = make(stt=FakeStt(delay=0.35), ack_after_s=0.1)
    turn = s.process(utt())
    assert not turn.ack_said


def test_a_fast_answer_has_no_acknowledgement():
    speech, _, s = make(ack_after_s=0.5)
    turn = s.process(utt())
    time.sleep(0.6)
    assert not turn.ack_said and len(speech.said) == 1


# -- who talks first ---------------------------------------------------------------------------------------------
def press(s, phase):
    s.on_action("push_to_talk", phase)


def test_first_to_start_ignores_a_press_while_the_engineer_is_speaking():
    speech, radio, s = make()
    speech.busy = True
    press(s, "press")
    assert radio.state == IDLE and radio.ignored_presses == 1 and speech.cancelled == 0
    press(s, "release")  # the release of an ignored press does nothing
    assert radio.state == IDLE


def test_driver_priority_cuts_the_engineer():
    speech, radio, s = make(priority="driver")
    speech.busy = True
    press(s, "press")
    assert speech.cancelled == 1 and radio.state == DRIVER


def test_the_channel_is_busy_for_the_alerts_while_the_driver_talks_and_thinks():
    _, radio, s = make()
    assert not radio.busy
    press(s, "press")
    assert radio.state == DRIVER and radio.busy
    press(s, "release")
    assert radio.state == THINKING and radio.busy
    s.process(utt())
    assert not radio.busy


def test_a_critical_alert_goes_out_even_while_the_driver_talks():
    speech, radio, s = make()
    press(s, "press")
    radio.say("Fuego", interrupt=True, meta={"rule": "x"})
    assert speech.said[-1][:2] == ("Fuego", True)


def test_acknowledge_cuts_what_is_being_said():
    speech, _, s = make()
    speech.busy = True
    s.on_action("acknowledge", "tap")
    assert speech.cancelled == 1


def test_the_worker_runs_submitted_utterances_in_order():
    _, radio, s = make()
    s.start()
    try:
        s.submit(utt())
        s.submit(utt())
        deadline = time.time() + 3
        while len(s.turns) < 2 and time.time() < deadline:
            time.sleep(0.02)
    finally:
        s.stop()
    assert len(s.turns) == 2 and radio.state == IDLE


def test_a_dropped_touch_frees_the_channel():
    _, radio, s = make()
    press(s, "press")
    press(s, "release")
    assert radio.state == THINKING
    s.release_silent()
    assert radio.state == IDLE


# -- the real speech output understands a cancel -------------------------------------------------------------------
def test_cancel_stops_the_backend_and_says_nothing():
    class Spy(ConsoleTts):
        def __init__(self):
            super().__init__(speaking_for_s=5.0)
            self.stops = 0

        def stop(self):
            self.stops += 1
            super().stop()

    spy = Spy()
    out = SpeechOutput(lambda: spy)
    assert out.start()
    out.say("un mensaje largo")
    time.sleep(0.1)
    out.cancel()
    deadline = time.time() + 2
    while spy.stops < 1 and time.time() < deadline:
        time.sleep(0.02)
    out.stop()
    assert spy.stops >= 1 and spy.spoken == ["un mensaje largo"]


# -- the simulated command ---------------------------------------------------------------------------------------------
def test_simulated_voice_command_answers_a_wav_from_a_recording(tmp_path):
    fx = "tests/fixtures/live_lfm_magione_session.pwrec.gz"
    w = tmp_path / "q.wav"
    write_wav(w, utt(1.5))
    ns = argparse.Namespace(
        file=fx, wav=[str(w)], session="practice", tts="console", stt_device=None
    )
    lines: list[str] = []
    rc = cli_voice.cmd_voice(
        AppConfig(), ns, transcriber=FakeStt("cuánta gasolina me queda"), out=lines.append
    )
    text = "\n".join(lines)
    assert rc == 0 and "heard:" in text and "answer:" in text and "from release to answer" in text


def test_hotwords_from_the_track_names():
    class Track:
        names = {1: "Curvone", 2: "Prima Esse"}

    class Inputs:
        track = Track

    class R:
        def inputs(self):
            return Inputs

    assert cli_voice.hotwords_for(R()).startswith("Curvone, Prima Esse")
