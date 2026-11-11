"""Run a check or an explanation, from git data to findings."""

import hashlib
import os
import re
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chesterton import __version__
from chesterton.cache import Cache
from chesterton.config import Settings
from chesterton.diff.analyzer import analyze
from chesterton.diff.symbols import Symbol, enclosing, index_symbols, language_for
from chesterton.gitutil import Git, GitError
from chesterton.history import signals
from chesterton.history.blame import blame_range
from chesterton.history.log import (
    CommitInfo,
    lines_removed_by,
    load_commits,
    range_authors,
    range_history,
)
from chesterton.local import comments, registry, suppressions, tests
from chesterton.models import (
    Evidence,
    EvidenceType,
    Explanation,
    Finding,
    HistoryEntry,
    Hunk,
    HunkKind,
    Report,
    Verdict,
)
from chesterton.scoring import heuristic, weights
from chesterton.verdict.engine import decide

_UNANALYZED = (HunkKind.whitespace, HunkKind.rename)
_MAX_ORIGINS = 5
_HISTORY_LIMIT = 10
_TARGET = re.compile(r"(\d+)(?:-(\d+))?")


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


def _date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")


def _explain(hunk: Hunk, evidence: list[Evidence]) -> str:
    if hunk.kind is HunkKind.whitespace:
        return "Only whitespace changed."
    if hunk.kind is HunkKind.rename:
        return "The file was renamed with no content change."
    if not evidence:
        text = "History records no specific reason for this code."
    else:
        top = evidence[0]
        label = weights.LABELS[top.signals[0]]
        first_line = top.excerpt.split("\n", 1)[0].strip()
        match top.type:
            case EvidenceType.commit:
                text = f'{label}: {top.ref[:7]} on {_date(top.committed_at or 0)}, "{first_line}".'
            case EvidenceType.comment | EvidenceType.suppression:
                text = f'{label}: line {top.line} reads "{first_line}".'
            case EvidenceType.test:
                text = f"{label}: {first_line}."
            case EvidenceType.registry:
                text = f"{label}: {first_line}"
    return f"Moved elsewhere in this change. {text}" if hunk.kind is HunkKind.move else text


def _finding(hunk: Hunk, scored: heuristic.ScoredHunk, shas: list[str], settings: Settings, floor: Verdict | None) -> Finding:
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
        verdict=decide(scored.score, settings.thresholds, floor),
    )


