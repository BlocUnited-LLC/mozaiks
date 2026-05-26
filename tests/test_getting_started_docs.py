from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_getting_started_uses_user_facing_headings() -> None:
    doc = _read("docs/getting-started.md")

    assert "## Install Mozaiks" in doc
    assert "## Create Your Workspace" in doc
    assert "## Start Mozaiks" in doc
    assert "## Minimum Config For Real Builds" in doc

    assert "## Reopen the Console" not in doc
    assert "## Console, Studio, And CLI" not in doc
    assert "## Two-Step Mental Model" not in doc


def test_getting_started_explains_how_to_start_again() -> None:
    doc = _read("docs/getting-started.md")
    restart_prefix = "After that, if you want to start the same workspace again later"
    restart_section = doc.split(restart_prefix, 1)[1]

    assert "`quickstart` opens the Console during first setup." in doc
    assert "workspace-local `.venv`" in doc
    assert restart_prefix in doc
    assert "run-console.ps1" in doc
    assert "http://localhost:3000/" in doc
    assert ".\\.venv\\Scripts\\Activate.ps1" not in restart_section
    assert ".\\scripts\\run-console.ps1" in doc


def test_getting_started_clarifies_workspace_terms() -> None:
    doc = _read("docs/getting-started.md")

    assert "creates the workspace folder if it does not already exist" in doc
    assert "MongoDB is not required just to open the Console" in doc
    assert "builds will fail until" in doc
