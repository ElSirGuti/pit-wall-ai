"""The LLM behind an interface: swap the provider in the YAML without touching the engineer.

Providers register a factory by name. ``mock`` ships now (offline, deterministic, for development and
tests); ``gemini`` is registered in phase 5. Requests carry a compact structured ``context`` (never raw
telemetry); a provider turns it into whatever its API needs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..config import LLMConfig


@dataclass(frozen=True, slots=True)
class LLMRequest:
    system: str
    user: str
    context: dict[str, Any] = field(default_factory=dict)
    max_output_tokens: int | None = None

    def cache_key(self) -> str:
        return json.dumps([self.system, self.user, self.context], sort_keys=True, default=str)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    model: str
    latency_s: float = 0.0
    cached: bool = False
    usage: dict[str, int] | None = None


class LLMError(RuntimeError):
    """Any failure of a provider call."""


class LLMUnavailable(LLMError):
    """No key, no network, unknown provider: the call cannot be made at all."""


class LLMRateLimited(LLMError):
    """The API refused because of its quota (HTTP 429 / RESOURCE_EXHAUSTED)."""

    def __init__(self, message: str = "rate limited", retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Blocking call (it runs in the engineer's own thread). Raise :class:`LLMError` on failure."""


ProviderFactory = Callable[[LLMConfig], LLMProvider]
_REGISTRY: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _REGISTRY[name.lower()] = factory


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def create_provider(cfg: LLMConfig) -> LLMProvider:
    try:
        return _REGISTRY[cfg.provider.lower()](cfg)
    except KeyError:
        raise LLMUnavailable(
            f"unknown LLM provider {cfg.provider!r}; available: {', '.join(available_providers())}"
        ) from None


class MockProvider:
    """Offline provider: answers with a short radio-style line built from the context."""

    name = "mock"

    def __init__(self, cfg: LLMConfig) -> None:
        self.model = "mock"

    def generate(self, request: LLMRequest) -> LLMResponse:
        lap = request.context.get("lap")
        tail = f" Lap {lap}." if lap is not None else ""
        return LLMResponse(text=f"Copy. {request.user.strip()[:80]}{tail}", model=self.model)


register_provider("mock", MockProvider)
