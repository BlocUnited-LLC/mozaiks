from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows._shared.context_graph import load_context_graph_context as loader_mod
from mozaiksai.control_plane.context_graph import (
    build_context_graph_catalog,
    build_context_graph_scope,
)
from mozaiksai.core.app_context.context_graph import (
    apply_semantic_annotations,
    build_context_graph_from_file_map,
    build_semantic_annotation_request,
    context_graph_parser_status,
)
from mozaiksai.core.app_context.models import GraphEdgeType, GraphNodeType


def test_context_graph_extracts_code_symbols_and_relationships() -> None:
    graph = build_context_graph_from_file_map(
        app_id="app_1",
        build_record_id="av_1",
        build_family="app_bundle",
        file_map={
            "modules/tasks/module.yaml": "id: tasks\nactions:\n  - id: list_tasks\n",
            "modules/tasks/backend/service.py": (
                "from .repo import TaskRepo\n\n"
                "class TaskService:\n"
                "    def list_tasks(self):\n"
                "        return TaskRepo().list_tasks()\n\n"
                "def list_tasks():\n"
                "    return TaskService().list_tasks()\n"
            ),
            "modules/tasks/backend/repo.py": "class TaskRepo:\n    def list_tasks(self):\n        return []\n",
            "ui/pages/tasks.yaml": "id: tasks\ncomponents:\n  - module_id: tasks\n",
        },
    )

    node_types = {node.node_type for node in graph.nodes}
    edge_types = {edge.edge_type for edge in graph.edges}
    app_edges = [edge for edge in graph.edges if edge.source_node_id == "app"]

    assert GraphNodeType.ARTIFACT in node_types
    assert GraphNodeType.MODULE in node_types
    assert GraphNodeType.PAGE in node_types
    assert GraphNodeType.SYMBOL in node_types
    assert GraphEdgeType.DEFINES in edge_types
    assert GraphEdgeType.IMPORTS in edge_types
    assert GraphEdgeType.CALLS in edge_types
    assert any(edge.edge_type == GraphEdgeType.CONTAINS for edge in app_edges)
    assert any(
        node.label == "TaskRepo"
        and node.metadata.get("contract_role") == "module_repo_symbol"
        for node in graph.nodes
    )
    assert any(
        edge.edge_type == GraphEdgeType.CALLS
        and graph_node(graph, edge.target_node_id).label == "TaskRepo"
        for edge in graph.edges
    )


def test_context_graph_maps_module_actions_to_handler_symbols() -> None:
    graph = build_context_graph_from_file_map(
        app_id="app_1",
        build_record_id="av_1",
        build_family="app_bundle",
        file_map={
            "modules/tasks/module.yaml": "id: tasks\nactions:\n  - id: complete_task\n",
            "modules/tasks/backend/handler.py": (
                "class Handler:\n"
                "    def complete_task(self, payload):\n"
                "        return {'ok': True}\n"
            ),
        },
    )

    handler_symbol = next(
        node
        for node in graph.nodes
        if node.node_type == GraphNodeType.SYMBOL
        and node.metadata.get("contract_role") == "module_action_handler"
    )

    assert handler_symbol.label == "complete_task"
    assert handler_symbol.metadata["module_id"] == "tasks"
    assert handler_symbol.metadata["action_id"] == "complete_task"
    assert any(
        edge.edge_type == GraphEdgeType.IMPLEMENTS_CAPABILITY
        and edge.source_node_id == handler_symbol.node_id
        and graph_node(graph, edge.target_node_id).metadata.get("contract_role") == "module_action"
        for edge in graph.edges
    )


def test_semantic_annotation_request_and_apply_are_advisory_metadata() -> None:
    graph = build_context_graph_from_file_map(
        app_id="app_1",
        build_record_id="av_1",
        build_family="app_bundle",
        file_map={
            "modules/tasks/module.yaml": "id: tasks\nactions:\n  - id: complete_task\n",
            "modules/tasks/backend/handler.py": "def complete_task(payload):\n    return payload\n",
        },
    )

    request = build_semantic_annotation_request(graph=graph, max_items=10)
    item = next(item for item in request["items"] if item["contract_role"] == "module_action_handler")
    annotated = apply_semantic_annotations(
        graph=graph,
        annotations=[
            {
                "node_id": item["node_id"],
                "purpose": "Completes a task action request.",
                "domain_concepts": ["task"],
                "side_effects": ["writes_persistence"],
                "invariants": ["Only completes an existing task."],
                "risk_level": "medium",
                "test_targets": ["modules/tasks/backend/test_handler.py"],
                "confidence": 0.8,
            }
        ],
    )

    annotated_node = graph_node(annotated, item["node_id"])
    semantic = annotated_node.metadata["semantic_annotation"]
    assert semantic["source"] == "llm_advisory"
    assert semantic["risk_level"] == "medium"
    assert semantic["domain_concepts"] == ["task"]


def test_context_graph_catalog_and_scope_return_llm_context_packs() -> None:
    file_map = {
        "modules/tasks/backend/service.py": "def list_tasks():\n    return []\n",
        "ui/pages/tasks.yaml": "id: tasks\ncomponents:\n  - module_id: tasks\n",
    }
    graph = build_context_graph_from_file_map(
        app_id="app_1",
        build_record_id="av_1",
        build_family="app_bundle",
        file_map=file_map,
    )

    catalog = build_context_graph_catalog(
        graph=graph,
        raw_user_request="Update the tasks page list behavior",
        file_map=file_map,
    )
    scope = build_context_graph_scope(
        graph=graph,
        selected_paths=["modules/tasks/backend/service.py"],
        file_map=file_map,
        raw_user_request="Update the tasks page list behavior",
    )

    assert catalog["present"] is True
    assert catalog["candidate_files"]
    assert catalog["semantic_annotation_candidates"]
    assert scope["selected_scope"]["modules/tasks/backend/service.py"]["symbols"]
    assert scope["semantic_annotation_request"]["items"]


