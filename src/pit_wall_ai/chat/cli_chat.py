"""``pit-wall ask`` and ``pit-wall chat``: the terminal front end of the conversation."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable

from ..config import AppConfig
from .runtime import ChatRuntime
from .session import ChatSession, make_client

EXIT_WORDS = {"salir", "exit", "quit", "q", "adios", "adiós"}
BANNER = {
    "es": "Ingeniero de carrera. Pregunta lo que quieras (salir para terminar).",
    "en": "Race engineer. Ask anything (type exit to finish).",
}


def _build(cfg: AppConfig, a: argparse.Namespace):
    """(runtime, session, poller|None, why-no-model)."""
    from ..telemetry.poller import TelemetryPoller

    live = not a.file
    # the missing-game warning is noise in a conversation: the answer to "where do I lose time" says it
    logging.getLogger("pit_wall_ai.analysis.trackctx").setLevel(logging.ERROR)
    runtime = ChatRuntime(cfg, live=live)
    poller = None
    if a.file:
        n = runtime.feed_file(a.file, session=a.session)
        print(f"[{n} samples of {a.file} loaded]", file=sys.stderr)
    else:
        from ..cli import build_source

        poller = TelemetryPoller(
            build_source(cfg), hz=cfg.telemetry.hz, buffer_seconds=5, retry_s=cfg.telemetry.retry_s
        )
        runtime.engine.attach(poller)
        poller.start()
        waited = 0.0
        while runtime.engine.view().last_snapshot is None and waited < a.wait:
            time.sleep(0.1)
            waited += 0.1
        if runtime.engine.view().last_snapshot is None:
            print(
                f"[no telemetry after {a.wait:.0f} s: answering without session data]",
                file=sys.stderr,
            )
    client, why = make_client(cfg)
    session = ChatSession(cfg, runtime.inputs, client, unavailable_reason=why)
    return runtime, session, poller


def _print_reply(reply, out: Callable[[str], None] = print) -> None:
    out(reply.text)
    out(f"  [{reply.tag}]")


def _close(runtime: ChatRuntime, poller) -> None:
    if poller is not None:
        poller.stop()
        runtime.engine.detach()


def cmd_ask(cfg: AppConfig, a: argparse.Namespace) -> int:
    runtime, session, poller = _build(cfg, a)
    try:
        _print_reply(session.ask(a.text))
    finally:
        _close(runtime, poller)
    return 0


def cmd_chat(cfg: AppConfig, a: argparse.Namespace, *, stdin=None) -> int:
    runtime, session, poller = _build(cfg, a)
    stdin = stdin or sys.stdin
    if not stdin.isatty() and hasattr(stdin, "reconfigure"):
        # piped text is UTF-8 in practice; the console code page would turn every accent into noise
        stdin.reconfigure(encoding="utf-8", errors="replace")
    print(BANNER[cfg.engineer.language])
    if session.client is None:
        print(f"[cloud model off: {session.unavailable_reason}]")
    try:
        while True:
            if stdin.isatty():
                print("> ", end="", flush=True)
            line = stdin.readline()
            if not line:
                break
            q = line.strip()
            if not q:
                continue
            if q.lower() in EXIT_WORDS:
                break
            _print_reply(session.ask(q))
    except KeyboardInterrupt:
        pass
    finally:
        _close(runtime, poller)
    return 0


def cmd_eval(cfg: AppConfig, a: argparse.Namespace) -> int:
    from pathlib import Path

    from . import evalset

    if not a.file:
        print(
            "eval needs --file (a recording): the cases are asked about a fixed session",
            file=sys.stderr,
        )
        return 2
    # the evaluation paces itself: give the client the room it needs and no daily cap
    rl = cfg.llm.rate_limit.model_copy(
        update={"initial_rpm": 12.0, "max_rpm": 12.0, "daily_budget": None, "adaptive": False}
    )
    cfg = cfg.model_copy(
        update={"llm": cfg.llm.model_copy(update={"rate_limit": rl, "cache_ttl_s": 0})}
    )
    runtime, session, poller = _build(cfg, a)
    only = {c.strip() for c in a.only.split(",")} if a.only else None
    print(
        f"[{len(evalset.CASES)} cases; model: {cfg.llm.model}; pace {a.pace:g} s]", file=sys.stderr
    )

    def show(r) -> None:
        mark = {"pass": "PASS", "fail": "FAIL", "skip": "skip"}[r.status]
        tag = r.reply.tag if r.reply else "-"
        print(f"{mark}  {r.case.id:18s} [{tag}]", flush=True)
        for why in r.reasons:
            print(f"      {why}", flush=True)

    try:
        report = evalset.run(session, only=only, pace_s=a.pace, on_result=show)
    finally:
        _close(runtime, poller)
    note = f"Model: `{cfg.llm.model}`, fallback: {', '.join(cfg.llm.fallback_models) or 'none'}. Recording: `{Path(a.file).name}`."
    Path(a.out).write_text(evalset.to_markdown(report, model_note=note), encoding="utf-8")
    s = report.summary()
    print(
        f"\npassed {s['passed']}/{s['ran']} (skipped {s['skipped']}); model answers {s['model_answers']}, "
        f"with removed claims {s['answers_with_violations']}; latency median "
        f"{(s['latency_median_ms'] or 0) / 1000:.2f} s, p95 {(s['latency_p95_ms'] or 0) / 1000:.2f} s"
    )
    print(f"report written to {a.out}")
    return 0 if s["failed"] == 0 else 1
