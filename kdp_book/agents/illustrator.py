"""IllustratorAgent — turns chapter scenes into composition-only image briefs.

The OutlineAgent already populates `IScene.illustration_brief`, but at
illustration time we want richer per-page prompts that include camera, pose,
action, lighting, and explicit characters_present so we can attach the right
reference images at render time.
"""

from __future__ import annotations

from agent_framework import Agent
from pydantic import BaseModel, Field

from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import (
    IBookBible,
    IBookConcept,
    IChapter,
    IIllustrationBrief,
)
from kdp_book.observability import record_output, record_prompt


class _IllustrationPlan(BaseModel):
    """Wrapper so the model can return a structured list."""

    briefs: list[IIllustrationBrief] = Field(default_factory=list)


SYSTEM_PROMPT = """You are an art director planning illustrations for a children's
or fiction book. Given a chapter and the available characters/locations, you
output one IIllustrationBrief PER SCENE that needs an illustration.

CRITICAL RULES
- Composition-only. Describe camera, pose, action, lighting, mood, and
  setting. Do NOT describe a character's face, hair color, or costume —
  those are locked by the reference image.
- Reference identity by NAME ONLY in `characters_present`. Use the exact
  bible names so we attach the right reference image.
- One brief per scene that warrants a picture. Children's picture books:
  one brief per scene (essentially per page). Light-novel style: 0-1
  briefs per chapter (chapter splash); skip scenes that don't need art.
- `composition` must be visual (≤ 200 chars): "wide shot of two figures
  walking through a meadow toward a glowing castle on the hill".
- `camera`: short ("low angle", "over-the-shoulder", "wide establishing").
- `pose`: short physical description ("arms stretched out, leaning forward").
- `action`: what's happening this instant ("reaching for the doorknob").
- `lighting`: light direction + quality ("warm golden hour from the right").
- `mood`: one or two emotional words ("anxious wonder").
- `location`: must match a bible location name when possible.
- `chapter_index` and `scene_index` must match what was given.

Output ONLY structured JSON matching the schema. No prose."""


def _build_user_prompt(
    *,
    chapter: IChapter,
    bible: IBookBible,
    concept: IBookConcept,
    illustrations_per_chapter: int,
) -> str:
    chars = "\n".join(f"- {c.name} ({c.role})" for c in bible.characters)
    locs = "\n".join(f"- {loc.name}: {loc.description[:80]}" for loc in bible.locations)
    scenes = (
        "\n".join(
            f"  Scene {sc.index}: {sc.summary} | mood={sc.mood} | "
            f"setting={sc.setting} | brief_hint={sc.illustration_brief}"
            for sc in chapter.scenes
        )
        or f"  Scene 1: {chapter.summary}"
    )
    return (
        f"BOOK: {concept.title}\n"
        f"Tone: {concept.tone}\n"
        f"Style intent: {concept.audience}\n\n"
        f"CHARACTERS\n{chars}\n\n"
        f"LOCATIONS\n{locs}\n\n"
        f"CHAPTER {chapter.index}: {chapter.title}\n"
        f"Summary: {chapter.summary}\n"
        f"SCENES:\n{scenes}\n\n"
        f"Target illustrations for this chapter: {illustrations_per_chapter}.\n"
        f"Return an _IllustrationPlan with briefs[] populated. "
        f"chapter_index={chapter.index} on every brief."
    )


async def plan_chapter_illustrations(
    *,
    concept: IBookConcept,
    bible: IBookBible,
    chapter: IChapter,
    illustrations_per_chapter: int,
) -> list[IIllustrationBrief]:
    user_prompt = _build_user_prompt(
        chapter=chapter,
        bible=bible,
        concept=concept,
        illustrations_per_chapter=illustrations_per_chapter,
    )
    model = get_settings().copilot_model
    record_prompt(
        agent_name="illustrator-agent",
        model=model,
        system=SYSTEM_PROMPT,
        user=user_prompt,
        response_format="_IllustrationPlan",
    )
    agent = Agent(
        client=get_chat_client(),
        name="illustrator-agent",
        instructions=SYSTEM_PROMPT,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": _IllustrationPlan},
    )
    if response.value is None:
        raise RuntimeError(f"Illustrator agent returned no value for chapter {chapter.index}")
    plan = response.value
    # Defensive: force chapter_index to match the requested chapter.
    for brief in plan.briefs:
        brief.chapter_index = chapter.index
    record_output(agent_name="illustrator-agent", value=plan)
    return plan.briefs
