"""BibleAgent — concept + outline → characters, locations, style guide.

The bible is the cross-cutting input every later prompt references.
Identity-locking sheets here become the canonical text inputs that get
rendered into reference images during the illustration phase.
"""

from __future__ import annotations

from agent_framework import Agent

from kdp_book.client import get_chat_client
from kdp_book.config import get_settings
from kdp_book.middleware import llm_call_logging
from kdp_book.models.book import (
    IBookBible,
    IBookConcept,
    IBookOutline,
    IBookTypeConfig,
)
from kdp_book.observability import record_output, record_prompt

SYSTEM_PROMPT = """\
You are an art director and continuity editor. From the concept and
outline, produce a reusable "bible" that downstream writing and
illustration steps will consume.

Rules:
- characters: 3-8 INDIVIDUAL named entities. ABSOLUTELY NEVER use plural,
  collective, or group entries (no "Children", "Young Dragons",
  "The Villagers", "Crew Members", etc). If a story features a group,
  pick 1-3 specific named members and list each separately. Each entry
  represents ONE specific person/animal/being whose face we will lock
  with a single reference image. For each, give:
    * name (short, distinctive, SINGULAR — a proper name like "Pip",
      "Luna", "Captain Finn", "Grandma Ember" — never a category).
    * role ("protagonist", "best friend", "antagonist", etc.)
    * age (concrete: "8 years old" / "early 20s" / "ancient")
    * appearance: face shape, hair color/length, skin tone, eye color,
      build. Concrete and visualizable. Describe ONE individual — never
      "they have" or "the group is".
    * costume: outfit they wear by default — fabric, color, signature
      detail.
    * palette: 3-5 hex colors that define their visual identity.
    * personality: 1 sentence.
    * voice: 1 short phrase capturing how they speak.
    * arc: their narrative trajectory in this book (1 sentence).
- locations: 2-6 named recurring places. Each has:
    * name, description (concrete sensory: light, materials, scale).
    * palette: 3-5 hex colors.
    * mood: 1 phrase.
- style_guide: a single object capturing the cross-cutting visual style.
    * art_style: 1 phrase ("watercolor children's-book", "shounen manga
      ink + grey wash", "minimalist tech illustration").
    * palette: 4-6 dominant colors as hex.
    * line_weight: "thin" / "medium" / "bold" / "varied" / etc.
    * lighting: "soft diffuse" / "high-contrast" / "golden-hour" / etc.
    * tone: 1 phrase capturing the emotional atmosphere.
    * inspirations: 2-3 concrete reference works ("in the style of X").

Only mention every named character/location that appears in the outline.
Do not invent characters not present in the chapter summaries.
"""


def _flatten_outline(outline: IBookOutline) -> str:
    lines: list[str] = []
    for ch in outline.chapters:
        lines.append(f"Chapter {ch.index}: {ch.title} — {ch.summary}")
        for sc in ch.scenes:
            chars = ", ".join(sc.characters) if sc.characters else "(none)"
            lines.append(f"  Scene {sc.index}: {sc.title} | setting={sc.setting} | chars={chars}")
    return "\n".join(lines)


def _build_user_prompt(
    concept: IBookConcept,
    outline: IBookOutline,
    cfg: IBookTypeConfig,
) -> str:
    return (
        f"Title: {concept.title}\n"
        f"Hook: {concept.hook}\n"
        f"Audience: {concept.audience}\n"
        f"Tone: {concept.tone}\n"
        f"Book type: {cfg.trim_size.value}, target pages ~{cfg.target_pages}\n\n"
        "Outline:\n"
        f"{_flatten_outline(outline)}\n\n"
        "Produce the structured bible."
    )


async def generate_bible(
    *,
    concept: IBookConcept,
    outline: IBookOutline,
    type_config: IBookTypeConfig,
) -> IBookBible:
    user_prompt = _build_user_prompt(concept, outline, type_config)
    model = get_settings().copilot_model
    record_prompt(
        agent_name="bible-agent",
        model=model,
        system=SYSTEM_PROMPT,
        user=user_prompt,
        response_format="IBookBible",
    )
    agent = Agent(
        client=get_chat_client(),
        name="bible-agent",
        instructions=SYSTEM_PROMPT,
        middleware=[llm_call_logging],
    )
    response = await agent.run(
        user_prompt,
        options={"response_format": IBookBible},
    )
    if response.value is None:
        raise RuntimeError("Bible agent returned no structured value")
    bible = response.value
    bible.characters = _drop_group_characters(bible.characters)
    record_output(agent_name="bible-agent", value=bible)
    return bible


def _is_group_name(name: str) -> bool:
    """Heuristic: drop bible entries whose name reads as a plural/collective.

    Conservative — only fires on well-known collective words. We accept the
    occasional plural-sounding proper name ("Charles") rather than risk
    dropping a legitimate character.
    """
    n = (name or "").strip()
    if not n:
        return True
    lower = n.lower()
    collective_words = {
        "children", "kids", "villagers", "townsfolk", "townspeople",
        "crowd", "crew", "soldiers", "guards", "students", "friends",
        "family", "neighbors", "neighbours", "siblings", "elders",
        "dragons", "knights", "wizards", "people", "men", "women",
        "boys", "girls", "animals", "monsters", "creatures", "twins",
        "triplets", "peers", "classmates", "teammates", "denizens",
        "heroes", "villains", "rivals",
    }
    tokens = set(lower.replace("-", " ").split())
    return bool(tokens & collective_words)


def _drop_group_characters(characters: list) -> list:
    kept = []
    for c in characters:
        if _is_group_name(c.name):
            continue
        kept.append(c)
    return kept
