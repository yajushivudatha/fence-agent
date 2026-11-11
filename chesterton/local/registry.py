"""Fences a team has declared on purpose in `.fences.yml`.

The registry is read from the base revision, not the working tree, so deleting
an entry and the code it protects in the same change still trips the fence.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import yaml

from chesterton.config import path_matches
from chesterton.diff.symbols import Symbol
from chesterton.gitutil import Git, GitError
from chesterton.models import Evidence, EvidenceType, Hunk, HunkKind, Verdict
from chesterton.scoring import weights as w

REGISTRY_FILES = (".fences.yml", ".fences.yaml")
_LEVELS = {"block": Verdict.block, "warn": Verdict.warn}
_ENFORCED_KINDS = (HunkKind.deletion, HunkKind.modification)


@dataclass(frozen=True)
class Fence:
    path: str
    symbol: str | None
    reason: str
    level: Verdict


def load(git: Git, rev: str) -> tuple[list[Fence], list[str]]:
    """Return the fences and any problems worth reporting. Never raises."""
    for name in REGISTRY_FILES:
        try:
            raw = git.blob(rev, name)
        except GitError:
            continue
        return _parse(name, raw)
    return [], []


def _parse(name: str, raw: bytes) -> tuple[list[Fence], list[str]]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return [], [f"{name} could not be parsed and was ignored: {exc}"]
    if data is None:
        return [], []
    if not isinstance(data, list):
        return [], [f"{name} must be a list of fences and was ignored."]

    fences, problems = [], []
    for index, entry in enumerate(data, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry.get("reason"):
            problems.append(f"{name} entry {index} needs a path and a reason and was ignored.")
            continue
        level = _LEVELS.get(str(entry.get("level", "block")).lower())
        if level is None:
            problems.append(f"{name} entry {index} has a level other than block or warn and was ignored.")
            continue
        symbol = entry.get("symbol")
        fences.append(Fence(entry["path"], str(symbol) if symbol else None, str(entry["reason"]), level))
    return fences, problems


def _names_match(declared: str, actual: str) -> bool:
    return actual == declared or actual.endswith(f".{declared}") or actual.endswith(f"::{declared}")


def matching(fences: list[Fence], hunk: Hunk, symbols_of: Callable[[str], list[Symbol]]) -> list[Fence]:
    """Fences whose file matches and whose symbol overlaps the hunk's lines.

    Overlap, not the hunk's own symbol, decides: deleting a fenced function
    together with its neighbour must still match.
    """
    found = []
    for fence in fences:
        if not (path_matches(hunk.old_path, fence.path) or path_matches(hunk.path, fence.path)):
            continue
        if fence.symbol is None:
            found.append(fence)
            continue
        for symbol in symbols_of(hunk.old_path):
            overlaps = symbol.start <= hunk.old_end and hunk.old_start <= symbol.end
            if overlaps and _names_match(fence.symbol, symbol.name):
                found.append(fence)
                break
    return found


def evidence(fence: Fence, weights: Mapping[str, int]) -> Evidence:
    signal = w.FENCE_REGISTRY if fence.level is Verdict.block else w.FENCE_REGISTRY_WARN
    target = f"{fence.path}#{fence.symbol}" if fence.symbol else fence.path
    return Evidence(
        id=f"registry:{target}",
        type=EvidenceType.registry,
        ref=target,
        excerpt=fence.reason,
        signals=[signal],
        weight=weights[signal],
    )


def floor(fences: list[Fence], kind: HunkKind) -> Verdict | None:
    """The minimum verdict the matching fences force.

    Only real removals are forced. A moved fence still shows its evidence but
    keeps the move discount, otherwise relocating protected code could never pass.
    """
    if not fences or kind not in _ENFORCED_KINDS:
        return None
    return max((f.level for f in fences), key=lambda v: v.rank)
