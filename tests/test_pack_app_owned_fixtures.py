"""App-authored smoke metadata cannot relax a verified pack's backend ownership."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackIntegrityError,
    verified_pack_template_inventory,
    verified_template_module_ids,
    verify_pack_integrity,
)
from factory_app.workflows.AppGenerator.tools.task_integrity import planned_artifact_diagnostics
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    pack_owned_output_paths,
)
from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
    load_module_smoke_fixtures,
)
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "factory_app/build_context/content"
FIXTURE = "modules/content/contracts/fixtures.yaml"
EDITOR = "app_content_editor"


def _fixture():
    return {
        "schema_version": "mozaiks.module_smoke_fixtures.v1",
        "fixtures": [{
            "action": "update_entry", "roles": [EDITOR],
            "setup": [{
                "id": "draft", "module": "content", "action": "create_entry", "roles": [EDITOR],
                "params": [{"field": "title", "source": "literal", "value_json": '"App-authored example"'}],
            }],
            "params": [
                {"field": "entry_id", "source": "response", "step_id": "draft", "response_path": ["entry", "entry_id"]},
                {"field": "status", "source": "literal", "value_json": '"published"'},
            ],
        }],
    }


def _selection():
    identity = verify_pack_integrity(PACK, "content")
    return {"id": "content", "pack_source_path": str(PACK), "capability_source": "generated_module",
            "version": identity["version"], "digest": identity["digest"]}


def _context():
    auth = yaml.safe_load((ROOT / "factory_app/build_context/webapp_builder/templates/config/auth.yaml")
                          .read_text(encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
    auth["authorization"] = {"roles": [{"id": EDITOR, "permissions": ["content.manage"]}]}
    task = {"task_id": "fixtures", "task_type": "module_contract", "surface_kind": "module",
            "capability_pack_id": "content", "initial_agent": "ConfigMiddlewareAgent", "owned_paths": [FIXTURE]}
    return create_context_container(initial=factory_context({
        "app_id": "app-fixture-proof", "capability_packs": [_selection()],
        "data_contract": {"version": "1", "surfaces": [], "shared_collections": []},
        "build_timestamp": "2026-11-13T00:00:00+00:00",
        "generated_files": {
            "app.json": json.dumps({"appId": "app-fixture-proof", "appName": "Fixture proof", "authRequired": True}),
            "config/auth.yaml": yaml.safe_dump(auth),
        },
        "app_build_plan": {
            "capability_packs": [{"capability_pack_id": "content", "capability_source": "generated_module"}],
            "build_tasks": [task],
        },
        "app_task_batch_items": [deepcopy(task)],
        "app_task_batch_results": {"fixtures": {"module_contract": {"module_id": "content", "fixtures_yaml": _fixture()}}},
    }))


@pytest.fixture
def source():
    inventory = verified_pack_template_inventory([_selection()])
    return {row["filename"]: row["content"] for row in inventory[0]["files"]}, inventory


@pytest.mark.asyncio
async def test_actual_assembly_keeps_app_authored_fixture_and_verified_backend(source):
    context = _context()
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    files = {row["filename"]: row["content"] for row in result["code_files"]}
    assert yaml.safe_load(files[FIXTURE]) == _fixture()
    templates, inventory = source
    assert verified_template_module_ids(files, inventory) == {"content"}
    assert all(files[path] == text for path, text in templates.items() if path.startswith("modules/content/"))
    assert FIXTURE not in pack_owned_output_paths(context)
    assert planned_artifact_diagnostics(context, files) == []
    before = deepcopy(files)
    admitted = load_module_smoke_fixtures(files)["content", "update_entry"]
    assert admitted.roles == [EDITOR]
    assert admitted.params[0].response_path == ["entry", "entry_id"]
    assert files == before


def test_pending_fixture_does_not_make_backend_model_work_but_unfinished_task_still_blocks(source):
    files, inventory = source
    assert FIXTURE not in files
    assert verified_template_module_ids(files, inventory) == {"content"}
    # Companion manifests may be absent; that does not attest that an unfinished
    # model task completed. Its accepted execution evidence is a separate gate.
    context = _context()
    context.set("app_task_batch_results", {"fixtures": {}})
    diagnostics = planned_artifact_diagnostics(context, {**files, "app.json": "{}"})
    assert [(row["code"], row["task_id"]) for row in diagnostics] == [("TASK_EVIDENCE_UNAVAILABLE", "fixtures")]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["invented_result", "unknown_role", "missing_grant", "unknown_action", "unknown_response"])
async def test_authored_fixture_is_still_admitted_by_the_existing_execution_gate(defect):
    result = await assemble_app_tasks(context_variables=_context())
    assert result["success"], result
    files = {row["filename"]: row["content"] for row in result["code_files"]}
    fixture = _fixture()
    if defect == "invented_result":
        fixture["fixtures"][0]["expected_status"] = 200
    elif defect == "unknown_role":
        fixture["fixtures"][0]["roles"] = ["invented_editor"]
    elif defect == "missing_grant":
        auth = yaml.safe_load(files["config/auth.yaml"])
        auth["authorization"]["roles"][0]["permissions"] = []
        files["config/auth.yaml"] = yaml.safe_dump(auth)
    elif defect == "unknown_action":
        fixture["fixtures"][0]["action"] = "approve_everything"
    else:
        fixture["fixtures"][0]["params"][0]["response_path"] = ["entry", "undeclared_id"]
    files[FIXTURE] = yaml.safe_dump(fixture)
    with pytest.raises(ValueError):
        load_module_smoke_fixtures(files)


@pytest.mark.parametrize("defect", ["undeclared_fixture", "generator_helper", "generator_policy", "workspace_policy",
                                    "other_contract", "rendered_generator_fixture"])
def test_only_exact_declared_generator_fixture_metadata_is_exempt(source, defect):
    files, inventory = deepcopy(source)
    files[FIXTURE] = yaml.safe_dump(_fixture())
    owners = inventory[0]["owner_map"]
    if defect == "undeclared_fixture":
        owners.pop(FIXTURE)
    elif defect in {"generator_helper", "other_contract"}:
        path = ("modules/content/backend/helper.py" if defect == "generator_helper"
                else "modules/content/contracts/another_fixture.yaml")
        owners[path] = "generator"
        files[path] = "# App-authored content"
    elif defect in {"generator_policy", "workspace_policy"}:
        owners["modules/content/backend/policy.py"] = defect.removesuffix("_policy")
        assert "content" not in verified_template_module_ids(files, inventory)
        return
    else:
        inventory[0]["files"].append({"filename": FIXTURE, "content": files[FIXTURE]})
    with pytest.raises(PackIntegrityError):
        verified_template_module_ids(files, inventory)


@pytest.mark.parametrize("path", ["module.yaml", "backend/base_handler.py", "backend/policy.py", "backend/service.py"])
def test_app_owned_fixture_does_not_authorize_template_tampering(source, path):
    files, inventory = source
    files[FIXTURE] = yaml.safe_dump(_fixture())
    files["modules/content/" + path] += "\n# Candidate altered the implementation\n"
    with pytest.raises(PackIntegrityError, match="template bytes differ"):
        verified_template_module_ids(files, inventory)
