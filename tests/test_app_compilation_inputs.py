"""Saved approval, not candidate source or latest workflow state, owns replay."""
from __future__ import annotations

import hashlib
import json
import zipfile
from copy import deepcopy
from dataclasses import FrozenInstanceError
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes
from mozaiksai.core.validation import (
    GeneratedAppValidationRequest,
    validate_generated_app_bundle,
    validate_generated_app_candidate,
)
from mozaiksai.core.validation.compilation_inputs import (
    ApprovedAppCompilation,
    capture_app_compilation_inputs,
    resolve_app_compilation_inputs,
)


class Store:
    def __init__(self):
        self.records = {}

    async def get_build_record(self, *, app_id, build_record_id):
        record = self.records.get(build_record_id)
        return record if record and record.app_id == app_id else None

    async def create_build_record(self, **kwargs):
        record = BuildRecord(id=f"child-{len(self.records)}", version_number=len(self.records), lineage_root_id="bundle", **kwargs)
        self.records[record.id] = record
        return record


@pytest.fixture
async def saved(tmp_path):
    store = Store()
    binding = {"target_app_id": "target", "build_registry_id": "registry", "build_id": "genesis", "phase": "genesis"}
    payload = {"data_contract": {"version": "1", "surfaces": [], "shared_collections": []}, "surface_map": {"surfaces": []}}
    raw = _summary_bytes(payload)
    design = BuildRecord(
        id="design", app_id="target", build_family="design_docs", build_key="design_docs", version_number=1,
        lineage_root_id="design", source_workflow="DesignDocs", lifecycle_status="current",
        files_manifest=[{"path": "design_docs.json", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}],
        commit_metadata={"author_user_id": "owner", "metadata": {"summary_payload": payload}},
    )
    store.records[design.id] = design
    packs = [{"id": "outcome_feedback", "capability_source": "generated_module"}]
    context = {
        "run_build_binding": binding, "app_plan_outcome": "ready",
        "app_build_plan": {"capability_packs": packs, "build_tasks": []},
        "data_contract": payload["data_contract"], "design_surface_map": payload["surface_map"],
        "design_docs_artifact": design.model_dump(mode="json", by_alias=True),
        "generated_files": {"modules/evil/handler.py": "candidate cannot authorize itself"},
        "app_bundle_acceptance_result": {"passed": True},
    }
    path = tmp_path / "bundle.zip"
    path.write_bytes(b"verified saved archive")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    compilation = await capture_app_compilation_inputs(context, owner_user_id="owner", archive_sha256=sha, artifact_store=store)
    bundle = BuildRecord(
        id="bundle", app_id="target", build_family="app_bundle", build_key="app_bundle", version_number=1,
        lineage_root_id="bundle", source_workflow="AppGenerator", lifecycle_status="current",
        canonical_inputs_version={"design_docs": "design"},
        files_manifest=[{"path": "bundle/bundle.zip", "sha256": sha, "size_bytes": path.stat().st_size, "content_type": "application/zip"}],
        commit_metadata={"author_user_id": "owner", "metadata": {
            **binding, "compilation_inputs": compilation, "capability_packs": packs,
            "bundle_name": "bundle", "artifact_path": str(path), "workspace_dir": str(tmp_path / "app"),
        }},
    )
    store.records[bundle.id] = bundle
    return store, context, bundle


async def resolve(saved, **kwargs):
    store, _, bundle = saved
    return await resolve_app_compilation_inputs(**{
        "app_id": "target", "build_record_id": bundle.id, "owner_user_id": "owner", "build_registry_id": "registry",
        "artifact_store": store, **kwargs,
    })


