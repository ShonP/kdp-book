"""Asset registry models — port of mangas/manga_studio/models/assets.py.

Drives character + location reference images for `gpt-image-2`. Variant
chaining lets a base "default" pose lock identity, and edits derive other
variants from it. Re-rendering the chain root marks dependents stale.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class IAssetEntry(BaseModel):
    """One generated reference image — character pose, location plate, prop."""

    path: str
    generated_at: datetime
    prompt_hash: str = ""
    validated: bool = False
    variant: str = ""
    notes: str = ""
    chain_from: str = ""
    reference_images: list[str] = []
    stale: bool = False


class ICharacterSheet(BaseModel):
    """All variants of a single character (default + emoting/posed alts)."""

    variants: dict[str, IAssetEntry] = {}


class IImageAsset(BaseModel):
    """A rendered page image (not a reference sheet)."""

    page_index: int
    path: str
    prompt: str
    refs: list[str] = []
    generated_at: datetime
    review_score: int | None = None
    review_issues: list[str] = []


class IAssetManifest(BaseModel):
    """`books/<slug>/manifest.json` — single source of truth for every ref image."""

    slug: str
    characters: dict[str, ICharacterSheet] = {}
    locations: dict[str, dict[str, IAssetEntry]] = {}
    props: dict[str, IAssetEntry] = {}
    cover: dict[str, IAssetEntry] = {}
    pages: list[IImageAsset] = []
    updated_at: datetime | None = None


class IMissingAsset(BaseModel):
    asset_type: str
    name: str
    variant: str = ""
    referenced_in: str = ""


class IAssetAuditResult(BaseModel):
    missing_characters: list[IMissingAsset] = []
    missing_locations: list[IMissingAsset] = []
    missing_props: list[IMissingAsset] = []
    total_missing: int = 0
    blocking: bool = False
