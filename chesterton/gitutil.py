"""Thin wrapper around the git command line."""

import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, root: Path):
        self.root = root
        self._env = {**os.environ, "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"}
        self._env.pop("GIT_EXTERNAL_DIFF", None)

    @classmethod
    def discover(cls, cwd: Path) -> "Git":
        probe = cls(cwd)
        return cls(Path(probe.text("rev-parse", "--show-toplevel").strip()))

    def run(self, *args: str, stdin: bytes | None = None, ok_codes: tuple[int, ...] = (0,)) -> bytes:
        cmd = ["git", "-C", str(self.root), "-c", "core.quotePath=false", *args]
        try:
            proc = subprocess.run(cmd, input=stdin, capture_output=True, env=self._env)
        except OSError as exc:
            raise GitError(f"could not run git: {exc}") from exc
        if proc.returncode not in ok_codes:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise GitError(f"git {args[0]} failed: {detail}")
        return proc.stdout

    def text(self, *args: str, stdin: bytes | None = None, ok_codes: tuple[int, ...] = (0,)) -> str:
        return self.run(*args, stdin=stdin, ok_codes=ok_codes).decode("utf-8", "replace")

    def common_dir(self) -> Path:
        path = Path(self.text("rev-parse", "--git-common-dir").strip())
        return path if path.is_absolute() else self.root / path

    def rev(self, ref: str) -> str:
        try:
            return self.text("rev-parse", "--verify", "-q", f"{ref}^{{commit}}").strip()
        except GitError as exc:
            raise GitError(f"unknown revision: {ref}") from exc

    def has_head(self) -> bool:
        try:
            self.rev("HEAD")
        except GitError:
            return False
        return True

    def blob(self, rev: str, path: str) -> bytes:
        return self.run("cat-file", "blob", f"{rev}:{path}")

    def shallow_boundaries(self) -> set[str]:
        """Commits whose parents were cut off by a shallow clone."""
        if self.text("rev-parse", "--is-shallow-repository").strip() != "true":
            return set()
        path = Path(self.text("rev-parse", "--git-path", "shallow").strip())
        if not path.is_absolute():
            path = self.root / path
        return set(path.read_text().split()) if path.exists() else set()
