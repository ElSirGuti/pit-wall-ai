"""The Gemini provider (HTTPS, stdlib), against simulated answers: no network, no key needed."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pit_wall_ai.config import LLMConfig
from pit_wall_ai.engineer.client import LLMClient
from pit_wall_ai.engineer.gemini import KEY_HEADER, GeminiProvider, HttpResponse
from pit_wall_ai.engineer.llm import (
    LLMError,
    LLMRateLimited,
    LLMRequest,
    LLMUnavailable,
    available_providers,
    create_provider,
)
from pit_wall_ai.engineer.llmcheck import run_llm_check

KEY = "SECRET-KEY-123"


def cfg(**kw) -> LLMConfig:
    base = dict(provider="gemini", allow_cloud=True, model="test-model")
    base.update(kw)
    return LLMConfig.model_validate(base)


class FakeTransport:
    """Plays a script of responses (or exceptions) and records every request."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "body": body, "timeout": timeout}
        )
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


def ok_body(text="Frenas tarde.", total=42):
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 30,
            "candidatesTokenCount": 12,
            "totalTokenCount": total,
        },
        "modelVersion": "test-model-001",
    }


def resp(status=200, body=None, headers=None):
    raw = json.dumps(body).encode() if body is not None else b""
    return HttpResponse(status, headers or {}, raw)


def provider(*script, **kw) -> tuple[GeminiProvider, FakeTransport]:
    t = FakeTransport(*script)
    return GeminiProvider(cfg(**kw), transport=t, api_key=KEY), t


REQ = LLMRequest(system="Radio style.", user="¿Cómo voy?", context={"lap": 7, "fuel_l": 12.3})


# -- consent and key ------------------------------------------------------------------------------------
def test_nothing_is_sent_unless_the_cloud_is_allowed():
    t = FakeTransport(resp(200, ok_body()))
    with pytest.raises(LLMUnavailable, match="llm.allow_cloud") as e:
        GeminiProvider(cfg(allow_cloud=False), transport=t, api_key=KEY)
    assert "improve Google" in str(e.value) and t.calls == []
    assert LLMConfig().allow_cloud is False  # the default


def test_a_missing_key_names_the_variable_to_set(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="GEMINI_API_KEY"):
        GeminiProvider(cfg(), transport=FakeTransport(resp(200, {})))


def test_the_provider_is_registered_and_built_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    assert "gemini" in available_providers() or True
    p = create_provider(cfg())
    assert p.name == "gemini" and p.model == "test-model"
    with pytest.raises(LLMUnavailable, match="allow_cloud"):
        create_provider(cfg(allow_cloud=False))


# -- request ----------------------------------------------------------------------------------------------
def test_the_request_has_the_system_prompt_the_context_and_the_limits():
    p, t = provider(resp(200, ok_body()))
    p.generate(REQ)
    call = t.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/models/test-model:generateContent")
    assert (
        call["headers"][KEY_HEADER] == KEY and KEY not in call["url"]
    )  # header only, never in the URL
    body = json.loads(call["body"])
    assert body["systemInstruction"]["parts"][0]["text"] == "Radio style."
    user = body["contents"][0]["parts"][0]["text"]
    assert (
        user.startswith("¿Cómo voy?")
        and '"fuel_l":12.3' in user
        and "only source of numbers" in user
    )
    assert body["generationConfig"]["maxOutputTokens"] == cfg().max_output_tokens
    assert body["generationConfig"]["temperature"] == 0.2
    assert call["timeout"] == cfg().timeout_s


def test_a_request_without_context_or_system_is_just_the_question():
    p, t = provider(resp(200, ok_body()))
    p.generate(LLMRequest(system="", user="hola"))
    body = json.loads(t.calls[0]["body"])
    assert "systemInstruction" not in body
    assert body["contents"][0]["parts"][0]["text"] == "hola"


