"""Application configuration: YAML file + ``.env`` for secrets, validated with pydantic.

Resolution order: explicit path > ``PIT_WALL_CONFIG`` > ``config/local.yaml`` (if present) > built-in
defaults (the same ones ``config/default.yaml`` documents). Secrets (API keys) are never part of the
config: they are read from the environment at the moment of use, so they cannot end up in a log or a dump.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

SessionName = Literal["practice", "qualify", "race", "hotlap", "time_attack", "drift", "drag"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in the YAML is an error, not a silent no-op


class ReplayConfig(_Model):
    file: str | None = None
    speed: float = Field(1.0, gt=0, le=50)
    start_s: float = Field(0.0, ge=0)
    loop: bool = False
    session: SessionName = "practice"


class TelemetryConfig(_Model):
    source: Literal["ac", "replay"] = "ac"
    hz: float = Field(60.0, gt=0, le=240)
    buffer_seconds: float = Field(30.0, gt=0, le=3600)
    retry_s: float = Field(1.0, gt=0)
    replay: ReplayConfig = ReplayConfig()


class LoggingConfig(_Model):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_logs: bool = False
    file: str | None = None


class RateLimitConfig(_Model):
    """Adaptive pacing: starts at ``initial_rpm``, backs off on a 429, creeps up while calls succeed."""

    adaptive: bool = True
    initial_rpm: float = Field(6.0, gt=0)
    min_rpm: float = Field(1.0, gt=0)
    max_rpm: float = Field(10.0, gt=0)
    increase_every_ok: int = Field(10, ge=1)
    increase_step_rpm: float = Field(0.5, gt=0)
    decrease_factor: float = Field(0.5, gt=0, lt=1)
    cooldown_after_429_s: float = Field(15.0, ge=0)
    daily_budget: int | None = Field(
        None, ge=1
    )  # hard cap of requests per local day; None = no cap

    @model_validator(mode="after")
    def _ordered(self) -> RateLimitConfig:
        if not self.min_rpm <= self.initial_rpm <= self.max_rpm:
            raise ValueError("need min_rpm <= initial_rpm <= max_rpm")
        return self


class LLMConfig(_Model):
    provider: str = "mock"  # "gemini" arrives in phase 5; "mock" works offline
    model: str = "gemini-2.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_s: float = Field(8.0, gt=0)
    max_output_tokens: int = Field(300, ge=16)
    cache_ttl_s: float = Field(20.0, ge=0)
    rate_limit: RateLimitConfig = RateLimitConfig()

    def api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        """The key from the environment (never stored on the object)."""
        return (env if env is not None else os.environ).get(self.api_key_env) or None


class AppConfig(_Model):
    telemetry: TelemetryConfig = TelemetryConfig()
    logging: LoggingConfig = LoggingConfig()
    llm: LLMConfig = LLMConfig()


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = (
            _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        )
    return out


def load_config(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> AppConfig:
    """Load and validate the configuration. Loads ``.env`` into the process environment first."""
    environ = os.environ if env is None else env
    if env is None:
        load_dotenv(override=False)
    chosen = path or environ.get("PIT_WALL_CONFIG")
    if not chosen and Path("config/local.yaml").is_file():
        chosen = "config/local.yaml"
    data: dict = {}
    if chosen:
        p = Path(chosen)
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        try:
            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{p}: invalid YAML: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"{p}: the top level must be a mapping")
        data = _deep_merge(data, loaded)
    try:
        return AppConfig.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError
        raise ConfigError(str(exc)) from exc
