from __future__ import annotations

import hashlib
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import HTTPException

from factory_app.app.modules.app_registry.backend.repo import AppRegistryRepo
from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.platform import build_target
from factory_app.workflows.AppGenerator.tools import hydrate_app_revision_context as revision
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from mozaiksai.core.artifacts.content_store import ContentNotFoundError
from mozaiksai.core.artifacts.models import BuildRecord, ChangeRequestDoc
from mozaiksai.core.auth import UserPrincipal
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.session import launcher
from mozaiksai.core.session.model import TriggerInput
from mozaiksai.core.session.router import SessionRouter
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    CALLER_INPUT_WRITER,
    TRANSITION_ROUTER_WRITER,
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.execution import lifecycle
from mozaiksai.core.workflow.workflow_manager import workflow_manager
from mozaiksai.hosts import studio
from tests.test_session_launcher import (
    _ChatSessionPersistenceAdapter,
    _FakePersistence,
    _MemoryCollection,
)

ROOT = Path(__file__).resolve().parents[1]
BINDING = {"build_registry_id": "registry", "target_app_id": "target", "build_id": "repair_build", "phase": "refinement"}
REQUEST = "Repair the existing saved form bindings without changing approved scope."
FIND_UNMET_DEPENDENCY = SessionRouter._find_first_unmet_dependency


@pytest.fixture
def retry(monkeypatch):
    pm = _FakePersistence()
    pm.create_chat_session = _ChatSessionPersistenceAdapter(pm).create_chat_session

    async def server_fields(*, chat_id, fields, **_):
        pm._default._docs[chat_id].update(deepcopy(fields))

    pm.persist_server_owned_session_fields = server_fields
    source = {
        "_id": "failed", "app_id": "host", "user_id": "owner", "workflow_name": "AppGenerator", "status": 2,
        "run_build_binding": deepcopy(BINDING), "build_mode": "revision", "change_request_id": "change",
        "revision_id": "revision", "refinement_request": REQUEST,
        "trigger_meta": {"trigger_source": "refinement", "change_class": "patch", "build_record_id": "baseline",
                         "build_family": "app_bundle", "workflow_sequence": "app_revision", "journey_id": "app_revision"},
        "generated_files": {"bad.py": "failed output"}, "app_validation_status": "passed",
        "app_bundle_acceptance_status": "passed", "integration_tests_passed": True,
        "app_plan_attempts": 3, "artifact_version_id": "failed_partial_output",
    }
    pm._default._docs["failed"] = source
    pm._default._docs["idle"] = {**deepcopy(source), "_id": "idle", "status": 0, "build_mode": "initial"}
    records = _MemoryCollection()
    record = {"_id": "registry", "build_registry_id": "registry", "app_id": "target", "chat_app_id": "host",
              "owner_user_id": "owner", "active_chat_id": "idle", "current_build_run": {"build_id": "repair_build"}}
    records._docs["registry"] = record
    repo = AppRegistryRepo(pm)
    monkeypatch.setattr(repo, "_collection", AsyncMock(return_value=records))
    monkeypatch.setattr(repo, "ensure_indexes", AsyncMock())
    service = AppRegistryService(repo)
    monkeypatch.setattr(service, "begin_refinement_run", AsyncMock(side_effect=AssertionError("Retry must not allocate a build")))
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: service)
    monkeypatch.setattr(build_target, "AppRegistryService", lambda: service)
    monkeypatch.setattr(launcher, "_PERSISTENCE_MANAGER", pm)
    hooks = PlatformHookRegistry()
    hooks.register_bundle({"chat_session_fields": build_target.bind_factory_session}, source="test")
    monkeypatch.setattr(launcher, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(launcher, "apply_launch_context_provider", AsyncMock(side_effect=lambda **kwargs: kwargs["context_variables"]))
    router = SessionRouter(persistence=pm)
    monkeypatch.setattr(SessionRouter, "_find_first_unmet_dependency", AsyncMock(return_value=None))
    monkeypatch.setattr(studio, "get_session_router", lambda: router)
    monkeypatch.setattr(studio, "get_orchestration_control_harness", lambda: SimpleNamespace())

    def config(name):
        directory = ROOT / "factory_app/workflows" / name
        return {key: yaml.safe_load((directory / f"{key}.yaml").read_text(encoding="utf-8"))
                for key in ("context_variables", "transition_graph")}

    monkeypatch.setattr(workflow_manager, "get_config", config)
    baseline = BuildRecord(
        _id="baseline", app_id="target", build_family="app_bundle", build_key="app_bundle",
        version_number=1, lineage_root_id="baseline", lifecycle_status="stale",
        commit_metadata={"author_user_id": "owner", "metadata": {
            **BINDING, "build_id": "approved_build", "workspace_dir": "/saved/approved/app",
        }},
    )
    change = ChangeRequestDoc(
        _id="change", app_id="target", created_by_user_id="owner", build_family="app_bundle", build_key="app_bundle",
        build_record_id="baseline", raw_user_request=REQUEST, classification="patch",
        refinement_request={"build_family": "app_bundle", "build_key": "app_bundle", "build_record_id": "baseline",
                            "raw_user_request": REQUEST, "app_id": "host", "target_app_id": "target", "user_id": "owner",
                            "source_surface": "app_workbench", "extra": {"bundle_path": "/stale/client/path"}},
        change_intent={"change_class": "patch", "source": "llm", "rationale": "Repair only."},
        impact_set={"workflow_sequence": "app_revision", "affected_workflows": ["AppGenerator"]},
        router_decision={"workflow_id": "AppGenerator", "workflow_sequence": "app_revision", "execution_mode": "workflow"},
    )
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=baseline), get_change_request=AsyncMock(return_value=change))
    monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
    principal = UserPrincipal(user_id="owner", app_id="host", email=None, name=None, roles=[],
                              scopes=["access_as_user"], raw_claims={}, provider="jwt")
    return SimpleNamespace(pm=pm, source=source, record=record, store=store, change=change, baseline=baseline,
                           principal=principal, service=service, hooks=hooks, config=config)


