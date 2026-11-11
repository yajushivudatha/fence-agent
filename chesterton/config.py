"""Tool settings, read from `.chesterton.toml` at the repository root."""

import re
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chesterton.history.signals import KEYWORDS
from chesterton.scoring.weights import WEIGHTS

CONFIG_FILE = ".chesterton.toml"

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


class ConfigError(Exception):
    pass


class Mode(StrEnum):
    advisory = "advisory"
    standard = "standard"
    strict = "strict"


@dataclass(frozen=True)
class Thresholds:
    llm: float = 35  # read by the LLM reasoner in M3
    warn: float = 40
    block: float = 75


@dataclass(frozen=True)
class Settings:
    mode: Mode = Mode.standard
    fail_closed: bool = False
    thresholds: Thresholds = field(default_factory=Thresholds)
    ignore: tuple[str, ...] = DEFAULT_IGNORE
    max_hunk_lines: int = 500
    weights: dict[str, int] = field(default_factory=lambda: dict(WEIGHTS))
    keywords: dict[str, re.Pattern[str]] = field(default_factory=lambda: dict(KEYWORDS))
    cache: bool = True


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ThresholdsFile(_Section):
    llm: float = Field(35, ge=0, le=100)
    warn: float = Field(40, ge=0, le=100)
    block: float = Field(75, ge=0, le=100)


class _PathsFile(_Section):
    ignore: list[str] = []
    use_default_ignore: bool = True


class _CacheFile(_Section):
    enabled: bool = True


class _ConfigFile(_Section):
    mode: Mode = Mode.standard
    fail_closed: bool = False
    max_hunk_lines: int = Field(500, ge=1)
    thresholds: _ThresholdsFile = _ThresholdsFile()
    paths: _PathsFile = _PathsFile()
    cache: _CacheFile = _CacheFile()
    keywords: dict[str, list[str]] = {}
    weights: dict[str, int] = {}
    # Accepted now so configs written from the spec load; used from M2 and M3.
    llm: dict[str, Any] = {}
    forge: dict[str, Any] = {}


def _extend(base: re.Pattern[str], extra: list[str]) -> re.Pattern[str]:
    alternatives = "|".join(f"(?:{pattern})" for pattern in extra)
    return re.compile(f"{base.pattern}|{alternatives}", base.flags)


def load_settings(root: Path) -> Settings:
    path = root / CONFIG_FILE
    if not path.exists():
        return Settings()
    try:
        parsed = _ConfigFile.model_validate(tomllib.loads(path.read_text()))
    except (tomllib.TOMLDecodeError, ValidationError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{CONFIG_FILE} is invalid: {exc}") from exc

    unknown_weights = set(parsed.weights) - set(WEIGHTS)
    unknown_keywords = set(parsed.keywords) - set(KEYWORDS)
    if unknown_weights or unknown_keywords:
        names = ", ".join(sorted(unknown_weights | unknown_keywords))
        raise ConfigError(f"{CONFIG_FILE} names unknown signals or keyword classes: {names}")
    if parsed.thresholds.warn > parsed.thresholds.block:
        raise ConfigError(f"{CONFIG_FILE}: the warn threshold is above the block threshold")

    keywords = dict(KEYWORDS)
    for name, extra in parsed.keywords.items():
        if extra:
            try:
                keywords[name] = _extend(KEYWORDS[name], extra)
            except re.error as exc:
                raise ConfigError(f"{CONFIG_FILE}: bad regex in keywords.{name}: {exc}") from exc

    defaults = DEFAULT_IGNORE if parsed.paths.use_default_ignore else ()
    t = parsed.thresholds
    return Settings(
        mode=parsed.mode,
        fail_closed=parsed.fail_closed,
        thresholds=Thresholds(llm=t.llm, warn=t.warn, block=t.block),
        ignore=(*defaults, *parsed.paths.ignore),
        max_hunk_lines=parsed.max_hunk_lines,
        weights={**WEIGHTS, **parsed.weights},
        keywords=keywords,
        cache=parsed.cache.enabled,
    )


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


def path_matches(path: str, pattern: str) -> bool:
    """Match a repo-relative path. A pattern with no slash matches the file name."""
    subject = path if "/" in pattern else path.rsplit("/", 1)[-1]
    return _glob_to_regex(pattern).fullmatch(subject) is not None


def is_ignored(path: str, patterns: tuple[str, ...]) -> bool:
    return any(path_matches(path, pattern) for pattern in patterns)
