from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from mozaiksai.control_plane.file_contract_context import (
    selected_file_contracts,
    server_page_bindings,
)
from mozaiksai.control_plane.implementations.contract_surface_planner import (
    ContractSurfacePlanner,
)
from mozaiksai.control_plane.implementations.refinement_router import ChangeClass, RefinementRequest
from mozaiksai.control_plane.implementations.surface_regeneration_worker import (
    SurfaceRegenerationWorker,
)
from mozaiksai.core.runtime.app.auth_contract import AppAuthContract


def saved_bundle() -> dict[str, str]:
    return {
        "app.json": '{"appId":"app-1"}',
        "modules/notes/module.yaml": "module:\n  id: notes\nactions:\n  - id: list_notes\n",
        "modules/notes/backend/handler.py": "# linked handler\n",
        "modules/notes/backend/service.py": "# linked service\n",
        "modules/notes/backend/repo.py": "# linked query searches title and body\n",
        "modules/private/backend/repo.py": "UNRELATED_PRIVATE_SENTINEL\n",
        "security/secrets.yaml": "SECRET_SENTINEL\n",
        "ui/pages/notes.yaml": yaml.safe_dump({
            "schema_version": "mozaiks.app_page.v1", "name": "notes", "route": "/notes",
            "title": "Notes", "page_type": "record_list", "layout": "full-width",
            "sections": [{"id": "notes", "primitive": "DataTable", "config": {
                "columns": ["title", "body"], "api_endpoint": "/api/modules/notes/list_notes",
                "pagination_mode": "server", "pagination": True, "page_size": 20,
                "data_key": "items", "total_key": "total", "search": True,
            }}],
        }),
    }


def test_page_contract_projection_uses_runtime_models_and_keeps_auth_shape():
    files = saved_bundle()
    files["config/auth.yaml"] = "provider: keycloak\n"
    contracts = selected_file_contracts(
        file_map=files, selected_paths=["ui/pages/notes.yaml", "config/auth.yaml", "security/secrets.yaml"],
        build_family="app_bundle",
    )
    assert set(contracts) == {"ui/pages/notes.yaml", "config/auth.yaml"}
    assert contracts["config/auth.yaml"] == AppAuthContract.model_json_schema()
    page = contracts["ui/pages/notes.yaml"]
    table = page["primitive_config_schemas"]["DataTable"]
    assert table["additionalProperties"] is False
    assert {"search", "search_keys", "pagination_mode"} <= set(table["properties"])
    assert "search_param" not in table["properties"] and "search_placeholder" not in table["properties"]
    assert selected_file_contracts(file_map=files, selected_paths=list(files), build_family="workflow_bundle") == {}
    many = {f"ui/pages/p{number}.yaml": files["ui/pages/notes.yaml"] for number in range(9)}
    bounded = selected_file_contracts(file_map=many, selected_paths=list(many), build_family="app_bundle")
    assert bounded["ui/pages/p8.yaml"]["source_document_available"] is False
    assert "context limit" in bounded["ui/pages/p8.yaml"]["omitted_reason"]


def test_server_binding_comes_from_the_validated_page_and_rejects_invalid_shapes():
    files = saved_bundle()
    expected = [{"page_path": "ui/pages/notes.yaml", "section_id": "notes",
                 "module_id": "notes", "action_id": "list_notes", "action_declared": True}]
    assert server_page_bindings(file_map=files, selected_paths=list(files)) == expected
    invalid = dict(files, **{"ui/pages/notes.yaml": files["ui/pages/notes.yaml"] + "  bad: true\n"})
    with pytest.raises(ValueError, match="Selected page"):
        server_page_bindings(file_map=invalid, selected_paths=["ui/pages/notes.yaml"])
    files["modules/notes/module.yaml"] = "module:\n  id: notes\nactions: []\n"
    assert server_page_bindings(file_map=files, selected_paths=["ui/pages/notes.yaml"])[0]["action_declared"] is False


def test_surface_worker_receives_canonical_schema_and_only_linked_backend_as_read_only():
    files = saved_bundle()
    page_path = "ui/pages/notes.yaml"
    surface = SimpleNamespace(kind="page_binding", target_id="notes", target_kind="page",
                              affected_paths=[page_path], generation_hint="Filter titles", rationale="User request")
    related = SurfaceRegenerationWorker._build_read_only_files(surface, files)
    assert related["modules/notes/backend/repo.py"] == files["modules/notes/backend/repo.py"]
    assert "modules/private/backend/repo.py" not in related and "security/secrets.yaml" not in related
    request = RefinementRequest(build_family="app_bundle", raw_user_request="Search notes by title")
    routing = SimpleNamespace(change_intent=SimpleNamespace(change_class=ChangeClass.FEATURE))
    prompt = SurfaceRegenerationWorker._build_user_prompt(
        surface=surface, refinement_request=request, routing_decision=routing,
        current_files={page_path: files[page_path]}, read_only_files=related,
    )
    payload, _ = json.JSONDecoder().raw_decode(prompt.split("payload_json:\n", 1)[1])
    assert set(payload["current_files"]) == {page_path}
    assert payload["selected_file_contracts"][page_path]["primitive_config_schemas"]["DataTable"]
    assert "no context tools" in prompt and "never rewrite" in prompt
    with pytest.raises(ValueError, match="outside declared surface scope"):
        SurfaceRegenerationWorker._extract_updated_files(
            response={"updated_files": [{"path": "modules/notes/backend/repo.py", "content": "unsafe"}]},
            allowed_paths={page_path},
        )


def test_planner_advertises_linked_server_owner_without_expanding_scope():
    files = saved_bundle()
    request = RefinementRequest(build_family="app_bundle", app_id="app-1", raw_user_request="Filter notes by title")
    routing = SimpleNamespace(change_intent=SimpleNamespace(change_class=ChangeClass.FEATURE, rationale="search"),
                              workflow_sequence="app_revision")
    prompt = ContractSurfacePlanner._build_user_prompt(
        request=request, routing_decision=routing, context_graph_catalog=None,
        workspace_files=files, allowed_paths=["ui/pages/notes.yaml"],
    )
    payload, _ = json.JSONDecoder().raw_decode(prompt.split("payload_json:\n", 1)[1])
    assert payload["server_page_bindings"] == [{
        "page_path": "ui/pages/notes.yaml", "section_id": "notes", "module_id": "notes",
        "action_id": "list_notes", "action_declared": True,
        "owner_available": True, "owner_within_allowed_paths": False,
    }]
    assert payload["allowed_paths"] == ["ui/pages/notes.yaml"]