@pytest.mark.asyncio
async def test_resolver_replays_only_exact_saved_inputs(saved):
    store, context, _ = saved
    context["app_build_plan"]["build_tasks"].append({"task_id": "later-chat-edit"})
    # A newer record cannot replace the pinned input; no list/latest method exists.
    store.records["new-design"] = store.records["design"].model_copy(update={"id": "new-design", "version_number": 2})
    approval = await resolve(saved)
    projected = approval.context_for("target")
    assert projected["app_build_plan"]["build_tasks"] == []
    assert not {"generated_files", "code_files", "app_bundle_acceptance_result", "app_task_outputs"} & projected.keys()
    projected["app_build_plan"]["build_tasks"].append({"task_id": "candidate-edit"})
    assert approval.context_for("target")["app_build_plan"]["build_tasks"] == []
    with pytest.raises(FrozenInstanceError):
        approval._context_json = b"{}"
    with pytest.raises(ValueError, match="different app"):
        approval.context_for("foreign")
    with pytest.raises(TypeError, match="resolver|resolve_app"):
        ApprovedAppCompilation(key=object(), context={}, binding=None, record=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_count", [5, 10])
async def test_resolver_preserves_shared_app_bundle_archive_limit(saved, asset_count):
    from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
    from mozaiksai.core.artifacts.content_store import ContentIntegrityError

    _, _, bundle = saved
    path = Path(bundle.commit_metadata.metadata["artifact_path"])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(asset_count):
            archive.writestr(f"bundle/ui/assets/{index}.png", b"x" * 7_000_000)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    bundle.files_manifest[0].sha256 = sha
    bundle.files_manifest[0].size_bytes = len(raw)
    bundle.commit_metadata.metadata["compilation_inputs"]["archive_sha256"] = sha
    if asset_count == 5:
        assert len(raw) > 32_000_000
        files, diagnostics = await read_artifact_bundle(bundle, include_binary=True)
        assert len(files) == asset_count and not diagnostics
        assert (await resolve(saved)).context_for("target")["run_build_binding"]["build_id"] == "genesis"
    else:
        for read in (lambda: read_artifact_bundle(bundle, include_binary=True), lambda: resolve(saved)):
            with pytest.raises(ContentIntegrityError, match="archive_too_large"):
                await read()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["owner", "registry", "app", "build", "summary", "stale", "plan", "archive", "reference", "missing"])
async def test_resolver_refuses_identity_and_authority_drift(saved, kind):
    store, _, bundle = saved
    overrides = {}
    if kind == "owner":
        overrides["owner_user_id"] = "foreign"
    elif kind == "registry":
        overrides["build_registry_id"] = "foreign"
    elif kind == "app":
        overrides["app_id"] = "foreign"
    elif kind == "build":
        bundle.commit_metadata.metadata["build_id"] = "foreign"
    elif kind == "summary":
        store.records["design"].commit_metadata.metadata["summary_payload"]["data_contract"]["version"] = "tampered"
    elif kind == "stale":
        store.records["design"] = store.records["design"].model_copy(update={"lifecycle_status": type(bundle.lifecycle_status).STALE})
    elif kind == "plan":
        bundle.commit_metadata.metadata["compilation_inputs"]["inputs"]["app_build_plan"]["build_tasks"] = [{"task_id": "tampered"}]
    elif kind == "archive":
        bundle.commit_metadata.metadata["compilation_inputs"]["archive_sha256"] = "0" * 64
    elif kind == "reference":
        bundle.canonical_inputs_version["design_docs"] = "other"
    else:
        del store.records["design"]
    with pytest.raises(ValueError):
        await resolve(saved, **overrides)


@pytest.mark.asyncio
async def test_capture_refuses_unready_or_unbound_context(saved):
    store, context, _ = saved
    for update in ({"app_plan_outcome": "blocked"}, {"design_docs_artifact": None}, {"data_contract": {"version": "candidate"}}):
        with pytest.raises(ValueError):
            await capture_app_compilation_inputs({**context, **update}, owner_user_id="owner", archive_sha256="a" * 64, artifact_store=store)


@pytest.mark.asyncio
async def test_summary_identity_does_not_replace_role_review(saved):
    from tests.test_design_access_policy import _bundle

    store, context, _ = saved
    design = _bundle()
    payload = {key: design[key] for key in ("data_contract", "surface_map", "access_policy")}
    record = store.records["design"]
    record.commit_metadata.metadata["summary_payload"] = payload
    raw = _summary_bytes(payload)
    record.files_manifest[0].sha256 = hashlib.sha256(raw).hexdigest()
    record.files_manifest[0].size_bytes = len(raw)
    context.update(data_contract=payload["data_contract"], design_surface_map=payload["surface_map"],
                   design_docs_artifact=record.model_dump(mode="json", by_alias=True))
    with pytest.raises(ValueError, match="access_policy"):
        await capture_app_compilation_inputs(context, owner_user_id="owner", archive_sha256="a" * 64, artifact_store=store)


