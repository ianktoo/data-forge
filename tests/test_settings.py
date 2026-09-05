"""Adapter test for the Settings contract — env vars must actually take effect.

Regression test for a bug where two ``model_config = SettingsConfigDict(...)``
assignments on the same class silently dropped ``env_prefix="DATAFORGE_"``
(the second assignment replaces, not merges with, the first). This made
every documented DATAFORGE_* variable in .env.example a no-op.
"""
from __future__ import annotations

import os

import pytest

from dataforge.config.settings import Settings


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("DATAFORGE_") or key.endswith("_API_KEY"):
            monkeypatch.delenv(key, raising=False)
    yield monkeypatch


def test_dataforge_prefixed_env_vars_are_honored(clean_env):
    clean_env.setenv("DATAFORGE_LLM_MODEL", "gpt-4-turbo-test")
    clean_env.setenv("DATAFORGE_LLM_PROVIDER", "anthropic")
    clean_env.setenv("DATAFORGE_RATE_LIMIT", "9.5")

    s = Settings(_env_file=None)

    assert s.llm_model == "gpt-4-turbo-test"
    assert s.llm_provider == "anthropic"
    assert s.rate_limit == 9.5


def test_output_dir_env_var_is_honored(clean_env, tmp_path):
    target = tmp_path / "custom_out"
    clean_env.setenv("DATAFORGE_OUTPUT_DIR", str(target))

    s = Settings(_env_file=None)

    assert s.output_dir == target.expanduser().resolve()


def test_unprefixed_provider_key_aliases_still_work(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "sk-test-123")

    s = Settings(_env_file=None)

    assert s.openai_api_key == "sk-test-123"
