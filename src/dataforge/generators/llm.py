"""LiteLLM wrapper with retry, cost tracking, and streaming support."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

from dataforge.config import get_settings, litellm_model, model_supports_thinking
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

    async def complete_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Stream a completion, routing thinking tokens and regular tokens to separate callbacks.

        For Anthropic Claude thinking models, extended thinking is enabled automatically.
        For DeepSeek-R1 (Ollama), <think>...</think> tags are parsed out of the stream.
        For all other models, tokens are passed to on_token without thinking support.
        """
        s = get_settings()
        supports_thinking = model_supports_thinking(s.llm_provider, s.llm_model)

        req_max_tokens = max_tokens or self._max_tk
        kwargs: dict[str, Any] = {
            "model":       self._model,
            "messages":    messages,
            "temperature": temperature or self._temp,
            "max_tokens":  req_max_tokens,
            "stream":      True,
        }

        if supports_thinking and s.llm_provider == "anthropic":
            budget = max(1024, min(8000, req_max_tokens - 1000))
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kwargs["max_tokens"] = max(req_max_tokens, budget + 2000)

        thinking_text = ""
        content_text = ""
        # For DeepSeek-R1 inline <think> tag tracking
        _in_think_tag = False

        try:
            stream = await litellm.acompletion(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # Anthropic extended thinking — LiteLLM exposes thinking as delta.thinking
                thinking_delta = getattr(delta, "thinking", None)
                if thinking_delta:
                    thinking_text += thinking_delta
                    if on_thinking:
                        on_thinking(thinking_delta)
                    continue

                raw = getattr(delta, "content", None) or ""
                if not isinstance(raw, str) or not raw:
                    continue

                if supports_thinking and s.llm_provider in ("ollama",):
                    # DeepSeek-R1 emits <think>…</think> inline in the content stream
                    buf = raw
                    while buf:
                        if _in_think_tag:
                            end = buf.find("</think>")
                            if end == -1:
                                thinking_text += buf
                                if on_thinking:
                                    on_thinking(buf)
                                buf = ""
                            else:
                                chunk_think = buf[:end]
                                thinking_text += chunk_think
                                if on_thinking:
                                    on_thinking(chunk_think)
                                _in_think_tag = False
                                buf = buf[end + len("</think>"):]
                        else:
                            start = buf.find("<think>")
                            if start == -1:
                                content_text += buf
                                if on_token:
                                    on_token(buf)
                                buf = ""
                            else:
                                pre = buf[:start]
                                if pre:
                                    content_text += pre
                                    if on_token:
                                        on_token(pre)
                                _in_think_tag = True
                                buf = buf[start + len("<think>"):]
                else:
                    content_text += raw
                    if on_token:
                        on_token(raw)

            # Estimate usage (streaming doesn't always return usage)
            pt, ct, cost = 0, 0, 0.0
            result = LLMResponse(
                content=content_text,
                model=self._model,
                prompt_tokens=pt,
                completion_tokens=ct,
                cost_usd=cost,
            )
            self.usage.add(result)
            return result

        except Exception as exc:
            self.usage.errors += 1
            msg = str(exc).lower()
            if "auth" in msg or "api key" in msg or "401" in msg or "403" in msg:
                from dataforge.utils.errors import MissingCredentialError
                _KEY_MAP = {
                    "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                    "groq": "GROQ_API_KEY", "together": "TOGETHER_API_KEY",
                }
                key = _KEY_MAP.get(s.llm_provider, "API key")
                raise MissingCredentialError(key, s.llm_provider) from exc
            if "connect" in msg or "timeout" in msg or "unreachable" in msg:
                from dataforge.utils.errors import LLMConnectionError
                raise LLMConnectionError(str(exc)) from exc
            log.error(f"LLM stream error: {exc}")
            raise

    async def test_connection(self) -> bool:
        try:
            await self.complete([{"role": "user", "content": "Reply with: ok"}],
                                max_tokens=5)
            return True
        except Exception:
            return False
