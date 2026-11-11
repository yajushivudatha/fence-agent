"""Focused tests for parsing, classification, and scoring rules."""

from collections import Counter

import pytest

from chesterton.config import DEFAULT_IGNORE, is_ignored
from chesterton.diff.analyzer import is_whitespace_only, parse_patch
from chesterton.diff.moves import line_pool, mark_moves
from chesterton.diff.symbols import enclosing, index_symbols
from chesterton.history import signals
from chesterton.history.blame import parse_porcelain
from chesterton.models import Hunk, HunkKind
from chesterton.scoring.heuristic import combine


def test_parser_does_not_mistake_removed_sql_comment_for_header():
    patch = b"\n".join(
        [
            b"diff --git a/q.sql b/q.sql",
            b"--- a/q.sql",
            b"+++ b/q.sql",
            b"@@ -2,2 +2,0 @@",
            b"--- keep this index hint",
            b"-select 1;",
            b"",
        ]
    )
    [file] = parse_patch(patch)
    assert file.old_path == "q.sql"
    assert file.hunks[0].removed == ["-- keep this index hint", "select 1;"]


def test_parser_handles_missing_counts_and_no_newline_marker():
    patch = b"\n".join(
        [
            b"diff --git a/a b/a",
            b"--- a/a",
            b"+++ b/a",
            b"@@ -3 +3 @@",
            b"-old",
            b"\\ No newline at end of file",
            b"+new",
            b"\\ No newline at end of file",
            b"",
        ]
    )
    [file] = parse_patch(patch)
    assert (file.hunks[0].old_count, file.hunks[0].new_count) == (1, 1)
    assert file.hunks[0].removed == ["old"] and file.hunks[0].added == ["new"]


def test_parser_decodes_quoted_paths():
    patch = b'diff --git "a/caf\\303\\251.py" "a/caf\\303\\251.py"\n--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n'
    assert parse_patch(patch)[0].old_path == "café.py"


def test_whitespace_detection():
    assert is_whitespace_only(["  a = 1"], ["a  =  1"], "x.js")
    assert is_whitespace_only(["", "   "], [], "x.js")
    assert not is_whitespace_only(['x = "a b"'], ['x = "ab"'], "x.js")
    assert is_whitespace_only(["    a  =  1"], ["    a = 1"], "x.py")
    assert not is_whitespace_only(["        a()"], ["    a()"], "x.py")


def _hunk(removed, added=(), path="a.py", start=1):
    return Hunk(
        id=f"{path}{start}",
        path=path,
        old_path=path,
        old_start=start,
        old_end=start + len(removed) - 1,
        new_start=1,
        new_end=1,
        kind=HunkKind.modification if added else HunkKind.deletion,
        removed_text="\n".join(removed),
        added_text="\n".join(added),
    )


BLOCK = ["alpha_one()", "beta_two()", "gamma_three()", "delta_four()", "epsilon_five()"]


def test_move_requires_eighty_percent():
    moved = _hunk(BLOCK)
    mark_moves([moved], line_pool(BLOCK[:4]))
    assert moved.kind is HunkKind.move

    kept = _hunk(BLOCK)
    mark_moves([kept], line_pool(BLOCK[:3]))
    assert kept.kind is HunkKind.deletion


def test_short_hunks_are_not_moves():
    guard = _hunk(["if user is None:", "    raise Denied()"])
    mark_moves([guard], line_pool(guard.removed_lines))
    assert guard.kind is HunkKind.deletion


def test_lines_rewritten_in_place_are_not_a_move():
    hunk = _hunk(BLOCK, added=BLOCK)
    mark_moves([hunk], line_pool(BLOCK))
    assert hunk.kind is HunkKind.modification


def test_one_added_block_cannot_excuse_two_removals():
    first = _hunk(BLOCK, start=1)
    second = _hunk(BLOCK, start=50)
    mark_moves([first, second], line_pool(BLOCK))
    assert sorted(h.kind.value for h in (first, second)) == ["deletion", "move"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], 0), ([55], 55), ([65, 55], 81.5), ([80, 65, 55, 40], 100)],
)
def test_combine(values, expected):
    assert combine(values) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("message", "classes"),
    [
        ("Fix crash on login", {"fix"}),
        ("Add prefix to the key", set()),
        ("Handle SEV2 from the gateway", {"incident"}),
        ("Sanitize filename for CVE-2023-1234", {"security"}),
        ("Add retry on timeout", {"concurrency"}),
        ("Keep IE11 working", {"compat"}),
        ("Tidy\n\nSigned-off-by: Patch Adams <patch@example.com>", set()),
    ],
)
def test_keyword_classes(message, classes):
    assert signals.keyword_classes(message) == classes


def test_trivial_fix():
    assert signals.is_trivial_fix("Fix typo in README")
    assert not signals.is_trivial_fix("Fix crash on login")


def test_revert_target():
    assert signals.revert_target('Revert "x"\n\nThis reverts commit abc1234def.') == "abc1234def"
    assert signals.revert_target("Reverse the list") is None


def test_extract_refs():
    message = "Fix UTF-8 bug in SHA-256 (#123), ABC-77\n\nSee https://x.test/a\nFixes: PROJ-9"
    assert signals.extract_refs(message) == ["PROJ-9", "#123", "ABC-77", "https://x.test/a"]


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("vendor/a.py", True),
        ("src/vendor/a.py", True),
        ("src/generated/x/y.ts", True),
        ("yarn.lock", True),
        ("sub/Cargo.lock", True),
        ("db/migrations/001.sql", True),
        ("docs/web-security.rst", True),
        ("History.md", True),
        ("src/vendors.py", False),
        ("src/app.py", False),
    ],
)
def test_ignore_globs(path, ignored):
    assert is_ignored(path, DEFAULT_IGNORE) is ignored


def test_porcelain_counts_repeat_headers():
    sha_a = "a" * 40
    sha_b = "b" * 40
    output = f"{sha_a} 1 1 2\nauthor A\nfilename x\n\tline\n{sha_a} 2 2\n\tline\n{sha_b} 7 3 1\nauthor B\n\tline\n"
    assert parse_porcelain(output) == Counter({sha_a: 2, sha_b: 1})


@pytest.mark.parametrize(
    ("language", "source", "line", "expected"),
    [
        ("python", b"class A:\n    def f(self):\n        return 1\n", 3, "A.f"),
        ("python", b"class A:\n    @staticmethod\n    def f():\n        return 1\n", 2, "A.f"),
        ("javascript", b"const f = () => {\n  return 1;\n};\n", 2, "f"),
        ("javascript", b"res.send = function send(body) {\n  return 1;\n};\n", 2, "send"),
        ("typescript", b"class A {\n  m(): number {\n    return 1;\n  }\n}\n", 3, "A.m"),
        ("tsx", b"function App() {\n  return <div />;\n}\n", 2, "App"),
        ("go", b"package p\n\nfunc (t T) Do() int {\n\treturn 1\n}\n", 4, "Do"),
        ("java", b"class A {\n  int f() {\n    return 1;\n  }\n}\n", 3, "A.f"),
        ("rust", b"impl Foo {\n    fn bar() -> i32 {\n        1\n    }\n}\n", 3, "Foo.bar"),
        ("c", b"int *f(void) {\n  return 0;\n}\n", 2, "f"),
        ("cpp", b"int A::f() {\n  return 0;\n}\n", 2, "A::f"),
    ],
)
def test_symbols(language, source, line, expected):
    symbol = enclosing(index_symbols(source, language), line, line)
    assert symbol is not None and symbol.name == expected
