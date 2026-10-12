"""Adaptive request pacing for a rate-limited LLM API (free tiers change their quotas over time).

Instead of trusting a fixed number, the limiter learns: it starts at ``initial_rpm``, cuts the rate
(multiplicative decrease) and honours ``Retry-After`` when the API answers 429, and raises it slowly
(additive increase) while calls keep succeeding, never beyond ``max_rpm``. An optional daily budget is a
hard stop. It never sleeps or blocks: callers ask ``allow()`` and degrade gracefully when told "not now".
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import date

from ..config import RateLimitConfig


class AdaptiveRateLimiter:
    def __init__(
        self,
        cfg: RateLimitConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.cfg = cfg or RateLimitConfig()
        self._clock = clock
        self._today = today
        self._lock = threading.Lock()
        self._rpm = self.cfg.initial_rpm
        self._next_ok = 0.0  # earliest clock time of the next normal request
        self._blocked_until = 0.0  # set by a 429 (Retry-After / cooldown)
        self._ok_streak = 0
        self._day = today()
        self._used_today = 0
        self.denied = 0

    # -- state ------------------------------------------------------------------------------------
    @property
    def rpm(self) -> float:
        with self._lock:
            return self._rpm

    @property
    def interval_s(self) -> float:
        return 60.0 / self.rpm

    def _roll_day(self) -> None:
        d = self._today()
        if d != self._day:
            self._day, self._used_today = d, 0

    @property
    def remaining_today(self) -> int | None:
        with self._lock:
            self._roll_day()
            if self.cfg.daily_budget is None:
                return None
            return max(0, self.cfg.daily_budget - self._used_today)

    def retry_in(self) -> float:
        """Seconds until a normal request would be allowed (0 if now)."""
        with self._lock:
            return max(0.0, max(self._next_ok, self._blocked_until) - self._clock())

    # -- the decision -----------------------------------------------------------------------------
    def allow(self, *, urgent: bool = False) -> bool:
        """Reserve a slot. ``urgent`` (the driver just spoke) skips the pacing interval, but never the
        cooldown after a 429 nor the daily budget."""
        with self._lock:
            self._roll_day()
            now = self._clock()
            ok = now >= self._blocked_until
            if (
                ok
                and self.cfg.daily_budget is not None
                and self._used_today >= self.cfg.daily_budget
            ):
                ok = False
            if ok and not urgent and now < self._next_ok:
                ok = False
            if not ok:
                self.denied += 1
                return False
            self._used_today += 1
            self._next_ok = max(self._next_ok, now) + 60.0 / self._rpm
            return True

    # -- feedback ---------------------------------------------------------------------------------
    def on_success(self) -> None:
        with self._lock:
            if not self.cfg.adaptive:
                return
            self._ok_streak += 1
            if self._ok_streak >= self.cfg.increase_every_ok:
                self._ok_streak = 0
                self._rpm = min(self.cfg.max_rpm, self._rpm + self.cfg.increase_step_rpm)

    def on_rate_limited(self, retry_after_s: float | None = None) -> None:
        """The API said 429: back off and wait at least ``retry_after_s`` (or the configured cooldown)."""
        with self._lock:
            self._ok_streak = 0
            if self.cfg.adaptive:
                self._rpm = max(self.cfg.min_rpm, self._rpm * self.cfg.decrease_factor)
            wait = max(retry_after_s or 0.0, self.cfg.cooldown_after_429_s)
            self._blocked_until = max(self._blocked_until, self._clock() + wait)
            self._next_ok = max(self._next_ok, self._blocked_until)

    def on_failure(self) -> None:
        """A non-429 failure (network, 5xx): do not change the rate, but restart the success streak."""
        with self._lock:
            self._ok_streak = 0

    def snapshot(self) -> dict:
        with self._lock:
            self._roll_day()
            now = self._clock()
            return {
                "rpm": round(self._rpm, 2),
                "retry_in_s": round(max(0.0, max(self._next_ok, self._blocked_until) - now), 2),
                "used_today": self._used_today,
                "denied": self.denied,
            }
