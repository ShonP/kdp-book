"""Observability: per-step metadata, run aggregate, image sidecars.

Layout per book run:
    books/<slug>/
      metadata/
        01-concept.json   ← StepMetadataRecorder writes one per @step
        02-outline.json
        ...
      run_metadata.json   ← aggregate, updated after every step
      images/.../*.json   ← image sidecars (Phase 5)

Design
------
A `StepRecorder` is a context manager scoped to one `@step` invocation. It:
- captures wall-clock start/end + elapsed
- snapshots `TokenUsage` before and after
- collects every `record_prompt(...)` and `record_output(...)` made inside it
- on exit (success or failure), atomically writes the per-step JSON and
  updates `run_metadata.json`

Agents call `record_prompt(...)` / `record_output(...)` to log what was sent
and received. The chat middleware already updates `TokenUsage`; we just diff
it so a step's `tokens` field is exactly the tokens that step burned.
"""

from __future__ import annotations

import json
import re
import time
import traceback
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kdp_book.log import log
from kdp_book.middleware import get_token_usage

# gpt-5.5 pricing assumptions (USD per 1M tokens). Override via env later if needed.
COST_PER_1M_INPUT = 5.0
COST_PER_1M_OUTPUT = 15.0


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return round(
        (prompt_tokens / 1_000_000) * COST_PER_1M_INPUT
        + (completion_tokens / 1_000_000) * COST_PER_1M_OUTPUT,
        6,
    )


@dataclass
class _PromptRecord:
    agent_name: str
    model: str
    system: str
    user: str
    response_format: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class _OutputRecord:
    agent_name: str
    value: Any
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class _ImageRecord:
    """One image generation event recorded against the current step."""

    asset_type: str
    name: str
    path: str
    prompt: str
    references: list[str]
    model: str
    size: str
    quality: str
    retry_count: int = 0
    safety_filter_hits: int = 0
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class StepRecorder:
    """Captures metadata for one `@step` invocation."""

    book_dir: Path
    step_name: str
    step_index: int
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    duration_seconds: float = 0.0
    status: str = "running"
    error: str | None = None
    prompts: list[_PromptRecord] = field(default_factory=list)
    outputs: list[_OutputRecord] = field(default_factory=list)
    images: list[_ImageRecord] = field(default_factory=list)
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_total: int = 0
    estimated_cost_usd: float = 0.0
    retry_count: int = 0

    _start_monotonic: float = 0.0
    _tokens_baseline: tuple[int, int, int] = (0, 0, 0)
    _token: object = None

    def add_prompt(
        self,
        *,
        agent_name: str,
        model: str,
        system: str,
        user: str,
        response_format: str | None = None,
    ) -> None:
        self.prompts.append(
            _PromptRecord(
                agent_name=agent_name,
                model=model,
                system=system,
                user=user,
                response_format=response_format,
            )
        )

    def add_output(self, *, agent_name: str, value: Any) -> None:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        self.outputs.append(_OutputRecord(agent_name=agent_name, value=value))

    def add_image(self, image: _ImageRecord) -> None:
        self.images.append(image)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "step_index": self.step_index,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "tokens": {
                "prompt": self.tokens_prompt,
                "completion": self.tokens_completion,
                "total": self.tokens_total,
            },
            "estimated_cost_usd": self.estimated_cost_usd,
            "retry_count": self.retry_count,
            "error": self.error,
            "prompts": [p.__dict__ for p in self.prompts],
            "outputs": [o.__dict__ for o in self.outputs],
            "images": [i.__dict__ for i in self.images],
        }

    def __enter__(self) -> StepRecorder:
        self._start_monotonic = time.monotonic()
        usage = get_token_usage()
        self._tokens_baseline = (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
        self._token = _current_step.set(self)
        log.debug("Step %s started", self.step_name)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc, tb) -> bool:
        self.duration_seconds = time.monotonic() - self._start_monotonic
        self.completed_at = datetime.now(UTC).isoformat()
        usage = get_token_usage()
        bp, bc, bt = self._tokens_baseline
        self.tokens_prompt = max(0, usage.prompt_tokens - bp)
        self.tokens_completion = max(0, usage.completion_tokens - bc)
        self.tokens_total = max(0, usage.total_tokens - bt)
        self.estimated_cost_usd = estimate_cost(self.tokens_prompt, self.tokens_completion)

        if exc is not None:
            self.status = "failed"
            self.error = f"{exc_type.__name__}: {exc}\n{''.join(traceback.format_exception(exc_type, exc, tb))[:4000]}"
        else:
            self.status = "ok"

        try:
            self._persist()
        except Exception as persist_err:
            log.warning("Failed to write step metadata for %s: %s", self.step_name, persist_err)
        finally:
            if self._token is not None:
                _current_step.reset(self._token)  # type: ignore[arg-type]
                self._token = None
        return False  # don't swallow exceptions

    def _persist(self) -> None:
        meta_dir = Path(self.book_dir) / "metadata"
        slug = re.sub(r"[^a-z0-9_-]+", "-", self.step_name.lower())
        path = meta_dir / f"{self.step_index:02d}-{slug}.json"
        _atomic_write_json(path, self.to_dict())
        update_run_metadata(self.book_dir, self)


_current_step: ContextVar[StepRecorder | None] = ContextVar("current_step", default=None)
_step_counter: ContextVar[int] = ContextVar("step_counter", default=0)


def get_current_step() -> StepRecorder | None:
    return _current_step.get()


def step_recorder(book_dir: str | Path, step_name: str) -> StepRecorder:
    """Build a `StepRecorder` for a step. Use as a `with` block."""
    idx = _step_counter.get() + 1
    _step_counter.set(idx)
    return StepRecorder(book_dir=Path(book_dir), step_name=step_name, step_index=idx)


