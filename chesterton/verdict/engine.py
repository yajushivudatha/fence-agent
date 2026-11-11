"""Turn scores into verdicts and verdicts into exit codes."""

from chesterton.config import Mode, Thresholds
from chesterton.models import Finding, Verdict

EXIT_OK = 0
EXIT_BLOCK = 1
EXIT_STRICT_WARN = 2
EXIT_TOOL_ERROR = 3


def decide(score: float, thresholds: Thresholds, floor: Verdict | None = None) -> Verdict:
    """Verdict from the score, raised to `floor` when a fence registry entry demands it."""
    if score >= thresholds.block:
        verdict = Verdict.block
    elif score >= thresholds.warn:
        verdict = Verdict.warn
    else:
        verdict = Verdict.allow
    return floor if floor is not None and floor.rank > verdict.rank else verdict


def exit_code(findings: list[Finding], mode: Mode) -> int:
    if mode is Mode.advisory:
        return EXIT_OK
    verdicts = {f.verdict for f in findings}
    if Verdict.block in verdicts:
        return EXIT_BLOCK
    if mode is Mode.strict and Verdict.warn in verdicts:
        return EXIT_STRICT_WARN
    return EXIT_OK
