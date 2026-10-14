"""``pit-wall ask`` and ``pit-wall chat``: the terminal front end of the conversation."""

from __future__ import annotations

import argparse
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
