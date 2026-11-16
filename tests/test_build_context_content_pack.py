"""Content pack registration and cross-contract closure, without model calls."""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import release_pack_owned_paths
from factory_app.workflows.AppGenerator.tools.hook_managed_capabilities_context import (
    inject_managed_capabilities_context,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_declared_pack_output_paths,
    resolve_templates_for_pack,
    verify_pack_integrity,
)
from mozaiksai.core.runtime.app.module_loader import ModuleDefinition
from mozaiksai.core.runtime.persistence.intent_loader import load_data_contract
from mozaiksai.core.session.build_context import load_trusted_build_context
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    pack_owned_output_paths,
)

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "factory_app/build_context/content"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def rendered() -> dict[str, str]:
    return {row["filename"]: row["content"] for row in resolve_templates_for_pack(PACK, "content")}


def _registered_context(workflow: str, build_context_root: Path) -> dict:
    definitions = _yaml(ROOT / f"factory_app/workflows/{workflow}/context_variables.yaml")["definitions"]
    policy = build_context_authority_policy(workflow_name=workflow, definitions=definitions)
    return load_trusted_build_context(policy, build_context_root=build_context_root)


@pytest.mark.parametrize("workflow", ["DesignDocs", "AppGenerator"])
def test_content_registers_through_the_workflows_actual_trusted_context(workflow, tmp_path):
    """A declared named context supplies descriptors and contracts, including their real source."""
    root = tmp_path / "build_context"
    copied = root / "content"
    shutil.copytree(PACK, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    projected = _registered_context(workflow, root)
    [descriptor] = projected["capability_packs"]
    [contract] = projected["operator_contracts"]
    assert descriptor["id"] == "content"
    assert descriptor["capability_source"] == "generated_module"
    assert descriptor["status"] == "active"
    assert descriptor["pack_source_path"] == str(copied)
    assert descriptor["version"] == verify_pack_integrity(copied, "content")["version"]
    assert not descriptor.get("required_integrations")
    assert contract["contract_id"] == "content"
    assert contract["source_path"] == str(copied / "contract.yaml")
    assert {key: value for key, value in contract.items() if key != "source_path"} == _yaml(PACK / "contract.yaml")

    if workflow == "AppGenerator":
        agent = SimpleNamespace(name="AppPlanAgent", context_variables=projected, system_message="Build the approved app.")
        agent.update_system_message = lambda message: setattr(agent, "system_message", message)
        inject_managed_capabilities_context(agent, [])
        for capability in descriptor["capabilities"]:
            assert capability["capability_id"] in agent.system_message
        for output in contract["required_outputs"]:
            assert output["path"] in agent.system_message


def test_content_materializes_exactly_its_declared_outputs(rendered):
    outputs = _yaml(PACK / "contract.yaml")["required_outputs"]
    declared = {row["path"]: row["owner"] for row in outputs}
    assert len(declared) == len(outputs), "Every output needs one unambiguous ownership declaration"
    source_paths = {
        path.relative_to(PACK / "templates").as_posix().removesuffix(".j2")
        for path in (PACK / "templates").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
    }
    generated = {path for path, owner in declared.items() if owner == "generator"}
    assert generated == {"modules/content/contracts/fixtures.yaml"}
    assert source_paths == set(declared) - generated == set(rendered)
    assert {path for path, owner in declared.items() if owner == "workspace"} == {
        "modules/content/backend/handler.py",
    }
    assert set(declared.values()) == {"templates", "workspace", "generator"}
    assert all(path.startswith(("modules/content/", "data/")) for path in declared)


def test_content_capabilities_and_handlers_close_against_the_loaded_manifest(rendered):
    module = ModuleDefinition.model_validate(yaml.safe_load(rendered["modules/content/module.yaml"]))
    actions = {action.id: action for action in module.actions}
    capabilities = {item.capability_id: item for item in module.capabilities}
    registered = {item["capability_id"] for item in _yaml(PACK / "context.yaml")["capabilities"]}
    assert registered == set(capabilities)
    assert capabilities
    assert {item.target for item in capabilities.values()} <= set(actions)
    assert all(item.kind == "action" for item in capabilities.values())
    for item in capabilities.values():
        assert item.permissions == actions[item.target].permissions
    methods = {
        node.name
        for path in ("modules/content/backend/handler.py", "modules/content/backend/base_handler.py")
        for node in ast.walk(ast.parse(rendered[path]))
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert set(module.action_method_map.values()) <= methods


def test_content_implementation_is_released_from_model_tasks(rendered):
    context = {"capability_packs": [{"id": "content", "pack_source_path": str(PACK)}]}
    declared = resolve_declared_pack_output_paths(context["capability_packs"], context_variables=context)
    owned = pack_owned_output_paths(context)
    assert {path for path in rendered if path.startswith("modules/content/")} <= owned
    assert owned <= declared
    plan = {
        "build_tasks": [
            {"task_id": "content-copy", "owned_paths": sorted(owned)},
            {"task_id": "fixture", "owned_paths": ["modules/content/contracts/fixtures.yaml"]},
            {"task_id": "app-page", "owned_paths": ["ui/pages/about.yaml"], "depends_on": ["content-copy"]},
        ],
        "generation_order": ["content-copy", "fixture", "app-page"],
    }
    assert release_pack_owned_paths(plan, context)
    assert plan == {
        "build_tasks": [
            {"task_id": "fixture", "owned_paths": ["modules/content/contracts/fixtures.yaml"]},
            {"task_id": "app-page", "owned_paths": ["ui/pages/about.yaml"], "depends_on": []},
        ],
        "generation_order": ["fixture", "app-page"],
    }
    revision = {**context, "build_mode": "revision"}
    assert pack_owned_output_paths(revision) == owned - {"modules/content/backend/handler.py"}


def test_content_declares_public_reads_and_permission_protected_editor_actions(rendered):
    module = ModuleDefinition.model_validate(yaml.safe_load(rendered["modules/content/module.yaml"]))
    assert module.module.user_data_scope is False
    assert {permission.id for permission in module.permissions} == {"content.manage"}
    actions = {action.id: action for action in module.actions}
    assert set(actions) == {"list_entries", "get_entry", "create_entry", "update_entry", "archive_entry"}
    for name, action in actions.items():
        assert action.entitlement_gate is None
        assert action.permissions == ([] if name in {"list_entries", "get_entry"} else ["content.manage"])
        assert action.api_surface == ("public_readonly" if name in {"list_entries", "get_entry"} else "public")
        assert action.input_schema.get("additionalProperties") is False


def test_content_persistence_contribution_is_complete_and_module_owned(rendered, tmp_path):
    data_dir = tmp_path / "app/data"
    data_dir.mkdir(parents=True)
    (data_dir / "contract.json").write_text(rendered["data/contract.json"], encoding="utf-8")
    contract = load_data_contract(data_dir.parent)
    [surface] = contract["surfaces"]
    assert (surface["surface_id"], surface["surface_kind"]) == ("content", "module")
    [collection] = surface["collections"]
    assert (collection["name"], collection["scope"], collection["tenancy"]) == ("entries", "app", "app_wide")
    assert not collection.get("owner_field")
    fields = {field["name"] for field in collection["fields"]}
    assert {"entry_id", "title", "slug", "summary", "body", "status"} <= fields
    assert not fields & {"user_id", "author_id", "owner_id"}
    assert not contract.get("shared_collections")
    migrations = [json.loads(content) for path, content in rendered.items() if path.startswith("data/migrations/")]
    [migration] = migrations
    assert migration["schema_version"] == "mozaiks.data_migration.v1"
    operations = migration["operations"]
    assert {operation["type"] for operation in operations} == {"ensure_collection", "ensure_index"}
    assert {(operation["module_id"], operation["entity_name"]) for operation in operations} == {("content", "entries")}
    assert {operation["index"]["name"] for operation in operations if operation["type"] == "ensure_index"} == {
        index["name"] for index in collection["indexes"]
    }
