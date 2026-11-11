"""Build scripted git repositories with known fences.

Each scenario leaves a change staged and records what the tool should say about
it. Authors and dates are fixed, so commit SHAs are the same on every run.

Run directly to create every scenario for manual testing:

    python tests/fixtures/repos/make_repo.py /tmp/fence-fixtures
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ALICE = ("Alice Example", "alice@example.com")
BOB = ("Bob Example", "bob@example.com")
CAROL = ("Carol Example", "carol@example.com")


class Repo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        full_env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            **(env or {}),
        }
        done = subprocess.run(["git", *args], cwd=self.path, env=full_env, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def write(self, name: str, text: str) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def stage(self) -> None:
        self.git("add", "-A")

    def commit(self, message: str, author: tuple[str, str] = ALICE, date: str = "2024-01-15T12:00:00Z") -> str:
        self.stage()
        name, email = author
        env = {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": date,
        }
        self.git("commit", "-q", "--no-gpg-sign", "--allow-empty", "-m", message, env=env)
        return self.git("rev-parse", "HEAD")


def _padding(count: int, prefix: str = "helper") -> str:
    return "".join(f"def {prefix}_{i}(value):\n    total = value + {i}\n    return total\n\n\n" for i in range(count))


CHARGE = """def charge(order):
    amount = order.amount
    tax = order.tax
    total = amount + tax
    receipt = gateway.submit(total)
    ledger.record(receipt)
    mailer.send(order.customer)
    return receipt
"""

GUARD = """    if receipt is None:
        raise GatewayError("empty response")
"""

CHARGE_GUARDED = CHARGE.replace("    ledger.record(receipt)\n", GUARD + "    ledger.record(receipt)\n")
BASE = _padding(3) + CHARGE


def _seed_guard(repo: Repo, message: str) -> None:
    repo.write("billing.py", BASE)
    repo.commit("Add billing module", date="2024-01-01T12:00:00Z")
    repo.write("billing.py", _padding(3) + CHARGE_GUARDED)
    repo.commit(message)


def surgical_fix(repo: Repo) -> None:
    _seed_guard(repo, "Fix crash when gateway returns an empty body\n\nFixes #42")
    repo.write("billing.py", BASE)
    repo.stage()


def incident_surgical(repo: Repo) -> None:
    _seed_guard(repo, "Fix duplicate charges during gateway outage (INC-412)")
    repo.write("billing.py", BASE)
    repo.stage()


def _big_block(prefix: str) -> str:
    return _padding(5, prefix)


def incident_large(repo: Repo) -> None:
    repo.write("billing.py", BASE)
    repo.commit("Add billing module", date="2024-01-01T12:00:00Z")
    repo.write("billing.py", BASE + "\n\n" + _big_block("idempotent"))
    repo.commit("Add idempotency helpers after the March outage")
    repo.write("billing.py", BASE)
    repo.stage()


def concurrency_large(repo: Repo) -> None:
    repo.write("billing.py", BASE)
    repo.commit("Add billing module", date="2024-01-01T12:00:00Z")
    repo.write("billing.py", BASE + "\n\n" + _big_block("backoff"))
    repo.commit("Add backoff for gateway timeouts")
    repo.write("billing.py", BASE)
    repo.stage()


def revert_of_removal(repo: Repo) -> None:
    repo.write("billing.py", _padding(3) + CHARGE_GUARDED)
    repo.commit("Add billing module", date="2024-01-01T12:00:00Z")
    repo.write("billing.py", BASE)
    removal = repo.commit("Remove unused check", date="2024-01-02T12:00:00Z")
    repo.write("billing.py", _padding(3) + CHARGE_GUARDED)
    repo.commit(f'Revert "Remove unused check"\n\nThis reverts commit {removal}.', date="2024-01-03T12:00:00Z")
    repo.write("billing.py", BASE)
    repo.stage()


MOVABLE = """def reconcile(batch):
    pending = batch.pending_items()
    settled = ledger.settle(pending)
    report = summarize(settled)
    archive.store(report)
    return report
