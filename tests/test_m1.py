"""Config, cache, local signals, the fence registry, and `explain`."""

import json
import re

import pytest
from make_repo import build
from typer.testing import CliRunner

from chesterton.cache import Cache
from chesterton.cli import app
from chesterton.config import ConfigError, Mode, Settings, Thresholds, load_settings
from chesterton.local import comments, registry, tests
from chesterton.models import Hunk, HunkKind, Verdict
from chesterton.pipeline import Selection, run_check
from chesterton.verdict.engine import decide

runner = CliRunner()


def _top(report):
    assert report.findings
    return report.findings[0]


def _config(repo, text):
    (repo.path / ".chesterton.toml").write_text(text)


# Config


def test_missing_config_gives_defaults(tmp_path):
    assert load_settings(tmp_path) == Settings()


def test_config_overrides(tmp_path):
    (tmp_path / ".chesterton.toml").write_text(
        """
mode = "strict"
max_hunk_lines = 50

[thresholds]
warn = 30
block = 60

[paths]
ignore = ["legacy/**"]

[keywords]
incident = ["INC-\\\\d+", "pager"]

[weights]
fix_surgical = 80

[llm]
provider = "anthropic"
"""
    )
    settings = load_settings(tmp_path)
    assert settings.mode is Mode.strict
    assert settings.thresholds == Thresholds(llm=35, warn=30, block=60)
    assert settings.max_hunk_lines == 50
    assert "legacy/**" in settings.ignore and "*.lock" in settings.ignore
    assert settings.weights["fix_surgical"] == 80
    assert settings.keywords["incident"].search("woken by the PAGER")
    assert settings.keywords["incident"].search("an outage")


@pytest.mark.parametrize(
    "text",
    [
        "mode = 'loud'",
        "[weights]\nmade_up = 5",
        "[keywords]\nfix = ['(']",
        "[thresholds]\nwarn = 80\nblock = 50",
        "surprise = true",
        "not toml",
    ],
)
def test_invalid_config_is_rejected(tmp_path, text):
    (tmp_path / ".chesterton.toml").write_text(text)
    with pytest.raises(ConfigError):
        load_settings(tmp_path)


def test_config_changes_verdicts(tmp_path, monkeypatch):
    repo = build("surgical_fix", tmp_path / "repo")
    _config(repo, "[weights]\nfix_surgical = 80\n")
    monkeypatch.chdir(repo.path)
    assert runner.invoke(app, ["check"]).exit_code == 1


def test_config_mode_and_cli_override(tmp_path, monkeypatch):
    repo = build("surgical_fix", tmp_path / "repo")
    _config(repo, 'mode = "strict"\n')
    monkeypatch.chdir(repo.path)
    assert runner.invoke(app, ["check"]).exit_code == 2
    assert runner.invoke(app, ["check", "--mode", "standard"]).exit_code == 0


def test_config_keywords_and_ignore(tmp_path, monkeypatch):
    repo = build("plain_removal", tmp_path / "repo")
    _config(repo, '[keywords]\nincident = ["response logging"]\n')
    monkeypatch.chdir(repo.path)
    payload = json.loads(runner.invoke(app, ["check", "--format", "json"]).stdout)
    assert payload["findings"][0]["verdict"] == "warn"

    _config(repo, '[paths]\nignore = ["billing.py"]\n')
    payload = json.loads(runner.invoke(app, ["check", "--format", "json"]).stdout)
    assert payload["findings"] == []


@pytest.mark.parametrize(("flags", "code"), [([], 0), (["--fail-closed"], 3)])
def test_broken_config_fails_open_by_default(tmp_path, monkeypatch, flags, code):
    repo = build("incident_surgical", tmp_path / "repo")
    _config(repo, "mode = 'loud'")
    monkeypatch.chdir(repo.path)
    result = runner.invoke(app, ["check", *flags])
    assert result.exit_code == code
    assert ".chesterton.toml" in result.stderr