async def launch(retry, **changes):
    body = studio.WorkflowTriggerRequest(**{
        "workflow_id": "AppGenerator", "app_id": "host", "user_id": retry.principal.user_id,
        "trigger_source": "manual", "source_chat_id": "failed", "retry_failed": True, **changes,
    })
    return await studio.trigger_workflow(body, retry.principal)


@pytest.mark.asyncio
async def test_refinement_retry_uses_saved_request_and_baseline_with_fresh_execution_state(retry):
    before = deepcopy(retry.pm._default._docs)
    result = await launch(retry)
    fresh = retry.pm._default._docs[result["chat_id"]]
    assert result["chat_id"] not in before
    assert result["journey_id"] == "app_revision"
    assert fresh["run_build_binding"] == BINDING
    assert fresh["build_mode"] == "revision"
    assert fresh["refinement_request"] == REQUEST
    assert fresh["revision_scope"] == "patch"
    assert fresh["artifact_version_id"] == "baseline"
    assert fresh["change_request_id"] == "change"
    assert fresh["revision_id"] == "revision"
    assert fresh["refinement_request_meta"]["extra"]["bundle_path"] == "/saved/approved/app"
    assert fresh["lifecycle_state"] == "review"
    assert fresh["status"] == 0 and fresh["messages"] == [] and fresh["last_sequence"] == 0
    for key in ("generated_files", "app_validation_status", "app_bundle_acceptance_status", "integration_tests_passed", "app_plan_attempts"):
        assert key not in fresh
    for chat_id, original in before.items():
        assert retry.pm._default._docs[chat_id] == original
    retry.service.begin_refinement_run.assert_not_awaited()
    retry.store.get_build_record.assert_awaited_once_with(app_id="target", build_record_id="baseline")
    # The canonical initial agent stays InterviewAgent; its revision prompt, not
    # a new runtime override, supplies the patch -> NEXT behavior.
    directory = ROOT / "factory_app/workflows/AppGenerator"
    assert yaml.safe_load((directory / "orchestrator.yaml").read_text())["initial_agent"] == "InterviewAgent"
    agent = yaml.safe_load((directory / "agents.yaml").read_text(encoding="utf-8"))["agents"][0]
    prompt = "\n".join(section["content"] for section in agent["prompt_sections"])
    assert "ContextVariables.build_mode` equals `revision`" in prompt
    assert "`patch`: confirm the targeted change only; emit NEXT immediately." in prompt
    assert "initial_agent_name_override" not in fresh


