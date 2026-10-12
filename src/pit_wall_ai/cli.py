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
from .logging_setup import setup_logging
from .telemetry import shm_structs as S
from .telemetry.ac_shared_memory import ACSharedMemorySource
from .telemetry.console import run_dashboard
from .telemetry.dump import RangeTracker, format_dump, report_json
from .telemetry.poller import TelemetryPoller
from .telemetry.replay import ReplaySource
from .telemetry.source import TelemetrySource

_SESSIONS = {kind.name.lower(): int(kind) for kind in S.SessionKind}


def build_source(cfg: AppConfig) -> TelemetrySource:
    t = cfg.telemetry
    if t.source == "replay":
        if not t.replay.file:
            raise ConfigError("telemetry.source is 'replay' but no file was given (--file)")
        if not Path(t.replay.file).is_file():
            raise ConfigError(f"replay file not found: {t.replay.file}")
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
        sp.add_argument("--file", help="recording to replay (.ld or MoTeC .csv)")
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
    if a.source:
        t["source"] = a.source
    if a.file:
        t["replay"]["file"] = a.file
        t["source"] = "replay" if not a.source else a.source
    if a.speed is not None:
        t["replay"]["speed"] = a.speed
    if a.start is not None:
        t["replay"]["start_s"] = a.start
    if a.loop:
        t["replay"]["loop"] = True
    if a.hz is not None:
        t["hz"] = a.hz
    if a.log_level:
        data["logging"]["level"] = a.log_level
    return AppConfig.model_validate(data)


def _cmd_run(cfg: AppConfig, a: argparse.Namespace) -> int:
    source = build_source(cfg)
    poller = TelemetryPoller(
        source,
        hz=cfg.telemetry.hz,
        buffer_seconds=cfg.telemetry.buffer_seconds,
        retry_s=cfg.telemetry.retry_s,
    )
    poller.start()
    stop = threading.Event()
    try:
        if a.plain:
            t0 = time.monotonic()
            while not stop.is_set():
                s, st = poller.buffer.latest(), poller.stats
                if s is None:
                    print(f"[{st.status}] waiting for telemetry... {st.last_error}", flush=True)
                else:
                    print(
                        f"[{st.status}] {s.speed_kmh:6.1f} km/h gear {s.gear} lap {s.completed_laps} "
                        f"pos {s.normalized_pos:.3f} {st.measured_hz:5.1f} Hz drop {st.dropped}",
                        flush=True,
                    )
                if st.status == "ended" or (a.duration and time.monotonic() - t0 >= a.duration):
                    break
                stop.wait(1.0)
        else:
            run_dashboard(poller, stop=stop, duration_s=a.duration)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
    return 0


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
        return _cmd_run(cfg, args) if args.cmd == "run" else _cmd_dump(cfg, args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
