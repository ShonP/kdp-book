"""OpenAI chat completion client factory.

Single source of truth for how kdp-book talks to gpt-5.5 (Azure-routed
through the OpenAI SDK, same shape as deep-research).
"""

from __future__ import annotations

from agent_framework_openai import OpenAIChatCompletionClient

from kdp_book.config import get_api_key, get_settings


def get_chat_client() -> OpenAIChatCompletionClient:
    """Create an `OpenAIChatCompletionClient` configured from environment."""
    settings = get_settings()
    return OpenAIChatCompletionClient(
        model=settings.copilot_model,
        api_key=get_api_key(),
        base_url=settings.openai_base_url,
    )


def get_vision_client() -> OpenAIChatCompletionClient:
    """Vision-capable client (used by ConsistencyAgent / QualityAgent)."""
    settings = get_settings()
    return OpenAIChatCompletionClient(
        model=settings.openai_validation_model,
        api_key=get_api_key(),
        base_url=settings.openai_base_url,
    )
