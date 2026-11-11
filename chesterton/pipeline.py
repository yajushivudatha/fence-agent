"""Run one check from diff to report."""

import hashlib
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from chesterton import __version__
from chesterton.config import Settings
from chesterton.diff.analyzer import analyze
from chesterton.gitutil import Git, GitError
from chesterton.history import signals
from chesterton.history.blame import blame_range
from chesterton.history.log import CommitInfo, lines_removed_by, load_commits, range_authors
from chesterton.models import Evidence, Finding, Hunk, HunkKind, Report
from chesterton.scoring import heuristic, weights
from chesterton.verdict.engine import decide

_UNANALYZED = (HunkKind.whitespace, HunkKind.rename)
_MAX_ORIGINS = 5


@dataclass(frozen=True)
class Selection:
    """Which change to check. All empty means the staged changes."""

    base_ref: str | None = None
    commit_range: str | None = None


def _resolve(git: Git, selection: Selection) -> tuple[str, str | None]:
    """Return the base commit and the target commit, or None for the index."""
    if selection.commit_range:
        three_dot = "..." in selection.commit_range
        left, sep, right = selection.commit_range.partition("..." if three_dot else "..")
        if not sep:
            raise GitError(f"expected a range like A..B, got {selection.commit_range!r}")
        left, right = git.rev(left or "HEAD"), git.rev(right or "HEAD")
        return (git.text("merge-base", left, right).strip() if three_dot else left), right
    if selection.base_ref:
        head = git.rev("HEAD")
        return git.text("merge-base", git.rev(selection.base_ref), head).strip(), head
    return git.rev("HEAD"), None


def _pool_size() -> int:
    return min(8, os.cpu_count() or 1)


def _explain(hunk: Hunk, evidence: list[Evidence]) -> str:
    if hunk.kind is HunkKind.whitespace:
        return "Only whitespace changed."
    if hunk.kind is HunkKind.rename:
        return "The file was renamed with no content change."
    if not evidence:
        text = "History records no specific reason for this code."
    else:
        top = evidence[0]
        date = datetime.fromtimestamp(top.committed_at, UTC).strftime("%Y-%m-%d")
        subject = top.excerpt.split("\n", 1)[0].strip()
        text = f'{weights.LABELS[top.signals[0]]}: {top.ref[:7]} on {date}, "{subject}".'
    return f"Moved elsewhere in this change. {text}" if hunk.kind is HunkKind.move else text


def _finding(hunk: Hunk, scored: heuristic.ScoredHunk, shas: list[str], settings: Settings) -> Finding:
    identity = "|".join([hunk.path, hunk.symbol or "", *sorted(shas)])
    return Finding(
        id=hashlib.sha1(identity.encode()).hexdigest()[:12],
        hunk_id=hunk.id,
        path=hunk.path,
        symbol=hunk.symbol,
        old_start=hunk.old_start,
        old_end=hunk.old_end,
        kind=hunk.kind,
        is_rewrite=hunk.is_rewrite,
        heuristic_score=scored.score,
        modifiers=scored.modifiers,
        evidence=scored.evidence,
        explanation=_explain(hunk, scored.evidence),
        verdict=decide(scored.score, settings.thresholds),
    )


def run_check(cwd: Path, selection: Selection, settings: Settings) -> Report:
    started = time.perf_counter()
    git = Git.discover(cwd)
    if not git.has_head():
        return Report(
            repo=str(git.root),
            base="",
            head="",
            notes=["This repository has no commits yet, so there is no history to check."],
            tool_version=__version__,
        )

    base, target = _resolve(git, selection)
    report = Report(repo=str(git.root), base=base, head=target or "INDEX", tool_version=__version__)
    analysis = analyze(git, base, target, settings)
    report.stats.hunks = len(analysis.hunks)
    report.stats.skipped_paths = analysis.skipped_paths
    report.stats.skipped_oversize = analysis.skipped_oversize
    if analysis.skipped_oversize:
        report.notes.append(
            f"{analysis.skipped_oversize} hunk(s) over {settings.max_hunk_lines} lines were not analyzed."
        )

    shallow = git.shallow_boundaries()
    if shallow:
        report.notes.append(
            "Insufficient history: this is a shallow clone. In CI, set fetch-depth: 0 for full results."
        )

    scorable = [h for h in analysis.hunks if h.kind not in _UNANALYZED]

    def blame(hunk: Hunk) -> Counter[str] | None:
        try:
            return blame_range(git, base, hunk.old_path, hunk.old_start, hunk.old_end)
        except GitError:
            return None

    with ThreadPoolExecutor(max_workers=_pool_size()) as pool:
        blames = list(pool.map(blame, scorable))

        report.stats.blame_failures = sum(result is None for result in blames)
        if report.stats.blame_failures:
            report.notes.append(f"Blame failed for {report.stats.blame_failures} hunk(s), which scored as no history.")

        shas = {sha for result in blames if result for sha in result} - shallow
        commits = load_commits(git, shas)
        report.stats.origin_commits = len(commits)

        targets = {t for c in commits.values() if (t := signals.revert_target(c.message))}
        removed_by_target = dict(zip(targets, pool.map(lambda t: lines_removed_by(git, t), targets)))

        reference_time = int(git.text("show", "-s", "--format=%ct", base).strip())

        def origins_of(result: Counter[str] | None) -> list[CommitInfo]:
            ranked = [sha for sha, _ in (result or Counter()).most_common() if sha in commits]
            return [commits[sha] for sha in ranked]

        origin_lists = [origins_of(result) for result in blames]

        def authors_of(item: tuple[Hunk, list[CommitInfo]]) -> set[str]:
            hunk, origins = item
            if not heuristic.needs_author_history(origins, reference_time):
                return set()
            try:
                return range_authors(git, base, hunk.old_path, hunk.old_start, hunk.old_end)
            except GitError:
                return {c.author for c in origins}

        author_sets = list(pool.map(authors_of, zip(scorable, origin_lists)))

    findings: list[Finding] = []
    for hunk, origins, authors in zip(scorable, origin_lists, author_sets):
        limited = origins[:_MAX_ORIGINS]
        scored = heuristic.score_hunk(hunk, limited, removed_by_target, authors, reference_time)
        findings.append(_finding(hunk, scored, [c.sha for c in limited], settings))
    for hunk in analysis.hunks:
        if hunk.kind in _UNANALYZED:
            findings.append(_finding(hunk, heuristic.ScoredHunk(0.0), [], settings))

    findings.sort(key=lambda f: (-f.heuristic_score, f.path, f.old_start))
    report.findings = findings
    report.stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return report
