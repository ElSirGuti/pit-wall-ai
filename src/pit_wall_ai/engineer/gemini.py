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
DAILY_QUOTA_COOLDOWN_S = (
    3600.0  # a key that hit its DAILY quota rests this long (then it is tried again)
)


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
        api_keys: list[str] | None = None,
        clock=time.monotonic,
    ) -> None:
        if not cfg.allow_cloud:
            raise LLMUnavailable(
                "the cloud model is disabled (llm.allow_cloud: false): only the local answers work. "
                "Set llm.allow_cloud: true in config/local.yaml to let the compact session summary and your "
                "question be sent to Google (the free tier may use them to improve Google's products)."
            )
        keys = list(api_keys) if api_keys else ([api_key] if api_key else cfg.api_keys())
        if not keys:
            raise LLMUnavailable(
                f"no API key: put {cfg.api_key_env}=... in .env (a free key from Google AI Studio)"
            )
        self.cfg = cfg
        self.model = cfg.model
        self._keys = keys
        self._active = 0  # the key in use: it is kept until it fails (sticky), then the next one
        self._dead: set[int] = set()  # rejected by the API (401/403): not tried again in this run
        self._transport = transport or UrllibTransport()
        self._clock = clock
        self._cooldown_until: dict[tuple[int, str], float] = {}  # (key index, model)
        self._no_thinking: set[str] = set()  # models that answered 400 to a thinking budget
        self._cooldown_rate: set[tuple[int, str]] = (
            set()
        )  # resting because of a 429 (not an overload)

    # -- plumbing -----------------------------------------------------------------------------------
    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def keys_usable(self) -> int:
        return len(self._keys) - len(self._dead)

    def _headers(self, key_index: int | None = None) -> dict[str, str]:
        k = self._keys[self._active if key_index is None else key_index]
        return {KEY_HEADER: k, "Content-Type": "application/json"}

    def _send(
        self, method: str, path: str, body: dict | None = None, key_index: int | None = None
    ) -> HttpResponse:
        url = f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            return self._transport.request(
                method, url, self._headers(key_index), data, self.cfg.timeout_s
            )
        except OSError as exc:
            raise LLMTransient(
                f"network error talking to the model: {self._redact(str(exc))}"
            ) from exc

    def _redact(self, text: str) -> str:
        for k in self._keys:
            if k:
                text = text.replace(k, "***")
        return text

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
            wait = _retry_after(resp)
            if b"PerDay" in resp.body or b"per day" in resp.body.lower():
                wait = max(
                    wait or 0.0, DAILY_QUOTA_COOLDOWN_S
                )  # a daily quota will not come back in a minute
            raise LLMRateLimited(f"rate limited: {msg}", wait)
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

        With several API keys, a key that is over its quota passes the request to the next key **for the same
        model** before a fallback model is tried (a reserve key has the same quality). The key in use is kept
        until it fails, so a long race spends one key at a time.

        A (key, model) pair that just failed is left alone for a while (its ``Retry-After`` on a 429, a few
        seconds on an overload): failed requests count against the daily quota too, so hammering is
        expensive. When every pair is cooling down no request is made at all and the shortest wait is reported.
        """
        models = list(dict.fromkeys([self.cfg.model, *self.cfg.fallback_models]))
        n = len(self._keys)
        order = [(self._active + j) % n for j in range(n)]
        order = [k for k in order if k not in self._dead]
        if not order:
            raise LLMUnavailable("every API key was rejected by the service")
        now = self._clock()
        pairs = [(m, k) for m in models for k in order]
        ready = [p for p in pairs if now >= self._cooldown_until.get((p[1], p[0]), 0.0)]
        if not ready:
            soonest = min(pairs, key=lambda p: self._cooldown_until[(p[1], p[0])])
            wait = max(self._cooldown_until[(soonest[1], soonest[0])] - now, 0.0)
            msg = "every configured model and key is resting after a recent failure"
            if (
                soonest[1],
                soonest[0],
            ) in self._cooldown_rate:  # a real 429: the limiter must back off too
                raise LLMRateLimited(msg, wait)
            raise LLMTransient(f"{msg}; try again in {wait:.0f} s")
        last: LLMError | None = None
        for i, (model, k) in enumerate(ready):
            if k in self._dead:
                continue
            slot = (k, model)
            try:
                resp = self._generate_with(model, request, k)
            except LLMRateLimited as exc:
                self._cooldown_until[slot] = self._clock() + (exc.retry_after_s or 30.0)
                self._cooldown_rate.add(slot)
                last = exc
                self._log_next(model, k, ready, i, exc)
                continue
            except LLMTransient as exc:
                self._cooldown_until[slot] = self._clock() + TRANSIENT_COOLDOWN_S
                self._cooldown_rate.discard(slot)
                last = exc
                self._log_next(model, k, ready, i, exc)
                continue
            except LLMUnavailable as exc:
                if n == 1 or "rejected" not in str(exc):
                    raise
                self._dead.add(k)  # this key is refused: another one may still work
                last = exc
                log.warning("an API key was rejected; trying the next one", extra={"key": k + 1})
                continue
            if k != self._active:
                log.info("switched to API key", extra={"key": k + 1, "of": n})
                self._active = k
            if i:
                log.info(
                    "answered by a reserve",
                    extra={"model": model, "primary": models[0], "key": k + 1},
                )
            return resp
        assert last is not None
        raise last

    @staticmethod
    def _log_next(model: str, key: int, ready: list, i: int, exc: LLMError) -> None:
        log.warning(
            "model call failed, trying the next one" if i + 1 < len(ready) else "model call failed",
            extra={"model": model, "key": key + 1, "error": str(exc)},
        )

    def _generate_with(
        self, model: str, request: LLMRequest, key_index: int | None = None
    ) -> LLMResponse:
        t0 = self._clock()
        body = self.build_body(request, model)
        resp = self._send("POST", f"models/{model}:generateContent", body, key_index)
        if resp.status == 400 and "thinkingConfig" in body["generationConfig"]:
            # this model does not take a thinking budget: remember it and ask again without
            self._no_thinking.add(model)
            log.warning(
                "the model rejected the thinking budget; retrying without it",
                extra={"model": model, "api_message": self._redact(_error_message(resp))},
            )
            resp = self._send(
                "POST",
                f"models/{model}:generateContent",
                self.build_body(request, model),
                key_index,
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
