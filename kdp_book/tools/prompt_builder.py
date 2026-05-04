"""Composition-only prompt builder — identity comes from refs, not text.

The cardinal rule (from mangas): never describe a character's face/hair/costume
in a per-page prompt. The reference images carry identity. Per-page prompts
describe ONLY composition: camera, pose, action, lighting, mood, location.
"""

from __future__ import annotations

from kdp_book.models.book import (
    IBookConcept,
    IIllustrationBrief,
    IStyleGuide,
)


def build_character_sheet_prompt(
    *,
    character_name: str,
    appearance: str,
    costume: str,
    palette: list[str],
    style: IStyleGuide,
) -> str:
    """Identity-locking sheet — used to generate the character's `default` reference.

    This is the ONLY prompt where we spell out facial features and costume.
    Subsequent page prompts will reference the rendered image instead.
    """
    palette_str = ", ".join(palette) if palette else "matched to story palette"
    return (
        f"Full-body character reference sheet for {character_name}. "
        f"Front view, neutral pose, plain off-white background, even soft lighting. "
        f"Style: {style.art_style}. Line weight: {style.line_weight or 'clean'}. "
        f"Lighting: soft and flat for reference (not dramatic). "
        f"Appearance: {appearance}. "
        f"Costume: {costume or 'simple appropriate outfit'}. "
        f"Palette: {palette_str}. "
        f"No text, no labels, no logos, no watermark, no border. "
        f"Single subject only — no other characters or props in frame."
    )


def build_scene_prompt(
    *,
    brief: IIllustrationBrief,
    style: IStyleGuide,
    concept: IBookConcept,
) -> str:
    """Composition-only page prompt. Identity is supplied via reference images."""
    parts: list[str] = [
        f"Illustration in style: {style.art_style}.",
        f"Tone: {style.tone or concept.tone}.",
    ]
    if brief.location:
        parts.append(f"Setting: {brief.location}.")
    parts.append(f"Composition: {brief.composition}.")
    if brief.camera:
        parts.append(f"Camera: {brief.camera}.")
    if brief.pose:
        parts.append(f"Pose: {brief.pose}.")
    if brief.action:
        parts.append(f"Action: {brief.action}.")
    if brief.lighting or style.lighting:
        parts.append(f"Lighting: {brief.lighting or style.lighting}.")
    if brief.mood:
        parts.append(f"Mood: {brief.mood}.")
    if brief.characters_present:
        parts.append(
            "Characters in frame (identity locked by reference images, "
            "match exactly): " + ", ".join(brief.characters_present) + "."
        )
    parts.append(
        "Do NOT add text, captions, speech bubbles, watermarks, page numbers, "
        "or borders. Single illustration filling the frame."
    )
    return " ".join(parts)
