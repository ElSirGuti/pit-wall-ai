"""Facts: every number handed to the model (or said locally) with the identity it belongs to.

The compact JSON context of the chat is built *from* a :class:`FactSheet`, so the grounding check knows not
only that "98" exists in the context but that it is the core temperature of the **front right** tyre. That is
what lets it catch a correct number attached to the wrong wheel, corner or lap.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


def norm(text: str) -> str:
    """Lowercase, accents removed, punctuation to single spaces."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


@dataclass(frozen=True)
class Subject:
    """What a number is about: a wheel, a corner, a lap, or the session as a whole (``None``)."""

    kind: str  # "wheel" | "corner" | "lap"
    id: str  # "FL" | "6" | "12"


@dataclass(frozen=True)
class Fact:
    key: str  # dotted path in the context, e.g. "tyres.FL.core_c"
    value: Any  # number, string or bool: what goes into the context
    unit: str = ""
    subject: Subject | None = None
    # every numeric rendering a correct answer may use (seconds and milliseconds of the same lap time...)
    numbers: tuple[float, ...] = ()
    label: str = ""

    def accepts(self) -> tuple[float, ...]:
        if self.numbers:
            return self.numbers
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            return ()
        return (float(self.value),)


@dataclass
class FactSheet:
    facts: list[Fact] = field(default_factory=list)
    # aliases of every subject as they may appear in a sentence ("delantero izquierdo", "curvone", "vuelta 5")
    aliases: dict[Subject, tuple[str, ...]] = field(default_factory=dict)
    lang: str = "es"
    extra_numbers: list[float] = field(
        default_factory=list
    )  # numbers of the question, alert texts...

    def add(self, fact: Fact) -> None:
        self.facts.append(fact)

    def alias(self, subject: Subject, *names: str) -> None:
        merged = list(self.aliases.get(subject, ()))
        merged += [norm(n) for n in names if norm(n)]
        self.aliases[subject] = tuple(dict.fromkeys(merged))

    def context(self) -> dict[str, Any]:
        """The nested JSON of the facts, with units in the key names; order of insertion."""
        root: dict[str, Any] = {}
        for f in self.facts:
            node = root
            *path, leaf = f.key.split(".")
            for p in path:
                node = node.setdefault(p, {})
            node[leaf] = f.value
        return root

    def by_key(self, key: str) -> Fact | None:
        return next((f for f in self.facts if f.key == key), None)

    def value(self, key: str, default: Any = None) -> Any:
        f = self.by_key(key)
        return default if f is None else f.value
