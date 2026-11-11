"""Human-readable terminal output."""

from datetime import UTC, datetime

from rich.console import Console

from chesterton.models import Evidence, EvidenceType, Explanation, Finding, Report, Verdict

_STYLES = {Verdict.block: "bold red", Verdict.warn: "yellow", Verdict.allow: "green"}


def _date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")


def _printer(console: Console):
    def line(text: str = "", style: str | None = None) -> None:
        console.print(text, style=style, markup=False, highlight=False, soft_wrap=True)

    return line


def _evidence_line(item: Evidence) -> str:
    first_line = item.excerpt.split("\n", 1)[0].strip()
    signals = ", ".join(item.signals)
    if item.type is EvidenceType.commit:
        return f"  {item.id}  {_date(item.committed_at or 0)}  {first_line}  ({signals})"
    return f"  {item.id}  {first_line}  ({signals})"


def _finding_block(finding: Finding, line) -> None:
    line(f"  {finding.explanation}")
    for item in finding.evidence:
        line(_evidence_line(item), style="dim")


def render(report: Report, console: Console) -> None:
    line = _printer(console)
    counts = {v: sum(f.verdict is v for f in report.findings) for v in Verdict}
    line(f"chesterton: {counts[Verdict.block]} block, {counts[Verdict.warn]} warn, {counts[Verdict.allow]} allow")

    for finding in report.findings:
        if finding.verdict is Verdict.allow:
            continue
        line()
        symbol = f"  {finding.symbol}" if finding.symbol else ""
        kind = f"{finding.kind.value}, rewrite" if finding.is_rewrite else finding.kind.value
        location = f"{finding.path}:{finding.old_start}-{finding.old_end}"
        line(f"{finding.verdict.value.upper()}  {finding.heuristic_score:g}  {location}{symbol}  [{kind}]", style=_STYLES[finding.verdict])
        _finding_block(finding, line)

    if report.notes:
        line()
        for note in report.notes:
            line(f"note: {note}")


def render_explanation(explanation: Explanation, console: Console) -> None:
    line = _printer(console)
    finding = explanation.finding
    where = f" in {finding.symbol}" if finding.symbol else ""
    span = f"{explanation.start}" if explanation.start == explanation.end else f"{explanation.start}-{explanation.end}"
    line(f"{explanation.path}:{span}{where}  (at {explanation.rev[:7]})")
    width = len(str(explanation.end))
    for offset, text in enumerate(explanation.lines):
        line(f"  {explanation.start + offset:>{width}} | {text}", style="dim")

    line()
    line(f"If removed: {finding.verdict.value.upper()}  {finding.heuristic_score:g}", style=_STYLES[finding.verdict])
    _finding_block(finding, line)

    if explanation.history:
        line()
        line("History of these lines, newest first:")
        for entry in explanation.history:
            line(f"  {entry.sha[:7]}  {_date(entry.committed_at)}  {entry.author}  {entry.subject}")

    notes = list(explanation.notes)
    if explanation.uncommitted_changes:
        notes.append(f"{explanation.path} has uncommitted changes. Line numbers refer to {explanation.rev[:7]}.")
    if notes:
        line()
        for note in notes:
            line(f"note: {note}")
