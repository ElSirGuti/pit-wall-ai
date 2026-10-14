"""Google Gemini over plain HTTPS (standard library only), behind :class:`LLMProvider`.

* The model name comes **only** from the configuration (``llm.model``); nothing here knows one. If the API
  says the model does not exist, the error lists the models the key *can* use.
* Nothing leaves the machine unless ``llm.allow_cloud`` is true (default false).
* The API key is sent in a header and is never part of a URL, a log line or an exception text.
* The HTTP layer is a small interface (:class:`HttpTransport`) so the tests, and ``llm-check`` without a key,
  run against recorded fake answers.

The request and response shapes below follow Google's public REST reference for ``generateContent`` and
``models.list``; they have **not** yet been exercised against the real service (no key at the time of
writing), which is the purpose of ``pit-wall llm-check``.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import LLMConfig
from .llm import (
    LLMError,
    LLMRateLimited,
    LLMRequest,
    LLMResponse,
    LLMTransient,
    LLMUnavailable,
    register_provider,
)

log = logging.getLogger(__name__)
KEY_HEADER = "x-goog-api-key"
TRANSIENT_COOLDOWN_S = 5.0  # leave an overloaded model alone this long before trying it again


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8")) if self.body else {}
        except (ValueError, UnicodeDecodeError):
            return {}


class HttpTransport(Protocol):
    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        """Raise :class:`OSError` (or a subclass) on connection problems and timeouts."""


class UrllibTransport:
    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return HttpResponse(
                    resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
                )
        except urllib.error.HTTPError as exc:  # 4xx / 5xx still carry a body worth reading
            return HttpResponse(
                exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b""
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OSError(str(getattr(exc, "reason", exc))) from exc


@dataclass(frozen=True)
class ModelInfo:
    name: str  # without the "models/" prefix
    methods: tuple[str, ...] = ()

    @property
    def can_generate(self) -> bool:
        return "generateContent" in self.methods


_RETRY_DELAY = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*s\s*$")
# "... Please update your code to use models/<name> ..." (what the API says when a model is retired)
_SUGGESTED = re.compile(r"\buse\s+(?:models/)?([A-Za-z0-9][A-Za-z0-9._-]+)", re.IGNORECASE)


def _retry_after(resp: HttpResponse) -> float | None:
    """Seconds to wait, from the Retry-After header or the RetryInfo detail of the error body."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    for detail in (resp.json().get("error", {}) or {}).get("details", []) or []:
        m = _RETRY_DELAY.match(str(detail.get("retryDelay", "")))
        if m:
            return float(m.group(1))
    return None