"""


def _seed_movable(repo: Repo) -> str:
    repo.write("billing.py", BASE)
    repo.commit("Add billing module", date="2024-01-01T12:00:00Z")
    text = _padding(3) + MOVABLE + "\n\n" + CHARGE
    repo.write("billing.py", text)
    repo.commit("Add reconcile after the settlement outage")
    return text


def move_same_file(repo: Repo) -> None:
    _seed_movable(repo)
    repo.write("billing.py", BASE + "\n\n" + MOVABLE)
    repo.stage()


def move_to_new_file(repo: Repo) -> None:
    _seed_movable(repo)
    repo.write("billing.py", BASE)
    repo.write("payments/reconcile.py", "import ledger\n\n\n" + MOVABLE)
    repo.stage()


def whitespace_reindent(repo: Repo) -> None:
    source = "function charge(order) {\n  if (!order) {\n    throw new Error('empty order');\n  }\n}\n"
    repo.write("billing.js", "function charge(order) {\n}\n")
    repo.commit("Add billing module", date="2024-01-01T12:00:00Z")
    repo.write("billing.js", source)
    repo.commit("Fix crash on empty order")
    repo.write("billing.js", source.replace("  if", "    if").replace("    throw", "        throw").replace("  }", "    }"))
    repo.stage()


def python_semantic_dedent(repo: Repo) -> None:
    template = "def charge(order):\n    audit(order)\n    if order.retry:\n        refund(order)\n{line}    log(order)\n    return order\n"
    repo.write("billing.py", template.format(line=""))
    repo.commit("Add billing module", date="2024-01-01T12:00:00Z")
    repo.write("billing.py", template.format(line="        notify_customer(order)\n"))
    repo.commit("Fix missing refund notice")
    repo.write("billing.py", template.format(line="    notify_customer(order)\n"))
    repo.stage()


def pure_rename(repo: Repo) -> None:
    _seed_guard(repo, "Fix crash when gateway returns an empty body")
    repo.git("mv", "billing.py", "payments.py")
    repo.stage()


def plain_removal(repo: Repo) -> None:
    _seed_guard(repo, "Add response logging")
    repo.write("billing.py", BASE)
    repo.stage()


def trivial_fix_message(repo: Repo) -> None:
    _seed_guard(repo, "Fix typo in gateway error")
    repo.write("billing.py", BASE)
    repo.stage()


def bulk_origin(repo: Repo) -> None:
    repo.write("billing.py", _padding(3) + CHARGE_GUARDED)
    repo.commit("Initial commit with security hardening", date="2024-01-01T12:00:00Z")
    repo.write("billing.py", BASE)
    repo.stage()


def old_many_authors(repo: Repo) -> None:
    template = "def charge(order):\n    audit(order)\n{a}{b}{c}    submit(order)\n    settle(order)\n    return order\n"
    empty = template.format(a="", b="", c="")
    a, b, c = "    step_one(order)\n", "    step_two(order)\n", "    step_three(order)\n"
    repo.write("billing.py", empty)
    repo.commit("Add billing module", date="2019-06-01T12:00:00Z")
    repo.write("billing.py", template.format(a=a, b="", c=""))
    repo.commit("Add step one", author=ALICE, date="2020-01-01T12:00:00Z")
    repo.write("billing.py", template.format(a=a, b=b, c=""))
    repo.commit("Add step two", author=BOB, date="2020-02-01T12:00:00Z")
    repo.write("billing.py", template.format(a=a, b=b, c=c))
    repo.commit("Add step three", author=CAROL, date="2020-03-01T12:00:00Z")
    repo.write("unrelated.txt", "later work\n")
    repo.commit("Unrelated later commit", date="2024-01-15T12:00:00Z")
    repo.write("billing.py", empty)
    repo.stage()


def rewrite_keeps_guard(repo: Repo) -> None:
    _seed_guard(repo, "Fix crash when gateway returns an empty body")
    rewritten = """def charge(order):
    subtotal = pricing.subtotal(order)
    levy = pricing.levy(order)
    combined = pricing.combine(subtotal, levy)
    receipt = processor.send(combined)
