"""Step helpers — pure functions that take `IBookState` and return the next.

Each helper wraps exactly one agent invocation. The workflow file
(`pipeline.py`) decorates these with `@step` for checkpointing.
"""

from __future__ import annotations

from kdp_book.agents.bible import generate_bible
from kdp_book.agents.concept import generate_concept
from kdp_book.agents.outline import generate_outline
from kdp_book.log import log
from kdp_book.models.book import IBookState


async def do_concept(state: IBookState) -> IBookState:
    if state.concept is not None and "concept" in state.completed_steps:
        log.debug("Concept already present, skipping")
        return state
    state.concept = await generate_concept(
        topic=state.config.topic,
        book_type=state.config.book_type,
        type_config=state.config.type_config,
    )
    log.info("Concept: %r — %s", state.concept.title, state.concept.tone)
    state.mark_done("concept")
    return state


async def do_outline(state: IBookState) -> IBookState:
    if state.concept is None:
        raise RuntimeError("Cannot outline before concept")
    if state.outline is not None and "outline" in state.completed_steps:
        log.debug("Outline already present, skipping")
        return state
    state.outline = await generate_outline(
        concept=state.concept,
        type_config=state.config.type_config,
    )
    log.info("Outline: %d chapters", len(state.outline.chapters))
    state.mark_done("outline")
    return state


async def do_bible(state: IBookState) -> IBookState:
    if state.concept is None or state.outline is None:
        raise RuntimeError("Cannot build bible before concept + outline")
    if state.bible is not None and "bible" in state.completed_steps:
        log.debug("Bible already present, skipping")
        return state
    state.bible = await generate_bible(
        concept=state.concept,
        outline=state.outline,
        type_config=state.config.type_config,
    )
    log.info(
        "Bible: %d characters, %d locations",
        len(state.bible.characters),
        len(state.bible.locations),
    )
    state.mark_done("bible")
    return state
