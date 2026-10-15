"""Test isolation: the developer's own configuration and secrets must never leak into the tests.

``config/local.yaml`` and ``.env`` are git-ignored files that a developer fills with a real provider and a
real key; a test that loads the default configuration would silently talk to the network with them.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_local_config(monkeypatch, tmp_path):
    empty = tmp_path / "no-local-config.yaml"
    empty.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("PIT_WALL_CONFIG", str(empty))  # instead of config/local.yaml
    monkeypatch.setattr("pit_wall_ai.config.load_dotenv", lambda *a, **k: False)  # no .env
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _isolate_alert_memory(monkeypatch, tmp_path):
    """A test must never write to (or read from) the author's real alert memory."""
    monkeypatch.setenv("PIT_WALL_AI_HOME", str(tmp_path / "pit-wall-ai-home"))
