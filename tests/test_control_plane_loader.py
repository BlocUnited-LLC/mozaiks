from __future__ import annotations

import json
from pathlib import Path

import pytest

from mozaiksai.control_plane import (
    ControlPlanePackLoadError,
    load_control_plane_pack,
    load_selected_control_plane_pack,
)


def test_load_default_factory_control_plane_pack() -> None:
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    pack = load_control_plane_pack(app_root=app_root)

    assert pack.manifest.profile.id == "factory_app"
    assert pack.path == (Path(__file__).resolve().parents[1] / "factory_app" / "control_plane").resolve()
    assert pack.manifest.routing.default_artifact_kind == "app_bundle"
    app_bundle = pack.routing_for_artifact("app_bundle")
    assert app_bundle is not None
    assert app_bundle.routes.core.workflow_sequence == "full_rebuild"
    assert app_bundle.routes.patch.workflow_sequence == "app_revision"
    assert app_bundle.routes.core.route_to is None
    request_intake = pack.checkpoint_by_event("request_submitted")
    assert request_intake is not None
    assert request_intake.prompt_id == "change_classifier_system"
    assert request_intake.tool_ids == [
        "get_revision_context",
        "get_artifact_summary",
    ]
    decision = pack.checkpoint_by_event("decision_requested")
    assert decision is not None
    assert decision.tool_ids == []
    scope = pack.checkpoint_by_event("scope_requested")
    assert scope is not None
    assert scope.prompt_id == "coding_scope_selection_system"
    assert scope.tool_ids == [
        "get_revision_context",
        "get_artifact_summary",
        "get_artifact_workspace_catalog",
    ]
    assert pack.policies.scope.max_selected_paths == 3
    assert pack.policies.scope.auto_apply_max_paths == 1
    assert pack.policies.scope.overflow_behavior == "clarify"
    coding = pack.checkpoint_by_event("coding_requested")
    assert coding is not None
    assert coding.prompt_id == "coding_refinement_system"
    assert coding.tool_ids == [
        "get_revision_context",
        "get_artifact_summary",
        "get_artifact_workspace_scope",
    ]
    assert pack.prompt_by_id("change_classifier_system") is not None
    assert pack.prompt_by_id("coding_refinement_system") is not None
    assert [tool.id for tool in pack.tools.tools] == [
        "get_revision_context",
        "get_artifact_summary",
        "get_artifact_workspace_scope",
        "get_artifact_workspace_catalog",
    ]


def test_load_selected_control_plane_pack_uses_app_override(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    workspace_root = tmp_path
    (app_root / "config").mkdir(parents=True)
    (workspace_root / "control_plane" / "config").mkdir(parents=True)
    (workspace_root / "control_plane" / "prompts").mkdir(parents=True)

    (app_root / "config" / "ai.json").write_text(
        json.dumps(
            {
                "control_plane": {
                    "enabled": True,
                    "classifier": {"enabled": True, "llm_config": {"model": "gpt-4o-mini"}},
                }
            }
        ),
        encoding="utf-8",
    )
    (workspace_root / "control_plane" / "config" / "control_plane.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.control_plane",
                "profile:",
                "  id: custom",
                "  display_name: Custom",
                "  description: Custom pack",
                "harness:",
                "  implementation: example.harness:Harness",
                "checkpoints:",
                "  - id: request_intake",
                "    event: request_submitted",
                "    entrypoint: example.classifier:Classifier",
                "    prompt_id: classify",
                "  - id: route",
                "    event: route_requested",
                "    entrypoint: example.router:Router",
            ]
        ),
        encoding="utf-8",
    )
    (workspace_root / "control_plane" / "prompts" / "classify.yaml").write_text(
        "\n".join(
            [
                "id: classify",
                "content: custom prompt",
            ]
        ),
        encoding="utf-8",
    )
    (workspace_root / "control_plane" / "config" / "tools.yaml").write_text(
        "schema_version: mozaiks.control_plane.tools\ntools: []\n",
        encoding="utf-8",
    )
    (workspace_root / "control_plane" / "config" / "policies.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.control_plane.policies",
                "scope:",
                "  max_selected_paths: 2",
                "  auto_apply_max_paths: 1",
                "  overflow_behavior: clarify",
            ]
        ),
        encoding="utf-8",
    )

    pack = load_selected_control_plane_pack(app_root=app_root)

    assert pack.manifest.profile.id == "custom"
    assert pack.path == (workspace_root / "control_plane").resolve()
    assert pack.policies.scope.max_selected_paths == 2


def test_load_control_plane_pack_validates_prompt_references(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    pack_root = tmp_path / "control_plane"
    (app_root / "config").mkdir(parents=True)
    (pack_root / "config").mkdir(parents=True)
    (pack_root / "prompts").mkdir(parents=True)

    (pack_root / "config" / "control_plane.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.control_plane",
                "profile:",
                "  id: broken",
                "  display_name: Broken",
                "  description: Broken pack",
                "harness:",
                "  implementation: example.harness:Harness",
                "checkpoints:",
                "  - id: request_intake",
                "    event: request_submitted",
                "    entrypoint: example.classifier:Classifier",
                "    prompt_id: missing_prompt",
            ]
        ),
        encoding="utf-8",
    )
    (pack_root / "config" / "tools.yaml").write_text(
        "schema_version: mozaiks.control_plane.tools\ntools: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ControlPlanePackLoadError, match="prompt_id"):
        load_control_plane_pack(app_root=app_root, factory_root=pack_root)


def test_load_control_plane_pack_validates_component_tool_availability(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    pack_root = tmp_path / "control_plane"
    (app_root / "config").mkdir(parents=True)
    (pack_root / "config").mkdir(parents=True)
    (pack_root / "prompts").mkdir(parents=True)

    (pack_root / "config" / "control_plane.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.control_plane",
                "profile:",
                "  id: broken",
                "  display_name: Broken",
                "  description: Broken pack",
                "harness:",
                "  implementation: example.harness:Harness",
                "checkpoints:",
                "  - id: request_intake",
                "    event: request_submitted",
                "    entrypoint: example.classifier:Classifier",
                "    prompt_id: classify",
                "    tool_ids:",
                "      - router_only_tool",
                "  - id: route",
                "    event: route_requested",
                "    entrypoint: example.router:Router",
            ]
        ),
        encoding="utf-8",
    )
    (pack_root / "prompts" / "classify.yaml").write_text(
        "\n".join(
            [
                "id: classify",
                "content: classify",
            ]
        ),
        encoding="utf-8",
    )
    (pack_root / "config" / "tools.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.control_plane.tools",
                "tools:",
                "  - id: router_only_tool",
                "    kind: context_tool",
                "    description: Router tool only",
                "    entrypoint: example.tools:router_only_tool",
                "    available_to:",
                "      - route_requested",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ControlPlanePackLoadError, match="not available to 'request_submitted'"):
        load_control_plane_pack(app_root=app_root, factory_root=pack_root)
