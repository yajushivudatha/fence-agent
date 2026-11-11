import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "repos"))


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch):
    """Keep the developer's own git config out of the results."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
