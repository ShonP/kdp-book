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


def _record(state: IBookState, step_name: str):
    return step_recorder(state.book_dir, step_name)


@step
async def step_concept(state: IBookState) -> IBookState:
    log.info("Step concept: %s (%s)", state.config.topic, state.config.book_type.value)
    with _record(state, "concept"):
        state = await do_concept(state)
        save_state(state)
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
    log.info("Step illustrate: planning page-level briefs")
    with _record(state, "illustrate"):
        state = await do_illustrate(state)
        save_state(state)
    return state


@step
async def step_characters(state: IBookState) -> IBookState:
    log.info("Step characters: rendering reference sheets")
    with _record(state, "characters"):
        state = await do_characters(state)
        save_state(state)
    return state


@step
async def step_images(state: IBookState) -> IBookState:
    log.info("Step images: rendering page illustrations")
    with _record(state, "images"):
        state = await do_images(state)
        save_state(state)
    return state


@step
async def step_consistency(state: IBookState) -> IBookState:
    log.info("Step consistency: not yet implemented (Phase 8)")
    with _record(state, "consistency"):
        state.mark_done("consistency")
        save_state(state)
    return state


@step
async def step_cover(state: IBookState) -> IBookState:
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
    log.info("Step publish: not yet implemented (Phase 10)")
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
        return existing

    book_type = BookType(input_data["book_type"])
    config = IBookConfig(
        topic=input_data["topic"],
        book_type=book_type,
        type_config=get_book_type_config(book_type),
        author=input_data.get("author") or get_settings().kdp_author_name,
    )
    state = IBookState(slug=book_dir.name, book_dir=str(book_dir), config=config)
    save_state(state)
    return state


async def run_book_async(
    topic: str,
    book_type: BookType,
    *,
    resume: str | None = None,
    author: str | None = None,
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

    log.info("Starting kdp-book: topic=%r type=%s slug=%s", topic, book_type.value, book_dir.name)
    log.info("Book directory: %s", book_dir)

    checkpoint_dir = book_dir / ".checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    storage = FileCheckpointStorage(str(checkpoint_dir))

    try:
        result = await book_workflow.run(
            {
                "topic": topic,
                "book_type": book_type.value,
                "book_dir": str(book_dir),
                "author": author or get_settings().kdp_author_name,
                "started_at": datetime.now(UTC).isoformat(),
            },
            checkpoint_storage=storage,
        )
        outputs = result.get_outputs() if hasattr(result, "get_outputs") else []
        log.info("Pipeline complete (%d outputs)", len(outputs) if outputs else 0)
        finalize_run_metadata(book_dir, status="ok")
        return str(book_dir)
    except Exception:
        log.exception("Pipeline failed")
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


def run_book(
    topic: str,
    book_type: BookType,
    *,
    resume: str | None = None,
    author: str | None = None,
) -> str:
    """Synchronous wrapper for `run_book_async`."""
    return asyncio.run(run_book_async(topic, book_type, resume=resume, author=author))