@pytest.mark.asyncio
async def test_resolver_checks_the_saved_archive_bytes(saved):
    from mozaiksai.core.artifacts.content_store import ContentIntegrityError

    Path(saved[2].commit_metadata.metadata["artifact_path"]).write_bytes(b"replaced archive")
    with pytest.raises(ContentIntegrityError):
        await resolve(saved)


@pytest.mark.asyncio
async def test_factory_save_pins_used_design_instead_of_later_current(saved, monkeypatch):
    from factory_app.workflows.AppGenerator.tools import generate_and_download as download

    store, context, parent = saved
    context["app_bundle_acceptance_status"] = "passed"
    artifacts = import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts, "resolve_latest_artifact_version_refs", AsyncMock(return_value={
        "design_docs": "newer-unrelated-design", "subscription_contract": "not-used", "workflow_bundle": "workflow",
    }))
    child = await download._register_app_bundle_artifact_version(
        app_id="target", user_id="owner", workflow_name="AppGenerator", chat_id="chat", bundle_name="bundle",
        zip_path=Path(parent.commit_metadata.metadata["artifact_path"]), context_variables=context, artifact_store=store,
    )
    assert child.canonical_inputs_version == {"design_docs": "design", "workflow_bundle": "workflow"}
    assert child.commit_metadata.metadata["compilation_inputs"]["inputs"]["design_docs"]["record_id"] == "design"


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["design_docs_artifact", "design_docs_build_artifact"])
async def test_factory_save_pins_data_reference_loaded_design(saved, monkeypatch, reference):
    from factory_app.workflows.AppGenerator.tools import generate_and_download as download
    from mozaiksai.core.workflow.context import variables
    from mozaiksai.core.workflow.context.schema import ContextVariableDefinition
    from mozaiksai.core.workflow.generator_support.design_access_policy import access_policy_sha256
    from tests.test_context_data_reference_ordering import _Collection, _install_collection

    store, context, parent = saved
    design = store.records["design"]
    payload = design.commit_metadata.metadata["summary_payload"]
    payload["access_policy"] = {"roles": [], "grants": []}
    payload["access_policy_review"] = {
        "status": "not_required", "build_id": "genesis", "target_app_id": "target",
        "policy_sha256": access_policy_sha256(payload["access_policy"]),
    }
    raw = _summary_bytes(payload)
    design.files_manifest[0].sha256 = hashlib.sha256(raw).hexdigest()
    design.files_manifest[0].size_bytes = len(raw)
    row = design.model_dump(mode="json", by_alias=True)
    collection = _Collection([row])
    _install_collection(monkeypatch, collection)
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator/context_variables.yaml"
    definition = ContextVariableDefinition.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))["definitions"][reference]
    )
    context.pop("design_docs_artifact")
    context[reference] = await variables._load_data_reference_value(
        reference, definition, default_database_name=None, app_id="mozaiks-factory", context=context,
    )
    assert collection.query["app_id"] == "target"
    if reference == "design_docs_build_artifact":
        assert collection.query["commit_metadata.metadata.summary_payload.access_policy_review.build_id"] == "genesis"
    context["app_bundle_acceptance_status"] = "passed"
    monkeypatch.setattr(import_module("mozaiksai.core.artifacts"), "resolve_latest_artifact_version_refs",
                        AsyncMock(return_value={"design_docs": "later-design"}))
    child = await download._register_app_bundle_artifact_version(
        app_id="target", user_id="owner", workflow_name="AppGenerator", chat_id="chat", bundle_name="bundle",
        zip_path=Path(parent.commit_metadata.metadata["artifact_path"]), context_variables=context, artifact_store=store,
    )
    assert child.canonical_inputs_version == {"design_docs": "design"}
    assert child.commit_metadata.metadata["compilation_inputs"]["inputs"]["design_docs"] == {
        "record_id": "design", "sha256": hashlib.sha256(raw).hexdigest(),
    }


