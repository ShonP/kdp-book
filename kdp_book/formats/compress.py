"""Image / PDF / EPUB compression for KDP outputs.

Generates lightweight counterparts of the print-ready artifacts so authors can
preview / share without shipping 150MB+ files:

* `interior.pdf`           → `interior-compressed.pdf`
* `cover.pdf`              → `cover-compressed.pdf`
* `<slug>.epub`            → `<slug>-compressed.epub`

Strategy
--------
Re-render PDF/EPUB through the existing builders, but first replace every
referenced PNG illustration with a downscaled JPEG (max 1200px on the long
edge, quality 85). reportlab and ebooklib both accept JPEG transparently, so
no other change to the builders is needed beyond making the EPUB media-type
follow the file extension (see `epub_builder.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from kdp_book.formats.epub_builder import build_epub
from kdp_book.formats.pdf_interior import build_interior_pdf
from kdp_book.log import log
from kdp_book.models.book import IBookState

IMAGE_MAX_DIM = 1200
IMAGE_QUALITY = 85
COMPRESSED_DIRNAME = "compressed_assets"


@dataclass(frozen=True)
class ICompressionConfig:
    max_dim: int = IMAGE_MAX_DIM
    quality: int = IMAGE_QUALITY


def compress_image_to_jpeg(
    src: Path,
    dst: Path,
    *,
    max_dim: int = IMAGE_MAX_DIM,
    quality: int = IMAGE_QUALITY,
) -> Path:
    """Open `src`, downscale so the long edge ≤ `max_dim`, save as JPEG.

    Preserves aspect ratio. Never upscales. RGBA / LA images are flattened
    onto a white background. Returns `dst`.
    """
    if not src.exists():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as im:
        im.load()
        flat = _flatten_to_rgb(im)
        long_edge = max(flat.size)
        if long_edge > max_dim:
            scale = max_dim / float(long_edge)
            new_size = (
                max(1, round(flat.size[0] * scale)),
                max(1, round(flat.size[1] * scale)),
            )
            flat = flat.resize(new_size, Image.LANCZOS)
        flat.save(
            dst,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
    return dst


def _flatten_to_rgb(im: Image.Image) -> Image.Image:
    """Drop alpha by compositing onto white — JPEG has no alpha channel."""
    if im.mode == "RGB":
        return im
    if im.mode in {"RGBA", "LA"} or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return im.convert("RGB")


def _compressed_dir(state: IBookState) -> Path:
    book_dir = Path(state.book_dir)
    out = book_dir / COMPRESSED_DIRNAME
    out.mkdir(parents=True, exist_ok=True)
    return out


def _compress_one(
    rel_path: str,
    book_dir: Path,
    compressed_dir: Path,
    config: ICompressionConfig,
) -> str | None:
    """Compress an image whose path is relative to `book_dir`.

    Returns the new path (relative to book_dir) or None if the source
    is missing.
    """
    src = book_dir / rel_path
    if not src.exists():
        log.warning("Compress: source missing, skipping: %s", src)
        return None
    dst = compressed_dir / (Path(rel_path).stem + ".jpg")
    compress_image_to_jpeg(
        src, dst, max_dim=config.max_dim, quality=config.quality
    )
    return str(dst.relative_to(book_dir))


def _state_with_compressed_images(
    state: IBookState, config: ICompressionConfig
) -> IBookState:
    """Return a deep-copied state where every image path points at a JPEG.

    Cover front/back/composed are also rewritten if present. Missing files
    are blanked so the builders skip them gracefully.
    """
    book_dir = Path(state.book_dir)
    compressed_dir = _compressed_dir(state)
    clone = state.model_copy(deep=True)

    for img in clone.images:
        new_rel = _compress_one(img.image_path, book_dir, compressed_dir, config)
        if new_rel is not None:
            img.image_path = new_rel

    if clone.cover is not None:
        if clone.cover.front_image_path:
            new_rel = _compress_one(
                clone.cover.front_image_path, book_dir, compressed_dir, config,
            )
            clone.cover.front_image_path = new_rel or ""
        if clone.cover.back_image_path:
            new_rel = _compress_one(
                clone.cover.back_image_path, book_dir, compressed_dir, config,
            )
            clone.cover.back_image_path = new_rel or ""

    return clone


def compress_interior_pdf(
    state: IBookState,
    output_path: Path,
    *,
    config: ICompressionConfig | None = None,
) -> Path:
    """Render `interior-compressed.pdf` using JPEG-encoded illustrations."""
    cfg = config or ICompressionConfig()
    compressed_state = _state_with_compressed_images(state, cfg)
    return build_interior_pdf(compressed_state, output_path)


def compress_epub(
    state: IBookState,
    output_path: Path,
    *,
    config: ICompressionConfig | None = None,
) -> Path:
    """Render `<slug>-compressed.epub` using JPEG-encoded illustrations."""
    cfg = config or ICompressionConfig()
    compressed_state = _state_with_compressed_images(state, cfg)
    return build_epub(compressed_state, output_path)


def compress_cover_pdf(
    src_image: Path,
    output_path: Path,
    *,
    config: ICompressionConfig | None = None,
) -> Path:
    """Compress a cover PNG/PDF source down to a JPEG-backed PDF.

    The cover wrap is a single rasterized page; we re-encode it as a JPEG
    page inside a fresh PDF, dropping the lossless PNG layer that bloats
    `cover.pdf`.
    """
    cfg = config or ICompressionConfig()
    if not src_image.exists():
        raise FileNotFoundError(src_image)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_image) as im:
        im.load()
        flat = _flatten_to_rgb(im)
        long_edge = max(flat.size)
        if long_edge > cfg.max_dim * 2:
            # Cover wraps are 300dpi. Allow up to 2x the interior cap so
            # they stay print-acceptable while still shedding most weight.
            scale = (cfg.max_dim * 2) / float(long_edge)
            flat = flat.resize(
                (round(flat.size[0] * scale), round(flat.size[1] * scale)),
                Image.LANCZOS,
            )
        flat.save(
            output_path,
            format="PDF",
            resolution=300,
            quality=cfg.quality,
        )
    return output_path
