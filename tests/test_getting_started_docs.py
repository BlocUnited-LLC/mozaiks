from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_getting_started_uses_user_facing_headings() -> None:
    doc = _read("docs/getting-started.md")

    assert "## Start The Console Again" in doc
    assert "## Which Tool To Use" in doc
    assert "## Your Workspace vs Your App" in doc

    assert "## Reopen the Console" not in doc
    assert "## Console, Studio, And CLI" not in doc
    assert "## Two-Step Mental Model" not in doc


def test_getting_started_explains_restarting_console_in_plain_language() -> None:
    doc = _read("docs/getting-started.md")

    assert "start the Console again any time you come back later" in doc
    assert "if you close your terminal, restart your computer, or stop" in doc
    assert ".\\scripts\\run-console.ps1" in doc


def test_getting_started_clarifies_workspace_and_studio_terms() -> None:
    doc = _read("docs/getting-started.md")

    assert "It is not a second app you need to learn." in doc
    assert "The CLI is just how you set things up locally." in doc
    assert "It is not the app itself." in doc
    assert "The app itself is created later from inside the Console" in doc