@pytest.mark.asyncio
async def test_capture_uses_saved_subscription_id_and_rejects_contract_substitution(saved):
    from tests.appgenerator_subscription_fixture import subscription_contract

    store, context, bundle = saved
    contract = subscription_contract("commerce", "create_product")
    raw = _summary_bytes(contract)
    record = BuildRecord(
        id="subscription-used", app_id="target", build_family="subscription_contract", build_key="subscription_contract",
        version_number=1, lineage_root_id="subscription-used", source_workflow="SubscriptionContractDesigner", lifecycle_status="draft",
        files_manifest=[{"path": "subscription_contract.json", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}],
        commit_metadata={"author_user_id": "owner", "metadata": {"summary_payload": contract}},
    )
    store.records[record.id] = record
    context.update(subscription_contract=contract, subscription_contract_artifact_version_id=record.id)
    snapshot = await capture_app_compilation_inputs(
        context, owner_user_id="owner", archive_sha256=bundle.files_manifest[0].sha256, artifact_store=store,
    )
    bundle.commit_metadata.metadata["compilation_inputs"] = snapshot
    bundle.canonical_inputs_version["subscription_contract"] = record.id
    assert (await resolve(saved)).context_for("target")["subscription_contract"] == contract
    context["subscription_contract"] = {**contract, "selected_features_by_plan": {"free": ["unapproved"]}}
    with pytest.raises(ValueError, match="subscription differs"):
        await capture_app_compilation_inputs(context, owner_user_id="owner", archive_sha256="a" * 64, artifact_store=store)


@pytest.mark.asyncio
async def test_child_keeps_original_inputs_and_exact_parent(saved):
    store, _, bundle = saved
    approval = await resolve(saved)
    metadata = deepcopy(bundle.commit_metadata.model_dump())
    metadata["metadata"]["build_id"] = "refinement"
    metadata["metadata"]["phase"] = "refinement"
    metadata["metadata"]["compilation_inputs"] = approval.for_child(bundle.files_manifest[0].sha256)
    child = bundle.model_copy(update={
        "id": "child", "version_number": 2, "parent_build_record_id": bundle.id,
        "source_workflow": "control_plane_coding", "commit_metadata": type(bundle.commit_metadata).model_validate(metadata),
    })
    store.records[child.id] = child
    child_approval = await resolve(saved, build_record_id=child.id)
    assert child_approval.context_for("target") == approval.context_for("target")
    child.parent_build_record_id = "foreign"
    with pytest.raises(ValueError, match="parent"):
        await resolve(saved, build_record_id=child.id)


@pytest.mark.asyncio
async def test_fresh_candidate_checks_receive_approved_inputs_without_old_evidence(saved, monkeypatch):
    approval = await resolve(saved)
    owner = import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    acceptance = AsyncMock(return_value={"status": "passed", "passed": True})
    build = AsyncMock(return_value={"validation_status": "passed", "validation_strategy": "docker"})
    monkeypatch.setattr(owner, "run_app_bundle_acceptance_gate", acceptance)
    monkeypatch.setattr(owner, "validate_app_build", build)
    packs = saved[2].commit_metadata.metadata["capability_packs"]
    result = await validate_generated_app_candidate(
        files={"app.json": '{"appId":"target"}'}, app_id="target", capability_packs=packs,
        approved_compilation=approval, validation_strategy="docker",
    )
    assert result["validation_status"] == "passed"
    projected = acceptance.await_args.kwargs["context_variables"]
    assert projected["data_contract"] == saved[1]["data_contract"]
    assert "app_bundle_acceptance_result" not in projected
    assert build.await_count == acceptance.await_count == 1
    assert build.await_args.kwargs["context_variables"] == {"app_id": "target"}