def reset_step_counter() -> None:
    _step_counter.set(0)


# ── run_metadata.json ─────────────────────────────────────────────────────────


def init_run_metadata(
    *,
    book_dir: str | Path,
    run_id: str,
    topic: str,
    book_type: str,
    slug: str,
) -> Path:
    """Create or refresh `<book_dir>/run_metadata.json` at the start of a run."""
    path = Path(book_dir) / "run_metadata.json"
    payload: dict[str, Any] = {
        "run_id": run_id,
        "slug": slug,
        "topic": topic,
        "book_type": book_type,
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "status": "running",
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
        "estimated_cost_usd": 0.0,
        "steps": [],
        "agents_used": [],
        "models_used": [],
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            # preserve completed step history across resumes
            payload["steps"] = existing.get("steps", [])
            payload["tokens"] = existing.get("tokens", payload["tokens"])
            payload["estimated_cost_usd"] = existing.get("estimated_cost_usd", 0.0)
            payload["agents_used"] = existing.get("agents_used", [])
            payload["models_used"] = existing.get("models_used", [])
            payload["started_at"] = existing.get("started_at", payload["started_at"])
        except Exception as e:
            log.warning("Could not read existing run_metadata.json (%s) — overwriting", e)
    _atomic_write_json(path, payload)
    return path


def update_run_metadata(book_dir: str | Path, recorder: StepRecorder) -> None:
    """Append a step's summary to `run_metadata.json` and roll up totals."""
    path = Path(book_dir) / "run_metadata.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Could not read run_metadata.json (%s)", e)
        return

    summary = {
        "step_name": recorder.step_name,
        "step_index": recorder.step_index,
        "status": recorder.status,
        "duration_seconds": round(recorder.duration_seconds, 3),
        "tokens": {
            "prompt": recorder.tokens_prompt,
            "completion": recorder.tokens_completion,
            "total": recorder.tokens_total,
        },
        "estimated_cost_usd": recorder.estimated_cost_usd,
        "started_at": recorder.started_at,
        "completed_at": recorder.completed_at,
        "error": recorder.error,
        "image_count": len(recorder.images),
    }

    steps = [s for s in payload.get("steps", []) if s.get("step_index") != recorder.step_index]
    steps.append(summary)
    steps.sort(key=lambda s: s.get("step_index", 0))
    payload["steps"] = steps

    payload["tokens"]["prompt"] += recorder.tokens_prompt
    payload["tokens"]["completion"] += recorder.tokens_completion
    payload["tokens"]["total"] += recorder.tokens_total
    payload["estimated_cost_usd"] = round(
        payload.get("estimated_cost_usd", 0.0) + recorder.estimated_cost_usd, 6
    )

    for prompt in recorder.prompts:
        if prompt.agent_name and prompt.agent_name not in payload.setdefault("agents_used", []):
            payload["agents_used"].append(prompt.agent_name)
        if prompt.model and prompt.model not in payload.setdefault("models_used", []):
            payload["models_used"].append(prompt.model)

    _atomic_write_json(path, payload)


def finalize_run_metadata(book_dir: str | Path, *, status: str = "ok") -> None:
    """Mark the run as finished."""
    path = Path(book_dir) / "run_metadata.json"
    if not path.exists():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = status
    payload["completed_at"] = datetime.now(UTC).isoformat()
    _atomic_write_json(path, payload)


# ── image sidecar ─────────────────────────────────────────────────────────────


def make_image_record(
    *,
    asset_type: str,
    name: str,
    path: str | Path,
    prompt: str,
    references: list[str | Path],
    model: str,
    size: str,
    quality: str,
    retry_count: int = 0,
    safety_filter_hits: int = 0,
    duration_seconds: float = 0.0,
) -> _ImageRecord:
    return _ImageRecord(
        asset_type=asset_type,
        name=name,
        path=str(path),
        prompt=prompt,
        references=[str(r) for r in references],
        model=model,
        size=size,
        quality=quality,
        retry_count=retry_count,
        safety_filter_hits=safety_filter_hits,
        duration_seconds=duration_seconds,
    )


def write_image_sidecar(image_path: str | Path, record: _ImageRecord) -> Path:
    """Write `<image>.json` next to the rendered image."""
    p = Path(image_path)
    sidecar = p.with_suffix(p.suffix + ".json") if p.suffix else p.with_suffix(".json")
    payload = record.__dict__
    _atomic_write_json(sidecar, payload)
    return sidecar


# ── helpers used by agents to log prompts/outputs ─────────────────────────────


def record_prompt(
    *,
    agent_name: str,
    model: str,
    system: str,
    user: str,
    response_format: str | None = None,
) -> None:
    rec = get_current_step()
    if rec is None:
        return
    rec.add_prompt(
        agent_name=agent_name,
        model=model,
        system=system,
        user=user,
        response_format=response_format,
    )


def record_output(*, agent_name: str, value: Any) -> None:
    rec = get_current_step()
    if rec is None:
        return
    rec.add_output(agent_name=agent_name, value=value)


def record_image(record: _ImageRecord) -> None:
    rec = get_current_step()
    if rec is None:
        return
    rec.add_image(record)


__all__ = [
    "COST_PER_1M_INPUT",
    "COST_PER_1M_OUTPUT",
    "StepRecorder",
    "estimate_cost",
    "finalize_run_metadata",
    "get_current_step",
    "init_run_metadata",
    "make_image_record",
    "record_image",
    "record_output",
    "record_prompt",
    "reset_step_counter",
    "step_recorder",
    "update_run_metadata",
    "write_image_sidecar",
]
