"""OutlineAgent — concept + book-type config → chapter-level outline."""

from __future__ import annotations

from agent_framework import Agent

from kdp_book.agents._language import language_directive
from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import IBookConcept, IBookOutline, IBookTypeConfig
from kdp_book.observability import record_output, record_prompt

SYSTEM_PROMPT = """\
You are a structural editor. Given a book concept and the target book-type
parameters, produce a chapter-level outline that will hold up when written
to length.

Rules:
- Honor the chapter-count band exactly (do not exceed the upper bound).
- Each chapter has: index (1-based), title, summary (1-3 sentences),
  pov (if first/third-person fiction), target_word_count (within the
  per-chapter band).
- Each chapter contains scenes_per_chapter scenes (use the target count
  given to you). Each scene has: index (1-based within chapter), title,
  summary (1-2 sentences), setting (location label), characters (list of
  named characters), mood, illustration_brief (short composition-only
  description: pose, action, framing — NO facial features, NO costume
  details).
- The outline as a whole must show a clear arc: setup → escalation →
  climax → resolution. For non-fiction, show progressive scaffolding from
  prerequisites to advanced application.
- For children-picture-book: each chapter is a 2-page spread, exactly one
  scene, ≤ 150 words target.
- For light-novel: chapters open with a hook, end on a beat that pulls
  the reader to the next.

Do not write prose here — outline only. Prose is added in a later step.
"""


def _build_user_prompt(concept: IBookConcept, cfg: IBookTypeConfig) -> str:
    cmps = "; ".join(concept.comparable_titles) or "(none provided)"
    themes = ", ".join(concept.themes) or "(none provided)"
    return (
        f"Title: {concept.title}\n"
        f"Subtitle: {concept.subtitle}\n"
        f"Hook: {concept.hook}\n"
        f"Audience: {concept.audience}\n"
        f"Tone: {concept.tone}\n"
        f"Themes: {themes}\n"
        f"Comparable titles: {cmps}\n\n"
        f"Trim: {cfg.trim_size.value}; Pages: ~{cfg.target_pages}\n"
        f"Chapter band: {cfg.chapter_count[0]}-{cfg.chapter_count[1]}\n"
        f"Target chapter count from concept: {concept.target_chapter_count}\n"
        f"Words/chapter: {cfg.words_per_chapter[0]}-{cfg.words_per_chapter[1]}\n"
        f"Scenes/chapter: {cfg.scenes_per_chapter[0]}-{cfg.scenes_per_chapter[1]}\n\n"
        "Produce the structured outline."
    )


async def generate_outline(
    *,
    concept: IBookConcept,
    type_config: IBookTypeConfig,
    language: str = "en",
) -> IBookOutline:
    user_prompt = _build_user_prompt(concept, type_config)
    model = get_settings().copilot_model
    instructions = SYSTEM_PROMPT + language_directive(language)
    record_prompt(
        agent_name="outline-agent",
        model=model,
        system=instructions,
        user=user_prompt,
        response_format="IBookOutline",
    )
    agent = Agent(
        client=get_chat_client(),
        name="outline-agent",
        instructions=instructions,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": IBookOutline},
    )
    if response.value is None:
        raise RuntimeError("Outline agent returned no structured value")
    record_output(agent_name="outline-agent", value=response.value)
    return response.value
