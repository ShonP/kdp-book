"""ConceptAgent — turns a topic + book type into a structured `IBookConcept`."""

from __future__ import annotations

from agent_framework import Agent

from kdp_book.agents._language import language_directive
from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import BookType, IBookConcept, IBookTypeConfig
from kdp_book.observability import record_output, record_prompt

SYSTEM_PROMPT = """\
You are a senior commissioning editor at a major book publisher. Given a
topic and a target book type, produce a tight, sellable concept for a
self-published Kindle Direct Publishing (KDP) title.

Rules:
- The title must be specific, evocative, and ≤ 60 characters.
- The subtitle (if any) must add a clear hook, not restate the title.
- The hook is one paragraph (≤ 60 words) that would sell the book in a
  KDP description's opening.
- The audience must be a concrete reader description (age + interests for
  children's; reader profile for fiction; experience level for non-fiction).
- Tone is one short phrase ("warm and rhyming", "fast-paced isekai
  comedy", "blunt practical mentor").
- target_word_count and target_chapter_count must respect the book-type
  defaults given to you (you may pick within the band, do not exceed it).
- comparable_titles: 3 real, recently-successful KDP titles in the same
  niche. Format "Title by Author".
- themes: 2-5 short noun phrases.

Stay grounded — no purple prose, no marketing fluff in the structured
fields. The hook is the only place for voice.
"""


def _build_user_prompt(topic: str, book_type: BookType, cfg: IBookTypeConfig) -> str:
    return (
        f"Topic: {topic}\n"
        f"Book type: {book_type.value}\n"
        f"Trim size: {cfg.trim_size.value}\n"
        f"Target page count: ~{cfg.target_pages}\n"
        f"Chapter count band: {cfg.chapter_count[0]}-{cfg.chapter_count[1]}\n"
        f"Words/chapter band: {cfg.words_per_chapter[0]}-{cfg.words_per_chapter[1]}\n"
        f"Illustrations per chapter: {cfg.illustrations_per_chapter}\n\n"
        "Produce a structured concept that fits these constraints exactly."
    )


async def generate_concept(
    *,
    topic: str,
    book_type: BookType,
    type_config: IBookTypeConfig,
    language: str = "en",
) -> IBookConcept:
    """Run the concept agent and return a validated `IBookConcept`."""
    user_prompt = _build_user_prompt(topic, book_type, type_config)
    model = get_settings().copilot_model
    instructions = SYSTEM_PROMPT + language_directive(language)
    record_prompt(
        agent_name="concept-agent",
        model=model,
        system=instructions,
        user=user_prompt,
        response_format="IBookConcept",
    )
    agent = Agent(
        client=get_chat_client(),
        name="concept-agent",
        instructions=instructions,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": IBookConcept},
    )
    if response.value is None:
        raise RuntimeError("Concept agent returned no structured value")
    record_output(agent_name="concept-agent", value=response.value)
    return response.value
