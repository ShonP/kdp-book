"""Pydantic data models — the typed contract between every step.

All agents return one of these models; the workflow's checkpoint state is the
union of them. Every model is stable JSON-serializable so checkpoint files
survive code changes within a major version.

Modules planned (see PLAN.md for phase mapping):
    book_type.py     → BookType enum + per-type strategy parameters     (Phase 0)
    concept.py       → IConcept                                         (Phase 1)
    outline.py       → IOutline, IChapter, IScene                       (Phase 1)
    bible.py         → ICharacter, ILocation, IStyleGuide               (Phase 1)
    manuscript.py    → IManuscript, IChapterDraft                       (Phase 2)
    illustration.py  → IIllustrationBrief, IPageImage                   (Phase 6)
    cover.py         → ICoverDesign, ICoverDimensions                   (Phase 4)
    metadata.py      → IKDPMetadata                                     (Phase 5)
    review.py        → IQualityReport, IIssue                           (Phase 8)
    assets.py        → IAssetEntry, IAssetManifest (mangas-ported)      (Phase 6)
    state.py         → IBookState — root checkpoint payload             (Phase 0)
"""
