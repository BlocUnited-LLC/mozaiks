from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_getting_started_uses_user_facing_headings() -> None:
    doc = _read("docs/getting-started.md")

    assert "## Prerequisites" in doc
    assert "## Install Mozaiks" in doc
    assert "## Create Your Workspace" in doc
    assert "## Start Mozaiks" in doc
    assert "## Minimum Config For Real Builds" in doc

    assert "## Reopen the Console" not in doc
    assert "## Console, Studio, And CLI" not in doc
    assert "## Two-Step Mental Model" not in doc


def test_getting_started_explains_how_to_start_again() -> None:
    doc = _read("docs/getting-started.md")
    normalized = " ".join(doc.split())

    assert "`quickstart` opens the Console during first setup." in doc
    assert "python -m pipx install mozaiks" in doc
    assert "mozaiks console --dir .\\mozaiks-workspace --open" in doc
    assert "Local Setup" in normalized
    assert "local-setup.md" in normalized
    assert "source checkout or contributor setup" in normalized
    assert "http://localhost:3000/" in doc
    assert "python -m venv .venv" not in doc
    assert "python -m pip install mozaiks" not in doc
    assert ".\\scripts\\run-console.ps1" not in doc


def test_getting_started_clarifies_workspace_terms() -> None:
    doc = _read("docs/getting-started.md")
    normalized = " ".join(doc.split())

    assert "creates the workspace folder if it does not already exist" in doc
    assert "You do not need to create a `.venv` in the parent folder" in normalized
    assert "You need MongoDB to open the Console" in doc
    assert "Mozaiks does not start MongoDB for you" in doc
