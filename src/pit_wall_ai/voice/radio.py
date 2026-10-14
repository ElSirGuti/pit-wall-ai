"""The radio channel and the voice turn (phase 6, step 4).

A conversation with the engineer works like a half-duplex radio:

* one side talks at a time: **whoever starts first wins** (``radio.priority: first_to_start``). A press of
  the push-to-talk button while the engineer is speaking is ignored with a short note; with
  ``radio.priority: driver`` the press cuts the engineer instead;
* **critical alerts always go out** (they interrupt, whatever the state); the rule alerts that are not
  critical wait: :attr:`RadioChannel.busy` is true while the driver talks or an answer is being prepared, and
  the engineer already keeps what is pending and drops it when it goes stale;
* when the answer takes longer than ``ack_after_s`` an acknowledgement phrase is spoken first ("Un momento"),
  so the silence is not mistaken for a failure;
* the ``acknowledge`` action stops a non-critical message in progress.

Nothing here touches hardware: the speech output, the transcriber and the question-answering function are
injected, so the whole turn is tested with fakes.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..audio.capture import Utterance
from ..chat.commands import parse as parse_command
from ..stt import SttUnavailable, Transcriber
from ..stt.postfix import fix as fix_transcript

log = logging.getLogger(__name__)

IDLE, DRIVER, THINKING = "idle", "driver", "thinking"

PHRASES = {
    "es": {
        "ack": "Un momento.",
        "nothing": "No te he entendido, repítelo.",
        "stt_down": "No puedo entenderte ahora mismo.",
        "busy": "",
        "quiet": "Entendido, en silencio. Solo te aviso si es crítico.",
        "resume": "Vale, vuelvo a avisarte.",
    },
    "en": {
        "ack": "One moment.",
        "nothing": "I did not catch that, say it again.",
        "stt_down": "I cannot understand you right now.",
        "busy": "",
        "quiet": "Understood, quiet. I will only call you for something critical.",
        "resume": "Okay, I am back on the alerts.",
    },
    "de": {
        "ack": "Einen Moment.",
        "nothing": "Das habe ich nicht verstanden, sag es noch einmal.",
        "stt_down": "Ich kann dich gerade nicht verstehen.",
        "busy": "",
        "quiet": "Verstanden, ich bin still. Ich melde mich nur, wenn es kritisch ist.",
        "resume": "Okay, ich melde mich wieder.",
    },
}


class SpeechLike(Protocol):
    @property
    def busy(self) -> bool: ...

    def say(self, text: str, *, interrupt: bool = False, meta: dict | None = None): ...


class Answer(Protocol):
    text: str


@dataclass
class VoiceTurn:
    """What one push-to-talk turn did, with its timings (seconds from the release of the button)."""

    heard: str = ""
    answer: str = ""
    source: str = ""
    audio_s: float = 0.0
    stt_s: float = 0.0
    chat_s: float = 0.0
    total_s: float = 0.0
    ack_said: bool = False
    heard_command: str = ""
    error: str = ""
    notes: list[str] = field(default_factory=list)


class RadioChannel:
    """The ``speaker`` of the engineer: shares the speech output with the conversation."""

    def __init__(self, speech: SpeechLike, *, priority: str = "first_to_start") -> None:
        self.speech = speech
        self.priority = priority
        self.state = IDLE
        self._lock = threading.Lock()
        self.ignored_presses = 0

    # the Engineer's Speaker interface
    @property
    def busy(self) -> bool:
        return self.state != IDLE or bool(self.speech.busy)

    def say(self, text: str, *, interrupt: bool = False, meta: dict | None = None):
        return self.speech.say(text, interrupt=interrupt, meta=meta)

    # driver side
    def try_open(self) -> bool:
        """The driver presses the button. True when the channel is theirs."""
        with self._lock:
            if self.state != IDLE:
                return False
            if self.speech.busy:
                if self.priority == "driver":
                    cancel = getattr(self.speech, "cancel", None)
                    if cancel is not None:
                        cancel()
                else:
                    self.ignored_presses += 1
                    return False
            self.state = DRIVER
            return True

    def set_state(self, state: str) -> None:
        with self._lock:
            self.state = state


class VoiceSession:
    """Button -> utterance -> text -> answer -> speech, one turn at a time."""

    def __init__(
        self,
        *,
        radio: RadioChannel,
        transcriber: Transcriber,
        ask: Callable[[str], Answer],
        language: str = "es",
        ack_after_s: float = 1.5,
        hotwords: Callable[[], str] = lambda: "",
        clock: Callable[[], float] = time.perf_counter,
        on_turn: Callable[[VoiceTurn], None] | None = None,
        history: int = 100,
        engineer=None,
        quiet_ends_on_speech: bool = True,
    ) -> None:
        self.radio = radio
        self.transcriber = transcriber
        self.ask = ask
        self.phrases = PHRASES.get(language, PHRASES["en"])
        self.ack_after_s = ack_after_s
        self.hotwords = hotwords
        self._clock = clock
        self.on_turn = on_turn
        self.engineer = engineer  # has ``quiet`` and ``set_quiet(bool)``
        self.quiet_ends_on_speech = quiet_ends_on_speech
        self.turns: deque[VoiceTurn] = deque(maxlen=history)
        self._pcm: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._recorder = None  # set by attach_recorder
        self._opened = False

    # -- wiring ---------------------------------------------------------------------------------------
    def attach_recorder(self, recorder) -> None:
        """``recorder`` has ``press()`` / ``release()``; its ``on_utterance`` must be :meth:`submit`."""
        self._recorder = recorder

    def start(self) -> None:
        self._thread = threading.Thread(target=self._worker, name="voice-turns", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._pcm.put(None)
        if self._thread is not None:
            self._thread.join(5.0)

    # -- actions (from the ActionManager) -------------------------------------------------------------
    def on_action(self, action: str, phase: str) -> None:
        if action == "push_to_talk":
            if phase == "press":
                self._opened = self.radio.try_open()
                if self._opened and self._recorder is not None:
                    self._recorder.press()
            elif phase == "release" and self._opened:
                self._opened = False
                if self._recorder is not None:
                    self._recorder.release()
                # the thinking state starts now, so the rule alerts keep waiting until the answer is out
                self.radio.set_state(THINKING)
        elif action == "mute_engineer" and phase == "tap" and self.engineer is not None:
            self._set_quiet(not self.engineer.quiet)
        elif action == "acknowledge" and phase == "tap":
            cancel = getattr(self.radio.speech, "cancel", None)
            if cancel is not None and self.radio.speech.busy:
                cancel()

    # -- utterances -----------------------------------------------------------------------------------
    def submit(self, u: Utterance) -> None:
        """Called by the recorder (audio thread side): the turn runs on the worker thread."""
        self._pcm.put((u, self._clock()))

    def release_silent(self) -> None:
        """The recorder dropped the utterance (touch too short): the channel is free again."""
        if self.radio.state == THINKING and self._pcm.empty():
            self.radio.set_state(IDLE)

    def _worker(self) -> None:
        while True:
            item = self._pcm.get()
            if item is None:
                return
            u, t_in = item
            try:
                self.process(u, t_released=t_in)
            except Exception:  # noqa: BLE001 - a bad turn must not end the conversation
                log.exception("voice turn failed")
                self.radio.set_state(IDLE)

    def process(self, u: Utterance, *, t_released: float | None = None) -> VoiceTurn:
        """Run one turn synchronously (the worker calls it; the tests and the simulator call it too)."""
        t0 = t_released if t_released is not None else self._clock()
        turn = VoiceTurn(audio_s=u.duration_s)
        self.radio.set_state(THINKING)
        ack_timer = threading.Timer(self.ack_after_s, self._ack, args=(turn,))
        ack_timer.daemon = True
        if self.ack_after_s > 0:
            ack_timer.start()
        try:
            t = self._clock()
            try:
                tr = self.transcriber.transcribe(
                    np.asarray(u.pcm, dtype=np.int16), hotwords=self.hotwords()
                )
                text = fix_transcript(tr.text.strip())
            except SttUnavailable as exc:
                turn.error = str(exc)
                ack_timer.cancel()
                self._speak(self.phrases["stt_down"], turn)
                return self._finish(turn, t0)
            turn.stt_s = self._clock() - t
            turn.heard = text
            if not _has_words(text):
                ack_timer.cancel()
                self._speak(self.phrases["nothing"], turn)
                return self._finish(turn, t0)
            command = parse_command(text)
            if command is not None and self.engineer is not None:
                ack_timer.cancel()
                self._set_quiet(command == "quiet")
                turn.heard_command = command
                self._speak(self.phrases[command if command == "quiet" else "resume"], turn)
                return self._finish(turn, t0)
            if self.engineer is not None and self.engineer.quiet and self.quiet_ends_on_speech:
                self._set_quiet(False)  # "until I talk to you again"
                turn.notes.append("quiet mode ended: the driver spoke")
            t = self._clock()
            reply = self.ask(text)
            turn.chat_s = self._clock() - t
            turn.answer = reply.text
            turn.source = getattr(reply, "source", "")
            ack_timer.cancel()
            self._speak(reply.text, turn)
            return self._finish(turn, t0)
        finally:
            ack_timer.cancel()

    # -- internals ------------------------------------------------------------------------------------
    def _set_quiet(self, on: bool) -> None:
        if self.engineer is not None:
            self.engineer.set_quiet(on)

    def _ack(self, turn: VoiceTurn) -> None:
        if self.radio.state == THINKING and not turn.answer:
            turn.ack_said = True
            self.radio.say(self.phrases["ack"], meta={"rule": "ack"})

    def _speak(self, text: str, turn: VoiceTurn) -> None:
        if text:
            self.radio.say(text, meta={"rule": "answer"})

    def _finish(self, turn: VoiceTurn, t0: float) -> VoiceTurn:
        turn.total_s = self._clock() - t0
        self.radio.set_state(IDLE)
        self.turns.append(turn)
        if self.on_turn is not None:
            try:
                self.on_turn(turn)
            except Exception:  # noqa: BLE001
                log.exception("turn listener failed")
        return turn


def _has_words(text: str) -> bool:
    return any(c.isalnum() for c in text)
