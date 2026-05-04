"""Application settings loaded from `.env` and environment variables.

Mirrors the deep-research / newsroom / linkedin-advisor pattern: a single
`Settings` class with `pydantic-settings`, accessed everywhere via
`get_settings()`.

Implementation lands in Phase 0 of PLAN.md.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration loaded from `.env` file and environment variables."""

    # ── Azure OpenAI text + vision ────────────────────────────────────────────
    azure_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    copilot_model: str = "gpt-5.5"
    openai_validation_model: str = "gpt-5.5"

    # ── Azure gpt-image-2 ─────────────────────────────────────────────────────
    azure_image_endpoint: str = ""
    azure_image_edit_endpoint: str = ""
    image_size: str = "2048x2048"
    image_quality: str = "high"
    image_generation_workers: int = 4

    # ── Azure Blob Storage (optional, publish target) ─────────────────────────
    azure_storage_connection_string: str = ""
    azure_storage_container: str = "kdp-books"

    # ── Run defaults ──────────────────────────────────────────────────────────
    kdp_books_dir: Path = Path("books")
    kdp_author_name: str = "Anonymous"
    min_review_score: int = 7
    max_safety_retries: int = 5

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached `Settings` instance (created on first call)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
