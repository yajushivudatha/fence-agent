"""Detect removed code that reappears elsewhere in the same change."""

import re
from collections import Counter

from chesterton.models import Hunk, HunkKind

MOVE_RATIO = 0.8
# A one or two line match is too weak: the same guard added to a different
# function would otherwise excuse removing it here.
MIN_MEANINGFUL_LINES = 3

_WHITESPACE = re.compile(r"\s+")
_TRIVIAL = {"return", "return;", "break", "break;", "continue", "continue;", "else:", "else{", "}else{", "try:", "pass"}


def normalize(line: str) -> str:
    return _WHITESPACE.sub("", line)


def is_meaningful(normalized: str) -> bool:
    if normalized in _TRIVIAL:
        return False
    return sum(ch.isalnum() for ch in normalized) >= 4


def line_pool(added_lines: list[str]) -> Counter[str]:
    return Counter(n for n in map(normalize, added_lines) if is_meaningful(n))


def mark_moves(hunks: list[Hunk], pool: Counter[str]) -> None:
    """Reclassify hunks as moves, consuming each added line at most once.

    Largest hunks claim lines first so a moved block is not starved by a small
    hunk that happens to share a few lines with it.
    """
    candidates = [h for h in hunks if h.kind in (HunkKind.deletion, HunkKind.modification)]
    for hunk in sorted(candidates, key=lambda h: len(h.removed_lines), reverse=True):
        needed = Counter(n for n in map(normalize, hunk.removed_lines) if is_meaningful(n))
        total = sum(needed.values())
        if total < MIN_MEANINGFUL_LINES:
            continue
        # Lines re-added by this same hunk did not move "elsewhere".
        own = Counter(map(normalize, hunk.added_lines))
        matched = {line: min(n, max(pool[line] - own[line], 0)) for line, n in needed.items()}
        if sum(matched.values()) / total >= MOVE_RATIO:
            hunk.kind = HunkKind.move
            pool.subtract(matched)