@pytest.mark.asyncio
async def test_genesis_retry_does_not_inherit_refinement_or_failed_output(retry):
    retry.source["run_build_binding"]["phase"] = "genesis"
    retry.source["build_mode"] = "initial"
    retry.source.pop("change_request_id")
    retry.source["trigger_meta"] = {"trigger_source": "transition", "journey_id": "build"}
    result = await launch(retry)
    fresh = retry.pm._default._docs[result["chat_id"]]
    assert fresh["run_build_binding"]["phase"] == "genesis"
    assert result["journey_id"] == fresh["trigger_meta"]["journey_id"] == "build"
    assert "build_mode" not in fresh and "refinement_request" not in fresh and "generated_files" not in fresh
    retry.store.get_change_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_source_chat_launch_is_unchanged_without_retry_intent(retry):
    result = await launch(retry, retry_failed=False)
    fresh = retry.pm._default._docs[result["chat_id"]]
    assert "build_mode" not in fresh and "refinement_request" not in fresh
    retry.store.get_change_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_of_failed_retry_retains_intent_and_provenance_not_execution_state(retry):
    first = await launch(retry)
    failed_retry = retry.pm._default._docs[first["chat_id"]]
    failed_retry.update(status=2, generated_files={"bad.py": "second failed output"}, app_plan_attempts=3,
                        app_validation_status="failed", artifact_version_id="new_failed_partial_output")
    retry.record["active_chat_id"] = first["chat_id"]
    before = deepcopy(retry.pm._default._docs)
    second = await launch(retry, source_chat_id=first["chat_id"])
    fresh = retry.pm._default._docs[second["chat_id"]]
    assert second["chat_id"] not in before
    assert fresh["run_build_binding"] == BINDING
    assert fresh["build_mode"] == "revision" and fresh["refinement_request"] == REQUEST
    assert fresh["change_request_id"] == "change" and fresh["revision_id"] == "revision"
    assert fresh["artifact_version_id"] == "baseline"
    for key, value in {"build_record_id": "baseline", "build_family": "app_bundle", "change_class": "patch",
                       "journey_id": "app_revision", "workflow_sequence": "app_revision"}.items():
        assert fresh["trigger_meta"][key] == failed_retry["trigger_meta"][key] == value
    assert fresh["status"] == 0 and fresh["messages"] == [] and fresh["last_sequence"] == 0
    assert not {"generated_files", "app_validation_status", "app_plan_attempts"} & fresh.keys()
    for chat_id, original in before.items():
        assert retry.pm._default._docs[chat_id] == original
    retry.service.begin_refinement_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_archive", "changed_archive", "binary", "retired_after_launch"])
