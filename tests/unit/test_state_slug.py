"""Unit tests for slug + book-dir rename in `kdp_book.workflow.state`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kdp_book.models.book import (
    BookType,
    IBookConcept,
    IBookConfig,
    IBookState,
    get_book_type_config,
)
from kdp_book.workflow.state import (
    _slugify,
    load_state,
    rename_book_dir_for_title,
    save_state,
)


def _make_state(
    book_dir: Path, *, topic: str, slug: str, title: str | None = None
) -> IBookState:
    book_type = BookType.CHILDREN_PICTURE_BOOK
    config = IBookConfig(
        topic=topic,
        book_type=book_type,
        type_config=get_book_type_config(book_type),
        author="Tester",
        language="he" if any(0x0590 <= ord(c) <= 0x05FF for c in topic) else "en",
        created_at=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
    )
    state = IBookState(slug=slug, book_dir=str(book_dir), config=config)
    if title:
        state.concept = IBookConcept(
            title=title,
            subtitle="",
            hook="x",
            audience="kids 4-6",
            tone="warm",
            target_word_count=400,
            target_chapter_count=8,
        )
    save_state(state)
    return state


@pytest.mark.unit
def test_slugify_ascii_unchanged() -> None:
    assert _slugify("Hello World") == "hello-world"
    assert _slugify("Echo") == "echo"


@pytest.mark.unit
def test_slugify_keeps_hebrew_letters() -> None:
    title = "סירות בעיר הגדולה"
    slug = _slugify(title)
    assert slug == "סירות-בעיר-הגדולה"
    assert "untitled" not in slug


@pytest.mark.unit
def test_slugify_keeps_japanese_letters() -> None:
    slug = _slugify("日本語タイトル")
    assert slug == "日本語タイトル"


@pytest.mark.unit
def test_slugify_collapses_whitespace_and_punctuation() -> None:
    assert _slugify("  Hello!!  World??  ") == "hello-world"
    assert _slugify("a---b___c") == "a-b___c"


@pytest.mark.unit
def test_slugify_punctuation_only_falls_back_to_hash() -> None:
    slug = _slugify("!!!")
    assert slug.startswith("book-")
    assert len(slug) == len("book-") + 8
    # Same input must produce the same hash slug deterministically.
    assert _slugify("!!!") == slug


@pytest.mark.unit
def test_slugify_empty_string_falls_back_to_hash() -> None:
    slug = _slugify("")
    assert slug.startswith("book-")


@pytest.mark.unit
def test_slugify_truncates_to_60_chars() -> None:
    long_title = "a" * 200
    slug = _slugify(long_title)
    assert len(slug) == 60


@pytest.mark.unit
def test_slugify_strips_leading_trailing_dashes() -> None:
    assert _slugify("---hello---") == "hello"


@pytest.mark.unit
def test_rename_renames_dir_and_updates_state(tmp_path: Path) -> None:
    book_dir = tmp_path / "untitled-children-picture-book-20260506-194851"
    book_dir.mkdir()
    state = _make_state(
        book_dir, topic="ספר מצויר", slug=book_dir.name, title="סירות בעיר הגדולה",
    )

    result = rename_book_dir_for_title(state, "סירות בעיר הגדולה")

    assert result is not None
    old_path, new_path = result
    assert old_path == book_dir
    assert new_path.name == "סירות-בעיר-הגדולה-children-picture-book-20260506-194851"
    assert new_path.exists()
    assert not old_path.exists()
    assert state.book_dir == str(new_path)
    assert state.slug == new_path.name

    persisted = load_state(new_path)
    assert persisted is not None
    assert persisted.slug == new_path.name


@pytest.mark.unit
def test_rename_is_noop_when_slug_unchanged(tmp_path: Path) -> None:
    title = "Hello World"
    dir_name = "hello-world-children-picture-book-20260506-194851"
    book_dir = tmp_path / dir_name
    book_dir.mkdir()
    state = _make_state(book_dir, topic=title, slug=dir_name, title=title)

    result = rename_book_dir_for_title(state, title)

    assert result is None
    assert book_dir.exists()
    assert state.book_dir == str(book_dir)


@pytest.mark.unit
def test_rename_appends_suffix_on_collision(tmp_path: Path) -> None:
    src = tmp_path / "untitled-children-picture-book-20260506-194851"
    src.mkdir()
    collision = tmp_path / "echo-children-picture-book-20260506-194851"
    collision.mkdir()
    state = _make_state(src, topic="x", slug=src.name, title="Echo")

    result = rename_book_dir_for_title(state, "Echo")

    assert result is not None
    _, new_path = result
    assert new_path.name == "echo-2-children-picture-book-20260506-194851"
    assert new_path.exists()


@pytest.mark.unit
def test_rename_skips_when_dir_pattern_unrecognized(tmp_path: Path) -> None:
    book_dir = tmp_path / "totally-custom-name"
    book_dir.mkdir()
    state = _make_state(book_dir, topic="x", slug=book_dir.name, title="New Title")

    result = rename_book_dir_for_title(state, "New Title")

    assert result is None
    assert book_dir.exists()


@pytest.mark.unit
def test_rename_preserves_relative_image_paths(tmp_path: Path) -> None:
    book_dir = tmp_path / "untitled-children-picture-book-20260506-194851"
    (book_dir / "images").mkdir(parents=True)
    payload = b"\x89PNG\r\n\x1a\n"
    (book_dir / "images" / "page1.png").write_bytes(payload)
    state = _make_state(book_dir, topic="topic", slug=book_dir.name, title="שלום עולם")

    result = rename_book_dir_for_title(state, "שלום עולם")

    assert result is not None
    _, new_path = result
    rel_image = Path("images/page1.png")
    assert (new_path / rel_image).read_bytes() == payload
