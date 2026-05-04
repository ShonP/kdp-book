"""Language utilities — locks all user-facing natural-language output to a
target language while keeping JSON schema keys and image-composition fields
in English.

Why this exists
- The pipeline runs in English by default. When the user asks for a Hebrew
  book, every agent that generates *reader-facing* text (concept, outline,
  bible, writer, metadata, cover-typography) must produce Hebrew prose.
- The illustrator agent and per-page composition prompts stay in English
  because gpt-image-2 reasons about scene composition in English; only the
  *baked text* on a children's-picture-book page is rendered in the target
  language.
- The editor and quality agents are internal QA passes and continue to
  emit English notes for the engineer reading the report — they still read
  Hebrew prose just fine.

Usage
    from kdp_book.agents._language import language_directive
    instructions = SYSTEM_PROMPT + language_directive(state.config.language)
"""

from __future__ import annotations

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"name": "English", "direction": "ltr"},
    "he": {"name": "Hebrew", "direction": "rtl"},
}

CLI_LANGUAGE_TO_ISO: dict[str, str] = {
    "english": "en",
    "hebrew": "he",
}

CLI_LANGUAGE_CHOICES: list[str] = list(CLI_LANGUAGE_TO_ISO.keys())


def normalize_language(value: str | None) -> str:
    """Accept either an ISO code ('en', 'he') or a CLI label ('english',
    'hebrew') and return a canonical ISO code. Defaults to 'en'."""
    if not value:
        return "en"
    v = value.strip().lower()
    if v in LANGUAGES:
        return v
    return CLI_LANGUAGE_TO_ISO.get(v, "en")


def language_name(language: str) -> str:
    return LANGUAGES.get(normalize_language(language), LANGUAGES["en"])["name"]


def language_direction(language: str) -> str:
    return LANGUAGES.get(normalize_language(language), LANGUAGES["en"])["direction"]


def is_rtl(language: str) -> bool:
    return language_direction(language) == "rtl"


def language_directive(language: str) -> str:
    """Return a system-prompt addendum that locks all natural-language values
    to the requested language.

    Returns an empty string for English (the default) so we don't pollute
    prompts that don't need it.
    """
    iso = normalize_language(language)
    if iso == "en":
        return ""
    info = LANGUAGES[iso]
    name = info["name"]
    rtl_note = ""
    if info["direction"] == "rtl":
        rtl_note = (
            f"\n- {name} is read right-to-left. Write natural {name} prose; "
            f"do not embed left-to-right Latin punctuation patterns inside "
            f"sentences in a way that breaks RTL flow."
        )
    return (
        "\n\nLANGUAGE LOCK (HARD)\n"
        f"- Write ALL natural-language output in {name}. This includes "
        f"titles, subtitles, hooks, audience descriptions, tone phrases, "
        f"themes, chapter titles, chapter summaries, scene titles, scene "
        f"summaries, character names, character descriptions, voice "
        f"phrases, location names, location descriptions, prose, blurbs, "
        f"keywords, and any other free-text VALUES.\n"
        f"- JSON schema field NAMES and structural enum values stay in "
        f"English; only the VALUES are in {name}.\n"
        f"- Hex color codes, file paths, BISAC category codes, and ISO "
        f"language codes also stay in English/ASCII.\n"
        f"- Use authentic native {name} names for characters and "
        f"locations rather than transliterating English names — pick names "
        f"that feel natural to {name} readers.\n"
        f"- Do NOT mix English and {name} in the same value. Do NOT "
        f"transliterate; produce native {name} script.{rtl_note}"
    )


HEBREW_BAKED_TEXT_TYPOGRAPHY = (
    "FIXED HEBREW TYPOGRAPHY (must be IDENTICAL on every single page of the "
    "book — do not vary face, weight, size, color, or treatment): "
    "use a warm rounded children's-book HEBREW serif (e.g. Frank Ruehl-CLM "
    "style, or a friendly Hebrew face such as Heebo Bold / Rubik Bold / "
    "Assistant Bold with rounded contours). The face MUST natively render "
    "Hebrew letterforms (alef א, bet ב, gimel ג, ...), NOT Latin or "
    "pseudo-Hebrew shapes. "
    "Approx 18-20pt equivalent. "
    "Right-to-left text flow with correct Hebrew letter shaping. "
    "Niqqud (vowel points) optional but CONSISTENT: if you include them on "
    "one page, include them on every page; if you omit them, omit on every "
    "page. "
    "Color: deep brown ink (#3E2723). "
    "Background behind the text: a soft cream/parchment wash (#FFF8E7) just "
    "barely tinted, sized as a quiet rectangle large enough that NO letters "
    "are crowded by illustration elements. "
    "Treatment: NO outline, NO drop shadow, NO border, NO glow, NO stroke, "
    "NO 3D effect, NO bold/italic mixing. Plain dark-brown Hebrew letters "
    "on the soft cream panel. "
    "Use this exact same typography on every page so the typography reads "
    "as one consistent voice throughout the book."
)
