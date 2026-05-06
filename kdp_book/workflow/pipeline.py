"""Resumable book pipeline.

`@workflow` + `@step` + `FileCheckpointStorage` — same idiom as
deep-research. Every step is a pure transformation `IBookState → IBookState`
so a crash anywhere can be resumed by re-running with the same slug.

Phase 1 ships `step_concept` + `step_outline` + `step_bible` wired live.
Later phases plug in their step implementations — this file already
declares the full chain so the workflow shape is locked early.
"""

from __future__ import annotations

import asyncio
import warnings
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

warnings.filterwarnings("ignore", message=r".*FUNCTIONAL_WORKFLOWS.*")

from agent_framework import FileCheckpointStorage, step, workflow  # noqa: E402

from kdp_book.config import get_settings  # noqa: E402
from kdp_book.log import attach_file_handler, detach_file_handler, log, new_run_id  # noqa: E402
from kdp_book.models.book import (  # noqa: E402
    BookType,
    IBookConfig,
    IBookState,
    get_book_type_config,
)
from kdp_book.observability import (  # noqa: E402
    finalize_run_metadata,
    init_run_metadata,
    reset_step_counter,
    step_recorder,
)
from kdp_book.workflow.state import (  # noqa: E402
    load_state,
    rename_book_dir_for_title,
    resolve_book_dir,
    save_state,
)
from kdp_book.workflow.steps import (  # noqa: E402
    do_bible,
    do_characters,
    do_concept,
    do_cover,
    do_edit,
    do_format,
    do_illustrate,
    do_images,
    do_metadata,
    do_outline,
    do_quality,
    do_write,
)

_active_storage: ContextVar[FileCheckpointStorage | None] = ContextVar(
    "_active_storage", default=None,
)


def _record(state: IBookState, step_name: str):
    return step_recorder(state.book_dir, step_name)


def _rename_dir_after_concept(state: IBookState) -> IBookState:
    """Rename `<book_dir>` to use the title-derived slug now that concept is known.

    Called from `step_concept` immediately after `do_concept`. Updates:
      * the directory on disk
      * `state.book_dir` and `state.slug` (persisted via `rename_book_dir_for_title`)
      * the active `FileCheckpointStorage.storage_path` so subsequent
        framework checkpoints land in the renamed directory
      * the file log handler so subsequent log lines target the new path
    """
    if state.concept is None or not state.concept.title:
        return state
    rename = rename_book_dir_for_title(state, state.concept.title)
    if rename is None:
        return state
    old_path, new_path = rename

    storage = _active_storage.get()
    if storage is not None:
        old_storage_path = Path(storage.storage_path)
        try:
            relative = old_storage_path.relative_to(old_path)
            storage.storage_path = new_path / relative
            storage.storage_path.mkdir(parents=True, exist_ok=True)
        except ValueError:
            log.debug(
                "Storage path %s is not inside renamed dir %s; leaving untouched",
                old_storage_path, old_path,
            )

    attach_file_handler(new_path)
    return state


@step
async def step_concept(state: IBookState) -> IBookState:
    log.info("Step concept: %s (%s)", state.config.topic, state.config.book_type.value)
    with _record(state, "concept"):
        state = await do_concept(state)
        save_state(state)
    state = _rename_dir_after_concept(state)
    return state


@step
async def step_outline(state: IBookState) -> IBookState:
    log.info("Step outline: building chapter plan")
    with _record(state, "outline"):
        state = await do_outline(state)
        save_state(state)
    return state


@step
async def step_bible(state: IBookState) -> IBookState:
    log.info("Step bible: characters + style guide")
    with _record(state, "bible"):
        state = await do_bible(state)
        save_state(state)
    return state


@step
async def step_write(state: IBookState) -> IBookState:
    log.info("Step write: drafting manuscript")
    with _record(state, "write"):
        state = await do_write(state)
        save_state(state)
    return state


