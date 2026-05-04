"""MAF middleware for LLM call logging, token tracking, and tool retries."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from agent_framework import (
    ChatContext,
    FunctionInvocationContext,
    chat_middleware,
    function_middleware,
)
from pydantic import BaseModel

from kdp_book.log import log


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion

    def reset(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


_token_usage = TokenUsage()


def get_token_usage() -> TokenUsage:
    return _token_usage


def reset_token_usage() -> None:
    _token_usage.reset()


@chat_middleware
async def llm_call_logging(context: ChatContext, next_handler) -> None:
    msg_count = len(context.messages) if context.messages else 0
    log.info("LLM call: %d messages", msg_count)
    start = time.monotonic()
    try:
        await next_handler()
        elapsed = (time.monotonic() - start) * 1000
        result = context.result
        text = getattr(result, "text", None) or ""
        usage = getattr(result, "usage_details", None)
        if usage:
            p_tok = usage.get("input_token_count", 0) or 0
            c_tok = usage.get("output_token_count", 0) or 0
            _token_usage.add(p_tok, c_tok)
            log.info(
                "LLM ok in %.0fms, %d chars, tokens %d+%d",
                elapsed,
                len(text),
                p_tok,
                c_tok,
            )
        else:
            log.info("LLM ok in %.0fms, %d chars", elapsed, len(text))
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        log.error("LLM call FAILED after %.0fms: %s", elapsed, e)
        raise


@function_middleware
async def tool_call_logging(context: FunctionInvocationContext, next_handler) -> None:
    log.info("Tool: %s(%s)", context.function.name, str(context.arguments)[:200])
    start = time.monotonic()
    try:
        await next_handler()
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        log.error("Tool %s FAILED after %.0fms: %s", context.function.name, elapsed, e)
        raise
    elapsed = (time.monotonic() - start) * 1000
    raw = context.result
    if hasattr(raw, "text"):
        result_str = raw.text or ""
    elif isinstance(raw, str):
        result_str = raw
    else:
        result_str = str(raw) if raw is not None else ""
    log.info("Tool %s → %d chars in %.0fms", context.function.name, len(result_str), elapsed)


@function_middleware
async def retry(context: FunctionInvocationContext, next_handler) -> None:
    for attempt in range(3):
        try:
            await next_handler()
            return
        except Exception as e:
            if attempt == 2:
                raise
            log.warning("Tool %s failed (attempt %d/3): %s", context.function.name, attempt + 1, e)
            await asyncio.sleep(2**attempt)


ALL_MIDDLEWARE = [llm_call_logging]
