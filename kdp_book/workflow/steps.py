"""Step helpers — pure functions that take `IBookState` and return the next.

Each helper wraps exactly one agent invocation. The workflow file
(`pipeline.py`) decorates these with `@step` for checkpointing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from kdp_book.agents.bible import generate_bible
from kdp_book.agents.concept import generate_concept
from kdp_book.agents.editor import edit_manuscript
from kdp_book.agents.illustrator import plan_chapter_illustrations
from kdp_book.agents.outline import generate_outline
from kdp_book.agents.writer import write_chapter
from kdp_book.log import log
from kdp_book.models.assets import IAssetEntry, IImageAsset
from kdp_book.models.book import (
    IBookState,
    IChapterDraft,
    IIllustrationBrief,
    IManuscript,
    IPageImage,
)
from kdp_book.observability import make_image_record, record_image, write_image_sidecar
from kdp_book.tools.asset_registry import (
    add_character_variant,
    add_page_image,
    get_character_ref,
)
from kdp_book.tools.atomic_io import atomic_write_bytes
from kdp_book.tools.image_gen import render_with_retry
from kdp_book.tools.prompt_builder import (
    build_character_sheet_prompt,
    build_scene_prompt,
)
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


async def do_illustrate(state: IBookState) -> IBookState:
    """Plan composition-only illustration briefs per chapter."""
    if state.outline is None or state.bible is None or state.concept is None:
        raise RuntimeError("Cannot illustrate before outline/bible/concept")
    if state.illustrations and "illustrate" in state.completed_steps:
        log.debug("Illustrations already planned, skipping")
        return state

    illustrations_per_chapter = state.config.type_config.illustrations_per_chapter
    if illustrations_per_chapter <= 0:
        log.info("Book type %s requests 0 illustrations per chapter; skipping", state.config.book_type.value)
        state.illustrations = []
        state.mark_done("illustrate")
        return state

    all_briefs: list[IIllustrationBrief] = []
    for chapter in state.outline.chapters:
        log.info("Planning illustrations for chapter %d", chapter.index)
        briefs = await plan_chapter_illustrations(
            concept=state.concept,
            bible=state.bible,
            chapter=chapter,
            illustrations_per_chapter=illustrations_per_chapter,
        )
        all_briefs.extend(briefs)
        # Persist progress so a crash mid-plan resumes correctly.
        state.illustrations = all_briefs
        save_state(state)

    log.info("Planned %d illustrations across %d chapters", len(all_briefs), len(state.outline.chapters))
    state.mark_done("illustrate")
    return state


async def do_characters(state: IBookState) -> IBookState:
    """Render the `default` reference sheet for every character in the bible."""
    if state.bible is None:
        raise RuntimeError("Cannot render characters before bible")
    if "characters" in state.completed_steps:
        log.debug("Characters already rendered, skipping")
        return state

    book_dir = Path(state.book_dir)
    refs_dir = book_dir / "references" / "characters"
    refs_dir.mkdir(parents=True, exist_ok=True)

    for character in state.bible.characters:
        # Skip if already rendered
        existing = get_character_ref(book_dir, character.name, "default")
        if existing is not None:
            log.debug("Character %s/default already rendered, skipping", character.name)
            continue

        prompt = build_character_sheet_prompt(
            character_name=character.name,
            appearance=character.appearance,
            costume=character.costume,
            palette=character.palette,
            style=state.bible.style_guide,
        )
        log.info("Rendering character sheet: %s", character.name)
        try:
            img_bytes, retry_meta = render_with_retry(
                prompt=prompt,
                references=None,
                size=state.config.type_config.image_size,
                quality="high",
                content_rating=_content_rating(state),
            )
        except RuntimeError as e:
            log.error("Failed to render character %s: %s", character.name, e)
            continue

        safe_name = character.name.lower().replace(" ", "-")
        rel_path = f"references/characters/{safe_name}__default.png"
        out_path = book_dir / rel_path
        atomic_write_bytes(out_path, img_bytes)

        rec = make_image_record(
            asset_type="character_sheet",
            name=f"{character.name}/default",
            path=str(out_path),
            prompt=prompt,
            references=[],
            model="gpt-image-2",
            size=state.config.type_config.image_size,
            quality="high",
            retry_count=len(retry_meta.get("retries", [])),
            safety_filter_hits=retry_meta.get("safety_filter_hits", 0),
            duration_seconds=retry_meta.get("duration_seconds", 0.0),
        )
        write_image_sidecar(out_path, rec)
        record_image(rec)

        entry = IAssetEntry(
            path=rel_path,
            generated_at=datetime.now(UTC),
            variant="default",
            chain_from="",
            stale=False,
        )
        add_character_variant(book_dir, name=character.name, variant="default", entry=entry)

    state.mark_done("characters")
    return state


async def do_images(state: IBookState) -> IBookState:
    """Render every page image, attaching the right character refs."""
    if not state.illustrations or state.bible is None or state.concept is None:
        log.info("No illustrations planned; skipping page renders")
        state.mark_done("images")
        return state
    if "images" in state.completed_steps and len(state.images) >= len(state.illustrations):
        log.debug("All page images already rendered, skipping")
        return state

    book_dir = Path(state.book_dir)
    pages_dir = book_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    rendered_keys = {(img.chapter_index, img.scene_index) for img in state.images}

    page_counter = max([img.page_index for img in state.images], default=0)

    for brief in state.illustrations:
        key = (brief.chapter_index, brief.scene_index)
        if key in rendered_keys:
            continue
        page_counter += 1
        brief.page_index = page_counter

        # Resolve refs for every named character present
        refs: list[Path] = []
        for char_name in brief.characters_present:
            ref = get_character_ref(book_dir, char_name, "default")
            if ref is not None:
                refs.append(ref)
            else:
                log.warning("No reference image for %s; proceeding text-only", char_name)

        prompt = build_scene_prompt(
            brief=brief,
            style=state.bible.style_guide,
            concept=state.concept,
        )
        log.info(
            "Rendering page %d (chapter %d, scene %d) with %d refs",
            page_counter, brief.chapter_index, brief.scene_index, len(refs),
        )
        try:
            img_bytes, retry_meta = render_with_retry(
                prompt=prompt,
                references=refs or None,
                size=state.config.type_config.image_size,
                quality="high",
                content_rating=_content_rating(state),
            )
        except RuntimeError as e:
            log.error(
                "Failed to render page %d (ch%d sc%d): %s",
                page_counter, brief.chapter_index, brief.scene_index, e,
            )
            continue

        rel_path = f"pages/page-{page_counter:03d}.png"
        out_path = book_dir / rel_path
        atomic_write_bytes(out_path, img_bytes)

        rec = make_image_record(
            asset_type="page",
            name=f"ch{brief.chapter_index:02d}-sc{brief.scene_index:02d}",
            path=str(out_path),
            prompt=prompt,
            references=refs,
            model="gpt-image-2",
            size=state.config.type_config.image_size,
            quality="high",
            retry_count=len(retry_meta.get("retries", [])),
            safety_filter_hits=retry_meta.get("safety_filter_hits", 0),
            duration_seconds=retry_meta.get("duration_seconds", 0.0),
        )
        sidecar = write_image_sidecar(out_path, rec)
        record_image(rec)

        page_image = IPageImage(
            chapter_index=brief.chapter_index,
            scene_index=brief.scene_index,
            page_index=page_counter,
            image_path=rel_path,
            sidecar_path=str(sidecar.relative_to(book_dir)),
        )
        state.images.append(page_image)

        # Also record in manifest
        add_page_image(
            book_dir,
            IImageAsset(
                page_index=page_counter,
                path=rel_path,
                prompt=prompt,
                refs=[str(r.relative_to(book_dir)) for r in refs],
                generated_at=datetime.now(UTC),
            ),
        )
        save_state(state)

    state.mark_done("images")
    return state


def _content_rating(state: IBookState) -> str:
    """Map BookType to a content rating for the safety filter softener."""
    from kdp_book.models.book import BookType

    return {
        BookType.CHILDREN_PICTURE_BOOK: "all-ages",
        BookType.LIGHT_NOVEL: "teen",
        BookType.NON_FICTION: "all-ages",
        BookType.FICTION_NOVEL: "teen",
    }.get(state.config.book_type, "all-ages")


async def do_cover(state: IBookState) -> IBookState:
    """Design + render front (and optional back) cover, then compose the wrap."""
    from kdp_book.agents.cover import design_cover
    from kdp_book.formats.cover_compositor import compose_cover_wrap
    from kdp_book.formats.cover_geometry import cover_dimensions

    if state.concept is None or state.bible is None:
        raise RuntimeError("Cannot design cover before concept + bible")
    if state.cover is not None and "cover" in state.completed_steps:
        log.debug("Cover already done, skipping")
        return state

    book_dir = Path(state.book_dir)
    cover_dir = book_dir / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)

    if state.cover is None:
        state.cover = await design_cover(
            concept=state.concept,
            bible=state.bible,
            metadata=state.metadata,
        )

    rating = _content_rating(state)
    front_path = cover_dir / "front.png"
    if not front_path.exists():
        log.info("Rendering front cover")
        img_bytes, retry_meta = render_with_retry(
            prompt=state.cover.front_prompt,
            references=None,
            size=state.config.type_config.image_size,
            quality="high",
            content_rating=rating,
        )
        atomic_write_bytes(front_path, img_bytes)
        rec = make_image_record(
            asset_type="cover_front",
            name="cover/front",
            path=str(front_path),
            prompt=state.cover.front_prompt,
            references=[],
            model="gpt-image-2",
            size=state.config.type_config.image_size,
            quality="high",
            retry_count=len(retry_meta.get("retries", [])),
            safety_filter_hits=retry_meta.get("safety_filter_hits", 0),
            duration_seconds=retry_meta.get("duration_seconds", 0.0),
        )
        write_image_sidecar(front_path, rec)
        record_image(rec)
    state.cover.front_image_path = str(front_path.relative_to(book_dir))

    back_path: Path | None = None
    if state.cover.back_prompt:
        back_path = cover_dir / "back.png"
        if not back_path.exists():
            log.info("Rendering back cover")
            try:
                img_bytes, retry_meta = render_with_retry(
                    prompt=state.cover.back_prompt,
                    references=None,
                    size=state.config.type_config.image_size,
                    quality="high",
                    content_rating=rating,
                )
                atomic_write_bytes(back_path, img_bytes)
                rec = make_image_record(
                    asset_type="cover_back",
                    name="cover/back",
                    path=str(back_path),
                    prompt=state.cover.back_prompt,
                    references=[],
                    model="gpt-image-2",
                    size=state.config.type_config.image_size,
                    quality="high",
                    retry_count=len(retry_meta.get("retries", [])),
                    safety_filter_hits=retry_meta.get("safety_filter_hits", 0),
                    duration_seconds=retry_meta.get("duration_seconds", 0.0),
                )
                write_image_sidecar(back_path, rec)
                record_image(rec)
            except RuntimeError as e:
                log.warning("Back cover render failed (%s); falling back to solid back", e)
                back_path = None
        if back_path is not None and back_path.exists():
            state.cover.back_image_path = str(back_path.relative_to(book_dir))

    pages = state.config.type_config.target_pages
    dims = cover_dimensions(
        trim=state.config.type_config.trim_size,
        paper=state.config.type_config.paper_type,
        pages=pages,
    )
    composed = cover_dir / "wrap.png"
    spine_text = state.cover.spine_text or f"{state.concept.title} - {state.config.author}"
    compose_cover_wrap(
        front_png=front_path,
        back_png=back_path,
        title=state.concept.title,
        subtitle=state.concept.subtitle,
        author=state.config.author,
        spine_text=spine_text,
        palette=state.cover.palette,
        dimensions=dims,
        output_path=composed,
    )
    state.cover.composed_path = str(composed.relative_to(book_dir))
    state.mark_done("cover")
    return state
