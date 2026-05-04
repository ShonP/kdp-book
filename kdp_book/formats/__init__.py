"""KDP-compliant output formatters.

Renders the final manuscript + assets into KDP-accepted artifacts:
print-interior PDF (mirror margins, gutter, optional bleed), wrap-cover PDF
(front + spine + back at 0.125" bleed, with spine width derived from page
count and paper type), and a reflowable EPUB.

Modules planned (see PLAN.md):
    margins.py        → KDP gutter/margin lookup table (port from
                        kdp-book-generator/src/config/book-config.ts)   (Phase 3)
    trim_sizes.py     → Catalog: 6×9, 5.5×8.5, 5×8, 8.5×11, 8.5×8.5,
                        7×10, 7.5×9.25 — with bleed math                (Phase 3)
    typography.py     → Per-book-type font + leading + drop-cap rules   (Phase 3)
    pdf_interior.py   → reportlab-based interior renderer
                        (running heads, page numbers, image plates)     (Phase 3)
    pdf_cover.py      → reportlab-based wrap cover renderer
                        (KDP template math: width = front + spine +
                         back + 2 × 0.125" bleed; spine = pages × ppi
                         where ppi depends on paper type)               (Phase 4)
    epub_builder.py   → ebooklib-based reflowable EPUB                  (Phase 3)
"""
