"""Verified pack collection anchors reach design and its real save boundary.

Storage and owner-review responses are doubles; selection, prompt middleware,
composition, validation and tool outcomes execute their actual implementations.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    verify_pack_integrity,
)
from factory_app.workflows.DesignDocs.tools import save_design_doc
from factory_app.workflows.DesignDocs.tools.select_capability_packs import select_capability_packs
from mozaiksai.core.session.build_context import load_trusted_build_context
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.execution.middleware import MozaiksPromptMiddleware, _resolve_import
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from mozaiksai.core.workflow.generator_support.module_role_grants import (
    validate_reviewed_pack_data,
    verified_pack_authorization,
)
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory
from tests.test_designdocs_selected_pack_context import PACK, WORKFLOW, _prompt, _prompt_runner

persistence = inventory.persistence
COLLECTION_PREFIX = "Pack-owned collection: "


@pytest.fixture
def content_context(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    shutil.copytree(PACK, root / "content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(inventory.ROOT / "factory_app"))
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    context = inventory._context(monetized=False)
    for key, value in load_trusted_build_context(policy, build_context_root=root).items():
        context.set(key, value)
    context.set("pack_selection", None)
    return context


def _collections(prompt):
    return [json.loads(line.strip().removeprefix(COLLECTION_PREFIX)) for line in prompt.splitlines()
            if line.strip().startswith(COLLECTION_PREFIX)]


def _select(context):
    result = select_capability_packs(["content"], context_variables=context)
    assert result["status"] == "selected", result
    return verified_pack_authorization(context, selection=detach(context.get("pack_selection")))


def _bundle(*, custom_collection="reports"):
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [
        {**ownership._collection("reports", custom_collection), "entity": "Report"},
    ]
    bundle["access_policy"] = {
        "roles": [{"role_id": "editor", "label": "Editor", "description": "Maintain the approved editorial app."}],
        "grants": [{"role_id": "editor", "collection": "entries", "actions": [
            "create_entry", "update_entry", "archive_entry",
        ]}],
    }
    return bundle


def _repair_prompt(context):
    entries = yaml.safe_load((WORKFLOW / "middleware.yaml").read_text(encoding="utf-8"))["prompt_middleware"]
    hooks = [_resolve_import("DesignDocs", entry["filename"], entry["function"], WORKFLOW)[0]
             for entry in entries if entry["agent"] == "DesignDocsAgent"]
    assert all(hooks)
    runner = MozaiksPromptMiddleware(
        None, None, middleware_functions=hooks, agent_name="DesignDocsAgent",
        base_system_message="Design the approved app.", context_bridge=context, context_variables=[],
    )
    return asyncio.run(runner._run_prompt_middleware(SimpleNamespace(variables={}, prompt=[])))


def test_selected_content_projects_the_verified_collection_and_real_actions(content_context):
    _select(content_context)
    before = content_context.snapshot()
    prompt = _prompt(_prompt_runner(content_context))
    contract = json.loads((PACK / "templates/data/contract.json").read_text(encoding="utf-8"))
    group = contract["surfaces"][0]
    collection = group["collections"][0]

    assert _collections(prompt) == [{
        "pack_id": "content", "module_id": group["surface_id"], "collection": collection["name"],
        "entity": collection["entity"], "tenancy": collection["tenancy"],
    }]
    for action in ("list_entries", "get_entry", "create_entry", "update_entry", "archive_entry"):
        assert f"content.{action}" in prompt
    assert "content.manage" in prompt
    assert "class ContentHandler" not in prompt
    assert "templates/data/contract.json" not in prompt
    assert content_context.snapshot() == before


def test_missing_selection_cannot_project_catalog_owned_collections(content_context):
    assert content_context.get("capability_packs")
    assert _collections(_prompt(_prompt_runner(content_context))) == []


def test_other_verified_pack_projects_its_own_module_collection_identities(content_context):
    result = select_capability_packs(["social"], context_variables=content_context)
    assert result["status"] == "selected", result
    contract = json.loads((PACK.parent / "social/templates/data/contract.json").read_text(encoding="utf-8"))

    expected = [
        {"pack_id": "social", "module_id": group["surface_id"], "collection": collection["name"],
         "entity": collection["entity"], "tenancy": collection["tenancy"]}
        for group in contract["surfaces"] for collection in group["collections"]
    ]
    assert sorted(_collections(_prompt(_prompt_runner(content_context))), key=lambda row: (row["module_id"], row["collection"])) == sorted(
        expected, key=lambda row: (row["module_id"], row["collection"]),
    )


def test_invalid_refreshed_pack_shared_owner_suppresses_all_collection_hints(
    content_context, tmp_path, caplog,
):
    copied = tmp_path / "build_context/content"
    path = copied / "templates/data/contract.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    invalid = data["surfaces"][0]["collections"].pop()
    invalid.update(
        name="private_invalid_shared_marker", entity="ForeignSharedEntry",
        ownership={"surface_id": "friends", "surface_kind": "module"},
    )
    data["shared_collections"] = [invalid]
    path.write_text(json.dumps(data), encoding="utf-8")
    current = verify_pack_integrity(copied, "content")

    selected = select_capability_packs(["content", "social"], context_variables=content_context)
    assert selected["status"] == "selected", selected
    pins = detach(content_context.get("pack_selection"))
    assert next(row for row in pins if row["pack_id"] == "content")["digest"] == current["digest"]
    authorization = verified_pack_authorization(content_context, selection=pins)
    assert "friends" in authorization.actions and "content" in authorization.actions
    assert any(contract.get("shared_collections") == [invalid] for contract in authorization.data_contracts)
    with pytest.raises(ValueError, match="module-owned collection surfaces only"):
        compile_data_contract({}, pack_data_contracts=list(authorization.data_contracts))

    prompt = _prompt(_prompt_runner(content_context))

    assert _collections(prompt) == []
    assert "Selected pack collection ownership is unavailable" in prompt
    assert "friends.send_friend_request" in prompt
    assert "private_invalid_shared_marker" not in prompt
    assert "private_invalid_shared_marker" not in caplog.text


def test_deselection_removes_collection_anchors_from_the_next_prompt(content_context):
    _select(content_context)
    runner = _prompt_runner(content_context)
    assert _collections(_prompt(runner))
    result = select_capability_packs([], context_variables=content_context)
    assert result["status"] == "selected"
    assert _collections(_prompt(runner)) == []


def test_unregistered_descriptor_cannot_supply_verified_collection_anchors(content_context):
    content_context.set("capability_packs", [{"id": "unverified_editorial", "capability_source": "generated_module"}])
    content_context.set("operator_contracts", [{
        "contract_id": "unverified_editorial", "surface_ownership": [{
            "surface_id": "forged", "collection": "private_drafts", "entity": "ForgedEntry",
        }],
    }])
    result = select_capability_packs(["unverified_editorial"], context_variables=content_context)
    assert result["status"] == "revise"
    assert content_context.get("pack_selection") is None
    prompt = _prompt(_prompt_runner(content_context))
    assert _collections(prompt) == []
    assert "private_drafts" not in prompt


def test_repair_prompt_preserves_verified_owner_after_duplicate_entries_rejection(
    content_context, persistence, monkeypatch,
):
    _select(content_context)
    bundle = _bundle(custom_collection="entries")
    before = deepcopy(bundle)
    review = AsyncMock()
    monkeypatch.setattr(save_design_doc, "use_ui_tool", review)

    result = inventory._save(content_context, bundle)

    assert result["outcome"] == "revise", result
    feedback = content_context.get("design_docs_save_feedback")
    assert result["error"] == feedback
    assert "entries" in feedback
    assert "ambiguous" in feedback
    assert bundle == before
    assert content_context.get("data_contract") is None
    assert content_context.get("access_policy") is None
    review.assert_not_awaited()
    persistence[1].assert_not_called()
    persistence[2].assert_not_awaited()
    prompt = _repair_prompt(content_context)
    assert prompt.startswith("[REPAIR REQUIRED]")
    assert feedback in prompt
    assert _collections(prompt)[0]["module_id"] == "content"


@pytest.mark.parametrize("pack_only", [False, True])
def test_strict_design_saves_pack_data_and_optional_app_collection_after_review(
    content_context, persistence, monkeypatch, pack_only,
):
    authorization = _select(content_context)
    shown = []

    async def approve(_tool, payload, **_kwargs):
        persistence[1].assert_not_called()
        persistence[2].assert_not_awaited()
        shown.append(payload)
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", approve)
    bundle = _bundle()
    if pack_only:
        bundle["data_contract"].update(surfaces=[], shared_collections=[])
        page = bundle["experience_spec"]["pages"][0]
        page.update(name="Entries", route="/entries", intent="Read the app's published editorial entries.")
        bundle["surface_map"]["surfaces"] = [{
            "surface_id": "content", "label": "Editorial content", "surface_kind": "module", "owner": "app",
            "primary_entities": [], "owned_pages": ["Entries"], "source_capability_packs": ["content"],
        }]
    models, _ = load_workflow_structured_outputs("DesignDocs")
    bundle = models["DesignDocsBundle"].model_validate(bundle).model_dump(mode="json")
    before = deepcopy(bundle)
    result = inventory._save(content_context, bundle)

    assert result["outcome"] == "saved", result
    assert shown
    assert "Editor may create_entry, update_entry, archive_entry on entries." in shown[0]["checkpoints"]
    contract = detach(content_context.get("data_contract"))
    assert validate_reviewed_pack_data(contract, authorization) == {("content", "entries")}
    groups = {group["surface_id"]: group for group in contract["surfaces"]}
    if pack_only:
        assert bundle["data_contract"]["surfaces"] == bundle["data_contract"]["shared_collections"] == []
        assert set(groups) == {"content"}
    else:
        assert groups["reports"]["collections"][0]["name"] == "reports"
        assert groups["reports"]["collections"][0]["entity"] == "Report"
    assert groups["content"]["collections"][0]["entity"] == "ContentEntry"
    assert detach(content_context.get("access_policy")) == bundle["access_policy"]
    assert bundle == before
    persistence[2].assert_awaited_once()


def test_selected_content_cannot_gain_an_invented_delete_action(content_context, persistence, monkeypatch):
    _select(content_context)
    bundle = _bundle()
    bundle["access_policy"]["grants"][0]["actions"].append("delete_entry")
    review = AsyncMock()
    monkeypatch.setattr(save_design_doc, "use_ui_tool", review)

    result = inventory._save(content_context, bundle)

    assert result["outcome"] == "revise", result
    assert "delete_entry" in result["error"] and "not an approved action" in result["error"]
    assert "archive_entry" in result["error"]
    assert content_context.get("access_policy") is None
    review.assert_not_awaited()
    persistence[1].assert_not_called()
    persistence[2].assert_not_awaited()
