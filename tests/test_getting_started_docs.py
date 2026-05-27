from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_getting_started_uses_user_facing_headings() -> None:
    doc = _read("docs/getting-started.md")

    assert "## Prerequisites" in doc
    assert "## 1. Install" in doc
    assert "## 2. Start MongoDB" in doc
    assert "## 3. Set environment variables" in doc
    assert "## 4. Create your workspace and open Studio" in doc
    assert "## 5. Build your first app" in doc

    assert "## Reopen Studio" not in doc
    assert "## Two-Step Mental Model" not in doc


def test_getting_started_explains_how_to_start_again() -> None:
    doc = _read("docs/getting-started.md")
    normalized = " ".join(doc.split())

    assert "pip install mozaiks" in doc
    assert "python -m mozaiks quickstart --dir .\\my-workspace" in doc
    assert "python -m mozaiks studio --dir .\\my-workspace --open" in doc
    assert "You can open Studio without a key but builds will not run." in doc
    assert "`mozaiks` is not recognized" in doc
    assert "Local Setup" in normalized
    assert "local-setup.md" in normalized
    assert "repo checkout" in normalized
    assert "http://localhost:3000" in doc
    assert "python -m venv .venv" not in doc
    assert "python -m pip install mozaiks" not in doc
    assert "pipx" not in doc
    assert ".\\scripts\\run-studio.ps1" not in doc
    assert "Then open a new PowerShell before running `mozaiks`" not in doc


def test_getting_started_clarifies_workspace_terms() -> None:
    doc = _read("docs/getting-started.md")
    normalized = " ".join(doc.split())

    assert "This scaffolds the workspace" in doc
    assert "python -m venv .venv" not in normalized
    assert "MongoDB connection error" in doc
