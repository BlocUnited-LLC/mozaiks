from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.import_utils import import_module_directly

_config = import_module_directly("mozaiksai.core.workflow.pack.config")
_paths = import_module_directly("mozaiksai.core.workflow.paths")
_resources = import_module_directly("mozaiksai.resources")
get_global_pack_graph_path = _config.get_global_pack_graph_path
get_workflow_pack_graph_path = _config.get_workflow_pack_graph_path
load_global_pack_graph = _config.load_global_pack_graph
load_workflow_pack_graph = _config.load_workflow_pack_graph
list_workflow_sequences = _config.list_workflow_sequences


def _use_repo_factory_workflows(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflows_root = repo_root / "factory_app" / "workflows"
    monkeypatch.setenv("PLATFORM_PATH", str(repo_root / "__no_active_app__"))
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))
    _config._GLOBAL_CACHE = None
    _config._WORKFLOW_CACHE = {}


def test_global_pack_graph_path_points_to_repo_workflows_pack(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    path = get_global_pack_graph_path()
    assert path.name == "extension_registry.json"
    assert path.parent.name == "extended_orchestration"
    assert path.parent.parent.name == "workflows"


def test_workflow_pack_graph_path_points_to_workflow_pack(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    path = get_workflow_pack_graph_path("AgentGenerator")
    expected_suffix = (
        Path("workflows")
        / "AgentGenerator"
        / "extended_orchestration"
        / "mfj_extension.json"
    )
    assert str(path).endswith(str(expected_suffix))


def test_load_global_pack_graph_uses_canonical_file(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    assert graph.version == 3
    assert {entry.id for entry in graph.workflows} == {
        "ValueEngine",
        "ThemeCapture",
        "DesignDocs",
        "AgentGenerator",
        "AppGenerator",
        "ExistingAppDiscovery",
        "AppReview",
    }


def test_load_global_pack_graph_review_transition_uses_chat_session_contract(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None

    review = next((transition for transition in graph.transitions if transition.id == "app_review"), None)
    assert review is not None
    assert review.transition_type == "chat_session"
    assert review.ui is None
    assert review.route_to == "AppReview"
    assert review.confirm_route is None
    assert review.cancel_route is None


def test_single_root_registry_path_uses_explicit_override_without_factory_merge(monkeypatch, tmp_path) -> None:
    _use_repo_factory_workflows(monkeypatch)

    app_root = tmp_path / "app"
    workflows_root = tmp_path / "workflows"
    registry_dir = workflows_root / "extended_orchestration"
    app_root.mkdir(parents=True)
    registry_dir.mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName":"test-app"}', encoding="utf-8")
    target_registry = registry_dir / "extension_registry.json"
    target_registry.write_text(
        json.dumps(
            {
                "pack_name": "TestOverlay",
                "version": 3,
                "workflows": [
                    {"id": "HostedOnly", "description": "Overlay workflow"},
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))
    _config._GLOBAL_CACHE = None

    path = get_global_pack_graph_path()
    assert path == target_registry.resolve()

    graph = load_global_pack_graph()
    assert graph is not None
    assert {workflow.id for workflow in graph.workflows} == {"HostedOnly"}
    assert graph.journeys == []


def test_load_workflow_pack_graph_uses_canonical_file(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_workflow_pack_graph("AgentGenerator")
    assert graph is not None
    assert graph.version == 3
    assert len(graph.mid_flight_journeys) >= 1
    assert graph.mid_flight_journeys[0].id.startswith("workflow_generation")


def test_declared_global_workflows_match_physical_workflow_folders(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None

    declared = {w.id for w in graph.workflows}
    workflows_root = get_global_pack_graph_path().parent.parent

    physical = {
        p.name
        for p in workflows_root.iterdir()
        if p.is_dir() and p.name not in {"_pack", "__pycache__", "extended_orchestration"} and not p.name.startswith(".")
    }

    assert declared.issubset(physical)


def test_list_workflow_sequences_returns_declared_sequences(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    assert [item.id for item in list_workflow_sequences(graph)] == [
        item.id for item in graph.journeys
    ]


def test_workflows_path_rejects_path_lists(monkeypatch, tmp_path: Path) -> None:
    local_root = tmp_path / "local"
    shared_root = tmp_path / "shared"
    local_root.mkdir()
    shared_root.mkdir()
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", f"{local_root}{os.pathsep}{shared_root}")

    with pytest.raises(ValueError, match="single workflow root"):
        _paths.resolve_workflows_root()


def test_single_root_registry_rejects_invalid_artifact_dependency_graph(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflows_root = tmp_path / "workflows"
    registry_dir = workflows_root / "extended_orchestration"
    registry_dir.mkdir(parents=True)
    (registry_dir / "extension_registry.json").write_text(
        json.dumps(
            {
                "pack_name": "BrokenPack",
                "version": 3,
                "artifact_dependency_graph": {
                    "app_bundle": "design_docs",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))
    _config._GLOBAL_CACHE = None

    with pytest.raises(ValueError, match="artifact_dependency_graph"):
        load_global_pack_graph()


def test_workflows_path_selects_one_root(monkeypatch, tmp_path: Path) -> None:
    local_root = tmp_path / "local"
    local_graph_dir = local_root / "LocalOnly" / "extended_orchestration"
    local_graph_dir.mkdir(parents=True)

    local_graph = local_graph_dir / "mfj_extension.json"
    local_graph.write_text(json.dumps({"version": 3, "mid_flight_journeys": []}), encoding="utf-8")
    (local_root / "LocalOnly" / "orchestrator.yaml").write_text("workflow_name: LocalOnly\n", encoding="utf-8")

    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(local_root))
    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    _config._GLOBAL_CACHE = None
    _config._WORKFLOW_CACHE = {}

    assert _paths.resolve_workflows_root() == local_root.resolve()
    assert get_workflow_pack_graph_path("LocalOnly") == local_graph.resolve()


def test_single_root_override_does_not_inject_factory_fallback(monkeypatch, tmp_path: Path) -> None:
    local_root = tmp_path / "local-workflows"
    local_root.mkdir(parents=True)
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(local_root))

    assert _paths.resolve_workflows_root() == local_root.resolve()


def test_default_roots_use_repo_factory_workflows_when_no_active_app(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)
    monkeypatch.delenv("PLATFORM_PATH", raising=False)

    root = _paths.resolve_workflows_root()

    assert root == (repo_root / "factory_app" / "workflows").resolve()
    assert "__no_active_app__" not in str(root)


def test_active_app_root_uses_workspace_env_when_platform_path_missing(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "external-workspace"
    app_root = workspace_root / "app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName":"External Workspace"}', encoding="utf-8")

    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace_root))

    assert _paths.resolve_active_app_root() == app_root.resolve()


def test_chat_ui_src_root_defaults_to_repo_checkout(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("MOZAIKS_CHAT_UI_PATH", raising=False)

    root = _resources.resolve_chat_ui_src_root()

    assert root == (repo_root / "chat-ui" / "src").resolve()


def test_resource_resolution_prefers_repo_assets_over_stale_package_copy(monkeypatch, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    stale_package_root = tmp_path / "stale-package"
    stale_package_root.mkdir()
    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("MOZAIKS_WEB_SHELL_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_CHAT_UI_PATH", raising=False)
    monkeypatch.setattr(_resources, "_resolve_package_dir", lambda _package_name: stale_package_root)

    assert _resources.resolve_web_shell_root() == (repo_root / "web_shell").resolve()
    assert _resources.resolve_chat_ui_root() == (repo_root / "chat-ui").resolve()


# ── artifact_dependency_graph ──────────────────────────────────────────────────

def test_artifact_dependency_graph_experience_spec_depends_on_design_docs(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    deps = graph.artifact_dependency_graph
    assert "design_docs" in deps["experience_spec"]
    assert "concept" in deps["experience_spec"]


def test_artifact_dependency_graph_is_acyclic(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    deps = graph.artifact_dependency_graph

    # DFS cycle detection (three-colour: unvisited / in-progress / done)
    colors: dict = {}

    def _dfs(node: str) -> bool:
        colors[node] = "gray"
        for upstream in deps.get(node, []):
            if colors.get(upstream) == "gray":
                return False  # back-edge → cycle
            if colors.get(upstream) != "black":
                if not _dfs(upstream):
                    return False
        colors[node] = "black"
        return True

    for node in deps:
        if node not in colors:
            assert _dfs(node), f"Cycle detected in artifact_dependency_graph involving '{node}'"


def test_stale_propagation_design_docs_reaches_experience_spec(monkeypatch) -> None:
    """design_docs is declared as an upstream dependency of experience_spec.
    When design_docs is written, BFS propagation via ArtifactInvalidationService
    will mark experience_spec stale. This test verifies the structural edge exists."""
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    deps = graph.artifact_dependency_graph
    # experience_spec directly lists design_docs as an upstream dependency
    assert "design_docs" in deps.get("experience_spec", [])
    # app_bundle remains downstream of experience_spec
    assert "experience_spec" in deps.get("app_bundle", [])


# ── conceptual_replan sequence ─────────────────────────────────────────────────

def test_conceptual_replan_sequence_exists(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    sequence_ids = [s.id for s in graph.journeys]
    assert "conceptual_replan" in sequence_ids


def test_conceptual_replan_affected_families_are_complete(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    replan = next(s for s in graph.journeys if s.id == "conceptual_replan")
    expected = {"concept", "brand", "design_docs", "experience_spec", "workflow_bundle", "app_bundle"}
    assert set(replan.affected_declarative_families) == expected


def test_conceptual_replan_steps_match_full_rebuild_workflow_order(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    replan = next(s for s in graph.journeys if s.id == "conceptual_replan")
    full_rebuild = next(s for s in graph.journeys if s.id == "full_rebuild")

    def _workflow_ids(seq) -> list:
        return [wf for step in seq.steps for wf in step.workflows]

    assert _workflow_ids(replan) == _workflow_ids(full_rebuild)


def test_full_rebuild_sequence_still_valid(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    full_rebuild = next((s for s in graph.journeys if s.id == "full_rebuild"), None)
    assert full_rebuild is not None
    expected_families = {"concept", "brand", "design_docs", "experience_spec", "workflow_bundle", "app_bundle"}
    assert set(full_rebuild.affected_declarative_families) == expected_families


def test_all_factory_sequence_ids_are_unique(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    ids = [s.id for s in graph.journeys]
    assert len(ids) == len(set(ids)), f"Duplicate sequence ids: {[x for x in ids if ids.count(x) > 1]}"
