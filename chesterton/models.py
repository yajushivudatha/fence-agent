"""Data types shared across the pipeline."""

from enum import StrEnum

from pydantic import BaseModel, Field


class HunkKind(StrEnum):
    deletion = "deletion"
    modification = "modification"
    move = "move"
    rename = "rename"
    whitespace = "whitespace"


class EvidenceType(StrEnum):
    commit = "commit"


class Verdict(StrEnum):
    allow = "allow"
    warn = "warn"
    block = "block"


class Hunk(BaseModel):
    id: str
    path: str
    old_path: str
    old_start: int
    old_end: int
    new_start: int
    new_end: int
    kind: HunkKind
    symbol: str | None = None
    language: str | None = None
    removed_text: str = ""
    added_text: str = ""
    is_rewrite: bool = False

    @property
    def removed_lines(self) -> list[str]:
        return self.removed_text.split("\n") if self.removed_text else []

    @property
    def added_lines(self) -> list[str]:
        return self.added_text.split("\n") if self.added_text else []


class Evidence(BaseModel):
    id: str
    type: EvidenceType
    ref: str
    url: str | None = None
    excerpt: str
    signals: list[str]
    weight: int
    committed_at: int


class Finding(BaseModel):
    id: str
    hunk_id: str
    path: str
    symbol: str | None
    old_start: int
    old_end: int
    kind: HunkKind
    is_rewrite: bool
    heuristic_score: float
    modifiers: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    explanation: str
    verdict: Verdict


class Stats(BaseModel):
    elapsed_ms: int = 0
    hunks: int = 0
    skipped_paths: int = 0
    skipped_oversize: int = 0
    blame_failures: int = 0
    origin_commits: int = 0


class Report(BaseModel):
    repo: str
    base: str
    head: str
    findings: list[Finding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    stats: Stats = Field(default_factory=Stats)
    tool_version: str
