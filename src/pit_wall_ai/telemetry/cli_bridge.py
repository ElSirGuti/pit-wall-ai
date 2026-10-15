"""``pit-wall bridge-install`` and ``pit-wall bridge-test``: the CSP Lua bridge, installed and checked."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from ..config import AppConfig
from .csp_bridge import BridgeReceiver


def _source_dir() -> Path | None:
    """tools/csp_bridge/pitwall_bridge of a source checkout (the app is not shipped inside the wheel)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "tools" / "csp_bridge" / "pitwall_bridge"
        if (cand / "manifest.ini").is_file():
            return cand
    return None


def _ac_roots(cfg: AppConfig, extra: list[str] | None) -> list[Path]:
    from ..analysis.tracks import find_tracks_dirs

    roots: list[Path] = []
    for tracks in find_tracks_dirs([*(extra or []), *cfg.analysis.ac_dirs]):
        root = tracks.parent.parent  # <assettocorsa>/content/tracks
        if root not in roots:
            roots.append(root)
    return roots


def cmd_bridge_install(cfg: AppConfig, a: argparse.Namespace, *, out=print) -> int:
    src = _source_dir()
    if src is None:
        out("the app was not found: run this from a checkout of the repository (tools/csp_bridge)")
        return 1
    roots = _ac_roots(cfg, a.ac_dir)
    if not roots:
        out(
            "no Assetto Corsa folder found: pass --ac-dir <...>\assettocorsa or set analysis.ac_dirs"
        )
        return 1
    dest = roots[0] / "apps" / "lua" / "pitwall_bridge"
    if not (roots[0] / "extension").is_dir():
        out(f"warning: {roots[0]} has no extension folder: the app needs Custom Shaders Patch")
    dest.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
    out(f"installed in {dest}")
    out(
        "next: in the game, open the app bar and enable 'Pit Wall Bridge' once; then `pit-wall bridge-test`"
    )
    return 0


def cmd_bridge_test(cfg: AppConfig, a: argparse.Namespace, *, out=print) -> int:
    port = cfg.telemetry.csp_bridge.port
    rx = BridgeReceiver(port, max_age_s=cfg.telemetry.csp_bridge.max_age_s)
    if not rx.start():
        out(f"port {port} is busy (is `pit-wall voice` or `run` already open? close it first)")
        return 1
    out(f"listening on 127.0.0.1:{port} for {a.seconds:.0f} s: start a session in the game...")
    last = None
    t_end = time.monotonic() + a.seconds
    try:
        while time.monotonic() < t_end:
            time.sleep(0.5)
            d = rx.latest()
            if d is not None and d != last:
                last = d
                w = d.get("wear", [])
                out(
                    f"  wear FL/FR/RL/RR: {' / '.join(f'{x:.1f}' for x in w)} %   tyre {d.get('tyre', '?')}"
                    f"   flag {d.get('flag')}   penalty {d.get('penalty')}   damage {d.get('damage')}"
                )
    finally:
        rx.stop()
    out(f"{rx.received} messages received")
    if rx.received == 0:
        out(
            "nothing arrived: is the app enabled in the game's app bar? (`pit-wall bridge-install` copies it)"
        )
        return 1
    return 0
