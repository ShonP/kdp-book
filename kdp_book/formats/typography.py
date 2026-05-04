"""KDP interior typography + margins.

Margin/gutter table (KDP minimums for paperback):

    page_count   gutter_in
    24-150       0.375
    151-300      0.5
    301-500      0.625
    501-700      0.75
    701-828      0.875

Outside / top / bottom: 0.25" min, 0.375" recommended.
Bleed: 0.125" on outer edges if full-bleed art, else 0.

Type sizes (point) are conservative defaults; the IBookTypeConfig overrides
when needed.
"""

from __future__ import annotations

from dataclasses import dataclass


def gutter_inches(pages: int) -> float:
    if pages <= 150:
        return 0.375
    if pages <= 300:
        return 0.5
    if pages <= 500:
        return 0.625
    if pages <= 700:
        return 0.75
    return 0.875


@dataclass
class IInteriorMargins:
    gutter_in: float
    outer_in: float
    top_in: float
    bottom_in: float
    bleed_in: float


def interior_margins(pages: int, *, full_bleed: bool = False) -> IInteriorMargins:
    return IInteriorMargins(
        gutter_in=gutter_inches(pages),
        outer_in=0.375,
        top_in=0.5,
        bottom_in=0.5,
        bleed_in=0.125 if full_bleed else 0.0,
    )


@dataclass
class ITypography:
    body_pt: float
    leading_pt: float
    chapter_title_pt: float
    chapter_subtitle_pt: float
    paragraph_indent_in: float = 0.2
    paragraph_spacing_pt: float = 4.0


def typography_for(book_type_value: str) -> ITypography:
    if book_type_value == "children-picture-book":
        return ITypography(body_pt=22, leading_pt=28, chapter_title_pt=36, chapter_subtitle_pt=20)
    if book_type_value == "non-fiction":
        return ITypography(body_pt=11, leading_pt=14, chapter_title_pt=24, chapter_subtitle_pt=14)
    # light-novel / fiction-novel
    return ITypography(body_pt=11, leading_pt=14, chapter_title_pt=22, chapter_subtitle_pt=14)
