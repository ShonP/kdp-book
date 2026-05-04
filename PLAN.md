# kdp-book — Implementation Plan

> Phased plan to build the system designed in [`../DESIGN.md`](../DESIGN.md).
> Each phase ends with a usable, demoable artifact and a clean `git commit`.
> No phase modifies a finished artifact from a previous phase except to extend it.

---

## 0. Principles

- **Resumable by default.** The pipeline is one MAF `@workflow` composed of
  `@step` functions. State lives in `IBookState`. `FileCheckpointStorage`
  persists state after every step at
  `books/<slug>/.checkpoints/`. Re-running `kdp-book generate` for the same
  slug picks up exactly where the last run died.
- **One agent, one file, one `response_format`.** Every agent returns a
  Pydantic model — no free-form strings cross step boundaries.
- **Per-step CLI verbs.** Every `@step` is also reachable as
  `kdp-book step <slug> <step_name>` so we can re-run individual stages
  without re-running cheap steps (and never re-running gpt-image-2).
- **Strategy-pattern on `BookType`.** `children-picture-book`,
  `light-novel`, `non-fiction`, `fiction-novel` differ only in
  configuration objects, not in pipeline shape.
- **Visual consistency = mangas asset registry.** We port
  `mangas/manga_studio/tools/asset_registry.py` and the multi-image
  `gpt-image-2` edit pattern verbatim. No re-invention.
- **TDD where the cost is real.** Unit tests for KDP math (margins, spine,
  bleed), Pydantic schemas, and registry invalidation. Smoke tests with
  recorded LLM/image responses for the full pipeline. Live LLM/image tests
  are opt-in (`pytest -m image`) to avoid burning Azure credits on CI.

---

## 1. Pipeline shape

```
kdp-book generate --topic "…" --type <book-type>
        │
        ▼
  ┌─────────────────────────  @workflow book_workflow ──────────────────────────┐
  │                                                                              │
  │  @step concept    →  @step outline   →  @step bible      →  @step write     │
  │  @step edit       →  @step illustrate                       (image briefs)  │
  │  @step characters    (asset registry: ref-sheet per character)              │
  │  @step images        (gpt-image-2 ref-edits, parallel render)               │
  │  @step consistency   (vision review → regenerate flagged pages)             │
  │  @step cover         (front + spine + back, KDP wrap math)                  │
  │  @step format        (PDF interior, PDF cover, EPUB)                        │
  │  @step metadata      (title/blurb/keywords/categories)                      │
  │  @step quality       (final pass: originality, KDP compliance, score)      │
  │  @step publish       (zip + optional Azure Blob upload)                     │
  │                                                                              │
  └─────────────────  FileCheckpointStorage(books/<slug>/.checkpoints) ──────────┘
```

State is a single `IBookState` dict. Each `@step` accepts the previous
state, mutates one field, and returns the new state. A step is idempotent:
re-running it with the same input produces the same output (the only
exception is image generation, which is gated by an explicit `--regenerate`
flag).

The slug is `slugify(concept.title) + '-' + ulid()` so re-running the same
topic always produces a fresh slug unless the user explicitly resumes.

---

## 2. Resumability — how `@workflow`/`@step` + `FileCheckpointStorage` work

The pattern is the one used by `deep-research`:

```python
# kdp_book/workflow/pipeline.py
from agent_framework import FileCheckpointStorage, step, workflow

@step
async def step_concept(state: IBookState) -> IBookState: ...

@step
async def step_outline(state: IBookState) -> IBookState: ...

@workflow(name="kdp_book")
async def book_workflow(input_data: dict) -> str:
    state = IBookState.from_input(input_data)
    state = await step_concept(state)
    state = await step_outline(state)
    state = await step_bible(state)
    state = await step_write(state)
    state = await step_edit(state)
    state = await step_illustrate(state)
    state = await step_characters(state)
    state = await step_images(state)
    state = await step_consistency(state)
    state = await step_cover(state)
    state = await step_format(state)
    state = await step_metadata(state)
    state = await step_quality(state)
    return await step_publish(state)
```

Entry point:

```python
# kdp_book/workflow/pipeline.py
async def run_book_async(topic: str, book_type: BookType, *, resume: str | None) -> None:
    book_dir = resolve_book_dir(topic, book_type, resume=resume)
    storage  = FileCheckpointStorage(book_dir / ".checkpoints")
    await book_workflow.run(
        {"topic": topic, "book_type": book_type.value, "book_dir": str(book_dir)},
        checkpoint_storage=storage,
    )
```

Behaviour:

| Scenario | What MAF does |
|----------|---------------|
| First run, all steps succeed | Each step writes a checkpoint after it returns |
| Crash mid-step (e.g. gpt-image-2 timeout) | On rerun with same input, MAF skips completed steps and resumes from the failed step |
| Manual rerun of one step | `kdp-book step <slug> images --force` deletes checkpoints from `images` onward and reruns |
| Schema change in `IBookState` | `kdp-book step <slug> validate` re-loads the most recent checkpoint and surfaces missing fields |

In addition to MAF's native checkpoint, every step writes a denormalised
artifact under `books/<slug>/`:

```
books/the-boy-who-loved-chocolate-01J…/
├── .checkpoints/             # MAF FileCheckpointStorage
├── concept.json              # IConcept
├── outline.json              # IOutline
├── bible.json                # ICharacter[], ILocation[]
├── manuscript.json           # IManuscript
├── illustration_briefs.json  # IIllustrationBrief[]
├── manifest.json             # IAssetManifest (mangas-format)
├── images/                   # gpt-image-2 outputs
│   ├── characters/<name>/<variant>.png
│   ├── pages/page-001.png …
│   └── cover/{front,back,spine}.png
├── pages/                    # rendered PDF page tiles (debug)
├── kdp/
│   ├── interior.pdf          # KDP-ready interior
│   ├── cover.pdf             # KDP wrap cover
│   ├── book.epub             # reflowable EPUB
│   └── kdp.json              # metadata bundle for upload
├── logs/
│   └── pipeline-<run-id>.log
└── reports/
    └── quality.json
```

These denormalised files are the source of truth for `kdp-book` *commands
that aren't running the workflow* (`format`, `cover`, `metadata`,
`publish`) — they're idempotent over the on-disk state.

---

## 3. Character consistency — the mangas method, verbatim

DESIGN.md §5 covers the theory. Here's exactly what we build, in order, and
which mangas file each piece is ported from.

### 3.1 Asset manifest (Phase 6)

Port `mangas/manga_studio/tools/asset_registry.py` →
`kdp_book/tools/asset_registry.py`.

```jsonc
// books/<slug>/manifest.json
{
  "characters": {
    "Tomo": {
      "default_variant": "default",
      "variants": {
        "default": {
          "path": "images/characters/Tomo/default.png",
          "prompt": "...identity-locking sheet prompt...",
          "refs": [],
          "chain_from": null,
          "stale": false
        },
        "smiling": {
          "path": "images/characters/Tomo/smiling.png",
          "prompt": "...delta from default: smiling, eyes closed...",
          "refs": ["images/characters/Tomo/default.png"],
          "chain_from": "default",
          "stale": false
        }
      }
    }
  },
  "locations": { /* same shape */ },
  "props":     { /* same shape */ }
}
```

Rules (verbatim from mangas):

- Every variant inherits identity from `chain_from` by being generated as a
  `gpt-image-2` **edit** with `chain_from` as a reference.
- Regenerating a `default` marks every variant whose `chain_from` is that
  default `stale: true` via `invalidate_dependents()`.
- Every prompt that references a character or location includes the
  variant's resolved reference image — identity is in pixels, not words.

### 3.2 Reference sheet generation (Phase 6)

For each character / major location:

1. `BibleAgent` produces an identity card (face, hair, build, costume,
   colour palette).
2. `ImageAgent` calls `generate_image()` with **only** the identity card
   (no scene / pose / camera) — output is the canonical reference.
3. `manifest.json` records the reference; everything downstream uses
   `edit_image(prompt, refs=[...])` against this reference.

### 3.3 Page generation (Phase 6)

Per page:

```python
refs = []
for entity in scene.entities:                          # characters + props
    refs.append(asset_registry.resolve(entity).path)
refs.append(style_guide_reference)                     # 1 fixed style ref
prompt = prompt_builder.scene(                         # composition only —
    scene,                                             # camera, pose,
    book_type=book_type,                               # action, lighting
)
image = await image_client.edit_image(
    prompt=prompt, refs=refs[:8], size=size, quality=quality, input_fidelity="high"
)
```