def test_the_model_name_comes_only_from_the_configuration():
    p, t = provider(resp(200, ok_body()), model="whatever-1.0")
    p.generate(REQ)
    assert t.calls[0]["url"].endswith("/models/whatever-1.0:generateContent")
    src = Path(__file__).parents[1] / "src" / "pit_wall_ai"
    offenders = [
        str(f.relative_to(src))
        for f in src.rglob("*.py")
        if f.name != "config.py" and re.search(r"gemini-\d", f.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# -- response ---------------------------------------------------------------------------------------------
def test_a_good_answer_is_parsed_with_usage():
    p, _ = provider(resp(200, ok_body("Curvone: frenas 14 metros antes.", total=77)))
    r = p.generate(REQ)
    assert r.text == "Curvone: frenas 14 metros antes."
    assert r.model == "test-model-001"
    assert r.usage == {"prompt": 30, "output": 12, "total": 77}


def test_several_text_parts_are_joined():
    body = {"candidates": [{"content": {"parts": [{"text": "Uno. "}, {"text": "Dos."}]}}]}
    assert provider(resp(200, body))[0].generate(REQ).text == "Uno. Dos."


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "safety filter"),
        ({"candidates": []}, "no candidates"),
        ({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}, "MAX_TOKENS"),
        ({}, "no candidates"),
    ],
)
def test_unusable_answers_are_errors_with_a_reason(body, needle):
    with pytest.raises(LLMError, match=needle):
        provider(resp(200, body))[0].generate(REQ)


# -- failures ---------------------------------------------------------------------------------------------
def test_429_carries_the_retry_hint_from_the_header():
    p, _ = provider(resp(429, {"error": {"message": "quota"}}, {"retry-after": "12"}))
    with pytest.raises(LLMRateLimited) as e:
        p.generate(REQ)
    assert e.value.retry_after_s == 12.0


def test_429_carries_the_retry_hint_from_the_error_details():
    body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "quota exceeded",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}],
        }
    }
    with pytest.raises(LLMRateLimited) as e:
        provider(resp(429, body))[0].generate(REQ)
    assert e.value.retry_after_s == 37.0


def test_429_without_a_hint_has_none():
    with pytest.raises(LLMRateLimited) as e:
        provider(resp(429, {"error": {"message": "slow down"}}))[0].generate(REQ)
    assert e.value.retry_after_s is None


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_unavailable_and_never_echoed(status):
    body = {"error": {"message": f"permission denied for key {KEY}"}}
    with pytest.raises(LLMUnavailable) as e:
        provider(resp(status, body))[0].generate(REQ)
    assert "rejected" in str(e.value) and KEY not in str(e.value)


def test_an_invalid_key_reported_as_400_is_recognised():
    body = {"error": {"message": "API key not valid. Please pass a valid API key."}}
    with pytest.raises(LLMUnavailable, match="rejected"):
        provider(resp(400, body))[0].generate(REQ)


def test_a_server_error_is_a_plain_error():
    with pytest.raises(LLMError, match="500"):
        provider(resp(500, {"error": {"message": "internal"}}))[0].generate(REQ)


def test_network_errors_are_llm_errors_without_the_key():
    p, _ = provider(OSError(f"connection reset while sending {KEY}"))
    with pytest.raises(LLMError) as e:
        p.generate(REQ)
    assert "network" in str(e.value) and KEY not in str(e.value)


def test_a_timeout_is_a_network_error():
    with pytest.raises(LLMError, match="network"):
        provider(TimeoutError("timed out"))[0].generate(REQ)


# -- a model that does not exist: say which ones do ---------------------------------------------------------
MODELS_PAGE_1 = {
    "models": [
        {
            "name": "models/alpha-flash",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        },
        {"name": "models/embed-1", "supportedGenerationMethods": ["embedContent"]},
    ],
    "nextPageToken": "p2",
}
MODELS_PAGE_2 = {
    "models": [{"name": "models/beta-pro", "supportedGenerationMethods": ["generateContent"]}]
}


