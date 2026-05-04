"""Composition-only prompt builder — identity comes from refs, not text.

The cardinal rule (from mangas): never describe a character's face/hair/costume
in a per-page prompt. The reference images carry identity. Per-page prompts
describe ONLY composition: camera, pose, action, lighting, mood, location.

Children's picture books are a special case: the page text itself is BAKED
INTO the illustration by gpt-image-2 so the typography becomes part of the
artwork (warm rounded serif, cream/white, large and child-friendly). All
other book types use composition-only prompts and render text separately
in the PDF/EPUB.

Composition language (camera, pose, lighting, etc.) stays in English even
for non-English books — gpt-image-2 reasons about scenes in English. The
only field that switches to the target language is the verbatim page text
that gets typeset into the illustration for picture books.
"""

from __future__ import annotations

from kdp_book.agents._language import (
    HEBREW_BAKED_TEXT_TYPOGRAPHY,
    is_rtl,
    language_name,
    normalize_language,
)
from kdp_book.models.book import (
    BookType,
    IBookConcept,
    IIllustrationBrief,
    IStyleGuide,
)

# Single source of truth for picture-book typography. Repeated verbatim in
# every page prompt so gpt-image-2 keeps the same face across all pages.
PICTURE_BOOK_TEXT_TYPOGRAPHY = (
    "FIXED TYPOGRAPHY (must be IDENTICAL on every single page of the book — "
    "do not vary face, weight, size, color, or treatment): "
    "rounded storybook serif (a slightly bold, friendly children's serif "
    "such as Mrs Eaves Bold or a similar warm rounded serif). "
    "Approx 18-20pt equivalent. "
    "Letter-spacing: relaxed. Letter-shape: hand-painted but consistent — "
    "NOT calligraphy, NOT brush script, NOT sans-serif. "
    "Color: deep brown ink (#3E2723). "
    "Background behind the text: a soft cream/parchment wash (#FFF8E7) just "
    "barely tinted, sized as a quiet rectangle large enough that NO letters "
    "are crowded by illustration elements. "
    "Treatment: NO outline, NO drop shadow, NO border, NO glow, NO stroke, "
    "NO 3D effect, NO bold/italic mixing. Plain dark-brown letters on the "
    "soft cream panel. "
    "Use this exact same typography on every page so the typography reads "
    "as one consistent voice throughout the book."
)


def _typography_block(language: str) -> str:
    """Return the typography lock for the requested language."""
    iso = normalize_language(language)
    if iso == "he":
        return HEBREW_BAKED_TEXT_TYPOGRAPHY
    return PICTURE_BOOK_TEXT_TYPOGRAPHY


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
    language: str = "en",
) -> str:
    """Composition-only page prompt. Identity is supplied via reference images.

    For `BookType.CHILDREN_PICTURE_BOOK`, when `page_text` is provided the
    prompt instructs gpt-image-2 to typeset the text directly into the
    illustration with the language-appropriate typography lock so every
    page reads as one cohesive book.
    """
    iso = normalize_language(language)
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
        lang_label = language_name(iso)
        rtl_clause = ""
        if is_rtl(iso):
            rtl_clause = (
                f" The text is in {lang_label} and is written RIGHT-TO-LEFT. "
                f"Render the {lang_label} characters with proper {lang_label} "
                f"letterforms (real Hebrew/Arabic-script glyphs — NOT Latin, "
                f"NOT pseudo-script). Maintain right-to-left reading order. "
                f"Quote the text VERBATIM — do not translate, do not "
                f"transliterate, do not paraphrase."
            )
        elif iso != "en":
            rtl_clause = (
                f" The text is in {lang_label}. Render the {lang_label} "
                f"characters verbatim with proper {lang_label} letterforms. "
                f"Do not translate or transliterate."
            )
        parts.append(
            f'PAGE TEXT TO RENDER ({lang_label}): typeset the following text '
            f'directly into the illustration, in a clear airy area near the '
            f'bottom of the page (or top if the bottom is busy). Render it '
            f'verbatim, exactly: "{flat}".{rtl_clause} '
            f'Typography: {_typography_block(iso)} '
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
