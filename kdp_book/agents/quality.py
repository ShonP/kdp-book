"""QualityAgent — final review pass, returns IQualityReport with blockers."""

from __future__ import annotations

from agent_framework import Agent

from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import (
    IBookBible,
    IBookConcept,
    IBookMetadata,
    IManuscript,
    IQualityReport,
)
from kdp_book.observability import record_output, record_prompt

SYSTEM_PROMPT = """You are a senior production editor doing the FINAL quality
gate before a KDP submission. You receive the concept, bible, manuscript,
and KDP metadata. Decide whether the book is ready to publish.

Score 1-10:
- 10: ship now
- 8-9: ship after copy edit
- 6-7: ship-ready but with caveats
- 4-5: significant problems — list blockers
- 1-3: do not ship

Specifically check for:
- KDP CONTENT POLICY: no offensive content, no copyrighted characters,
  no real public figures presented as fictional, no medical/legal/
  financial advice masquerading as fact in non-fiction.
- METADATA COMPLIANCE: title length, blurb length (under 4000 chars
  for KDP), 7 keywords, no duplicate keywords, no keywords reusing
  title words, BISAC categories present.
- BIBLE CONSISTENCY: characters introduced; arcs resolved; no contradictions
  with the bible.
- MANUSCRIPT QUALITY: no obvious AI-tells, no markdown leftovers, no
  lorem-ipsum, no placeholder text.
- AGE APPROPRIATENESS: matches the declared audience.

Populate `blockers` with anything that MUST be fixed before submission.
Populate `concerns` with non-blocking issues that should still be addressed.

Return ONLY structured JSON matching IQualityReport."""


def _build_user_prompt(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    manuscript: IManuscript,
    metadata: IBookMetadata | None,
) -> str:
    chapter_lines = [
        f"- Ch {ch.index} '{ch.title}': {ch.word_count} words"
        for ch in manuscript.chapters
    ]
    md_block = "(no metadata generated)"
    if metadata:
        md_block = (
            f"Title: {metadata.title}\n"
            f"Subtitle: {metadata.subtitle}\n"
            f"Author: {metadata.author}\n"
            f"Blurb ({len(metadata.blurb)} chars):\n{metadata.blurb}\n"
            f"Keywords ({len(metadata.keywords)}): {', '.join(metadata.keywords)}\n"
            f"BISAC: {', '.join(metadata.bisac_categories)}\n"
            f"Age range: {metadata.age_range}\n"
            f"AI disclosure: {metadata.ai_disclosure}"
        )
    excerpts = []
    for ch in manuscript.chapters[:3]:
        excerpts.append(f"--- Ch {ch.index} ({ch.title}) opening ---\n{ch.prose[:600]}")

    return (
        f"CONCEPT\n"
        f"Title: {concept.title}\n"
        f"Audience: {concept.audience}\n"
        f"Tone: {concept.tone}\n\n"
        f"BIBLE\n"
        f"{len(bible.characters)} characters, {len(bible.locations)} locations.\n\n"
        f"MANUSCRIPT\n"
        f"Total: {manuscript.total_word_count} words across "
        f"{len(manuscript.chapters)} chapters.\n"
        + "\n".join(chapter_lines) + "\n\n"
        "FIRST CHAPTER EXCERPTS\n" + "\n\n".join(excerpts) + "\n\n"
        f"KDP METADATA\n{md_block}\n\n"
        f"Return an IQualityReport now."
    )


async def quality_review(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    manuscript: IManuscript,
    metadata: IBookMetadata | None = None,
) -> IQualityReport:
    user_prompt = _build_user_prompt(
        concept=concept, bible=bible, manuscript=manuscript, metadata=metadata,
    )
    model = get_settings().copilot_model
    record_prompt(
        agent_name="quality-agent",
        model=model,
        system=SYSTEM_PROMPT,
        user=user_prompt,
        response_format="IQualityReport",
    )
    agent = Agent(
        client=get_chat_client(),
        name="quality-agent",
        instructions=SYSTEM_PROMPT,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": IQualityReport},
    )
    if response.value is None:
        raise RuntimeError("Quality agent returned no value")
    record_output(agent_name="quality-agent", value=response.value)
    return response.value
