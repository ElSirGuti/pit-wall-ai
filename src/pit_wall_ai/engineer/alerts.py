"""The data an alert carries from the rule that fired it to the voice (and to the log)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Priority(enum.IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3

    @classmethod
    def parse(cls, name: str) -> Priority:
        return cls[name.upper()]


@dataclass(frozen=True)
class Alert:
    rule: str  # id of the rule that fired it (goes to the log, never to the voice)
    key: str  # rule + the thing it is about ("lockup:FL"): the cooldown unit
    priority: Priority
    text: str  # what is said
    values: dict = field(default_factory=dict)  # the numbers that made the rule fire (for the log)
    t: float = 0.0  # snapshot time of the sample that triggered it
    ttl_s: float = 8.0  # useless after this long without being said

    @property
    def interrupts(self) -> bool:
        return self.priority >= Priority.CRITICAL
