"""Atomic file I/O — write to .tmp then rename, never expose half-written files."""

from __future__ import annotations

from pathlib import Path


def atomic_write_text(path: str | Path, content: str, encoding: str = "utf-8") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(p)
    return p


def atomic_write_bytes(path: str | Path, content: bytes) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(p)
    return p