Hard rules carried over from mangas:

- **Never** describe the character's face / hair / costume in the page
  prompt. Identity is in `refs`. Description in prompt fights with refs and
  loses fidelity.
- **Never** composite refs side-by-side into one image. `gpt-image-2` accepts
  multi-image edits natively (up to ~8 images).
- Multi-character scenes differentiate by **pose / costume / spatial
  position**, not facial features.

### 3.4 Safety filter retry ladder (Phase 6)

Port `mangas/manga_studio/tools/safety_filter.py`:

```python
attempt 1: full prompt + refs
attempt 2: softer wording, refs
attempt 3: gentler still, refs
…
attempt N (last): no refs, defanged prompt — cosmetic placeholder
```

`MAX_SAFETY_RETRIES` defaults to 5; the ladder is configured in
`tools/safety_filter.py`.

### 3.5 Vision-review loop (Phase 7)

`ConsistencyAgent` runs **after** all pages are generated. It uses the same
bypass-MAF helper as mangas (`tools/vision_client.py`) because MAF doesn't
forward image attachments. Inputs: every rendered page + the relevant
character refs. Output: `IQualityReport` with
`pages_to_regenerate: list[int]`. The workflow loops back to `step_images`
for those page indices only, up to `--max-review-rounds` (default 2).

---

## 4. Phases

Each phase ends with: **one runnable command**, **one `git commit`**, and a
**short demo**. Tests in each phase are listed; the goal is the test suite
grows monotonically.

> **Status (autopilot run):** Phases 0–9 implemented and pushed to
> `ShonP/kdp-book`. The full pipeline runs end-to-end. Live-verified
> through `outline` + `--no-images` smoke; image rendering remains
> dependent on Azure `gpt-image-2` access.

### Phase 0 — Foundations *(no agents, no images)*

**Goal.** `uv run kdp-book doctor` validates the environment.

**Files.**
- `kdp_book/log.py` — colored console + per-run file handler.
- `kdp_book/middleware.py` — `llm_call_logging`, `TokenUsage`.
- `kdp_book/config.py` — already stubbed; finish field validation.
- `kdp_book/client.py` — `get_chat_client()`, `get_vision_client()`.
- `kdp_book/agents/base.py` — `create_agent()` factory: middleware,
  schema-suffix, file-tool deny.
- `kdp_book/tools/atomic_io.py` — `write_json_atomic`, `read_json`,
  filelock-backed.
- `kdp_book/tools/file_tools.py` — deny-list permission handler.
- `kdp_book/models/state.py` — `IBookState` skeleton.
- `kdp_book/models/book_type.py` — `BookType` enum + per-type config dict.

**CLI.** `kdp-book doctor`, `kdp-book --version`.

**Tests.**
- `tests/unit/test_config.py` — `.env` loading, missing key warns not crashes.
- `tests/unit/test_atomic_io.py` — concurrent write, no torn writes.
- `tests/unit/test_book_type.py` — every `BookType` resolves to a config.

**Exit criteria.**
- `uv sync` succeeds.
- `uv run kdp-book doctor` reports green/red per env var.
- `uv run pytest -q` passes.

---

### Phase 1 — Concept + Outline + Bible *(text only, no images, no PDF)*

**Goal.** `kdp-book outline --topic "..." --type ...` produces and persists
`concept.json`, `outline.json`, `bible.json`.

**Files.**
- `kdp_book/agents/concept.py`
- `kdp_book/agents/outline.py`
- `kdp_book/agents/bible.py`
- `kdp_book/models/{concept,outline,bible}.py`
- `kdp_book/workflow/pipeline.py` — wire `step_concept`, `step_outline`,
  `step_bible`, `book_workflow`, `run_book_async`.
- `kdp_book/workflow/pipeline_steps.py` — pure helpers per step.
- `kdp_book/workflow/state.py` — load/save `IBookState` to `book.json`.
- `kdp_book/workflow/strategies.py` — `BookType` strategy dispatcher
  (controls chapter count, scenes-per-chapter, tone defaults).

**Prompts (sketch — full text in agent files).**
- **ConceptAgent.** Input: topic, book_type. Output: title, subtitle,
  one-paragraph hook, target audience age + reading level, tone, expected
  word count, expected chapter count, three comparable titles.