class _Scorer:
    """Scores hunks against one base revision, sharing git reads and the cache."""

    def __init__(self, git: Git, base: str, settings: Settings, report: Report, cache: Cache):
        self.git = git
        self.base = base
        self.settings = settings
        self.report = report
        self.cache = cache
        self._lines: dict[str, list[str]] = {}
        self._symbols: dict[str, list[Symbol]] = {}
        self.fences, problems = registry.load(git, base)
        report.notes.extend(problems)

    def old_lines(self, path: str) -> list[str]:
        if path not in self._lines:
            try:
                text = self.git.blob(self.base, path).decode("utf-8", "replace")
            except GitError:
                text = ""
            self._lines[path] = text.split("\n")[:-1] if text.endswith("\n") else text.split("\n") if text else []
        return self._lines[path]

    def symbols(self, path: str) -> list[Symbol]:
        if path not in self._symbols:
            language = language_for(path)
            source = "\n".join(self.old_lines(path)).encode()
            self._symbols[path] = index_symbols(source, language) if language else []
        return self._symbols[path]

    def _cached(
        self,
        pool: Executor,
        kind: str,
        items: dict[str, Any],
        compute: Callable[[Any], Any],
        encode: Callable[[Any], Any] = lambda v: v,
        decode: Callable[[Any], Any] = lambda v: v,
    ) -> dict[str, Any]:
        """Look each key up in the cache and compute the misses in parallel.

        A computed value of None means "failed" and is not cached.
        """
        results: dict[str, Any] = {}
        missing: dict[str, Any] = {}
        for key, arg in items.items():
            hit = self.cache.get(kind, key)
            if hit is None:
                missing[key] = arg
            else:
                results[key] = decode(hit)
        for key, value in zip(missing, pool.map(compute, missing.values())):
            results[key] = value
            if value is not None:
                self.cache.put(kind, key, encode(value))
        return results

    def _commits(self, shas: set[str]) -> dict[str, CommitInfo]:
        commits: dict[str, CommitInfo] = {}
        for sha in shas:
            if (hit := self.cache.get("commit", sha)) is not None:
                commits[sha] = CommitInfo(**hit)
        loaded = load_commits(self.git, shas - set(commits))
        for sha, commit in loaded.items():
            self.cache.put("commit", sha, asdict(commit))
        return commits | loaded

    def _range_key(self, hunk: Hunk) -> str:
        return f"{self.base}:{hunk.old_path}:{hunk.old_start}:{hunk.old_end}"

    def score(self, hunks: list[Hunk]) -> list[Finding]:
        git, base, settings = self.git, self.base, self.settings
        stats = self.report.stats
        scorable = [h for h in hunks if h.kind not in _UNANALYZED]

        shallow = git.shallow_boundaries()
        if shallow:
            self.report.notes.append(
                "Insufficient history: this is a shallow clone. In CI, set fetch-depth: 0 for full results."
            )

        def blame(hunk: Hunk) -> Counter[str] | None:
            try:
                return blame_range(git, base, hunk.old_path, hunk.old_start, hunk.old_end)
            except GitError:
                return None

        def test_files(name: str) -> list[str] | None:
            try:
                return tests.find_test_files(git, base, name)
            except GitError:
                return None

        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
            blames = self._cached(
                pool, "blame", {self._range_key(h): h for h in scorable}, blame, encode=dict, decode=Counter
            )
            stats.blame_failures = sum(result is None for result in blames.values())
            if stats.blame_failures:
                self.report.notes.append(f"Blame failed for {stats.blame_failures} hunk(s), which scored as no history.")

            shas = {sha for result in blames.values() if result for sha in result} - shallow
            commits = self._commits(shas)
            stats.origin_commits = len(commits)

            targets = {t: t for c in commits.values() if (t := signals.revert_target(c.message))}
            removed_by_target = self._cached(
                pool, "revert", targets, lambda t: lines_removed_by(git, t), encode=sorted, decode=set
            )
            removed_by_target = {k: v or set() for k, v in removed_by_target.items()}

            reference_time = int(git.text("show", "-s", "--format=%ct", base).strip())

            def origins_of(hunk: Hunk) -> list[CommitInfo]:
                ranked = blames.get(self._range_key(hunk)) or Counter()
                return [commits[sha] for sha, _ in ranked.most_common() if sha in commits][:_MAX_ORIGINS]

            origins = {h.id: origins_of(h) for h in scorable}

            def authors_of(hunk: Hunk) -> list[str] | None:
                try:
                    return sorted(range_authors(git, base, hunk.old_path, hunk.old_start, hunk.old_end))
                except GitError:
                    return None

            needs_authors = {
                self._range_key(h): h
                for h in scorable
                if heuristic.needs_author_history(origins[h.id], reference_time)
            }
            authors = self._cached(pool, "authors", needs_authors, authors_of)

            names = {name for h in scorable if (name := tests.searchable_name(h))}
            test_hits = self._cached(pool, "tests", {f"{base}:{n}": n for n in names}, test_files)

        findings = []
        for hunk in scorable:
            hunk_origins = origins[hunk.id]
            hunk_authors = authors.get(self._range_key(hunk)) or [c.author for c in hunk_origins]
            evidence = heuristic.commit_evidence(
                hunk, hunk_origins, removed_by_target, set(hunk_authors), reference_time,
                settings.weights, settings.keywords,
            )
            evidence += comments.scan(hunk.old_path, self.old_lines(hunk.old_path), hunk.old_start, hunk.old_end, settings.weights)
            evidence += suppressions.scan(hunk, settings.weights)
            name = tests.searchable_name(hunk)
            if name and (files := test_hits.get(f"{base}:{name}")):
                evidence.append(tests.evidence(name, files, settings.weights))
            fences = registry.matching(self.fences, hunk, self.symbols)
            evidence += [registry.evidence(f, settings.weights) for f in fences]

            scored = heuristic.score(hunk, evidence, hunk_origins, settings.weights)
            floor = registry.floor(fences, hunk.kind)
            findings.append(_finding(hunk, scored, [c.sha for c in hunk_origins], settings, floor))

        for hunk in hunks:
            if hunk.kind in _UNANALYZED:
                findings.append(_finding(hunk, heuristic.ScoredHunk(0.0), [], settings, None))

        findings.sort(key=lambda f: (-f.heuristic_score, f.path, f.old_start))
        return findings