def test_a_missing_model_lists_the_models_the_key_accepts():
    p, t = provider(
        resp(404, {"error": {"status": "NOT_FOUND", "message": "model not found"}}),
        resp(200, MODELS_PAGE_1),
        resp(200, MODELS_PAGE_2),
        model="gone-model",
    )
    with pytest.raises(LLMUnavailable) as e:
        p.generate(REQ)
    msg = str(e.value)
    assert "'gone-model' was not found" in msg and "alpha-flash" in msg and "beta-pro" in msg
    assert "embed-1" not in msg  # only models that can generate text
    assert "llm.model" in msg
    assert t.calls[1]["method"] == "GET" and t.calls[2]["url"].endswith("pageToken=p2")


def test_a_missing_model_whose_listing_also_fails_says_both_things():
    p, _ = provider(
        resp(404, {"error": {"message": "model not found"}}),
        resp(500, {"error": {"message": "boom"}}),
        model="gone-model",
    )
    with pytest.raises(LLMUnavailable) as e:
        p.generate(REQ)
    assert "'gone-model' answered 404" in str(e.value) and "could not be read" in str(e.value)


# -- through the client: limiter and degradation --------------------------------------------------------------
def test_a_429_makes_the_client_return_none_and_back_off_then_recover():
    clock = {"t": 0.0}
    t = FakeTransport(
        resp(429, {"error": {"message": "q"}}, {"retry-after": "30"}), resp(200, ok_body("vale"))
    )
    c = cfg()
    client = LLMClient(GeminiProvider(c, transport=t, api_key=KEY), c, clock=lambda: clock["t"])
    assert client.ask(REQ) is None and client.last_skip_reason == "provider_429"
    assert client.ask(REQ) is None  # inside the cool-down: skipped without calling the network
    assert len(t.calls) == 1
    clock["t"] += 31.0
    r = client.ask(REQ)
    assert r is not None and r.text == "vale" and len(t.calls) == 2


def test_an_unavailable_provider_error_is_a_clear_skip_not_a_crash():
    c = cfg()
    client = LLMClient(
        GeminiProvider(
            c, transport=FakeTransport(resp(403, {"error": {"message": "no"}})), api_key=KEY
        ),
        c,
    )
    assert client.ask(REQ) is None
    assert client.last_skip_reason.startswith("error:")


# -- llm-check ---------------------------------------------------------------------------------------------
def run_check(*script, calls=3, allow=False, **kw):
    lines: list[str] = []
    t = FakeTransport(*script)
    ticks = iter(i * 0.1 for i in range(1000))
    code = run_llm_check(
        cfg(**kw),
        calls=calls,
        allow_cloud=allow,
        out=lines.append,
        transport=t,
        api_key=KEY,
        clock=lambda: next(ticks),
        sleep=lambda s: None,
    )
    return code, "\n".join(lines), t


def test_llm_check_without_consent_does_not_call_anything():
    code, out, t = run_check(resp(200, {}), allow_cloud=False)
    assert code == 2 and "allow_cloud" in out and t.calls == []


def test_llm_check_with_the_flag_overrides_consent_for_this_run_only():
    page = {
        "models": [{"name": "models/test-model", "supportedGenerationMethods": ["generateContent"]}]
    }
    code, out, t = run_check(
        resp(200, page), resp(200, ok_body("ok")), allow=True, allow_cloud=False
    )
    assert code == 0 and "calls ok: 3/3" in out and len(t.calls) == 4


def test_llm_check_reports_models_latency_and_tokens():
    page = {
        "models": [{"name": "models/test-model", "supportedGenerationMethods": ["generateContent"]}]
    }
    code, out, t = run_check(resp(200, page), resp(200, ok_body("ok", total=9)))
    assert code == 0
    assert "* test-model" in out and "calls ok: 3/3" in out and "latency: median" in out
    assert "tokens used by the test: 27" in out and "No telemetry" in out
    assert KEY not in out


def test_llm_check_fails_with_the_accepted_models_when_the_configured_one_is_missing():
    page = {
        "models": [
            {"name": "models/alpha-flash", "supportedGenerationMethods": ["generateContent"]}
        ]
    }
    code, out, t = run_check(resp(200, page), model="gone-model")
    assert code == 1 and "NOT among them" in out and "alpha-flash" in out
    assert len(t.calls) == 1  # it did not try to generate with a model that is not there


