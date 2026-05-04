"""Agents — one module per pipeline role.

Each agent is a thin wrapper around `agent_framework.Agent` (or the OpenAI
chat client) with:
    - a system prompt
    - a Pydantic `response_format`
    - the shared middleware stack from `kdp_book.middleware`

Modules planned (see PLAN.md for which phase each lands in):
    concept.py        → ConceptAgent       (Phase 1)
    outline.py        → OutlineAgent       (Phase 1)
    bible.py          → BibleAgent         (Phase 1) — character/world bible, fed into every downstream prompt
    writer.py         → WriterAgent        (Phase 2) — chapter-by-chapter prose, structured output per scene
    editor.py         → EditorAgent        (Phase 2) — pass over manuscript, fix continuity / pacing
    illustration.py   → IllustrationAgent  (Phase 6) — turns scenes into image briefs
    image.py          → ImageAgent         (Phase 6) — calls gpt-image-2 (text-to-image / ref-edit)
    consistency.py    → ConsistencyAgent   (Phase 7) — vision review loop with regenerate hints
    cover.py          → CoverAgent         (Phase 4) — front + spine + back assembly
    frontmatter.py    → FrontmatterAgent   (Phase 3)
    backmatter.py     → BackmatterAgent    (Phase 3)
    metadata.py       → MetadataAgent      (Phase 5) — title, blurb, KDP keywords + categories
    quality.py        → QualityAgent       (Phase 8) — final pass: originality, fact-check, KDP compliance
    base.py           → `create_agent()`   (Phase 0) — factory that injects middleware + JSON-schema suffix
"""
