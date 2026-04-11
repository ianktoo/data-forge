"""LLM provider registry — maps friendly names to litellm model strings."""
from __future__ import annotations

from dataclasses import dataclass

PROVIDERS: dict[str, list[str]] = {
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ],
    "anthropic": [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "groq": [
        "groq/llama-3.3-70b-versatile",
        "groq/llama-3.1-8b-instant",
        "groq/mixtral-8x7b-32768",
    ],
    "together": [
        "together_ai/meta-llama/Llama-3-70b-chat-hf",
        "together_ai/mistralai/Mixtral-8x7B-Instruct-v0.1",
    ],
    "ollama": [
        "ollama/llama3.2",
        "ollama/mistral",
        "ollama/phi4",
        "ollama/deepseek-r1",
    ],
}


@dataclass
class ProviderInfo:
    name: str
    models: list[str]
    requires_key: bool
    key_env: str


PROVIDER_INFO: dict[str, ProviderInfo] = {
    "openai":    ProviderInfo("OpenAI",    PROVIDERS["openai"],    True,  "OPENAI_API_KEY"),
    "anthropic": ProviderInfo("Anthropic", PROVIDERS["anthropic"], True,  "ANTHROPIC_API_KEY"),
    "groq":      ProviderInfo("Groq",      PROVIDERS["groq"],      True,  "GROQ_API_KEY"),
    "together":  ProviderInfo("Together",  PROVIDERS["together"],  True,  "TOGETHER_API_KEY"),
    "ollama":    ProviderInfo("Ollama",    PROVIDERS["ollama"],    False, ""),
}


def litellm_model(provider: str, model: str) -> str:
    """Return the litellm-compatible model string."""
    if provider == "openai":
        return model
    if provider == "anthropic":
        return model
    # groq, together already have prefix in model string
    return model


# Models that support extended thinking / reasoning tokens.
# Anthropic Claude 4.x and DeepSeek-R1 can stream their reasoning process.
THINKING_MODELS: set[str] = {
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "ollama/deepseek-r1",
}


def model_supports_thinking(provider: str, model: str) -> bool:
    """Return True if this model can emit thinking/reasoning tokens."""
    return model in THINKING_MODELS
