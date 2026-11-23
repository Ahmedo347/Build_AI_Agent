"""Shared fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def repo(tmp_path):
    """A tiny working directory the tools can operate on."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "main.py").write_text(
        'import sys\nprint("hello", *sys.argv[1:])\n', encoding="utf-8"
    )
    (root / "pkg" / "calc.py").write_text("VALUE = 41\n", encoding="utf-8")
    (root / "notes.txt").write_text("café — 日本語\n", encoding="utf-8")
    return root
