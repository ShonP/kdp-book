"""CLI for `kdp-book`.

Subcommands match `PLAN.md` §5. Subcommands not yet implemented print a
clear "Phase N" notice instead of crashing.
"""

from __future__ import annotations

import asyncio
import json
import warnings

warnings.filterwarnings("ignore", category=Warning, module="agent_framework.*")

import click  # noqa: E402

from kdp_book.config import get_settings  # noqa: E402
from kdp_book.log import attach_file_handler, detach_file_handler, log, new_run_id  # noqa: E402
from kdp_book.models.book import BookType  # noqa: E402
from kdp_book.observability import (  # noqa: E402
    finalize_run_metadata,
    init_run_metadata,
    reset_step_counter,
    step_recorder,
)
from kdp_book.workflow.pipeline import run_book  # noqa: E402
from kdp_book.workflow.state import load_state, resolve_book_dir, save_state  # noqa: E402
from kdp_book.workflow.steps import do_bible, do_concept, do_outline  # noqa: E402

BOOK_TYPE_CHOICES = [t.value for t in BookType]


@click.group()
@click.version_option(package_name="kdp-book")
def main() -> None:
    """kdp-book — generate KDP-ready books with AI agents."""


# ── doctor ────────────────────────────────────────────────────────────────────

@main.command()
def doctor() -> None:
    """Validate the local environment (env vars, output dir)."""
    s = get_settings()
    checks = [
        ("AZURE_API_KEY / OPENAI_API_KEY", bool(s.azure_api_key or s.openai_api_key)),
        ("OPENAI_BASE_URL", bool(s.openai_base_url)),
        ("AZURE_IMAGE_ENDPOINT", bool(s.azure_image_endpoint)),
        ("AZURE_IMAGE_EDIT_ENDPOINT", bool(s.azure_image_edit_endpoint)),
        ("Books directory", s.kdp_books_dir.parent.exists() or s.kdp_books_dir.exists()),
    ]
    failed = 0
    for name, ok in checks:
        click.echo(f"  {'✓' if ok else '✗'}  {name}")
        if not ok:
            failed += 1
    click.echo()
    click.echo(f"Model: {s.copilot_model}  |  Image size: {s.image_size}  |  Quality: {s.image_quality}")
    click.echo(f"Books dir: {s.kdp_books_dir.resolve()}")
    if failed:
        click.echo(click.style(f"\n{failed} check(s) failed", fg="red"))
        raise SystemExit(1)
    click.echo(click.style("\nAll checks passed.", fg="green"))


# ── generate ──────────────────────────────────────────────────────────────────

@main.command()
@click.option("--topic", required=True, help="Topic / premise of the book.")
@click.option("--type", "book_type", required=True, type=click.Choice(BOOK_TYPE_CHOICES), help="Book type.")
@click.option("--resume", default=None, help="Slug of an existing book directory to resume.")
@click.option("--author", default=None, help="Author/pen name. Defaults to KDP_AUTHOR_NAME.")
@click.option("--no-images", is_flag=True, help="Skip illustrations/cover (cheap text-only smoke test).")
def generate(
    topic: str,
    book_type: str,
    resume: str | None,
    author: str | None,
    no_images: bool,
) -> None:
    """Run the full pipeline end-to-end. Resumable via --resume <slug>."""
    book_dir = run_book(
        topic,
        BookType(book_type),
        resume=resume,
        author=author,
        skip_images=no_images,
    )
    click.echo(click.style(f"\nDone. Output: {book_dir}", fg="green"))


# ── outline ───────────────────────────────────────────────────────────────────

@main.command()
@click.option("--topic", required=True, help="Topic / premise of the book.")
@click.option("--type", "book_type", required=True, type=click.Choice(BOOK_TYPE_CHOICES), help="Book type.")
@click.option("--author", default=None, help="Author name.")
def outline(topic: str, book_type: str, author: str | None) -> None:
    """Generate concept + outline + bible (no writing, no images)."""
    asyncio.run(_run_outline(topic, BookType(book_type), author))


