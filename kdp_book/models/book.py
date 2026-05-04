"""Book domain models — typed contracts between every pipeline step."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class BookType(str, Enum):
    """Catalog of supported KDP book types. Strategy dispatcher keys off this."""

    CHILDREN_PICTURE_BOOK = "children-picture-book"
    LIGHT_NOVEL = "light-novel"
    NON_FICTION = "non-fiction"
    FICTION_NOVEL = "fiction-novel"


class TrimSize(str, Enum):
    """KDP-supported trim sizes (inches)."""

    SQUARE_8_5 = "8.5x8.5"
    PORTRAIT_6X9 = "6x9"
    PORTRAIT_5_5X8_5 = "5.5x8.5"
    PORTRAIT_5X8 = "5x8"
    LETTER_8_5X11 = "8.5x11"
    PORTRAIT_7X10 = "7x10"
    PORTRAIT_7_5X9_25 = "7.5x9.25"


class PaperType(str, Enum):
    BW_WHITE = "bw-white"
    BW_CREAM = "bw-cream"
    COLOR = "color"


class IBookTypeConfig(BaseModel):
    """Per-`BookType` defaults — drives outline shape, word counts, art density."""

    trim_size: TrimSize
    paper_type: PaperType
    target_pages: int
    chapter_count: tuple[int, int]
    words_per_chapter: tuple[int, int]
    scenes_per_chapter: tuple[int, int]
    illustrations_per_chapter: int
    cover_art: bool = True
    full_bleed: bool = False
    image_size: str = "1024x1024"


BOOK_TYPE_DEFAULTS: dict[BookType, IBookTypeConfig] = {
    BookType.CHILDREN_PICTURE_BOOK: IBookTypeConfig(
        trim_size=TrimSize.SQUARE_8_5,
        paper_type=PaperType.COLOR,
        target_pages=32,
        chapter_count=(12, 16),
        words_per_chapter=(50, 150),
        scenes_per_chapter=(1, 1),
        illustrations_per_chapter=1,
        full_bleed=True,
        image_size="2048x2048",
    ),
    BookType.LIGHT_NOVEL: IBookTypeConfig(
        trim_size=TrimSize.PORTRAIT_5X8,
        paper_type=PaperType.BW_CREAM,
        target_pages=240,
        chapter_count=(8, 14),
        words_per_chapter=(3500, 6000),
        scenes_per_chapter=(4, 6),
        illustrations_per_chapter=0,
        image_size="2048x2048",
    ),
    BookType.NON_FICTION: IBookTypeConfig(
        trim_size=TrimSize.PORTRAIT_6X9,
        paper_type=PaperType.BW_WHITE,
        target_pages=180,
        chapter_count=(8, 12),
        words_per_chapter=(2000, 4000),
        scenes_per_chapter=(3, 5),
        illustrations_per_chapter=0,
        image_size="1024x1024",
    ),
    BookType.FICTION_NOVEL: IBookTypeConfig(
        trim_size=TrimSize.PORTRAIT_6X9,
        paper_type=PaperType.BW_CREAM,
        target_pages=320,
        chapter_count=(20, 30),
        words_per_chapter=(4000, 7000),
        scenes_per_chapter=(2, 4),
        illustrations_per_chapter=0,
        image_size="1024x1024",
    ),
}


def get_book_type_config(book_type: BookType) -> IBookTypeConfig:
    return BOOK_TYPE_DEFAULTS[book_type]


class IScene(BaseModel):
    """One scene/beat inside a chapter — atomic unit for writing and illustration."""

    index: int
    title: str = ""
    summary: str
    setting: str = ""
    characters: list[str] = Field(default_factory=list)
    mood: str = ""
    illustration_brief: str = ""


class IChapter(BaseModel):
    """A chapter outline — beats only at outline time, prose added during writing."""

    index: int
    title: str
    summary: str
    pov: str = ""
    target_word_count: int = 0
    scenes: list[IScene] = Field(default_factory=list)
    prose: str = ""


class ICharacter(BaseModel):
    """A character bible entry — face, hair, costume, palette, voice.

    Identity-locking sheet — every page-level prompt for this character
    points to the rendered reference image, NOT to these fields.
    """

    name: str
    role: str = ""
    age: str = ""
    appearance: str
    costume: str = ""
    palette: list[str] = Field(default_factory=list)
    personality: str = ""
    voice: str = ""
    arc: str = ""


class ILocation(BaseModel):
    name: str
    description: str
    palette: list[str] = Field(default_factory=list)
    mood: str = ""


class IStyleGuide(BaseModel):
    """Cross-cutting visual + tonal style — fed into every prompt."""

    art_style: str
    palette: list[str] = Field(default_factory=list)
    line_weight: str = ""
    lighting: str = ""
    tone: str = ""
    inspirations: list[str] = Field(default_factory=list)


class IBookConcept(BaseModel):
    """Top-level concept produced by `ConceptAgent`."""

    title: str
    subtitle: str = ""
    hook: str
    audience: str
    reading_level: str = ""
    tone: str
    target_word_count: int
    target_chapter_count: int
    comparable_titles: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)


class IBookOutline(BaseModel):
    """Full chapter-level outline produced by `OutlineAgent`."""

    chapters: list[IChapter]


class IBookBible(BaseModel):
    """Characters + locations + style guide produced by `BibleAgent`."""

    characters: list[ICharacter]
    locations: list[ILocation]
    style_guide: IStyleGuide


class IChapterDraft(BaseModel):
    """A single drafted chapter — produced by `WriterAgent`."""

    index: int
    title: str
    prose: str
    word_count: int = 0
    notes: list[str] = Field(default_factory=list)


class IManuscript(BaseModel):
    """All chapter drafts assembled — produced after `step_write`."""

    chapters: list[IChapterDraft] = Field(default_factory=list)
    total_word_count: int = 0


class IEditorIssue(BaseModel):
    """One concrete issue raised by the editor agent."""

    chapter_index: int | None = None
    severity: str = "minor"  # blocker | important | minor
    category: str = ""  # consistency | voice | pacing | grammar | plot | factual
    note: str


class IEditorReport(BaseModel):
    """Editor agent verdict — produced by `step_edit`."""

    score: int  # 1..10
    summary: str
    strengths: list[str] = Field(default_factory=list)
    issues: list[IEditorIssue] = Field(default_factory=list)
    chapters_to_revise: list[int] = Field(default_factory=list)


class IIllustrationBrief(BaseModel):
    """Composition-only image brief for one scene — Phase 5."""

    chapter_index: int
    scene_index: int
    page_index: int = 0
    composition: str
    camera: str = ""
    pose: str = ""
    action: str = ""
    lighting: str = ""
    mood: str = ""
    characters_present: list[str] = Field(default_factory=list)
    location: str = ""


class IPageImage(BaseModel):
    """One rendered page/scene image — Phase 5."""

    chapter_index: int
    scene_index: int
    page_index: int = 0
    image_path: str
    sidecar_path: str = ""


class ICoverDesign(BaseModel):
    """Cover concept produced by CoverAgent — Phase 6."""

    front_prompt: str
    back_prompt: str = ""
    spine_text: str = ""
    typography_notes: str = ""
    palette: list[str] = Field(default_factory=list)
    front_image_path: str = ""
    back_image_path: str = ""
    composed_path: str = ""


class IQualityIssue(BaseModel):
    chapter_index: int | None = None
    page_index: int | None = None
    severity: str = "minor"
    category: str = ""
    note: str


class IQualityReport(BaseModel):
    """Final QualityAgent verdict — Phase 8."""

    score: int  # 1..10
    summary: str
    blockers: list[IQualityIssue] = Field(default_factory=list)
    concerns: list[IQualityIssue] = Field(default_factory=list)


class IBookMetadata(BaseModel):
    """KDP listing metadata — produced by `MetadataAgent` (Phase 8)."""

    title: str
    subtitle: str = ""
    author: str = "Anonymous"
    blurb: str
    keywords: list[str] = Field(default_factory=list, max_length=7)
    bisac_categories: list[str] = Field(default_factory=list, max_length=3)
    series_name: str = ""
    series_index: int | None = None
    age_range: str = ""
    language: str = "en"
    ai_disclosure: bool = True


class IBookConfig(BaseModel):
    """Per-run configuration — what the user asked for."""

    topic: str
    book_type: BookType
    type_config: IBookTypeConfig
    author: str = "Anonymous"
    language: str = "en"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IBookState(BaseModel):
    """Root checkpoint payload — every step accepts and returns one of these."""

    slug: str
    book_dir: str
    config: IBookConfig
    concept: IBookConcept | None = None
    outline: IBookOutline | None = None
    bible: IBookBible | None = None
    manuscript: IManuscript | None = None
    editor_report: IEditorReport | None = None
    illustrations: list[IIllustrationBrief] = Field(default_factory=list)
    images: list[IPageImage] = Field(default_factory=list)
    cover: ICoverDesign | None = None
    metadata: IBookMetadata | None = None
    quality_report: IQualityReport | None = None
    completed_steps: list[str] = Field(default_factory=list)
    written_chapter_indices: list[int] = Field(default_factory=list)
    skip_images: bool = False

    def mark_done(self, step: str) -> None:
        if step not in self.completed_steps:
            self.completed_steps.append(step)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