async def _settle_five_tasks(context, monkeypatch):
    """Let the canonical batch owner create evidence; substitute only model execution."""
    from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
    from mozaiksai.core.ports.orchestration import RunStatus
    from mozaiksai.core.workflow import task_batches as batches

    tasks = [
        {"task_id": f"task-{index}", "task_type": "business_services", "initial_agent": "EvidenceAgent",
         "initial_message": "Write the owned fixture file", "owned_paths": [f"modules/records/backend/part{index}.py"],
         "depends_on": [f"task-{index - 1}"] if index else []}
        for index in range(5)
    ]
    context["app_build_plan"]["build_tasks"] = deepcopy(tasks)
    context["app_task_batch_items"] = deepcopy(tasks)
    root = Path(__file__).resolve().parents[1]
    config = batches.parse_task_batches_config(yaml.safe_load(
        (root / "factory_app/workflows/AppGenerator/extended_orchestration/task_batches.yaml").read_text(encoding="utf-8"),
    ))
    calls, checkpoints = [], []

    async def run(self, request):
        calls.append(request.task_id)
        task = request.context_variables["current_task"]
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output={
            "task_id": request.task_id,
            "code_files": [{"filename": path, "content": "# café — 星\n"} for path in task["owned_paths"]],
        })

    async def checkpoint(updates):
        checkpoints.append(deepcopy(updates))

    monkeypatch.setattr(batches.AG2TaskBatchRunner, "run", run)
    await batches.execute_task_batches_for_trigger(
        workflow_name="CompilationEvidenceTest", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"EvidenceAgent": object()}, context_variables=context, fresh_agents_per_task=False,
        checkpoint=checkpoint, parent_channel_id="evidence-channel",
    )
    assert calls == [task["task_id"] for task in tasks]
    assert context["app_task_batch_status"] == "completed"
    assert checkpoints[-1]["app_task_batch_results"] == context["app_task_batch_results"]
    return {path: "# café — 星\n" for task in tasks for path in task["owned_paths"]}


@pytest.mark.asyncio
async def test_compilation_roundtrip_preserves_five_settled_tasks_and_non_ascii_digest(saved, monkeypatch):
    from factory_app.workflows.AppGenerator.tools.task_integrity import planned_artifact_diagnostics

    store, context, bundle = saved
    files = {**await _settle_five_tasks(context, monkeypatch), "app.json": "{}"}
    original = deepcopy(context["app_task_batch_results"])
    bundle.commit_metadata.metadata["compilation_inputs"] = await capture_app_compilation_inputs(
        context, owner_user_id="owner", archive_sha256=bundle.files_manifest[0].sha256, artifact_store=store,
    )
    approved = await resolve(saved)
    projected = approved.context_for("target")
    assert projected["app_task_batch_results"] == original
    assert projected["app_task_batch_items"] == context["app_task_batch_items"]
    assert planned_artifact_diagnostics(projected, files) == []
    context["app_task_batch_results"]["task-0"]["code_files"][0]["content"] = "later mutable session edit"
    assert approved.context_for("target")["app_task_batch_results"] == original
    projected["app_task_batch_results"]["task-0"]["code_files"][0]["content"] = "caller edit"
    assert approved.context_for("target")["app_task_batch_results"] == original


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["capture", "resolve"])
@pytest.mark.parametrize("damage", [
    "missing", "meta", "version", "status", "failed", "pending", "in_flight", "extra",
    "completed", "count", "inventory", "plan", "output_digest", "task_id", "worker", "paths",
])
async def test_compilation_refuses_unsettled_or_mismatched_task_evidence(saved, monkeypatch, stage, damage):
    from mozaiksai.core.validation import compilation_inputs as owner
    from mozaiksai.core.workflow.task_batches import task_evidence_digest

    store, context, bundle = saved
    await _settle_five_tasks(context, monkeypatch)
    if stage == "resolve":
        snapshot = await capture_app_compilation_inputs(
            context, owner_user_id="owner", archive_sha256=bundle.files_manifest[0].sha256, artifact_store=store,
        )
        bundle.commit_metadata.metadata["compilation_inputs"] = snapshot
        target = snapshot["inputs"]
    else:
        target = context
    results = target["app_task_batch_results"]
    meta = results["_meta"]
    if damage == "missing":
        results.pop("task-0")
    elif damage == "meta":
        results["_meta"] = "invalid"
    elif damage == "version":
        meta["evidence_version"] = 0
    elif damage == "status":
        meta["status"] = "partial"
    elif damage in {"failed", "pending", "in_flight"}:
        meta[{"failed": "failed_tasks", "pending": "pending_tasks", "in_flight": "in_flight"}[damage]] = ["task-0"]
    elif damage == "extra":
        results["unplanned"] = deepcopy(results["task-0"])
    elif damage == "completed":
        meta["completed_tasks"].pop()
    elif damage == "count":
        meta["task_count"] = 4
    elif damage == "inventory":
        meta["task_inventory_digest"] = "0" * 64
    elif damage == "plan":
        meta["input_digests"]["app_build_plan"] = "0" * 64
    elif damage == "output_digest":
        results["task-0"]["code_files"][0]["content"] = "changed"
    else:
        key = {"task_id": "_task_id", "worker": "_worker_agent", "paths": "_owned_paths"}[damage]
        results["task-0"][key] = ["other.py"] if damage == "paths" else "other"
        meta["accepted_output_digests"]["task-0"] = task_evidence_digest(results["task-0"])
    if stage == "resolve":
        # The outer checksum alone must not confer canonical task identity.
        snapshot["inputs_sha256"] = owner._digest(target)
        operation = resolve(saved)
    else:
        operation = capture_app_compilation_inputs(
            target, owner_user_id="owner", archive_sha256=bundle.files_manifest[0].sha256, artifact_store=store,
        )
    with pytest.raises(ValueError):
        await operation


