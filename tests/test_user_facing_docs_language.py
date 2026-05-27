from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_readme_uses_plain_tool_framing() -> None:
    readme = _read("README.md")

    assert "### Which Tool To Use" in readme
    assert "pip install mozaiks" in readme
    assert "python -m mozaiks quickstart --dir .\\mozaiks-workspace" in readme
    assert "Docker Desktop is not required" in readme
    assert "pipx" not in readme
    assert "python -m venv .venv" not in readme
    assert "python -m pip install mozaiks" not in readme
    assert "The **CLI** is just how you set up the" in readme
    assert "Most users can start from Studio" in readme
    assert "developer entrypoint" not in readme
    assert "internal host name" not in readme


def test_local_setup_avoids_internal_workspace_shell_language() -> None:
    doc = _read("docs/local-setup.md")

    assert "## Choose The Right Setup Path" in doc
    assert "Public package install" in doc
    assert "Docker Desktop is not required" in doc
    assert "Repo contributor setup" in doc
    assert "Standalone workspace setup" in doc
    assert "## Which Tool To Use Here" in doc
    assert "The CLI gets the local install running." in doc
    assert "Do not create a shared `.venv` in the parent folder" in doc
    assert "workspace shell" not in doc
    assert "management surfaces" not in doc


def test_studio_overview_uses_plain_product_terms() -> None:
    doc = _read("docs/guides/studio/01-overview.md")

    assert "Studio opens automatically" in doc
    assert "Click **Create App**" in doc
    assert "Everything happens in the chat" in doc
    assert "[Configuration](../../user-configuration.md)" not in doc
    assert "management surfaces" not in doc
    assert "workflow sequence" not in doc


def test_docs_homepage_uses_plain_summary_language() -> None:
    doc = _read("docs/index.md")
    normalized = " ".join(doc.split())

    assert "Mozaiks is an open-source AI app builder." in doc
    assert "all inside a chat interface" in normalized
    assert "Install Mozaiks, open Studio, and create your first app in minutes." in doc
    assert "generated app workspace contract" not in doc
    assert "Production-readiness gates" not in doc


def test_mkdocs_nav_uses_plain_user_facing_labels() -> None:
    nav = _read("mkdocs.yml")

    assert "- Getting Started: getting-started.md" in nav
    assert "- Use Studio: guides/studio/01-overview.md" in nav
    assert "- Local Setup: local-setup.md" in nav

    assert "- Quickstart: getting-started.md" not in nav
    assert "- Local Dev Setup: local-setup.md" not in nav