def test_fail_closed_from_config(tmp_path, monkeypatch):
    repo = build("surgical_fix", tmp_path / "repo")
    _config(repo, "fail_closed = true\n")
    monkeypatch.chdir(repo.path)
    assert runner.invoke(app, ["check", "--base", "no-such-branch"]).exit_code == 3


# Cache


def test_second_run_is_served_from_cache(tmp_path):
    repo = build("incident_surgical", tmp_path / "repo")
    first = run_check(repo.path, Selection(), Settings())
    second = run_check(repo.path, Selection(), Settings())
    assert first.stats.cache_misses > 0
    assert second.stats.cache_misses == 0 and second.stats.cache_hits > 0
    assert second.findings == first.findings


def test_cache_can_be_disabled(tmp_path):
    repo = build("incident_surgical", tmp_path / "repo")
    run_check(repo.path, Selection(), Settings())
    report = run_check(repo.path, Selection(), Settings(cache=False))
    assert report.stats.cache_hits == 0


def test_corrupt_cache_does_not_break_the_check(tmp_path):
    repo = build("incident_surgical", tmp_path / "repo")
    db = repo.path / ".git" / "chesterton" / "cache.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"this is not a database" * 100)
    assert _top(run_check(repo.path, Selection(), Settings())).verdict is Verdict.block


def test_cache_prune(tmp_path, monkeypatch):
    repo = build("incident_surgical", tmp_path / "repo")
    run_check(repo.path, Selection(), Settings())
    monkeypatch.chdir(repo.path)
    assert runner.invoke(app, ["cache", "prune"]).stdout.strip() == "Removed 0 cache entries."
    removed = re.search(r"\d+", runner.invoke(app, ["cache", "prune", "--days", "0"]).stdout)
    assert int(removed[0]) > 0


def test_cache_round_trip(tmp_path):
    cache = Cache(tmp_path / "c.db")
    assert cache.get("k", "a") is None
    cache.put("k", "a", {"x": [1, 2]})
    cache.close()
    reopened = Cache(tmp_path / "c.db")
    assert reopened.get("k", "a") == {"x": [1, 2]}
    assert (reopened.hits, reopened.misses) == (1, 0)


# Local signals


@pytest.mark.parametrize(
    ("line", "signal"),
    [
        ("    # Do not remove, see INC-1", "do_not_remove_comment"),
        ("    // needed because Safari drops the header", "do_not_remove_comment"),
        ("  /* workaround for kernel bug */", "do_not_remove_comment"),
        ("    # NOTE: order matters", "explanatory_comment"),
        ("    x = 1  # see #123", "explanatory_comment"),
        ("    -- HACK: planner picks the wrong index", "explanatory_comment"),
        ('    raise Error("do not remove")', None),
        ("    # plain comment", None),
    ],
)
def test_comment_patterns(line, signal):
    found = comments.scan("f.py", [line], 1, 1, Settings().weights)
    assert (found[0].signals[0] if found else None) == signal


def test_comments_count_only_nearby_lines():
    lines = ["# do not remove"] + ["x = 1"] * 10
    weights = Settings().weights
    assert comments.scan("f.py", lines, 4, 4, weights)
    assert not comments.scan("f.py", lines, 5, 5, weights)


def _hunk(symbol, path="billing.py", start=1, end=1):
    return Hunk(id="h", path=path, old_path=path, old_start=start, old_end=end, new_start=1, new_end=1, kind=HunkKind.deletion, symbol=symbol)


@pytest.mark.parametrize(
    ("symbol", "path", "expected"),
    [
        ("Billing.charge_card", "billing.py", "charge_card"),
        ("ns::Billing::charge_card", "billing.cc", "charge_card"),
        ("run", "billing.py", None),
        ("__init__", "billing.py", None),
        ("abc", "billing.py", None),
        ("charge_card", "tests/test_billing.py", None),
        (None, "billing.py", None),
    ],
)
def test_searchable_name(symbol, path, expected):
    assert tests.searchable_name(_hunk(symbol, path)) == expected


