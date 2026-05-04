"""Image client for `gpt-image-2` (Azure OpenAI deployment).

Mirrors the mangas pattern:
- `generate_image` — text-only generation
- `edit_image` — multipart `image[]` edit with reference images (NATIVE
  multi-image; never composite refs side-by-side)
- `edit_image_composite` — falls back to primary ref if multi rejected
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx

from kdp_book.config import get_settings
from kdp_book.log import log

REQUEST_TIMEOUT_CONNECT = 30.0
REQUEST_TIMEOUT_READ = 600.0
MAX_RETRIES = 3

_TIMEOUT = httpx.Timeout(
    connect=REQUEST_TIMEOUT_CONNECT,
    read=REQUEST_TIMEOUT_READ,
    write=30.0,
    pool=30.0,
)


def _api_key() -> str:
    s = get_settings()
    return s.azure_api_key or s.openai_api_key or ""


def _post_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict | None = None,
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    data: dict[str, str] | None = None,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if files:
                resp = httpx.post(url, headers=headers, files=files, data=data, timeout=_TIMEOUT)
            else:
                resp = httpx.post(url, headers=headers, json=json_body, timeout=_TIMEOUT)
            if resp.status_code == 429:
                wait = min(30 * attempt, 120)
                log.warning("Rate limited (429). Sleeping %ds (%d/%d)", wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue
            return resp
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout) as e:
            last_exc = e
            wait = 15 * attempt
            log.warning(
                "Image API timeout (%s). Retrying in %ds (%d/%d)",
                type(e).__name__, wait, attempt, MAX_RETRIES,
            )
            time.sleep(wait)
    if last_exc:
        raise last_exc
    raise RuntimeError("Image API: max retries exceeded")


def _extract_b64(response: httpx.Response) -> bytes:
    if response.status_code != 200:
        raise RuntimeError(f"Image API error ({response.status_code}): {response.text[:500]}")
    payload = response.json()
    data = payload.get("data") or []
    if not data:
        raise RuntimeError(f"Image API: empty data in response: {payload}")
    b64 = data[0].get("b64_json")
    if not b64:
        raise RuntimeError(f"Image API: no b64_json in response: {payload}")
    return base64.b64decode(b64)


def generate_image(
    prompt: str,
    *,
    size: str | None = None,
    quality: str | None = None,
) -> bytes:
    """Text-only image generation. Defaults pull from settings."""
    s = get_settings()
    if not s.azure_image_endpoint:
        raise RuntimeError("AZURE_IMAGE_ENDPOINT not configured")
    size = size or s.image_size
    quality = quality or s.image_quality
    log.info("Image generate: %d-char prompt @ %s (q=%s)", len(prompt), size, quality)
    resp = _post_with_retry(
        s.azure_image_endpoint,
        headers={"api-key": _api_key(), "Content-Type": "application/json"},
        json_body={"prompt": prompt, "n": 1, "size": size, "quality": quality},
    )
    return _extract_b64(resp)


def edit_image(
    prompt: str,
    references: list[Path],
    *,
    size: str | None = None,
    quality: str | None = None,
    input_fidelity: str = "high",
) -> bytes:
    """Edit using one or more reference images (multipart `image[]`)."""
    if not references:
        raise ValueError("edit_image requires at least one reference image")
    s = get_settings()
    if not s.azure_image_edit_endpoint:
        raise RuntimeError("AZURE_IMAGE_EDIT_ENDPOINT not configured")
    size = size or s.image_size
    quality = quality or s.image_quality
    log.info("Image edit: %d refs @ %s (q=%s)", len(references), size, quality)
    files = [
        ("image[]", (ref.name, ref.read_bytes(), "image/png"))
        for ref in references
    ]
    resp = _post_with_retry(
        s.azure_image_edit_endpoint,
        headers={"api-key": _api_key()},
        files=files,
        data={
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "input_fidelity": input_fidelity,
        },
    )
    return _extract_b64(resp)


def edit_image_composite(
    prompt: str,
    references: list[Path],
    *,
    size: str | None = None,
    quality: str | None = None,
) -> bytes:
    """Multi-ref edit with native `image[]`; falls back to primary ref on rejection."""
    try:
        return edit_image(prompt, references, size=size, quality=quality)
    except RuntimeError as e:
        if len(references) <= 1:
            raise
        log.warning(
            "Multi-image edit rejected (%s); falling back to primary ref %s",
            e, references[0].name,
        )
        return edit_image(prompt, references[:1], size=size, quality=quality)
