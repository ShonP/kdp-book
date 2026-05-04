"""Prompt softening for content-filter retries — port of mangas/safety_filter."""

from __future__ import annotations

import re

from kdp_book.log import log

SOFTENING_RULES: list[dict[str, str]] = [
    {},
    # Level 1 — replace dramatic literary words that misfire on benign scenes.
    {
        "tremor": "movement",
        "sweat": "moisture",
        "stagger": "stumble",
        "torn": "damaged",
        "ripped": "damaged",
        "smudge": "mark",
        "smudges": "marks",
        "agony": "pain",
        "wound": "injury",
        "gash": "mark",
    },
    # Level 2 — replace combat / gore vocabulary with neutralized synonyms.
    {
        "blood": "dark fluid",
        "bleeding": "wounded",
        "stab": "hit",
        "slash": "strike",
        "kill": "defeat",
        "death": "fall",
        "corpse": "fallen figure",
        "scream": "shout",
        "terror": "fear",
        "demonic": "monstrous",
        "fighting": "playing",
        "aggressive": "assertive",
        "threatening": "tense",
    },
    # Level 3 — neutralize remaining weapon/danger vocabulary.
    {
        "blood": "energy",
        "bleeding": "glowing",
        "stab": "touch",
        "slash": "wave",
        "weapon": "tool",
        "sword": "staff",
        "knife": "wand",
        "battle": "contest",
        "war": "challenge",
        "attack": "move",
        "destroy": "change",
        "dark": "dim",
        "shadow": "shade",
        "danger": "challenge",
        "panic": "unease",
        "fear": "surprise",
    },
    # Level 4 — soften age & vulnerability vocabulary that triggers child-safety filters.
    {
        "child": "young person",
        "children": "young people",
        "boy": "young character",
        "girl": "young character",
        "scared": "alert",
        "restrained": "held",
        "trapped": "stuck",
        "violence": "conflict",
        "violent": "dramatic",
    },
]

RATING_SUFFIX: dict[str, str] = {
    "all-ages": " Suitable for all ages. No graphic violence. Peaceful atmosphere.",
    "teen": " Suitable for teens. No graphic gore.",
    "mature": "",
}

MAX_SOFTENING_LEVEL = len(SOFTENING_RULES) - 1


def _case_replace(text: str, old: str, new: str) -> str:
    pattern = re.compile(re.escape(old), re.IGNORECASE)
    return pattern.sub(new, text)


def soften_prompt(prompt: str, level: int, content_rating: str = "all-ages") -> str:
    """Apply progressive softening up to `level` (0 = no-op)."""
    if level <= 0:
        return prompt
    level = min(level, MAX_SOFTENING_LEVEL)

    result = prompt
    for lvl in range(1, level + 1):
        for original, replacement in SOFTENING_RULES[lvl].items():
            result = _case_replace(result, original, replacement)
        log.debug("Applied softening level %d", lvl)

    suffix = RATING_SUFFIX.get(content_rating, RATING_SUFFIX["all-ages"])
    if level >= 2 and suffix:
        result += suffix
    return result


SAFETY_HINT_TOKENS = (
    "content_policy",
    "safety_filter",
    "responsible_ai",
    "moderation",
    "violates",
)


def is_safety_rejection(error_message: str) -> bool:
    """Heuristic — does this error look like a content-policy rejection?"""
    msg = (error_message or "").lower()
    return any(tok in msg for tok in SAFETY_HINT_TOKENS)
