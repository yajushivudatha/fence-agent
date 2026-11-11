"""Human-readable terminal report."""

from datetime import UTC, datetime

from rich.console import Console

from chesterton.models import Finding, Report, Verdict

_STYLES = {Verdict.block: "bold red", Verdict.warn: "yellow", Verdict.allow: "green"}


def _location(finding: Finding) -> str:
    return f"{finding.path}:{finding.old_start}-{finding.old_end}"


def render(report: Report, console: Console) -> None:
    def line(text: str = "", style: str | None = None) -> None:
        console.print(text, style=style, markup=False, highlight=False, soft_wrap=True)

    counts = {v: sum(f.verdict is v for f in report.findings) for v in Verdict}
    line(f"chesterton: {counts[Verdict.block]} block, {counts[Verdict.warn]} warn, {counts[Verdict.allow]} allow")

    for finding in report.findings:
        if finding.verdict is Verdict.allow:
            continue
        line()
        symbol = f"  {finding.symbol}" if finding.symbol else ""
        kind = f"{finding.kind.value}, rewrite" if finding.is_rewrite else finding.kind.value
        header = f"{finding.verdict.value.upper()}  {finding.heuristic_score:g}  {_location(finding)}{symbol}  [{kind}]"
        line(header, style=_STYLES[finding.verdict])
        line(f"  {finding.explanation}")
        for item in finding.evidence:
            date = datetime.fromtimestamp(item.committed_at, UTC).strftime("%Y-%m-%d")
            subject = item.excerpt.split("\n", 1)[0].strip()
            line(f"  {item.id}  {date}  {subject}  ({', '.join(item.signals)})", style="dim")

    if report.notes:
        line()
        for note in report.notes:
            line(f"note: {note}")
