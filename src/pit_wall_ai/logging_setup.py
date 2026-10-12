"""Structured logging on the standard library: ``key=value`` for the console, JSON lines for files/tools.

Anything passed as ``extra={...}`` to a logging call becomes a field, so log lines stay machine-readable
without a logging dependency.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def _fields(record: logging.LogRecord) -> dict:
    return {
        k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")
    }


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        base = f"{ts} {record.levelname:<7} {record.name} | {record.getMessage()}"
        extras = " ".join(f"{k}={v!r}" for k, v in _fields(record).items())
        text = f"{base} {extras}".rstrip()
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_fields(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(
    level: str = "INFO", *, json_logs: bool = False, file: str | None = None, console: bool = True
) -> None:
    """Configure the root logger. Safe to call twice (handlers are replaced, not stacked)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_pit_wall", False):
            root.removeHandler(h)
    root.setLevel(level)
    fmt = JsonFormatter() if json_logs else KeyValueFormatter()
    handlers: list[logging.Handler] = []
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    if file:
        handlers.append(logging.FileHandler(file, encoding="utf-8"))
        handlers[-1].setFormatter(JsonFormatter())
    for h in handlers:
        if h.formatter is None:
            h.setFormatter(fmt)
        h._pit_wall = True  # type: ignore[attr-defined]
        root.addHandler(h)
