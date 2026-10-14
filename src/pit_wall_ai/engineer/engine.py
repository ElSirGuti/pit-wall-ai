"""The engineer: runs the rules, applies the session policy and decides WHEN each message is said.

Delivery rules (the point of this module):

* A **critical** alert (spin, puncture, fuel almost gone, black flag) is said at once and cuts whatever is
  being said.
* Everything else waits for a **calm moment** (brake and steering almost released for a moment, or the car
  stopped), never over braking or a corner, and is thrown away if it stays stale for its ``ttl``.
* One message at a time, with a minimum silence between two, and nothing while the game is not live.
* Each fired alert is logged with the rule that raised it and the numbers that made it fire; so is every
  message that is dropped, with the reason. That log is what thresholds are calibrated from.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..analysis.laps import LapTracker
from ..config import AnalysisConfig, CalmConfig, EngineerConfig, PolicyConfig
from ..events import Event, SessionChanged
from ..session.detector import SessionState
from ..telemetry.snapshot import Snapshot
from .alerts import Alert, Priority
from .rules import Rule, RuleContext, build_rules

log = logging.getLogger("pit_wall_ai.engineer.alerts")
MUTE_PHASES = {"menu", "replay", "paused"}
POLICY_KEYS = {"practice", "qualify", "race"}


class Speaker(Protocol):
    @property
    def busy(self) -> bool: ...

    def say(self, text: str, *, interrupt: bool = False, meta: dict | None = None): ...


class CalmDetector:
    """ "The driver can listen": brake and steering almost released for a moment, or the car stopped."""

    def __init__(self, cfg: CalmConfig) -> None:
        self.cfg = cfg
        self._since: float | None = None

    def update(self, s: Snapshot) -> bool:
        free = (s.brake < self.cfg.brake_below and abs(s.steer_angle) < self.cfg.steer_below) or (
            s.speed_kmh < 5.0
        )
        if not free:
            self._since = None
            return False
        if self._since is None:
            self._since = s.t
        return s.t - self._since >= self.cfg.hold_s


@dataclass
class AlertRecord:
    """What the chat can say about an alert: when, which rule, the words and the numbers, what happened."""

    t: float
    rule: str
    text: str
    priority: str
    values: dict
    outcome: str = "fired"  # fired | said | dropped:<reason>


@dataclass
class EngineerStats:
    fired: int = 0
    spoken: int = 0
    interrupted: int = 0
    dropped_policy: int = 0
    dropped_expired: int = 0
    dropped_muted: int = 0
    dropped_quiet: int = 0


class Engineer:
    def __init__(
        self,
        cfg: EngineerConfig,
        speaker: Speaker,
        *,
        rules: list[Rule] | None = None,
        analysis: AnalysisConfig | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.cfg = cfg
        self.speaker = speaker
        self.rules = rules if rules is not None else build_rules(cfg, analysis)
        self.calm = CalmDetector(cfg.calm)
        self.pending: dict[str, Alert] = {}
        self.recent: deque[AlertRecord] = deque(maxlen=20)
        self._records: dict[str, AlertRecord] = {}
        self.stats = EngineerStats()
        self._clock = clock
        self._last_said_t = -1e9
        self._session_started_t: float | None = None
        self.quiet = False  # the driver asked not to be disturbed: only critical alerts go out

    def set_quiet(self, on: bool) -> None:
        """ "Do not bother me": drop what waits and everything below CRITICAL until it is switched off."""
        self.quiet = on
        if on:
            self.stats.dropped_quiet += len(self.pending)
            self.pending.clear()
        log.info("engineer quiet mode", extra={"quiet": on})

    # -- policy -----------------------------------------------------------------------------------
    def _policy(self, session: str) -> PolicyConfig:
        p = self.cfg.policies
        return getattr(p, session) if session in POLICY_KEYS else p.other

    def _allowed(self, alert: Alert, session: str) -> bool:
        pol = self._policy(session)
        if alert.rule in pol.allow_rules:
            return True
        return alert.priority >= Priority.parse(pol.min_priority)

    # -- the per-sample entry point -----------------------------------------------------------------
    def on_sample(
        self, snap: Snapshot, state: SessionState, tracker: LapTracker, events: list[Event]
    ) -> list[Alert]:
        """Evaluate every rule on this sample and deliver what is due. Returns the alerts fired."""
        if any(isinstance(e, SessionChanged) for e in events):
            self._session_started_t = snap.t
            self.pending.clear()
            for r in self.rules:  # a new session is a clean slate
                r._last.clear()
        calm = self.calm.update(snap)
        muted = state.phase in MUTE_PHASES or (
            self._session_started_t is not None
            and snap.t - self._session_started_t < self.cfg.startup_quiet_s
        )
        fired: list[Alert] = []
        ctx = RuleContext(snap, state, tracker, events, self.cfg.language)
        for rule in self.rules:
            try:
                fired += rule.evaluate(ctx)
            except Exception:  # noqa: BLE001 - one broken rule must not silence the others
                log.exception("rule failed", extra={"rule": rule.id})
        for a in fired:
            self.stats.fired += 1
            rec = AlertRecord(snap.t, a.rule, a.text, a.priority.name, dict(a.values))
            self.recent.append(rec)
            self._records[a.key] = rec
            log.info(
                "alert fired",
                extra={
                    "rule": a.rule,
                    "key": a.key,
                    "priority": a.priority.name,
                    "text": a.text,
                    "session": state.session,
                    "phase": state.phase,
                    "calm": calm,
                    **a.values,
                },
            )
            if muted:
                self.stats.dropped_muted += 1
                self._drop(a, "muted")
            elif self.quiet and a.priority < Priority.CRITICAL:
                self.stats.dropped_quiet += 1
                self._drop(a, "quiet")
            elif not self._allowed(a, state.session):
                self.stats.dropped_policy += 1
                self._drop(a, f"policy:{state.session}")
            elif a.interrupts:
                self._say(a, snap, interrupt=True)
            else:
                self.pending[a.key] = a
        self._deliver(snap, calm, muted)
        return fired

    # -- delivery ---------------------------------------------------------------------------------
    def _deliver(self, snap: Snapshot, calm: bool, muted: bool) -> None:
        for key, a in list(self.pending.items()):
            if muted or snap.t - a.t > a.ttl_s:
                self.stats.dropped_expired += 1
                self._drop(a, "muted" if muted else "expired")
                del self.pending[key]
        if not self.pending or not calm:
            return
        if self.speaker.busy or snap.t - self._last_said_t < self.cfg.calm.min_gap_s:
            return
        best = max(self.pending.values(), key=lambda a: (a.priority, -a.t))
        del self.pending[best.key]
        self._say(best, snap, interrupt=False)

    def _say(self, a: Alert, snap: Snapshot, *, interrupt: bool) -> None:
        self._last_said_t = snap.t
        self.stats.spoken += 1
        if a.key in self._records:
            self._records[a.key].outcome = "said"
        if interrupt:
            self.stats.interrupted += 1
        self.speaker.say(a.text, interrupt=interrupt, meta={"rule": a.rule, "key": a.key})
        log.info(
            "alert said",
            extra={
                "rule": a.rule,
                "key": a.key,
                "priority": a.priority.name,
                "text": a.text,
                "interrupt": interrupt,
                "waited_s": round(snap.t - a.t, 2),
            },
        )

    def _drop(self, a: Alert, reason: str) -> None:
        if a.key in self._records:
            self._records[a.key].outcome = f"dropped:{reason}"
        log.info(
            "alert dropped",
            extra={
                "rule": a.rule,
                "key": a.key,
                "priority": a.priority.name,
                "reason": reason,
                "text": a.text,
            },
        )
