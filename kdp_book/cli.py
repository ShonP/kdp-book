"""CLI entry point for `kdp-book`.

Skeleton only — subcommands are implemented across the phases described in
PLAN.md. This file currently exposes the click group + the `--help` surface
so `uv run kdp-book --help` works after `uv sync`.
"""

from __future__ import annotations

import click


@click.group()
@click.version_option(package_name="kdp-book")
def main() -> None:
    """kdp-book — generate KDP-ready books with AI agents."""


@main.command()
def doctor() -> None:
    """Validate the local environment (keys, fonts, output dir).

    Implemented in Phase 0.
    """
    click.echo("doctor: not yet implemented (Phase 0)")


# Subcommands stubbed here for discoverability; bodies land in later phases.
# See PLAN.md §"CLI surface by phase" for the rollout schedule.
#
#   kdp-book generate    --topic STR --type CHOICE   (Phase 1+)
#   kdp-book step        <slug> <step_name>          (Phase 1+)
#   kdp-book status      <slug>                      (Phase 1)
#   kdp-book outline     --topic STR --type CHOICE   (Phase 1)
#   kdp-book write       --from <slug>               (Phase 2)
#   kdp-book illustrate  --from <slug>               (Phase 6)
#   kdp-book format      --from <slug> --output FMT  (Phase 3)
#   kdp-book cover       --from <slug>               (Phase 4)
#   kdp-book metadata    --from <slug>               (Phase 5)
#   kdp-book quality     --from <slug>               (Phase 8)
#   kdp-book publish     --from <slug>               (Phase 10)
