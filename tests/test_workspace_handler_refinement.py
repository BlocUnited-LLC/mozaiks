"""The app-owned Messaging handler changes only through an admitted backend refinement.

``modules/messages/backend/handler.py`` is ``owner: workspace`` in the pack
contract. Regeneration, UI refinement, raw worker output and AppGenerator plan
tasks keep the verified copy. An explicit or user-confirmed selected-file patch
from the build the registry serves may change it, as a thin extension of the
template base handler, leaving every pack-owned file and the Messaging
authorization boundary as they were.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import zipfile
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.app_validation import (
    validate_module_implementation_contract,
)
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from mozaiksai.control_plane.config import ControlPlaneConfig
from mozaiksai.control_plane.contracts import (
    ContractSurfacePlan,
    ProposedFileChange,
    ScopeProposal,
    StagedPatchProposal,
    SurfacePlanExecutionResult,
)
from mozaiksai.control_plane.implementations import orchestration_control
from mozaiksai.control_plane.implementations.coding_worker import ScopedRefinementCodingWorker
from mozaiksai.control_plane.implementations.refinement_router import (
    ChangeIntent,
    ImpactSet,
    RefinementRequest,
    RefinementRoutingDecision,
    RefinementTriggerRouteResolver,
)
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.context.adapter import create_context_container
from tests.test_app_ui_composition import component, compose
from tests.test_appgenerator_revision_baseline import _replace_archive
from tests.test_appgenerator_revision_baseline import baseline as revision_baseline  # noqa: F401
from tests.test_messaging_pack_persistence_composition import PACK, _assembled_files, _context

HANDLER = "modules/messages/backend/handler.py"
POLICY = "modules/messages/backend/policy.py"
PAGE = "ui/pages/custom/board.jsx"
PROVENANCE = ".mozaiks/pack_provenance.json"
APP_ID = "customer-app"
REQUEST = "Collapse repeated spaces in sent messages."
TEMPLATE_HANDLER = (PACK / "templates" / HANDLER).read_text(encoding="utf-8")
NORMALIZING_HANDLER = TEMPLATE_HANDLER + '''
    async def send_message(self, ctx, *, thread_id, body, message_type="text", **_):
        # Collapse whitespace runs before the template service validates and stores the body.
        return await super().send_message(
            ctx, thread_id=thread_id, body=" ".join(body.split()), message_type=message_type,
        )
'''
# Passing a privileged service flag would let a non-participant post.
PRIVILEGED_HANDLER = TEMPLATE_HANDLER + '''
    async def send_message(self, ctx, *, thread_id, body, message_type="text", **_):
        return await self._service.send_message(
            ctx, thread_id=thread_id, body=body, message_type=message_type, allow_nonparticipant_sender=True,
        )
'''
BINDING = RunBuildBinding(build_registry_id="registry_1", target_app_id=APP_ID, build_id="refine_1", phase="refinement")


def _sha(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _files(result):
    return {file["filename"]: file["content"] for file in result.get("code_files") or []}


def _packs():
    return _context()["capability_packs"]


def _baseline(handler=TEMPLATE_HANDLER, *, app_id=APP_ID):
    board, pack = component(path=PAGE), _assembled_files()
    files = {**board, **pack, **compose(board, pack),
             "app.json": json.dumps({"appId": app_id, "appName": "Messaging", "authRequired": True})}
    files[HANDLER] = handler
    return files


def _template_owned(files):
    """Template-owned pack files; provenance carries its build time, so it is compared separately."""
    from mozaiksai.control_plane.implementations.coding_worker import pack_output_owners

    return {
        path: files[path] for path, owner in pack_output_owners(_packs()).items()
        if owner == "templates" and path != PROVENANCE
    }


def _archive_files(record):
    metadata = record.commit_metadata.metadata
    prefix = metadata["bundle_name"] + "/"
    with zipfile.ZipFile(metadata["artifact_path"]) as archive:
        return {name.removeprefix(prefix): archive.read(name).decode("utf-8") for name in archive.namelist()}


class _Store:
    """The saved parent and every candidate the finalizer persists."""

    def __init__(self, *, app_id=APP_ID, metadata=None):
        self.app_id = app_id
        self.records = {"parent": BuildRecord(
            id="parent", app_id=app_id, build_family="app_bundle", build_key="app_bundle",
            version_number=1, lineage_root_id="parent",
            commit_metadata={"author_user_id": "owner", "metadata": {
                "capability_packs": deepcopy(_packs()), **(metadata or BINDING.model_dump()),
            }},
        )}
        self.created = []

    async def get_build_record(self, *, app_id, build_record_id):
        record = self.records.get(build_record_id)
        return record if record is not None and record.app_id == app_id else None

    async def create_build_record(self, **kwargs):
        self.created.append(kwargs)
        record = BuildRecord(
            id=f"child_{len(self.created)}", version_number=len(self.created) + 1, lineage_root_id="parent", **kwargs,
        )
        self.records[record.id] = record
        return record


def _evidence():
    return {
        "validation_status": "passed", "validation_strategy": "docker", "errors": [],
        "app_bundle_acceptance_result": {"passed": True, "status": "passed", "validation_evidence": {"completed": ["app_runtime_smoke"]}},
        "app_validation_result": {"validation_status": "passed", "validation_strategy": "docker"},
    }


def _config():
    return ControlPlaneConfig(
        enabled=True, llm_profiles={"codegen": {"llm_config": {"model": "test-model"}}},
        coding={"enabled": True, "llm_profile": "codegen"},
    )


def _proposal(changes):
    return StagedPatchProposal(
        proposal_id="proposal-1", provider_id="scripted", status="completed", summary=REQUEST,
        rationale="Owner-requested backend extension", owned_paths=sorted(changes),
        changed_files=[ProposedFileChange(path=path, content=content) for path, content in changes.items()],
    )


def _harness(store, tmp_path, changes, **options):
    validate = AsyncMock(return_value=_evidence())
    worker = ScopedRefinementCodingWorker(
        provider=SimpleNamespace(execute=AsyncMock(return_value=_proposal(changes))), config_loader=_config,
        candidate_validation_runner=validate, artifact_store=store, output_root=tmp_path / "refinements",
    )
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config, **options)
    return harness, validate


def _refinement(**extra):
    return RefinementRequest(
        app_id="studio", target_app_id=APP_ID, user_id="owner", build_family="app_bundle",
        build_record_id="parent", raw_user_request=REQUEST, extra=extra,
    )


def _routing(refinement, change_class="patch"):
    return RefinementRoutingDecision(
        workflow_id="AppGenerator", refinement_request=refinement, impact_set=ImpactSet(),
        change_intent=ChangeIntent(change_class=change_class, rationale=REQUEST),
    )


async def _prepared(harness, baseline, paths, *, registry="parent", extra=None, **options):
    """Mirror Studio: resolve the scope, then bind the run, baseline and registry read."""
    refinement = _refinement(**(extra or {}))
    request = harness.build_coding_request(
        refinement_request=refinement, routing_decision=_routing(refinement),
        payload={"files": {path: baseline[path] for path in paths}},
    )
    prepared, decision = await harness.prepare_coding_request(request, **options)
    if prepared is None:
        return None, decision
    metadata = {**prepared.metadata, **({"registry_artifact_version_id": registry} if registry else {})}
    return prepared.model_copy(update={"run_build_binding": BINDING, "baseline_files": baseline, "metadata": metadata}), decision


@pytest.fixture(autouse=True)
def _quiet_tracking(monkeypatch):
    monkeypatch.setattr(orchestration_control, "record_refinement_event", AsyncMock())


async def _backend_child(tmp_path, *, extra=None):
    baseline, store = _baseline(), _Store()
    harness, validate = _harness(store, tmp_path, {HANDLER: NORMALIZING_HANDLER})
    request, _ = await _prepared(harness, baseline, [HANDLER], extra=extra)
    result = await harness.execute_coding_request(request)
    assert result.status == "validated", result.error
    child = store.records[result.metadata["build_record_id"]]
    return baseline, store, validate, child, _archive_files(child)


@pytest.mark.asyncio
async def test_explicit_backend_patch_changes_only_the_workspace_handler(tmp_path):
    baseline, store, validate, child, child_files = await _backend_child(tmp_path)
    metadata = child.commit_metadata.metadata
    assert child.parent_build_record_id == "parent"
    assert metadata["applied_paths"] == [HANDLER]
    assert metadata["refinement_scope"] == {
        "selected_file_paths": [HANDLER], "scope_origin": "explicit", "changed_workspace_pack_paths": [HANDLER],
    }
    assert set(child_files) == set(baseline)
    assert {path for path in baseline if child_files[path] != baseline[path]} == {HANDLER}
    assert child_files[HANDLER] == NORMALIZING_HANDLER
    # Validation ran the complete candidate under the parent's own pack contracts.
    assert validate.await_args.kwargs["files"][HANDLER] == NORMALIZING_HANDLER
    assert validate.await_args.kwargs["capability_packs"] == _packs()
    # Every pack-owned file stays the parent's and the pinned template's bytes.
    assert _template_owned(child_files) == _template_owned(baseline) == _template_owned(_assembled_files())
    # A new app revision; the pack's source, version and digest are unchanged.
    assert metadata["capability_packs"] == store.records["parent"].commit_metadata.metadata["capability_packs"]
    assert child_files[PROVENANCE] == baseline[PROVENANCE]
    assert metadata["staged_file_sha256"][HANDLER] == _sha(NORMALIZING_HANDLER) != _sha(baseline[HANDLER])


def _rejected_case(case, baseline):
    drifted = baseline[POLICY] + "\n# drift\n"
    helper = TEMPLATE_HANDLER + "\n    async def archive_thread(self, ctx, *, thread_id):\n        return {'success': True}\n"
    persistence = TEMPLATE_HANDLER + '''
    async def send_message(self, ctx, *, thread_id, body, message_type="text", **_):
        await ctx.persistence.collection("messages", "messages").insert_one({"body": body})
        return await super().send_message(ctx, thread_id=thread_id, body=body, message_type=message_type)
'''
    return {
        "privileged_override": ({HANDLER: PRIVILEGED_HANDLER}, {}, "WORKSPACE_HANDLER_EXTENSION_REJECTED"),
        "undeclared_helper": ({HANDLER: helper}, {}, "WORKSPACE_HANDLER_EXTENSION_REJECTED"),
        "direct_persistence": ({HANDLER: persistence}, {}, "WORKSPACE_HANDLER_EXTENSION_REJECTED"),
        "template_drift": ({POLICY: drifted}, {}, "PACK_TEMPLATE_OUTPUT_CHANGED"),
        "handler_with_template_drift": ({HANDLER: NORMALIZING_HANDLER, POLICY: drifted}, {}, "PACK_TEMPLATE_OUTPUT_CHANGED"),
        "stale_parent": ({HANDLER: NORMALIZING_HANDLER}, {"registry": "newer_build"}, "STALE_REFINEMENT_BASELINE"),
        "registry_unread": ({HANDLER: NORMALIZING_HANDLER}, {"registry": None}, "STALE_REFINEMENT_BASELINE"),
    }[case]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "privileged_override", "undeclared_helper", "direct_persistence", "template_drift",
    "handler_with_template_drift", "stale_parent", "registry_unread",
])
async def test_explicit_patch_outside_the_extension_contract_is_rejected_without_a_child(tmp_path, case):
    baseline, store = _baseline(), _Store()
    changes, options, code = _rejected_case(case, baseline)
    harness, validate = _harness(store, tmp_path, changes)
    request, _ = await _prepared(harness, baseline, list(changes), **options)
    result = await harness.execute_coding_request(request)
    assert result.status == "failed" and result.error.startswith(code)
    assert result.metadata["candidate_admission"]["code"] == code
    validate.assert_not_awaited()
    assert store.created == []


@pytest.mark.asyncio
async def test_unrecorded_scope_origin_cannot_change_the_workspace_handler(tmp_path):
    baseline, store = _baseline(), _Store()
    harness, validate = _harness(store, tmp_path, {HANDLER: NORMALIZING_HANDLER})
    refinement = _refinement()
    # Built but never resolved through the harness: nothing records who chose the scope.
    request = harness.build_coding_request(
        refinement_request=refinement, routing_decision=_routing(refinement), payload={"files": {HANDLER: baseline[HANDLER]}},
    ).model_copy(update={"run_build_binding": BINDING, "baseline_files": baseline})
    request.metadata["registry_artifact_version_id"] = "parent"
    result = await harness.execute_coding_request(request)
    assert result.metadata["candidate_admission"]["code"] == "WORKSPACE_PACK_OUTPUT_UNAUTHORIZED"
    assert result.metadata["candidate_admission"]["scope_origin"] == "unspecified"
    validate.assert_not_awaited()
    assert store.created == []


@pytest.mark.asyncio
async def test_inferred_scope_waits_for_confirmation_before_changing_the_handler(tmp_path):
    baseline, store = _baseline(), _Store()
    proposer = SimpleNamespace(
        propose=AsyncMock(return_value=ScopeProposal(
            resolution="scoped_files", selected_paths=[HANDLER], rationale="The send action lives here.",
            confidence=0.95, clarification_question=None, signals=["messages"],
        )),
        materialize_files=AsyncMock(return_value={HANDLER: baseline[HANDLER]}),
    )
    harness, validate = _harness(store, tmp_path, {HANDLER: NORMALIZING_HANDLER}, scope_proposer=proposer)
    refinement = _refinement()
    inferred = harness.build_coding_request(refinement_request=refinement, routing_decision=_routing(refinement), payload={})

    # Studio passes the saved parent's app-owned pack paths: the proposal is not applied.
    prepared, decision = await harness.prepare_coding_request(inferred, confirmation_required_paths=frozenset({HANDLER}))
    assert prepared is None
    assert decision.decision_type == "clarify_scope" and decision.selected_paths == [HANDLER]
    assert [action.action_id for action in decision.actions] == ["apply_proposed_scope", "clarify_scope"]
    assert decision.metadata["confirmation_required_paths"] == [HANDLER]

    # A caller that skipped that routing still cannot apply an inferred scope.
    prepared, decision = await harness.prepare_coding_request(inferred)
    assert decision.decision_type == "auto_patch" and prepared.metadata["scope_origin"] == "auto_proposal"
    result = await harness.execute_coding_request(prepared.model_copy(update={
        "run_build_binding": BINDING, "baseline_files": baseline,
        "metadata": {**prepared.metadata, "registry_artifact_version_id": "parent"},
    }))
    assert result.metadata["candidate_admission"]["code"] == "WORKSPACE_PACK_OUTPUT_UNAUTHORIZED"
    assert store.created == []
    validate.assert_not_awaited()

    # Confirming the pending scope (Studio sets harness_action only after
    # resolving that decision) is the authorized path.
    confirmed, decision = await _prepared(
        harness, baseline, [HANDLER], extra={"harness_action": {"action_id": "apply_proposed_scope"}},
        confirmation_required_paths=frozenset({HANDLER}),
    )
    assert decision.decision_type == "auto_patch"
    result = await harness.execute_coding_request(confirmed)
    assert result.status == "validated", result.error
    child = store.records[result.metadata["build_record_id"]]
    assert child.commit_metadata.metadata["refinement_scope"]["scope_origin"] == "confirmed"
    assert _archive_files(child)[HANDLER] == NORMALIZING_HANDLER


_SEND = '    async def send_message(self, ctx, *, thread_id, body, message_type="text", **_):\n'


@pytest.mark.parametrize(("handler", "reason"), [
    (TEMPLATE_HANDLER + _SEND + "        self._service = None\n        return {}\n", "self._service"),
    (TEMPLATE_HANDLER + _SEND + "        return {'actor': ctx.user_id}\n", "ctx.user_id"),
    (TEMPLATE_HANDLER + _SEND + "        return await super().send_message(dict(), thread_id=thread_id, body=body)\n",
     "only positional service argument"),
    (TEMPLATE_HANDLER + _SEND + "        return await super().send_message(ctx, **{'thread_id': thread_id})\n", "unpacks"),
    (TEMPLATE_HANDLER + _SEND + "        return await super().create_thread(ctx, title=body)\n",
     "outside a permitted service call"),
    (TEMPLATE_HANDLER + _SEND.replace("async def", "def") + "        return {}\n", "sync/async form"),
    (TEMPLATE_HANDLER + _SEND.replace(", **_", "") + "        return {}\n", "exact parameters"),
    (TEMPLATE_HANDLER + _SEND + "        return getattr(super(), 'send_message')\n", "getattr"),
    ("import os\n" + TEMPLATE_HANDLER, "Import"),
])
def test_workspace_handler_extension_grammar_rejects_reaching_past_the_base_handler(handler, reason):
    from mozaiksai.core.semantics.implementation_artifacts import (
        ImplementationArtifactError,
        prove_workspace_handler_extension,
    )

    module = PACK / "templates/modules/messages"
    options = {
        "base_handler_source": (module / "backend/base_handler.py").read_text(encoding="utf-8"),
        "handler_class": "MessagesHandler",
        "action_methods": ["create_thread", "list_threads", "get_thread", "send_message", "mark_thread_read"],
    }
    assert prove_workspace_handler_extension(handler_source=TEMPLATE_HANDLER, **options) == ()
    assert prove_workspace_handler_extension(handler_source=NORMALIZING_HANDLER, **options) == ("send_message",)
    with pytest.raises(ImplementationArtifactError, match=reason):
        prove_workspace_handler_extension(handler_source=handler, **options)


def _surface(kind, target_id, paths):
    return ContractSurfacePlan(
        summary=REQUEST, change_class="feature", build_family="app_bundle",
        surfaces=[{"kind": kind, "target_id": target_id, "target_kind": "module" if kind == "module_action" else "page",
                   "affected_paths": paths, "rationale": REQUEST}],
    )


@pytest.mark.asyncio
async def test_surface_plan_without_a_file_limit_cannot_change_the_handler(tmp_path):
    baseline, store = _baseline(), _Store()
    harness, validate = _harness(store, tmp_path, {})
    refinement = _refinement()
    module_paths = [f"modules/messages/{name}" for name in (
        "module.yaml", "backend/handler.py", "backend/service.py", "backend/schemas.py", "backend/repo.py", "backend/policy.py",
    )]
    result = await harness.finalize_surface_output(
        plan=_surface("module_action", "messages", module_paths),
        result=SurfacePlanExecutionResult(status="success", all_files={HANDLER: NORMALIZING_HANDLER}),
        refinement_request=refinement, routing_decision=_routing(refinement, "feature"),
        workspace_files=baseline, run_build_binding=BINDING, registry_artifact_version_id="parent",
    )
    assert result.status == "failed"
    assert result.metadata["candidate_admission"]["code"] == "WORKSPACE_PACK_OUTPUT_UNAUTHORIZED"
    assert result.metadata["candidate_admission"]["scope_origin"] == "surface_plan"
    validate.assert_not_awaited()
    assert store.created == []


@pytest.mark.asyncio
async def test_later_ui_refinement_keeps_the_accepted_handler(tmp_path):
    _, _, _, backend, backend_files = await _backend_child(tmp_path / "backend")
    store = _Store()
    store.records["parent"].commit_metadata.metadata.update(backend.commit_metadata.metadata)
    harness, validate = _harness(store, tmp_path / "ui", {})
    refinement = _refinement()
    refined = backend_files[PAGE].replace("return null;", "return 'refined board';")
    result = await harness.finalize_surface_output(
        plan=_surface("page_binding", "board", [PAGE]),
        result=SurfacePlanExecutionResult(status="success", all_files={PAGE: refined}),
        refinement_request=refinement, routing_decision=_routing(refinement, "design"),
        workspace_files=backend_files, run_build_binding=BINDING, allowed_paths=[PAGE],
    )
    assert result.status == "validated", result.error
    child = store.records[result.metadata["build_record_id"]]
    child_files = _archive_files(child)
    assert {path for path in backend_files if child_files[path] != backend_files[path]} == {PAGE}
    assert child_files[HANDLER] == NORMALIZING_HANDLER
    assert _template_owned(child_files) == _template_owned(_assembled_files())
    assert child.commit_metadata.metadata["refinement_scope"] == {
        "selected_file_paths": [PAGE], "scope_origin": "explicit", "changed_workspace_pack_paths": [],
    }


def _revision_context(saved, files, **extra):
    return create_context_container(initial={
        **saved.context, **_context(), "data_contract": json.loads(files["data/contract.json"]), **extra,
    })


@pytest.mark.asyncio
async def test_ui_only_appgenerator_revision_preserves_the_customized_handler(request):
    saved = request.getfixturevalue("revision_baseline")
    saved.files = _baseline(NORMALIZING_HANDLER)
    _replace_archive(saved, {})
    revised = component(path=PAGE)
    revised[PAGE] = "export default function View() { return 'refined'; }"
    plan = {**_context()["app_build_plan"], "pages": [{"name": "board", "route": "/board", "ui_surface": "custom_react_page"}],
            "build_tasks": [{"task_id": "pages", "task_type": "page_bundle", "owned_paths": sorted(revised)}]}
    result = await assemble_app_tasks(context_variables=_revision_context(saved, saved.files, app_build_plan=plan, app_task_batch_results={
        "pages": {"code_files": [{"filename": path, "content": content} for path, content in revised.items()]},
    }))
    assert result["success"], result
    files = _files(result)
    assert files[PAGE] == revised[PAGE] and files[HANDLER] == NORMALIZING_HANDLER


@pytest.mark.asyncio
async def test_admitted_handler_survives_regeneration_and_changes_the_send_action(tmp_path, request, monkeypatch, caplog):
    *_, child_files = await _backend_child(tmp_path)
    saved = request.getfixturevalue("revision_baseline")
    saved.files = child_files
    _replace_archive(saved, {})
    # Re-materializing the same pack keeps the admitted handler; a raw worker
    # copy that no current plan task owns is ignored.
    caplog.set_level(logging.WARNING, logger="factory_app.workflows.AppGenerator.tools.assemble_app_tasks")
    result = await assemble_app_tasks(context_variables=_revision_context(saved, child_files, app_task_batch_results={
        "old_worker": {"code_files": [{"filename": HANDLER, "content": TEMPLATE_HANDLER}]},
    }))
    assert result["success"], result
    assert any("WORKSPACE_PACK_OUTPUT_IGNORED" in record.getMessage() and "task:old_worker" in record.getMessage()
               for record in caplog.records)
    files = _files(result)
    assert files[HANDLER] == NORMALIZING_HANDLER
    assert _template_owned(files) == _template_owned(_assembled_files())
    assert validate_module_implementation_contract(files)["passed"] is True

    runtime = _module_runtime(tmp_path / "normalized", monkeypatch, files)
    alice, bob = runtime.token(), runtime.token(user="bob")
    thread = runtime.action(alice, "create_thread", {"participant_ids": ["alice"]}).json()["thread"]
    sent = runtime.action(alice, "send_message", {"thread_id": thread["thread_id"], "body": "hello   there   world"})
    assert sent.status_code == 200 and sent.json()["success"] is True
    assert sent.json()["message"]["body"] == "hello there world"
    assert [row["body"] for row in runtime.rows("messages")] == ["hello there world"]
    # The template service still owns every authorization and validation rule.
    outsider = runtime.action(bob, "send_message", {"thread_id": thread["thread_id"], "body": "let   me   in"})
    assert outsider.status_code == 200 and outsider.json() == {"success": False, "error": "access denied"}
    unpermitted = runtime.action(runtime.token(scopes="messages.read"), "send_message",
                                 {"thread_id": thread["thread_id"], "body": "no permission"})
    assert unpermitted.status_code == 403
    too_long = runtime.action(alice, "send_message", {"thread_id": thread["thread_id"], "body": "x" * 4001})
    assert too_long.json()["success"] is False and "exceeds" in too_long.json()["error"]
    privileged = runtime.action(bob, "send_message", {
        "thread_id": thread["thread_id"], "body": "let me in", "allow_nonparticipant_sender": True,
    })
    assert privileged.status_code in {400, 422}
    assert [row["body"] for row in runtime.rows("messages")] == ["hello there world"]

    # The same request through the template handler stores the spaces: the
    # behavior comes from the admitted extension, not the test runtime.
    template = _module_runtime(tmp_path / "template", monkeypatch, _assembled_files())
    token = template.token()
    thread = template.action(token, "create_thread", {"participant_ids": ["alice"]}).json()["thread"]
    assert template.action(token, "send_message", {
        "thread_id": thread["thread_id"], "body": "hello   there   world",
    }).json()["message"]["body"] == "hello   there   world"


@pytest.mark.asyncio
@pytest.mark.parametrize("planned_content", ["revert", "unchanged"])
async def test_plan_task_owning_the_handler_cannot_change_it(tmp_path, request, planned_content):
    *_, child_files = await _backend_child(tmp_path)
    saved = request.getfixturevalue("revision_baseline")
    saved.files = child_files
    _replace_archive(saved, {})
    content = TEMPLATE_HANDLER if planned_content == "revert" else NORMALIZING_HANDLER
    plan = {**_context()["app_build_plan"], "build_tasks": [
        {"task_id": "svc", "task_type": "business_services", "owned_paths": [HANDLER]},
    ]}
    context = _revision_context(saved, child_files, app_build_plan=plan, app_task_batch_results={
        "svc": {"code_files": [{"filename": HANDLER, "content": content}]},
    })
    result = await assemble_app_tasks(context_variables=context)
    if planned_content == "unchanged":
        assert result["success"], result
        assert _files(result)[HANDLER] == NORMALIZING_HANDLER
        return
    # Never ship the plan's change, and never report success while dropping it.
    assert not result["success"]
    assert result["diagnostic"]["code"] == "WORKSPACE_PACK_OUTPUT_CHANGE_UNSUPPORTED"
    assert result["diagnostic"]["missing_capability"] == "authorized_backend_refinement"
    assert context.get("app_assembly_status") == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("bound_source", [True, False])
async def test_explicit_rebuild_keeps_the_handler_only_through_a_bound_verified_source(request, bound_source):
    saved = request.getfixturevalue("revision_baseline")
    saved.files = _baseline(NORMALIZING_HANDLER)
    _replace_archive(saved, {})
    context = _revision_context(saved, saved.files, workflow_sequence="full_rebuild", generated_files={
        "app.json": saved.files["app.json"], "data/contract.json": saved.files["data/contract.json"],
    })
    if not bound_source:
        context.set("artifact_version_id", None)
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    # A rebuild bound to the app's verified revision keeps its handler; with no
    # such binding there is no app copy to keep, and the starter template is used.
    assert _files(result)[HANDLER] == (NORMALIZING_HANDLER if bound_source else TEMPLATE_HANDLER)
    assert saved.store.get_build_record.await_count == (1 if bound_source else 0)


def _studio_route(monkeypatch, tmp_path, *, registry_current, files_payload, proposer=None):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.core.session.router import SessionRouter
    from mozaiksai.hosts import studio
    from tests.test_session_router import _FakePersistence
    from tests.test_studio_workflow_trigger import _artifact_version, _async_classifier

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    baseline = _baseline(app_id="app_1")
    archive = tmp_path / "parent.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path, content in baseline.items():
            zipped.writestr(path, content)
    store = _Store(app_id="app_1", metadata={})
    parent = _artifact_version(artifact_version_id="parent", zip_path=archive)
    parent.commit_metadata.metadata["capability_packs"] = deepcopy(_packs())
    store.records["parent"] = parent

    async def accept(**kwargs):
        return SimpleNamespace(id="cr_1")

    store.create_change_request = accept
    store.invalidate_artifact_version_refs = AsyncMock(return_value=[])
    store.create_refinement_session = AsyncMock(return_value=SimpleNamespace(id="rs_1"))
    record = {"build_registry_id": "appreg_1", "app_id": "app_1", "chat_app_id": "factory", "lifecycle_state": "review",
              "current_build_run": {"build_id": "build_0", "artifact_version_id": registry_current}}
    binding = RunBuildBinding(build_registry_id="appreg_1", target_app_id="app_1", build_id="build_1", phase="refinement")
    registry = SimpleNamespace(
        get_app_record=AsyncMock(return_value={"app": record}), begin_refinement_run=AsyncMock(return_value=binding),
        update_build_status=AsyncMock(return_value={"success": True, "app": record}),
    )
    validate = AsyncMock(return_value=_evidence())
    worker = ScopedRefinementCodingWorker(
        provider=SimpleNamespace(execute=AsyncMock(return_value=_proposal({HANDLER: NORMALIZING_HANDLER}))),
        config_loader=_config, candidate_validation_runner=validate, artifact_store=store,
        output_root=tmp_path / "refinements",
    )
    harness = orchestration_control.OrchestrationControlHarness(
        refinement_resolver=RefinementTriggerRouteResolver(classifier=SimpleNamespace(classify=_async_classifier(
            change_class="patch", rationale=REQUEST, confidence=0.95, signals=["messages"],
        ))),
        coding_worker=worker, config_loader=_config, **({"scope_proposer": proposer} if proposer else {}),
    )
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: registry)
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *args, **kwargs: ("factory", "demo-user"))
    monkeypatch.setattr(studio, "get_session_router", lambda: SessionRouter(persistence=_FakePersistence()))
    monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
    monkeypatch.setattr(studio, "get_orchestration_control_harness", lambda: harness)
    response = TestClient(studio.app).post("/api/workflows/trigger", json={
        "build_registry_id": "appreg_1", "trigger_source": "refinement",
        "trigger_payload": {
            "refinement_request": {"build_family": "app_bundle", "build_key": "app_bundle", "build_record_id": "parent",
                                   "raw_user_request": REQUEST, "source_surface": "app_workbench"},
            "coding_request": files_payload,
        },
    })
    return SimpleNamespace(response=response, store=store, registry=registry, validate=validate)


@pytest.mark.parametrize("registry_current", ["parent", "newer_build"])
def test_studio_backend_patch_requires_the_current_build(monkeypatch, tmp_path, registry_current):
    route = _studio_route(monkeypatch, tmp_path, registry_current=registry_current, files_payload={"files": {HANDLER: ""}})
    assert route.response.status_code == 200, route.response.text
    body = route.response.json()
    assert body["execution_mode"] == "coding_worker"
    status = route.registry.update_build_status.await_args.kwargs
    if registry_current == "parent":
        assert body["coding_worker"]["status"] == "validated"
        child = route.store.records[body["coding_worker"]["metadata"]["build_record_id"]]
        assert child.commit_metadata.metadata["refinement_scope"]["scope_origin"] == "explicit"
        assert _archive_files(child)[HANDLER] == NORMALIZING_HANDLER
        assert status["status"] == "review" and status["artifact_version_id"] == child.id
        return
    assert body["coding_worker"]["status"] == "failed"
    assert body["coding_worker"]["metadata"]["candidate_admission"]["code"] == "STALE_REFINEMENT_BASELINE"
    assert body["coding_worker"]["metadata"]["candidate_admission"]["registry_artifact_version_id"] == "newer_build"
    route.validate.assert_not_awaited()
    assert route.store.created == [] and status["status"] == "needs_revision"


def test_studio_inferred_handler_scope_returns_the_confirmation_decision(monkeypatch, tmp_path):
    proposer = SimpleNamespace(
        propose=AsyncMock(return_value=ScopeProposal(
            resolution="scoped_files", selected_paths=[HANDLER], rationale="The send action lives here.",
            confidence=0.95, clarification_question=None, signals=["messages"],
        )),
        materialize_files=AsyncMock(side_effect=lambda **kwargs: {HANDLER: TEMPLATE_HANDLER}),
    )
    route = _studio_route(monkeypatch, tmp_path, registry_current="parent", files_payload={}, proposer=proposer)
    assert route.response.status_code == 200, route.response.text
    body = route.response.json()
    assert body["execution_mode"] == "harness_decision"
    decision = body["harness_decision"]
    assert decision["decision_type"] == "clarify_scope" and decision["selected_paths"] == [HANDLER]
    assert decision["metadata"]["confirmation_required_paths"] == [HANDLER]
    assert "apply_proposed_scope" in [action["action_id"] for action in decision["actions"]]
    route.registry.begin_refinement_run.assert_not_awaited()
    assert route.store.created == []


def _module_runtime(app_root, monkeypatch, files):
    """Signed HTTP dispatch through the real module router, loader and executor."""
    from mozaiksai.core.auth.adapters import registry as auth_registry
    from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
    from mozaiksai.core.auth.config import clear_auth_config_cache
    from mozaiksai.core.runtime.app.module_loader import ModuleLoader
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
    from mozaiksai.hosts.routers import modules as module_router
    from tests.test_generated_messaging_scope_http import _Collection

    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in {"ENV": "test", "ENVIRONMENT": "test", "AUTH_ENABLED": "true", "AUTH_PROVIDER": "jwt",
                        "AUTH_AUDIENCE": "handler-refinement-test"}.items():
        monkeypatch.setenv(name, value)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "handler-refinement-test", "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience="handler-refinement-test",
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **_kwargs: None)
    hooks = PlatformHookRegistry()
    monkeypatch.setattr(module_router, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())
    for path, content in files.items():
        if path.startswith("modules/messages/"):
            target = app_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    loaded = ModuleLoader(str(app_root)).load("messages")
    database = defaultdict(lambda: defaultdict(_Collection))
    executor = ModuleExecutor(persistence_client=database, persistence_database="handler_refinement_test",
                              data_contract=json.loads(files["data/contract.json"]))
    executor.register_loaded_module(loaded)
    app = FastAPI()
    app.state.loaded_app_id = "handler-refinement-app"
    app.state.module_action_surfaces = {"messages": loaded.action_api_surface_map}
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.include_router(module_router.router)
    client = TestClient(app, raise_server_exceptions=False)

    def token(*, user="alice", scopes="messages.read messages.write"):
        claims = {"sub": user, "iss": "https://auth.test", "aud": "handler-refinement-test",
                  "app_id": "handler-refinement-app", "scp": scopes, "exp": int(time.time()) + 300}
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "handler-refinement-test", "typ": "at+jwt"},
        )}

    def rows(suffix):
        return [row for name, collection in database["handler_refinement_test"].items()
                if name.endswith(suffix) for row in collection.rows]

    return SimpleNamespace(
        token=token, rows=rows,
        action=lambda headers, action, params: client.post(
            f"/api/modules/messages/{action}", json={"params": params}, headers=headers,
        ),
    )
