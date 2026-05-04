"""Asset registry — mangas-style manifest with character variant chaining.

`books/<slug>/manifest.json` is the single source of truth for every reference
image and rendered page image. Character variants chain off a default pose, so
re-rendering the default invalidates dependents (`stale=True`).

On-disk layout (per book):
    books/<slug>/
      assets/
        <character-name>/
          default.png          ← canonical reference (front view)
          default.png.json     ← image sidecar
          variants/
            side.png
            side.png.json
            angry.png
        <other-character>/
          default.png
      images/
        page-001.png
        page-001.png.json
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from kdp_book.log import log
from kdp_book.models.assets import (
    IAssetEntry,
    IAssetManifest,
    ICharacterSheet,
    IImageAsset,
)
from kdp_book.tools.atomic_io import atomic_write_text


def character_slug(name: str) -> str:
    """Filesystem-safe slug for a character name."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "unnamed"


def character_dir(book_dir: Path, name: str) -> Path:
    return book_dir / "assets" / character_slug(name)


def character_image_path(
    book_dir: Path, name: str, variant: str = "default",
) -> Path:
    """Where the rendered PNG lives for `(name, variant)`."""
    base = character_dir(book_dir, name)
    if variant == "default":
        return base / "default.png"
    return base / "variants" / f"{character_slug(variant)}.png"


def _manifest_path(book_dir: Path) -> Path:
    return book_dir / "manifest.json"


def load_manifest(book_dir: Path) -> IAssetManifest:
    path = _manifest_path(book_dir)
    if not path.exists():
        return IAssetManifest(slug=book_dir.name)
    return IAssetManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_manifest(book_dir: Path, manifest: IAssetManifest) -> None:
    manifest.updated_at = datetime.now(UTC)
    atomic_write_text(_manifest_path(book_dir), manifest.model_dump_json(indent=2))


def add_character_variant(
    book_dir: Path,
    *,
    name: str,
    variant: str,
    entry: IAssetEntry,
) -> None:
    manifest = load_manifest(book_dir)
    sheet = manifest.characters.setdefault(name, ICharacterSheet())
    sheet.variants[variant] = entry
    save_manifest(book_dir, manifest)
    log.info("Registered character: %s/%s → %s", name, variant, entry.path)


def add_location_variant(
    book_dir: Path,
    *,
    name: str,
    variant: str,
    entry: IAssetEntry,
) -> None:
    manifest = load_manifest(book_dir)
    locs = manifest.locations.setdefault(name, {})
    locs[variant] = entry
    save_manifest(book_dir, manifest)
    log.info("Registered location: %s/%s → %s", name, variant, entry.path)


def add_page_image(book_dir: Path, image: IImageAsset) -> None:
    manifest = load_manifest(book_dir)
    manifest.pages = [p for p in manifest.pages if p.page_index != image.page_index]
    manifest.pages.append(image)
    manifest.pages.sort(key=lambda p: p.page_index)
    save_manifest(book_dir, manifest)


def get_character_ref(book_dir: Path, name: str, variant: str = "default") -> Path | None:
    manifest = load_manifest(book_dir)
    sheet = manifest.characters.get(name)
    if not sheet:
        return None
    entry = sheet.variants.get(variant)
    if not entry:
        return None
    p = Path(entry.path)
    if not p.is_absolute():
        p = book_dir / entry.path
    return p if p.exists() else None


def get_location_ref(book_dir: Path, name: str, variant: str = "default") -> Path | None:
    manifest = load_manifest(book_dir)
    loc = manifest.locations.get(name)
    if not loc:
        return None
    entry = loc.get(variant)
    if not entry:
        return None
    p = Path(entry.path)
    if not p.is_absolute():
        p = book_dir / entry.path
    return p if p.exists() else None


def invalidate_dependents(
    book_dir: Path,
    *,
    name: str,
    variant: str,
) -> list[str]:
    """Mark every character variant chained off `(name, variant)` as stale."""
    manifest = load_manifest(book_dir)
    sheet = manifest.characters.get(name)
    if not sheet:
        return []
    invalidated: list[str] = []
    for v_name, entry in sheet.variants.items():
        if v_name == variant:
            continue
        if entry.chain_from == variant:
            entry.stale = True
            invalidated.append(f"{name}/{v_name}")
    if invalidated:
        save_manifest(book_dir, manifest)
        log.info(
            "Invalidated %d dependents of %s/%s: %s",
            len(invalidated), name, variant, ", ".join(invalidated),
        )
    return invalidated
