"""Provider registry (config/providers.py) — backs the "Any model: OpenAI,
Anthropic, Groq, Together, or fully local via Ollama" claim, plus Google,
which the registry supports but nothing previously pinned (issue #11 / #12).

No network calls — this only asserts registry shape and pure functions.
"""
from __future__ import annotations

import pytest

from dataforge.config.providers import (
    PROVIDER_INFO,
    PROVIDERS,
    THINKING_MODELS,
    litellm_model,
    model_supports_thinking,
)

EXPECTED_PROVIDERS = {"openai", "anthropic", "google", "groq", "together", "ollama", "openai_compatible"}

# Model IDs for a local OpenAI-compatible server depend on what it has loaded,
# so it deliberately lists none (the picker offers a custom ID).
NO_FIXED_MODELS = {"openai_compatible"}


def test_provider_info_covers_every_provider():
    assert set(PROVIDER_INFO.keys()) == EXPECTED_PROVIDERS


def test_every_provider_has_at_least_one_model():
    for name, info in PROVIDER_INFO.items():
        assert info.models or name in NO_FIXED_MODELS, f"{name} has no models configured"
        assert info.models == PROVIDERS[name], f"{name} PROVIDER_INFO.models drifted from PROVIDERS"


def test_key_requirement_is_consistent_with_key_env():
    for name, info in PROVIDER_INFO.items():
        if info.requires_key:
            assert info.key_env, f"{name} requires a key but has no key_env name"
        else:
            assert info.key_env == "", f"{name} does not require a key but declares one"


def test_only_local_providers_do_not_require_a_key():
    no_key = {name for name, info in PROVIDER_INFO.items() if not info.requires_key}
    assert no_key == {"ollama", "openai_compatible"}


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-sonnet-4-6"),
        ("groq", "groq/llama-3.3-70b-versatile"),
        ("together", "together_ai/meta-llama/Llama-3-70b-chat-hf"),
        ("google", "gemini/gemini-2.0-flash"),
        ("ollama", "ollama/llama3.2"),
    ],
)
def test_litellm_model_returns_every_configured_model_unchanged(provider, model):
    """Every model string in PROVIDERS is already litellm-ready (provider
    prefix baked in where litellm needs one) — litellm_model() must be a
    no-op passthrough for all of them, not just openai/anthropic."""
    assert litellm_model(provider, model) == model


def test_litellm_model_passes_through_unknown_provider():
    assert litellm_model("some-custom-provider", "some-model") == "some-model"


def test_thinking_models_are_a_small_explicit_allowlist():
    """Extended-thinking support is opt-in per exact model string, not
    inferred from the provider — a new Claude/DeepSeek model must be added
    here explicitly before it gets thinking-mode behavior."""
    assert THINKING_MODELS == {
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "ollama/deepseek-r1",
    }


@pytest.mark.parametrize("model", sorted(THINKING_MODELS))
def test_model_supports_thinking_true_for_allowlisted_models(model):
    assert model_supports_thinking("anthropic", model) is True


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "gpt-4o"),
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-haiku-4-5-20251001"),
        ("groq", "groq/llama-3.3-70b-versatile"),
        ("google", "gemini/gemini-2.0-flash"),
        ("ollama", "ollama/llama3.2"),
        ("ollama", "ollama/mistral"),
    ],
)
def test_model_supports_thinking_false_for_every_other_configured_model(provider, model):
    assert model_supports_thinking(provider, model) is False


def test_every_provider_model_appears_in_provider_info_and_vice_versa():
    """Catches PROVIDERS/PROVIDER_INFO drifting apart (e.g. a model added to
    one but not the other) — both must describe the same registry."""
    for name in EXPECTED_PROVIDERS:
        assert PROVIDERS[name] == PROVIDER_INFO[name].models
