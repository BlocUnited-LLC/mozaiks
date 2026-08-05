from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from mozaiksai.control_plane import (
    DEFAULT_REFINEMENT_HARNESS_EXTENDS,
    ControlPlanePackLoadError,
    load_refinement_harness,
    load_selected_refinement_harness,
)


def test_load_default_factory_refinement_harness() -> None:
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    pack = load_refinement_harness(app_root=app_root)

    assert pack.path == (Path(__file__).resolve().parents[1] / "factory_app" / "refinement_harness").resolve()
    assert pack.manifest.routing.default_build_family == "app_bundle"
    app_bundle = pack.routing_for_artifact("app_bundle")
    assert app_bundle is not None
    assert app_bundle.routes.core.workflow_sequence == "full_rebuild"
    assert app_bundle.routes.patch.workflow_sequence == "app_revision"
    theme_config = pack.routing_for_artifact("theme_config")
    assert theme_config is not None
    assert theme_config.routes.patch.workflow_sequence == "theme_patch"
    assert theme_config.routes.design.workflow_sequence == "theme_revision"
    request_intake = pack.checkpoint_by_event("request_submitted")
    assert request_intake is not None
    assert request_intake.mode == "ag2_structured_agent"
    assert request_intake.prompt_id == "change_classifier_system"
    assert request_intake.output_contract == "ChangeClassifierResult"
    assert request_intake.tool_ids == [
        "get_revision_context",
        "get_artifact_summary",
        "get_app_intelligence_context",
        "get_stale_artifact_families",
    ]
    route = pack.checkpoint_by_event("route_requested")
    assert route is not None
    assert route.mode == "deterministic_handler"
    assert route.tool_ids == []
    decision = pack.checkpoint_by_event("decision_requested")
    assert decision is not None
    assert decision.mode == "deterministic_handler"
    assert decision.tool_ids == []
    scope = pack.checkpoint_by_event("scope_requested")
    assert scope is not None
    assert scope.mode == "ag2_structured_agent"
    assert scope.prompt_id == "coding_scope_selection_system"
    assert scope.output_contract == "ScopeProposal"
    assert scope.tool_ids == [
        "get_revision_context",
        "get_artifact_summary",
        "get_app_intelligence_context",
        "get_artifact_workspace_catalog",
        "get_context_graph_catalog",
        "search_app_source_context",
    ]
    assert pack.policies.scope.max_selected_paths == 3
    assert pack.policies.scope.auto_apply_max_paths == 1
    assert pack.policies.scope.overflow_behavior == "clarify"
    coding = pack.checkpoint_by_event("coding_requested")
    assert coding is not None
    assert coding.mode == "ag2_structured_agent"
    assert coding.prompt_id == "coding_refinement_system"
    assert coding.output_contract == "CodingWorkerPlan"
    assert coding.tool_ids == [
        "get_revision_context",
        "get_artifact_summary",
        "get_app_intelligence_context",
        "get_artifact_workspace_scope",
        "get_context_graph_scope",
        "search_app_source_context",
        "read_app_source_file",
        "get_related_app_source_files",
    ]
    assert pack.prompt_by_id("change_classifier_system") is not None
    assert pack.prompt_by_id("coding_refinement_system") is not None
    assert [tool.id for tool in pack.tools.tools] == [
        "get_revision_context",
        "get_artifact_summary",
        "get_artifact_workspace_scope",
        "get_artifact_workspace_catalog",
        "get_context_graph_catalog",
        "get_app_intelligence_context",
        "run_app_source_validation",
        "get_context_graph_scope",
        "search_app_source_context",
        "read_app_source_file",
        "get_related_app_source_files",
        "get_stale_artifact_families",
        "get_contract_surface_context",
        "get_carry_forward_candidates",
        "read_artifact_file",
    ]
    contract_surface_checkpoint = pack.checkpoint_by_event("contract_surface_requested")
    assert contract_surface_checkpoint is not None
    assert contract_surface_checkpoint.mode == "ag2_structured_agent"
    assert contract_surface_checkpoint.prompt_id == "contract_surface_selection_system"
    assert contract_surface_checkpoint.output_contract == "ContractSurfaceClassification"
    assert contract_surface_checkpoint.tool_ids == [
        "get_contract_surface_context",
        "get_app_intelligence_context",
        "search_app_source_context",
    ]
    assert pack.prompt_by_id("contract_surface_selection_system") is not None