- **OutlineAgent.** Input: `IConcept`. Output: chapter list, each with
  beats / scenes (count varies by book_type — picture book = 1 spread per
  chapter; light novel = 4–6 scenes per chapter).
- **BibleAgent.** Input: `IConcept` + `IOutline`. Output: 3–8 named
  characters with identity cards (face, hair, build, costume, palette,
  voice), 2–6 named locations, a cross-cutting style guide
  (genre tone, palette, line weight, lighting). The style guide is the
  one input shared by every later prompt.

**CLI.**
- `kdp-book outline --topic STR --type CHOICE [--out books/<slug>/]`
- `kdp-book step <slug> {concept|outline|bible} [--force]`
- `kdp-book status <slug>` — prints the pipeline progress + last
  checkpoint.

**Tests.**
- `tests/unit/test_concept_schema.py`, etc. — Pydantic round-trips.
- `tests/unit/test_strategy.py` — picture-book outline has ≤ 16 chapters,
  light-novel ≥ 6.
- `tests/smoke/test_outline_resume.py` — kill after `step_concept`,
  resume, the outline+bible run, no rerun of `concept`.

**Exit criteria.** Every book type produces a sensible outline + bible
under 30s on average for a fresh topic. Resume works.

---

### Phase 2 — Writer + Editor *(prose, still no images, no PDF)*

**Goal.** A finished `manuscript.json` per book.

**Files.**
- `kdp_book/agents/writer.py` — chapter-by-chapter, structured output
  (paragraphs + scene boundaries). Streams via MAF, but stores the final
  per-chapter result.
- `kdp_book/agents/editor.py` — single pass over the assembled manuscript:
  fixes character-name drift, pacing notes, plot-hole flags. Returns
  `IManuscript` with `editor_notes` plus revised chapters where edits are
  applied.
- `kdp_book/models/manuscript.py` — `IManuscript`, `IChapterDraft`,
  `IScene`, `IEditorNote`.
- `kdp_book/workflow/pipeline_steps.py` — add `step_write`, `step_edit`.
- `kdp_book/workflow/pipeline.py` — extend `book_workflow`.

**Per-book-type knobs.**
| Type | Words / chapter | Tone | Editor strictness |
|------|-----------------|------|-------------------|
| children-picture-book | 50–150 | rhyming or rhythmic, simple vocabulary | high — vocab gate |
| light-novel | 3500–6000 | first-person, dialogue-heavy | medium |
| non-fiction | 2000–4000 | declarative, code blocks allowed | medium |
| fiction-novel | 4000–7000 | third-person, scene-driven | high |

**CLI.**
- `kdp-book write --from <slug>`
- `kdp-book step <slug> {write|edit}`

**Tests.**
- `tests/unit/test_manuscript_schema.py`
- `tests/smoke/test_writer_word_count.py` — recorded responses, asserts
  per-type word-count bands.

**Exit criteria.** Manuscript produced for all four book types from a
fixed prompt seed. Resume works.

---

### Phase 3 — Format: KDP-ready interior PDF + EPUB *(text-only book is shippable)*

**Goal.** A typeset, KDP-compliant interior PDF (no images yet, just
typography) and an EPUB. We can already submit this as a text-only book.

**Files.**
- `kdp_book/formats/margins.py` — KDP gutter/margin lookup table (port
  from `kdp-book-generator/src/config/book-config.ts`).
- `kdp_book/formats/trim_sizes.py` — catalog of accepted trims (6×9,
  5.5×8.5, 5×8, 8.5×11, 8.5×8.5, 7×10, 7.5×9.25), with bleed math.
- `kdp_book/formats/typography.py` — per-`BookType` font + leading + drop
  cap rules.
- `kdp_book/formats/pdf_interior.py` — reportlab; mirror margins, gutter
  by page count, running heads, page numbers, recto chapter starts,
  optional bleed pages.
- `kdp_book/formats/epub_builder.py` — ebooklib; chapter splits, TOC, no
  pixel-pinned styling.
- `kdp_book/agents/frontmatter.py` — title page, copyright, dedication,
  TOC entries.
- `kdp_book/agents/backmatter.py` — about-the-author, also-by, AI-content
  disclosure.
- `kdp_book/workflow/pipeline_steps.py` — `step_frontmatter`,
  `step_backmatter`, `step_format`.

