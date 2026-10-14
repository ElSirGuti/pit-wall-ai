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
    LLMUnavailable,
    register_provider,
)

log = logging.getLogger(__name__)
KEY_HEADER = "x-goog-api-key"


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

    # -- plumbing -----------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {KEY_HEADER: self._key, "Content-Type": "application/json"}

    def _send(self, method: str, path: str, body: dict | None = None) -> HttpResponse:
        url = f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            return self._transport.request(method, url, self._headers(), data, self.cfg.timeout_s)
        except OSError as exc:
            raise LLMError(f"network error talking to the model: {self._redact(str(exc))}") from exc

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
    def _raise_for_status(self, resp: HttpResponse, *, listing: bool = False) -> None:
        s = resp.status
        if 200 <= s < 300:
            return
        msg = self._redact(_error_message(resp))
        if s == 429:
            raise LLMRateLimited(f"rate limited: {msg}", _retry_after(resp))
        if s in (401, 403) or (s == 400 and "api key" in msg.lower()):
            raise LLMUnavailable(f"the API key was rejected ({s}): {msg}")
        if s == 404 and not listing:
            raise LLMUnavailable(self._model_missing_message(msg))
        raise LLMError(f"the model API answered {s}: {msg}")

    def _model_missing_message(self, api_message: str) -> str:
        """The API said 404 for generateContent. Always include what the API itself said, and tell apart
        "this name is not in the key's list" from "it is listed but generation answers 404"."""
        try:
            names = self.accepted_models()
        except LLMError as exc:  # the listing failed too: say so, do not hide the first problem
            return (
                f"model {self.cfg.model!r} answered 404 ({api_message}); the list of models "
                f"available to this key could not be read either: {exc}"
            )
        shown = ", ".join(names[:20]) + (" ..." if len(names) > 20 else "")
        if self.cfg.model in names:
            return (
                f"model {self.cfg.model!r} is in this key's list but generateContent answered 404: "
                f"{api_message}. It may be retired or not available for text generation. "
                f"Try another with llm.model (or --model): {shown}"
            )
        return (
            f"model {self.cfg.model!r} was not found ({api_message}). Models this key can use: "
            f"{shown or '(none)'}. Set llm.model in config/local.yaml to one of them."
        )

    # -- generation ---------------------------------------------------------------------------------
    def build_body(self, request: LLMRequest) -> dict[str, Any]:
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
        if request.system.strip():
            body["systemInstruction"] = {"parts": [{"text": request.system.strip()}]}
        return body

    def generate(self, request: LLMRequest) -> LLMResponse:
        t0 = self._clock()
        resp = self._send(
            "POST", f"models/{self.cfg.model}:generateContent", self.build_body(request)
        )
        self._raise_for_status(resp)
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
        text = "".join(str(p.get("text", "")) for p in parts).strip()
        if not text:
            reason = cand.get("finishReason", "unknown")
            raise LLMError(f"the model returned no text (finish reason: {reason})")
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            model=str(data.get("modelVersion") or self.cfg.model),
            latency_s=self._clock() - t0,
            usage={
                k: int(usage[v])
                for k, v in (
                    ("prompt", "promptTokenCount"),
                    ("output", "candidatesTokenCount"),
                    ("total", "totalTokenCount"),
                )
                if v in usage
            }
            or None,
        )


register_provider("gemini", GeminiProvider)
