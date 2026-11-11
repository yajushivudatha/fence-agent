"""Comments in or near a hunk that say why the code is there."""

import re
from collections.abc import Mapping

from chesterton.models import Evidence, EvidenceType
from chesterton.scoring import weights as w

ADJACENT_LINES = 3
_MAX_PER_HUNK = 3

_KEEP = re.compile(
    r"\b(?:do\s+not|don'?t|never|must\s+not)\s+(?:remove|delete|touch|change)\b"
    r"|\b(?:needed|required|necessary)\s+(?:because|for|since)\b"
    r"|\bworkaround\s+for\b"
    r"|\bkeep\s+this\b",
    re.IGNORECASE,
)
_EXPLAINS = re.compile(r"\bHACK\b|\bXXX\b|\bNOTE:|\bsee\s+#\d+|https?://\S+", re.IGNORECASE)
# Where a comment starts. `#` and `//` may follow code; `*` and `--` only open a line.
_COMMENT_START = re.compile(r"#|//|/\*|<!--|^\s*\*|^\s*--")


def comment_text(line: str) -> str | None:
    match = _COMMENT_START.search(line)
    return line[match.start() :].strip() if match else None


def scan(path: str, old_lines: list[str], start: int, end: int, weights: Mapping[str, int]) -> list[Evidence]:
    """Look at the removed lines plus a few lines either side of them."""
    found: list[Evidence] = []
    first = max(1, start - ADJACENT_LINES)
    last = min(len(old_lines), end + ADJACENT_LINES)
    for number in range(first, last + 1):
        comment = comment_text(old_lines[number - 1])
        if comment is None:
            continue
        if _KEEP.search(comment):
            signal = w.DO_NOT_REMOVE_COMMENT
        elif _EXPLAINS.search(comment):
            signal = w.EXPLANATORY_COMMENT
        else:
            continue
        found.append(
            Evidence(
                id=f"comment:{path}:{number}",
                type=EvidenceType.comment,
                ref=f"{path}:{number}",
                excerpt=comment[:200],
                signals=[signal],
                weight=weights[signal],
                line=number,
            )
        )
    found.sort(key=lambda e: -e.weight)
    return found[:_MAX_PER_HUNK]
