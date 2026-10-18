"""The practice checklist (``docs/PHASE8_PLAN.md`` 1.3): typed items, validated operations, undo, and items that tick
themselves from the telemetry.

The model (or the driver) never edits text: every change is an operation (``add``, ``remove``, ``replace``,
``reorder``, ``skip``, ``set_rule``) that is validated and applied or refused with a reason, shown as a diff, and can
be undone. A guard *tells* (a rain tyre on a dry track) but never forbids: the driver's explicit order wins and is kept
with its reason.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .laps import LapRecord

KINDS = ("stint", "quali_sim", "race_sim", "setup_test", "info")
STATUSES = ("pending", "active", "done", "skipped", "removed")
SOURCES = ("planner", "user", "llm")
PARAMS = {"compound", "laps", "fuel_l", "conditions", "text"}
RULES = {"mandatory_stop", "stop_requires_tyres", "compounds_distinct_required", "window"}
FUEL_TOLERANCE_L = 1.5  # a stint starts "with 15 L" when the first lap begins within this of it
WET_WORDS = ("wet", "rain", "inter", "mojado", "lluvia", "regen")


@dataclass
class Item:
    id: str
    kind: str
    params: dict = field(default_factory=dict)
    status: str = "pending"
    source: str = "user"
    reason: str = ""
    evidence: str = ""
    note: str = ""  # something to show, not to guess (an ambiguous match)

    def title(self) -> str:
        p = self.params
        bits = []
        if p.get("laps"):
            bits.append(f"{p['laps']} laps")
        if p.get("compound"):
            bits.append(str(p["compound"]))
        if p.get("fuel_l") is not None:
            bits.append(f"{p['fuel_l']} L")
        if p.get("text"):
            bits.append(str(p["text"]))
        return f"{self.kind}: " + ", ".join(bits) if bits else self.kind


@dataclass
class OpResult:
    ok: bool
    message: str
    warnings: list[str] = field(default_factory=list)
    diff: list[str] = field(default_factory=list)


class Checklist:
    def __init__(self) -> None:
        self.items: list[Item] = []
        self.rules: dict = {}
        self._n = 0
        self.path: Path | None = None  # where it is kept (set when it is loaded from a file)
        self._undo: list[tuple[list[Item], dict, int]] = []

    # -- lookup -------------------------------------------------------------------------------------------
    def get(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id and i.status != "removed"), None)

    def visible(self) -> list[Item]:
        return [i for i in self.items if i.status != "removed"]

    # -- operations ---------------------------------------------------------------------------------------
    def apply(self, op: dict, *, source: str = "user", conditions: str | None = None) -> OpResult:
        """One validated operation. Invalid ones are refused with the reason; nothing changes in that case."""
        name = op.get("op")
        handler = {
            "add": self._add,
            "remove": self._remove,
            "replace": self._replace,
            "reorder": self._reorder,
            "skip": self._skip,
            "set_rule": self._set_rule,
        }.get(name)
        if handler is None:
            return OpResult(
                False,
                f"unknown operation {name!r}; valid: add, remove, replace, reorder, skip, set_rule",
            )
        snapshot = (copy.deepcopy(self.items), dict(self.rules), self._n)
        res = handler(op, source, conditions)
        if res.ok:
            self._undo.append(snapshot)
            self._undo = self._undo[-30:]
        return res

    def undo(self) -> OpResult:
        if not self._undo:
            return OpResult(False, "nothing to undo")
        self.items, self.rules, self._n = self._undo.pop()
        return OpResult(True, "undone")

    @staticmethod
    def _clean_params(params) -> tuple[dict | None, str]:
        if not isinstance(params, dict):
            return None, "params must be an object"
        bad = set(params) - PARAMS
        if bad:
            return None, f"unknown parameter(s): {', '.join(sorted(bad))}"
        out = dict(params)
        if "laps" in out:
            try:
                out["laps"] = int(out["laps"])
            except (TypeError, ValueError):
                return None, "laps must be a number"
            if not 1 <= out["laps"] <= 200:
                return None, "laps must be between 1 and 200"
        if "fuel_l" in out:
            try:
                out["fuel_l"] = float(out["fuel_l"])
            except (TypeError, ValueError):
                return None, "fuel_l must be a number"
            if not 0 <= out["fuel_l"] <= 300:
                return None, "fuel_l must be between 0 and 300"
        return out, ""

    def _guard(self, params: dict, conditions: str | None) -> list[str]:
        """Tells, never forbids: a wet tyre on a dry track or a slick on a wet one."""
        comp = str(params.get("compound", "")).lower()
        if not comp or conditions not in ("dry", "wet"):
            return []
        is_wet_tyre = any(w in comp for w in WET_WORDS)
        if is_wet_tyre and conditions == "dry":
            return [f"{params['compound']} is a wet tyre and the track reads dry"]
        if not is_wet_tyre and conditions == "wet":
            return [f"{params['compound']} is not a wet tyre and the track reads wet"]
        return []

    def _add(self, op, source, conditions) -> OpResult:
        kind = op.get("kind")
        if kind not in KINDS:
            return OpResult(False, f"kind must be one of {', '.join(KINDS)}")
        params, why = self._clean_params(op.get("params", {}))
        if params is None:
            return OpResult(False, why)
        if kind == "stint" and not params.get("laps"):
            return OpResult(False, "a stint needs laps")
        self._n += 1
        item = Item(
            id=f"C{self._n}",
            kind=kind,
            params=params,
            source=source if source in SOURCES else "user",
            reason=str(op.get("reason", "")),
        )
        at = op.get("position")
        if isinstance(at, int) and 0 <= at <= len(self.items):
            self.items.insert(at, item)
        else:
            self.items.append(item)
        warnings = self._guard(params, conditions)
        return OpResult(
            True, f"added {item.id}: {item.title()}", warnings, [f"+ {item.id} {item.title()}"]
        )

    def _need(self, op):
        item = self.get(str(op.get("id", "")))
        return item, (None if item else OpResult(False, f"no item {op.get('id')!r}"))

    def _remove(self, op, source, conditions) -> OpResult:
        item, err = self._need(op)
        if err:
            return err
        item.status, item.reason = "removed", str(op.get("reason", "")) or item.reason
        return OpResult(True, f"removed {item.id}", diff=[f"- {item.id} {item.title()}"])

    def _skip(self, op, source, conditions) -> OpResult:
        item, err = self._need(op)
        if err:
            return err
        if item.status == "done":
            return OpResult(False, f"{item.id} is already done")
        item.status, item.reason = "skipped", str(op.get("reason", "")) or item.reason
        return OpResult(True, f"skipped {item.id}", diff=[f"~ {item.id} skipped"])

    def _replace(self, op, source, conditions) -> OpResult:
        item, err = self._need(op)
        if err:
            return err
        params, why = self._clean_params({**item.params, **op.get("params", {})})
        if params is None:
            return OpResult(False, why)
        before = item.title()
        item.params, item.status = params, "pending"
        item.source = source if source in SOURCES else item.source
        item.reason = str(op.get("reason", "")) or item.reason
        return OpResult(
            True,
            f"changed {item.id}",
            self._guard(params, conditions),
            [f"- {item.id} {before}", f"+ {item.id} {item.title()}"],
        )

    def _reorder(self, op, source, conditions) -> OpResult:
        item, err = self._need(op)
        if err:
            return err
        to = op.get("position")
        if not isinstance(to, int) or not 0 <= to < len(self.items):
            return OpResult(False, "position must be a valid index")
        self.items.remove(item)
        self.items.insert(to, item)
        return OpResult(True, f"moved {item.id} to position {to}", diff=[f"> {item.id} -> {to}"])

    def _set_rule(self, op, source, conditions) -> OpResult:
        rule = op.get("rule")
        if rule not in RULES:
            return OpResult(False, f"rule must be one of {', '.join(sorted(RULES))}")
        value = op.get("value")
        if rule == "mandatory_stop" and value not in ("none", "one", "n"):
            return OpResult(False, "mandatory_stop is none, one or n")
        if rule in ("stop_requires_tyres", "compounds_distinct_required") and not isinstance(
            value, bool
        ):
            return OpResult(False, f"{rule} is true or false")
        if (
            rule == "window"
            and value is not None
            and not (
                isinstance(value, list | tuple)
                and len(value) == 2
                and all(isinstance(x, int) for x in value)
            )
        ):
            return OpResult(False, "window is [lap_from, lap_to] or null")
        self.rules[rule] = value
        return OpResult(True, f"{rule} = {value}", diff=[f"rule {rule} = {value}"])

    # -- ticking itself -----------------------------------------------------------------------------------
    def autotick(self, laps: list[LapRecord]) -> list[str]:
        """Mark items from the laps driven. Returns what changed; ambiguous matches are noted, not guessed."""
        changed: list[str] = []
        pending = [
            i
            for i in self.items
            if i.status in ("pending", "active") and i.kind in ("stint", "quali_sim", "race_sim")
        ]
        cursor = (
            0  # index into laps: items complete in order, each from where the previous one ended
        )
        for item in [i for i in self.items if i.status == "done" and i.evidence]:
            last = _last_lap_of(item.evidence)
            if last is not None:
                cursor = max(cursor, last)
        for item in pending:
            want = int(item.params.get("laps") or 1)
            comp = str(item.params.get("compound", "")).lower()
            fuel = item.params.get("fuel_l")
            run: list[LapRecord] = []
            item.note = ""
            for lap in laps:
                if lap.number <= cursor:
                    continue
                if comp and lap.compound.lower() != comp and comp not in lap.compound.lower():
                    if run:
                        break
                    continue
                if (
                    not run
                    and fuel is not None
                    and abs(lap.fuel_start - float(fuel)) > FUEL_TOLERANCE_L
                ):
                    item.note = f"laps on {lap.compound or '?'} start with {lap.fuel_start:.1f} L, not {float(fuel):g} L"
                    continue
                if lap.eligible_for_best or item.kind != "stint":
                    run.append(lap)
                elif run:
                    break
                if len(run) >= want:
                    break
            if len(run) >= want:
                item.status = "done"
                item.evidence = f"laps {run[0].number}-{run[-1].number}"
                changed.append(f"{item.id} done ({item.evidence})")
                cursor = run[-1].number
            elif run and item.status != "active":
                item.status = "active"
                changed.append(f"{item.id} active ({len(run)}/{want} laps)")
            else:
                break  # items go in order: a later one is not started before this one is done
        return changed

    # -- views and files ----------------------------------------------------------------------------------
    def render(self) -> str:
        marks = {"pending": "[ ]", "active": "[~]", "done": "[x]", "skipped": "[-]"}
        lines = []
        for i, it in enumerate(self.visible()):
            extra = f"  ({it.evidence})" if it.evidence else (f"  ! {it.note}" if it.note else "")
            lines.append(f"{marks.get(it.status, '[ ]')} {i}  {it.id}  {it.title()}{extra}")
        if self.rules:
            lines.append("rules: " + ", ".join(f"{k}={v}" for k, v in self.rules.items()))
        return "\n".join(lines) or "(empty checklist)"

    def to_dict(self) -> dict:
        return {
            "items": [asdict(i) for i in self.items],
            "rules": self.rules,
            "n": self._n,
            "undo": [
                {"items": [asdict(i) for i in items], "rules": rules, "n": n}
                for items, rules, n in self._undo[-10:]
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Checklist:
        c = cls()
        c.items = [Item(**i) for i in d.get("items", [])]
        c.rules = dict(d.get("rules", {}))
        c._n = int(d.get("n", len(c.items)))
        c._undo = [
            ([Item(**i) for i in u.get("items", [])], dict(u.get("rules", {})), int(u.get("n", 0)))
            for u in d.get("undo", [])
        ]
        return c

    def save(self, path: str | Path | None = None) -> None:
        p = Path(path) if path else self.path
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Checklist:
        try:
            c = cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            c = cls()
        c.path = Path(path)
        return c


def _last_lap_of(evidence: str) -> int | None:
    try:
        return int(evidence.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None
