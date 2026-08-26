"""Contract tests for the RefinementLane enum and coding provider config.

Before this, the eight lane strings existed only as bare literals scattered
across dry_run, promotion_policy, app_context_policy, and validation_runner —
a rename in one file would silently break the others. These tests pin the
canonical set and prove the inference cascade and policy consumers stay inside
it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.control_plane import (
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    RefinementLane,
)
from mozaiksai.control_plane.dry_run import infer_refinement_lane

_CANONICAL_LANES = {
    "ui_patch",
    "experience_design",
    "feature_addition",
    "integration",
    "managed_capability_change",
    "data_model_migration",
    "architecture_replan",
    "conceptual_reframe",
}


def test_refinement_lane_values_are_pinned() -> None:
    assert {lane.value for lane in RefinementLane} == _CANONICAL_LANES


@pytest.mark.parametrize(
    ("request_text", "change_class", "workflow_sequence", "paths", "expected"),
    [
        ("fix the dashboard spacing", "patch", "app_revision", ["app/ui/pages/Dashboard.jsx"], RefinementLane.UI_PATCH),
        ("add a required field to the data model", "patch", "app_revision", ["app/data/contract.json"], RefinementLane.DATA_MODEL_MIGRATION),
        ("switch to the managed billing capability", "patch", "app_revision", [], RefinementLane.MANAGED_CAPABILITY_CHANGE),
        ("wire the slack connector", "patch", "app_revision", [], RefinementLane.INTEGRATION),
        ("rethink the architecture", "core", "full_rebuild", [], RefinementLane.ARCHITECTURE_REPLAN),
        ("make it a marketplace instead", "core", "full_rebuild", [], RefinementLane.CONCEPTUAL_REFRAME),
        ("restyle the whole experience", "design", "app_surface_revision", [], RefinementLane.EXPERIENCE_DESIGN),
        ("add an approvals module action", "feature", "app_revision", [], RefinementLane.FEATURE_ADDITION),
    ],
)
def test_infer_refinement_lane_returns_canonical_values(
    request_text: str,
    change_class: str,
    workflow_sequence: str,
    paths: list[str],
    expected: RefinementLane,
) -> None:
    lane = infer_refinement_lane(
        request=request_text,
        change_class=change_class,
        workflow_sequence=workflow_sequence,
        affected_bundle_paths=paths,
        scope_summary="",
    )
    assert lane == expected.value
    assert lane in _CANONICAL_LANES


def test_policy_consumers_reference_only_canonical_lanes() -> None:
    # Every remaining lane-shaped string in the policy modules must come from
    # the enum; a literal that drifts from the canonical set fails here.
    # "integration" is excluded from the scan: it also appears legitimately as
    # a keyword *term* inside inference tuples (e.g. ("connector",
    # "integration", "adapter")), which are search text, not lane ids.
    import inspect

    from mozaiksai.control_plane import (
        app_context_policy,
        dry_run,
        promotion_policy,
        validation_runner,
    )

    scanned_lanes = _CANONICAL_LANES - {"integration"}
    for module in (dry_run, promotion_policy, app_context_policy, validation_runner):
        source = inspect.getsource(module)
        for lane in scanned_lanes:
            assert f'"{lane}"' not in source, (
                f"{module.__name__} still contains bare lane literal \"{lane}\"; use RefinementLane"
            )


# ---------------------------------------------------------------------------
# Coding provider config
# ---------------------------------------------------------------------------


def test_coding_providers_default_to_acp_disabled() -> None:
    config = ControlPlaneConfig(enabled=True, coding={"enabled": True})

    assert isinstance(config.coding, ControlPlaneCodingCapabilityConfig)
    assert config.coding.providers.acp.enabled is False
    assert config.coding.providers.acp.adapter == "claude_code"
    assert config.coding.providers.acp.budget.max_files == 3
    assert config.coding.providers.acp.budget.max_retries == 1


def test_coding_provider_block_parses_with_budget_overrides() -> None:
    config = ControlPlaneConfig.model_validate(
        {
            "enabled": True,
            "coding": {
                "enabled": True,
                "providers": {
                    "acp": {
                        "enabled": True,
                        "adapter": "codex",
                        "budget": {"max_files": 5, "max_wall_seconds": 300},
                    }
                },
            },
        }
    )

    acp = config.coding.providers.acp
    assert acp.enabled is True
    assert acp.adapter == "codex"
    assert acp.budget.max_files == 5
    assert acp.budget.max_wall_seconds == 300
    assert acp.budget.max_diff_bytes == 262_144  # untouched default


def test_unknown_provider_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ControlPlaneConfig.model_validate(
            {"enabled": True, "coding": {"providers": {"mystery_provider": {"enabled": True}}}}
        )


@pytest.mark.parametrize(
    "budget",
    [
        {"max_files": 0},
        {"max_files": 51},
        {"max_wall_seconds": 5},
        {"max_retries": 10},
        {"max_diff_bytes": 1},
    ],
)
def test_budget_bounds_are_enforced(budget: dict) -> None:
    with pytest.raises(ValidationError):
        ControlPlaneConfig.model_validate(
            {"enabled": True, "coding": {"providers": {"acp": {"budget": budget}}}}
        )


def test_provider_config_rejects_connection_shaped_fields() -> None:
    # Credentials and connection details must never enter refinement policy.
    for forbidden in ({"api_key": "sk-x"}, {"command": ["claude-agent-acp"]}, {"env": {"HOME": "/x"}}):
        with pytest.raises(ValidationError):
            ControlPlaneConfig.model_validate(
                {"enabled": True, "coding": {"providers": {"acp": {"enabled": True, **forbidden}}}}
            )


def test_factory_refinement_policy_parses_with_acp_disabled() -> None:
    from pathlib import Path

    import yaml

    policy_path = Path(__file__).resolve().parents[1] / "factory_app" / "app" / "config" / "refinement_policy.yaml"
    config = ControlPlaneConfig.model_validate(yaml.safe_load(policy_path.read_text(encoding="utf-8")))

    assert config.coding.enabled is True
    assert config.coding.providers.acp.enabled is False
