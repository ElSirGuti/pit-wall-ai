"""Several API keys: a reserve is used when the first one is over its quota or rejected."""

from __future__ import annotations

import json

import pytest

from pit_wall_ai.config import LLMConfig
from pit_wall_ai.engineer.gemini import KEY_HEADER, GeminiProvider, HttpResponse
from pit_wall_ai.engineer.llm import LLMRateLimited, LLMRequest, LLMUnavailable


def ok(text="ok"):
    body = {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "modelVersion": "m",
    }
    return HttpResponse(200, {}, json.dumps(body).encode())


def err(status, message="x", extra=None):
    body = {"error": {"message": message, "details": extra or []}}
    return HttpResponse(status, {}, json.dumps(body).encode())


class Fake:
    """Answers by (key, model): a dict of lists of responses (the last one repeats)."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def request(self, method, url, headers, body, timeout):
        key = headers.get(KEY_HEADER)
        model = url.split("/models/")[-1].split(":")[0]
        self.calls.append((key, model))
        seq = self.script.get((key, model)) or self.script.get((key, None)) or [ok()]
        return seq.pop(0) if len(seq) > 1 else seq[0]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def provider(script, keys=("KEY-A", "KEY-B", "KEY-C"), fallbacks=("model-b",)):
    cfg = LLMConfig(
        allow_cloud=True, provider="gemini", model="model-a", fallback_models=list(fallbacks)
    )
    t, c = Fake(script), Clock()
    return GeminiProvider(cfg, transport=t, api_keys=list(keys), clock=c), t, c


REQ = LLMRequest(system="s", user="u")


def test_keys_are_read_from_the_environment_in_order_and_deduplicated():
    cfg = LLMConfig()
    env = {
        "GEMINI_API_KEY": "k1",
        "GEMINI_API_KEY_2": "k2",
        "GEMINI_API_KEY_4": "k4",
        "GEMINI_API_KEY_LIST": "k5, k2 ,k6",
    }
    assert cfg.api_keys(env) == ["k1", "k2", "k4", "k5", "k6"]
    assert cfg.api_key(env) == "k1" and cfg.api_keys({}) == [] and cfg.api_key({}) is None


def test_a_key_over_its_quota_passes_the_request_to_the_next_key_on_the_same_model():
    p, t, _ = provider({("KEY-A", "model-a"): [err(429, "quota")]})
    assert p.generate(REQ).text == "ok"
    assert t.calls == [("KEY-A", "model-a"), ("KEY-B", "model-a")]  # not the fallback model


def test_the_key_in_use_is_kept_while_it_works_and_the_exhausted_one_rests():
    p, t, c = provider({("KEY-A", "model-a"): [err(429, "quota", [{"retryDelay": "60s"}])]})
    p.generate(REQ)
    t.calls.clear()
    p.generate(REQ)
    p.generate(REQ)
    assert t.calls == [("KEY-B", "model-a")] * 2  # sticky on B, A is not hammered
    c.t += 61
    t.script[("KEY-B", "model-a")] = [err(429, "quota")]
    t.calls.clear()
    p.generate(REQ)  # B is out now; A has recovered
    assert t.calls[0] == ("KEY-B", "model-a") and t.calls[1] == ("KEY-C", "model-a")


def test_a_daily_quota_rests_for_a_long_time():
    daily = HttpResponse(
        429,
        {},
        json.dumps(
            {"error": {"message": "quota", "details": [{"quotaId": "RequestsPerDayPerProject"}]}}
        ).encode(),
    )
    p, t, c = provider({("KEY-A", "model-a"): [daily]}, keys=("KEY-A", "KEY-B"))
    p.generate(REQ)
    c.t += 600  # ten minutes later A is still resting
    t.calls.clear()
    p.generate(REQ)
    assert t.calls == [("KEY-B", "model-a")]
    c.t += 3600
    t.calls.clear()
    t.script[("KEY-B", "model-a")] = [err(429, "quota")]
    p.generate(REQ)
    assert t.calls[:2] == [("KEY-B", "model-a"), ("KEY-A", "model-a")]


def test_all_keys_over_quota_then_the_fallback_model_is_tried_and_then_it_is_rate_limited():
    script = {(k, "model-a"): [err(429, "q")] for k in ("KEY-A", "KEY-B")}
    p, t, _ = provider(script, keys=("KEY-A", "KEY-B"))
    assert p.generate(REQ).text == "ok"  # model-b answers on the first key
    assert t.calls == [("KEY-A", "model-a"), ("KEY-B", "model-a"), ("KEY-A", "model-b")]
    script = {(k, m): [err(429, "q")] for k in ("KEY-A", "KEY-B") for m in ("model-a", "model-b")}
    p, _, _ = provider(script, keys=("KEY-A", "KEY-B"))
    with pytest.raises(LLMRateLimited):
        p.generate(REQ)


def test_a_rejected_key_is_dropped_and_the_next_one_answers():
    p, t, _ = provider({("KEY-A", None): [err(403, "API key not valid. The API key was rejected")]})
    assert p.generate(REQ).text == "ok"
    assert p.keys_usable == 2
    t.calls.clear()
    p.generate(REQ)
    assert all(k != "KEY-A" for k, _ in t.calls)


def test_all_keys_rejected_is_unavailable_and_no_key_ever_appears_in_an_error():
    script = {
        (k, None): [err(403, "the API key was rejected: KEY-A KEY-B")] for k in ("KEY-A", "KEY-B")
    }
    p, _, _ = provider(script, keys=("KEY-A", "KEY-B"))
    with pytest.raises(LLMUnavailable) as e:
        p.generate(REQ)
    assert "KEY-A" not in str(e.value) and "KEY-B" not in str(e.value)


def test_llm_check_reports_each_key_without_printing_it(monkeypatch):
    from pit_wall_ai.engineer.llmcheck import run_llm_check

    monkeypatch.setenv("GEMINI_API_KEY", "KEY-A")
    monkeypatch.setenv("GEMINI_API_KEY_2", "KEY-B")
    cfg = LLMConfig(allow_cloud=True, provider="gemini", model="model-a", fallback_models=[])
    t = Fake({("KEY-B", None): [err(403, "rejected")]})
    lines: list[str] = []
    run_llm_check(cfg, calls=1, out=lines.append, transport=t, clock=Clock(), sleep=lambda s: None)
    text = " ".join(lines)
    assert "API keys: 2" in text and "key 1: accepted" in text and "key 2: answered 403" in text
    assert "KEY-A" not in text and "KEY-B" not in text
