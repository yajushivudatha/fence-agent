"""Deterministic risk score for one hunk."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from chesterton.diff.moves import is_meaningful, normalize
from chesterton.history import signals
from chesterton.history.log import CommitInfo
from chesterton.models import Evidence, EvidenceType, Hunk, HunkKind
from chesterton.scoring import weights as w

_BULK_SUBJECT = re.compile(r"\b(?:initial (?:commit|import)|first commit|bulk import)\b", re.IGNORECASE)
_EXCERPT_CHARS = 400


@dataclass
class ScoredHunk:
    score: float
    evidence: list[Evidence] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)


def combine(values: list[int]) -> float:
    """The strongest signal plus diminishing credit for the rest, capped at 100."""
    if not values:
        return 0.0
    ordered = sorted(values, reverse=True)
    return min(100.0, ordered[0] + 0.3 * sum(ordered[1:]))


def is_bulk(commit: CommitInfo) -> bool:
    return (
        not commit.parents
        or commit.files_changed >= w.BULK_MIN_FILES
        or _BULK_SUBJECT.search(commit.subject) is not None
    )


def _restores_lines(hunk: Hunk, removed_by_target: set[str]) -> bool:
    return any(is_meaningful(n) and n in removed_by_target for n in map(normalize, hunk.removed_lines))


def commit_signals(
    commit: CommitInfo,
    hunk: Hunk,
    removed_by_target: set[str],
    keywords: Mapping[str, re.Pattern[str]] = signals.KEYWORDS,
) -> set[str]:
    classes = signals.keyword_classes(commit.message, keywords)
    found: set[str] = set()
    if classes & {"incident", "security"}:
        found.add(w.INCIDENT_OR_SECURITY)
    if classes & {"concurrency", "compat"}:
        found.add(w.CONCURRENCY_OR_COMPAT)
    surgical = commit.lines_changed <= w.SURGICAL_MAX_LINES and not commit.is_merge
    if "fix" in classes and surgical and not signals.is_trivial_fix(commit.subject):
        found.add(w.FIX_SURGICAL)
    if signals.revert_target(commit.message) and _restores_lines(hunk, removed_by_target):
        found.add(w.REVERT_OF_REMOVAL)
    return found


def needs_author_history(origins: list[CommitInfo], reference_time: int) -> bool:
    """Whether the code is old enough for its author count to matter."""
    return bool(origins) and reference_time - min(c.committed_at for c in origins) >= w.OLD_CODE_SECONDS


def _commit_evidence(commit: CommitInfo, names: set[str], weights: Mapping[str, int]) -> Evidence:
    ordered = sorted(names, key=lambda n: (-weights[n], n))
    return Evidence(
        id=f"commit:{commit.sha[:12]}",
        type=EvidenceType.commit,
        ref=commit.sha,
        excerpt=commit.message[:_EXCERPT_CHARS],
        signals=ordered,
        weight=weights[ordered[0]],
        committed_at=commit.committed_at,
    )


def commit_evidence(
    hunk: Hunk,
    origins: list[CommitInfo],
    removed_by_revert_target: Mapping[str, set[str]],
    authors: set[str],
    reference_time: int,
    weights: Mapping[str, int] = w.WEIGHTS,
    keywords: Mapping[str, re.Pattern[str]] = signals.KEYWORDS,
) -> list[Evidence]:
    """Evidence from the commits that wrote the hunk's lines.

    Ages are measured from `reference_time`, the base commit's date, which
    keeps results reproducible.
    """
    by_commit: dict[str, set[str]] = {}
    for commit in origins:
        target = signals.revert_target(commit.message)
        removed = removed_by_revert_target.get(target, set()) if target else set()
        found = commit_signals(commit, hunk, removed, keywords)
        if found:
            by_commit[commit.sha] = found
    if needs_author_history(origins, reference_time) and len(authors) >= w.OLD_CODE_MIN_AUTHORS:
        oldest = min(origins, key=lambda c: c.committed_at)
        by_commit.setdefault(oldest.sha, set()).add(w.OLD_MANY_AUTHORS)
    commits = {c.sha: c for c in origins}
    return [_commit_evidence(commits[sha], names, weights) for sha, names in by_commit.items()]


def score(
    hunk: Hunk,
    evidence: list[Evidence],
    origins: list[CommitInfo],
    weights: Mapping[str, int] = w.WEIGHTS,
) -> ScoredHunk:
    """Combine all evidence for a hunk. Each kind of signal counts once."""
    distinct = {name for item in evidence for name in item.signals}
    value = combine([weights[name] for name in distinct])

    modifiers: list[str] = []
    if hunk.kind is HunkKind.move:
        value *= w.MODIFIER_MOVE
        modifiers.append("moved")
    if origins and all(is_bulk(c) for c in origins):
        value *= w.MODIFIER_BULK
        modifiers.append("bulk_origin")

    ranked = sorted(evidence, key=lambda e: (-e.weight, e.id))
    return ScoredHunk(round(value, 1), ranked, modifiers)
