"""WriterAgent — drafts one chapter at a time given concept, outline, and bible."""

from __future__ import annotations

from agent_framework import Agent

from kdp_book.agents._language import language_directive
from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import (
    IBookBible,
    IBookConcept,
    IBookOutline,
    IBookTypeConfig,
    IChapter,
    IChapterDraft,
)
from kdp_book.observability import record_output, record_prompt

SYSTEM_PROMPT = """You are a senior commercial fiction/non-fiction writer drafting a chapter
for a Kindle Direct Publishing book. You will be given:
- the concept (title, hook, audience, tone)
- the full chapter outline (so you know where this chapter sits in the arc)
- the character bible + style guide (the source of truth — never contradict)
- the previous chapter's last paragraph for continuity (when available)
- this chapter's outline (title, summary, scenes/beats, target word count)

Write the chapter as polished, ready-to-publish prose. Rules:

GENRE / TONE
- Match the tone field exactly — no genre drift.
- Children's picture books: short rhythmic sentences, gentle repetition,
  and concrete sensory imagery. Read-aloud cadence. Plain language.
- Light novels: tight 1st or close-3rd POV, fast banter, vivid action,
  internal monologue that propels the plot.
- Non-fiction: clear thesis up front, concrete examples, step-by-step
  logic, no purple prose.
- Adult fiction: scene-driven, varied sentence length, earned emotion.

CONTINUITY
- Do not contradict the bible (names, ages, costumes, locations, palettes).
- Maintain POV unless the chapter outline explicitly switches it.
- Pick up emotional state where the previous chapter ended.

LENGTH
- Hit the target word count to within ±15%.
- Cover every beat from the chapter outline; expand into prose, never list.

OUTPUT
- prose: the chapter body only. Do NOT include the chapter title — the
  formatter will render that.
- word_count: integer count of `prose`.
- notes: optional short list of things you couldn't fully resolve and want
  the editor to look at (one line each, ≤ 80 chars).

No filler, no AI-tells ("As an AI…"), no meta commentary, no markdown
headings inside `prose`. Plain prose with paragraph breaks (\\n\\n).
"""


def _build_user_prompt(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    outline: IBookOutline,
    chapter: IChapter,
    type_config: IBookTypeConfig,
    previous_tail: str,
) -> str:
    char_lines = [
        f"- {c.name} ({c.role}): {c.appearance}; voice: {c.voice or 'n/a'}"
        for c in bible.characters
    ]
    location_lines = [f"- {loc.name}: {loc.description}" for loc in bible.locations]
    style = bible.style_guide
    outline_lines = [
        f"  {ch.index}. {ch.title} — {ch.summary[:140]}" for ch in outline.chapters
    ]
    scene_lines = [
        f"  Scene {sc.index}: {sc.summary} (mood: {sc.mood or 'n/a'})"
        for sc in chapter.scenes
    ] or ["  (no scene breakdown — write to the chapter summary)"]

    target_words = chapter.target_word_count or sum(type_config.words_per_chapter) // 2

    return (
        f"BOOK CONCEPT\n"
        f"Title: {concept.title}\n"
        f"Subtitle: {concept.subtitle}\n"
        f"Hook: {concept.hook}\n"
        f"Audience: {concept.audience}\n"
        f"Tone: {concept.tone}\n"
        f"Themes: {', '.join(concept.themes) or 'n/a'}\n\n"
        f"STYLE GUIDE\n"
        f"Art style: {style.art_style}\n"
        f"Tone: {style.tone}\n"
        f"Lighting/mood: {style.lighting}\n\n"
        f"CHARACTERS\n" + "\n".join(char_lines) + "\n\n"
        "LOCATIONS\n" + "\n".join(location_lines) + "\n\n"
        "FULL OUTLINE (for context — do not rewrite past or future chapters)\n"
        + "\n".join(outline_lines)
        + "\n\n"
        f"PREVIOUS CHAPTER TAIL\n"
        f"{previous_tail or '(this is the first chapter)'}\n\n"
        f"THIS CHAPTER\n"
        f"#{chapter.index}: {chapter.title}\n"
        f"POV: {chapter.pov or 'narrator default'}\n"
        f"Summary: {chapter.summary}\n"
        f"Beats:\n" + "\n".join(scene_lines) + "\n\n"
        f"TARGET\n"
        f"Word count: ~{target_words} (band {type_config.words_per_chapter[0]}-"
        f"{type_config.words_per_chapter[1]})\n\n"
        f"Write Chapter {chapter.index} now."
    )


async def write_chapter(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    outline: IBookOutline,
    chapter: IChapter,
    type_config: IBookTypeConfig,
    previous_tail: str = "",
    language: str = "en",
) -> IChapterDraft:
    """Draft a single chapter. Caller iterates."""
    user_prompt = _build_user_prompt(
        concept=concept,
        bible=bible,
        outline=outline,
        chapter=chapter,
        type_config=type_config,
        previous_tail=previous_tail,
    )
    model = get_settings().copilot_model
    instructions = SYSTEM_PROMPT + language_directive(language)
    record_prompt(
        agent_name="writer-agent",
        model=model,
        system=instructions,
        user=user_prompt,
        response_format="IChapterDraft",
    )
    agent = Agent(
        client=get_chat_client(),
        name="writer-agent",
        instructions=instructions,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": IChapterDraft},
    )
    if response.value is None:
        raise RuntimeError(f"Writer agent returned no value for chapter {chapter.index}")
    draft = response.value
    if not draft.title:
        draft.title = chapter.title
    if not draft.index:
        draft.index = chapter.index
    if not draft.word_count and draft.prose:
        draft.word_count = len(draft.prose.split())
    record_output(agent_name="writer-agent", value=draft)
    return draft
