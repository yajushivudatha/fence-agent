"""SQLite cache inside the git directory, so repeat runs skip slow git calls.

Entries are keyed by what makes them true. Commit metadata is keyed by SHA,
since commits never change. Blame and authorship are keyed by base commit, path
and line range. The spec suggests the blob SHA instead, but a file can return
to an earlier blob through a revert. Its lines then belong to the revert
commit, and a blob-keyed entry would hide the revert-of-removal signal.

Any database problem disables the cache for the run instead of failing it.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from chesterton.gitutil import Git

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    used_at INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
)
"""


class Cache:
    def __init__(self, path: Path | None):
        self.path = path
        self.hits = 0
        self.misses = 0
        self._db: sqlite3.Connection | None = None
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(path, timeout=2)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(_SCHEMA)
            self._db = db
        except (OSError, sqlite3.Error):
            self._db = None

    @classmethod
    def for_repo(cls, git: Git, enabled: bool = True) -> "Cache":
        return cls(git.common_dir() / "chesterton" / "cache.db" if enabled else None)

    @property
    def enabled(self) -> bool:
        return self._db is not None

    def _disable(self) -> None:
        if self._db is not None:
            self._db.close()
        self._db = None

    def get(self, kind: str, key: str) -> Any | None:
        if self._db is None:
            return None
        try:
            row = self._db.execute("SELECT value FROM entries WHERE kind = ? AND key = ?", (kind, key)).fetchone()
            if row is None:
                self.misses += 1
                return None
            self._db.execute(
                "UPDATE entries SET used_at = ? WHERE kind = ? AND key = ?", (int(time.time()), kind, key)
            )
        except sqlite3.Error:
            self._disable()
            return None
        self.hits += 1
        return json.loads(row[0])

    def put(self, kind: str, key: str, value: Any) -> None:
        if self._db is None:
            return
        try:
            self._db.execute(
                "INSERT OR REPLACE INTO entries (kind, key, value, used_at) VALUES (?, ?, ?, ?)",
                (kind, key, json.dumps(value), int(time.time())),
            )
        except sqlite3.Error:
            self._disable()

    def prune(self, days: float) -> int:
        """Delete entries unused for `days` days. Returns how many were removed."""
        if self._db is None:
            return 0
        cutoff = time.time() - days * 86400
        try:
            removed = self._db.execute("DELETE FROM entries WHERE used_at < ?", (cutoff,)).rowcount
            self._db.commit()
            self._db.execute("VACUUM")
        except sqlite3.Error:
            self._disable()
            return 0
        return removed

    def close(self) -> None:
        if self._db is None:
            return
        try:
            self._db.commit()
        except sqlite3.Error:
            pass
        self._disable()
