from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_readme_uses_plain_tool_framing() -> None:
    readme = _read("README.md")

    assert "### Which Tool To Use" in readme
    assert "The **CLI** is just how you set up the" in readme
    assert "Most users can ignore it and start from the Console." in readme
    assert "developer entrypoint" not in readme
    assert "internal host name" not in readme


def test_local_setup_avoids_internal_workspace_shell_language() -> None:
    doc = _read("docs/local-setup.md")

    assert "## Which Tool To Use Here" in doc
    assert "The CLI gets the local install running." in doc
    assert "workspace shell" not in doc
    assert "management surfaces" not in doc


def test_console_overview_uses_plain_product_terms() -> None:
    doc = _read("docs/guides/console/01-overview.md")

    assert "starts the app build flow" in doc
    assert "list of apps in your workspace" in doc
    assert "page-by-page app builder" in doc
    assert "management surfaces" not in doc
    assert "workflow sequence" not in doc


def test_docs_homepage_uses_plain_summary_language() -> None:
    doc = _read("docs/index.md")
    normalized = " ".join(doc.split())

    assert "It brings together four things" in doc
    assert "Validation checks" in doc
    assert "guides you through planning, generation, review, and revision" in normalized
    assert "generated app workspace contract" not in doc
    assert "Production-readiness gates" not in doc


def test_mkdocs_nav_uses_plain_user_facing_labels() -> None:
    nav = _read("mkdocs.yml")

    assert "- Getting Started: getting-started.md" in nav
    assert "- Use the Console: guides/console/01-overview.md" in nav
    assert "- Local Setup: local-setup.md" in nav

    assert "- Quickstart: getting-started.md" not in nav
    assert "- Use The Console: guides/console/01-overview.md" not in nav
    assert "- Local Dev Setup: local-setup.md" not in nav