"""On-disk state IO — book directory layout, atomic JSON, slug resolution."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from kdp_book.config import get_settings
from kdp_book.log import log
from kdp_book.models.book import BookType, IBookState

_STATE_FILE = "book.json"

_SLUG_MAX = 60


def _slugify(text: str) -> str:
    """Filesystem-safe slug that preserves Unicode letters (Hebrew, etc.).

    Strips punctuation/whitespace runs to a single dash. Keeps every
    Unicode word character (letters, digits, underscore) plus dashes.
    Falls back to a stable hash of the original text when the result
    would otherwise be empty (e.g. punctuation-only or pure-emoji
    titles), so we never produce the meaningless slug `untitled`.
    """
    cleaned = re.sub(r"[^\w-]+", "-", (text or "").strip(), flags=re.UNICODE)
    cleaned = re.sub(r"-+", "-", cleaned)
    cleaned = cleaned.strip("-_").lower()[:_SLUG_MAX]
    if cleaned:
        return cleaned
    digest = hashlib.md5((text or "").encode("utf-8")).hexdigest()[:8]
    return f"book-{digest}"


def _split_dir_suffix(name: str, book_type: BookType) -> str | None:
    """Return the trailing `-<book_type>-<timestamp>` slice of a book-dir name.

    The original dir is `<topic-slug>-<book_type.value>-YYYYMMDD-HHMMSS`. We
    locate the trailing `-<book_type.value>-` separator with `rfind` and
    return everything from that point on, including the leading dash.
    Returns None if the suffix can't be located, which signals the caller
    to leave the directory alone.
    """
    needle = f"-{book_type.value}-"
    idx = name.rfind(needle)
    if idx < 0:
        return None
    return name[idx:]


def rename_book_dir_for_title(
    state: IBookState, title: str
) -> tuple[Path, Path] | None:
    """Rename `<book_dir>` to use the slugified `title` instead of the topic.

    Returns `(old_path, new_path)` on rename, `None` if the resulting slug
    matched the existing one or the directory layout was unrecognised.

    Mutates `state.book_dir` and `state.slug` to point at the new path
    and persists the updated state. Image paths inside `state` are stored
    relative to `book_dir`, so they continue to resolve after the rename.
    """
    title_slug = _slugify(title)
    book_type = state.config.book_type
    old_path = Path(state.book_dir)
    if not old_path.exists():
        log.warning("rename_book_dir: source missing: %s", old_path)
        return None

    suffix = _split_dir_suffix(old_path.name, book_type)
    if suffix is None:
        log.debug(
            "rename_book_dir: dir name %r does not match expected pattern, skipping",
            old_path.name,
        )
        return None

    new_name = f"{title_slug}{suffix}"
    if new_name == old_path.name:
        return None

    new_path = old_path.with_name(new_name)
    if new_path.exists():
        new_path = old_path.with_name(f"{title_slug}-2{suffix}")
    if new_path.exists():
        log.warning(
            "rename_book_dir: target %s already exists, skipping rename", new_path,
        )
        return None

    old_path.rename(new_path)
    state.book_dir = str(new_path)
    state.slug = new_path.name
    save_state(state)
    log.info("Renamed book directory: %s -> %s", old_path.name, new_path.name)
    return old_path, new_path


def resolve_book_dir(topic: str, book_type: BookType, *, resume: str | None = None) -> Path:
    """Resolve the directory for a given run. Reuses existing slug when resuming."""
    base = get_settings().kdp_books_dir
    base.mkdir(parents=True, exist_ok=True)

    if resume:
        candidate = base / resume
        if not candidate.exists():
            raise FileNotFoundError(f"Cannot resume: {candidate} does not exist")
        return candidate

    slug_root = f"{_slugify(topic)}-{book_type.value}"
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    book_dir = base / f"{slug_root}-{timestamp}"
    book_dir.mkdir(parents=True, exist_ok=True)
    return book_dir


def state_path(book_dir: str | Path) -> Path:
    return Path(book_dir) / _STATE_FILE


def save_state(state: IBookState) -> None:
    """Atomic-write the book state to `<book_dir>/book.json`."""
    path = state_path(state.book_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state(book_dir: str | Path) -> IBookState | None:
    """Load `IBookState` from disk if it exists; otherwise return `None`."""
    path = state_path(book_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return IBookState.model_validate(data)
    except Exception as exc:
        log.warning("Failed to read state at %s: %s", path, exc)
        return None
