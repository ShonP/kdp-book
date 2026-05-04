"""KDP cover-wrap geometry.

KDP gives the spine width as a function of page count and paper type:
    spine_inches = pages * ppi
    ppi = 0.002252  (BW white)
         0.0025    (BW cream)
         0.002347  (Premium color)

Bleed: 0.125 inch on every outer edge.

Total cover dimensions for a paperback wrap (single image, front + spine + back):
    width  = 2 * trim_width + spine + 2 * bleed
    height = trim_height + 2 * bleed
"""

from __future__ import annotations

from dataclasses import dataclass

from kdp_book.models.book import PaperType, TrimSize

PPI: dict[PaperType, float] = {
    PaperType.BW_WHITE: 0.002252,
    PaperType.BW_CREAM: 0.0025,
    PaperType.COLOR: 0.002347,
}

BLEED_INCHES = 0.125


def trim_size_inches(trim: TrimSize) -> tuple[float, float]:
    """Return (width, height) in inches for a `TrimSize`."""
    w_str, h_str = trim.value.split("x")
    return float(w_str), float(h_str)


def spine_width_inches(pages: int, paper: PaperType) -> float:
    """KDP spine width formula. Min 0 (covers under ~80 pages have no spine text)."""
    return max(0.0, pages * PPI[paper])


@dataclass
class ICoverDimensions:
    """All measurements for a print-ready cover wrap."""

    trim_width_in: float
    trim_height_in: float
    spine_width_in: float
    bleed_in: float

    @property
    def total_width_in(self) -> float:
        return 2 * self.trim_width_in + self.spine_width_in + 2 * self.bleed_in

    @property
    def total_height_in(self) -> float:
        return self.trim_height_in + 2 * self.bleed_in

    def at_dpi(self, dpi: int) -> tuple[int, int]:
        return (round(self.total_width_in * dpi), round(self.total_height_in * dpi))


def cover_dimensions(
    *,
    trim: TrimSize,
    paper: PaperType,
    pages: int,
) -> ICoverDimensions:
    w, h = trim_size_inches(trim)
    return ICoverDimensions(
        trim_width_in=w,
        trim_height_in=h,
        spine_width_in=spine_width_inches(pages, paper),
        bleed_in=BLEED_INCHES,
    )
