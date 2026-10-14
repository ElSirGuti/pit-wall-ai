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
    return ACSharedMemorySource()


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

    sub.add_parser(
        "rules", help="list the alert rules, their thresholds and the session policies"
    ).add_argument("--config", help="YAML config")

    chk = sub.add_parser(
        "llm-check", help="test the configured cloud model: models the key can use, latency, 429"
    )
    chk.add_argument("--config", help="YAML config")
    chk.add_argument("--calls", type=int, default=3, help="how many test calls (default 3)")
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
        "--session",
        choices=["practice", "qualify", "race", "hotlap"],
        default="practice",
        help="session kind for .ld/.csv logs, which do not say (default practice)",
    )

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


def _make_speech(cfg: AppConfig, *, mute: bool = False) -> SpeechOutput:
    v = cfg.engineer.voice
    if mute:
        return SpeechOutput(lambda: ConsoleTts())
    return SpeechOutput(
        lambda: create_backend(
            v.backend,
            language=cfg.engineer.language,
            voice_name=v.name,
            rate=v.rate,
            volume=v.volume,
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
    return speech


def _cmd_say(cfg: AppConfig, a: argparse.Namespace) -> int:
    lang = cfg.engineer.language
    text = a.text or M.say(lang, "test")
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


def _cmd_llm_check(cfg: AppConfig, a: argparse.Namespace) -> int:
    from .engineer.llmcheck import run_llm_check

    return run_llm_check(cfg.llm, calls=max(1, a.calls), allow_cloud=a.allow_cloud)


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
            "llm-check": _cmd_llm_check,
        }[args.cmd]
        return handler(cfg, args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