@pytest.mark.asyncio
async def test_compilation_version_one_is_not_backfilled_from_current_session(saved):
    _, _, bundle = saved
    snapshot = bundle.commit_metadata.metadata["compilation_inputs"]
    snapshot["version"] = 1
    snapshot["inputs"].pop("app_task_batch_items")
    snapshot["inputs"].pop("app_task_batch_results")
    before = deepcopy(snapshot)
    with pytest.raises(ValueError, match="version 1.*new Factory build"):
        await resolve(saved)
    assert snapshot == before


@pytest.mark.asyncio
async def test_compilation_input_size_boundary_is_identical_at_capture_and_resolve(saved):
    from mozaiksai.core.validation.compilation_inputs import _MAX_COMPILATION_INPUT_BYTES

    store, context, bundle = saved
    assert _MAX_COMPILATION_INPUT_BYTES == 8 * 1024 * 1024
    context["app_build_plan"]["description"] = ""
    snapshot = await capture_app_compilation_inputs(
        context, owner_user_id="owner", archive_sha256=bundle.files_manifest[0].sha256, artifact_store=store,
    )
    context["app_build_plan"]["description"] = "x" * (
        _MAX_COMPILATION_INPUT_BYTES - len(_summary_bytes(snapshot["inputs"]))
    )
    snapshot = await capture_app_compilation_inputs(
        context, owner_user_id="owner", archive_sha256=bundle.files_manifest[0].sha256, artifact_store=store,
    )
    assert len(_summary_bytes(snapshot["inputs"])) == _MAX_COMPILATION_INPUT_BYTES
    bundle.commit_metadata.metadata["compilation_inputs"] = snapshot
    assert (await resolve(saved)).context_for("target")["app_build_plan"] == context["app_build_plan"]
    context["app_build_plan"]["description"] += "x"
    with pytest.raises(ValueError, match="8 MiB"):
        await capture_app_compilation_inputs(
            context, owner_user_id="owner", archive_sha256=bundle.files_manifest[0].sha256, artifact_store=store,
        )
    snapshot["inputs"]["app_build_plan"]["description"] += "x"
    with pytest.raises(ValueError, match="8 MiB"):
        await resolve(saved)


@pytest.mark.asyncio
async def test_public_facade_rejects_candidate_supplied_authority_and_selection(saved):
    for authority in ({"app_build_plan": {}}, await resolve(saved)):
        result = validate_generated_app_bundle(GeneratedAppValidationRequest(
            files={"app.json": json.dumps({"appId": "target"})}, capability_packs=[], approved_compilation=authority,
        ))
        assert not result.passed
        assert result.diagnostics[0].code == "approved_compilation_invalid"