@pytest.mark.parametrize(
    ("path", "is_test"),
    [
        ("tests/a.py", True),
        ("pkg/test_a.py", True),
        ("a_test.go", True),
        ("src/a.spec.ts", True),
        ("src/FooTest.java", True),
        ("src/latest.py", False),
        ("src/contest/a.py", False),
    ],
)
def test_is_test_path(path, is_test):
    assert tests.is_test_path(path) is is_test


# Registry


def test_registry_parsing_reports_bad_entries():
    raw = b"- path: a.py\n  reason: ok\n- path: b.py\n- path: c.py\n  reason: x\n  level: maybe\n- 7\n"
    fences, problems = registry._parse(".fences.yml", raw)
    assert [f.path for f in fences] == ["a.py"] and fences[0].level is Verdict.block
    assert len(problems) == 3


def test_registry_must_be_a_list():
    assert registry._parse(".fences.yml", b"path: a.py\n")[1]
    assert registry._parse(".fences.yml", b": : :\n  - [")[1]


def test_registry_symbol_names_match_qualified():
    assert registry._names_match("charge", "Billing.charge")
    assert registry._names_match("charge", "billing::charge")
    assert not registry._names_match("charge", "recharge")


def test_registry_floor_ignores_moves():
    fence = registry.Fence("a.py", None, "why", Verdict.block)
    assert registry.floor([fence], HunkKind.deletion) is Verdict.block
    assert registry.floor([fence], HunkKind.move) is None


def test_decide_respects_floor():
    t = Thresholds()
    assert decide(10, t, Verdict.warn) is Verdict.warn
    assert decide(90, t, Verdict.warn) is Verdict.block
    assert decide(10, t) is Verdict.allow


def test_broken_registry_is_noted_not_fatal(tmp_path):
    repo = build("surgical_fix", tmp_path / "repo")
    repo.git("stash", "-q")
    repo.write(".fences.yml", "{ not: [valid")
    repo.commit("Add fences")
    repo.git("stash", "pop", "-q", "--index")
    report = run_check(repo.path, Selection(), Settings())
    assert any(".fences.yml" in note for note in report.notes)
    assert _top(report).verdict is Verdict.warn


# Explain


def test_explain_cli(tmp_path, monkeypatch):
    repo = build("incident_surgical", tmp_path / "repo")
    (repo.path / "sub").mkdir()
    monkeypatch.chdir(repo.path / "sub")
    result = runner.invoke(app, ["explain", "../billing.py:21-22"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "billing.py:21-22 in charge" in out
    assert "If removed: BLOCK  81.5" in out
    assert "Fix duplicate charges during gateway outage (INC-412)" in out
    assert "History of these lines" in out
    assert "uncommitted changes" in out


def test_explain_json(tmp_path, monkeypatch):
    repo = build("surgical_fix", tmp_path / "repo")
    repo.git("reset", "-q", "--hard")
    monkeypatch.chdir(repo.path)
    payload = json.loads(runner.invoke(app, ["explain", "billing.py:21", "--format", "json"]).stdout)
    assert payload["finding"]["verdict"] == "warn"
    assert payload["lines"] == ["    if receipt is None:"]
    assert payload["uncommitted_changes"] is False
    assert [h["subject"] for h in payload["history"]][0].startswith("Fix crash")


@pytest.mark.parametrize("target", ["billing.py", "billing.py:0", "billing.py:9-3", "billing.py:999", "nope.py:1", "/etc/hosts:1"])
def test_explain_rejects_bad_targets(tmp_path, monkeypatch, target):
    repo = build("surgical_fix", tmp_path / "repo")
    monkeypatch.chdir(repo.path)
    assert runner.invoke(app, ["explain", target]).exit_code == 3