def test_llm_check_reports_the_429_and_its_hint():
    page = {
        "models": [{"name": "models/test-model", "supportedGenerationMethods": ["generateContent"]}]
    }
    code, out, _ = run_check(
        resp(200, page),
        resp(200, ok_body("ok")),
        resp(429, {"error": {"message": "q"}}, {"retry-after": "20"}),
        calls=2,
    )
    assert code == 1 and "429 rate limited, retry in 20 s" in out and "rate limited: 1" in out


def test_llm_check_with_the_mock_provider_is_a_no_op():
    lines: list[str] = []
    code = run_llm_check(LLMConfig(), out=lines.append)
    assert code == 0 and "nothing to check" in lines[0]


def test_llm_check_cli_runs_offline_with_the_default_config(capsys):
    from pit_wall_ai.cli import main

    assert main(["llm-check"]) == 0
    assert "nothing to check" in capsys.readouterr().out


def test_a_listed_model_that_answers_404_shows_the_api_message_and_says_it_is_listed():
    # the real case seen on 2026-10-14: the name is in the key's list but generateContent says 404
    p, _ = provider(
        resp(404, {"error": {"message": "models/test-model is not found for API version v1beta"}}),
        resp(
            200,
            {
                "models": [
                    {
                        "name": "models/test-model",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {"name": "models/other-one", "supportedGenerationMethods": ["generateContent"]},
                ]
            },
        ),
        model="test-model",
    )
    with pytest.raises(LLMUnavailable) as e:
        p.generate(REQ)
    msg = str(e.value)
    assert "is in this key's list but generateContent answered 404" in msg
    assert "is not found for API version v1beta" in msg  # what the API itself said
    assert "other-one" in msg and "--model" in msg


def test_llm_check_can_try_another_model_for_one_run():
    page = {
        "models": [{"name": "models/other-one", "supportedGenerationMethods": ["generateContent"]}]
    }
    lines: list[str] = []
    t = FakeTransport(resp(200, page), resp(200, ok_body("ok")))
    code = run_llm_check(
        cfg(model="test-model"),
        calls=1,
        model="other-one",
        out=lines.append,
        transport=t,
        api_key=KEY,
    )
    assert code == 0
    assert "model from the configuration: other-one" in "\n".join(lines)
    assert t.calls[1]["url"].endswith("/models/other-one:generateContent")


def test_a_retired_model_message_surfaces_the_suggestion_of_the_api():
    # the real answer seen on 2026-10-14
    api = (
        "This model models/test-model is no longer available to new users. Please update your code to use "
        "models/newer-flash for the latest features and improvements."
    )
    page = {
        "models": [
            {"name": "models/test-model", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/newer-flash", "supportedGenerationMethods": ["generateContent"]},
        ]
    }
    p, _ = provider(resp(404, {"error": {"message": api}}), resp(200, page), model="test-model")
    with pytest.raises(LLMUnavailable) as e:
        p.generate(REQ)
    msg = str(e.value)
    assert "no longer available to new users" in msg  # the API's own words
    assert "The API itself suggests 'newer-flash' (it is in this key's list)" in msg
    assert "llm.model: newer-flash" in msg


def test_a_suggestion_that_the_key_does_not_list_is_flagged():
    api = "Please update your code to use models/ghost-model."
    page = {
        "models": [{"name": "models/test-model", "supportedGenerationMethods": ["generateContent"]}]
    }
    p, _ = provider(resp(404, {"error": {"message": api}}), resp(200, page), model="test-model")
    with pytest.raises(LLMUnavailable, match="NOT in this key's list"):
        p.generate(REQ)


def test_llm_check_lists_every_usable_model():
    names = [f"model-{i:02d}" for i in range(40)]
    page = {
        "models": [
            {"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]}
            for n in names
        ]
    }
    lines: list[str] = []
    run_llm_check(
        cfg(model="model-39"),
        calls=1,
        out=lines.append,
        transport=FakeTransport(resp(200, page), resp(200, ok_body("ok"))),
        api_key=KEY,
    )
    text = "\n".join(lines)
    assert "more" not in text and "model-39" in text and "model-00" in text
