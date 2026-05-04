"""Cover compositor — Pillow-based front/spine/back wrap assembly.

The cover-agent + image-gen pipeline produces front (and optionally back)
illustrations. This module composes them into a single print-ready PDF
wrap with KDP-spec spine width and bleed, and overlays title/author/spine
text in the configured palette.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from kdp_book.formats.cover_geometry import ICoverDimensions
from kdp_book.log import log

DPI = 300


def _hex_to_rgb(value: str, fallback: tuple[int, int, int] = (250, 248, 240)) -> tuple[int, int, int]:
    s = (value or "").lstrip("#").strip()
    if len(s) != 6:
        return fallback
    try:
        return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _load_font(size: int) -> ImageFont.ImageFont:
    """Best-effort font loader. Falls back to default if no system font found."""
    candidates = [
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVu-Serif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def compose_cover_wrap(
    *,
    front_png: Path,
    back_png: Path | None,
    title: str,
    subtitle: str,
    author: str,
    spine_text: str,
    palette: list[str],
    dimensions: ICoverDimensions,
    output_path: Path,
) -> Path:
    """Assemble a print-ready cover wrap PNG (and PDF) at the given path."""
    total_w_px, total_h_px = dimensions.at_dpi(DPI)

    bg = _hex_to_rgb(palette[0] if palette else "#fffaf0")
    accent = _hex_to_rgb(palette[1] if len(palette) > 1 else "#3b2a1a", fallback=(60, 40, 20))

    canvas = Image.new("RGB", (total_w_px, total_h_px), bg)

    bleed_px = round(dimensions.bleed_in * DPI)
    trim_w_px = round(dimensions.trim_width_in * DPI)
    trim_h_px = round(dimensions.trim_height_in * DPI)
    spine_w_px = round(dimensions.spine_width_in * DPI)

    # Layout: [bleed][back][spine][front][bleed]
    back_x = bleed_px
    spine_x = back_x + trim_w_px
    front_x = spine_x + spine_w_px

    # Front art
    front = Image.open(front_png).convert("RGB")
    front_resized = front.resize((trim_w_px + bleed_px, trim_h_px + 2 * bleed_px))
    canvas.paste(front_resized, (front_x, 0))

    # Back art (or solid)
    if back_png and back_png.exists():
        back = Image.open(back_png).convert("RGB")
        back_resized = back.resize((trim_w_px + bleed_px, trim_h_px + 2 * bleed_px))
        canvas.paste(back_resized, (0, 0))
    else:
        ImageDraw.Draw(canvas).rectangle(
            [0, 0, back_x + trim_w_px, total_h_px],
            fill=bg,
        )

    # Title overlay on front (top quarter)
    draw = ImageDraw.Draw(canvas)
    title_size = max(48, int(trim_h_px * 0.07))
    subtitle_size = max(28, int(title_size * 0.45))
    author_size = max(28, int(title_size * 0.5))

    title_font = _load_font(title_size)
    subtitle_font = _load_font(subtitle_size)
    author_font = _load_font(author_size)

    # Title — centered horizontally inside the front area, near the top.
    title_y = bleed_px + int(trim_h_px * 0.07)
    _draw_centered(draw, title, title_font, accent, x_center=front_x + trim_w_px // 2, y=title_y)

    if subtitle:
        sub_y = title_y + title_size + int(title_size * 0.2)
        _draw_centered(
            draw, subtitle, subtitle_font, accent,
            x_center=front_x + trim_w_px // 2, y=sub_y,
        )

    # Author — bottom of front
    author_y = bleed_px + trim_h_px - author_size - int(trim_h_px * 0.08)
    _draw_centered(
        draw, author, author_font, accent,
        x_center=front_x + trim_w_px // 2, y=author_y,
    )

    # Spine text (vertical) — only if spine is wide enough
    if spine_w_px > 60 and spine_text:
        _draw_spine(canvas, spine_text, spine_x, spine_w_px, total_h_px, accent)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", dpi=(DPI, DPI))
    pdf_path = output_path.with_suffix(".pdf")
    canvas.save(pdf_path, format="PDF", resolution=DPI)
    log.info(
        "Composed cover: %s (%d×%d px @ %d DPI, spine %.3fin)",
        output_path, total_w_px, total_h_px, DPI, dimensions.spine_width_in,
    )
    return output_path


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int],
    *,
    x_center: int,
    y: int,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    draw.text((x_center - width // 2, y), text, fill=color, font=font)


def _draw_spine(
    canvas: Image.Image,
    text: str,
    spine_x: int,
    spine_w_px: int,
    total_h_px: int,
    color: tuple[int, int, int],
) -> None:
    """Render spine text rotated 90° and paste onto the spine column."""
    spine_height = total_h_px
    font_size = max(22, min(spine_w_px - 24, 60))
    font = _load_font(font_size)

    txt_img = Image.new("RGBA", (spine_height, spine_w_px), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_img)
    bbox = d.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    d.text(
        ((spine_height - text_w) // 2, (spine_w_px - text_h) // 2),
        text, fill=(*color, 255), font=font,
    )
    rotated = txt_img.rotate(90, expand=True)
    canvas.paste(rotated, (spine_x, 0), rotated)
