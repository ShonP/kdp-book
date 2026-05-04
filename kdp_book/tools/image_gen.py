"""Image generation orchestrator — wraps the gpt-image-2 client with safety retries.

`render_with_retry(prompt, refs, ...)` is the single entry point used by the
character-sheet, page, and cover renderers. It returns `(bytes, retry_meta)`
where `retry_meta` matches the mangas sidecar shape so QualityAgent + sidecars
can both consume it.
"""

from __future__ import annotations

import time
from pathlib import Path

from kdp_book.log import log
from kdp_book.tools.image_client import edit_image_composite, generate_image
from kdp_book.tools.safety_filter import (
    MAX_SOFTENING_LEVEL,
    is_safety_rejection,
    soften_prompt,
)


def render_with_retry(
    *,
    prompt: str,
    references: list[Path] | None = None,
    size: str | None = None,
    quality: str | None = None,
    content_rating: str = "all-ages",
    max_attempts: int = 5,
) -> tuple[bytes, dict]:
    """Render an image, progressively softening the prompt on safety rejections.

    Returns (image_bytes, retry_meta) where retry_meta is:
      {
        "original_prompt": str,
        "retries": [{attempt, softening_level, prompt, error}, ...],
        "final_softening_level": int,
        "duration_seconds": float,
        "safety_filter_hits": int,
      }

    Strategy:
      1. Try original prompt with refs (if any).
      2. On safety rejection: soften prompt; level ↑ each attempt.
      3. After softening exhausted with refs: drop refs, soften max, retry.
      4. Non-safety errors propagate immediately.
    """
    started = time.monotonic()
    refs = references or []
    retries: list[dict] = []
    safety_hits = 0
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        softening_level = min(attempt - 1, MAX_SOFTENING_LEVEL)
        softened = soften_prompt(prompt, softening_level, content_rating)
        try:
            if refs:
                img = edit_image_composite(softened, refs, size=size, quality=quality)
            else:
                img = generate_image(softened, size=size, quality=quality)
            duration = time.monotonic() - started
            return img, {
                "original_prompt": prompt,
                "retries": retries,
                "final_softening_level": softening_level,
                "duration_seconds": round(duration, 2),
                "safety_filter_hits": safety_hits,
            }
        except RuntimeError as e:
            err = str(e)
            last_error = e
            if not is_safety_rejection(err):
                log.error("Image render failed (non-safety): %s", err[:300])
                raise
            safety_hits += 1
            retries.append(
                {
                    "attempt": attempt,
                    "softening_level": softening_level,
                    "prompt": softened[:600],
                    "error": err[:300],
                }
            )
            log.warning(
                "Safety rejection on attempt %d (softening %d). Retrying.",
                attempt, softening_level,
            )

    # Exhausted with refs — try one final attempt with no refs + max softening.
    if refs:
        try:
            softened = soften_prompt(prompt, MAX_SOFTENING_LEVEL, content_rating)
            img = generate_image(softened, size=size, quality=quality)
            duration = time.monotonic() - started
            retries.append(
                {
                    "attempt": max_attempts + 1,
                    "softening_level": MAX_SOFTENING_LEVEL,
                    "prompt": softened[:600],
                    "error": "fallback: dropped references",
                }
            )
            return img, {
                "original_prompt": prompt,
                "retries": retries,
                "final_softening_level": MAX_SOFTENING_LEVEL,
                "duration_seconds": round(duration, 2),
                "safety_filter_hits": safety_hits,
            }
        except RuntimeError as e:
            last_error = e

    raise RuntimeError(
        f"Image render exhausted {max_attempts} attempts: {last_error}"
    )
