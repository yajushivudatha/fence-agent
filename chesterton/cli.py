"""Command line entry point."""

import sys
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console

from chesterton.cache import Cache
from chesterton.config import ConfigError, Mode, Settings, load_settings
from chesterton.gitutil import Git, GitError
from chesterton.pipeline import Selection, explain, run_check
from chesterton.report import text
from chesterton.verdict.engine import EXIT_OK, EXIT_TOOL_ERROR, exit_code

app = typer.Typer(add_completion=False, help="Warn when a change removes code that exists for a reason.")
cache_app = typer.Typer(help="Manage the local cache in the git directory.")
app.add_typer(cache_app, name="cache")


class OutputFormat(StrEnum):
    text = "text"
    json = "json"


def _settings(cwd: Path) -> Settings:
    return load_settings(Git.discover(cwd).root)


def _fail(message: str, fail_closed: bool) -> typer.Exit:
    typer.echo(f"chesterton: {message}", err=True)
    return typer.Exit(EXIT_TOOL_ERROR if fail_closed else EXIT_OK)


@app.command()
def check(
    staged: bool = typer.Option(False, "--staged", help="Check staged changes. This is the default."),
    base: str | None = typer.Option(None, "--base", help="Check HEAD against its merge base with this ref."),
    commit_range: str | None = typer.Option(None, "--range", help="Check a commit range, A..B or A...B."),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
    mode: Mode | None = typer.Option(None, "--mode", help="Overrides the mode in .chesterton.toml."),
    fail_closed: bool | None = typer.Option(None, "--fail-closed/--fail-open", help="Exit 3 on a tool error instead of 0."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore and don't update the cache."),
) -> None:
    """Check a change for removed code that history says is there on purpose."""
    if sum([staged, base is not None, commit_range is not None]) > 1:
        typer.echo("Use only one of --staged, --base, --range.", err=True)
        raise typer.Exit(EXIT_TOOL_ERROR)

    try:
        settings = _settings(Path.cwd())
    except (GitError, ConfigError) as exc:
        raise _fail(str(exc), bool(fail_closed)) from exc
    settings = replace(
        settings,
        mode=mode or settings.mode,
        fail_closed=settings.fail_closed if fail_closed is None else fail_closed,
        cache=settings.cache and not no_cache,
    )

    try:
        report = run_check(Path.cwd(), Selection(base, commit_range), settings)
    except GitError as exc:
        raise _fail(str(exc), settings.fail_closed) from exc

    if output_format is OutputFormat.json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        text.render(report, Console(file=sys.stdout))
    raise typer.Exit(exit_code(report.findings, settings.mode))


@app.command("explain")
def explain_command(
    target: str = typer.Argument(..., help="PATH:LINE or PATH:START-END"),
    rev: str = typer.Option("HEAD", "--rev", help="Revision whose line numbers TARGET uses."),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    """Explain why lines exist, and what removing them would trigger."""
    try:
        explanation = explain(Path.cwd(), target, rev, _settings(Path.cwd()))
    except (GitError, ConfigError) as exc:
        typer.echo(f"chesterton: {exc}", err=True)
        raise typer.Exit(EXIT_TOOL_ERROR) from exc
    if output_format is OutputFormat.json:
        typer.echo(explanation.model_dump_json(indent=2))
    else:
        text.render_explanation(explanation, Console(file=sys.stdout))


@cache_app.command("prune")
def cache_prune(days: float = typer.Option(30, "--days", min=0, help="Remove entries unused for this many days.")) -> None:
    """Remove cache entries that haven't been used recently. --days 0 clears it."""
    try:
        cache = Cache.for_repo(Git.discover(Path.cwd()))
    except GitError as exc:
        typer.echo(f"chesterton: {exc}", err=True)
        raise typer.Exit(EXIT_TOOL_ERROR) from exc
    removed = cache.prune(days)
    cache.close()
    typer.echo(f"Removed {removed} cache entries.")