async def test_retry_seed_runs_canonical_before_chat_baseline_loader(retry, monkeypatch, tmp_path, fault):
    archive = tmp_path / "bundle.zip"
    files = {"app.json": json.dumps({"appId": "target"}), "ui/pages/home.yaml": "page_type: settings\n"}
    with zipfile.ZipFile(archive, "w") as bundle:
        for path, text in files.items():
            bundle.writestr(f"bundle/{path}", text)
        if fault == "binary":
            bundle.writestr("bundle/image.png", b"\x89PNG")
    retry.baseline = BuildRecord.model_validate({
        **retry.baseline.model_dump(mode="python"),
        "files_manifest": [{"path": "bundle/bundle.zip", "content_type": "application/zip",
                            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}],
        "commit_metadata": {"author_user_id": "owner", "metadata": {
            **retry.baseline.commit_metadata.metadata, "bundle_name": "bundle", "artifact_path": str(archive),
        }},
    })
    retry.store.get_build_record.return_value = retry.baseline
    result = await launch(retry)
    fresh = deepcopy(retry.pm._default._docs[result["chat_id"]])
    policy = build_context_authority_policy(
        workflow_name="AppGenerator", definitions=retry.config("AppGenerator")["context_variables"]["definitions"],
        transition_rules=retry.config("AppGenerator")["transition_graph"],
    )
    # Seed the closed baseline selector with the same trusted writer as prepare;
    # lifecycle loading then uses the runtime container, as orchestration does.
    artifact_id = fresh.pop("artifact_version_id")
    seed = create_context_container(fresh, authority_policy=policy, writer_id=TRANSITION_ROUTER_WRITER)
    seed.set("artifact_version_id", artifact_id)
    context = create_context_container(seed.snapshot(), authority_policy=policy)
    monkeypatch.setattr(revision, "get_artifact_store", lambda: retry.store)
    monkeypatch.setattr(lifecycle, "resolve_workflow_path", lambda _: ROOT / "factory_app/workflows/AppGenerator")
    manager = lifecycle.get_lifecycle_manager("AppGenerator")
    first_hook = manager.tools[lifecycle.LifecycleTrigger.BEFORE_CHAT][0]
    assert first_hook.function == "hydrate_app_revision_context"
    assert first_hook.file == "hydrate_app_revision_context.py"
    monkeypatch.setitem(first_hook.callable.__globals__, "get_artifact_store", lambda: retry.store)
    # Other lifecycle hooks have unrelated stores; execute the real first hook only.
    manager.tools[lifecycle.LifecycleTrigger.BEFORE_CHAT] = [first_hook]
    monkeypatch.setattr(manager, "_emit_lifecycle_event", AsyncMock())
    expected_error = None
    if fault == "missing_archive":
        retry.baseline.commit_metadata.metadata["artifact_path"] = str(tmp_path / "nonexistent.zip")
        expected_error = "Local content not found"
    elif fault == "changed_archive":
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("bundle/changed.txt", "changed since commit")
        expected_error = "artifact_bundle_digest_mismatch"
    elif fault == "binary":
        expected_error = "revision_baseline_incomplete: binary_asset"
    elif fault == "retired_after_launch":
        retry.baseline.lifecycle_status = "deleted"
        expected_error = "revision_baseline_artifact_retired"
    await manager.trigger_before_chat(context_variables=context)
    event = manager._emit_lifecycle_event.await_args.args
    assert context.get("build_mode") == "revision"
    if expected_error:
        assert event[0] == "lifecycle.tool_error" and expected_error in event[1]["error"]
        assert context.get("generated_files") is None
        # Lifecycle errors are logged, not fatal. Assembly must still reject the
        # same missing/changed baseline, never assemble from an empty genesis.
        with pytest.raises((ValueError, ContentNotFoundError), match=expected_error):
            await assemble_app_tasks(context_variables=context)
        assert context.get("generated_files") is None
    else:
        assert event[0] == "lifecycle.tool_result" and event[1]["status"] == "success"
        assert context.get("generated_files") == files
        assert "bad.py" not in context.get("generated_files")
    assert "generated_files" not in retry.pm._default._docs[result["chat_id"]]


def test_retry_lifecycle_seed_remains_closed_to_callers(retry):
    assert launcher.validate_context_for_workflow("AppGenerator", {"lifecycle_state": "review"}) == {"lifecycle_state": "review"}
    with pytest.raises(ContextAuthorityError):
        launcher.validate_context_for_workflow("AppGenerator", {"lifecycle_state": "review"}, writer_id=CALLER_INPUT_WRITER)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["genesis", "refinement"])
async def test_retry_unmet_dependency_does_not_persist_a_redirect_or_create_chat(retry, monkeypatch, phase):
    retry.source["run_build_binding"]["phase"] = phase
    if phase == "genesis":
        retry.source["trigger_meta"] = {"trigger_source": "transition", "journey_id": "build"}
    await launch(retry)
    before_chats = deepcopy(retry.pm._default._docs)
    before_state = deepcopy({name: coll._docs for name, coll in retry.pm._named.items()})
    monkeypatch.setattr(SessionRouter, "_find_first_unmet_dependency", FIND_UNMET_DEPENDENCY)
    with pytest.raises(HTTPException) as error:
        await launch(retry)
    assert error.value.status_code == 400
    assert "exact route" in error.value.detail
    assert retry.pm._default._docs == before_chats
    assert {name: coll._docs for name, coll in retry.pm._named.items()} == before_state


