from __future__ import annotations

from pathlib import Path


def test_generated_ui_acceptance_has_no_private_retry_state() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "generated_ui_acceptance.py").read_text(encoding="utf-8")

    forbidden = (
        "review_ui_acceptance_findings",
        "prior_revision_count",
        "revision_count",
        "revision_request",
        '"needs_revision"',
    )
    for legacy_name in forbidden:
        assert legacy_name not in source


def test_generated_ui_acceptance_uses_canonical_controller() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "generated_ui_acceptance.py").read_text(encoding="utf-8")

    assert "ValidationRegistry" in source
    assert "AcceptanceController" in source
    assert "repair_decision" not in source
