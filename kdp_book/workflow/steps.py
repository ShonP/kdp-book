"""Step helpers — pure functions that take `IBookState` and return the next.

Each helper wraps exactly one agent invocation. The workflow file
(`pipeline.py`) decorates these with `@step` for checkpointing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from kdp_book.agents.bible import generate_bible
from kdp_book.agents.concept import generate_concept
from kdp_book.agents.editor import edit_manuscript
from kdp_book.agents.illustrator import plan_chapter_illustrations
from kdp_book.agents.outline import generate_outline
from kdp_book.agents.writer import write_chapter
from kdp_book.config import get_settings
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
    character_image_path,
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
        language=state.config.language,
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
        language=state.config.language,
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
        language=state.config.language,
    )
    log.info(
        "Bible: %d characters, %d locations",
        len(state.bible.characters),
        len(state.bible.locations),
    )
    state.mark_done("bible")
    return state


async def do_write(state: IBookState) -> IBookState:
    """Draft every chapter in parallel. Idempotent per-chapter via
    `written_chapter_indices`. Concurrency capped by
    `settings.chapter_writer_workers` so we don't melt rate limits.

    Continuity hint: the writer receives the previous chapter's outline
    summary instead of the previous chapter's prose tail (so chapters can
    be drafted independently in any order). This is a deliberate trade-off
    — for novels you may want sequential drafting; toggle by setting
    `chapter_writer_workers=1` in `.env`.
    """
    if state.concept is None or state.outline is None or state.bible is None:
        raise RuntimeError("Cannot write before concept + outline + bible")

    chapters_dir = Path(state.book_dir) / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    if state.manuscript is None:
        state.manuscript = IManuscript()

    drafted_by_index: dict[int, IChapterDraft] = {
        d.index: d for d in state.manuscript.chapters
    }

    # Build the to-do list, snapshotting the previous-chapter outline
    # summary as a continuity hint for the writer (cheaper than waiting
    # for prose to be drafted sequentially).
    chapters_by_index = {c.index: c for c in state.outline.chapters}
    pending = [
        c for c in state.outline.chapters
        if not (c.index in state.written_chapter_indices and c.index in drafted_by_index)
    ]
    if not pending:
        log.debug("All chapters already drafted")
        state.mark_done("write")
        return state

    workers = max(1, get_settings().chapter_writer_workers)
    log.info(
        "Writing %d chapters with %d worker(s)",
        len(pending), workers,
    )
    sem = asyncio.Semaphore(workers)

    async def draft_one(chapter):
        async with sem:
            previous_summary = ""
            if chapter.index > 1:
                prev = chapters_by_index.get(chapter.index - 1)
                if prev:
                    previous_summary = f"Previous chapter ({prev.title}): {prev.summary}"
            log.info(
                "Writing chapter %d/%d: %s",
                chapter.index, len(state.outline.chapters), chapter.title,
            )
            draft = await write_chapter(
                concept=state.concept,
                bible=state.bible,
                outline=state.outline,
                chapter=chapter,
                type_config=state.config.type_config,
                previous_tail=previous_summary,
                language=state.config.language,
            )
            path = chapters_dir / f"chapter-{chapter.index:02d}.md"
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                f"# {draft.title}\n\n{draft.prose.strip()}\n",
                encoding="utf-8",
            )
            tmp.replace(path)
            log.info("Chapter %d done (%d words)", chapter.index, draft.word_count)
            return draft

    drafts = await asyncio.gather(*(draft_one(c) for c in pending))
    for draft in drafts:
        drafted_by_index[draft.index] = draft
        if draft.index not in state.written_chapter_indices:
            state.written_chapter_indices.append(draft.index)

    state.manuscript.chapters = sorted(drafted_by_index.values(), key=lambda c: c.index)
    state.manuscript.total_word_count = sum(c.word_count for c in state.manuscript.chapters)
    save_state(state)

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
            language=state.config.language,
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

    workers = max(1, get_settings().chapter_writer_workers)
    log.info(
        "Planning illustrations for %d chapter(s) with %d worker(s)",
        len(state.outline.chapters), workers,
    )
    sem = asyncio.Semaphore(workers)

    async def plan_one(chapter):
        async with sem:
            log.info("Planning illustrations for chapter %d", chapter.index)
            return await plan_chapter_illustrations(
                concept=state.concept,
                bible=state.bible,
                chapter=chapter,
                illustrations_per_chapter=illustrations_per_chapter,
            )

    chapter_briefs = await asyncio.gather(*(plan_one(c) for c in state.outline.chapters))
    all_briefs: list[IIllustrationBrief] = []
    for briefs in chapter_briefs:
        all_briefs.extend(briefs)
    state.illustrations = all_briefs
    save_state(state)

    log.info("Planned %d illustrations across %d chapters", len(all_briefs), len(state.outline.chapters))
    state.mark_done("illustrate")
    return state


async def do_characters(state: IBookState) -> IBookState:
    """Render the `default` reference sheet for every character in parallel.

    Each character lives in its own directory:
        assets/<character-name>/default.png
        assets/<character-name>/default.png.json
        assets/<character-name>/variants/<variant>.png
    """
    if state.bible is None:
        raise RuntimeError("Cannot render characters before bible")
    if "characters" in state.completed_steps:
        log.debug("Characters already rendered, skipping")
        return state

    book_dir = Path(state.book_dir)
    settings = get_settings()
    rating = _content_rating(state)
    quality = settings.image_quality
    size = state.config.type_config.image_size

    pending = []
    for character in state.bible.characters:
        existing = get_character_ref(book_dir, character.name, "default")
        if existing is not None:
            log.debug("Character %s/default already rendered, skipping", character.name)
            continue
        pending.append(character)

    if not pending:
        state.mark_done("characters")
        return state

    workers = max(1, settings.image_generation_workers)
    log.info(
        "Rendering %d character sheet(s) with %d worker(s) at %s/%s",
        len(pending), workers, size, quality,
    )
    sem = asyncio.Semaphore(workers)

    async def render_one(character):
        prompt = build_character_sheet_prompt(
            character_name=character.name,
            appearance=character.appearance,
            costume=character.costume,
            palette=character.palette,
            style=state.bible.style_guide,
        )
        log.info("Rendering character sheet: %s", character.name)
        async with sem:
            try:
                img_bytes, retry_meta = await asyncio.to_thread(
                    render_with_retry,
                    prompt=prompt,
                    references=None,
                    size=size,
                    quality=quality,
                    content_rating=rating,
                )
            except RuntimeError as e:
                log.exception("Failed to render character %s: %s", character.name, e)
                return None

        out_path = character_image_path(book_dir, character.name, "default")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(out_path, img_bytes)

        rec = make_image_record(
            asset_type="character_sheet",
            name=f"{character.name}/default",
            path=str(out_path),
            prompt=prompt,
            references=[],
            model="gpt-image-2",
            size=size,
            quality=quality,
            retry_count=len(retry_meta.get("retries", [])),
            safety_filter_hits=retry_meta.get("safety_filter_hits", 0),
            duration_seconds=retry_meta.get("duration_seconds", 0.0),
        )
        write_image_sidecar(out_path, rec)
        return character.name, out_path, rec

    results = await asyncio.gather(*(render_one(c) for c in pending))

    # Apply results sequentially to keep manifest writes safe.
    for result in results:
        if result is None:
            continue
        name, out_path, rec = result
        record_image(rec)
        rel_path = str(out_path.relative_to(book_dir))
        entry = IAssetEntry(
            path=rel_path,
            generated_at=datetime.now(UTC),
            variant="default",
            chain_from="",
            stale=False,
        )
        add_character_variant(book_dir, name=name, variant="default", entry=entry)

    state.mark_done("characters")
    return state


async def do_images(state: IBookState) -> IBookState:
    """Render every page image in parallel, attaching the right character refs."""
    if not state.illustrations or state.bible is None or state.concept is None:
        log.info("No illustrations planned; skipping page renders")
        state.mark_done("images")
        return state
    if "images" in state.completed_steps and len(state.images) >= len(state.illustrations):
        log.debug("All page images already rendered, skipping")
        return state

    book_dir = Path(state.book_dir)
    images_dir = book_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    rendered_keys = {(img.chapter_index, img.scene_index) for img in state.images}
    page_counter = max([img.page_index for img in state.images], default=0)

    settings = get_settings()
    rating = _content_rating(state)
    quality = settings.image_quality
    size = state.config.type_config.image_size

    chapter_prose_by_index = {
        c.index: (c.prose or "") for c in (state.manuscript.chapters if state.manuscript else [])
    }

    pending: list[tuple[int, IIllustrationBrief, list[Path]]] = []
    for brief in state.illustrations:
        key = (brief.chapter_index, brief.scene_index)
        if key in rendered_keys:
            continue
        page_counter += 1
        brief.page_index = page_counter
        refs: list[Path] = []
        for char_name in brief.characters_present:
            ref = get_character_ref(book_dir, char_name, "default")
            if ref is not None:
                refs.append(ref)
            else:
                log.warning(
                    "No reference image for %s; proceeding text-only",
                    char_name,
                )
        pending.append((page_counter, brief, refs))

    if not pending:
        state.mark_done("images")
        return state

    workers = max(1, settings.image_generation_workers)
    log.info(
        "Rendering %d page illustration(s) with %d worker(s) at %s/%s",
        len(pending), workers, size, quality,
    )
    sem = asyncio.Semaphore(workers)

    async def render_one(page_index: int, brief: IIllustrationBrief, refs: list[Path]):
        page_text = chapter_prose_by_index.get(brief.chapter_index, "")
        prompt = build_scene_prompt(
            brief=brief,
            style=state.bible.style_guide,
            concept=state.concept,
            book_type=state.config.book_type,
            page_text=page_text,
            language=state.config.language,
        )
        log.info(
            "Rendering page %d (chapter %d, scene %d) with %d refs",
            page_index, brief.chapter_index, brief.scene_index, len(refs),
        )
        async with sem:
            try:
                img_bytes, retry_meta = await asyncio.to_thread(
                    render_with_retry,
                    prompt=prompt,
                    references=refs or None,
                    size=size,
                    quality=quality,
                    content_rating=rating,
                )
            except RuntimeError as e:
                log.exception(
                    "Failed to render page %d (ch%d sc%d): %s",
                    page_index, brief.chapter_index, brief.scene_index, e,
                )
                return None

        rel_path = f"images/page-{page_index:03d}.png"
        out_path = book_dir / rel_path
        atomic_write_bytes(out_path, img_bytes)
        rec = make_image_record(
            asset_type="page",
            name=f"ch{brief.chapter_index:02d}-sc{brief.scene_index:02d}",
            path=str(out_path),
            prompt=prompt,
            references=refs,
            model="gpt-image-2",
            size=size,
            quality=quality,
            retry_count=len(retry_meta.get("retries", [])),
            safety_filter_hits=retry_meta.get("safety_filter_hits", 0),
            duration_seconds=retry_meta.get("duration_seconds", 0.0),
        )
        sidecar = write_image_sidecar(out_path, rec)
        return page_index, brief, refs, rel_path, rec, sidecar, prompt

    results = await asyncio.gather(*(render_one(*p) for p in pending))

    for result in results:
        if result is None:
            continue
        page_index, brief, refs, rel_path, rec, sidecar, prompt = result
        record_image(rec)
        page_image = IPageImage(
            chapter_index=brief.chapter_index,
            scene_index=brief.scene_index,
            page_index=page_index,
            image_path=rel_path,
            sidecar_path=str(sidecar.relative_to(book_dir)),
        )
        state.images.append(page_image)
        add_page_image(
            book_dir,
            IImageAsset(
                page_index=page_index,
                path=rel_path,
                prompt=prompt,
                refs=[str(r.relative_to(book_dir)) for r in refs],
                generated_at=datetime.now(UTC),
            ),
        )
    state.images.sort(key=lambda im: im.page_index)
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


MAX_COVER_CHARACTER_REFS = 3


def _resolve_cover_character_refs(
    book_dir: Path, state: IBookState,
) -> list[Path]:
    """Resolve `default.png` reference paths for characters featured on the cover.

    Priority:
      1. Names listed in `state.cover.characters_on_cover` (CoverAgent's pick).
      2. Fallback to the bible's first character (typically the protagonist)
         when the agent didn't choose any. Cover always benefits from at least
         one character ref so faces line up with the interior illustrations.
    Caps at `MAX_COVER_CHARACTER_REFS` to keep the gpt-image-2 edit payload
    small and conditioning focused.
    """
    if state.cover is None:
        return []
    chosen: list[str] = []
    for n in state.cover.characters_on_cover:
        if n and n not in chosen:
            chosen.append(n)
    if not chosen and state.bible and state.bible.characters:
        chosen = [state.bible.characters[0].name]
    refs: list[Path] = []
    for name in chosen[:MAX_COVER_CHARACTER_REFS]:
        ref = get_character_ref(book_dir, name, "default")
        if ref is not None:
            refs.append(ref)
        else:
            log.warning("No character reference for %r; cover will skip it", name)
    return refs


async def do_cover(state: IBookState) -> IBookState:
    """Design + render front (and optional back) cover, then compose the wrap.

    Front and back are rendered in parallel.
    """
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
            book_type=state.config.book_type.value,
            language=state.config.language,
        )

    settings = get_settings()
    rating = _content_rating(state)
    quality = settings.image_quality
    size = state.config.type_config.image_size

    front_path = cover_dir / "front.png"
    back_path: Path | None = cover_dir / "back.png" if state.cover.back_prompt else None

    front_refs = _resolve_cover_character_refs(book_dir, state)
    if front_refs:
        log.info(
            "Cover: conditioning front on %d character ref(s): %s",
            len(front_refs), ", ".join(p.name for p in front_refs),
        )
    else:
        log.info("Cover: no character refs resolved; rendering front prompt-only")

    async def render_panel(
        panel: str,
        dest: Path,
        prompt: str,
        asset_type: str,
        refs: list[Path],
    ):
        if dest.exists():
            return dest, None, None
        log.info("Rendering %s cover (%d refs)", panel, len(refs))
        try:
            img_bytes, retry_meta = await asyncio.to_thread(
                render_with_retry,
                prompt=prompt,
                references=refs or None,
                size=size,
                quality=quality,
                content_rating=rating,
            )
        except RuntimeError as e:
            log.exception("%s cover render failed: %s", panel.title(), e)
            return None, None, None
        atomic_write_bytes(dest, img_bytes)
        rec = make_image_record(
            asset_type=asset_type,
            name=f"cover/{panel}",
            path=str(dest),
            prompt=prompt,
            references=refs,
            model="gpt-image-2",
            size=size,
            quality=quality,
            retry_count=len(retry_meta.get("retries", [])),
            safety_filter_hits=retry_meta.get("safety_filter_hits", 0),
            duration_seconds=retry_meta.get("duration_seconds", 0.0),
        )
        write_image_sidecar(dest, rec)
        return dest, rec, prompt

    tasks = [
        render_panel(
            "front", front_path, state.cover.front_prompt, "cover_front", front_refs,
        )
    ]
    if back_path is not None:
        tasks.append(
            render_panel(
                "back", back_path, state.cover.back_prompt, "cover_back", [],
            )
        )

    results = await asyncio.gather(*tasks)

    front_result = results[0]
    if front_result[0] is None:
        raise RuntimeError("Front cover failed to render")
    if front_result[1] is not None:
        record_image(front_result[1])
    state.cover.front_image_path = str(front_path.relative_to(book_dir))

    if len(results) > 1:
        back_result = results[1]
        if back_result[0] is not None and back_path is not None and back_path.exists():
            if back_result[1] is not None:
                record_image(back_result[1])
            state.cover.back_image_path = str(back_path.relative_to(book_dir))
        else:
            back_path = None

    pages = state.config.type_config.target_pages
    dims = cover_dimensions(
        trim=state.config.type_config.trim_size,
        paper=state.config.type_config.paper_type,
        pages=pages,
    )
    composed = cover_dir / "wrap.png"
    spine_text = state.cover.spine_text or f"{state.concept.title} — {state.config.author}"
    compose_cover_wrap(
        front_png=front_path,
        back_png=back_path,
        spine_text=spine_text,
        palette=state.cover.palette,
        dimensions=dims,
        output_path=composed,
    )
    state.cover.composed_path = str(composed.relative_to(book_dir))
    state.mark_done("cover")
    return state


async def do_format(state: IBookState, *, output: str = "both") -> IBookState:
    """Render PDF + EPUB + cover.pdf into books/<slug>/output/.

    Always emits both full-quality and compressed counterparts:

      * `interior.pdf`            +  `interior-compressed.pdf`
      * `cover.pdf`               +  `cover-compressed.pdf`
      * `<slug>.epub`             +  `<slug>-compressed.epub`

    Compressed variants downscale embedded images to ≤1200px on the long
    edge and re-encode them as JPEG q=85, typically dropping a 150-200MB
    interior to ~20-30MB without visibly affecting on-screen reading.
    """
    import shutil

    from kdp_book.formats.compress import (
        compress_cover_pdf,
        compress_epub,
        compress_interior_pdf,
    )
    from kdp_book.formats.epub_builder import build_epub
    from kdp_book.formats.pdf_interior import build_interior_pdf

    if state.manuscript is None or not state.manuscript.chapters:
        raise RuntimeError("Cannot format before write")
    if "format" in state.completed_steps:
        log.debug("Format already done, skipping")
        return state

    book_dir = Path(state.book_dir)
    out_dir = book_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    if output in ("pdf", "both"):
        interior_pdf = out_dir / "interior.pdf"
        build_interior_pdf(state, interior_pdf)
        compress_interior_pdf(state, out_dir / "interior-compressed.pdf")
        _log_size_pair(interior_pdf, out_dir / "interior-compressed.pdf")
    if output in ("epub", "both"):
        epub_path = out_dir / f"{state.slug}.epub"
        build_epub(state, epub_path)
        compress_epub(state, out_dir / f"{state.slug}-compressed.epub")
        _log_size_pair(epub_path, out_dir / f"{state.slug}-compressed.epub")

    # Expose the print-ready cover wrap at output/cover.pdf alongside the interior.
    cover_pdf_src = book_dir / "cover" / "wrap.pdf"
    cover_png_src = book_dir / "cover" / "wrap.png"
    if cover_pdf_src.exists():
        shutil.copy2(cover_pdf_src, out_dir / "cover.pdf")
        if cover_png_src.exists():
            compress_cover_pdf(cover_png_src, out_dir / "cover-compressed.pdf")
            _log_size_pair(out_dir / "cover.pdf", out_dir / "cover-compressed.pdf")
        else:
            log.warning("cover/wrap.png not found; skipping cover-compressed.pdf")
    else:
        log.warning("cover/wrap.pdf not found; output/cover.pdf will be missing")

    state.mark_done("format")
    return state


def _log_size_pair(full: Path, compressed: Path) -> None:
    """Emit a single info line comparing full vs compressed file size."""
    if not full.exists() or not compressed.exists():
        return
    full_mb = full.stat().st_size / (1024 * 1024)
    comp_mb = compressed.stat().st_size / (1024 * 1024)
    if full_mb <= 0:
        return
    ratio = comp_mb / full_mb
    log.info(
        "Compression: %s %.1fMB → %s %.1fMB (%.0f%% of original)",
        full.name, full_mb, compressed.name, comp_mb, ratio * 100,
    )


async def do_metadata(state: IBookState) -> IBookState:
    from kdp_book.agents.metadata import generate_metadata

    if state.concept is None or state.bible is None:
        raise RuntimeError("Cannot generate metadata before concept + bible")
    if state.metadata is not None and "metadata" in state.completed_steps:
        log.debug("Metadata already done, skipping")
        return state

    md = await generate_metadata(
        concept=state.concept,
        bible=state.bible,
        author=state.config.author,
        language=state.config.language,
    )
    state.metadata = md
    state.mark_done("metadata")
    return state


async def do_quality(state: IBookState) -> IBookState:
    from kdp_book.agents.quality import quality_review

    if state.concept is None or state.bible is None or state.manuscript is None:
        raise RuntimeError("Cannot run quality before concept+bible+manuscript")

    report = await quality_review(
        concept=state.concept,
        bible=state.bible,
        manuscript=state.manuscript,
        metadata=state.metadata,
    )
    state.quality_report = report
    state.mark_done("quality")
    return state
