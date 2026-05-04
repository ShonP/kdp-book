"""Composition-only prompt builder — identity comes from refs, not text.

The cardinal rule (from mangas): never describe a character's face/hair/costume
in a per-page prompt. The reference images carry identity. Per-page prompts
describe ONLY composition: camera, pose, action, lighting, mood, location.

Children's picture books are a special case: the page text itself is BAKED
INTO the illustration by gpt-image-2 so the typography becomes part of the
artwork (warm rounded serif, cream/white, large and child-friendly). All
other book types use composition-only prompts and render text separately
in the PDF/EPUB.
"""

from __future__ import annotations

from kdp_book.models.book import (
    BookType,
    IBookConcept,
    IIllustrationBrief,
    IStyleGuide,
)

# Single source of truth for picture-book typography. Repeated verbatim in
# every page prompt so gpt-image-2 keeps the same face across all pages.
PICTURE_BOOK_TEXT_TYPOGRAPHY = (
    "warm hand-lettered rounded storybook serif, large and child-friendly, "
    "creamy off-white text with a soft dark outline for readability, "
    "letters slightly hand-painted to feel cozy. Use this exact same "
    "typography on every page of the book — same face, same color, same "
    "weight, same size, same gentle outline — so the typography reads as "
    "one consistent voice throughout."
)


def build_character_sheet_prompt(
    *,
    character_name: str,
    appearance: str,
    costume: str,
    palette: list[str],
    style: IStyleGuide,
) -> str:
    """Identity-locking sheet — used to generate the character's `default` reference.

    This is the ONLY prompt where we spell out facial features and costume.
    Subsequent page prompts will reference the rendered image instead.
    """
    palette_str = ", ".join(palette) if palette else "matched to story palette"
    return (
        f"Full-body character reference sheet for {character_name}. "
        f"Front view, neutral pose, plain off-white background, even soft lighting. "
        f"Style: {style.art_style}. Line weight: {style.line_weight or 'clean'}. "
        f"Lighting: soft and flat for reference (not dramatic). "
        f"Appearance: {appearance}. "
        f"Costume: {costume or 'simple appropriate outfit'}. "
        f"Palette: {palette_str}. "
        f"No text, no labels, no logos, no watermark, no border. "
        f"Single subject only — no other characters or props in frame."
    )


def build_scene_prompt(
    *,
    brief: IIllustrationBrief,
    style: IStyleGuide,
    concept: IBookConcept,
    book_type: BookType | None = None,
    page_text: str = "",
) -> str:
    """Composition-only page prompt. Identity is supplied via reference images.

    For `BookType.CHILDREN_PICTURE_BOOK`, when `page_text` is provided the
    prompt instructs gpt-image-2 to typeset the text directly into the
    illustration with the shared `PICTURE_BOOK_TEXT_TYPOGRAPHY` so every
    page reads as one cohesive book.
    """
    parts: list[str] = [
        f"Illustration in style: {style.art_style}.",
        f"Tone: {style.tone or concept.tone}.",
    ]
    if brief.location:
        parts.append(f"Setting: {brief.location}.")
    parts.append(f"Composition: {brief.composition}.")
    if brief.camera:
        parts.append(f"Camera: {brief.camera}.")
    if brief.pose:
        parts.append(f"Pose: {brief.pose}.")
    if brief.action:
        parts.append(f"Action: {brief.action}.")
    if brief.lighting or style.lighting:
        parts.append(f"Lighting: {brief.lighting or style.lighting}.")
    if brief.mood:
        parts.append(f"Mood: {brief.mood}.")
    if brief.characters_present:
        parts.append(
            "Characters in frame (identity locked by reference images, "
            "match exactly): " + ", ".join(brief.characters_present) + "."
        )

    cleaned = (page_text or "").strip()
    if book_type == BookType.CHILDREN_PICTURE_BOOK and cleaned:
        # Quote the text so gpt-image-2 renders it verbatim. Use single-line
        # form because picture-book prose is short (≤4 sentences/page).
        flat = " ".join(cleaned.split())
        parts.append(
            'PAGE TEXT TO RENDER: typeset the following text directly '
            'into the illustration, in a clear airy area near the bottom '
            'of the page (or top if the bottom is busy). Render it '
            f'verbatim, exactly: "{flat}". '
            f'Typography: {PICTURE_BOOK_TEXT_TYPOGRAPHY} '
            'Leave generous breathing room around the text — do not crowd '
            'it with illustration elements; treat the text panel as a '
            'soft watercolor wash if the background is busy.'
        )
        parts.append(
            "Do NOT add any other text, captions, speech bubbles, page "
            "numbers, watermarks, or borders — only the page text above. "
            "Single illustration filling the frame."
        )
    else:
        parts.append(
            "Do NOT add text, captions, speech bubbles, watermarks, page numbers, "
            "or borders. Single illustration filling the frame."
        )
    return " ".join(parts)
