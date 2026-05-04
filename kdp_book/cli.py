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
def generate(topic: str, book_type: str, resume: str | None, author: str | None) -> None:
    """Run the full pipeline end-to-end. Resumable via --resume <slug>."""
    book_dir = run_book(topic, BookType(book_type), resume=resume, author=author)
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
    new_run_id()
    book_dir = resolve_book_dir(topic, book_type)
    attach_file_handler(book_dir)
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

        state = await do_concept(state)
        save_state(state)
        state = await do_outline(state)
        save_state(state)
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
    finally:
        detach_file_handler()


# ── stubs for later phases ────────────────────────────────────────────────────

@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def write(from_slug: str) -> None:
    """Write the manuscript (Phase 2)."""
    click.echo("write: not yet implemented (Phase 2)")


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def illustrate(from_slug: str) -> None:
    """Generate illustrations (Phase 6)."""
    click.echo("illustrate: not yet implemented (Phase 6)")


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
@click.option("--output", default="both", type=click.Choice(["pdf", "epub", "both"]))
def format(from_slug: str, output: str) -> None:
    """Render PDF/EPUB (Phase 3)."""
    click.echo(f"format ({output}): not yet implemented (Phase 3)")


@main.command()
@click.option("--from", "from_slug", required=True, help="Existing book slug.")
def cover(from_slug: str) -> None:
    """Render cover (Phase 4)."""
    click.echo("cover: not yet implemented (Phase 4)")


@main.command()
@click.argument("slug")
@click.argument("step_name")
@click.option("--force", is_flag=True, help="Force re-run by clearing later checkpoints.")
def step(slug: str, step_name: str, force: bool) -> None:
    """Run a single named step on an existing book slug."""
    click.echo(f"step {slug}/{step_name} (force={force}): not yet implemented (Phase 1+)")


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