def _open_cache(git: Git, settings: Settings) -> Cache:
    return Cache.for_repo(git, settings.cache)


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

    cache = _open_cache(git, settings)
    try:
        report.findings = _Scorer(git, base, settings, report, cache).score(analysis.hunks)
    finally:
        cache.close()
    report.stats.cache_hits, report.stats.cache_misses = cache.hits, cache.misses
    report.stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return report


def _parse_target(git: Git, cwd: Path, target: str) -> tuple[str, int, int]:
    path_part, _, range_part = target.rpartition(":")
    match = _TARGET.fullmatch(range_part)
    if not path_part or match is None:
        raise GitError(f"expected PATH:LINE or PATH:START-END, got {target!r}")
    start = int(match[1])
    end = int(match[2] or start)
    if start < 1 or end < start:
        raise GitError(f"invalid line range in {target!r}")
    absolute = (cwd / path_part).resolve()
    try:
        relative = absolute.relative_to(git.root.resolve())
    except ValueError as exc:
        raise GitError(f"{path_part} is outside the repository") from exc
    return relative.as_posix(), start, end


def explain(cwd: Path, target: str, rev: str, settings: Settings) -> Explanation:
    """Score lines as if they were deleted, and show the history behind them."""
    git = Git.discover(cwd)
    path, start, end = _parse_target(git, cwd, target)
    base = git.rev(rev)
    report = Report(repo=str(git.root), base=base, head=base, tool_version=__version__)
    cache = _open_cache(git, settings)
    try:
        scorer = _Scorer(git, base, settings, report, cache)
        lines = scorer.old_lines(path)
        if not lines:
            raise GitError(f"{path} does not exist at {rev}, or is empty")
        if end > len(lines):
            raise GitError(f"{path} has {len(lines)} lines at {rev}")
        removed = lines[start - 1 : end]
        symbol = enclosing(scorer.symbols(path), start, end)
        hunk = Hunk(
            id=hashlib.sha1(f"{path}:{start}:{end}".encode()).hexdigest()[:12],
            path=path,
            old_path=path,
            old_start=start,
            old_end=end,
            new_start=start,
            new_end=start,
            kind=HunkKind.deletion,
            symbol=symbol.name if symbol else None,
            language=language_for(path),
            removed_text="\n".join(removed),
        )
        [finding] = scorer.score([hunk])
    finally:
        cache.close()

    history = [
        HistoryEntry(sha=sha, committed_at=at, author=author, subject=subject)
        for sha, at, author, subject in range_history(git, base, path, start, end, _HISTORY_LIMIT)
    ]
    dirty = bool(git.text("diff", "--name-only", base, "--", path).strip())
    return Explanation(
        path=path,
        start=start,
        end=end,
        rev=base,
        lines=removed,
        finding=finding,
        history=history,
        uncommitted_changes=dirty,
        notes=report.notes,
    )
