"""Lint and type-checker suppressions: someone chose to keep an oddity."""

import re
from collections.abc import Mapping

from chesterton.models import Evidence, EvidenceType, Hunk
from chesterton.scoring import weights as w

_MAX_PER_HUNK = 3
_SUPPRESSION = re.compile(
    r"#\s*noqa\b|#\s*type:\s*ignore|pylint:\s*disable|#\s*nosec\b"
    r"|eslint-disable|@ts-ignore|@ts-expect-error"
    r"|//\s*nolint\b|\bNOLINT(?:NEXTLINE)?\b"
    r"|@SuppressWarnings|@SuppressLint|#\[allow\(|rubocop:disable"
)


def scan(hunk: Hunk, weights: Mapping[str, int]) -> list[Evidence]:
    found: list[Evidence] = []
    for offset, line in enumerate(hunk.removed_lines):
        match = _SUPPRESSION.search(line)
        if match is None:
            continue
        number = hunk.old_start + offset
        found.append(
            Evidence(
                id=f"suppression:{hunk.old_path}:{number}",
                type=EvidenceType.suppression,
                ref=f"{hunk.old_path}:{number}",
                excerpt=line.strip()[:200],
                signals=[w.SUPPRESSION],
                weight=weights[w.SUPPRESSION],
                line=number,
            )
        )
        if len(found) == _MAX_PER_HUNK:
            break
    return found