def _error_message(resp: HttpResponse) -> str:
    err = resp.json().get("error", {}) or {}
    return str(err.get("message") or f"HTTP {resp.status}")[:300]


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        cfg: LLMConfig,
        *,
        transport: HttpTransport | None = None,
        api_key: str | None = None,
        clock=time.monotonic,
    ) -> None:
        if not cfg.allow_cloud:
            raise LLMUnavailable(
                "the cloud model is disabled (llm.allow_cloud: false): only the local answers work. "
                "Set llm.allow_cloud: true in config/local.yaml to let the compact session summary and your "
                "question be sent to Google (the free tier may use them to improve Google's products)."
            )
        key = api_key or cfg.api_key()
        if not key:
            raise LLMUnavailable(
                f"no API key: put {cfg.api_key_env}=... in .env (a free key from Google AI Studio)"
            )
        self.cfg = cfg
        self.model = cfg.model
        self._key = key
        self._transport = transport or UrllibTransport()
        self._clock = clock
        self._cooldown_until: dict[str, float] = {}
        self._no_thinking: set[str] = set()  # models that answered 400 to a thinking budget

    # -- plumbing -----------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {KEY_HEADER: self._key, "Content-Type": "application/json"}

    def _send(self, method: str, path: str, body: dict | None = None) -> HttpResponse:
        url = f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            return self._transport.request(method, url, self._headers(), data, self.cfg.timeout_s)
        except OSError as exc:
            raise LLMTransient(
                f"network error talking to the model: {self._redact(str(exc))}"
            ) from exc

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    # -- models -------------------------------------------------------------------------------------
    def list_models(self) -> list[ModelInfo]:
        """The models this key can see. Raises :class:`LLMUnavailable` if the key is rejected."""
        out: list[ModelInfo] = []
        token = ""
        for _ in range(10):  # a safety bound on pagination
            path = "models?pageSize=100" + (f"&pageToken={token}" if token else "")
            resp = self._send("GET", path)
            self._raise_for_status(resp, listing=True)
            data = resp.json()
            for m in data.get("models", []) or []:
                name = str(m.get("name", "")).removeprefix("models/")
                if name:
                    out.append(
                        ModelInfo(name, tuple(m.get("supportedGenerationMethods", []) or []))
                    )
            token = data.get("nextPageToken", "") or ""
            if not token:
                break
        return out

    def accepted_models(self) -> list[str]:
        return sorted(m.name for m in self.list_models() if m.can_generate)

    # -- errors -------------------------------------------------------------------------------------
    def _raise_for_status(
        self, resp: HttpResponse, *, listing: bool = False, model: str | None = None
    ) -> None:
        s = resp.status
        if 200 <= s < 300:
            return
        msg = self._redact(_error_message(resp))
        if s == 429:
            raise LLMRateLimited(f"rate limited: {msg}", _retry_after(resp))
        if s in (401, 403) or (s == 400 and "api key" in msg.lower()):
            raise LLMUnavailable(f"the API key was rejected ({s}): {msg}")
        if s == 404 and not listing:
            raise LLMUnavailable(self._model_missing_message(msg, model or self.cfg.model))
        if s in (503, 504):
            raise LLMTransient(f"the model is overloaded or unreachable ({s}): {msg}")
        raise LLMError(f"the model API answered {s}: {msg}")

    def _model_missing_message(self, api_message: str, model: str | None = None) -> str:
        """The API said 404 for generateContent. Always include what the API itself said, and tell apart
        "this name is not in the key's list" from "it is listed but generation answers 404"."""
        model = model or self.cfg.model
        try:
            names = self.accepted_models()
        except LLMError as exc:  # the listing failed too: say so, do not hide the first problem
            return (
                f"model {model!r} answered 404 ({api_message}); the list of models "
                f"available to this key could not be read either: {exc}"
            )
        shown = ", ".join(names[:20]) + (" ..." if len(names) > 20 else "")
        m = _SUGGESTED.search(api_message)
        suggestion = m.group(1) if m else ""
        hint = ""
        if suggestion:
            where = (
                "it is in this key's list"
                if suggestion in names
                else "it is NOT in this key's list"
            )
            hint = (
                f" The API itself suggests {suggestion!r} ({where}): try llm.model: {suggestion}."
            )
        if model in names:
            return (
                f"model {model!r} is in this key's list but generateContent answered 404: "
                f"{api_message}. It may be retired or not available for text generation.{hint} Set llm.model (or use --model) to another. "
                f"Models: {shown}"
            )
        return (
            f"model {model!r} was not found ({api_message}). Models this key can use: "
            f"{shown or '(none)'}. Set llm.model in config/local.yaml to one of them."
        )

    # -- generation ---------------------------------------------------------------------------------
    def thinking_budget_for(self, model: str) -> int | None:
        if model in self._no_thinking:
            return None
        return self.cfg.thinking_budgets.get(model, self.cfg.thinking_budget)

    def build_body(self, request: LLMRequest, model: str | None = None) -> dict[str, Any]:
        model = model or self.cfg.model
        user = request.user.strip()
        if request.context:
            ctx = json.dumps(
                request.context, ensure_ascii=False, separators=(",", ":"), default=str
            )
            user = f"{user}\n\nCONTEXT (json, the only source of numbers):\n{ctx}"
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": self.cfg.temperature,
                "maxOutputTokens": request.max_output_tokens or self.cfg.max_output_tokens,
            },
        }
        budget = self.thinking_budget_for(model)
        if budget is not None:  # some models accept it, some answer 400 (see docs/LLM.md)
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": budget}
        if request.system.strip():
            body["systemInstruction"] = {"parts": [{"text": request.system.strip()}]}
        return body

    def generate(self, request: LLMRequest) -> LLMResponse:
        """The configured model first; if it is overloaded, times out or is over its quota, the
        ``fallback_models`` in order. Quotas are per model, so a reserve has its own allowance.

        A model that just failed is left alone for a while (its ``Retry-After`` on a 429, a few seconds on an
        overload): failed requests count against the daily quota too, so hammering is expensive. When every
        model is cooling down no request is made at all and the shortest wait is reported.
        """
        models = list(dict.fromkeys([self.cfg.model, *self.cfg.fallback_models]))
        now = self._clock()
        ready = [m for m in models if now >= self._cooldown_until.get(m, 0.0)]
        if not ready:
            wait = min(self._cooldown_until[m] for m in models) - now
            raise LLMRateLimited(
                "every configured model is cooling down after a recent failure", max(wait, 0.0)
            )
        last: LLMError | None = None
        for i, model in enumerate(ready):
            try:
                resp = self._generate_with(model, request)
            except LLMRateLimited as exc:
                self._cooldown_until[model] = self._clock() + (exc.retry_after_s or 30.0)
                last = exc
                self._log_next(model, ready, i, exc)
                continue
            except LLMTransient as exc:
                self._cooldown_until[model] = self._clock() + TRANSIENT_COOLDOWN_S
                last = exc
                self._log_next(model, ready, i, exc)
                continue
            if i:
                log.info(
                    "answered by a fallback model", extra={"model": model, "primary": models[0]}
                )
            return resp
        assert last is not None
        raise last

    @staticmethod
    def _log_next(model: str, ready: list[str], i: int, exc: LLMError) -> None:
        log.warning(
            "model call failed, trying the next one" if i + 1 < len(ready) else "model call failed",
            extra={"model": model, "error": str(exc)},
        )

    def _generate_with(self, model: str, request: LLMRequest) -> LLMResponse:
        t0 = self._clock()
        body = self.build_body(request, model)
        resp = self._send("POST", f"models/{model}:generateContent", body)
        if resp.status == 400 and "thinkingConfig" in body["generationConfig"]:
            # this model does not take a thinking budget: remember it and ask again without
            self._no_thinking.add(model)
            log.warning(
                "the model rejected the thinking budget; retrying without it",
                extra={"model": model, "api_message": self._redact(_error_message(resp))},
            )
            resp = self._send(
                "POST", f"models/{model}:generateContent", self.build_body(request, model)
            )
        self._raise_for_status(resp, model=model)
        data = resp.json()
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise LLMError(
                f"the request was blocked by the safety filter ({feedback['blockReason']})"
            )
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("the model returned no candidates")
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        # thinking models may return thought parts; only the visible answer is text without "thought": true
        text = "".join(str(p.get("text", "")) for p in parts if not p.get("thought")).strip()
        usage = data.get("usageMetadata") or {}
        if not text:
            reason = cand.get("finishReason", "unknown")
            if reason == "MAX_TOKENS":
                spent = int(usage.get("thoughtsTokenCount", 0) or 0)
                budget = request.max_output_tokens or self.cfg.max_output_tokens
                raise LLMError(
                    f"the model used its whole output budget ({budget} tokens"
                    + (f", {spent} of them thinking" if spent else "")
                    + ") before writing any text; thinking tokens count in that budget. Raise "
                    "llm.max_output_tokens or lower llm.thinking_budget"
                )
            raise LLMError(f"the model returned no text (finish reason: {reason})")
        return LLMResponse(
            text=text,
            model=str(data.get("modelVersion") or model),
            latency_s=self._clock() - t0,
            usage={
                k: int(usage[v])
                for k, v in (
                    ("prompt", "promptTokenCount"),
                    ("output", "candidatesTokenCount"),
                    ("thoughts", "thoughtsTokenCount"),
                    ("total", "totalTokenCount"),
                )
                if v in usage
            }
            or None,
        )


register_provider("gemini", GeminiProvider)
