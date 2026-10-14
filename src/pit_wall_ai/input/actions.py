"""From raw press/release events to actions: hold, tap, chords, conflicts.

The manager owns no thread and no hardware: backends call :meth:`ActionManager.feed` and the actions come
out through a callback, so the whole logic is tested with a fake backend.

* A **hold** action (``push_to_talk``) emits ``press`` when its binding goes down and ``release`` when the last
  of its bindings goes up. The keyboard's auto-repeat does not matter: a press of an input already down is
  ignored.
* A **tap** action emits ``tap`` on the press.
* A binding with ``with:`` modifiers only counts when those are down.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .model import Binding, InputEvent, InputId

log = logging.getLogger(__name__)

HOLD_ACTIONS = {"push_to_talk"}
TAP_ACTIONS = {
    "cancel",
    "repeat_last",
    "mute_engineer",
    "feedback_better",
    "feedback_same",
    "feedback_worse",
    "checklist_next",
    "checklist_skip",
}
ACTIONS = HOLD_ACTIONS | TAP_ACTIONS

ActionCallback = Callable[[str, str], None]  # (action, phase) phase: "press" | "release" | "tap"


class BindingError(ValueError):
    pass


def parse_bindings(raw: Mapping[str, list[dict]] | None) -> dict[str, list[Binding]]:
    """Validate the ``input.bindings`` section: known actions, known inputs, no input on two actions."""
    out: dict[str, list[Binding]] = {}
    seen: dict[tuple, str] = {}
    for action, specs in (raw or {}).items():
        if action not in ACTIONS:
            raise BindingError(f"unknown action {action!r}; known: {', '.join(sorted(ACTIONS))}")
        bindings = []
        for spec in specs or []:
            try:
                b = Binding.from_spec(spec)
            except (KeyError, ValueError, TypeError) as exc:
                raise BindingError(f"{action}: invalid binding {spec!r}: {exc}") from exc
            ident = (
                b.input.kind,
                b.input.device,
                b.input.code,
                tuple((r.kind, r.device, r.code) for r in b.requires),
            )
            if ident in seen and seen[ident] != action:
                raise BindingError(f"{b.label} is bound to both {seen[ident]} and {action}")
            seen[ident] = action
            bindings.append(b)
        out[action] = bindings
    return out


@dataclass
class ActionManager:
    bindings: dict[str, list[Binding]]
    callback: ActionCallback
    _down: set[InputId] = field(default_factory=set)
    _active: dict[str, set[InputId]] = field(
        default_factory=dict
    )  # hold action -> inputs keeping it down

    def feed(self, event: InputEvent) -> None:
        i = event.input
        if event.pressed:
            if i in self._down:  # auto-repeat of a key that is already down
                return
            self._down.add(i)
            self._on_press(i)
        else:
            if i not in self._down:
                return
            self._down.discard(i)
            self._on_release(i)

    def _satisfied(self, b: Binding) -> bool:
        return all(r in self._down for r in b.requires)

    def _on_press(self, i: InputId) -> None:
        for action, bindings in self.bindings.items():
            for b in bindings:
                if b.input != i or not self._satisfied(b):
                    continue
                if action in HOLD_ACTIONS:
                    holders = self._active.setdefault(action, set())
                    first = not holders
                    holders.add(i)
                    if first:
                        self._emit(action, "press")
                else:
                    self._emit(action, "tap")

    def _on_release(self, i: InputId) -> None:
        for action, holders in list(self._active.items()):
            if i in holders:
                holders.discard(i)
                if not holders:
                    del self._active[action]
                    self._emit(action, "release")
                continue
            # a modifier of the holding binding went up: the chord is broken
            for b in self.bindings.get(action, []):
                if i in b.requires and b.input in holders:
                    holders.discard(b.input)
            if not holders:
                del self._active[action]
                self._emit(action, "release")

    def _emit(self, action: str, phase: str) -> None:
        try:
            self.callback(action, phase)
        except Exception:  # noqa: BLE001 - a broken consumer must not kill the input thread
            log.exception("action callback failed", extra={"action": action, "phase": phase})

    def holding(self, action: str) -> bool:
        return bool(self._active.get(action))

    def release_all(self) -> None:
        """When the devices go away: nothing stays 'held'."""
        for action in list(self._active):
            del self._active[action]
            self._emit(action, "release")
        self._down.clear()
