"""Turn a git diff into classified hunks.

The diff deliberately keeps added files and omits copy detection. Either of
those would hide the destination of moved code: a function moved to a new file
only shows up as `+` lines if the new file is reported in full.
"""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from chesterton.config import Settings, is_ignored
from chesterton.diff import moves
from chesterton.diff.symbols import Symbol, enclosing, index_symbols, language_for
from chesterton.gitutil import Git, GitError
from chesterton.models import Hunk, HunkKind

REWRITE_RATIO = 0.5
_HUNK_HEADER = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_INDENT_SIGNIFICANT = {".py", ".pyi", ".yml", ".yaml", ".hs", ".nim", ".coffee"}
_NOT_IN_QUOTES = re.compile(r"""("[^"\n]*"|'[^'\n]*')|\s+""")
_ESCAPES = {b"a": 7, b"b": 8, b"t": 9, b"n": 10, b"v": 11, b"f": 12, b"r": 13, b'"': 34, b"\\": 92}


@dataclass
class RawHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    status: str = "M"
    old_path: str | None = None
    new_path: str | None = None
    hunks: list[RawHunk] = field(default_factory=list)


@dataclass
class Analysis:
    hunks: list[Hunk]
    skipped_paths: int = 0
    skipped_oversize: int = 0


def _unquote(raw: bytes) -> str:
    """Decode a path that git wrote in its C-style quoted form."""
    if not (len(raw) >= 2 and raw.startswith(b'"') and raw.endswith(b'"')):
        return raw.decode("utf-8", "replace")
    body, out, i = raw[1:-1], bytearray(), 0
    while i < len(body):
        if body[i] != 92:  # backslash
            out.append(body[i])
            i += 1
        elif body[i + 1 : i + 2].isdigit():
            out.append(int(body[i + 1 : i + 4], 8))
            i += 4
        else:
            out.append(_ESCAPES.get(body[i + 1 : i + 2], body[i + 1]))
            i += 2
    return out.decode("utf-8", "replace")