**KDP math (encoded, tested, frozen).**
- Gutter by page count: 24 → 0.375", 25–150 → 0.5", 151–300 → 0.625",
  301–500 → 0.75", 501+ → 0.875".
- Outer margin minimum 0.25" (we use 0.5" for type quality).
- Bleed = 0.125" all sides if any page bleeds.
- Min image DPI = 300.
- Min page count = 24 (we'll error out below this).

**CLI.**
- `kdp-book format --from <slug> --output {pdf|epub|both}`
- `kdp-book step <slug> format`

**Tests.**
- `tests/unit/test_margins.py` — exhaustive table of (page_count → gutter).
- `tests/unit/test_trim_sizes.py` — bleed dimensions correct.
- `tests/smoke/test_pdf_interior.py` — produces valid PDF, checks page
  count + margin via `pikepdf` or by reading geometry markers.
- `tests/smoke/test_epub_validates.py` — `epubcheck` not required (large
  Java dep); we self-validate ToC, content.opf, spine.

**Exit criteria.** We can submit a non-fiction text-only book to KDP from
this output. Picture book interior produces correctly even without final
images (uses placeholder boxes at correct DPI).

---

### Phase 4 — Cover *(text-only cover is shippable)*

**Goal.** KDP wrap cover PDF: front + spine + back at 0.125" bleed, with
spine width derived from page count and paper type.

**Files.**
- `kdp_book/agents/cover.py` — composes title, subtitle, author, blurb,
  back cover layout. Returns `ICoverDesign`.
- `kdp_book/models/cover.py` — `ICoverDesign`, `ICoverDimensions`.
- `kdp_book/formats/pdf_cover.py` — reportlab cover renderer.

**Spine math (encoded, tested, frozen).**
| Paper | inches per page |
|-------|-----------------|
| BW white | 0.002252 |
| BW cream | 0.0025 |
| Color    | 0.002347 |

`cover_width = 0.125" + back_trim + spine + front_trim + 0.125"`
`cover_height = 0.125" + trim_height + 0.125"`
Spine text only renders when `pages ≥ 80` (KDP rule).

**CLI.**
- `kdp-book cover --from <slug>`
- `kdp-book step <slug> cover`

**Tests.**
- `tests/unit/test_spine.py` — paper × page-count grid.
- `tests/smoke/test_cover_pdf.py` — full text-cover renders, spine width
  matches expected.

**Exit criteria.** Cover renders for any page count ≥ 24 with text-only
artwork (image plate is filled with placeholder until Phase 6).

---

### Phase 5 — Metadata *(KDP listing is ready)*

**Goal.** A `kdp.json` package ready to paste into KDP.

**Files.**
- `kdp_book/agents/metadata.py` — title (≤ 200 chars), subtitle, blurb
  (≤ 4000 chars, marketing tone), 7 KDP keywords, 2 BISAC categories,
  series info, age range (children's books).
- `kdp_book/tools/kdp_metadata.py` — KDP keyword catalog + validators
  (forbidden words, length limits, duplicate-of-title check).
- `kdp_book/models/metadata.py` — `IKDPMetadata`.

**CLI.**
- `kdp-book metadata --from <slug>`
- `kdp-book step <slug> metadata`

**Tests.**
- `tests/unit/test_kdp_metadata_validators.py` — rejects forbidden
  keywords, oversized blurbs, duplicate keywords.
- `tests/smoke/test_metadata_for_each_type.py`.

**Exit criteria.** Output validates against the KDP form's hard limits.

---

### Phase 6 — Illustration + Image generation *(visual book)*

**Goal.** A fully illustrated picture book / light-novel insert spreads.

**Files.**
- `kdp_book/agents/illustration.py` — turns each scene into a
  composition-only image brief.
- `kdp_book/agents/image.py` — wraps the gpt-image-2 client with retry +
  safety ladder.
- `kdp_book/tools/image_client.py` — port of mangas
  `tools/image_client.py`: `generate_image`, `edit_image`,
  multipart, `input_fidelity=high`, multi-ref native.
- `kdp_book/tools/asset_registry.py` — port of mangas
  `tools/asset_registry.py`: manifest IO, variant chaining,
  `invalidate_dependents`, `resolve(entity, variant)`.
- `kdp_book/tools/safety_filter.py` — port of mangas
  `tools/safety_filter.py`: progressive prompt softening ladder.
- `kdp_book/tools/prompt_builder.py` — `identity_sheet(character)`,
  `scene(scene, refs)`, `cover_panel(...)`. Composition-only rules
  enforced here.
- `kdp_book/models/{illustration,assets}.py` — `IIllustrationBrief`,
  `IPageImage`, `IAssetEntry`, `IAssetManifest`.
- `kdp_book/workflow/pipeline_steps.py` — `step_illustrate`,
  `step_characters`, `step_images`.
- `kdp_book/formats/pdf_interior.py` — fill image plates with the real
  generated PNGs.
- `kdp_book/formats/pdf_cover.py` — fill front-cover panel.

**Per-book-type rendering.**
| Type | Pages illustrated | Style |
|------|-------------------|-------|
| children-picture-book | every spread | full-bleed, square 8.5×8.5, 2048×2048 |
| light-novel | 1 cover insert + 4–8 chapter inserts | manga, B&W with grey wash |
| non-fiction | diagrams + chapter openers | clean line, 1024×1024 |
| fiction-novel | cover + ~6 chapter heads | painterly, 1024×1024 |

**CLI.**
- `kdp-book illustrate --from <slug>` — runs `step_illustrate` +
  `step_characters` + `step_images` end-to-end.
- `kdp-book step <slug> {illustrate|characters|images} [--regenerate-pages 1,4,7]`

**Tests.**
- `tests/unit/test_asset_registry.py` — variant chaining, stale propagation.
- `tests/unit/test_safety_filter.py` — ladder progresses on detection.
- `tests/unit/test_prompt_builder.py` — composition-only rule (no facial
  details leaking into scene prompt).
- `tests/smoke/test_illustration_brief_only.py` — runs without hitting
  gpt-image-2.
- `tests/image/test_one_character_sheet.py` (`-m image`, opt-in).

**Exit criteria.** A children-picture-book renders end-to-end with
character-consistent illustrations across pages. Re-running `images` for a
single page works without disturbing others.

---

### Phase 7 — Consistency / vision review *(quality bar holds)*

**Goal.** A visual-review loop that flags inconsistent images and
regenerates them.

**Files.**
- `kdp_book/agents/consistency.py` — calls `tools/vision_client.py`
  (bypass-MAF), inputs every rendered page + relevant character refs,
  returns `IQualityReport` with per-page issues + score.
- `kdp_book/tools/vision_client.py` — port of mangas
  `tools/vision_client.py`: direct OpenAI SDK call with image
  attachments.
- `kdp_book/workflow/pipeline_steps.py` — `step_consistency` loops back
  into `step_images` for flagged pages, max
  `--max-review-rounds` (default 2).

**CLI.**
- `kdp-book step <slug> consistency`

**Tests.**
- `tests/unit/test_consistency_report_schema.py`.
- `tests/smoke/test_consistency_loop.py` — recorded inputs, simulated
  flagged pages → exactly those page indices regenerate.

**Exit criteria.** With a known-bad fixture (a swapped costume),
`consistency` flags it and `images` regenerates only that page.

---

### Phase 8 — Quality / KDP compliance *(publishable bar)*

**Goal.** A final pass that produces a quality report blocking publish if
it fails.

**Files.**
- `kdp_book/agents/quality.py` — final evaluator: reading-level vs target
  audience, originality (web search via existing tavily / ddgs path used
  in deep-research), AI-disclosure compliance, KDP keyword + category
  validity, page-count ≥ 24, image-DPI ≥ 300.
- `kdp_book/tools/originality.py` — web-search-backed "is this an obvious
  paraphrase" check.
- `kdp_book/models/review.py` — `IQualityReport`, `IIssue` (severity:
  block | warn | info).

**Block-list.**
- Page count < 24
- Cover spine math wrong
- Image DPI < 300
- Missing AI-content disclosure when AI-generated text/images are present
- Originality score < threshold

**CLI.**
- `kdp-book quality --from <slug>`
- `kdp-book step <slug> quality`

**Tests.**
- `tests/unit/test_quality_blockers.py` — every blocker triggers.
- `tests/smoke/test_quality_passes_picture_book.py`.

**Exit criteria.** A finished children-picture-book passes quality
unblocked.

---

### Phase 9 — End-to-end `kdp-book generate`

**Goal.** One command runs everything for any book type.

**Files.**
- `kdp_book/cli.py` — wire `generate`, `step`, `status`, `outline`,
  `write`, `illustrate`, `format`, `cover`, `metadata`, `quality`.
- `kdp_book/workflow/pipeline.py` — full `book_workflow` chain wired and
  resumable end-to-end.

**CLI.**
- `kdp-book generate --topic STR --type CHOICE [--resume SLUG] [--no-images] [--max-review-rounds N]`

**Tests.**
- `tests/smoke/test_generate_picture_book.py` — recorded responses,
  `--no-images`, asserts every artifact present and valid.
- `tests/smoke/test_resume.py` — kill after each step in turn, ensure
  every can resume.

**Exit criteria.** A user can run a single command and get a complete
KDP-ready book directory.

---

### Phase 10 — Publish (optional Azure Blob + future KDP automation)

**Goal.** `kdp-book publish` zips the deliverables and uploads to Azure
Blob Storage. Real KDP upload is out of scope (KDP has no public API);
the publish step writes a `kdp.json` and a `submit.md` that lists every
field to copy-paste.

**Files.**
- `kdp_book/agents/publish.py` (or just a step — no LLM needed).
- `kdp_book/workflow/pipeline_steps.py` — `step_publish`.

**CLI.**
- `kdp-book publish --from <slug> [--blob-container NAME]`

**Tests.**
- `tests/unit/test_publish_zip.py` — zip layout matches KDP expectations.

**Exit criteria.** A running `publish` produces a single zip containing
`interior.pdf`, `cover.pdf`, `book.epub`, `kdp.json`, `submit.md`, with
optional upload to Azure Blob.

---

## 5. CLI surface — final

```
kdp-book doctor
kdp-book generate    --topic STR --type {children-picture-book|light-novel|non-fiction|fiction-novel} [--resume SLUG] [--no-images] [--max-review-rounds N]
kdp-book status      <slug>
kdp-book outline     --topic STR --type CHOICE
kdp-book write       --from <slug>
kdp-book illustrate  --from <slug>
kdp-book format      --from <slug> --output {pdf|epub|both}
kdp-book cover       --from <slug>
kdp-book metadata    --from <slug>
kdp-book quality     --from <slug>
kdp-book publish     --from <slug>
kdp-book step        <slug> <step_name> [--force] [--regenerate-pages 1,4,7]
```

`step` is the universal escape hatch: every `@step` in the workflow is
addressable by name and can be re-run in isolation.

---

## 6. Things explicitly out of scope (YAGNI)

- Real KDP API upload (none exists publicly).
- Audiobook generation.
- Translation.
- Multi-author / collaboration.
- Multi-volume series management beyond a `series_name` field.
- Live web UI — `kdp-book` is a CLI-first tool. A FastAPI surface can be
  added later but is not on the path.
- Custom fine-tuned image models. `gpt-image-2` + reference images is
  enough for v1.

---

## 7. Open questions (parked, decide before that phase)

1. **EPUB validation strictness.** Do we ship `epubcheck` (Java) or
   self-validate? Decision: Phase 3.
2. **Originality check provider.** Tavily for parity with deep-research,
   or DDG fallback for free runs? Decision: Phase 8.
3. **Cover style transfer.** Do we generate the cover from a reference of
   the protagonist or from a separately-generated style sheet? Decision:
   Phase 4 / Phase 6 boundary.
4. **Chapter-illustration policy for non-fiction.** Decorative chapter
   openers, or only diagrams? Decision: Phase 6.
5. **Author identity.** Do we expose a configurable pen name + bio, or
   always "Anonymous"? Decision: Phase 5.

---

## 8. Definition of Done

A book run is "Done" when, for any of the four `BookType`s:

- `kdp-book generate --topic "..." --type ...` runs to completion with no
  unhandled exceptions.
- `books/<slug>/kdp/{interior.pdf, cover.pdf, book.epub, kdp.json}` exist
  and validate against KDP rules encoded in `formats/`.
- `books/<slug>/reports/quality.json` has `status == "pass"` and no
  blockers.
- Every step is **resumable**: deleting `books/<slug>/.checkpoints/<step>`
  and rerunning regenerates exactly that step's outputs and nothing else.