async def _run_outline(topic: str, book_type: BookType, author: str | None) -> None:
    run_id = new_run_id()
    book_dir = resolve_book_dir(topic, book_type)
    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=run_id,
        topic=topic,
        book_type=book_type.value,
        slug=book_dir.name,
    )
    try:
        from kdp_book.models.book import IBookConfig, IBookState, get_book_type_config

        cfg = IBookConfig(
            topic=topic,
            book_type=book_type,
            type_config=get_book_type_config(book_type),
            author=author or get_settings().kdp_author_name,
        )
        state = IBookState(slug=book_dir.name, book_dir=str(book_dir), config=cfg)
        save_state(state)
        log.info("Outline run: %s (%s)", topic, book_type.value)

        with step_recorder(state.book_dir, "concept"):
            state = await do_concept(state)
            save_state(state)
        with step_recorder(state.book_dir, "outline"):
            state = await do_outline(state)
            save_state(state)
        with step_recorder(state.book_dir, "bible"):
            state = await do_bible(state)
            save_state(state)

        click.echo(click.style(f"\nConcept: {state.concept.title}", fg="cyan"))
        click.echo(f"  Subtitle:  {state.concept.subtitle}")
        click.echo(f"  Audience:  {state.concept.audience}")
        click.echo(f"  Tone:      {state.concept.tone}")
        click.echo(f"\nOutline: {len(state.outline.chapters)} chapters")
        for ch in state.outline.chapters:
            click.echo(f"  {ch.index:>2}. {ch.title}")
        click.echo(f"\nBible: {len(state.bible.characters)} characters, {len(state.bible.locations)} locations")
        for c in state.bible.characters:
            click.echo(f"  • {c.name} — {c.role}")
        click.echo(click.style(f"\nSaved to: {book_dir}", fg="green"))
        finalize_run_metadata(book_dir, status="ok")
    except Exception:
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


# ── write ─────────────────────────────────────────────────────────────────────


def _slug_to_book_dir(slug: str):
    book_dir = get_settings().kdp_books_dir / slug
    if not book_dir.exists():
        raise click.UsageError(f"No book directory at {book_dir}")
    return book_dir


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def write(from_slug: str) -> None:
    """Draft every chapter for an existing book slug."""
    asyncio.run(_run_write(from_slug))


async def _run_write(slug: str) -> None:
    from kdp_book.workflow.steps import do_write

    book_dir = _slug_to_book_dir(slug)
    state = load_state(book_dir)
    if state is None:
        raise click.UsageError(f"No book.json at {book_dir}")

    new_run_id()
    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=new_run_id(),
        topic=state.config.topic,
        book_type=state.config.book_type.value,
        slug=state.slug,
    )
    try:
        with step_recorder(state.book_dir, "write"):
            state = await do_write(state)
            save_state(state)
        click.echo(click.style(
            f"\nManuscript: {len(state.manuscript.chapters)} chapters, "
            f"{state.manuscript.total_word_count} words",
            fg="green",
        ))
        finalize_run_metadata(book_dir, status="ok")
    except Exception:
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def edit(from_slug: str) -> None:
    """Run the editorial review on a drafted manuscript."""
    asyncio.run(_run_edit(from_slug))


async def _run_edit(slug: str) -> None:
    from kdp_book.workflow.steps import do_edit

    book_dir = _slug_to_book_dir(slug)
    state = load_state(book_dir)
    if state is None:
        raise click.UsageError(f"No book.json at {book_dir}")

    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=new_run_id(),
        topic=state.config.topic,
        book_type=state.config.book_type.value,
        slug=state.slug,
    )
    try:
        with step_recorder(state.book_dir, "edit"):
            state = await do_edit(state)
            save_state(state)
        report = state.editor_report
        click.echo(click.style(
            f"\nEditor: {report.score}/10 — {len(report.issues)} issues, "
            f"{len(report.chapters_to_revise)} chapters revised",
            fg="green" if report.score >= 7 else "yellow",
        ))
        click.echo(report.summary)
        finalize_run_metadata(book_dir, status="ok")
    except Exception:
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


# ── stubs for later phases ────────────────────────────────────────────────────


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def illustrate(from_slug: str) -> None:
    """Plan + render illustrations (character refs + page art)."""
    asyncio.run(_run_illustrate(from_slug))


async def _run_illustrate(slug: str) -> None:
    from kdp_book.workflow.steps import do_characters, do_illustrate, do_images

    book_dir = _slug_to_book_dir(slug)
    state = load_state(book_dir)
    if state is None:
        raise click.UsageError(f"No book.json at {book_dir}")

    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=new_run_id(),
        topic=state.config.topic,
        book_type=state.config.book_type.value,
        slug=state.slug,
    )
    try:
        with step_recorder(state.book_dir, "illustrate"):
            state = await do_illustrate(state)
            save_state(state)
        with step_recorder(state.book_dir, "characters"):
            state = await do_characters(state)
            save_state(state)
        with step_recorder(state.book_dir, "images"):
            state = await do_images(state)
            save_state(state)
        click.echo(click.style(
            f"\nIllustrations: {len(state.illustrations)} planned, "
            f"{len(state.images)} rendered",
            fg="green",
        ))
        finalize_run_metadata(book_dir, status="ok")
    except Exception:
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
@click.option("--output", default="both", type=click.Choice(["pdf", "epub", "both"]))
def format(from_slug: str, output: str) -> None:
    """Render PDF and/or EPUB into the book's output/ directory."""
    asyncio.run(_run_format(from_slug, output))


