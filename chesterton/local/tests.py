"""Tests that mention the symbol a hunk belongs to."""

import re
from collections.abc import Mapping

from chesterton.gitutil import Git
from chesterton.models import Evidence, EvidenceType, Hunk
from chesterton.scoring import weights as w

_SHOWN_FILES = 3
_MIN_NAME_LENGTH = 4
# Names so common that a test mentioning one says nothing about this symbol.
_GENERIC = {
    "main", "init", "__init__", "setup", "teardown", "call", "__call__", "handle", "handler",
    "run", "start", "stop", "close", "open", "read", "write", "load", "save", "update",
    "create", "delete", "render", "parse", "process", "execute", "apply", "build", "reset",
    "constructor", "toString", "equals", "hashCode", "default", "value", "values", "items",
}
_TEST_PATHSPECS = (
    ":(glob)**/test/**",
    ":(glob)**/tests/**",
    ":(glob)**/__tests__/**",
    ":(glob)**/spec/**",
    ":(glob)**/test_*.py",
    ":(glob)**/*_test.py",
    ":(glob)**/*_test.go",
    ":(glob)**/*.test.*",
    ":(glob)**/*.spec.*",
    ":(glob)**/*Test.java",
    ":(glob)**/*Tests.cs",
)
_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|__tests__|specs?)/"
    r"|(?:^|/)test_[^/]*\.py$|_test\.(?:py|go)$|\.(?:test|spec)\.[^/]+$|Tests?\.(?:java|cs)$"
)


def is_test_path(path: str) -> bool:
    return _TEST_PATH.search(path) is not None


def searchable_name(hunk: Hunk) -> str | None:
    """The bare symbol name worth grepping for, or None for test code and generic names."""
    if hunk.symbol is None or is_test_path(hunk.old_path):
        return None
    name = re.split(r"\.|::", hunk.symbol)[-1]
    if len(name) < _MIN_NAME_LENGTH or name in _GENERIC or name.startswith("__"):
        return None
    return name


def find_test_files(git: Git, rev: str, name: str) -> list[str]:
    output = git.text("grep", "-l", "-w", "-F", "-e", name, rev, "--", *_TEST_PATHSPECS, ok_codes=(0, 1))
    prefix = f"{rev}:"
    return sorted(line.removeprefix(prefix) for line in output.split("\n") if line)


def evidence(name: str, files: list[str], weights: Mapping[str, int]) -> Evidence:
    shown = ", ".join(files[:_SHOWN_FILES])
    more = len(files) - _SHOWN_FILES
    return Evidence(
        id=f"test:{files[0]}",
        type=EvidenceType.test,
        ref=files[0],
        excerpt=f"{name} appears in {shown}" + (f" and {more} more" if more > 0 else ""),
        signals=[w.TESTED],
        weight=weights[w.TESTED],
    )