def _header_path(raw: bytes) -> str | None:
    # git appends a tab to these lines when the path contains spaces.
    path = _unquote(raw.removesuffix(b"\t"))
    if path == "/dev/null":
        return None
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse_patch(data: bytes) -> list[FileDiff]:
    """Parse `git diff -U0` output.

    Hunk bodies are consumed by the counts in their `@@` header, so a removed
    SQL comment such as `-- note` (rendered `--- note`) is not read as a header.
    """
    files: list[FileDiff] = []
    lines = data.split(b"\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith(b"diff --git "):
            files.append(FileDiff())
            continue
        if not files:
            continue
        current = files[-1]
        if line.startswith(b"rename from "):
            current.status, current.old_path = "R", _unquote(line[12:])
        elif line.startswith(b"rename to "):
            current.new_path = _unquote(line[10:])
        elif line.startswith(b"new file mode"):
            current.status = "A"
        elif line.startswith(b"deleted file mode"):
            current.status = "D"
        elif line.startswith(b"--- "):
            current.old_path = _header_path(line[4:])
        elif line.startswith(b"+++ "):
            current.new_path = _header_path(line[4:])
        elif match := _HUNK_HEADER.match(line):
            old_count = int(match[2]) if match[2] is not None else 1
            new_count = int(match[4]) if match[4] is not None else 1
            hunk = RawHunk(int(match[1]), old_count, int(match[3]), new_count)
            while (len(hunk.removed) < old_count or len(hunk.added) < new_count) and i < len(lines):
                body = lines[i]
                i += 1
                if body.startswith(b"-"):
                    hunk.removed.append(body[1:].decode("utf-8", "replace"))
                elif body.startswith(b"+"):
                    hunk.added.append(body[1:].decode("utf-8", "replace"))
            current.hunks.append(hunk)
    return files


def _squash(text: str) -> str:
    """Remove whitespace, except inside same-line string literals."""
    return _NOT_IN_QUOTES.sub(lambda m: m.group(1) or "", text)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _same_indentation(removed: list[str], added: list[str]) -> bool:
    old = [line for line in removed if line.strip()]
    new = [line for line in added if line.strip()]
    if len(old) != len(new):
        return False
    return all(_indent(a) == _indent(b) for a, b in zip(old, new))


def is_whitespace_only(removed: list[str], added: list[str], path: str) -> bool:
    if _squash("\n".join(removed)) != _squash("\n".join(added)):
        return False
    if PurePosixPath(path).suffix.lower() in _INDENT_SIGNIFICANT:
        # Moving a line in or out of a block changes what the program does.
        return _same_indentation(removed, added)
    return True


def _hunk_id(path: str, start: int, end: int) -> str:
    return hashlib.sha1(f"{path}:{start}:{end}".encode()).hexdigest()[:12]


def _build_hunk(file: FileDiff, raw: RawHunk) -> Hunk:
    old_end = raw.old_start + raw.old_count - 1
    if is_whitespace_only(raw.removed, raw.added, file.new_path or file.old_path):
        kind = HunkKind.whitespace
    else:
        kind = HunkKind.modification if raw.added else HunkKind.deletion
    return Hunk(
        id=_hunk_id(file.old_path, raw.old_start, old_end),
        path=file.new_path or file.old_path,
        old_path=file.old_path,
        old_start=raw.old_start,
        old_end=old_end,
        new_start=raw.new_start,
        new_end=raw.new_start + max(raw.new_count - 1, 0),
        kind=kind,
        removed_text="\n".join(raw.removed),
        added_text="\n".join(raw.added),
    )


def _apply_symbols(git: Git, base: str, hunks: list[Hunk]) -> list[Hunk]:
    """Attach symbols and widen heavily rewritten functions to their full body."""
    by_file: dict[str, list[Hunk]] = defaultdict(list)
    for hunk in hunks:
        if hunk.kind is not HunkKind.rename:
            by_file[hunk.old_path].append(hunk)

    result = [h for h in hunks if h.kind is HunkKind.rename]
    for path, file_hunks in by_file.items():
        language = language_for(path)
        if language is None:
            result.extend(file_hunks)
            continue
        try:
            source = git.blob(base, path)
        except GitError:
            result.extend(file_hunks)
            continue
        symbols = index_symbols(source, language)
        groups: dict[Symbol, list[Hunk]] = defaultdict(list)
        for hunk in file_hunks:
            hunk.language = language
            symbol = enclosing(symbols, hunk.old_start, hunk.old_end)
            if symbol is None:
                result.append(hunk)
                continue
            hunk.symbol = symbol.name
            removable = hunk.kind in (HunkKind.deletion, HunkKind.modification)
            if removable and symbol.is_function:
                groups[symbol].append(hunk)
            else:
                result.append(hunk)
        old_lines = source.decode("utf-8", "replace").split("\n")
        for symbol, members in groups.items():
            result.extend(_widen_rewrite(symbol, members, old_lines))
    return sorted(result, key=lambda h: (h.path, h.old_start))


def _widen_rewrite(symbol: Symbol, members: list[Hunk], old_lines: list[str]) -> list[Hunk]:
    changed = sum(h.old_end - h.old_start + 1 for h in members)
    covers_whole = len(members) == 1 and members[0].old_start <= symbol.start and members[0].old_end >= symbol.end
    if covers_whole or changed / symbol.span <= REWRITE_RATIO:
        return members
    first = members[0]
    added = [line for h in members for line in h.added_lines]
    return [
        first.model_copy(
            update={
                "id": _hunk_id(first.old_path, symbol.start, symbol.end),
                "old_start": symbol.start,
                "old_end": symbol.end,
                "new_start": min(h.new_start for h in members),
                "new_end": max(h.new_end for h in members),
                "kind": HunkKind.modification,
                "removed_text": "\n".join(old_lines[symbol.start - 1 : symbol.end]),
                "added_text": "\n".join(added),
                "is_rewrite": True,
            }
        )
    ]


def analyze(git: Git, base: str, target: str | None, settings: Settings) -> Analysis:
    """Diff `base` against `target`, or against the index when `target` is None."""
    args = ["diff", "-U0", "-M", "--diff-filter=ACDMR", "--no-color", "--no-ext-diff"]
    args += ["--src-prefix=a/", "--dst-prefix=b/"]
    args += ["--cached", base] if target is None else [base, target]
    files = parse_patch(git.run(*args))

    pool = moves.line_pool([line for f in files for h in f.hunks for line in h.added])
    analysis = Analysis(hunks=[])
    for file in files:
        if file.status == "A" or file.old_path is None:
            continue
        if is_ignored(file.old_path, settings.ignore):
            analysis.skipped_paths += 1
            continue
        if file.status == "R" and not file.hunks:
            analysis.hunks.append(
                Hunk(
                    id=_hunk_id(file.old_path, 0, 0),
                    path=file.new_path,
                    old_path=file.old_path,
                    old_start=0,
                    old_end=0,
                    new_start=0,
                    new_end=0,
                    kind=HunkKind.rename,
                )
            )
            continue
        for raw in file.hunks:
            if raw.old_count == 0:
                continue
            if raw.old_count > settings.max_hunk_lines:
                analysis.skipped_oversize += 1
                continue
            analysis.hunks.append(_build_hunk(file, raw))

    moves.mark_moves(analysis.hunks, pool)
    analysis.hunks = _apply_symbols(git, base, analysis.hunks)
    return analysis