def test_load_selected_refinement_harness_uses_app_override(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    workspace_root = tmp_path
    (app_root / "config").mkdir(parents=True)
    (workspace_root / "refinement_harness" / "config").mkdir(parents=True)
    (workspace_root / "refinement_harness" / "prompts").mkdir(parents=True)

    (app_root / "config" / "ai.json").write_text(
        json.dumps(
            {
                "control_plane": {
                    "enabled": True,
                    "classifier": {"enabled": True, "llm_config": {"model": "gpt-5-nano"}},
                }
            }
        ),
        encoding="utf-8",
    )
    (workspace_root / "refinement_harness" / "config" / "harness.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.refinement_harness.v1",
                "checkpoints:",
                "  - event: request_submitted",
                "    prompt_id: classify",
            ]
        ),
        encoding="utf-8",
    )
    (workspace_root / "refinement_harness" / "prompts" / "classify.yaml").write_text(
        "\n".join(
            [
                "id: classify",
                "content: custom prompt",
            ]
        ),
        encoding="utf-8",
    )
    (workspace_root / "refinement_harness" / "config" / "tools.yaml").write_text(
        "schema_version: mozaiks.refinement_harness.tools.v1\ntools: []\n",
        encoding="utf-8",
    )
    (workspace_root / "refinement_harness" / "config" / "policies.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.refinement_harness.policies.v1",
                "scope:",
                "  max_selected_paths: 2",
                "  auto_apply_max_paths: 1",
                "  overflow_behavior: clarify",
            ]
        ),
        encoding="utf-8",
    )

    pack = load_selected_refinement_harness(app_root=app_root)

    assert pack.path == (workspace_root / "refinement_harness").resolve()
    assert pack.policies.scope.max_selected_paths == 2


