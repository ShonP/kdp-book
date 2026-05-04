"""Application settings loaded from `.env` and environment variables.

Mirrors deep-research / newsroom / linkedin-advisor: a single `Settings`
class with `pydantic-settings`, accessed everywhere via `get_settings()`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration loaded from `.env` and environment variables."""

    azure_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""

    copilot_model: str = "gpt-5.5"
    openai_validation_model: str = "gpt-5.5"

    azure_image_endpoint: str = ""
    azure_image_edit_endpoint: str = ""
    image_size: str = "1024x1024"
    image_quality: str = "low"
    image_generation_workers: int = 5
    chapter_writer_workers: int = 6

    azure_storage_connection_string: str = ""
    azure_storage_container: str = "kdp-books"

    kdp_books_dir: Path = Path("books")
    kdp_author_name: str = "Shon Pazarker"
    min_review_score: int = 7
    max_safety_retries: int = 5

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_api_key() -> str:
    """Resolve the OpenAI/Azure API key (prefers OPENAI_API_KEY, falls back to AZURE_API_KEY)."""
    settings = get_settings()
    return settings.openai_api_key or settings.azure_api_key
