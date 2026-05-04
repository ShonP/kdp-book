"""ReportLab interior PDF builder.

Children's picture books: full-page illustration on one side, large text
on facing page (or overlaid).
Light novel / fiction / non-fiction: text-first with optional chapter
art at the start of each chapter.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)

from kdp_book.formats.cover_geometry import trim_size_inches
from kdp_book.formats.typography import (
    interior_margins,
    typography_for,
)
from kdp_book.log import log
from kdp_book.models.book import BookType, IBookState


def build_interior_pdf(state: IBookState, output_path: Path) -> Path:
    """Render the book interior as a print-ready KDP PDF."""
    if state.manuscript is None or not state.manuscript.chapters:
        raise RuntimeError("Cannot render PDF: manuscript missing")
    if state.concept is None:
        raise RuntimeError("Cannot render PDF: concept missing")

    book_dir = Path(state.book_dir)
    type_config = state.config.type_config
    pages_estimate = max(type_config.target_pages, len(state.manuscript.chapters) * 4)
    margins = interior_margins(pages_estimate, full_bleed=type_config.full_bleed)
    typography = typography_for(type_config.trim_size.value if False else state.config.book_type.value)
    trim_w_in, trim_h_in = trim_size_inches(type_config.trim_size)

    page_w_pt = (trim_w_in + 2 * margins.bleed_in) * inch
    page_h_pt = (trim_h_in + 2 * margins.bleed_in) * inch

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(output_path),
        pagesize=(page_w_pt, page_h_pt),
        leftMargin=margins.outer_in * inch,
        rightMargin=margins.outer_in * inch,
        topMargin=margins.top_in * inch,
        bottomMargin=margins.bottom_in * inch,
        title=state.concept.title,
        author=state.config.author,
    )

    # Mirror margins: even pages get gutter on right, odd pages on left.
    inner_margin_pt = margins.gutter_in * inch
    outer_margin_pt = margins.outer_in * inch
    top_margin_pt = margins.top_in * inch
    bottom_margin_pt = margins.bottom_in * inch
    bleed_pt = margins.bleed_in * inch

    frame_w = page_w_pt - inner_margin_pt - outer_margin_pt
    frame_h = page_h_pt - top_margin_pt - bottom_margin_pt

    odd_frame = Frame(
        bleed_pt + inner_margin_pt, bleed_pt + bottom_margin_pt,
        frame_w, frame_h, id="odd",
    )
    even_frame = Frame(
        bleed_pt + outer_margin_pt, bleed_pt + bottom_margin_pt,
        frame_w, frame_h, id="even",
    )
    doc.addPageTemplates([
        PageTemplate(id="odd", frames=[odd_frame]),
        PageTemplate(id="even", frames=[even_frame]),
    ])

    accent = HexColor("#2a1b10") if state.bible and state.bible.style_guide.palette else black

    body_style = ParagraphStyle(
        "Body",
        fontName="Times-Roman",
        fontSize=typography.body_pt,
        leading=typography.leading_pt,
        firstLineIndent=typography.paragraph_indent_in * inch,
        spaceAfter=typography.paragraph_spacing_pt,
        textColor=black,
    )
    chapter_title_style = ParagraphStyle(
        "ChapterTitle",
        fontName="Times-Bold",
        fontSize=typography.chapter_title_pt,
        leading=typography.chapter_title_pt * 1.2,
        spaceBefore=typography.chapter_title_pt * 1.5,
        spaceAfter=typography.chapter_title_pt * 0.6,
        textColor=accent,
        alignment=1,  # centre
    )

    story: list = []

    # Title page
    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph(
        f"<para alignment='center'>{_escape(state.concept.title)}</para>",
        ParagraphStyle("TitleBig", fontName="Times-Bold", fontSize=typography.chapter_title_pt + 8,
                       leading=(typography.chapter_title_pt + 8) * 1.2, alignment=1, textColor=accent),
    ))
    if state.concept.subtitle:
        story.append(Spacer(1, 0.3 * inch))
        story.append(Paragraph(
            f"<para alignment='center'><i>{_escape(state.concept.subtitle)}</i></para>",
            ParagraphStyle("TitleSub", fontName="Times-Italic", fontSize=typography.chapter_subtitle_pt,
                           leading=typography.chapter_subtitle_pt * 1.3, alignment=1),
        ))
    story.append(Spacer(1, 1 * inch))
    story.append(Paragraph(
        f"<para alignment='center'>{_escape(state.config.author)}</para>",
        ParagraphStyle("TitleAuthor", fontName="Times-Roman", fontSize=typography.chapter_subtitle_pt,
                       leading=typography.chapter_subtitle_pt * 1.4, alignment=1),
    ))
    story.append(PageBreak())

    images_by_chapter = _index_images(state)
    is_picture_book = state.config.book_type == BookType.CHILDREN_PICTURE_BOOK

    for chapter in state.manuscript.chapters:
        story.append(Paragraph(_escape(chapter.title), chapter_title_style))
        story.append(Spacer(1, typography.body_pt * 1.2))

        # Chapter art (first image of the chapter, if any).
        # For picture books the prose is baked into the image, so we render
        # the illustration full-width and skip the separate prose block.
        if state.config.type_config.illustrations_per_chapter > 0:
            for img_path in images_by_chapter.get(chapter.index, [])[:1]:
                full = book_dir / img_path
                if full.exists():
                    title_h = chapter_title_style.leading + typography.body_pt * 1.2
                    avail_h = frame_h - title_h - typography.body_pt
                    target = frame_w * (1.0 if is_picture_book else 0.85)
                    fig_w = min(target, avail_h)
                    story.append(Image(str(full), width=fig_w, height=fig_w))
                    story.append(Spacer(1, typography.body_pt))

        if not is_picture_book:
            for para in (chapter.prose or "").split("\n\n"):
                text = para.strip()
                if not text:
                    continue
                story.append(Paragraph(_escape(text), body_style))
        story.append(PageBreak())

    doc.build(story)
    log.info("Interior PDF: %s (%d chapters)", output_path, len(state.manuscript.chapters))
    return output_path


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _index_images(state: IBookState) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for img in state.images:
        out.setdefault(img.chapter_index, []).append(img.image_path)
    return out
