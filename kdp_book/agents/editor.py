"""EditorAgent — reviews the assembled manuscript and produces an editor report."""

from __future__ import annotations

from agent_framework import Agent

from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import (
    IBookBible,
    IBookConcept,
    IBookOutline,
    IEditorReport,
    IManuscript,
)
from kdp_book.observability import record_output, record_prompt

SYSTEM_PROMPT = """You are a senior book editor reviewing a complete manuscript before
publication. Your job is to flag concrete, actionable problems — not to
restate or reword.

Score the manuscript on a 1-10 scale where:
- 10: ready to publish as-is
- 8-9: minor copy edits only
- 7: solid; small targeted polish on a few chapters
- 5-6: real structural issues — flag chapters_to_revise
- 1-4: needs deep rework

For every issue you raise, provide:
- chapter_index (when applicable)
- severity: blocker | important | minor
- category: consistency | voice | pacing | grammar | plot | factual
- note: one short, actionable line (≤ 120 chars)

Look specifically for:
- Bible drift: character traits, costume, or location details that
  contradict the bible.
- Voice drift: chapters where the narrator's tone shifts away from the
  declared tone.
- Pacing: scenes that are too long, too short, or skip beats.
- Plot holes: setups without payoff, payoffs without setup.
- Continuity: timeline errors, prop disappearances.
- Audience fit: vocabulary or content inappropriate for the declared
  audience (especially children's books).

If score < 7, populate `chapters_to_revise` with the chapter indices
that the writer must redraft. Otherwise leave it empty.

Return ONLY structured JSON matching IEditorReport. Do not write prose."""


def _build_user_prompt(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    outline: IBookOutline,
    manuscript: IManuscript,
) -> str:
    chapter_blocks = []
    for ch in manuscript.chapters:
        outline_ch = next((o for o in outline.chapters if o.index == ch.index), None)
        outline_summary = outline_ch.summary if outline_ch else "(no outline)"
        chapter_blocks.append(
            f"\n=== Chapter {ch.index}: {ch.title} ===\n"
            f"OUTLINE INTENT: {outline_summary}\n"
            f"WORD COUNT: {ch.word_count}\n"
            f"PROSE:\n{ch.prose}\n"
        )
    char_lines = [
        f"- {c.name} ({c.role}): {c.appearance}; voice: {c.voice or 'n/a'}"
        for c in bible.characters
    ]
    return (
        f"BOOK\n"
        f"Title: {concept.title}\n"
        f"Audience: {concept.audience}\n"
        f"Tone: {concept.tone}\n"
        f"Themes: {', '.join(concept.themes) or 'n/a'}\n\n"
        f"BIBLE\n" + "\n".join(char_lines) + "\n\n"
        f"MANUSCRIPT (total {manuscript.total_word_count} words, "
        f"{len(manuscript.chapters)} chapters)\n"
        + "".join(chapter_blocks)
        + "\n\nReturn an IEditorReport now."
    )


async def edit_manuscript(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    outline: IBookOutline,
    manuscript: IManuscript,
) -> IEditorReport:
    user_prompt = _build_user_prompt(
        concept=concept, bible=bible, outline=outline, manuscript=manuscript
    )
    model = get_settings().copilot_model
    record_prompt(
        agent_name="editor-agent",
        model=model,
        system=SYSTEM_PROMPT,
        user=user_prompt,
        response_format="IEditorReport",
    )
    agent = Agent(
        client=get_chat_client(),
        name="editor-agent",
        instructions=SYSTEM_PROMPT,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": IEditorReport},
    )
    if response.value is None:
        raise RuntimeError("Editor agent returned no value")
    record_output(agent_name="editor-agent", value=response.value)
    return response.value
