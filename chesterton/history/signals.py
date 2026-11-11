"""Read intent out of commit messages."""

import re
from collections.abc import Mapping

_F = re.IGNORECASE

KEYWORDS: dict[str, re.Pattern[str]] = {
    "fix": re.compile(r"\b(?:fix(?:es|ed|ing)?|bugs?|bugfix(?:es)?|regressions?|hotfix(?:es)?|patch(?:es|ed)?|workarounds?|hack(?:s|y)?)\b", _F),
    "incident": re.compile(r"\b(?:incidents?|outages?|postmortems?|post-mortems?|sev-?[0-9]|p0|rollbacks?|INC-\d+)\b", _F),
    "security": re.compile(r"\b(?:CVE-\d+|security|xss|injections?|auth(?:entication)? bypass|sanitiz(?:e|es|ed|ation))\b", _F),
    "concurrency": re.compile(r"\b(?:races?|race condition|deadlocks?|flaky|timeouts?|retr(?:y|ies|ied))\b", _F),
    "compat": re.compile(r"\b(?:legacy|backwards?[ -]compat(?:ible|ibility)?|IE11|older clients?|migrations?)\b", _F),
}

# A surgical "fix typo" commit says nothing about why the code must exist.
_TRIVIAL_FIX = re.compile(
    r"\bfix(?:es|ed|ing)?\s+(?:(?:a|an|the|some|minor)\s+)?"
    r"(?:typos?|spelling|grammar|lint(?:ing)?|style|format(?:ting)?|whitespace|indent(?:ation)?|comments?|docs?|tests?|imports?|ci|build)\b",
    _F,
)
_PERSON_TRAILER = re.compile(r"^[A-Za-z-]+:\s.*<[^>]*@[^>]*>\s*$", re.MULTILINE)
_REVERT = re.compile(r"This reverts commit ([0-9a-f]{7,64})", _F)
_ISSUE_TRAILER = re.compile(r"^(?:Fixes|Refs|Closes|Resolves):\s*(.+)$", re.MULTILINE | _F)
_HASH_REF = re.compile(r"(?<![\w/&])#\d+\b")
_TICKET_REF = re.compile(r"\b([A-Z][A-Z0-9]+)-\d+\b")
_URL = re.compile(r"https?://[^\s)>\]]+")
_NOT_TICKETS = {"UTF", "ISO", "SHA", "CVE", "RFC", "MD", "AES", "HTTP", "TLS", "SSL", "SEV", "UCS", "ES", "PEP"}


def _prose(message: str) -> str:
    """Drop sign-off lines, whose people's names could match a keyword."""
    return _PERSON_TRAILER.sub("", message)


def keyword_classes(message: str, keywords: Mapping[str, re.Pattern[str]] = KEYWORDS) -> set[str]:
    text = _prose(message)
    return {name for name, pattern in keywords.items() if pattern.search(text)}


def is_trivial_fix(subject: str) -> bool:
    return _TRIVIAL_FIX.search(subject) is not None


def revert_target(message: str) -> str | None:
    match = _REVERT.search(message)
    return match[1] if match else None


def extract_refs(message: str) -> list[str]:
    """Issue references, trailers, and URLs, in order of first appearance."""
    refs = [m[1].strip() for m in _ISSUE_TRAILER.finditer(message)]
    refs += _HASH_REF.findall(message)
    refs += [m[0] for m in _TICKET_REF.finditer(message) if m[1] not in _NOT_TICKETS]
    refs += _URL.findall(message)
    return list(dict.fromkeys(refs))
