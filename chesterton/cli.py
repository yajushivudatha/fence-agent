"""Command line entry point."""

import sys
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console

from chesterton.config import Settings
from chesterton.gitutil import GitError
from chesterton.pipeline import Selection, run_check
from chesterton.report import text
from chesterton.verdict.engine import EXIT_OK, EXIT_TOOL_ERROR, Mode, exit_code

app = typer.Typer(add_completion=False, help="Warn when a change removes code that exists for a reason.")


class OutputFormat(StrEnum):
    text = "text"
    json = "json"


@app.callback()
def main() -> None:
    """Keeps `check` a named subcommand while it is the only one."""


@app.command()
def check(
    staged: bool = typer.Option(False, "--staged", help="Check staged changes. This is the default."),
    base: str | None = typer.Option(None, "--base", help="Check HEAD against its merge base with this ref."),
    commit_range: str | None = typer.Option(None, "--range", help="Check a commit range, A..B or A...B."),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
    mode: Mode = typer.Option(Mode.standard, "--mode"),
    fail_closed: bool = typer.Option(False, "--fail-closed", help="Exit 3 on a tool error instead of 0."),
) -> None:
    """Check a change for removed code that history says is there on purpose."""
    if sum([staged, base is not None, commit_range is not None]) > 1:
        typer.echo("Use only one of --staged, --base, --range.", err=True)
        raise typer.Exit(EXIT_TOOL_ERROR)

    try:
        report = run_check(Path.cwd(), Selection(base, commit_range), Settings())
    except GitError as exc:
        typer.echo(f"chesterton: {exc}", err=True)
        raise typer.Exit(EXIT_TOOL_ERROR if fail_closed else EXIT_OK) from exc

    if output_format is OutputFormat.json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        text.render(report, Console(file=sys.stdout))
    raise typer.Exit(exit_code(report.findings, mode))
