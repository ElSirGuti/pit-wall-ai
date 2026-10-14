"""Glue for LLM calls: adaptive pacing + short cache + graceful degradation.

``ask`` never raises and never waits for the limiter: it returns ``None`` when the call is skipped or
fails, and the rule-based alerts (which do not depend on any of this) keep working.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace

from ..config import LLMConfig
from .llm import LLMError, LLMProvider, LLMRateLimited, LLMRequest, LLMResponse
from .ratelimit import AdaptiveRateLimiter

log = logging.getLogger(__name__)
_CACHE_MAX = 64


class LLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        cfg: LLMConfig,
        *,
        limiter: AdaptiveRateLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.cfg = cfg
        self.limiter = limiter or AdaptiveRateLimiter(cfg.rate_limit, clock=clock)
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, LLMResponse]] = OrderedDict()
        self._lock = threading.Lock()
        self.last_skip_reason = ""

    def _cached(self, key: str) -> LLMResponse | None:
        if self.cfg.cache_ttl_s <= 0:
            return None
        with self._lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] <= self.cfg.cache_ttl_s:
                self._cache.move_to_end(key)
                return replace(hit[1], cached=True)
            self._cache.pop(key, None)
        return None

    def _store(self, key: str, response: LLMResponse) -> None:
        if self.cfg.cache_ttl_s <= 0:
            return
        with self._lock:
            self._cache[key] = (self._clock(), response)
            while len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)

    def ask(self, request: LLMRequest, *, urgent: bool = False) -> LLMResponse | None:
        """The answer, or ``None`` if the request was skipped (rate limit, budget) or the call failed."""
        if request.max_output_tokens is None:
            request = replace(request, max_output_tokens=self.cfg.max_output_tokens)
        key = request.cache_key()
        hit = self._cached(key)
        if hit is not None:
            return hit
        if not self.limiter.allow(urgent=urgent):
            self.last_skip_reason = "rate_limited"
            log.info("LLM call skipped by the limiter", extra=self.limiter.snapshot())
            return None
        t0 = self._clock()
        try:
            response = self.provider.generate(request)
        except LLMRateLimited as exc:
            self.limiter.on_rate_limited(exc.retry_after_s)
            self.last_skip_reason = "provider_429"
            log.warning("LLM provider rate limited us", extra=self.limiter.snapshot())
            return None
        except LLMError as exc:
            self.limiter.on_failure()
            self.last_skip_reason = f"error: {exc}"
            log.warning("LLM call failed", extra={"error": str(exc)})
            return None
        except Exception:  # noqa: BLE001 - a provider bug must not kill the engineer
            self.limiter.on_failure()
            self.last_skip_reason = "error: unexpected"
            log.exception("unexpected LLM provider failure")
            return None
        self.limiter.on_success()
        self.last_skip_reason = ""
        response = replace(response, latency_s=response.latency_s or (self._clock() - t0))
        self._store(key, response)
        return response
