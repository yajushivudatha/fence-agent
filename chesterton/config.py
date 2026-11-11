"""Tool settings. Defaults only for now, `.chesterton.toml` loading arrives in M1."""

import re
from dataclasses import dataclass, field

DEFAULT_IGNORE = (
    "**/generated/**",
    "**/vendor/**",
    "**/node_modules/**",
    "**/migrations/**",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "go.sum",
    "*.min.js",
    "*.pb.go",
    "*_pb2.py",
    # Prose: the spec targets code, and docs history is full of the word "security".
    "**/docs/**",
    "*.md",
    "*.rst",
    "*.adoc",
)


@dataclass(frozen=True)
class Thresholds:
    warn: float = 40
    block: float = 75


@dataclass(frozen=True)
class Settings:
    thresholds: Thresholds = field(default_factory=Thresholds)
    ignore: tuple[str, ...] = DEFAULT_IGNORE
    max_hunk_lines: int = 500


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out += "(?:/.*)?"
            i += 3
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out)


def is_ignored(path: str, patterns: tuple[str, ...]) -> bool:
    """Match a repo-relative path. A pattern with no slash matches the file name."""
    basename = path.rsplit("/", 1)[-1]
    for pattern in patterns:
        subject = path if "/" in pattern else basename
        if _glob_to_regex(pattern).fullmatch(subject):
            return True
    return False
