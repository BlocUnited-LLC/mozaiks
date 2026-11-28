"""Selected subscription edits retain actual catalog and immutable reviewed inputs."""

from __future__ import annotations

import hashlib
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.subscription_revision import (
    capture_subscription_revision_baseline,
    subscription_revision_baseline,
    subscription_revision_design_context,
)
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.generator_support.design_access_policy import access_policy_sha256

ROOT = Path(__file__).resolve().parents[1]


def summary_record(family, payload, binding):
    raw = _summary_bytes(payload)
    workflow = {"design_docs": "DesignDocs", "subscription_contract": "SubscriptionContractDesigner"}[family]
    return BuildRecord(
        id=family + "-selected", app_id="notes", build_family=family, build_key=family,
        lineage_root_id=family + "-selected", version_number=1, lifecycle_status="current",
        source_chat_id=family + "-chat", source_workflow=workflow,
        files_manifest=[{"path": family + ".json", "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}],
        commit_metadata={"author_user_id": "owner", "source_chat_id": family + "-chat", "source_workflow": workflow,
                         "metadata": {**binding, "summary_payload": payload}},
    )


@pytest.fixture
def selected(tmp_path, monkeypatch):
    from mozaiksai.core.validation import compilation_inputs

    binding = RunBuildBinding(target_app_id="notes", build_registry_id="notes-reg", build_id="original", phase="genesis").model_dump()
    policy = {"roles": [], "grants": []}
    experience = {"pages": []}
    design_payload = {
        "surface_map": {"surfaces": []}, "data_contract": {"version": "1", "surfaces": []},
        "experience_spec": experience, "backend_markdown": "SELECTED BACKEND", "frontend_markdown": "SELECTED UI",
        "database_markdown": "SELECTED DATA", "access_policy": policy,
        "access_policy_review": {"status": "not_required", "target_app_id": "notes", "build_id": "original",
                                 "policy_sha256": access_policy_sha256(policy, experience)},
    }
    design = summary_record("design_docs", design_payload, binding)
    catalog = {"schema_version": "mozaiks.subscriptions.v1", "label": "Notes plans", "default_plan_id": "free", "plans": [
        {"plan_id": "free", "label": "Free", "capabilities": []},
        {"plan_id": "pro", "label": "Pro", "price": {"amount_cents": 1700, "currency": "usd", "interval": "month"}},
    ]}
    contract_payload = {"contract_required": True, "subscription_config_file": catalog,
                        "review_status": "confirmed", "user_confirmed": True}
    contract = summary_record("subscription_contract", contract_payload, binding)
    contract.canonical_inputs_version = {"design_docs": design.id}
    archive = tmp_path / "notes.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("notes/app.json", '{"appId":"notes"}')
        output.writestr("notes/config/subscriptions.yaml", yaml.safe_dump(catalog))
        output.writestr("notes/brand/theme_config.json", '{"theme":{"primary":"selected-cyan"}}')
        output.writestr("notes/ui/selected.txt", "Selected app bytes")
    raw = archive.read_bytes()
    bundle = BuildRecord(
        id="bundle-selected", app_id="notes", build_family="app_bundle", build_key="app_bundle", version_number=1,
        source_workflow="AppGenerator", source_chat_id="app-chat",
        lineage_root_id="bundle-selected", lifecycle_status="draft", validation_status="passed", app_validation_status="passed",
        canonical_inputs_version={"design_docs": design.id, "subscription_contract": contract.id},
        files_manifest=[{"path": "notes/notes.zip", "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "content_type": "application/zip"}],
        commit_metadata={"author_user_id": "owner", "metadata": {**binding, "bundle_name": "notes", "artifact_path": str(archive)}},
    )
    records = {record.id: record for record in (design, contract, bundle)}
    records["contract-newer"] = contract.model_copy(update={"id": "contract-newer", "version_number": 99})
    store = SimpleNamespace(get_build_record=AsyncMock(side_effect=lambda *, app_id, build_record_id: records.get(build_record_id) if app_id == "notes" else None))
    approved_context = {"run_build_binding": binding, "design_docs_artifact": design.model_dump(mode="json", by_alias=True),
                        "subscription_contract": contract_payload, "data_contract": design_payload["data_contract"],
                        "design_surface_map": design_payload["surface_map"]}
    real_resolver = compilation_inputs.resolve_app_compilation_inputs
    resolver = AsyncMock(return_value=SimpleNamespace(context_for=lambda app_id: deepcopy(approved_context)))
    monkeypatch.setattr(compilation_inputs, "resolve_app_compilation_inputs", resolver)
    return SimpleNamespace(bundle=bundle, design=design, contract=contract, catalog=catalog, records=records,
                           store=store, resolver=resolver, real_resolver=real_resolver, approved_context=approved_context, archive=archive)


async def capture(selected, **overrides):
    return await capture_subscription_revision_baseline(**{
        "app_record": selected.bundle, "owner_user_id": "owner", "build_registry_id": "notes-reg",
        "artifact_store": selected.store, **overrides,
    })


def context_for(payload):
    current = {**payload["app_binding"], "build_id": "new-revision", "phase": "refinement"}
    return ContextVariablesBridge({
        "run_build_binding": current, "user_id": "owner", "workflow_sequence": "subscription_patch",
        "app_id": "factory", "build_mode": "revision", "artifact_kind": "subscription_contract",
        "artifact_version_id": payload["subscription_record"]["_id"],
        "subscription_revision_baseline": payload, "refinement_request": "Rename Pro to Team.",
        "backend_design_document": "GLOBAL NEWER BACKEND", "design_surface_map": {"surfaces": [{"surface_id": "wrong"}]},
        "experience_spec": {"pages": []}, "data_contract": {"version": "1", "surfaces": []},
    })


@pytest.mark.asyncio
async def test_capture_uses_exact_catalog_and_inputs_without_latest(selected):
    payload = await capture(selected)
    assert payload["catalog"] == selected.catalog
    assert payload["subscription_record"]["_id"] == selected.contract.id
    assert payload["context"]["backend_design_document"] == "SELECTED BACKEND"
    assert payload["context"]["design_docs_build_artifact"] is None
    assert payload["input_binding"]["build_id"] == "original"
    assert all(call.kwargs["build_record_id"] != "contract-newer" for call in selected.store.get_build_record.await_args_list)
    selected.resolver.assert_awaited_once_with(app_id="notes", build_record_id="bundle-selected", owner_user_id="owner",
                                            build_registry_id="notes-reg", artifact_store=selected.store)


@pytest.mark.asyncio
async def test_capture_roundtrips_real_approved_compilation(selected, monkeypatch):
    from mozaiksai.core.validation import compilation_inputs

    packs = [{"id": "outcome_feedback", "capability_source": "generated_module"}]
    context = {**selected.approved_context, "app_plan_outcome": "ready",
               "app_build_plan": {"capability_packs": packs, "build_tasks": []},
               "subscription_contract_artifact_version_id": selected.contract.id}
    selected.bundle.commit_metadata.metadata["capability_packs"] = packs
    selected.bundle.commit_metadata.metadata["compilation_inputs"] = await compilation_inputs.capture_app_compilation_inputs(
        context, owner_user_id="owner", archive_sha256=selected.bundle.files_manifest[0].sha256,
        artifact_store=selected.store,
    )
    monkeypatch.setattr(compilation_inputs, "resolve_app_compilation_inputs", selected.real_resolver)
    payload = await capture(selected)
    assert payload["catalog"] == selected.catalog
    assert payload["context"]["design_docs_artifact"]["_id"] == selected.design.id


@pytest.mark.asyncio
async def test_summary_sources_need_no_invented_registry_metadata(selected):
    # persist_summary_artifact records author/source/manifest, not run bindings.
    selected.contract.commit_metadata.metadata = {
        "summary_payload": selected.contract.commit_metadata.metadata["summary_payload"], "summary_format": "json",
    }
    payload = await capture(selected)
    assert payload["subscription_record"]["_id"] == selected.contract.id


@pytest.mark.asyncio
async def test_save_stages_exact_selected_parent_and_design_without_latest_inputs(selected, monkeypatch):
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    context = context_for(await capture(selected))
    normalized = deepcopy(selected.contract.commit_metadata.metadata["summary_payload"])
    normalized["subscription_config_file"]["plans"][1]["label"] = "Team"
    context.set("structured_output", normalized)
    context.set("chat_id", "revision-chat")
    monkeypatch.setattr(module, "normalize_subscription_contract", lambda output, design: deepcopy(output))
    monkeypatch.setattr(module, "validate_module_contract_updates", lambda *args: None)
    monkeypatch.setattr(module, "use_ui_tool", AsyncMock(return_value={"action": "confirm", "approved": True}))
    persist = AsyncMock(return_value=SimpleNamespace(id="contract-revised"))
    monkeypatch.setattr(module, "persist_summary_artifact", persist)
    result = await module.save_subscription_contract(context)
    assert result["success"] is True
    saved = persist.await_args.kwargs
    assert saved["parent_version_id"] == selected.contract.id
    assert saved["draft_only"] is True
    assert saved["input_artifact_kinds"] == ()
    assert saved["canonical_inputs_version"]["design_docs"] == selected.design.id
    assert saved["summary_payload"]["subscription_config_file"]["plans"][0] == selected.catalog["plans"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["summary_digest", "contract_owner", "contract_app", "design_pin", "catalog"])
async def test_bad_selected_inputs_do_not_enable_an_edit(selected, damage):
    if damage == "summary_digest":
        selected.contract.files_manifest[0].sha256 = "f" * 64
    elif damage == "contract_owner":
        selected.contract.commit_metadata.author_user_id = "another-owner"
    elif damage == "contract_app":
        selected.contract.app_id = "another-app"
    elif damage == "design_pin":
        selected.contract.canonical_inputs_version["design_docs"] = "newer-design"
    else:
        selected.contract.commit_metadata.metadata["summary_payload"]["subscription_config_file"]["plans"][0]["label"] = "Changed"
    with pytest.raises(ValueError):
        await capture(selected)


@pytest.mark.asyncio
async def test_export_reuses_reviewed_design_and_unchanged_refs_with_exact_new_contract(selected, monkeypatch):
    from factory_app.workflows._shared.export_inputs import export_input_versions
    from mozaiksai.core import artifacts

    selected.bundle.canonical_inputs_version["theme_capture"] = "theme-selected"
    payload = await capture(selected)
    context = context_for(payload)
    for key, value in payload["context"].items():
        context.set(key, value)
    revised = selected.contract.model_copy(deep=True, update={"id": "contract-revised", "version_number": 2})
    selected.records[revised.id] = revised
    context.set("subscription_contract_artifact_version_id", revised.id)
    context.set("subscription_contract", revised.commit_metadata.metadata["summary_payload"])
    latest = AsyncMock(side_effect=AssertionError("Selected revisions cannot query latest inputs"))
    monkeypatch.setattr(artifacts, "resolve_latest_artifact_version_refs", latest)
    pins = await export_input_versions(context, workflow="AppGenerator", app_id="notes", user_id="owner",
                                       artifact_store=selected.store, additional_families=("theme_capture",))
    assert pins == {"design_docs": selected.design.id, "subscription_contract": revised.id, "theme_capture": "theme-selected"}
    assert selected.design.commit_metadata.metadata["summary_payload"]["access_policy_review"]["build_id"] == "original"
    context.pop("subscription_contract_artifact_version_id")
    with pytest.raises(ValueError, match="Missing exact subscription_contract"):
        await export_input_versions(context, workflow="AppGenerator", app_id="notes", user_id="owner", artifact_store=selected.store)
    latest.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [{"owner_user_id": "other"}, {"build_registry_id": "other"}])
async def test_wrong_owner_or_registry_is_refused_before_source_read(selected, selector):
    with pytest.raises(ValueError, match="outside its owner"):
        await capture(selected, **selector)
    selected.resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_archive_and_summary_fail_closed(selected):
    selected.archive.write_bytes(b"changed")
    with pytest.raises(ValueError):
        await capture(selected)


@pytest.mark.asyncio
async def test_draft_design_is_unavailable_without_rewriting_its_review(selected):
    selected.approved_context["design_docs_artifact"]["lifecycle_status"] = "draft"
    before = deepcopy(selected.approved_context)
    with pytest.raises(ValueError, match="design is still a draft"):
        await capture(selected)
    assert selected.approved_context == before


@pytest.mark.asyncio
async def test_snapshot_survives_source_invalidation_and_uses_real_bridge(selected):
    payload = await capture(selected)
    selected.contract.lifecycle_status = "stale"
    context = context_for(payload)
    projected = subscription_revision_design_context(context)
    assert projected["backend_design_document"] == "SELECTED BACKEND"
    assert projected["design_docs_artifact_version_id"] == selected.design.id
    assert projected["run_build_binding"]["build_id"] == "new-revision"
    assert context.get("backend_design_document") == "GLOBAL NEWER BACKEND"
    assert subscription_revision_baseline(context).subscription_record.id == selected.contract.id
    context.set("user_id", "other")
    with pytest.raises(ValueError, match="outside this revision"):
        subscription_revision_baseline(context)


@pytest.mark.asyncio
async def test_subscription_revision_hydrates_selected_app_archive_and_theme(selected, monkeypatch):
    from factory_app.workflows._shared import revision_app_source
    from factory_app.workflows.AppGenerator.tools.hydrate_app_revision_context import (
        hydrate_revision_files,
    )

    payload = await capture(selected)
    context = context_for(payload)
    context.set("captured_theme_config", {"theme": {"primary": "GLOBAL NEWER THEME"}})
    selected.records["bundle-newer"] = selected.bundle.model_copy(update={"id": "bundle-newer", "version_number": 99})
    selected.bundle.lifecycle_status = "stale"
    monkeypatch.setattr(revision_app_source, "get_artifact_store", lambda: selected.store)
    monkeypatch.setattr(revision_app_source, "require_accepted_genesis_baseline", AsyncMock())
    result = await hydrate_revision_files(context, initialize_theme=True)
    assert result["artifact_version_id"] == selected.bundle.id
    assert context.get("artifact_version_id") == selected.contract.id
    assert context.get("captured_theme_config") == {"theme": {"primary": "selected-cyan"}}
    assert context.get("generated_files")["ui/selected.txt"] == "Selected app bytes"
    assert all(call.kwargs["build_record_id"] != "bundle-newer" for call in selected.store.get_build_record.await_args_list)
    context.set("revision_source_artifact_version_id", "bundle-newer")
    with pytest.raises(ValueError, match="selection_mismatch"):
        await hydrate_revision_files(context, initialize_theme=True)


@pytest.mark.asyncio
async def test_prompt_receives_selected_plan_content_and_original_design(selected):
    from factory_app.workflows._shared.subscription_contract_context import (
        inject_subscription_action_inventory,
    )

    context = context_for(await capture(selected))
    agent = SimpleNamespace(name="ContractDesignerAgent", context_variables=context, system_message="Design plans.")
    agent.update_system_message = lambda value: setattr(agent, "system_message", value)
    await inject_subscription_action_inventory(agent, [])
    assert "SELECTED SUBSCRIPTION REVISION" in agent.system_message
    assert '"amount_cents": 1700' in agent.system_message
    assert '"plan_id": "pro"' in agent.system_message
    assert "Preserve every unrequested plan" in agent.system_message
    assert "GLOBAL NEWER BACKEND" not in agent.system_message


@pytest.mark.asyncio
async def test_snapshot_digest_and_cross_app_run_are_checked(selected):
    payload = await capture(selected)
    payload["catalog"]["plans"][0]["label"] = "tampered"
    with pytest.raises(ValueError, match="snapshot changed"):
        subscription_revision_baseline(context_for(payload))
    context = context_for(await capture(selected))
    context.set("run_build_binding", {**dict(context.get("run_build_binding")), "target_app_id": "other"})
    with pytest.raises(ValueError, match="outside this revision"):
        subscription_revision_baseline(context)


@pytest.mark.parametrize("workflow", ["SubscriptionContractDesigner", "AgentGenerator", "AppGenerator"])
def test_closed_snapshot_authority_and_declared_data_reference_paths(workflow):
    definitions = yaml.safe_load((ROOT / "factory_app/workflows" / workflow / "context_variables.yaml").read_text())["definitions"]
    policy = build_context_authority_policy(workflow_name=workflow, definitions=definitions)
    policy.require_can_write("subscription_revision_baseline", writer_id="transition_router")
    for writer in ("caller_input", "context_bridge", "tool_writeback", "agent_text"):
        with pytest.raises(ContextAuthorityError):
            policy.require_can_write("subscription_revision_baseline", writer_id=writer)
    for key in ("backend_design_document", "design_surface_map", "data_contract"):
        if key not in definitions:
            continue
        assert definitions[key]["source"]["snapshot"] == {
            "context_key": "subscription_revision_baseline", "path": "context." + key,
        }
