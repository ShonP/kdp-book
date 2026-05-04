"""CoverAgent — designs front, spine, and back cover prompts + typography."""

from __future__ import annotations

from agent_framework import Agent

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
Given a book concept, the bible (style guide), and a metadata blurb,
produce a structured ICoverDesign.

DESIGN RULES
- front_prompt: a complete image prompt for the FRONT cover only.
  Composition-only — no character face descriptions (a separate render
  step references locked character images). Describe scene, palette,
  lighting, mood. Single illustration filling the frame. NO TEXT in
  the rendered image — title and author will be overlaid in code.
- back_prompt: optional. Either an extension of the front (full wrap art)
  or empty if the cover should be solid color on the back. If provided,
  it should compose with the front.
- spine_text: short text — usually "TITLE — AUTHOR".
- typography_notes: 1-2 sentences on font feel ("warm rounded serif,
  high-contrast title in cream against deep brown").
- palette: 3-5 hex colors that anchor the cover (front/back/spine all
  pull from this).

NEVER include text, captions, watermarks, page numbers, or borders in
the rendered image — typography is overlaid by the compositor."""


def _build_user_prompt(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    metadata: IBookMetadata | None,
) -> str:
    style = bible.style_guide
    blurb = metadata.blurb if metadata else concept.hook
    return (
        f"BOOK\n"
        f"Title: {concept.title}\n"
        f"Subtitle: {concept.subtitle}\n"
        f"Audience: {concept.audience}\n"
        f"Tone: {concept.tone}\n"
        f"Themes: {', '.join(concept.themes) or 'n/a'}\n"
        f"Blurb: {blurb}\n\n"
        f"STYLE GUIDE\n"
        f"Art style: {style.art_style}\n"
        f"Tone: {style.tone}\n"
        f"Lighting: {style.lighting}\n"
        f"Palette: {', '.join(style.palette) or 'designer choice'}\n\n"
        f"Produce an ICoverDesign now."
    )


async def design_cover(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    metadata: IBookMetadata | None = None,
) -> ICoverDesign:
    user_prompt = _build_user_prompt(concept=concept, bible=bible, metadata=metadata)
    model = get_settings().copilot_model
    record_prompt(
        agent_name="cover-agent",
        model=model,
        system=SYSTEM_PROMPT,
        user=user_prompt,
        response_format="ICoverDesign",
    )
    agent = Agent(
        client=get_chat_client(),
        name="cover-agent",
        instructions=SYSTEM_PROMPT,
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
