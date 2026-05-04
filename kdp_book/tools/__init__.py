"""Tools — non-LLM helpers used by agents and pipeline steps.

Modules planned (see PLAN.md for phase mapping):
    image_client.py   → gpt-image-2 wrapper: generate_image / edit_image      (Phase 6)
    vision_client.py  → bypass-MAF helper for vision review (multi-image)     (Phase 7)
    asset_registry.py → manifest.json + variant chaining (port from mangas)   (Phase 6)
    safety_filter.py  → progressive prompt softening + retry ladder           (Phase 6)
    prompt_builder.py → reusable identity / scene / cover prompt assemblers   (Phase 6)
    atomic_io.py      → atomic JSON / file writes (filelock-backed)           (Phase 0)
    file_tools.py     → deny-list permission handler injected into agents     (Phase 0)
    kdp_metadata.py   → KDP keyword + category catalog + validators           (Phase 5)
    originality.py    → web-search-backed originality / fact-check scaffolding (Phase 8)
"""
