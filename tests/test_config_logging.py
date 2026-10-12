"""Configuration loading and structured logging."""

from __future__ import annotations

import json
import logging

import pytest

from pit_wall_ai.config import AppConfig, ConfigError, LLMConfig, RateLimitConfig, load_config
from pit_wall_ai.logging_setup import JsonFormatter, KeyValueFormatter, setup_logging


def test_defaults_are_valid_and_safe():
    cfg = load_config(env={})
    assert cfg.telemetry.source == "ac" and cfg.telemetry.hz == 60
    assert cfg.llm.provider == "mock"  # nothing leaves the machine by default
    assert cfg.llm.rate_limit.adaptive is True


def test_yaml_overrides_defaults_deeply(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "telemetry:\n  source: replay\n  replay:\n    file: x.ld\n    speed: 4\nllm:\n  rate_limit:\n    initial_rpm: 4\n",
        encoding="utf-8",
    )
    cfg = load_config(p, env={})
    assert cfg.telemetry.source == "replay" and cfg.telemetry.replay.speed == 4
    assert cfg.telemetry.hz == 60  # untouched keys keep their defaults
    assert cfg.llm.rate_limit.initial_rpm == 4 and cfg.llm.rate_limit.max_rpm == 10


def test_env_var_selects_the_file(tmp_path):
    p = tmp_path / "env.yaml"
    p.write_text("telemetry:\n  hz: 30\n", encoding="utf-8")
    assert load_config(env={"PIT_WALL_CONFIG": str(p)}).telemetry.hz == 30


@pytest.mark.parametrize(
    "text",
    [
        "telemetry:\n  hz: 0\n",  # out of range
        "telemetry:\n  sourse: ac\n",  # typo: unknown key
        "llm:\n  rate_limit:\n    initial_rpm: 50\n",  # above max_rpm
        "- a\n- list\n",  # not a mapping
        "telemetry: [unclosed\n",  # invalid YAML
    ],
)
def test_invalid_config_is_a_clear_error(tmp_path, text):
    p = tmp_path / "bad.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p, env={})


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml", env={})


def test_api_key_comes_from_the_environment_and_is_not_in_the_config_dump():
    cfg = LLMConfig()
    assert cfg.api_key({}) is None
    assert cfg.api_key({"GEMINI_API_KEY": "sekret"}) == "sekret"
    assert "sekret" not in AppConfig().model_dump_json()
    assert RateLimitConfig().min_rpm <= RateLimitConfig().initial_rpm <= RateLimitConfig().max_rpm


def _record(msg="hello", **extra):
    r = logging.LogRecord("pit.test", logging.INFO, __file__, 1, msg, (), None)
    r.__dict__.update(extra)
    return r


def test_json_and_keyvalue_formatters_carry_extra_fields():
    js = json.loads(JsonFormatter().format(_record(source="ac", pages=3)))
    assert (
        js["msg"] == "hello" and js["level"] == "INFO" and js["source"] == "ac" and js["pages"] == 3
    )
    kv = KeyValueFormatter().format(_record(source="ac", pages=3))
    assert "hello" in kv and "source='ac'" in kv and "pages=3" in kv and "INFO" in kv


def test_setup_logging_is_idempotent_and_writes_json_to_a_file(tmp_path):
    f = tmp_path / "run.log"
    setup_logging("INFO", file=str(f), console=False)
    setup_logging("INFO", file=str(f), console=False)  # second call replaces, not stacks
    log = logging.getLogger("pit.idem")
    log.info("one line", extra={"k": 1})
    for h in logging.getLogger().handlers:
        h.flush()
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["k"] == 1
    setup_logging("WARNING", console=False)
    assert logging.getLogger().level == logging.WARNING
