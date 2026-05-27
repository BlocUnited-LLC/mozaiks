from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_add_workflows_guide_starts_with_plain_language() -> None:
    doc = _read("docs/guides/adding-workflows/01-overview.md")

    assert "## Before You Start" in doc
    assert "AI-driven flow behind a task, assistant, or build step" in doc
    assert "## Where It Lives" in doc
    assert "deterministic state machine" not in doc


def test_add_modules_guide_uses_plain_runtime_terms() -> None:
    doc = _read("docs/guides/adding-modules/01-overview.md")

    assert "backend application logic" in doc
    assert "Mozaiks loads modules automatically at startup." in doc
    assert "## Backend File Responsibilities" in doc
    assert "auto-discovers and registers" not in doc


def test_add_pages_guide_uses_plain_route_language() -> None:
    doc = _read("docs/guides/adding-pages/01-overview.md")

    assert "usually declared as YAML schemas instead of raw React" in doc
    assert "A route works only when" in doc
    assert "route is valid only" not in doc


def test_branding_guide_avoids_internal_studio_framing() -> None:
    doc = _read("docs/guides/custom-brand-integration/01-overview.md")

    assert "## What Branding Does Not Control" in doc
    assert "admin ownership and behavior" in doc
    assert "first-party Studio bundle" not in doc
    assert "admin shell ownership" not in doc