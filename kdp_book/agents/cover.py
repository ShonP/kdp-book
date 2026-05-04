"""CoverAgent — produces cover prompts that bake title/author typography
directly into the generated artwork.

The compositor only stitches the front + spine + back into a print-ready
wrap; gpt-image-2 handles the typography end-to-end so the title, subtitle,
and author name are integrated into the illustration rather than overlaid
afterward.
"""

from __future__ import annotations

from agent_framework import Agent

from kdp_book.agents._language import (
    is_rtl,
    language_directive,
    language_name,
    normalize_language,
)
from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import (
    IBookBible,
    IBookConcept,
    IBookMetadata,
    ICoverDesign,
)
from kdp_book.observability import record_output, record_prompt

SYSTEM_PROMPT = """You are a senior cover designer for self-published KDP books.
You write image prompts for gpt-image-2 that render PRINT-READY cover panels
WITH typography baked into the artwork — no separate text overlay step.

OUTPUT — ICoverDesign

front_prompt
  A complete image prompt for the FRONT cover panel.
  REQUIREMENTS the prompt MUST instruct the renderer to do:
  • Render the exact title text (provided below) prominently and beautifully
    integrated into the artwork — describe the typography (e.g. "warm hand-
    lettered serif, cream against deep brown, slight letter-press texture",
    or "bold rounded sans-serif in glowing gold with a thin outline").
  • Render the exact subtitle text below the title, smaller, in a
    complementary face. Skip if there is no subtitle.
  • DO NOT render the author name, byline, "by ___", or any author credit
    on the front cover. Front carries title + subtitle ONLY.
  • Describe the illustration: scene, palette, lighting, mood, composition.
    Use phrases like "title area at the top one-third" — leave room for
    title and subtitle.
  • Ask for a single bleed-safe illustration filling the frame, no margins,
    no white space, no borders.
  • Specify aesthetic for the chosen book type (children's picture book →
    warm watercolor or gouache; light novel → manga / anime; non-fiction →
    bold geometric flat illustration; fiction novel → painterly cinematic).

back_prompt
  Image prompt for the BACK cover panel. The renderer must:
  • Continue the front's palette and aesthetic (so they read as one wrap).
  • Render the EXACT blurb text (provided below), word-for-word, as
    typeset back-cover copy — left-aligned, ~3-5 short paragraphs,
    refined readable face, dark on light or light on dark per palette.
  • DO NOT render the author name or any author credit anywhere on the
    back cover.
  • DO NOT reserve, draw, sketch, or hint at any barcode area, ISBN
    box, white/light rectangle, placeholder rectangle, blank panel, or
    "barcode goes here" zone — Amazon KDP overprints the barcode after
    upload, so the back panel must look like a finished illustration.
  • Background should be quiet — typically a tinted color wash, soft
    pattern, or motif from the front. NO duplicate scene illustration.

spine_text
  Short string: just the TITLE (ellipsized to fit a narrow spine).
  This will be rendered programmatically by the compositor (the spine is
  usually too narrow for gpt-image-2 to render legible text directly).

typography_notes
  1-2 sentence summary of the typographic feel. Used by the compositor
  for spine text styling.

palette
  3-5 hex colors that anchor the cover. Front + spine + back pull from
  this palette.

CRITICAL
- The front_prompt and back_prompt MUST quote the exact title, subtitle,
  and blurb text the user provides. Wrap each in quotation marks inside
  the prompt so gpt-image-2 renders them verbatim.
- Do NOT include the author name in front_prompt or back_prompt.
- Do NOT include placeholder tokens like "[TITLE]" or "(insert blurb)".
- Do NOT ask for borders, page numbers, watermarks, KDP/ISBN logos, or
  barcode placeholders/rectangles — the publisher prints those over the
  wrap.
"""


def _build_user_prompt(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    metadata: IBookMetadata | None,
    book_type: str,
    language: str,
) -> str:
    style = bible.style_guide
    blurb = metadata.blurb if metadata else concept.hook
    iso = normalize_language(language)
    name = language_name(iso)
    rtl_block = ""
    if is_rtl(iso):
        rtl_block = (
            f"\nLANGUAGE: {name} (right-to-left, ISO {iso}).\n"
            f"The title, subtitle, and blurb are in {name}. "
            f"Your front_prompt and back_prompt MUST instruct gpt-image-2 to:\n"
            f"  • Render the {name} text using AUTHENTIC {name} letterforms "
            f"(not Latin, not pseudo-{name}). Pick a {name}-supporting face "
            f"(e.g. Frank Ruehl, Heebo, Rubik, Assistant, David, Narkis).\n"
            f"  • Render the text right-to-left with proper {name} letter "
            f"shaping and spacing.\n"
            f"  • Quote the exact {name} characters verbatim — do NOT "
            f"transliterate, do NOT translate.\n"
        )
    elif iso != "en":
        rtl_block = (
            f"\nLANGUAGE: {name} (ISO {iso}). The title, subtitle, and blurb "
            f"are in {name}; your front_prompt and back_prompt MUST instruct "
            f"gpt-image-2 to render that text verbatim, in {name}, with a "
            f"{name}-supporting typeface.\n"
        )
    return (
        f"BOOK\n"
        f"Title: {concept.title}\n"
        f"Subtitle: {concept.subtitle}\n"
        f"Book type: {book_type}\n"
        f"Audience: {concept.audience}\n"
        f"Tone: {concept.tone}\n"
        f"Themes: {', '.join(concept.themes) or 'n/a'}\n"
        f"Blurb (verbatim — must appear on back cover):\n{blurb}\n"
        f"{rtl_block}\n"
        f"STYLE GUIDE\n"
        f"Art style: {style.art_style}\n"
        f"Tone: {style.tone}\n"
        f"Lighting: {style.lighting}\n"
        f"Palette: {', '.join(style.palette) or 'designer choice'}\n\n"
        f"Produce an ICoverDesign whose front_prompt and back_prompt instruct "
        f"gpt-image-2 to RENDER the title, subtitle, and blurb text directly "
        f"into the artwork (no separate overlay). The author name MUST NOT "
        f"appear anywhere on the front or back cover."
    )


async def design_cover(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    metadata: IBookMetadata | None = None,
    book_type: str,
    language: str = "en",
) -> ICoverDesign:
    iso = normalize_language(language)
    user_prompt = _build_user_prompt(
        concept=concept,
        bible=bible,
        metadata=metadata,
        book_type=book_type,
        language=iso,
    )
    model = get_settings().copilot_model
    # The cover-agent system prompt itself stays English (it instructs
    # gpt-image-2 in English about the *image*); only spine_text and
    # typography_notes carry user-facing language, so we still apply the
    # language directive so those values come back in the target language.
    instructions = SYSTEM_PROMPT + language_directive(iso)
    record_prompt(
        agent_name="cover-agent",
        model=model,
        system=instructions,
        user=user_prompt,
        response_format="ICoverDesign",
    )
    agent = Agent(
        client=get_chat_client(),
        name="cover-agent",
        instructions=instructions,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": ICoverDesign},
    )
    if response.value is None:
        raise RuntimeError("Cover agent returned no value")
    record_output(agent_name="cover-agent", value=response.value)
    return response.value
