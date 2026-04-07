"""LiteLLM wrapper with retry, cost tracking, and streaming support."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

from dataforge.config import get_settings, litellm_model
from dataforge.utils import get_logger

log = get_logger("llm")
litellm.set_verbose = False


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


@dataclass
class UsageSummary:
    total_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    errors: int = 0

    def add(self, r: LLMResponse) -> None:
        self.total_calls += 1
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.cost_usd += r.cost_usd


class LLMClient:
    def __init__(self) -> None:
        s = get_settings()
        self._model  = litellm_model(s.llm_provider, s.llm_model)
        self._temp   = s.llm_temperature
        self._max_tk = s.llm_max_tokens
        self.usage   = UsageSummary()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        try:
            resp = await litellm.acompletion(
                model=self._model,
                messages=messages,
                temperature=temperature or self._temp,
                max_tokens=max_tokens or self._max_tk,
            )
            usage = resp.usage or {}
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            try:
                cost = litellm.completion_cost(completion_response=resp)
            except Exception:
                cost = 0.0

            result = LLMResponse(
                content=resp.choices[0].message.content or "",
                model=self._model,
                prompt_tokens=pt,
                completion_tokens=ct,
                cost_usd=cost,
            )
            self.usage.add(result)
            log.debug(f"LLM call: {pt}pt + {ct}ct = ${cost:.5f}")
            return result
        except Exception as exc:
            self.usage.errors += 1
            msg = str(exc).lower()
            # Surface actionable errors without a raw traceback
            if "auth" in msg or "api key" in msg or "401" in msg or "403" in msg:
                from dataforge.utils.errors import MissingCredentialError
                _KEY_MAP = {
                    "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                    "groq": "GROQ_API_KEY", "together": "TOGETHER_API_KEY",
                }
                s = get_settings()
                key = _KEY_MAP.get(s.llm_provider, "API key")
                raise MissingCredentialError(key, s.llm_provider) from exc
            if "connect" in msg or "timeout" in msg or "unreachable" in msg:
                from dataforge.utils.errors import LLMConnectionError
                raise LLMConnectionError(str(exc)) from exc
            log.error(f"LLM error: {exc}")
            raise

    async def test_connection(self) -> bool:
        try:
            await self.complete([{"role": "user", "content": "Reply with: ok"}],
                                max_tokens=5)
            return True
        except Exception:
            return False
