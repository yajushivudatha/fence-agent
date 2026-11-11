"""Turn scores into verdicts and verdicts into exit codes."""

from enum import StrEnum

from chesterton.config import Thresholds
from chesterton.models import Finding, Verdict

EXIT_OK = 0
EXIT_BLOCK = 1
EXIT_STRICT_WARN = 2
EXIT_TOOL_ERROR = 3


class Mode(StrEnum):
    advisory = "advisory"
    standard = "standard"
    strict = "strict"


def decide(score: float, thresholds: Thresholds) -> Verdict:
    if score >= thresholds.block:
        return Verdict.block
    if score >= thresholds.warn:
        return Verdict.warn
    return Verdict.allow


def exit_code(findings: list[Finding], mode: Mode) -> int:
    if mode is Mode.advisory:
        return EXIT_OK
    verdicts = {f.verdict for f in findings}
    if Verdict.block in verdicts:
        return EXIT_BLOCK
    if mode is Mode.strict and Verdict.warn in verdicts:
        return EXIT_STRICT_WARN
    return EXIT_OK
