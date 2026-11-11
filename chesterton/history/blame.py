"""Find the commits that introduced a range of lines."""

import re
from collections import Counter

from chesterton.gitutil import Git

_HEADER = re.compile(r"^([0-9a-f]{40,64}) \d+ \d+(?: \d+)?$")


def parse_porcelain(output: str) -> Counter[str]:
    """Count the blamed lines per origin commit."""
    origins: Counter[str] = Counter()
    for line in output.split("\n"):
        if match := _HEADER.match(line):
            origins[match[1]] += 1
    return origins


def blame_range(git: Git, rev: str, path: str, start: int, end: int) -> Counter[str]:
    # --root stops git from treating the first commit as a boundary.
    output = git.text("blame", "-w", "-M", "-C", "-C", "--root", "--porcelain", rev, "-L", f"{start},{end}", "--", path)
    return parse_porcelain(output)
