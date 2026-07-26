from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_config_guides_are_first_class_in_mkdocs_nav() -> None:
    nav = _read("mkdocs.yml")

    for page in (
        "guides/configs/index.md",
        "guides/configs/ai-startup.md",
        "guides/configs/subscriptions.md",
        "guides/configs/refinement.md",
        "guides/configs/module-contracts.md",
    ):
        assert page in nav

    assert "guides/extending-ai-functionality/02-ai-runtime-startup.md" not in nav
    assert "guides/extending-ai-functionality/03-refinement-policy.md" not in nav
    assert "guides/extending-ai-functionality/04-refinement-harness.md" not in nav


def test_config_guide_pages_exist_without_duplicate_ai_config_pages() -> None:
    for page in (
        "docs/guides/configs/ai-startup.md",
        "docs/guides/configs/subscriptions.md",
        "docs/guides/configs/refinement.md",
        "docs/guides/configs/module-contracts.md",
    ):
        assert (ROOT / page).exists()

    for page in (
        "docs/guides/extending-ai-functionality/02-ai-runtime-startup.md",
        "docs/guides/extending-ai-functionality/03-refinement-policy.md",
        "docs/guides/extending-ai-functionality/04-refinement-harness.md",
    ):
        assert not (ROOT / page).exists()


def test_user_facing_guides_use_current_config_language() -> None:
    forbidden_terms = (
        "hosted_services.yaml",
        "monetization.yaml",
        "llm.yaml",
        "legacy",
        "removed",
    )

    for path in (ROOT / "docs" / "guides").rglob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term not in text, f"{path.relative_to(ROOT)} contains {term!r}"


def test_config_overview_maps_core_current_files() -> None:
    doc = _read("docs/guides/configs/index.md")

    assert "## Build a Mozaiks App Checklist" in doc
    assert "`app/config/ai.json`" in doc
    assert "`app/config/subscriptions.yaml`" in doc
    assert "`app/config/refinement_policy.yaml`" in doc
    assert "`app/modules/{module_id}/contracts/`" in doc
    assert "`refinement_harness/config/`" in doc
