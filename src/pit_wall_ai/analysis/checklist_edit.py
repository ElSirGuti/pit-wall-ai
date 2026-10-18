"""Editing the checklist by voice or text: the model turns the request into operations, the code validates them.

The model only ever returns a JSON list of operations (``add``, ``remove``, ``replace``, ``reorder``, ``skip``,
``set_rule``); each goes through :meth:`Checklist.apply`. The set is all-or-nothing: if one operation is refused, the
ones before it are undone and the reason is given. The result is a short diff (and the guards' warnings) that is read
back to the driver, who can say "deshaz".
"""

from __future__ import annotations

import json
import logging
import re

from ..engineer.llm import LLMRequest
from .checklist import KINDS, RULES, Checklist

log = logging.getLogger(__name__)

_SYSTEM = (
    "You edit a practice-session checklist for a race driver. Answer with ONLY a JSON array of operations, no text. "
    "Operations: "
    '{"op":"add","kind":"stint|quali_sim|race_sim|setup_test|info","params":{"compound":str,"laps":int,"fuel_l":number,"text":str},"reason":str,"position":int?} | '
    '{"op":"remove","id":str,"reason":str} | {"op":"replace","id":str,"params":{...},"reason":str} | '
    '{"op":"reorder","id":str,"position":int} | {"op":"skip","id":str,"reason":str} | '
    '{"op":"set_rule","rule":"mandatory_stop|stop_requires_tyres|compounds_distinct_required|window","value":...}. '
    "Use the ids of the checklist you are given. If the request cannot be done with these operations, answer []. "
    "Never invent numbers the driver did not say."
)


def parse_ops(text: str) -> list[dict] | None:
    """The JSON array in a model answer (it may come inside a code fence); ``None`` if there is none."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        ops = json.loads(m.group(0))
    except ValueError:
        return None
    return ops if isinstance(ops, list) and all(isinstance(o, dict) for o in ops) else None


def apply_ops(
    checklist: Checklist, ops: list[dict], *, source: str = "llm", conditions: str | None = None
) -> tuple[bool, str]:
    """Apply a set of operations atomically; returns (ok, what to tell the driver)."""
    lines: list[str] = []
    warnings: list[str] = []
    for applied, op in enumerate(ops):
        res = checklist.apply(op, source=source, conditions=conditions)
        if not res.ok:
            for _ in range(applied):
                checklist.undo()
            return False, f"No lo cambié: {res.message}."
        lines += res.diff
        warnings += res.warnings
    text = "; ".join(lines) if lines else "sin cambios"
    if warnings:
        text += ". Aviso: " + "; ".join(warnings) + " (tu orden manda, lo dejo así)"
    return True, text


def edit_with_model(
    client,
    checklist: Checklist,
    instruction: str,
    *,
    lang: str = "es",
    conditions: str | None = None,
) -> tuple[bool, str]:
    """Ask the model for operations and apply them. Never raises: a failure is a sentence."""
    if client is None:
        return (
            False,
            "Sin el modelo no puedo editar la lista con frases libres; usa `pit-wall checklist`.",
        )
    ctx = {
        "items": [
            {"id": i.id, "kind": i.kind, "params": i.params, "status": i.status}
            for i in checklist.visible()
        ],
        "rules": checklist.rules,
        "kinds": list(KINDS),
        "rule_names": sorted(RULES),
    }
    request = LLMRequest(system=_SYSTEM, user=instruction, context=ctx, max_output_tokens=300)
    try:
        resp = client.ask(request, urgent=True)
    except Exception:  # noqa: BLE001 - editing must never break the chat
        log.exception("the model failed to edit the checklist")
        return False, "No pude consultar el modelo para cambiar la lista."
    if resp is None:
        return False, "No pude consultar el modelo para cambiar la lista."
    ops = parse_ops(resp.text)
    if ops is None:
        return False, "No entendí qué cambiar en la lista."
    if not ops:
        return False, "Eso no lo puedo hacer con las operaciones de la lista."
    return apply_ops(checklist, ops, source="llm", conditions=conditions)