@pytest.mark.asyncio
async def test_public_facade_replays_saved_compiled_pack_and_rejects_unbound_or_tampered_output(tmp_path):
    from mozaiksai.core.workflow.context.frozen import detach
    from tests.test_capability_pack_proof import PLAN_INPUTS, _assemble

    files, context, _ = await _assemble("outcome_feedback")
    binding = detach(context.get("run_build_binding"))
    app_id = binding["target_app_id"]
    # Persist the fixed approved input used by the assembler, never candidate-derived authority.
    payload = {"data_contract": deepcopy(PLAN_INPUTS["outcome_feedback"]["data_contract"]), "surface_map": None}
    raw = _summary_bytes(payload)
    store = Store()
    design = BuildRecord(
        id="design", app_id=app_id, build_family="design_docs", build_key="design_docs", version_number=1,
        lineage_root_id="design", source_workflow="DesignDocs", lifecycle_status="current",
        files_manifest=[{"path": "design_docs.json", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}],
        commit_metadata={"author_user_id": "owner", "metadata": {"summary_payload": payload}},
    )
    store.records[design.id] = design
    approved_context = {
        "run_build_binding": binding, "app_plan_outcome": "ready",
        "app_build_plan": detach(context.get("app_build_plan")),
        "data_contract": payload["data_contract"], "design_surface_map": None,
        "design_docs_artifact": design.model_dump(mode="json", by_alias=True),
    }
    # This fixture assembles deterministic templates without model task execution.
    approved_context["app_build_plan"]["build_tasks"] = []
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"bundle/{name}", content)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    compilation = await capture_app_compilation_inputs(
        approved_context, owner_user_id="owner", archive_sha256=sha, artifact_store=store,
    )
    packs = detach(context.get("capability_packs"))
    bundle = BuildRecord(
        id="bundle", app_id=app_id, build_family="app_bundle", build_key="app_bundle", version_number=1,
        lineage_root_id="bundle", source_workflow="AppGenerator", lifecycle_status="current",
        canonical_inputs_version={"design_docs": design.id},
        files_manifest=[{"path": "bundle/bundle.zip", "sha256": sha, "size_bytes": path.stat().st_size,
                         "content_type": "application/zip"}],
        commit_metadata={"author_user_id": "owner", "metadata": {
            **binding, "compilation_inputs": compilation, "capability_packs": packs,
            "bundle_name": "bundle", "artifact_path": str(path), "workspace_dir": str(tmp_path / "app"),
        }},
    )
    store.records[bundle.id] = bundle
    approval = await resolve_app_compilation_inputs(
        app_id=app_id, build_record_id=bundle.id, owner_user_id="owner",
        build_registry_id=binding["build_registry_id"], artifact_store=store,
    )
    result = validate_generated_app_bundle(GeneratedAppValidationRequest(
        files=files, capability_packs=packs, approved_compilation=approval,
    ))
    assert result.passed, result.diagnostics
    assert [row["pack_id"] for row in result.resolved_capability_packs] == ["outcome_feedback"]

    service = "modules/outcome_feedback/backend/service.py"
    for candidate, authority in ((files, None), ({**files, service: files[service] + "\n# unapproved edit\n"}, approval)):
        rejected = validate_generated_app_bundle(GeneratedAppValidationRequest(
            files=candidate, capability_packs=packs, approved_compilation=authority,
        ))
        assert not rejected.passed
        assert any(item.code == "generated_bundle_contract_failed" and service in item.message
                   and "verified template" in item.message for item in rejected.diagnostics), rejected.diagnostics


@pytest.mark.asyncio
async def test_coding_worker_passes_bound_inputs_and_persists_replayable_child(saved, tmp_path):
    from mozaiksai.control_plane.contracts import CodingWorkerPlan, CodingWorkerRequest
    from mozaiksai.control_plane.implementations.coding_worker import ScopedRefinementCodingWorker

    store, _, parent = saved
    evidence = {
        "validation_status": "passed", "validation_strategy": "docker",
        "app_bundle_acceptance_result": {"status": "passed", "passed": True},
        "app_validation_result": {"validation_status": "passed", "validation_strategy": "docker"},
    }
    validator = AsyncMock(return_value=evidence)
    worker = ScopedRefinementCodingWorker(artifact_store=store, candidate_validation_runner=validator, output_root=tmp_path / "worker")
    request = CodingWorkerRequest(
        app_id="factory", target_app_id="target", user_id="owner", build_record_id=parent.id,
        build_family="app_bundle", change_class="patch", run_build_binding={
            "target_app_id": "target", "build_registry_id": "registry", "build_id": "refine", "phase": "refinement",
        },
    )
    files = {"app.json": '{"appId":"target"}'}
    packs = parent.commit_metadata.metadata["capability_packs"]
    validation = await worker._run_candidate_validation(request=request, merged_files=files, validation_strategy="docker", capability_packs=packs)
    assert isinstance(validator.await_args.kwargs["approved_compilation"], ApprovedAppCompilation)
    plan = CodingWorkerPlan(
        summary="Update app", owned_paths=["app.json"], updated_files=[], validation_strategy="docker",
        validation_commands=[], start_preview=False, needs_human_review=True, rationale="Authorized refinement",
    )
    result = await worker._persist_refinement_artifact(
        request=request, resolved_artifact_kind="app_bundle", applied_files=files, merged_files=files,
        plan=plan, validation_result=validation, capability_packs=packs,
    )
    child = store.records[result["build_record_id"]]
    assert child.canonical_inputs_version == parent.canonical_inputs_version
    child_approval = await resolve(saved, build_record_id=child.id)
    assert child_approval.context_for("target")["run_build_binding"]["build_id"] == "genesis"