def test_context_graph_catalog_ranks_product_code_before_tests() -> None:
    file_map = {
        "app/modules/wallet/backend/handler.py": "def checkout(payload):\n    grant_entitlement(payload['user_id'])\n",
        "app/services/payments/entitlements.py": "def grant_entitlement(user_id):\n    return user_id\n",
        "tests/test_wallet_checkout.py": "def test_wallet_checkout_entitlement():\n    assert True\n",
    }
    graph = build_context_graph_from_file_map(
        app_id="app_1",
        build_record_id="av_1",
        build_family="app_bundle",
        file_map=file_map,
    )

    catalog = build_context_graph_catalog(
        graph=graph,
        raw_user_request="Update hosted wallet checkout entitlement behavior",
        file_map=file_map,
    )

    candidate_paths = [item["path"] for item in catalog["candidate_files"]]
    assert candidate_paths[0] == "app/modules/wallet/backend/handler.py"
    assert candidate_paths.index("app/services/payments/entitlements.py") < candidate_paths.index("tests/test_wallet_checkout.py")


def test_context_graph_parser_status_reports_fallbacks() -> None:
    status = context_graph_parser_status()

    assert status["tree_sitter_package_available"] in {True, False}
    assert status["languages"]["python"]["active_parser"] in {"tree_sitter", "python_ast"}
    assert status["languages"]["javascript"]["active_parser"] in {"tree_sitter", "regex"}
    assert status["languages"]["typescript"]["active_parser"] == "javascript_tree_sitter_or_regex"


@pytest.mark.asyncio
async def test_context_graph_startup_loader_populates_compact_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = build_context_graph_from_file_map(
        app_id="app_1",
        build_record_id="av_1",
        build_family="app_bundle",
        file_map={
            "modules/tasks/module.yaml": "id: tasks\nactions:\n  - id: list_tasks\n",
            "modules/tasks/backend/handler.py": "def list_tasks(payload):\n    return []\n",
        },
    )

    async def _current_graph(**_kwargs):
        return SimpleNamespace(graph=graph, warnings=[])

    monkeypatch.setattr(loader_mod, "get_current_app_context_graph", _current_graph)
    context = _Context({"app_id": "app_1", "refinement_request": "Update task listing"})

    result = await loader_mod.load_context_graph_context(context_variables=context)

    assert result["present"] is True
    assert context.data["context_graph_status"] == "loaded"
    assert context.data["context_graph_reason"] is None
    assert context.data["context_graph_warnings"] == []
    assert context.data["context_graph_health"]["source"] == "current_app_context_graph"
    assert context.data["context_graph_pack"] is not context.data["context_graph_catalog"]
    assert context.data["context_graph_pack"]["pack_kind"] == "context_graph_prompt_pack"
    assert context.data["context_graph_pack"]["graph_id"] == graph.graph_id
    assert context.data["context_graph_pack"]["semantic_annotation_targets"]
    assert context.data["context_graph_catalog"]["semantic_annotation_candidates"]


@pytest.mark.asyncio
async def test_context_graph_startup_loader_reports_missing_app_id() -> None:
    context = _Context({"context_graph_pack": {"graph_id": "stale"}, "context_graph_catalog": {"graph_id": "stale"}})

    result = await loader_mod.load_context_graph_context(context_variables=context)

    assert result == {"present": False, "reason": "missing_app_id"}
    assert context.data["context_graph_pack"]["status"] == "unavailable"
    assert context.data["context_graph_pack"]["reason"] == "missing_app_id"
    assert context.data["context_graph_catalog"] is None
    assert context.data["context_graph_status"] == "unavailable"
    assert context.data["context_graph_reason"] == "missing_app_id"
    assert context.data["context_graph_warnings"] == ["missing_app_id"]
    assert context.data["context_graph_health"]["reason"] == "missing_app_id"


def test_app_and_agent_generators_declare_and_load_context_graph_packs() -> None:
    root = Path(__file__).resolve().parents[1]
    for workflow_name in ("AppGenerator", "AgentGenerator"):
        workflow_dir = root / "factory_app" / "workflows" / workflow_name
        tools = yaml.safe_load((workflow_dir / "tools.yaml").read_text(encoding="utf-8")) or {}
        context_variables = yaml.safe_load((workflow_dir / "context_variables.yaml").read_text(encoding="utf-8")) or {}
        lifecycle_tools = tools.get("lifecycle_tools") or []
        definitions = (context_variables.get("definitions") or {})

        assert any(
            item.get("trigger") == "before_chat"
            and item.get("file") == "../_shared/context_graph/load_context_graph_context.py"
            and item.get("function") == "load_context_graph_context"
            for item in lifecycle_tools
        )
        for key in (
            "context_graph_pack",
            "context_graph_catalog",
            "context_graph_status",
            "context_graph_reason",
            "context_graph_warnings",
            "context_graph_health",
        ):
            assert key in definitions


class _Context:
    def __init__(self, data):
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def graph_node(graph, node_id: str):
    return next(node for node in graph.nodes if node.node_id == node_id)