@step
async def step_edit(state: IBookState) -> IBookState:
    log.info("Step edit: editorial review")
    with _record(state, "edit"):
        state = await do_edit(state)
        save_state(state)
    return state


@step
async def step_illustrate(state: IBookState) -> IBookState:
    if state.skip_images:
        log.info("Step illustrate: skipped (skip_images=True)")
        with _record(state, "illustrate"):
            state.mark_done("illustrate")
            save_state(state)
        return state
    log.info("Step illustrate: planning page-level briefs")
    with _record(state, "illustrate"):
        state = await do_illustrate(state)
        save_state(state)
    return state


@step
async def step_characters(state: IBookState) -> IBookState:
    if state.skip_images:
        log.info("Step characters: skipped (skip_images=True)")
        with _record(state, "characters"):
            state.mark_done("characters")
            save_state(state)
        return state
    log.info("Step characters: rendering reference sheets")
    with _record(state, "characters"):
        state = await do_characters(state)
        save_state(state)
    return state


@step
async def step_images(state: IBookState) -> IBookState:
    if state.skip_images:
        log.info("Step images: skipped (skip_images=True)")
        with _record(state, "images"):
            state.mark_done("images")
            save_state(state)
        return state
    log.info("Step images: rendering page illustrations")
    with _record(state, "images"):
        state = await do_images(state)
        save_state(state)
    return state


@step
async def step_consistency(state: IBookState) -> IBookState:
    """Visual consistency gate. Currently a structural check —
    full vision-model audit ships in a future phase."""
    log.info("Step consistency: validating asset coverage")
    with _record(state, "consistency"):
        if not state.skip_images and state.illustrations:
            covered = {(im.chapter_index, im.scene_index) for im in state.images}
            wanted = {(b.chapter_index, b.scene_index) for b in state.illustrations}
            missing = wanted - covered
            if missing:
                log.warning(
                    "Consistency: %d illustration briefs lack rendered images",
                    len(missing),
                )
        state.mark_done("consistency")
        save_state(state)
    return state


@step
async def step_cover(state: IBookState) -> IBookState:
    if state.skip_images:
        log.info("Step cover: skipped (skip_images=True)")
        with _record(state, "cover"):
            state.mark_done("cover")
            save_state(state)
        return state
    log.info("Step cover: design + render + compose wrap")
    with _record(state, "cover"):
        state = await do_cover(state)
        save_state(state)
    return state


@step
async def step_format(state: IBookState) -> IBookState:
    log.info("Step format: rendering PDF + EPUB")
    with _record(state, "format"):
        state = await do_format(state, output="both")
        save_state(state)
    return state


@step
async def step_metadata(state: IBookState) -> IBookState:
    log.info("Step metadata: generating KDP listing metadata")
    with _record(state, "metadata"):
        state = await do_metadata(state)
        save_state(state)
    return state


@step
async def step_quality(state: IBookState) -> IBookState:
    log.info("Step quality: final quality review")
    with _record(state, "quality"):
        state = await do_quality(state)
        save_state(state)
    return state


@step
async def step_publish(state: IBookState) -> str:
    """Final step: marks the run complete and returns the book directory.

    Real KDP API upload is not yet supported — the produced
    `output/interior.pdf`, `output/<slug>.epub`, and `cover/cover_wrap.pdf`
    are ready for manual upload to the KDP dashboard.
    """
    log.info("Step publish: pipeline complete — outputs ready in %s/output", state.book_dir)
    with _record(state, "publish"):
        state.mark_done("publish")
        save_state(state)
    return state.book_dir


@workflow(name="kdp_book")
async def book_workflow(input_data: dict) -> str:
    """Top-level resumable book pipeline."""
    state = _init_state(input_data)
    state = await step_concept(state)
    state = await step_outline(state)
    state = await step_bible(state)
    state = await step_write(state)
    state = await step_edit(state)
    state = await step_illustrate(state)
    state = await step_characters(state)
    state = await step_images(state)
    state = await step_consistency(state)
    state = await step_cover(state)
    state = await step_format(state)
    state = await step_metadata(state)
    state = await step_quality(state)
    return await step_publish(state)


