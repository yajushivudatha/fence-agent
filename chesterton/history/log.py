"""Read commit metadata from git."""

import re
from dataclasses import dataclass

from chesterton.diff.analyzer import parse_patch
from chesterton.diff.moves import is_meaningful, normalize
from chesterton.gitutil import Git, GitError

_RECORD_END = "\x00\x01"
_FORMAT = "%H%x00%P%x00%ae%x00%ct%x00%B%x00%x01"
_SHA = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass
class CommitInfo:
    sha: str
    parents: list[str]
    author: str
    committed_at: int
    message: str
    files_changed: int = 0
    lines_changed: int = 0

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0].strip()

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


def load_commits(git: Git, shas: set[str]) -> dict[str, CommitInfo]:
    if not shas:
        return {}
    stdin = "\n".join(sorted(shas)).encode() + b"\n"
    commits: dict[str, CommitInfo] = {}
    output = git.text("log", "--no-walk=unsorted", f"--format={_FORMAT}", "--stdin", stdin=stdin)
    for record in output.split(_RECORD_END):
        fields = record.lstrip("\n").split("\x00")
        if len(fields) != 5:
            continue
        sha, parents, author, committed_at, message = fields
        commits[sha] = CommitInfo(sha, parents.split(), author.lower(), int(committed_at), message.strip())

    numstat = git.text("diff-tree", "--stdin", "--root", "-r", "--numstat", "-M", stdin=stdin)
    current: CommitInfo | None = None
    for line in numstat.split("\n"):
        if _SHA.match(line):
            current = commits.get(line)
        elif current is not None and line.count("\t") >= 2:
            added, deleted, _ = line.split("\t", 2)
            current.files_changed += 1
            current.lines_changed += sum(int(n) for n in (added, deleted) if n.isdigit())
    return commits


def lines_removed_by(git: Git, sha: str) -> set[str] | None:
    """Normalized meaningful lines a commit deleted, or None if it can't be read."""
    try:
        patch = git.run("show", "--format=", "-U0", "--no-color", "--no-ext-diff", "--no-renames", sha)
    except GitError:
        return None
    removed = (normalize(line) for f in parse_patch(patch) for h in f.hunks for line in h.removed)
    return {line for line in removed if is_meaningful(line)}


def range_authors(git: Git, rev: str, path: str, start: int, end: int) -> set[str]:
    """Everyone who has touched these lines over their full history."""
    output = git.text("log", "-L", f"{start},{end}:{path}", "-s", "--format=%ae", rev)
    return {line.strip().lower() for line in output.split("\n") if line.strip()}


def range_history(git: Git, rev: str, path: str, start: int, end: int, limit: int) -> list[tuple[str, int, str, str]]:
    """The newest commits that touched these lines: (sha, time, author name, subject)."""
    output = git.text("log", f"-n{limit}", "-L", f"{start},{end}:{path}", "-s", "--format=%H%x00%ct%x00%an%x00%s", rev)
    entries = []
    for line in output.split("\n"):
        fields = line.split("\x00")
        if len(fields) == 4:
            entries.append((fields[0], int(fields[1]), fields[2], fields[3]))
    return entries