@pytest.mark.asyncio
async def test_media_child_carries_exact_input_lineage(saved, monkeypatch):
    from factory_app.workflows.AppMediaGenerator.tools import publish_app_media as publisher

    store, _, parent = saved
    approval = await resolve(saved)
    monkeypatch.setattr(publisher, "get_artifact_store", lambda: store)
    child, _ = await publisher._register_media_version(
        parent=parent, files={"app.json": '{"appId":"target"}', "ui/assets/new.svg": "<svg/>"},
        overlay={}, chat_id="media-chat", user_id="owner", approved_compilation=approval,
        validation={
            "validation_status": "passed", "validation_strategy": "docker",
            "app_bundle_acceptance_result": {"status": "passed", "passed": True},
            "app_validation_result": {"validation_status": "passed", "validation_strategy": "docker"},
        },
    )
    assert child.canonical_inputs_version == parent.canonical_inputs_version
    assert (await resolve(saved, build_record_id=child.id)).context_for("target") == approval.context_for("target")


@pytest.mark.asyncio
async def test_refinement_requires_parent_before_staging(saved, tmp_path):
    from mozaiksai.control_plane.contracts import CodingWorkerRequest
    from mozaiksai.control_plane.implementations.coding_worker import ScopedRefinementCodingWorker

    store, _, _ = saved
    output = tmp_path / "unbound"
    worker = ScopedRefinementCodingWorker(artifact_store=store, output_root=output)
    request = CodingWorkerRequest(
        app_id="factory", target_app_id="target", user_id="owner", change_class="patch", build_family="app_bundle",
    )
    with pytest.raises(ValueError, match="requires its parent"):
        await worker._persist_refinement_artifact(
            request=request, resolved_artifact_kind="app_bundle", applied_files={}, merged_files={},
            plan=None, validation_result={}, capability_packs=[],
        )
    assert not output.exists()


def test_subscription_record_id_relay_uses_runtime_router_authority(monkeypatch):
    from mozaiksai.core.workflow.context.authority import build_context_authority_policy
    from mozaiksai.core.workflow.pack.journey_orchestrator import _project_launch_context
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    root = Path(__file__).resolve().parents[1] / "factory_app/workflows"
    configs = {}
    for name in ("SubscriptionContractDesigner", "AgentGenerator", "AppGenerator"):
        configs[name] = {"context_variables": yaml.safe_load((root / name / "context_variables.yaml").read_text(encoding="utf-8"))}
        policy = build_context_authority_policy(workflow_name=name, definitions=configs[name]["context_variables"]["definitions"])
        assert policy.can_write("subscription_contract_artifact_version_id", writer_id="lifecycle_tool")
        for writer in ("caller_input", "structured_output", "agent_text"):
            assert not policy.can_write("subscription_contract_artifact_version_id", writer_id=writer)
    monkeypatch.setattr(workflow_manager, "get_config", lambda name: configs[name])
    seed = {"subscription_contract_artifact_version_id": "exact-saved-subscription"}
    for name in ("AgentGenerator", "AppGenerator"):
        seed = _project_launch_context(seed, name)
        assert seed == {"subscription_contract_artifact_version_id": "exact-saved-subscription"}