def _init_state(input_data: dict) -> IBookState:
    """Build (or restore) the initial `IBookState` for a workflow run."""
    book_dir = Path(input_data["book_dir"])
    existing = load_state(book_dir)
    if existing is not None:
        log.info("Resuming from %s (steps done: %s)", book_dir.name, existing.completed_steps)
        if "skip_images" in input_data:
            existing.skip_images = bool(input_data["skip_images"])
        return existing

    book_type = BookType(input_data["book_type"])
    config = IBookConfig(
        topic=input_data["topic"],
        book_type=book_type,
        type_config=get_book_type_config(book_type),
        author=input_data.get("author") or get_settings().kdp_author_name,
        language=input_data.get("language") or "en",
    )
    state = IBookState(slug=book_dir.name, book_dir=str(book_dir), config=config)
    state.skip_images = bool(input_data.get("skip_images", False))
    save_state(state)
    return state


async def run_book_async(
    topic: str,
    book_type: BookType,
    *,
    resume: str | None = None,
    author: str | None = None,
    skip_images: bool = False,
    language: str = "en",
) -> str:
    """Run the full pipeline. Returns the final book directory path."""
    run_id = new_run_id()
    book_dir = resolve_book_dir(topic, book_type, resume=resume)
    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=run_id,
        topic=topic,
        book_type=book_type.value,
        slug=book_dir.name,
    )

    log.info(
        "Starting kdp-book: topic=%r type=%s lang=%s slug=%s skip_images=%s",
        topic, book_type.value, language, book_dir.name, skip_images,
    )
    log.info("Book directory: %s", book_dir)

    checkpoint_dir = book_dir / ".checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    storage = FileCheckpointStorage(str(checkpoint_dir))
    _active_storage.set(storage)

    try:
        result = await book_workflow.run(
            {
                "topic": topic,
                "book_type": book_type.value,
                "book_dir": str(book_dir),
                "author": author or get_settings().kdp_author_name,
                "language": language,
                "skip_images": skip_images,
                "started_at": datetime.now(UTC).isoformat(),
            },
            checkpoint_storage=storage,
        )
        outputs = result.get_outputs() if hasattr(result, "get_outputs") else []
        log.info("Pipeline complete (%d outputs)", len(outputs) if outputs else 0)
        final_book_dir = _resolve_final_book_dir(book_dir, storage)
        finalize_run_metadata(final_book_dir, status="ok")
        return str(final_book_dir)
    except Exception:
        log.exception("Pipeline failed")
        final_book_dir = _resolve_final_book_dir(book_dir, storage)
        finalize_run_metadata(final_book_dir, status="failed")
        raise
    finally:
        _active_storage.set(None)
        detach_file_handler()


def _resolve_final_book_dir(
    original: Path, storage: FileCheckpointStorage,
) -> Path:
    """Return the current location of the book directory.

    `step_concept` may have renamed the directory after the concept step
    succeeded; the active `FileCheckpointStorage.storage_path` is the
    most reliable witness because it is patched in lockstep with the
    rename. Falls back to `original` if the storage path is unrelated
    or missing.
    """
    try:
        sp = Path(storage.storage_path)
        candidate = sp.parent
        if candidate.exists() and candidate != original:
            return candidate
    except Exception as e:
        log.debug("Could not derive final book dir from storage: %s", e)
    return original


def run_book(
    topic: str,
    book_type: BookType,
    *,
    resume: str | None = None,
    author: str | None = None,
    skip_images: bool = False,
    language: str = "en",
) -> str:
    """Synchronous wrapper for `run_book_async`."""
    return asyncio.run(run_book_async(
        topic, book_type,
        resume=resume,
        author=author,
        skip_images=skip_images,
        language=language,
    ))