""" + GUARD + """    books.write(receipt)
    notifier.notify(order.customer)
    return receipt
"""
    repo.write("billing.py", _padding(3) + rewritten)
    repo.stage()


def vendored_path(repo: Repo) -> None:
    repo.write("vendor/lib.py", BASE)
    repo.commit("Add vendored library", date="2024-01-01T12:00:00Z")
    repo.write("vendor/lib.py", _padding(1))
    repo.stage()


def mixed_report(repo: Repo) -> None:
    repo.write("billing.py", BASE)
    repo.write("retry.py", BASE)
    repo.commit("Add billing module", date="2024-01-01T12:00:00Z")
    repo.write("billing.py", _padding(3) + CHARGE_GUARDED)
    repo.commit("Fix duplicate charges during gateway outage (INC-412)")
    repo.write("retry.py", _padding(3) + CHARGE_GUARDED)
    repo.commit("Fix crash when gateway returns an empty body")
    repo.write("billing.py", BASE)
    repo.write("retry.py", BASE)
    repo.stage()


@dataclass(frozen=True)
class Scenario:
    build: Callable[[Repo], None]
    verdict: str
    kind: str
    score: float
    signals: frozenset[str] = field(default_factory=frozenset)
    rewrite: bool = False


def _s(*names: str) -> frozenset[str]:
    return frozenset(names)


SCENARIOS: dict[str, Scenario] = {
    "surgical_fix": Scenario(surgical_fix, "warn", "deletion", 55, _s("fix_surgical")),
    "incident_surgical": Scenario(incident_surgical, "block", "deletion", 81.5, _s("incident_or_security", "fix_surgical")),
    "incident_large": Scenario(incident_large, "warn", "deletion", 65, _s("incident_or_security")),
    "concurrency_large": Scenario(concurrency_large, "warn", "deletion", 40, _s("concurrency_or_compat")),
    "revert_of_removal": Scenario(revert_of_removal, "block", "deletion", 80, _s("revert_of_removal")),
    "move_same_file": Scenario(move_same_file, "allow", "move", 6.5, _s("incident_or_security")),
    "move_to_new_file": Scenario(move_to_new_file, "allow", "move", 6.5, _s("incident_or_security")),
    "whitespace_reindent": Scenario(whitespace_reindent, "allow", "whitespace", 0),
    "python_semantic_dedent": Scenario(python_semantic_dedent, "warn", "modification", 55, _s("fix_surgical")),
    "pure_rename": Scenario(pure_rename, "allow", "rename", 0),
    "plain_removal": Scenario(plain_removal, "allow", "deletion", 0),
    "trivial_fix_message": Scenario(trivial_fix_message, "allow", "deletion", 0),
    "bulk_origin": Scenario(bulk_origin, "allow", "deletion", 32.5, _s("incident_or_security")),
    "old_many_authors": Scenario(old_many_authors, "allow", "deletion", 15, _s("old_many_authors")),
    "rewrite_keeps_guard": Scenario(rewrite_keeps_guard, "warn", "modification", 55, _s("fix_surgical"), rewrite=True),
}


def build(name: str, path: Path) -> Repo:
    repo = Repo(path)
    builder = mixed_report if name == "mixed_report" else vendored_path if name == "vendored_path" else SCENARIOS[name].build
    builder(repo)
    return repo


def main(dest: str) -> None:
    root = Path(dest)
    if root.exists():
        shutil.rmtree(root)
    for name in [*SCENARIOS, "mixed_report", "vendored_path"]:
        build(name, root / name)
        print(root / name)


if __name__ == "__main__":
    main(sys.argv[1])
