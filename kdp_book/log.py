"""Structured logging with optional per-run file handler.

Skeleton — full implementation in Phase 0. Mirrors deep-research/log.py and
mangas/log.py: colored console output, contextvar-scoped `run_id`, and an
attachable `FileHandler` per book run that lands at
`books/<slug>/logs/pipeline-<run-id>.log`.
"""

from __future__ import annotations

# Implementation in Phase 0:
#
#   def new_run_id() -> str: ...
#   def attach_file_handler(book_dir: Path) -> None: ...
#   def detach_file_handler() -> None: ...
#   log = logging.getLogger("kdp_book")
