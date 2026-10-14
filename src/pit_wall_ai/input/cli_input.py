"""``pit-wall bind``, ``bindings`` and ``input-test``: assign and check buttons without the game."""

from __future__ import annotations

import argparse
import queue
import sys
import time
from collections.abc import Callable
from pathlib import Path

import yaml

from ..config import AppConfig
from .actions import ACTIONS, BindingError, parse_bindings
from .backends import InputBackend, WinMMJoystickBackend, default_backend
from .model import Binding, InputEvent, InputId, refused_alone

BINDINGS_FILE = Path(
    "config/bindings.yaml"
)  # git-ignored, merged into ``input.bindings`` by load_config
MODIFIERS = {
    "LCTRL",
    "RCTRL",
    "CTRL",
    "LSHIFT",
    "RSHIFT",
    "SHIFT",
    "LALT",
    "RALT",
    "ALT",
    "LWIN",
    "RWIN",
}


def _read_file(path: Path) -> dict:
    if path.is_file():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _write_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Written by `pit-wall bind`. Git-ignored. Edit by hand if you like.\n"
    path.write_text(
        header + yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )


def capture_binding(
    backend: InputBackend,
    *,
    timeout_s: float = 15.0,
    out: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
) -> Binding | None:
    """Wait for the first press of any input and return it as a binding (held modifiers become a chord).

    A plain left/right click is refused: it is how the user is probably pressing the terminal.
    """
    q: queue.SimpleQueue = queue.SimpleQueue()
    backend.start(q.put)
    down: set[InputId] = set()
    deadline = clock() + timeout_s
    try:
        while clock() < deadline:
            try:
                ev: InputEvent = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if not ev.pressed:
                down.discard(ev.input)
                continue
            down.add(ev.input)
            i = ev.input
            if i.kind == "key" and i.code in MODIFIERS:
                continue  # a modifier is waiting for the key it modifies
            if refused_alone(i):
                out(f"  {i.label}: a plain click is not a good binding, press something else")
                continue
            mods = tuple(
                sorted(
                    (d for d in down if d != i and d.kind == "key" and d.code in MODIFIERS),
                    key=lambda d: d.code,
                )
            )
            return Binding(i, mods)
    finally:
        backend.stop()
    return None


def bind(
    action: str,
    cfg: AppConfig,
    backend: InputBackend,
    *,
    path: Path = BINDINGS_FILE,
    replace: bool = False,
    timeout_s: float = 15.0,
    out: Callable[[str], None] = print,
) -> int:
    if action not in ACTIONS:
        out(f"unknown action {action!r}; known: {', '.join(sorted(ACTIONS))}")
        return 2
    out(f"Press the control you want for {action} ({timeout_s:.0f} s)...")
    b = capture_binding(backend, timeout_s=timeout_s, out=out)
    if b is None:
        out("nothing was pressed")
        return 1
    out(f"Detected: {b.label}")
    data = _read_file(path)
    bindings = data.setdefault("input", {}).setdefault("bindings", {})
    merged = {a: list(specs) for a, specs in {**cfg.input.bindings, **bindings}.items()}
    current = [] if replace else list(merged.get(action, []))
    if b.to_spec() not in current:
        current.append(b.to_spec())
    candidate = {**merged, action: current}
    try:
        parse_bindings(candidate)
    except BindingError as exc:
        out(f"not saved: {exc}")
        return 1
    bindings[action] = current
    _write_file(path, data)
    out(f"Saved to {path}")
    return 0


def cmd_bind(cfg: AppConfig, a: argparse.Namespace, *, backend: InputBackend | None = None) -> int:
    if a.clear:
        data = _read_file(BINDINGS_FILE)
        data.setdefault("input", {}).setdefault("bindings", {})[a.action] = []
        _write_file(BINDINGS_FILE, data)
        print(f"{a.action}: cleared in {BINDINGS_FILE}")
        return 0
    return bind(
        a.action,
        cfg,
        backend or default_backend(cfg.input.poll_hz),
        replace=a.replace,
        timeout_s=a.timeout,
    )


def cmd_bindings(cfg: AppConfig, a: argparse.Namespace) -> int:
    try:
        parsed = parse_bindings(cfg.input.bindings)
    except BindingError as exc:
        print(f"invalid bindings: {exc}", file=sys.stderr)
        return 1
    if not parsed or not any(parsed.values()):
        print("no bindings yet: use `pit-wall bind push_to_talk`")
    for action in sorted(parsed):
        for b in parsed[action]:
            print(f"{action:18s} {b.label}")
    print("\nconnected joysticks and wheels:")
    try:
        devices = WinMMJoystickBackend(cfg.input.poll_hz).devices()
    except OSError as exc:
        print(f"  (cannot list them: {exc})")
        devices = []
    for idx, dev, name, buttons in devices:
        print(f"  {name}  [{dev}]  {buttons} buttons  (WinMM slot {idx})")
    if not devices:
        print("  none found")
    return 0


def cmd_input_test(
    cfg: AppConfig, a: argparse.Namespace, *, backend: InputBackend | None = None
) -> int:
    backend = backend or default_backend(cfg.input.poll_hz)
    q: queue.SimpleQueue = queue.SimpleQueue()
    backend.start(q.put)
    print(f"Press and release things for {a.seconds:.0f} s (Ctrl+C to stop)...")
    t0 = time.monotonic()
    n = 0
    try:
        while time.monotonic() - t0 < a.seconds:
            try:
                ev: InputEvent = q.get(timeout=0.1)
            except queue.Empty:
                continue
            n += 1
            print(
                f"{time.monotonic() - t0:7.2f} s  {'press  ' if ev.pressed else 'release'}  {ev.input.label}"
            )
    except KeyboardInterrupt:
        pass
    finally:
        backend.stop()
    print(f"{n} events")
    return 0
