"""``pit-wall checklist``: look at and edit the practice checklist from the terminal (the same operations the model uses)."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..config import AppConfig
from ..home import home_dir
from .checklist import Checklist


def _path(cfg: AppConfig, a: argparse.Namespace) -> Path:
    if getattr(a, "path", None):
        return Path(a.path)
    if cfg.engineer.checklist.path:
        return Path(cfg.engineer.checklist.path)
    return home_dir() / "checklist.json"


def cmd_checklist(
    cfg: AppConfig, a: argparse.Namespace, *, out: Callable[[str], None] = print
) -> int:
    path = _path(cfg, a)
    cl = Checklist.load(path)
    act = a.action
    if act == "show":
        out(cl.render())
        return 0
    if act == "clear":
        cl = Checklist()
        cl.path = path
        cl.save()
        out("the checklist is empty")
        return 0
    if act == "undo":
        res = cl.undo()
    else:
        params = {
            k: v
            for k, v in (
                ("compound", a.compound),
                ("laps", a.laps),
                ("fuel_l", a.fuel),
                ("text", a.text),
            )
            if v is not None
        }
        op: dict = {"add": {"op": "add", "kind": a.kind, "params": params}}.get(act, {})
        if act in ("remove", "skip", "replace", "reorder"):
            op = {"op": act, "id": a.id}
            if act == "replace":
                op["params"] = params
            if act == "reorder":
                op["position"] = a.position
        if act == "rule":
            value = a.value
            if a.rule in ("stop_requires_tyres", "compounds_distinct_required"):
                value = str(a.value).lower() in ("true", "1", "yes", "si")
            elif a.rule == "window" and a.value not in (None, "null"):
                value = [int(x) for x in str(a.value).split("-")]
            op = {"op": "set_rule", "rule": a.rule, "value": value}
        if a.reason:
            op["reason"] = a.reason
        res = cl.apply(op, source="user", conditions=a.conditions)
    out(res.message)
    for line in res.diff:
        out("  " + line)
    for w in res.warnings:
        out("  warning: " + w + " (your order stands)")
    if res.ok:
        cl.path = path
        cl.save()
    out("\n" + cl.render())
    return 0 if res.ok else 1