async def _run_format(slug: str, output: str) -> None:
    from kdp_book.workflow.steps import do_format

    book_dir = _slug_to_book_dir(slug)
    state = load_state(book_dir)
    if state is None:
        raise click.UsageError(f"No book.json at {book_dir}")

    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=new_run_id(),
        topic=state.config.topic,
        book_type=state.config.book_type.value,
        slug=state.slug,
    )
    try:
        with step_recorder(state.book_dir, "format"):
            state = await do_format(state, output=output)
            save_state(state)
        click.echo(click.style(
            f"\nOutput written to: {book_dir / 'output'}",
            fg="green",
        ))
        finalize_run_metadata(book_dir, status="ok")
    except Exception:
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def cover(from_slug: str) -> None:
    """Design + render + compose the print-ready cover wrap."""
    asyncio.run(_run_cover(from_slug))


async def _run_cover(slug: str) -> None:
    from kdp_book.workflow.steps import do_cover

    book_dir = _slug_to_book_dir(slug)
    state = load_state(book_dir)
    if state is None:
        raise click.UsageError(f"No book.json at {book_dir}")

    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=new_run_id(),
        topic=state.config.topic,
        book_type=state.config.book_type.value,
        slug=state.slug,
    )
    try:
        with step_recorder(state.book_dir, "cover"):
            state = await do_cover(state)
            save_state(state)
        click.echo(click.style(
            f"\nCover composed: {state.cover.composed_path}",
            fg="green",
        ))
        finalize_run_metadata(book_dir, status="ok")
    except Exception:
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


@main.command()
@click.argument("slug")
@click.argument("step_name")
@click.option("--force", is_flag=True, help="Force re-run by clearing later checkpoints.")
def step(slug: str, step_name: str, force: bool) -> None:
    """Run a single named step on an existing book slug."""
    click.echo(f"step {slug}/{step_name} (force={force}): not yet implemented (Phase 1+)")


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def metadata(from_slug: str) -> None:
    """Generate KDP listing metadata (title, blurb, keywords, BISAC)."""
    asyncio.run(_run_metadata(from_slug))


async def _run_metadata(slug: str) -> None:
    from kdp_book.workflow.steps import do_metadata

    book_dir = _slug_to_book_dir(slug)
    state = load_state(book_dir)
    if state is None:
        raise click.UsageError(f"No book.json at {book_dir}")

    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=new_run_id(),
        topic=state.config.topic,
        book_type=state.config.book_type.value,
        slug=state.slug,
    )
    try:
        with step_recorder(state.book_dir, "metadata"):
            state = await do_metadata(state)
            save_state(state)
        md = state.metadata
        click.echo(click.style(
            f"\nMetadata generated:\n  title:    {md.title}\n"
            f"  keywords: {', '.join(md.keywords)}\n"
            f"  BISAC:    {', '.join(md.bisac_categories)}",
            fg="green",
        ))
        finalize_run_metadata(book_dir, status="ok")
    except Exception:
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def quality(from_slug: str) -> None:
    """Run the final quality review."""
    asyncio.run(_run_quality(from_slug))


async def _run_quality(slug: str) -> None:
    from kdp_book.workflow.steps import do_quality

    book_dir = _slug_to_book_dir(slug)
    state = load_state(book_dir)
    if state is None:
        raise click.UsageError(f"No book.json at {book_dir}")

    attach_file_handler(book_dir)
    reset_step_counter()
    init_run_metadata(
        book_dir=book_dir,
        run_id=new_run_id(),
        topic=state.config.topic,
        book_type=state.config.book_type.value,
        slug=state.slug,
    )
    try:
        with step_recorder(state.book_dir, "quality"):
            state = await do_quality(state)
            save_state(state)
        report = state.quality_report
        click.echo(click.style(
            f"\nQuality review: score {report.score}/10\n"
            f"  blockers: {len(report.blockers)}\n"
            f"  concerns: {len(report.concerns)}",
            fg="green" if report.score >= 7 else "yellow",
        ))
        finalize_run_metadata(book_dir, status="ok")
    except Exception:
        finalize_run_metadata(book_dir, status="failed")
        raise
    finally:
        detach_file_handler()


@main.command()
@click.argument("slug")
def status(slug: str) -> None:
    """Show pipeline progress for an existing slug."""
    book_dir = get_settings().kdp_books_dir / slug
    state = load_state(book_dir)
    if state is None:
        click.echo(click.style(f"No state at {book_dir}/book.json", fg="red"))
        raise SystemExit(1)
    click.echo(json.dumps(
        {
            "slug": state.slug,
            "book_dir": state.book_dir,
            "topic": state.config.topic,
            "type": state.config.book_type.value,
            "completed_steps": state.completed_steps,
            "has_concept": state.concept is not None,
            "has_outline": state.outline is not None,
            "has_bible": state.bible is not None,
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
