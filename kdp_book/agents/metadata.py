"""MetadataAgent — produces KDP listing metadata (title/blurb/keywords/categories)."""

from __future__ import annotations

from agent_framework import Agent

from kdp_book.agents._language import language_directive, normalize_language
from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import (
    IBookBible,
    IBookConcept,
    IBookMetadata,
)
from kdp_book.observability import record_output, record_prompt

SYSTEM_PROMPT = """You are a KDP metadata specialist. Given a finished book's
concept and bible, produce a complete IBookMetadata that maximizes
discoverability and conversion on Amazon Kindle / KDP.

CONSTRAINTS (must obey)
- title: ≤ 200 chars. Use the concept title verbatim unless it would
  be rejected by KDP (no excessive punctuation, no ALL CAPS, no
  promotional wording).
- subtitle: optional; ≤ 150 chars. Reinforces the hook without restating
  the title.
- blurb: 150-400 words for fiction; 200-500 for non-fiction. Open with
  a hook, set the stakes, end with a question or promise. No spoilers.
  No prices. No links. No "from the bestselling author of…" unless true.
  Plain text — no markdown.
- keywords: 7 short phrases (≤ 50 chars each), ranked by search intent.
  No quotation marks, no commas inside a single keyword, no duplicates,
  no words from the title (KDP indexes title separately).
- bisac_categories: 2-3 BISAC codes ('JUVENILE FICTION / Animals / Bears',
  'FICTION / Fantasy / Epic', etc). Use the standard hierarchy. BISAC codes
  are an English-language standard; keep them in English even for non-
  English books.
- age_range: required for children's books ('Ages 4-8'). Empty for adult.
- language: ISO 639-1 code matching the prose language ('en', 'he', ...).
  Use the value supplied to you in the input.
- ai_disclosure: true.
- author: copy from the input.

Return ONLY structured JSON matching IBookMetadata. No prose."""


def _build_user_prompt(
    *, concept: IBookConcept, bible: IBookBible, author: str, language: str,
) -> str:
    char_lines = [f"- {c.name} ({c.role})" for c in bible.characters]
    return (
        f"AUTHOR: {author}\n"
        f"LANGUAGE (ISO 639-1): {language}\n\n"
        f"CONCEPT\n"
        f"Title: {concept.title}\n"
        f"Subtitle: {concept.subtitle}\n"
        f"Hook: {concept.hook}\n"
        f"Audience: {concept.audience}\n"
        f"Tone: {concept.tone}\n"
        f"Themes: {', '.join(concept.themes) or 'n/a'}\n"
        f"Comparable titles: {', '.join(concept.comparable_titles) or 'n/a'}\n\n"
        f"CHARACTERS\n" + "\n".join(char_lines) + "\n\n"
        "Produce a complete IBookMetadata now."
    )


async def generate_metadata(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    author: str,
    language: str = "en",
) -> IBookMetadata:
    iso = normalize_language(language)
    user_prompt = _build_user_prompt(
        concept=concept, bible=bible, author=author, language=iso,
    )
    model = get_settings().copilot_model
    instructions = SYSTEM_PROMPT + language_directive(iso)
    record_prompt(
        agent_name="metadata-agent",
        model=model,
        system=instructions,
        user=user_prompt,
        response_format="IBookMetadata",
    )
    agent = Agent(
        client=get_chat_client(),
        name="metadata-agent",
        instructions=instructions,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": IBookMetadata},
    )
    if response.value is None:
        raise RuntimeError("Metadata agent returned no value")
    md = response.value
    if not md.author:
        md.author = author
    md.language = iso
    record_output(agent_name="metadata-agent", value=md)
    return md
