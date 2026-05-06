"""Unit tests for `kdp_book.formats.compress`."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from kdp_book.formats.compress import (
    ICompressionConfig,
    _flatten_to_rgb,
    compress_cover_pdf,
    compress_image_to_jpeg,
)


def _make_png(path: Path, size: tuple[int, int], mode: str = "RGB") -> Path:
    img = (
        Image.new("RGBA", size, (200, 100, 50, 128))
        if mode == "RGBA"
        else Image.new(mode, size, (200, 100, 50))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")
    return path


@pytest.mark.unit
def test_compress_image_downscales_oversized(tmp_path: Path) -> None:
    src = _make_png(tmp_path / "big.png", (4096, 2048))
    dst = tmp_path / "out.jpg"

    compress_image_to_jpeg(src, dst, max_dim=1200, quality=85)

    assert dst.exists()
    with Image.open(dst) as out:
        assert max(out.size) == 1200
        assert out.size == (1200, 600)
        assert out.format == "JPEG"
    assert dst.stat().st_size < src.stat().st_size


@pytest.mark.unit
def test_compress_image_does_not_upscale_small(tmp_path: Path) -> None:
    src = _make_png(tmp_path / "small.png", (300, 200))
    dst = tmp_path / "out.jpg"

    compress_image_to_jpeg(src, dst, max_dim=1200, quality=85)

    with Image.open(dst) as out:
        assert out.size == (300, 200)


@pytest.mark.unit
def test_compress_image_flattens_rgba_alpha(tmp_path: Path) -> None:
    src = _make_png(tmp_path / "rgba.png", (800, 600), mode="RGBA")
    dst = tmp_path / "out.jpg"

    compress_image_to_jpeg(src, dst, max_dim=1200, quality=85)

    with Image.open(dst) as out:
        assert out.mode == "RGB"
        assert out.format == "JPEG"


@pytest.mark.unit
def test_compress_image_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        compress_image_to_jpeg(
            tmp_path / "nope.png",
            tmp_path / "out.jpg",
        )


@pytest.mark.unit
def test_flatten_to_rgb_passthrough_for_rgb() -> None:
    img = Image.new("RGB", (10, 10), (1, 2, 3))
    assert _flatten_to_rgb(img) is img


@pytest.mark.unit
def test_flatten_to_rgb_handles_palette_with_transparency() -> None:
    img = Image.new("P", (10, 10), 0)
    img.info["transparency"] = 0
    flat = _flatten_to_rgb(img)
    assert flat.mode == "RGB"
    assert flat.size == (10, 10)


@pytest.mark.unit
def test_compress_cover_pdf_writes_pdf(tmp_path: Path) -> None:
    src = _make_png(tmp_path / "wrap.png", (3000, 2000))
    dst = tmp_path / "cover-compressed.pdf"

    compress_cover_pdf(src, dst, config=ICompressionConfig(max_dim=1200, quality=85))

    assert dst.exists()
    assert dst.stat().st_size > 0
    with dst.open("rb") as f:
        assert f.read(4) == b"%PDF"


@pytest.mark.unit
def test_compress_cover_pdf_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        compress_cover_pdf(tmp_path / "nope.png", tmp_path / "out.pdf")


@pytest.mark.unit
def test_compress_image_quality_lower_yields_smaller_file(tmp_path: Path) -> None:
    src = _make_png(tmp_path / "src.png", (1500, 1500))
    high = tmp_path / "high.jpg"
    low = tmp_path / "low.jpg"

    compress_image_to_jpeg(src, high, max_dim=1200, quality=95)
    compress_image_to_jpeg(src, low, max_dim=1200, quality=40)

    assert low.stat().st_size <= high.stat().st_size


@pytest.mark.unit
def test_compression_config_defaults() -> None:
    cfg = ICompressionConfig()
    assert cfg.max_dim == 1200
    assert cfg.quality == 85
