"""Step helpers — pure functions that take `IBookState` and return the next.

Each helper wraps exactly one agent invocation. The workflow file
(`pipeline.py`) decorates these with `@step` for checkpointing.
"""

from __future__ import annotations

from pathlib import Path

from kdp_book.agents.bible import generate_bible
from kdp_book.agents.concept import generate_concept
from kdp_book.agents.editor import edit_manuscript
from kdp_book.agents.outline import generate_outline
from kdp_book.agents.writer import write_chapter
from kdp_book.log import log
from kdp_book.models.book import IBookState, IChapterDraft, IManuscript
from kdp_book.workflow.state import save_state


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


async def do_write(state: IBookState) -> IBookState:
    """Draft every chapter. Idempotent per-chapter via `written_chapter_indices`."""
    if state.concept is None or state.outline is None or state.bible is None:
        raise RuntimeError("Cannot write before concept + outline + bible")

    chapters_dir = Path(state.book_dir) / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    if state.manuscript is None:
        state.manuscript = IManuscript()

    drafted_by_index: dict[int, IChapterDraft] = {
        d.index: d for d in state.manuscript.chapters
    }

    for chapter in state.outline.chapters:
        if chapter.index in state.written_chapter_indices and chapter.index in drafted_by_index:
            log.debug("Chapter %d already drafted, skipping", chapter.index)
            continue
        previous_tail = ""
        if chapter.index > 1:
            prev = drafted_by_index.get(chapter.index - 1)
            if prev and prev.prose:
                previous_tail = "\n\n".join(prev.prose.strip().split("\n\n")[-2:])

        log.info(
            "Writing chapter %d/%d: %s",
            chapter.index,
            len(state.outline.chapters),
            chapter.title,
        )
        draft = await write_chapter(
            concept=state.concept,
            bible=state.bible,
            outline=state.outline,
            chapter=chapter,
            type_config=state.config.type_config,
            previous_tail=previous_tail,
        )
        # Persist per-chapter file (atomic-ish: write tmp, rename).
        path = chapters_dir / f"chapter-{chapter.index:02d}.md"
        tmp = path.with_suffix(path.suffix + ".tmp")
        body = f"# {draft.title}\n\n{draft.prose.strip()}\n"
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)

        drafted_by_index[chapter.index] = draft
        state.manuscript.chapters = sorted(drafted_by_index.values(), key=lambda c: c.index)
        state.manuscript.total_word_count = sum(c.word_count for c in state.manuscript.chapters)
        if chapter.index not in state.written_chapter_indices:
            state.written_chapter_indices.append(chapter.index)
        save_state(state)
        log.info("Chapter %d done (%d words)", chapter.index, draft.word_count)

    state.mark_done("write")
    return state


async def do_edit(state: IBookState) -> IBookState:
    """Run the editor over the full manuscript and optionally rewrite weak sections."""
    if state.manuscript is None or not state.manuscript.chapters:
        raise RuntimeError("Cannot edit before write")
    if state.editor_report is not None and "edit" in state.completed_steps:
        log.debug("Editor report already present, skipping")
        return state
    if state.concept is None or state.outline is None or state.bible is None:
        raise RuntimeError("Cannot edit without concept + outline + bible")

    state.editor_report = await edit_manuscript(
        concept=state.concept,
        bible=state.bible,
        outline=state.outline,
        manuscript=state.manuscript,
    )
    log.info(
        "Editor score: %d/10 — %d issues, %d chapters flagged",
        state.editor_report.score,
        len(state.editor_report.issues),
        len(state.editor_report.chapters_to_revise),
    )

    # Persist editor report
    report_path = Path(state.book_dir) / "editor_report.json"
    tmp = report_path.with_suffix(".tmp")
    tmp.write_text(state.editor_report.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(report_path)

    # Revision pass: rewrite flagged chapters if score < threshold.
    if state.editor_report.score < 7 and state.editor_report.chapters_to_revise:
        await _revise_flagged_chapters(state)

    state.mark_done("edit")
    return state


async def _revise_flagged_chapters(state: IBookState) -> None:
    """Re-draft chapters the editor flagged using their issues as guidance."""
    if (
        state.manuscript is None
        or state.editor_report is None
        or state.outline is None
        or state.concept is None
        or state.bible is None
    ):
        return
    chapters_by_index = {c.index: c for c in state.outline.chapters}
    drafts_by_index = {d.index: d for d in state.manuscript.chapters}
    chapters_dir = Path(state.book_dir) / "chapters"

    for ch_idx in state.editor_report.chapters_to_revise:
        chapter = chapters_by_index.get(ch_idx)
        if chapter is None:
            continue
        notes = [
            issue.note for issue in state.editor_report.issues if issue.chapter_index == ch_idx
        ]
        # Append editor notes into the prompt by mutating the chapter summary
        # for the second draft.
        revised_chapter = chapter.model_copy(
            update={
                "summary": chapter.summary
                + "\n\nREVISION NOTES (must address):\n- "
                + "\n- ".join(notes or ["Tighten and improve clarity."])
            }
        )
        log.info("Revising chapter %d (%d notes)", ch_idx, len(notes))
        prev = drafts_by_index.get(ch_idx - 1)
        previous_tail = (
            "\n\n".join(prev.prose.strip().split("\n\n")[-2:]) if prev and prev.prose else ""
        )
        new_draft = await write_chapter(
            concept=state.concept,
            bible=state.bible,
            outline=state.outline,
            chapter=revised_chapter,
            type_config=state.config.type_config,
            previous_tail=previous_tail,
        )
        drafts_by_index[ch_idx] = new_draft
        path = chapters_dir / f"chapter-{ch_idx:02d}.md"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(f"# {new_draft.title}\n\n{new_draft.prose.strip()}\n", encoding="utf-8")
        tmp.replace(path)

    state.manuscript.chapters = sorted(drafts_by_index.values(), key=lambda c: c.index)
    state.manuscript.total_word_count = sum(c.word_count for c in state.manuscript.chapters)
    save_state(state)
