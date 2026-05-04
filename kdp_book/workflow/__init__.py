"""MAF workflow — the resumable pipeline.

`pipeline.py` defines the linear `@workflow` that orchestrates every step.
`pipeline_steps.py` holds the actual `@step` functions (one per pipeline
stage). State lives in `IBookState` from `kdp_book.models.state` and is
checkpointed by `FileCheckpointStorage` to `books/<slug>/.checkpoints/`.

The pipeline is **resumable**: any step can crash and be retried by
re-invoking the workflow with the same input — MAF will replay from the last
checkpoint rather than rerunning completed steps.

Modules planned:
    pipeline.py        → `book_workflow` + `run_book()` entry point    (Phase 1+)
    pipeline_steps.py  → step_concept, step_outline, step_bible,
                         step_write, step_edit, step_illustrate,
                         step_image, step_consistency, step_cover,
                         step_format, step_metadata, step_quality,
                         step_publish                                   (Phases 1–10)
    state.py           → on-disk state I/O (book.json, manifest.json)   (Phase 0)
    strategies.py      → BookType-strategy dispatcher                   (Phase 1)
"""
