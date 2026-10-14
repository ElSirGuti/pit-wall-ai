"""``pit-wall llm-check``: what does this key, this model and this connection really do?

It sends one tiny prompt several times ("reply with ok": no telemetry, nothing personal), lists the models
the key can use and reports latency, token usage and any 429 with its retry hint. The output is what the
adaptive limiter and the documentation should be calibrated with, instead of trusting a blog.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

from ..config import LLMConfig
from .gemini import GeminiProvider, HttpTransport
from .llm import LLMError, LLMRateLimited, LLMRequest, LLMUnavailable

PING_SYSTEM = "You are a connectivity test. Answer with exactly one word."
PING_USER = "Reply with the single word: ok"


def run_llm_check(
    cfg: LLMConfig,
    *,
    calls: int = 3,
    allow_cloud: bool = False,
    model: str | None = None,
    thinking_budget: int | None = None,
    out: Callable[[str], None] = print,
    transport: HttpTransport | None = None,
    api_key: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """0 = everything worked, 1 = the model or some calls failed, 2 = cannot run (no consent / key)."""
    if cfg.provider.lower() != "gemini":
        out(
            f"llm.provider is {cfg.provider!r}: it works offline and there is nothing to check. "
            "Set llm.provider: gemini (and llm.allow_cloud: true) to test the real model."
        )
        return 0
    if allow_cloud:
        cfg = cfg.model_copy(update={"allow_cloud": True})
    if model:  # a one-off try without editing the YAML
        cfg = cfg.model_copy(update={"model": model})
    if thinking_budget is not None:
        cfg = cfg.model_copy(update={"thinking_budget": thinking_budget})
    try:
        provider = GeminiProvider(cfg, transport=transport, api_key=api_key, clock=clock)
    except LLMUnavailable as exc:
        out(f"cannot run the check: {exc}")
        return 2

    out(f"model from the configuration: {cfg.model}")
    if cfg.thinking_budget is not None:
        out(f"thinking budget: {cfg.thinking_budget} tokens")
    if cfg.fallback_models:
        out("fallback models: " + ", ".join(cfg.fallback_models))
    out(f"endpoint: {cfg.base_url}")
    out(f"what is sent: the one-line prompt {PING_USER!r}. No telemetry, no personal data.")

    try:
        models = provider.list_models()
    except LLMUnavailable as exc:
        out(f"cannot list the models: {exc}")
        return 2
    except LLMError as exc:
        out(f"could not list the models ({exc}); going on with the generation test")
        models = []
    usable = sorted(m.name for m in models if m.can_generate)
    if models:
        out(f"models this key can use for generateContent: {len(usable)}")
        for name in usable:
            out(f"  {'*' if name == cfg.model else ' '} {name}")
        if cfg.model not in usable:
            out(
                f"the configured model {cfg.model!r} is NOT among them. "
                "Set llm.model in config/local.yaml to one of the names above."
            )
            return 1

    latencies: list[float] = []
    failures = 0
    limited: list[float | None] = []
    # the configured output budget, not a tiny one: thinking tokens count in it (16 gave MAX_TOKENS)
    request = LLMRequest(system=PING_SYSTEM, user=PING_USER)
    tokens = 0
    answered_by: set[str] = set()
    for i in range(1, calls + 1):
        t0 = clock()
        try:
            resp = provider.generate(request)
        except LLMRateLimited as exc:
            failures += 1
            limited.append(exc.retry_after_s)
            hint = f", retry in {exc.retry_after_s:g} s" if exc.retry_after_s else ", no retry hint"
            out(f"call {i}: 429 rate limited{hint}")
            continue
        except LLMUnavailable as exc:
            out(f"call {i}: {exc}")
            return 1
        except LLMError as exc:
            failures += 1
            out(f"call {i}: failed: {exc}")
            continue
        dt = resp.latency_s or (clock() - t0)
        latencies.append(dt)
        used = (resp.usage or {}).get("total", 0)
        answered_by.add(resp.model)
        tokens += used
        usage = resp.usage or {}
        extra = f", thinking {usage['thoughts']}" if usage.get("thoughts") else ""
        out(f"call {i}: {dt * 1000:.0f} ms, reply {resp.text[:30]!r}, tokens {used}{extra}")
        if i < calls:
            sleep(0.0)

    out("")
    out(
        f"calls ok: {len(latencies)}/{calls}, rate limited: {len(limited)}, other failures: "
        f"{failures - len(limited)}"
    )
    if latencies:
        out(
            f"latency: median {statistics.median(latencies) * 1000:.0f} ms, "
            f"max {max(latencies) * 1000:.0f} ms (the chat target is under 3 s)"
        )
    if answered_by:
        out("answered by: " + ", ".join(sorted(answered_by)))
    if tokens:
        out(f"tokens used by the test: {tokens}")
    out(
        "Free-tier quotas are shown per project in Google AI Studio and change over time: compare them "
        "with this result before raising llm.rate_limit.max_rpm."
    )
    return 0 if not failures else 1
