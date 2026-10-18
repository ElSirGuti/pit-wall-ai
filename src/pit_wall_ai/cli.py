"""Command line: ``pit-wall run`` (live dashboard) and ``pit-wall dump-shm`` (confirm field units)."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .config import AppConfig, ConfigError, load_config
from .engineer import messages as M
from .engineer.engine import Engineer
from .logging_setup import setup_logging
from .session.engine import SessionEngine
from .telemetry import shm_structs as S
from .telemetry.ac_shared_memory import ACSharedMemorySource
from .telemetry.console import run_dashboard
from .telemetry.dump import RangeTracker, format_dump, report_json
from .telemetry.poller import TelemetryPoller
from .telemetry.recorder import NativeReplaySource, Recorder, is_native, record_from_poller
from .telemetry.replay import ReplaySource
from .telemetry.source import TelemetrySource
from .voice.speech import SpeechOutput
from .voice.tts import ConsoleTts, create_backend

_SESSIONS = {kind.name.lower(): int(kind) for kind in S.SessionKind}


def build_source(cfg: AppConfig) -> TelemetrySource:
    t = cfg.telemetry
    if t.source == "replay":
        if not t.replay.file:
            raise ConfigError("telemetry.source is 'replay' but no file was given (--file)")
        if not Path(t.replay.file).is_file():
            raise ConfigError(f"replay file not found: {t.replay.file}")
        if is_native(t.replay.file):
            return NativeReplaySource(
                t.replay.file, speed=t.replay.speed, start_s=t.replay.start_s, loop=t.replay.loop
            )
        return ReplaySource(
            t.replay.file,
            speed=t.replay.speed,
            start_s=t.replay.start_s,
            loop=t.replay.loop,
            session=_SESSIONS[t.replay.session],
        )
    source = ACSharedMemorySource()
    if t.csp_bridge.enabled:
        from .telemetry.csp_bridge import BridgedSource, BridgeReceiver

        return BridgedSource(
            source,
            BridgeReceiver(t.csp_bridge.port, max_age_s=t.csp_bridge.max_age_s),
            crewchief=t.csp_bridge.crewchief,
        )
    return source


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pit-wall", description="Real-time AI race engineer for Assetto Corsa"
    )
    p.add_argument("--version", action="version", version=f"pit-wall-ai {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--config", help="YAML config (default: PIT_WALL_CONFIG or config/local.yaml)"
        )
        sp.add_argument("--source", choices=["ac", "replay"], help="live game or a recorded log")
        sp.add_argument("--file", help="recording to replay (.pwrec.gz, MoTeC .ld or .csv)")
        sp.add_argument("--speed", type=float, help="replay speed multiplier")
        sp.add_argument("--start", type=float, help="replay start offset in seconds")
        sp.add_argument("--loop", action="store_true", help="loop the recording")
        sp.add_argument("--hz", type=float, help="polling rate")
        sp.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    run = sub.add_parser("run", help="live dashboard")
    common(run)
    run.add_argument(
        "--plain", action="store_true", help="one status line per second instead of the dashboard"
    )
    run.add_argument("--duration", type=float, help="exit after this many seconds")
    run.add_argument("--voice", action="store_true", help="turn the engineer's voice on")
    run.add_argument(
        "--mute", action="store_true", help="evaluate the alerts but print them instead of speaking"
    )
    run.add_argument("--language", choices=["es", "en"], help="language of the spoken alerts")

    say = sub.add_parser(
        "say", help="speak a phrase with the configured voice and measure the latency"
    )
    common(say)
    say.add_argument("text", nargs="?", help="what to say (default: a test phrase)")
    say.add_argument("--language", choices=["es", "en"], help="language / voice")
    say.add_argument("--voice", help="part of the name of the voice to use (see `pit-wall voices`)")
    say.add_argument(
        "--device", help="part of the name of the audio output (see `pit-wall devices`)"
    )
    say.add_argument("--radio", choices=["off", "light", "strong"], help="the team-radio sound")
    say.add_argument("--out", help="write the sentence to this WAV instead of playing it")

    sub.add_parser(
        "rules", help="list the alert rules, their thresholds and the session policies"
    ).add_argument("--config", help="YAML config")

    ask = sub.add_parser("ask", help="ask the engineer one question (live game or --file)")
    common(ask)
    ask.add_argument("text", help="the question")
    ask.add_argument("--language", choices=["es", "en"], help="language of the answer")
    ask.add_argument(
        "--session",
        choices=["practice", "qualify", "race", "hotlap"],
        default="practice",
        help="session kind for .ld/.csv logs, which do not say (default practice)",
    )
    ask.add_argument("--wait", type=float, default=5.0, help="seconds to wait for live telemetry")

    chat = sub.add_parser(
        "chat", help="talk with the engineer in the terminal (live game or --file)"
    )
    common(chat)
    chat.add_argument("--language", choices=["es", "en"], help="language of the answers")
    chat.add_argument(
        "--session",
        choices=["practice", "qualify", "race", "hotlap"],
        default="practice",
        help="session kind for .ld/.csv logs, which do not say (default practice)",
    )
    chat.add_argument("--wait", type=float, default=5.0, help="seconds to wait for live telemetry")

    dv = sub.add_parser("devices", help="list the microphones and the audio outputs")
    dv.add_argument("--config", help="YAML config")

    vo = sub.add_parser(
        "voice", help="push-to-talk conversation (live, or simulated with --file/--wav)"
    )
    vo.add_argument("--config", help="YAML config")
    vo.add_argument("--file", help="recording to answer about (simulated mode)")
    vo.add_argument("--wav", action="append", help="a spoken question as a WAV file (repeatable)")
    vo.add_argument("--session", default="practice")
    vo.add_argument(
        "--stt-model",
        help="tiny | base | small | medium | high (large-v3) | turbo: override stt.model",
    )
    vo.add_argument(
        "--tts", choices=["console", "real"], default=None, help="console prints the voice"
    )
    vo.add_argument("--stt-device", choices=["auto", "cpu", "cuda"], help="override stt.device")
    tc = sub.add_parser("tyre-corners", help="which corners work the tyres hardest (estimate)")
    tc.add_argument("--config", help="YAML config")
    tc.add_argument("file", help="recording (.pwrec.gz, .ld, .csv)")
    tc.add_argument("--session", default="practice")
    tc.add_argument("--ac-dir", action="append", help="AC folder (repeatable)")
    tc.add_argument("--top", type=int, default=6, help="how many corners to list")
    tc.add_argument(
        "--reference",
        help="wear read off the game's tyre app at the END of the laps, FL,FR,RL,RR (e.g. 83,88,70,75)",
    )
    tc.add_argument("--start-wear", help="the same at the START (default 100,100,100,100)")
    st = sub.add_parser("stt-test", help="speech recognition: latency, accuracy, cpu vs gpu")
    st.add_argument("--config", help="YAML config")
    st.add_argument("files", nargs="*", help="wav files (none: synthesised phrases)")
    st.add_argument("--ref", action="append", help="reference text of each file, in order")
    st.add_argument("--device", choices=["cpu", "cuda", "both"], default="cpu")
    st.add_argument("--model", help="tiny | base | small | medium (default: stt.model)")
    st.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run the set this many times (sustained load, to see the effect on the game fps)",
    )
    st.add_argument("--hotword", action="append", help="extra words to bias the recogniser")
    mt = sub.add_parser("mic-test", help="record a few seconds and show the level")
    mt.add_argument("--config", help="YAML config")
    mt.add_argument("--device", help="part of the microphone name (default: audio.input_device)")
    mt.add_argument("--seconds", type=float, default=3.0, help="how long to record (3)")
    mt.add_argument("--out", help="also save the recording as a WAV file")

    vc = sub.add_parser("voices", help="list the installed voices and test them by ear")
    vc.add_argument("--config", help="YAML config")
    vc.add_argument("--test", action="store_true", help="play a sample with each voice")
    vc.add_argument("--voice", help="only the voices whose name contains this")
    vc.add_argument("--device", help="part of the name of the audio output to play on")
    vc.add_argument(
        "--prerender", action="store_true", help="render the fixed phrases into the cache"
    )
    vc.add_argument(
        "--piper-download", metavar="ID", help="download a Piper voice, e.g. es_ES-davefx-medium"
    )

    bd = sub.add_parser("bind", help="assign a button (any device) to an action by pressing it")
    bd.add_argument("--config", help="YAML config")
    bd.add_argument("action", help="push_to_talk, cancel, repeat_last, mute_engineer, ...")
    bd.add_argument(
        "--replace", action="store_true", help="replace the existing bindings of the action"
    )
    bd.add_argument("--clear", action="store_true", help="remove the bindings of the action")
    bd.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for a press")

    bl = sub.add_parser("bindings", help="list the bindings and the connected joysticks")
    bl.add_argument("--config", help="YAML config")

    it = sub.add_parser("input-test", help="print every press and release, to check a device")
    it.add_argument("--config", help="YAML config")
    it.add_argument("--seconds", type=float, default=30.0, help="how long to listen (30)")

    db = sub.add_parser("debrief", help="the post-session debrief of a recording (report + facts)")
    db.add_argument("file", help="recording (.pwrec.gz, .ld, .csv)")
    db.add_argument("--config", help="YAML config")
    db.add_argument("--session", default="practice", help="for logs that do not say (practice)")
    db.add_argument(
        "--lang", choices=["es", "en"], default=None, help="report language (the engineer's)"
    )
    db.add_argument("--out", help="write the Markdown report (and the facts next to it) here")
    db.add_argument("--json", action="store_true", help="print the facts instead of the report")
    db.add_argument(
        "--save",
        action="store_true",
        help="keep this session in sessions.jsonl and compare with the last",
    )
    db.add_argument(
        "--history", help="sessions file to compare against (default ~/.pit-wall-ai/sessions.jsonl)"
    )
    db.add_argument("--summary", action="store_true", help="the spoken summary (3-4 sentences)")
    db.add_argument(
        "--speak", action="store_true", help="say the summary with the engineer's voice"
    )
    db.add_argument("--no-model", action="store_true", help="the template, even if a model is on")
    db.add_argument("--ac-dir", action="append", help="AC folder (repeatable)")

    ck = sub.add_parser("checklist", help="the practice checklist: show it and edit it")
    ck.add_argument(
        "action",
        choices=["show", "add", "remove", "skip", "replace", "reorder", "rule", "undo", "clear"],
    )
    ck.add_argument("--config", help="YAML config")
    ck.add_argument("--path", help="the checklist file (default ~/.pit-wall-ai/checklist.json)")
    ck.add_argument(
        "--kind", default="stint", help="stint | quali_sim | race_sim | setup_test | info"
    )
    ck.add_argument("--id", help="the item (C1, C2...)")
    ck.add_argument("--compound")
    ck.add_argument("--laps", type=int)
    ck.add_argument("--fuel", type=float, help="litres at the start of the stint")
    ck.add_argument("--text")
    ck.add_argument("--position", type=int)
    ck.add_argument("--reason")
    ck.add_argument(
        "--rule", help="mandatory_stop | stop_requires_tyres | compounds_distinct_required | window"
    )
    ck.add_argument("--value", help="for rule: none|one|n, true|false, or 8-12")
    ck.add_argument(
        "--conditions", choices=["dry", "wet"], help="the track now, for the tyre guard"
    )

    ph = sub.add_parser(
        "phrases",
        help="record the phrases of docs/FRASES_VOZ.md and measure the recogniser on them",
    )
    ph_sub = ph.add_subparsers(dest="action", required=True)
    for name, helptext in (
        ("record", "record each phrase from the microphone (Enter to start, Enter to stop)"),
        ("test", "transcribe what was recorded and report the word error rate"),
    ):
        pp = ph_sub.add_parser(name, help=helptext)
        pp.add_argument("--config", help="YAML config")
        pp.add_argument("--lang", choices=["es", "de", "en"], default="es")
        pp.add_argument("--set", default="A", help="A = quiet, B = with the engine on (A)")
        pp.add_argument("--out", default="frases", help="folder of the recordings (frases)")
        if name == "record":
            pp.add_argument("--device", help="part of the microphone name")
            pp.add_argument("--only", help="e.g. 3,6-8: only these phrases")
            pp.add_argument(
                "--redo", action="store_true", help="record again the ones already saved"
            )
            pp.add_argument("--seconds", type=float, default=10.0, help="longest recording (10)")
        else:
            pp.add_argument("--dir", help="another folder (e.g. frases/telefono)")
            pp.add_argument("--device", choices=["cpu", "cuda"], help="override stt.device")
            pp.add_argument("--model", help="tiny | base | small | medium | high | turbo")

    bi = sub.add_parser(
        "bridge-install", help="copy the CSP Lua bridge app into the game's apps/lua"
    )
    bi.add_argument("--config", help="YAML config")
    bi.add_argument("--ac-dir", action="append", help="the assettocorsa folder (repeatable)")

    bt = sub.add_parser("bridge-test", help="listen for the CSP Lua bridge and print what it sends")
    bt.add_argument("--config", help="YAML config")
    bt.add_argument("--seconds", type=float, default=30.0, help="how long to listen (30)")

    tr = sub.add_parser(
        "trim", help="cut a window of a recording into a small .pwrec.gz at a lower rate"
    )
    tr.add_argument("--config", help="YAML config")
    tr.add_argument("--file", required=True, help="the recording to cut")
    tr.add_argument("--out", required=True, help="output .pwrec.gz")
    tr.add_argument("--from", dest="start", type=float, default=0.0, help="start, seconds")
    tr.add_argument("--to", dest="end", type=float, help="end, seconds (default: to the end)")
    tr.add_argument("--rate", type=float, default=10.0, help="samples per second to keep (10)")
    tr.add_argument(
        "--session",
        choices=["practice", "qualify", "race", "hotlap"],
        default="practice",
        help="session kind for .ld/.csv logs, which do not say",
    )

    ev = sub.add_parser(
        "eval",
        help="run the chat evaluation set on a recording (needs the cloud model for most cases)",
    )
    common(ev)
    ev.add_argument("--language", choices=["es", "en"], help="language of the engineer")
    ev.add_argument(
        "--session",
        choices=["practice", "qualify", "race", "hotlap"],
        default="race",
        help="session kind for .ld/.csv logs (default race)",
    )
    ev.add_argument("--only", help="comma separated categories: local,open,impossible,trap")
    ev.add_argument("--pace", type=float, default=5.0, help="seconds between model calls (quota)")
    ev.add_argument("--out", default="eval_report.md", help="where to write the Markdown report")

    chk = sub.add_parser(
        "llm-check", help="test the configured cloud model: models the key can use, latency, 429"
    )
    chk.add_argument("--config", help="YAML config")
    chk.add_argument("--calls", type=int, default=3, help="how many test calls (default 3)")
    chk.add_argument("--model", help="try this model instead of llm.model, for this run only")
    chk.add_argument(
        "--thinking-budget",
        type=int,
        help="thinking tokens to allow for this run (0 = none where the model allows it)",
    )
    chk.add_argument(
        "--allow-cloud",
        action="store_true",
        help="allow the cloud call for this run only (the YAML flag stays as it is)",
    )

    cor = sub.add_parser(
        "corners", help="list the numbered corners of a track from the game's racing line"
    )
    cor.add_argument("--config", help="YAML config")
    cor.add_argument("--track", help="track folder name, e.g. magione (default: from --file)")
    cor.add_argument(
        "--layout", help="layout folder; omit for 'no layout' (or unknown, with --any-layout)"
    )
    cor.add_argument("--any-layout", action="store_true", help="try every layout, pick by length")
    cor.add_argument(
        "--length", type=float, help="the game's track length in metres, to pick a line"
    )
    cor.add_argument("--file", help="a .pwrec.gz recording: track, layout and length come from it")
    cor.add_argument("--ac-dir", action="append", default=[], help="extra AC folder to search")
    cor.add_argument(
        "--what", help='which corner is this? e.g. --what "prima esse" or --what "curva 6"'
    )
    cor.add_argument(
        "--laps", action="store_true", help="also compare every lap of --file against the best lap"
    )
    cor.add_argument(
        "--map", help="also draw the racing line with its numbered corners to this .svg file"
    )
    cor.add_argument(
        "--session",
        choices=["practice", "qualify", "race", "hotlap"],
        default="practice",
        help="session kind for .ld/.csv logs, which do not say (default practice)",
    )

    cm = sub.add_parser(
        "circuit-maps",
        help="draw the racing line of every circuit of the table with its numbered corners",
    )
    cm.add_argument("--config", help="YAML config")
    cm.add_argument("--out", default="circuit_maps", help="folder for the SVG files and index.html")
    cm.add_argument("--only", action="append", help="a circuit id of the table (repeatable)")
    cm.add_argument("--ac-dir", action="append", default=[], help="extra AC folder to search")

    rec = sub.add_parser("record", help="record the live session to a .pwrec.gz file")
    common(rec)
    rec.add_argument("--out", required=True, help="output file, e.g. race.pwrec.gz")
    rec.add_argument(
        "--rate", type=float, default=20.0, help="samples per second to store (default 20)"
    )
    rec.add_argument("--duration", type=float, help="stop after this many seconds")

    dump = sub.add_parser(
        "dump-shm", help="print every field and the observed ranges (confirm units)"
    )
    common(dump)
    dump.add_argument(
        "--watch",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="collect for this long and report min/max/mean per field",
    )
    dump.add_argument("--json", metavar="PATH", help="write the report to a JSON file")
    dump.add_argument(
        "--any-status", action="store_true", help="also count samples while the game is not live"
    )
    dump.add_argument(
        "--include-player", action="store_true", help="keep the driver name in the output"
    )
    dump.add_argument(
        "--wait",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="how long to wait for the game to appear",
    )
    return p


def _apply_overrides(cfg: AppConfig, a: argparse.Namespace) -> AppConfig:
    data = cfg.model_dump()
    t = data["telemetry"]
    if getattr(a, "source", None):
        t["source"] = getattr(a, "source", None)
    if getattr(a, "file", None):
        t["replay"]["file"] = getattr(a, "file", None)
        t["source"] = "replay" if not getattr(a, "source", None) else getattr(a, "source", None)
    if getattr(a, "speed", None) is not None:
        t["replay"]["speed"] = getattr(a, "speed", None)
    if getattr(a, "start", None) is not None:
        t["replay"]["start_s"] = getattr(a, "start", None)
    if getattr(a, "loop", None):
        t["replay"]["loop"] = True
    if getattr(a, "hz", None) is not None:
        t["hz"] = getattr(a, "hz", None)
    if getattr(a, "log_level", None):
        data["logging"]["level"] = getattr(a, "log_level", None)
    if getattr(a, "language", None):
        data["engineer"]["language"] = a.language
    if getattr(a, "voice", False):
        data["engineer"]["enabled"] = True
    return AppConfig.model_validate(data)


def _plain_line(poller, engine) -> str:
    s, st = poller.buffer.latest(), poller.stats
    if s is None:
        return f"[{st.status}] waiting for telemetry... {st.last_error}"
    v = engine.view()
    cur = v.current
    delta = f" delta {cur.delta_ms / 1000:+.3f}s" if cur and cur.delta_ms is not None else ""
    best = f" best {v.best.time_ms / 1000:.3f}s" if v.best else ""
    return (
        f"[{st.status}] {v.state.session}/{v.state.phase} {s.speed_kmh:6.1f} km/h gear {s.gear} "
        f"lap {cur.number if cur else s.completed_laps + 1} pos {s.normalized_pos:.3f}"
        f"{delta}{best} {st.measured_hz:5.1f} Hz drop {st.dropped}"
    )


def _with_profile(cfg: AppConfig) -> AppConfig:
    """The config with the voice profile applied (``engineer.voice.profile``), or as it is."""
    v = cfg.engineer.voice
    if not v.profile:
        return cfg
    from .voice.profiles import apply_profile, load_profile

    return apply_profile(cfg, load_profile(v.profile, v.profiles_dir))


def _make_speech(cfg: AppConfig, *, mute: bool = False) -> SpeechOutput:
    if mute:
        return SpeechOutput(lambda: ConsoleTts())
    cfg = _with_profile(cfg)
    v = cfg.engineer.voice
    return SpeechOutput(
        lambda: create_backend(
            v.backend,
            language=cfg.engineer.language,
            voice_name=v.name,
            rate=v.rate,
            volume=v.volume,
            output_device=v.output_device,
            command=v.command,
            radio_effect=v.radio_effect,
            piper_model=v.piper_model,
            piper_exe=v.piper_exe,
            cache=v.cache,
            cache_dir=v.cache_dir,
            http_url=v.http_url,
            http_body=v.http_body,
            reference_audio=v.reference_audio,
        )
    )


def _start_engineer(
    cfg: AppConfig, a: argparse.Namespace, engine: SessionEngine
) -> SpeechOutput | None:
    if not (cfg.engineer.enabled or a.voice or a.mute):
        return None
    speech = _make_speech(cfg, mute=a.mute)
    if not speech.start():
        print(
            f"voice unavailable ({speech.error}); the alerts are printed instead", file=sys.stderr
        )
        speech = SpeechOutput(lambda: ConsoleTts())
        speech.start()
    eng = Engineer(cfg.engineer, speech, analysis=cfg.analysis)
    engine.add_sample_hook(
        lambda snap, state, tracker, events: eng.on_sample(snap, state, tracker, events)
    )
    from .chat.session import make_client
    from .engineer.memory_store import AlertMemory
    from .engineer.memory_store import listener as memory_listener
    from .engineer.milestones import attach as attach_milestones

    if cfg.engineer.memory.enabled:
        eng.listeners.append(
            memory_listener(
                AlertMemory(cfg.engineer.memory.path, max_records=cfg.engineer.memory.max_records)
            )
        )

    client, _why = make_client(cfg)
    from .analysis.debrief_live import LiveDebrief
    from .analysis.trackctx import TrackAnalysis

    live_debrief = LiveDebrief(
        cfg,
        speech,
        track_analysis=TrackAnalysis(cfg.analysis),
        client=client if cfg.engineer.debrief.use_model else None,
        quiet=lambda: eng.quiet,
    )
    engine.add_sample_hook(live_debrief.on_sample)
    attach_milestones(
        engine,
        speech,
        cfg,
        client=client,
        quiet=lambda: eng.quiet,
        on_milestone=live_debrief.on_milestone,
    )
    return speech


def _cmd_circuit_maps(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .analysis.cli_maps import cmd_circuit_maps

    return cmd_circuit_maps(cfg, a)


def _cmd_say(cfg: AppConfig, a: argparse.Namespace) -> int:
    if a.voice or a.device:  # a one-off test of another voice or output
        v = cfg.engineer.voice.model_copy(
            update={k: val for k, val in (("name", a.voice), ("output_device", a.device)) if val}
        )
        cfg = cfg.model_copy(update={"engineer": cfg.engineer.model_copy(update={"voice": v})})
    cfg = _with_profile(cfg)
    if getattr(a, "radio", None):
        v = cfg.engineer.voice.model_copy(update={"radio_effect": a.radio})
        cfg = cfg.model_copy(update={"engineer": cfg.engineer.model_copy(update={"voice": v})})
    lang = cfg.engineer.language
    text = a.text or M.say(lang, "test")
    if getattr(a, "out", None):
        return _say_to_file(cfg, text, a.out)
    speech = _make_speech(cfg)
    if not speech.start():
        print(f"voice unavailable: {speech.error}", file=sys.stderr)
        return 1
    rec = speech.say(text, meta={"rule": "say"})
    deadline = time.perf_counter() + 20
    while time.perf_counter() < deadline and (rec.ended_t is None):
        time.sleep(0.02)
    speech.stop()
    start = f"{rec.start_ms:.0f} ms" if rec.start_ms is not None else "not reported"
    print(f"said: {text!r}")
    print(
        f"voice: {cfg.engineer.voice.backend}  call: {rec.queue_ms:.1f} ms after the request  "
        f"first audio: {start} after the request"
    )
    return 0


def _say_to_file(cfg: AppConfig, text: str, out: str) -> int:
    """Render a sentence with the configured voice (and radio effect) into a WAV, to listen to it or compare."""
    import shutil

    from .voice.wav_voice import WavVoice, piper_command, read_pcm, render_command

    v = cfg.engineer.voice
    lang = cfg.engineer.language
    if v.backend == "piper" and v.piper_model:
        cmd = piper_command(v.piper_model, v.piper_exe)

        def render(t: str, p: str) -> None:
            render_command(cmd, t, p)

    elif v.backend == "http" and v.http_url:
        from .voice.profiles import render_http

        def render(t: str, p: str) -> None:
            render_http(v.http_url, v.http_body, t, p, language=lang, reference=v.reference_audio)

    elif v.backend == "command" and v.command:

        def render(t: str, p: str) -> None:
            render_command(v.command, t, p)

    else:

        def render(t: str, p: str) -> None:
            from .voice.tts import synth_to_wav

            synth_to_wav(t, p, voice_name=v.name, language=lang, rate=v.rate)

    wv = WavVoice(render, effect=v.radio_effect)
    t0 = time.perf_counter()
    try:
        path = wv.wav_for(text)
    except Exception as exc:  # noqa: BLE001 - any engine failure is reported, not a traceback
        print(f"could not render: {exc}", file=sys.stderr)
        return 1
    ms = (time.perf_counter() - t0) * 1000
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, out)
    pcm, rate = read_pcm(out)
    print(
        f"saved {out}: {len(pcm) / rate:.2f} s at {rate} Hz, radio effect {v.radio_effect}; rendered in {ms:.0f} ms"
    )
    return 0


def _cmd_rules(cfg: AppConfig, a: argparse.Namespace) -> int:
    e = cfg.engineer
    print(f"language: {e.language}  voice: {e.voice.backend}  enabled: {e.enabled}")
    print(
        f"calm: brake < {e.calm.brake_below}, steering < {e.calm.steer_below} for {e.calm.hold_s} s"
    )
    print("\npolicies (what may speak):")
    for name in ("practice", "qualify", "race", "other"):
        pol = getattr(e.policies, name)
        extra = f"  + always: {', '.join(pol.allow_rules)}" if pol.allow_rules else ""
        print(f"  {name:9s} >= {pol.min_priority}{extra}")
    print("\nrules:")
    for name, rule in e.rules:
        data = rule.model_dump()
        on = data.pop("enabled")
        vals = ", ".join(f"{k}={v}" for k, v in data.items())
        print(f"  {'on ' if on else 'off'} {name:14s} {vals}")
    return 0


def _cmd_run(cfg: AppConfig, a: argparse.Namespace) -> int:
    source = build_source(cfg)
    poller = TelemetryPoller(
        source,
        hz=cfg.telemetry.hz,
        buffer_seconds=cfg.telemetry.buffer_seconds,
        retry_s=cfg.telemetry.retry_s,
    )
    engine = SessionEngine()
    speech = _start_engineer(cfg, a, engine)
    engine.attach(poller)
    poller.start()
    stop = threading.Event()
    try:
        if a.plain:
            t0 = time.monotonic()
            while not stop.is_set():
                print(_plain_line(poller, engine), flush=True)
                if poller.stats.status == "ended" or (
                    a.duration and time.monotonic() - t0 >= a.duration
                ):
                    break
                stop.wait(1.0)
        else:
            run_dashboard(poller, stop=stop, duration_s=a.duration, engine=engine)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        engine.detach()
        engine.flush()
        if speech is not None:
            speech.stop()
    return 0


def _cmd_record(cfg: AppConfig, a: argparse.Namespace) -> int:
    source = build_source(cfg)
    poller = TelemetryPoller(
        source, hz=cfg.telemetry.hz, buffer_seconds=5, retry_s=cfg.telemetry.retry_s
    )
    recorder = Recorder(a.out, hz=a.rate)
    poller.start()
    stop = threading.Event()
    print(f"recording to {a.out} at {a.rate:g} Hz; Ctrl+C to stop", flush=True)
    n = 0
    try:
        n = record_from_poller(poller, recorder, stop, duration_s=a.duration)
    except KeyboardInterrupt:
        n = recorder.samples
    finally:
        poller.stop()
        recorder.close()
    print(f"{n} samples written to {a.out}")
    return 0 if n else 1


def _cmd_dump(cfg: AppConfig, a: argparse.Namespace) -> int:
    source = build_source(cfg)
    poller = TelemetryPoller(source, hz=cfg.telemetry.hz, buffer_seconds=5, retry_s=0.5)
    poller.start()
    sub = poller.subscribe(maxsize=4096)
    tracker = RangeTracker()
    last = None
    t0 = time.monotonic()
    try:
        while True:
            snap = sub.get(timeout=0.25)
            now = time.monotonic() - t0
            if snap is not None and snap.status != int(S.GameStatus.LIVE) and not a.any_status:
                snap = None  # menu / pause / replay: not driving values
            if snap is not None:
                last = snap
                tracker.add(snap)
                if a.watch <= 0:
                    break
            if poller.stats.status == "ended":
                break
            if last is None and now > a.wait:
                print(
                    f"no live telemetry after {a.wait:.0f} s: is Assetto Corsa running with the car on track?",
                    file=sys.stderr,
                )
                return 1
            if a.watch > 0 and last is not None and now >= a.watch:
                break
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
    if last is None:
        return 1
    print(format_dump(last, include_player=a.include_player))
    if a.watch > 0 or a.json:
        report = report_json(tracker, last, include_player=a.include_player)
        if a.json:
            Path(a.json).write_text(report, encoding="utf-8")
            print(f"\nreport written to {a.json} ({tracker.samples} samples)")
        else:
            print(f"\n{tracker.samples} samples; ranges:\n{report}")
    return 0


def _print_lap_comparison(g, names, result, cfg) -> None:
    from .analysis.braking import compare_lap, reference_lap, representative

    b = cfg.analysis.braking
    laps = [lap for lap in result.laps if lap.trace is not None]
    if not laps:
        print("\nno laps with a usable trace in the file")
        return
    print(
        f"\nlaps: {len(laps)}; reference = best valid lap with the same compound;"
        f" a corner is flagged (*) with |braking point| >= {b.min_distance_m:g} m and"
        f" a loss >= {b.min_time_s:g} s"
    )
    for lap in laps:
        tag = f"lap {lap.number}  {lap.time_ms / 1000:.3f} s"
        notes = [
            n
            for n, on in (("out", lap.out_lap), ("in", lap.in_lap), ("suspect", lap.suspect))
            if on
        ]
        if lap.out_lap or lap.in_lap or lap.coverage < 0.9:
            print(f"\n{tag}  [{', '.join(notes) or 'incomplete'}]  not compared")
            continue
        if notes:
            tag += f"  [{', '.join(notes)}: off track at some point, read its corners with care]"
        ref = reference_lap(result.laps, lap)
        if ref is None:
            print(f"\n{tag}  best valid lap of its compound: nothing to compare with")
            continue
        if not representative(lap, ref):
            print(
                f"\n{tag}  more than 25 % slower than lap {ref.number}: not a representative lap, not compared"
            )
            continue
        print(
            f"\n{tag}  vs lap {ref.number} ({ref.time_ms / 1000:.3f} s, {lap.time_ms - ref.time_ms:+d} ms)"
        )
        print("  #  braking-point m   earlier  d.time s  d.min km/h  d.throttle m  name")
        for c in compare_lap(g, lap, ref, b):
            bp = c.lap_metrics.brake_point_m
            rp = c.ref_metrics.brake_point_m
            fmt = lambda v, f: "   -   " if v is None else format(v, f)  # noqa: E731
            flag = "*" if c.significant(b) else " "
            print(
                f"{flag}{c.corner:2d}  {fmt(bp, '7.0f')} /{fmt(rp, '7.0f')}"
                f"  {fmt(c.brake_earlier_m, '+7.1f')}  {fmt(c.time_diff_s, '+7.2f')}"
                f"  {fmt(c.min_speed_delta_kmh, '+8.1f')}  {fmt(c.throttle_later_m, '+9.1f')}"
                f"  {names.get(c.corner, '')}"
            )


def _cmd_ask(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .chat.cli_chat import cmd_ask

    return cmd_ask(cfg, a)


def _cmd_chat(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .chat.cli_chat import cmd_chat

    return cmd_chat(cfg, a)


def _cmd_devices(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .audio.cli_audio import cmd_devices

    return cmd_devices(cfg, a)


def _cmd_mic_test(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .audio.cli_audio import cmd_mic_test

    return cmd_mic_test(cfg, a)


def _cmd_stt_test(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .stt.cli_stt import cmd_stt_test

    return cmd_stt_test(cfg, a)


def _cmd_tyre_corners(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .analysis.cli_tyres import cmd_tyre_corners

    return cmd_tyre_corners(cfg, a)


def _cmd_voice(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .voice.cli_voice import cmd_voice

    return cmd_voice(cfg, a)


def _cmd_voices(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .audio.cli_audio import cmd_voices

    return cmd_voices(cfg, a)


def _cmd_bind(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .input.cli_input import cmd_bind

    return cmd_bind(cfg, a)


def _cmd_bindings(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .input.cli_input import cmd_bindings

    return cmd_bindings(cfg, a)


def _cmd_input_test(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .input.cli_input import cmd_input_test

    return cmd_input_test(cfg, a)


def _cmd_debrief(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .analysis.cli_debrief import cmd_debrief

    return cmd_debrief(cfg, a)


def _cmd_checklist(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .analysis.cli_checklist import cmd_checklist

    return cmd_checklist(cfg, a)


def _cmd_phrases(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .stt.cli_phrases import cmd_phrases_record, cmd_phrases_test

    return (cmd_phrases_record if a.action == "record" else cmd_phrases_test)(cfg, a)


def _cmd_bridge_install(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .telemetry.cli_bridge import cmd_bridge_install

    return cmd_bridge_install(cfg, a)


def _cmd_bridge_test(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .telemetry.cli_bridge import cmd_bridge_test

    return cmd_bridge_test(cfg, a)


def _cmd_trim(cfg: AppConfig, a: argparse.Namespace) -> int:
    import os

    from .telemetry.trim import trim

    n = trim(a.file, a.out, start_s=a.start, end_s=a.end, rate_hz=a.rate, session=a.session)
    size = os.path.getsize(a.out) / 1024
    print(f"{n} samples written to {a.out} ({size:.0f} KB)")
    return 0 if n else 1


def _cmd_eval(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .chat.cli_chat import cmd_eval

    return cmd_eval(cfg, a)


def _cmd_llm_check(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .engineer.llmcheck import run_llm_check

    return run_llm_check(
        cfg.llm,
        calls=max(1, a.calls),
        allow_cloud=a.allow_cloud,
        model=a.model,
        thinking_budget=a.thinking_budget,
    )


def _cmd_corners(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .analysis.tracks import resolve

    track, layout, length = a.track, (None if a.any_layout else (a.layout or "")), a.length
    result = None
    if a.file:
        from .analysis.offline import analyse_file

        try:
            result = analyse_file(a.file, session=a.session) if a.laps else None
            if result is None:
                from .analysis.offline import iter_snapshots

                st, _ = iter_snapshots(a.file, session=a.session)
            else:
                st = result.static
        except (OSError, ValueError) as exc:
            print(f"cannot read {a.file}: {exc}", file=sys.stderr)
            return 2
        track = track or st.track
        if a.layout is None and not a.any_layout:
            layout = st.track_configuration
        length = length or st.track_length_m or None
    if not track:
        print("give --track or --file", file=sys.stderr)
        return 2
    res = resolve(track, layout, length_m=length, configured=[*a.ac_dir, *cfg.analysis.ac_dirs])
    if not res.ok:
        print(f"no geometry for {track}: {res.reason}", file=sys.stderr)
        for c in res.candidates:
            print(f"  candidate {c.path} {c.length_m or c.error}", file=sys.stderr)
        return 1
    g = res.geometry
    assert g is not None and res.candidate is not None
    print(
        f"{track}{'/' + res.layout if res.layout else ''}: line {g.length_m:.0f} m"
        f"{f' (game {length:.0f} m)' if length else ''}, {len(g.apexes)} corners ({len(g.corners)} groups), {res.reason}"
    )
    print(f"source: {res.candidate.path}")
    from .analysis.circuit_names import corner_names

    names = corner_names(g, track)
    print(" #  dir    kind      sev     Rmin   start    apex     end   (m along the line)  name")
    for c in g.apexes:
        print(
            f"{c.number:2d}  {c.direction:5s}  {c.kind:8s}  {c.severity:6s} {c.min_radius_m:5.0f}"
            f"  {c.start_m:6.0f}  {c.apex_m:6.0f}  {c.end_m:6.0f}   {names.get(c.number, '')}"
        )
    if not names:
        print("(no names for this circuit: numbered by the geometry only, numbering not verified)")
    if getattr(a, "map", None):
        from .analysis.circuit_names import all_straights
        from .analysis.render import write_svg

        out_path = write_svg(
            a.map,
            g,
            names,
            all_straights(track, g),
            title=f"{track}{'/' + res.layout if res.layout else ''}",
        )
        print(f"map written to {out_path} (numbers = apexes of the game's line)")
    if a.what:
        from .analysis.corner_text import describe_corner, find_corner

        found = find_corner(g, a.what, names)
        if not found:
            print(f"\nno corner matches {a.what!r}")
        for n in found:
            print(f"\n{a.what!r}: " + describe_corner(g, n, cfg.engineer.language, names))
    if result is not None:
        _print_lap_comparison(g, names, result, cfg)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg = _apply_overrides(load_config(args.config), args)
        setup_logging(
            cfg.logging.level,
            json_logs=cfg.logging.json_logs,
            file=cfg.logging.file,
            console=cfg.logging.level == "DEBUG" or args.cmd != "run" or args.plain,
        )
        handler = {
            "run": _cmd_run,
            "record": _cmd_record,
            "dump-shm": _cmd_dump,
            "say": _cmd_say,
            "rules": _cmd_rules,
            "corners": _cmd_corners,
            "circuit-maps": _cmd_circuit_maps,
            "llm-check": _cmd_llm_check,
            "ask": _cmd_ask,
            "eval": _cmd_eval,
            "trim": _cmd_trim,
            "bind": _cmd_bind,
            "devices": _cmd_devices,
            "mic-test": _cmd_mic_test,
            "voices": _cmd_voices,
            "stt-test": _cmd_stt_test,
            "voice": _cmd_voice,
            "tyre-corners": _cmd_tyre_corners,
            "bindings": _cmd_bindings,
            "input-test": _cmd_input_test,
            "debrief": _cmd_debrief,
            "checklist": _cmd_checklist,
            "phrases": _cmd_phrases,
            "bridge-install": _cmd_bridge_install,
            "bridge-test": _cmd_bridge_test,
            "chat": _cmd_chat,
        }[args.cmd]
        return handler(cfg, args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
