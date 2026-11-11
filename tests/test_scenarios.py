"""End-to-end checks against scripted repositories."""

import json
import os
from io import StringIO
from pathlib import Path

import pytest
from make_repo import SCENARIOS, Repo, build
from rich.console import Console
from typer.testing import CliRunner

from chesterton.cli import app
from chesterton.config import Settings
from chesterton.pipeline import Selection, run_check
from chesterton.report import text

runner = CliRunner()
GOLDEN = Path(__file__).parent / "golden" / "mixed_report.txt"


def _top(report):
    assert report.findings, "expected at least one finding"
    return report.findings[0]


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name, tmp_path):
    expected = SCENARIOS[name]
    repo = build(name, tmp_path / "repo")
    finding = _top(run_check(repo.path, Selection(), Settings()))
    assert finding.kind.value == expected.kind
    assert finding.verdict.value == expected.verdict
    assert finding.heuristic_score == pytest.approx(expected.score)
    assert {s for e in finding.evidence for s in e.signals} == expected.signals
    assert finding.is_rewrite is expected.rewrite


def test_non_allow_findings_cite_commits(tmp_path):
    repo = build("incident_surgical", tmp_path / "repo")
    finding = _top(run_check(repo.path, Selection(), Settings()))
    assert finding.evidence[0].id.startswith("commit:")
    assert "INC-412" in finding.explanation
    assert finding.symbol == "charge"


def test_ignored_paths_are_counted_not_scored(tmp_path):
    repo = build("vendored_path", tmp_path / "repo")
    report = run_check(repo.path, Selection(), Settings())
    assert report.findings == []
    assert report.stats.skipped_paths == 1


def test_oversize_hunks_are_reported(tmp_path):
    repo = build("incident_large", tmp_path / "repo")
    report = run_check(repo.path, Selection(), Settings(max_hunk_lines=3))
    assert report.findings == []
    assert report.stats.skipped_oversize == 1
    assert any("not analyzed" in note for note in report.notes)


def test_base_and_range_selection(tmp_path):
    repo = build("incident_surgical", tmp_path / "repo")
    main_tip = repo.git("rev-parse", "HEAD")
    repo.git("checkout", "-q", "-b", "feature")
    repo.commit("Remove the check")
    head = repo.git("rev-parse", "HEAD")

    for selection in (Selection(base_ref="main"), Selection(commit_range=f"{main_tip}..{head}")):
        report = run_check(repo.path, selection, Settings())
        assert _top(report).verdict.value == "block"
        assert report.base == main_tip and report.head == head


def test_three_dot_range_uses_merge_base(tmp_path):
    repo = build("incident_surgical", tmp_path / "repo")
    repo.git("reset", "-q", "--hard")
    fork = repo.git("rev-parse", "HEAD")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("billing.py", (repo.path / "billing.py").read_text().replace("    if receipt is None:\n", "    if not receipt:\n"))
    repo.commit("Tidy check")
    repo.git("checkout", "-q", "main")
    repo.write("other.txt", "moves main forward\n")
    repo.commit("Unrelated")
    report = run_check(repo.path, Selection(commit_range="main...feature"), Settings())
    assert report.base == fork


def test_shallow_clone_notes_missing_history(tmp_path):
    source = build("incident_surgical", tmp_path / "source")
    source.git("reset", "-q", "--hard")
    Repo(tmp_path / "unused")
    clone = tmp_path / "clone"
    source.git("clone", "-q", "--depth", "1", f"file://{source.path}", str(clone))
    shallow = Repo(clone)
    text_before = (clone / "billing.py").read_text()
    shallow.write("billing.py", text_before.replace('    if receipt is None:\n        raise GatewayError("empty response")\n', ""))
    shallow.stage()
    report = run_check(clone, Selection(), Settings())
    assert any("shallow" in note for note in report.notes)
    assert _top(report).verdict.value == "allow"
    assert _top(report).evidence == []


def test_repo_without_commits(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.write("a.py", "x = 1\n")
    repo.stage()
    report = run_check(repo.path, Selection(), Settings())
    assert report.findings == []
    assert report.notes


def test_paths_with_spaces_and_unicode(tmp_path):
    repo = Repo(tmp_path / "repo")
    name = 'my dir/café "bill".py'
    repo.write(name, "def f():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    return a\n")
    repo.commit("Add module", date="2024-01-01T12:00:00Z")
    repo.write(name, "def f():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    if a:\n        boom()\n    return a\n")
    repo.commit("Fix boom")
    repo.write(name, "def f():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    return a\n")
    repo.stage()
    finding = _top(run_check(repo.path, Selection(), Settings()))
    assert finding.path == name
    assert finding.verdict.value == "warn"


def test_runs_from_a_subdirectory(tmp_path):
    repo = build("surgical_fix", tmp_path / "repo")
    (repo.path / "nested").mkdir()
    assert _top(run_check(repo.path / "nested", Selection(), Settings())).verdict.value == "warn"


def test_golden_text_report(tmp_path):
    repo = build("mixed_report", tmp_path / "repo")
    report = run_check(repo.path, Selection(), Settings())
    buffer = StringIO()
    text.render(report, Console(file=buffer, no_color=True, width=120))
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(buffer.getvalue())
    assert buffer.getvalue() == GOLDEN.read_text()


@pytest.mark.parametrize(
    ("scenario", "mode", "code"),
    [
        ("incident_surgical", "standard", 1),
        ("incident_surgical", "strict", 1),
        ("incident_surgical", "advisory", 0),
        ("surgical_fix", "standard", 0),
        ("surgical_fix", "strict", 2),
        ("plain_removal", "strict", 0),
    ],
)
def test_exit_codes(scenario, mode, code, tmp_path, monkeypatch):
    repo = build(scenario, tmp_path / "repo")
    monkeypatch.chdir(repo.path)
    assert runner.invoke(app, ["check", "--mode", mode]).exit_code == code


def test_json_output(tmp_path, monkeypatch):
    repo = build("surgical_fix", tmp_path / "repo")
    monkeypatch.chdir(repo.path)
    result = runner.invoke(app, ["check", "--format", "json"])
    payload = json.loads(result.stdout)
    assert payload["head"] == "INDEX"
    assert payload["findings"][0]["verdict"] == "warn"


@pytest.mark.parametrize(("flags", "code"), [([], 0), (["--fail-closed"], 3)])
def test_tool_errors_fail_open_by_default(flags, code, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["check", *flags]).exit_code == code


def test_conflicting_selectors_are_rejected(tmp_path, monkeypatch):
    repo = build("surgical_fix", tmp_path / "repo")
    monkeypatch.chdir(repo.path)
    assert runner.invoke(app, ["check", "--staged", "--base", "main"]).exit_code == 3