@pytest.mark.asyncio
async def test_retry_journey_drift_is_rejected_before_router_state_mutation(retry):
    await launch(retry)
    before_chats = deepcopy(retry.pm._default._docs)
    before_state = deepcopy({name: coll._docs for name, coll in retry.pm._named.items()})
    contribution = await studio._failed_workflow_retry_contribution(
        studio.WorkflowTriggerRequest(workflow_id="AppGenerator", trigger_source="manual", source_chat_id="failed"),
        app_id="host", user_id="owner",
    )
    with pytest.raises(ValueError, match="exact route"):
        await studio.get_session_router().for_target("target").route_trigger(
            TriggerInput(app_id="host", user_id="owner", trigger_source="manual", workflow_id="AppGenerator",
                         journey_id="build"), contribution=contribution,
        )
    assert retry.pm._default._docs == before_chats
    assert {name: coll._docs for name, coll in retry.pm._named.items()} == before_state


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["foreign_owner", "foreign_host", "wrong_workflow", "active_source", "completed_source",
                                   "superseded_build", "wrong_registry", "missing_change", "foreign_change", "missing_baseline",
                                   "foreign_baseline", "retired_baseline", "wrong_baseline_registry", "wrong_baseline_selector",
                                   "caller_context", "caller_trigger_payload", "caller_journey", "caller_action", "caller_artifact",
                                   "missing_baseline_selector", "wrong_change_class", "missing_source", "wrong_trigger",
                                   "full_rebuild", "conceptual_replan", "full_restart", "wrong_request_kind"])
async def test_retry_rejects_invalid_sources_and_caller_overrides_before_launch(retry, fault):
    changes = {}
    if fault == "foreign_owner":
        retry.source["user_id"] = "another_owner"
    elif fault == "foreign_host":
        retry.source["app_id"] = "another_host"
    elif fault == "wrong_workflow":
        retry.source["workflow_name"] = "DesignDocs"
    elif fault == "active_source":
        retry.source["status"] = 0
    elif fault == "completed_source":
        retry.source["status"] = 1
    elif fault == "superseded_build":
        retry.record["current_build_run"]["build_id"] = "newer_build"
    elif fault == "wrong_registry":
        changes["build_registry_id"] = "another_registry"
    elif fault == "missing_change":
        retry.store.get_change_request.return_value = None
    elif fault == "foreign_change":
        retry.change.created_by_user_id = "another_owner"
    elif fault == "missing_baseline":
        retry.store.get_build_record.return_value = None
    elif fault == "foreign_baseline":
        retry.baseline.commit_metadata.author_user_id = "another_owner"
    elif fault == "retired_baseline":
        retry.baseline.lifecycle_status = "archived"
    elif fault == "wrong_baseline_registry":
        retry.baseline.commit_metadata.metadata["build_registry_id"] = "other"
    elif fault == "wrong_baseline_selector":
        retry.source["trigger_meta"]["build_record_id"] = "other"
    elif fault == "caller_context":
        changes["context_variables"] = {"build_mode": "revision", "app_validation_status": "passed"}
    elif fault == "caller_trigger_payload":
        changes["trigger_payload"] = {"refinement_request": {"raw_user_request": "different intent"}}
    elif fault == "caller_journey":
        changes["journey_id"] = "app_revision"
    elif fault == "caller_action":
        changes["action_id"] = "other"
    elif fault == "caller_artifact":
        changes["artifact_key"] = "other"
    elif fault == "missing_baseline_selector":
        retry.change.refinement_request.build_record_id = None
        retry.change.build_record_id = None
        retry.source["trigger_meta"].pop("build_record_id")
    elif fault == "wrong_change_class":
        retry.source["trigger_meta"]["change_class"] = "core"
    elif fault == "missing_source":
        changes["source_chat_id"] = None
    elif fault == "wrong_trigger":
        changes["trigger_source"] = "refinement"
    elif fault in {"full_rebuild", "conceptual_replan"}:
        retry.source["trigger_meta"].update(workflow_sequence=fault, journey_id=fault)
        retry.change.impact_set.workflow_sequence = fault
    elif fault == "full_restart":
        retry.change.router_decision["is_full_restart"] = True
    elif fault == "wrong_request_kind":
        retry.change.refinement_request.request_kind = "restart"
    before = deepcopy(retry.pm._default._docs)
    with pytest.raises(HTTPException) as error:
        await launch(retry, **changes)
    assert error.value.status_code in {400, 403, 404}
    assert retry.pm._default._docs == before
