"""``pit-wall voice``: the push-to-talk conversation.

Two modes:

* **simulated** (``--file RECORDING --wav a.wav --wav b.wav``): the utterances come from WAV files and the
  session from a recording; nothing needs a game, a wheel or a microphone. ``--tts console`` prints instead
  of speaking. This is how the whole chain is checked and timed away from the car.
* **live** (no ``--file``): the game's telemetry, the button of the wheel (``pit-wall bind push_to_talk``),
  the microphone and the voice. The rule alerts and the conversation share one channel.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..audio.capture import UtteranceRecorder, read_audio
from ..config import AppConfig
from ..stt import SttUnavailable, Transcriber, build_hotwords, create_transcriber
from .radio import RadioChannel, VoiceSession, VoiceTurn
from .speech import SpeechOutput
from .tts import ConsoleTts


def _speech(cfg: AppConfig, *, console: bool) -> SpeechOutput:
    if console:
        return SpeechOutput(lambda: ConsoleTts(speaking_for_s=0.0))
    from ..cli import _make_speech

    return _make_speech(cfg)


def format_turn(t: VoiceTurn) -> str:
    lines = [f"  heard:   {t.heard!r}" if t.heard else "  heard:   (nothing)"]
    if t.answer:
        lines.append(f"  answer:  {t.answer}")
    if t.error:
        lines.append(f"  error:   {t.error}")
    lines.append(
        f"  timing:  speech {t.audio_s:.1f} s | recognition {t.stt_s:.2f} s | chat {t.chat_s:.2f} s | "
        f"from release to answer {t.total_s:.2f} s" + ("  (ack said)" if t.ack_said else "")
    )
    return "\n".join(lines)


def hotwords_for(runtime, lang: str = "es") -> str:
    names: list[str] = []
    extra: list[str] = []
    try:
        info = runtime.inputs().track
        names = list(info.names.values()) if info is not None else []
    except Exception:  # noqa: BLE001
        info = None
    track_id = getattr(info, "track", None)
    if (
        isinstance(track_id, str) and track_id
    ):  # the circuit itself: "Magione" was heard as "mayones"
        from ..analysis.circuit_names import circuit_for

        c = circuit_for(track_id)
        extra.append(str(c["name"]) if c else track_id.replace("ks_", "").replace("_", " "))
    return build_hotwords(names, extra, lang=lang)


def cmd_voice(
    cfg: AppConfig,
    a: argparse.Namespace,
    *,
    transcriber: Transcriber | None = None,
    out: Callable[[str], None] = print,
) -> int:
    from ..chat.runtime import ChatRuntime
    from ..chat.session import ChatSession, make_client

    for noisy in (
        "faster_whisper",
        "httpx2",
        "httpx",
        "pit_wall_ai.analysis.trackctx",
        "pit_wall_ai.engineer.alerts",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    simulated = bool(a.file)
    tr = transcriber
    if tr is None:
        upd = {}
        if a.stt_device:
            upd["device"] = a.stt_device
        if getattr(a, "stt_model", None):
            upd["model"] = a.stt_model
        s = cfg.stt.model_copy(update=upd)
        tr = create_transcriber(s, cfg.engineer.language)
        try:
            loaded = tr.load()
        except SttUnavailable as exc:
            out(f"speech recognition unavailable: {exc}")
            return 1
        out(f"[recognition ready: model {tr.model_name}, loaded in {loaded:.1f} s]")
    speech = _speech(cfg, console=a.tts == "console" or (simulated and a.tts != "real"))
    if not speech.start():
        out(f"voice unavailable: {speech.error}")
        return 1
    radio = RadioChannel(speech, priority=cfg.radio.priority)
    runtime = ChatRuntime(cfg, live=not simulated, speaker=None if simulated else radio)
    if simulated:
        n = runtime.feed_file(a.file, session=a.session)
        out(f"[{n} samples of {a.file} loaded]")
    client, why = make_client(cfg)
    if runtime.milestones is not None:
        runtime.milestones.client = client if cfg.engineer.milestones.use_model else None
    chat = ChatSession(cfg, runtime.inputs, client, unavailable_reason=why)
    if client is None:
        out(f"[cloud model off: {why}; local answers only]")
    session = VoiceSession(
        radio=radio,
        transcriber=tr,
        ask=chat.ask,
        language=cfg.engineer.language,
        ack_after_s=cfg.radio.ack_after_s,
        hotwords=lambda: hotwords_for(runtime, cfg.engineer.language),
        engineer=runtime.engineer,
        quiet_ends_on_speech=cfg.radio.quiet_ends_on_speech,
        on_turn=lambda t: None,
    )
    try:
        if simulated:
            return _simulate(a, session, out)
        return _live(cfg, a, runtime, session, radio, out)
    finally:
        session.stop()
        speech.stop()


def _simulate(a: argparse.Namespace, session: VoiceSession, out) -> int:
    if not a.wav:
        out("simulated mode needs --wav FILE (repeatable): the questions as audio")
        return 2
    rc = 0
    for i, w in enumerate(a.wav, 1):
        u = read_audio(w)
        out(f"\n[{i}] {Path(w).name}")
        turn = session.process(u)
        out(format_turn(turn))
        if turn.error or not turn.answer:
            rc = 1
        time.sleep(0.05)
    t = [x.total_s for x in session.turns]
    if t:
        out(
            f"\n{len(t)} turns; from release to answer: mean {sum(t) / len(t):.2f} s, max {max(t):.2f} s"
        )
    return rc


def _live(cfg: AppConfig, a, runtime, session: VoiceSession, radio: RadioChannel, out) -> int:
    from ..audio.capture import Microphone, SoundDeviceBackend
    from ..audio.devices import DeviceNotFound, find_device
    from ..cli import build_source
    from ..input.actions import ActionManager, parse_bindings
    from ..input.backends import default_backend
    from ..telemetry.poller import TelemetryPoller

    bindings = parse_bindings(cfg.input.bindings)
    if "push_to_talk" not in bindings:
        out("push_to_talk has no button yet: run `pit-wall bind push_to_talk` first")
        return 2
    audio = SoundDeviceBackend()
    try:
        dev, _ = find_device(audio.devices(), cfg.audio.input_device, kind="input")
    except DeviceNotFound as exc:
        out(str(exc))
        return 2
    rec = UtteranceRecorder(
        on_utterance=session.submit,
        preroll_ms=cfg.audio.preroll_ms,
        tail_ms=cfg.audio.tail_ms,
        min_ms=cfg.audio.min_utterance_ms,
    )
    session.attach_recorder(rec)
    mic = Microphone(audio, dev, rec)
    poller = TelemetryPoller(
        build_source(cfg), hz=cfg.telemetry.hz, buffer_seconds=5, retry_s=cfg.telemetry.retry_s
    )
    runtime.engine.attach(poller)
    manager = ActionManager(bindings, session.on_action)
    inputs = default_backend(cfg.input.poll_hz)
    try:
        mic.start()
    except OSError as exc:
        out(f"cannot open the microphone: {exc}")
        return 1
    session.on_turn = lambda t: out(format_turn(t))
    session.start()
    poller.start()
    inputs.start(manager.feed)
    out("Hold the push-to-talk button and ask. Ctrl+C to finish.")
    seen = 0
    try:
        while True:
            time.sleep(0.25)
            if rec.dropped != seen:  # an accidental touch: the channel is free again
                seen = rec.dropped
                session.release_silent()
    except KeyboardInterrupt:
        pass
    finally:
        inputs.stop()
        mic.stop()
        poller.stop()
        runtime.engine.detach()
    return 0


__all__ = ["cmd_voice", "format_turn", "sys"]
