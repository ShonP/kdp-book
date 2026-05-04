"""On-disk state IO — book directory layout, atomic JSON, slug resolution."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from kdp_book.config import get_settings
from kdp_book.log import log
from kdp_book.models.book import BookType, IBookState

_STATE_FILE = "book.json"


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:60] or "untitled"


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