def test_load_selected_refinement_harness_extends_default_with_overlay(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    workspace_root = tmp_path
    overlay_root = workspace_root / "refinement_harness"
    (app_root / "config").mkdir(parents=True)
    (overlay_root / "config").mkdir(parents=True)
    (overlay_root / "prompts").mkdir(parents=True)

    (app_root / "config" / "ai.json").write_text(
        json.dumps(
            {
                "control_plane": {
                    "enabled": True,
                    "classifier": {"enabled": True, "llm_config": {"model": "gpt-5-nano"}},
                }
            }
        ),
        encoding="utf-8",
    )
    (overlay_root / "config" / "harness.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mozaiks.refinement_harness.v1",
                "extends": DEFAULT_REFINEMENT_HARNESS_EXTENDS,
                "overrides": {
                    "routing": {
                        "artifacts": [
                            {
                                "build_family": "app_bundle",
                                "label": "App Zero app bundle",
                            }
                        ]
                    },
                    "checkpoints": [
                        {
                            "event": "route_requested",
                            "tool_ids": ["get_carry_forward_candidates"],
                        },
                        {
                            "event": "coding_requested",
                            "append_tool_ids": ["read_artifact_file", "app_local_context"],
                        },
                    ],
                    "prompts": {
                        "coding_refinement_system": "refinement_harness/prompts/coding_refinement_system.yaml"
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (overlay_root / "prompts" / "coding_refinement_system.yaml").write_text(
        "\n".join(
            [
                "id: coding_refinement_system",
                "content: App-local coding prompt override.",
            ]
        ),
        encoding="utf-8",
    )
    (overlay_root / "config" / "tools.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mozaiks.refinement_harness.tools.v1",
                "tools": [
                    {
                        "id": "app_local_context",
                        "kind": "context_tool",
                        "description": "App-local context tool.",
                        "entrypoint": "app.refinement_tools:app_local_context",
                        "available_to": ["coding_requested"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (overlay_root / "config" / "policies.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mozaiks.refinement_harness.policies.v1",
                "scope": {
                    "max_selected_paths": 5,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    pack = load_selected_refinement_harness(app_root=app_root)

    assert pack.path == overlay_root.resolve()
    app_bundle = pack.routing_for_artifact("app_bundle")
    assert app_bundle is not None
    assert app_bundle.label == "App Zero app bundle"
    assert app_bundle.routes.patch.workflow_sequence == "app_revision"
    assert pack.routing_for_artifact("theme_config") is not None
    route = pack.checkpoint_by_event("route_requested")
    assert route is not None
    assert route.tool_ids == ["get_carry_forward_candidates"]
    coding = pack.checkpoint_by_event("coding_requested")
    assert coding is not None
    assert coding.tool_ids[-2:] == ["read_artifact_file", "app_local_context"]
    assert pack.tool_by_id("app_local_context") is not None
    assert pack.prompt_by_id("coding_refinement_system").content == "App-local coding prompt override."
    assert pack.policies.scope.max_selected_paths == 5
    assert pack.policies.scope.auto_apply_max_paths == 1


def test_extended_refinement_harness_rejects_whole_pack_fields(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    overlay_root = tmp_path / "refinement_harness"
    (app_root / "config").mkdir(parents=True)
    (overlay_root / "config").mkdir(parents=True)
    (overlay_root / "config" / "harness.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mozaiks.refinement_harness.v1",
                "extends": DEFAULT_REFINEMENT_HARNESS_EXTENDS,
                "routing": {"default_artifact_kind": "app_bundle"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ControlPlanePackLoadError, match="may only declare"):
        load_selected_refinement_harness(app_root=app_root)


def test_load_refinement_harness_validates_prompt_references(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    pack_root = tmp_path / "refinement_harness"
    (app_root / "config").mkdir(parents=True)
    (pack_root / "config").mkdir(parents=True)
    (pack_root / "prompts").mkdir(parents=True)

    (pack_root / "config" / "harness.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.refinement_harness.v1",
                "checkpoints:",
                "  - event: request_submitted",
                "    prompt_id: missing_prompt",
            ]
        ),
        encoding="utf-8",
    )
    (pack_root / "config" / "tools.yaml").write_text(
        "schema_version: mozaiks.refinement_harness.tools.v1\ntools: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ControlPlanePackLoadError, match="prompt_id"):
        load_refinement_harness(app_root=app_root, factory_root=pack_root)


def test_load_refinement_harness_validates_component_tool_availability(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    pack_root = tmp_path / "refinement_harness"
    (app_root / "config").mkdir(parents=True)
    (pack_root / "config").mkdir(parents=True)
    (pack_root / "prompts").mkdir(parents=True)

    (pack_root / "config" / "harness.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.refinement_harness.v1",
                "checkpoints:",
                "  - event: request_submitted",
                "    prompt_id: classify",
                "    tool_ids:",
                "      - router_only_tool",
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
                "schema_version: mozaiks.refinement_harness.tools.v1",
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
        load_refinement_harness(app_root=app_root, factory_root=pack_root)


def _write_checkpoint_contract_pack(
    pack_root: Path,
    *,
    checkpoint_lines: list[str],
    prompt_id: str | None = "classify",
) -> None:
    (pack_root / "config").mkdir(parents=True)
    (pack_root / "prompts").mkdir(parents=True)
    (pack_root / "config" / "harness.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.refinement_harness.v1",
                "checkpoints:",
                *checkpoint_lines,
            ]
        ),
        encoding="utf-8",
    )
    if prompt_id:
        (pack_root / "prompts" / f"{prompt_id}.yaml").write_text(
            f"id: {prompt_id}\ncontent: checkpoint prompt\n",
            encoding="utf-8",
        )
    (pack_root / "config" / "tools.yaml").write_text(
        "schema_version: mozaiks.refinement_harness.tools.v1\ntools: []\n",
        encoding="utf-8",
    )


def test_load_refinement_harness_rejects_ag2_checkpoint_without_prompt(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    pack_root = tmp_path / "refinement_harness"
    _write_checkpoint_contract_pack(
        pack_root,
        checkpoint_lines=[
            "  - event: request_submitted",
        ],
        prompt_id=None,
    )

    with pytest.raises(ControlPlanePackLoadError, match="prompt_id"):
        load_refinement_harness(app_root=app_root, factory_root=pack_root)


def test_load_refinement_harness_rejects_deterministic_checkpoint_prompt(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    pack_root = tmp_path / "refinement_harness"
    _write_checkpoint_contract_pack(
        pack_root,
        checkpoint_lines=[
            "  - event: route_requested",
            "    prompt_id: classify",
        ],
    )

    with pytest.raises(ControlPlanePackLoadError, match="must not declare prompt_id"):
        load_refinement_harness(app_root=app_root, factory_root=pack_root)


def test_load_refinement_harness_rejects_runtime_wiring_fields(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    pack_root = tmp_path / "refinement_harness"
    _write_checkpoint_contract_pack(
        pack_root,
        checkpoint_lines=[
            "  - event: scope_requested",
            "    prompt_id: classify",
            "    entrypoint: example.scope:Scope",
        ],
    )

    with pytest.raises(ControlPlanePackLoadError, match="entrypoint"):
        load_refinement_harness(app_root=app_root, factory_root=pack_root)


def test_load_refinement_harness_rejects_top_level_harness(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    pack_root = tmp_path / "refinement_harness"
    (pack_root / "config").mkdir(parents=True)
    (pack_root / "prompts").mkdir(parents=True)
    (pack_root / "config" / "harness.yaml").write_text(
        "\n".join(
            [
                "schema_version: mozaiks.refinement_harness.v1",
                "harness:",
                "  implementation: example.harness:Harness",
                "checkpoints: []",
            ]
        ),
        encoding="utf-8",
    )
    (pack_root / "config" / "tools.yaml").write_text(
        "schema_version: mozaiks.refinement_harness.tools.v1\ntools: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ControlPlanePackLoadError, match="harness"):
        load_refinement_harness(app_root=app_root, factory_root=pack_root)


def _write_minimal_refinement_harness(pack_root: Path, *, route_payload: dict) -> None:
    (pack_root / "config").mkdir(parents=True)
    (pack_root / "prompts").mkdir(parents=True)
    manifest = {
        "schema_version": "mozaiks.refinement_harness.v1",
        "routing": {
            "default_artifact_kind": "app_bundle",
            "artifacts": [
                {
                    "artifact_kind": "app_bundle",
                    "routes": {
                        "patch": route_payload,
                        "design": route_payload,
                        "feature": route_payload,
                        "core": route_payload,
                    },
                }
            ],
        },
        "checkpoints": [],
    }
    (pack_root / "config" / "harness.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    (pack_root / "config" / "tools.yaml").write_text(
        "schema_version: mozaiks.refinement_harness.tools.v1\ntools: []\n",
        encoding="utf-8",
    )


def _write_registry(root: Path, *, sequences: list[dict]) -> None:
    registry_dir = root / "extended_orchestration"
    registry_dir.mkdir(parents=True)
    (registry_dir / "extension_registry.json").write_text(
        json.dumps(
            {
                "version": 3,
                "workflows": [{"id": "AppGenerator"}],
                "entrypoints": [],
                "workflow_sequences": sequences,
                "transitions": [],
            }
        ),
        encoding="utf-8",
    )


def test_load_refinement_harness_rejects_unknown_workflow_sequence(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    pack_root = tmp_path / "refinement_harness"
    workflows_root = tmp_path / "workflows"
    _write_registry(
        workflows_root,
        sequences=[
            {
                "id": "known_sequence",
                "affected_declarative_families": ["app_bundle"],
                "steps": [{"workflows": ["AppGenerator"]}],
            }
        ],
    )
    _write_minimal_refinement_harness(pack_root, route_payload={"workflow_sequence": "missing_sequence"})
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))

    with pytest.raises(ControlPlanePackLoadError, match="unknown workflow_sequence 'missing_sequence'"):
        load_refinement_harness(app_root=app_root, factory_root=pack_root)


def test_load_refinement_harness_requires_sequence_impact_metadata(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    pack_root = tmp_path / "refinement_harness"
    workflows_root = tmp_path / "workflows"
    _write_registry(
        workflows_root,
        sequences=[
            {
                "id": "app_revision",
                "steps": [{"workflows": ["AppGenerator"]}],
            }
        ],
    )
    _write_minimal_refinement_harness(pack_root, route_payload={"workflow_sequence": "app_revision"})
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))

    with pytest.raises(ControlPlanePackLoadError, match="must declare affected_declarative_families"):
        load_refinement_harness(app_root=app_root, factory_root=pack_root)


def test_control_plane_routes_reject_duplicated_impact_fields(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    pack_root = tmp_path / "refinement_harness"
    _write_minimal_refinement_harness(
        pack_root,
        route_payload={
            "workflow_sequence": "app_revision",
            "affected_workflows": ["AppGenerator"],
        },
    )

    with pytest.raises(ControlPlanePackLoadError, match="affected_workflows"):
        load_refinement_harness(app_root=app_root, factory_root=pack_root)
