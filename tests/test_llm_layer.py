"""Adaptive rate limiter, LLM client (cache, degradation) and the provider registry."""

from __future__ import annotations

import threading
from datetime import date, timedelta

import pytest

from pit_wall_ai.config import LLMConfig, RateLimitConfig
from pit_wall_ai.engineer.client import LLMClient
from pit_wall_ai.engineer.llm import (
    LLMError,
    LLMRateLimited,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    available_providers,
    create_provider,
    register_provider,
)
from pit_wall_ai.engineer.ratelimit import AdaptiveRateLimiter


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def limiter(clock, **kw):
    cfg = RateLimitConfig(
        **{
            "initial_rpm": 6,
            "min_rpm": 1,
            "max_rpm": 10,
            "increase_every_ok": 3,
            "increase_step_rpm": 1.0,
            "decrease_factor": 0.5,
            "cooldown_after_429_s": 20,
            **kw,
        }
    )
    return AdaptiveRateLimiter(cfg, clock=clock)


def test_paces_requests_at_the_current_rate():
    clock = Clock()
    lim = limiter(clock)  # 6 rpm -> one request every 10 s
    assert lim.allow()
    assert not lim.allow()  # too soon
    assert lim.retry_in() == pytest.approx(10.0)
    clock.t += 9.9
    assert not lim.allow()
    clock.t += 0.2
    assert lim.allow()
    assert lim.denied == 2


def test_urgent_skips_the_interval_but_not_the_cooldown_or_budget():
    clock = Clock()
    lim = limiter(clock, daily_budget=3)
    assert lim.allow()
    assert lim.allow(urgent=True)  # the driver spoke: do not make him wait the interval
    lim.on_rate_limited()
    assert not lim.allow(urgent=True)  # the API told us to stop
    clock.t += 21
    assert lim.allow(urgent=True)  # third and last of the day
    assert not lim.allow(urgent=True) and lim.remaining_today == 0


def test_backs_off_on_429_honours_retry_after_and_never_below_min():
    clock = Clock()
    lim = limiter(clock)
    lim.on_rate_limited(retry_after_s=45)
    assert lim.rpm == 3.0 and lim.retry_in() == pytest.approx(
        45.0
    )  # Retry-After beats the cooldown
    for _ in range(5):
        lim.on_rate_limited()
    assert lim.rpm == 1.0
    clock.t += 1000
    assert lim.allow()


def test_creeps_up_while_calls_succeed_and_a_failure_restarts_the_streak():
    clock = Clock()
    lim = limiter(clock)
    lim.on_success()
    lim.on_success()
    lim.on_failure()  # network blip: no rate change, streak restarts
    assert lim.rpm == 6.0
    for _ in range(3):
        lim.on_success()
    assert lim.rpm == 7.0
    for _ in range(30):
        lim.on_success()
    assert lim.rpm == 10.0  # capped at max_rpm


def test_non_adaptive_mode_keeps_the_rate_but_still_honours_429():
    clock = Clock()
    lim = limiter(clock, adaptive=False)
    for _ in range(10):
        lim.on_success()
    lim.on_rate_limited()
    assert lim.rpm == 6.0 and lim.retry_in() == pytest.approx(20.0)


def test_daily_budget_resets_on_a_new_day():
    clock = Clock()
    today = {"d": date(2026, 10, 12)}
    lim = AdaptiveRateLimiter(
        RateLimitConfig(daily_budget=2, initial_rpm=10, max_rpm=10),
        clock=clock,
        today=lambda: today["d"],
    )
    assert lim.allow()
    clock.t += 10
    assert lim.allow()
    clock.t += 10
    assert not lim.allow() and lim.remaining_today == 0
    today["d"] += timedelta(days=1)
    assert lim.allow() and lim.remaining_today == 1
    assert set(lim.snapshot()) == {"rpm", "retry_in_s", "used_today", "denied"}


def test_limiter_is_thread_safe():
    clock = Clock()
    lim = limiter(clock, initial_rpm=10, max_rpm=10)
    granted = []

    def worker():
        granted.append(lim.allow(urgent=True))

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(granted) == 50 and lim.snapshot()["used_today"] == 50


# ── client ───────────────────────────────────────────────────────────────────────────────────────────
class Provider:
    name = "scripted"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.seen = []

    def generate(self, request):
        self.calls += 1
        self.seen.append(request)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return LLMResponse(text=out, model="m")


def client_for(provider, clock, **rl):
    cfg = LLMConfig(cache_ttl_s=30, rate_limit=RateLimitConfig(initial_rpm=60, max_rpm=60, **rl))
    return LLMClient(
        provider, cfg, limiter=AdaptiveRateLimiter(cfg.rate_limit, clock=clock), clock=clock
    )


REQ = LLMRequest(system="engineer", user="how are my tyres?", context={"lap": 4})


def test_client_caches_identical_requests_within_the_ttl():
    clock = Clock()
    prov = Provider(["tyres fine", "second"])
    c = client_for(prov, clock)
    a = c.ask(REQ)
    b = c.ask(REQ)
    assert a.text == b.text == "tyres fine" and b.cached and prov.calls == 1
    clock.t += 31
    assert c.ask(REQ).text == "second" and prov.calls == 2  # expired


def test_client_fills_in_the_default_token_limit_and_measures_latency():
    clock = Clock()
    prov = Provider(["ok"])
    c = client_for(prov, clock)
    r = c.ask(REQ)
    assert prov.seen[0].max_output_tokens == 300 and r.latency_s >= 0.0


def test_client_degrades_to_none_and_adapts_on_429():
    clock = Clock()
    prov = Provider([LLMRateLimited(retry_after_s=30), "later"])
    c = client_for(prov, clock)
    assert c.ask(REQ) is None and c.last_skip_reason == "provider_429"
    other = LLMRequest(system="engineer", user="different question")
    assert c.ask(other) is None and prov.calls == 1  # blocked by the limiter: no call made
    assert c.last_skip_reason == "rate_limited"
    assert c.limiter.rpm == 30.0
    clock.t += 31
    assert c.ask(other).text == "later"


def test_client_survives_provider_errors_and_bugs():
    clock = Clock()
    prov = Provider([LLMError("network down"), RuntimeError("bug"), "recovered"])
    c = client_for(prov, clock)
    assert c.ask(LLMRequest(system="s", user="1")) is None and "network down" in c.last_skip_reason
    clock.t += 2  # 60 rpm = one call per second
    assert c.ask(LLMRequest(system="s", user="2")) is None
    assert c.last_skip_reason == "error: unexpected"
    clock.t += 2
    assert c.ask(LLMRequest(system="s", user="3")).text == "recovered"
    assert c.limiter.rpm == 60.0  # failures do not change the rate


# ── registry ─────────────────────────────────────────────────────────────────────────────────────────
def test_registry_ships_the_offline_mock_and_rejects_unknown_providers():
    assert "mock" in available_providers()
    p = create_provider(LLMConfig(provider="mock"))
    out = p.generate(LLMRequest(system="s", user="Box this lap", context={"lap": 7}))
    assert out.text.startswith("Copy.") and "Lap 7" in out.text and out.model == "mock"
    with pytest.raises(LLMUnavailable) as e:
        create_provider(LLMConfig(provider="nonexistent"))
    assert "mock" in str(e.value)


def test_custom_providers_can_be_registered_by_name():
    class Echo:
        name = "echo"

        def __init__(self, cfg):
            pass

        def generate(self, request):
            return LLMResponse(text=request.user, model="echo")

    register_provider("Echo", Echo)
    assert create_provider(LLMConfig(provider="echo")).generate(REQ).text == "how are my tyres?"
