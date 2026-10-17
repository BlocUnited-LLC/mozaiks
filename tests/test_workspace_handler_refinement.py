"""The app-owned Messaging handler changes only through an admitted backend refinement.

``modules/messages/backend/handler.py`` is ``owner: workspace`` in the pack
contract. Regeneration, UI refinement, raw worker output and AppGenerator plan
tasks keep the verified copy. An explicit or user-confirmed selected-file patch
from the app's accepted current build may change it, as a thin extension of the
template base handler, leaving every pack-owned file and the Messaging
authorization boundary as they were.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
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
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    admitted_app_file_map,
    save_generated_code,
)
from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
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
from mozaiksai.core.artifacts import ArtifactLifecycleStatus
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
_FUTURE = "from __future__ import annotations\n\n"
# A literal the template input contract (module.yaml thread_type enum [group, direct]) never admits.
FORGED_SUPPORT_HANDLER = TEMPLATE_HANDLER + '''
    async def create_thread(self, ctx, *, title=None, participant_ids=None, thread_type="group", scope_type="app",
                            scope_id=None, subject_app_id=None, related_type=None, related_id=None, metadata=None, **_):
        return await self._service.create_thread(
            ctx, title=title, participant_ids=participant_ids, thread_type="support", scope_type=scope_type,
            scope_id=scope_id, subject_app_id=subject_app_id, related_type="support.request", related_id=related_id,
            metadata=metadata,
        )
'''


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
            version_number=1, lineage_root_id="parent", lifecycle_status=ArtifactLifecycleStatus.CURRENT,
            commit_metadata={"author_user_id": "owner", "metadata": {
                "capability_packs": deepcopy(_packs()), **(metadata or BINDING.model_dump()),
            }},
        )}
        self.created = []

    async def get_build_record(self, *, app_id, build_record_id):
        record = self.records.get(build_record_id)
        return record if record is not None and record.app_id == app_id else None

    async def get_current_build_record_refs(self, *, app_id):
        """The accepted current record per build family, as the artifact store reports it."""
        return {
            record.build_family: record.id for record in self.records.values()
            if record.app_id == app_id and record.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        }

    def supersede_parent_with(self, record_id):
        """Another accepted build replaced the parent as the app's current build."""
        self.records["parent"] = self.records["parent"].model_copy(
            update={"lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED},
        )
        self.records[record_id] = self.records["parent"].model_copy(
            update={"id": record_id, "version_number": 2, "lifecycle_status": ArtifactLifecycleStatus.CURRENT},
        )

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


async def _prepared(harness, baseline, paths, *, extra=None, **options):
    """Mirror Studio: resolve the scope, then bind the run and baseline."""
    refinement = _refinement(**(extra or {}))
    request = harness.build_coding_request(
        refinement_request=refinement, routing_decision=_routing(refinement),
        payload={"files": {path: baseline[path] for path in paths}},
    )
    prepared, decision = await harness.prepare_coding_request(request, **options)
    if prepared is None:
        return None, decision
    return prepared.model_copy(update={"run_build_binding": BINDING, "baseline_files": baseline}), decision


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
    # Without the future import this annotation is evaluated when the class body runs.
    annotation_side_effect = TEMPLATE_HANDLER.replace(_FUTURE, "") + '''
    async def send_message(self, ctx: open("pwned.txt", "w").write("ran at import"), *, thread_id, body, message_type="text", **_):
        return await super().send_message(ctx, thread_id=thread_id, body=body, message_type=message_type)
'''
    return {
        "privileged_override": ({HANDLER: PRIVILEGED_HANDLER}, None, "WORKSPACE_HANDLER_EXTENSION_REJECTED"),
        "undeclared_helper": ({HANDLER: helper}, None, "WORKSPACE_HANDLER_EXTENSION_REJECTED"),
        "direct_persistence": ({HANDLER: persistence}, None, "WORKSPACE_HANDLER_EXTENSION_REJECTED"),
        "annotation_side_effect": ({HANDLER: annotation_side_effect}, None, "WORKSPACE_HANDLER_EXTENSION_REJECTED"),
        "forged_service_value": ({HANDLER: FORGED_SUPPORT_HANDLER}, None, "WORKSPACE_HANDLER_EXTENSION_REJECTED"),
        "template_drift": ({POLICY: drifted}, None, "PACK_TEMPLATE_OUTPUT_CHANGED"),
        "handler_with_template_drift": ({HANDLER: NORMALIZING_HANDLER, POLICY: drifted}, None, "PACK_TEMPLATE_OUTPUT_CHANGED"),
        "stale_parent": ({HANDLER: NORMALIZING_HANDLER}, "newer_build", "STALE_REFINEMENT_BASELINE"),
        "parent_never_accepted": ({HANDLER: NORMALIZING_HANDLER}, "", "STALE_REFINEMENT_BASELINE"),
    }[case]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "privileged_override", "undeclared_helper", "direct_persistence", "annotation_side_effect",
    "forged_service_value", "template_drift", "handler_with_template_drift", "stale_parent", "parent_never_accepted",
])
async def test_explicit_patch_outside_the_extension_contract_is_rejected_without_a_child(tmp_path, case):
    baseline, store = _baseline(), _Store()
    changes, accepted, code = _rejected_case(case, baseline)
    if accepted:
        store.supersede_parent_with(accepted)
    elif accepted == "":
        store.records["parent"] = store.records["parent"].model_copy(
            update={"lifecycle_status": ArtifactLifecycleStatus.DRAFT},
        )
    harness, validate = _harness(store, tmp_path, changes)
    request, _ = await _prepared(harness, baseline, list(changes))
    result = await harness.execute_coding_request(request)
    assert result.status == "failed" and result.error.startswith(code)
    assert result.metadata["candidate_admission"]["code"] == code
    if code == "STALE_REFINEMENT_BASELINE":
        assert result.metadata["candidate_admission"]["accepted_build_record_id"] == (accepted or None)
    validate.assert_not_awaited()
    assert [record for record in store.records if record.startswith("child_")] == []


@pytest.mark.asyncio
async def test_unrecorded_scope_origin_cannot_change_the_workspace_handler(tmp_path):
    baseline, store = _baseline(), _Store()
    harness, validate = _harness(store, tmp_path, {HANDLER: NORMALIZING_HANDLER})
    refinement = _refinement()
    # Built but never resolved through the harness: nothing records who chose the scope.
    request = harness.build_coding_request(
        refinement_request=refinement, routing_decision=_routing(refinement), payload={"files": {HANDLER: baseline[HANDLER]}},
    ).model_copy(update={"run_build_binding": BINDING, "baseline_files": baseline})
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
        workspace_files=baseline, run_build_binding=BINDING,
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


def _studio_route(monkeypatch, tmp_path, *, files_payload, proposer=None, accepted="parent", registry_current="parent"):
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
    parent = _artifact_version(
        artifact_version_id="parent", zip_path=archive, lifecycle_status=ArtifactLifecycleStatus.CURRENT,
    )
    parent.commit_metadata.metadata["capability_packs"] = deepcopy(_packs())
    store.records["parent"] = parent
    if accepted != "parent":
        store.supersede_parent_with(accepted)

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


@pytest.mark.parametrize("accepted", ["parent", "newer_build"])
def test_studio_backend_patch_requires_the_current_build(monkeypatch, tmp_path, accepted):
    route = _studio_route(monkeypatch, tmp_path, accepted=accepted, files_payload={"files": {HANDLER: ""}})
    assert route.response.status_code == 200, route.response.text
    body = route.response.json()
    assert body["execution_mode"] == "coding_worker"
    status = route.registry.update_build_status.await_args.kwargs
    if accepted == "parent":
        assert body["coding_worker"]["status"] == "validated"
        child = route.store.records[body["coding_worker"]["metadata"]["build_record_id"]]
        assert child.commit_metadata.metadata["refinement_scope"]["scope_origin"] == "explicit"
        assert _archive_files(child)[HANDLER] == NORMALIZING_HANDLER
        assert status["status"] == "review" and status["artifact_version_id"] == child.id
        return
    assert body["coding_worker"]["status"] == "failed"
    assert body["coding_worker"]["metadata"]["candidate_admission"]["code"] == "STALE_REFINEMENT_BASELINE"
    assert body["coding_worker"]["metadata"]["candidate_admission"]["accepted_build_record_id"] == "newer_build"
    route.validate.assert_not_awaited()
    assert route.store.created == [] and status["status"] == "needs_revision"


def test_failed_inline_run_does_not_block_a_later_authorized_handler_patch(monkeypatch, tmp_path):
    # A failed inline run leaves the registry's current run without an artifact;
    # the accepted build is still the parent, so an authorized retry proceeds.
    route = _studio_route(monkeypatch, tmp_path, registry_current=None, files_payload={"files": {HANDLER: ""}})
    assert route.response.status_code == 200, route.response.text
    body = route.response.json()
    assert body["coding_worker"]["status"] == "validated", body["coding_worker"]
    child = route.store.records[body["coding_worker"]["metadata"]["build_record_id"]]
    assert _archive_files(child)[HANDLER] == NORMALIZING_HANDLER


def test_studio_inferred_handler_scope_returns_the_confirmation_decision(monkeypatch, tmp_path):
    proposer = SimpleNamespace(
        propose=AsyncMock(return_value=ScopeProposal(
            resolution="scoped_files", selected_paths=[HANDLER], rationale="The send action lives here.",
            confidence=0.95, clarification_question=None, signals=["messages"],
        )),
        materialize_files=AsyncMock(side_effect=lambda **kwargs: {HANDLER: TEMPLATE_HANDLER}),
    )
    route = _studio_route(monkeypatch, tmp_path, files_payload={}, proposer=proposer)
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


def _grammar_options(**extra):
    module = PACK / "templates/modules/messages"
    return {
        "base_handler_source": (module / "backend/base_handler.py").read_text(encoding="utf-8"),
        "handler_class": "MessagesHandler",
        "action_methods": ["create_thread", "list_threads", "get_thread", "send_message", "mark_thread_read"],
        **extra,
    }


_PASS_THROUGH = "        return await super().send_message(ctx, thread_id=thread_id, body=body, message_type=message_type)\n"


@pytest.mark.parametrize("variant", ["parameter", "keyword_parameter", "return", "string_parameter"])
def test_workspace_handler_annotations_cannot_run_code_when_the_handler_loads(tmp_path, monkeypatch, variant):
    import contextlib
    import importlib
    import typing

    from mozaiksai.core.semantics.implementation_artifacts import (
        ImplementationArtifactError,
        prove_workspace_handler_extension,
    )

    marker = tmp_path / "ran.txt"
    effect = f'open({marker.as_posix()!r}, "w").write("ran")'
    signature = {
        "parameter": f'(self, ctx: {effect}, *, thread_id, body, message_type="text", **_)',
        "keyword_parameter": f'(self, ctx, *, thread_id: {effect}, body, message_type="text", **_)',
        "return": f'(self, ctx, *, thread_id, body, message_type="text", **_) -> {effect}',
        "string_parameter": f'(self, ctx: {effect!r}, *, thread_id, body, message_type="text", **_)',
    }[variant]
    # Without the future import an annotation runs when the class body runs; a
    # string annotation runs when anything resolves the method's type hints.
    template = TEMPLATE_HANDLER if variant == "string_parameter" else TEMPLATE_HANDLER.replace(_FUTURE, "")
    source = template + f"    async def send_message{signature}:\n" + _PASS_THROUGH
    package = tmp_path / f"annotated_{variant}"
    (package / "backend").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "backend" / "__init__.py").write_text("")
    (package / "backend" / "base_handler.py").write_text("class MessagesBaseHandler:\n    pass\n")
    (package / "backend" / "handler.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module(f"annotated_{variant}.backend.handler")
    with contextlib.suppress(TypeError, NameError):
        typing.get_type_hints(module.MessagesHandler.send_message)
    assert marker.read_text() == "ran"  # the side effect is real once the handler loads
    with pytest.raises(ImplementationArtifactError, match="annotation"):
        prove_workspace_handler_extension(handler_source=source, **_grammar_options())


@pytest.mark.parametrize(("handler", "reason"), [
    # Keyword values reach the service only as the override's own same-named
    # parameter or a value derived from it, never a literal the input contract forbids.
    (FORGED_SUPPORT_HANDLER, "thread_type"),
    (TEMPLATE_HANDLER + _SEND + "        return await super().send_message(ctx, thread_id=thread_id, body=body, message_type='system')\n",
     "message_type"),
    (TEMPLATE_HANDLER + _SEND + "        return await super().send_message(ctx, thread_id=thread_id, body=thread_id, message_type=message_type)\n",
     "body"),
    (TEMPLATE_HANDLER + _SEND + "        fixed = 'pinned'\n"
     "        return await super().send_message(ctx, thread_id=thread_id, body=fixed, message_type=message_type)\n", "body"),
    # A local annotation is walked through the same grammar.
    (TEMPLATE_HANDLER + _SEND + "        cleaned: str(body) = body\n"
     "        return await super().send_message(ctx, thread_id=thread_id, body=cleaned, message_type=message_type)\n",
     "annotation"),
])
def test_workspace_handler_extension_grammar_bounds_service_values_and_local_annotations(handler, reason):
    from mozaiksai.core.semantics.implementation_artifacts import (
        ImplementationArtifactError,
        prove_workspace_handler_extension,
    )

    with pytest.raises(ImplementationArtifactError, match=reason):
        prove_workspace_handler_extension(handler_source=handler, **_grammar_options())


TYPED_HANDLER = TEMPLATE_HANDLER.replace(
    "from .base_handler import", "from typing import Any\n\nfrom .base_handler import",
) + '''
    async def send_message(
        self, ctx: ModuleContext, *, thread_id: str, body: str, message_type: str = "text", **_: object,
    ) -> dict[str, Any]:
        cleaned: str = " ".join(body.split())
        return await super().send_message(ctx, thread_id=thread_id, body=cleaned, message_type=message_type)
'''


@pytest.mark.parametrize("handler", [TEMPLATE_HANDLER, NORMALIZING_HANDLER, TYPED_HANDLER])
def test_workspace_handler_extension_grammar_keeps_typed_and_derived_overrides(handler):
    from mozaiksai.core.semantics.implementation_artifacts import prove_workspace_handler_extension

    assert prove_workspace_handler_extension(handler_source=handler, **_grammar_options()) in {(), ("send_message",)}


def test_workspace_handler_keeps_the_template_future_annotations_import():
    from mozaiksai.core.semantics.implementation_artifacts import (
        ImplementationArtifactError,
        prove_workspace_handler_extension,
    )

    options = _grammar_options(template_handler_source=TEMPLATE_HANDLER)
    assert prove_workspace_handler_extension(handler_source=NORMALIZING_HANDLER, **options) == ("send_message",)
    with pytest.raises(ImplementationArtifactError, match="from __future__ import annotations"):
        prove_workspace_handler_extension(handler_source=NORMALIZING_HANDLER.replace(_FUTURE, ""), **options)


@pytest.mark.asyncio
async def test_generator_owned_pack_outputs_are_not_template_owned(tmp_path):
    import shutil

    from mozaiksai.control_plane.implementations.coding_worker import (
        pack_output_owners,
        parent_workspace_pack_paths,
    )
    from tests.test_messaging_pack_persistence_composition import _context as pack_context

    root = tmp_path / "messaging"
    shutil.copytree(PACK, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    contract = root / "contract.yaml"
    generated = "config/messaging_digest.yaml"
    contract.write_text(contract.read_text(encoding="utf-8").replace(
        "forbidden_outputs:", f"  - path: {generated}\n    owner: generator\n\nforbidden_outputs:",
    ), encoding="utf-8")
    packs = pack_context(root, pinned=False)["capability_packs"]
    owners = pack_output_owners(packs)
    assert owners[generated] == "generator"
    assert owners[HANDLER] == "workspace" and owners["modules/messages/backend/base_handler.py"] == "templates"
    assert owners[POLICY] == "templates" and owners[PROVENANCE] == "templates"
    assert parent_workspace_pack_paths(packs) == frozenset({HANDLER})
    # A Studio patch to model-authored pack output is ordinary app code, not template drift.
    worker = ScopedRefinementCodingWorker(config_loader=_config, artifact_store=_Store(), output_root=tmp_path / "out")
    request = SimpleNamespace(
        metadata={}, files={generated: ""}, app_id=APP_ID, artifact_app_id=APP_ID, build_record_id="parent",
        build_family="app_bundle",
    )
    scope = await worker._admit_pack_outputs(
        request=request, capability_packs=packs, applied_files={generated: "digest: weekly\n"},
        merged_files={generated: "digest: weekly\n"},
    )
    assert scope["changed_workspace_pack_paths"] == []


EMPTY_CONTRACT = {"version": "1", "surfaces": [], "shared_collections": []}


def _owning_task_revision(request, handler, *, task_handler=None, **extra):
    """A revision whose plan task owns the app-owned handler, on a verified saved source."""
    saved = request.getfixturevalue("revision_baseline")
    saved.files = _baseline(handler)
    _replace_archive(saved, {})
    plan = {**_context()["app_build_plan"], "build_tasks": [
        {"task_id": "svc", "task_type": "business_services", "initial_agent": "ServiceAgent", "owned_paths": [HANDLER]},
    ]}
    return _revision_context(saved, saved.files, app_build_plan=plan, data_contract=EMPTY_CONTRACT, app_task_batch_results={
        "svc": {"code_files": [{"filename": HANDLER, "content": task_handler or handler}]},
    }, **extra)


def _select_handler_repair(context):
    prepare_bundle_repair({"passed": False, "diagnostics": [
        {"path": HANDLER, "error": f"{HANDLER}: module implementation contract failed"},
    ]}, context)
    active = context.get("bundle_repair_result")["active"]
    assert active["task_id"] == "svc" and list(active["allowed_paths"]) == [HANDLER]


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline_handler", ["template", "customized"])
@pytest.mark.parametrize("repair", ["privileged", "delete"])
async def test_bounded_repair_cannot_change_the_workspace_handler(request, baseline_handler, repair):
    verified = NORMALIZING_HANDLER if baseline_handler == "customized" else TEMPLATE_HANDLER
    context = _owning_task_revision(request, verified)
    assert (await assemble_app_tasks(context_variables=context))["success"]
    _select_handler_repair(context)
    context.set("structured_output", (
        {"code_files": [{"filename": HANDLER, "content": PRIVILEGED_HANDLER}]} if repair == "privileged"
        else {"code_files": [], "deleted_files": [HANDLER]}
    ))
    saved = save_generated_code(context)
    # Rejected whole, with the owned diagnostic naming the authorized path.
    assert saved["status"] == "rejected" and saved["saved_files"] == [] and saved["deleted_files"] == []
    assert saved["diagnostic"]["code"] == "WORKSPACE_PACK_OUTPUT_CHANGE_UNSUPPORTED"
    assert saved["diagnostic"]["paths"] == [HANDLER] and saved["diagnostic"]["task_id"] == "svc"
    assert saved["diagnostic"]["missing_capability"] == "authorized_backend_refinement"
    assert "Studio selected-file patch refinement" in saved["error"]
    assert context.get("bundle_repair_result")["active"]["status"] == "rejected"
    assert HANDLER not in {entry["filename"] for entry in context.get("code_files") or []}
    assert HANDLER not in (context.get("deleted_files") or [])
    assert admitted_app_file_map(context)[HANDLER] == verified


@pytest.mark.asyncio
async def test_repair_restating_the_verified_handler_is_admitted(request):
    context = _owning_task_revision(request, NORMALIZING_HANDLER)
    assert (await assemble_app_tasks(context_variables=context))["success"]
    _select_handler_repair(context)
    context.set("structured_output", {"code_files": [{"filename": HANDLER, "content": NORMALIZING_HANDLER}]})
    saved = save_generated_code(context)
    assert saved.get("status") != "rejected" and saved["saved_files"] == [HANDLER]
    # The responded repair records what it saved: ordering evidence for later composition.
    assert list(context.get("bundle_repair_result")["active"]["saved_paths"]) == [HANDLER]
    assert admitted_app_file_map(context)[HANDLER] == NORMALIZING_HANDLER


@pytest.mark.asyncio
@pytest.mark.parametrize("owning_task", [False, True])
async def test_overlay_copy_of_the_workspace_handler_never_replaces_the_verified_source(request, caplog, owning_task):
    overlay = [{"filename": HANDLER, "content": PRIVILEGED_HANDLER}]
    if owning_task:
        # A task change backed by an identical overlay copy still needs the authorized path.
        context = _owning_task_revision(request, NORMALIZING_HANDLER, task_handler=PRIVILEGED_HANDLER, code_files=overlay)
    else:
        saved = request.getfixturevalue("revision_baseline")
        saved.files = _baseline(NORMALIZING_HANDLER)
        _replace_archive(saved, {})
        context = _revision_context(saved, saved.files, code_files=overlay, deleted_files=[HANDLER])
    caplog.set_level(logging.WARNING, logger="factory_app.workflows.AppGenerator.tools")
    result = await assemble_app_tasks(context_variables=context)
    if owning_task:
        assert not result["success"]
        assert result["diagnostic"]["code"] == "WORKSPACE_PACK_OUTPUT_CHANGE_UNSUPPORTED"
        assert result["diagnostic"]["retained"] == "verified_revision_source"
        return
    assert result["success"], result
    assert _files(result)[HANDLER] == NORMALIZING_HANDLER
    # The projection that validation checks and export ships keeps the verified copy too.
    assert admitted_app_file_map(context)[HANDLER] == NORMALIZING_HANDLER
    assert any("WORKSPACE_PACK_OUTPUT_EXCLUDED" in record.getMessage() for record in caplog.records)


def _accept_records(store, children, *, scope=None, registry="registry_1"):
    from mozaiksai.core.artifacts import ArtifactValidationStatus

    # The registry's compare-and-set run chain names the latest registered run.
    store.registry_run = {"build_id": BINDING.build_id, "phase": "refinement", "artifact_version_id": children[-1]}
    for child in children:
        store.records[child] = BuildRecord(
            id=child, app_id=APP_ID, build_family="app_bundle", build_key="app_bundle", version_number=2,
            lineage_root_id="parent", parent_build_record_id="parent", lifecycle_status=ArtifactLifecycleStatus.DRAFT,
            validation_status=ArtifactValidationStatus.PASSED, app_validation_status="passed",
            commit_metadata={"author_user_id": "owner", "metadata": {
                **BINDING.model_dump(), "build_registry_id": registry,
                "refinement_scope": scope or {
                    "selected_file_paths": [HANDLER], "scope_origin": "explicit", "changed_workspace_pack_paths": [HANDLER],
                },
            }},
        )


def _accept_route(monkeypatch, store):
    from mozaiksai.hosts import studio

    store.accepted = []

    async def accept_build_record(*, app_id, build_record_id, commit_metadata=None):
        await asyncio.sleep(0)  # a store round-trip lets another request interleave
        target = store.records[build_record_id]
        for record_id, record in list(store.records.items()):
            if record.lifecycle_status == ArtifactLifecycleStatus.CURRENT and record.build_family == target.build_family:
                store.records[record_id] = record.model_copy(update={"lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED})
        store.records[build_record_id] = target.model_copy(update={"lifecycle_status": ArtifactLifecycleStatus.CURRENT})
        store.accepted.append(build_record_id)
        return store.records[build_record_id]

    async def scope(principal, *, build_registry_id, app_id=None):
        return APP_ID, principal.user_id

    def bundle(files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path, content in files.items():
                archive.writestr(f"bundle/{path}", content)
        return buffer.getvalue()

    async def verified_app_bundle(version):
        # Every record carries the starter handler unless a test sets its own files.
        return bundle(getattr(store, "bundle_files", {}).get(version.id, {HANDLER: "STARTER", PAGE: version.id}))

    class Registry:
        async def get_app_record(self, *, owner_user_id, build_registry_id):
            return {"app": {"current_build_run": dict(getattr(store, "registry_run", {}))}}

    store.accept_build_record = accept_build_record
    store.list_refinement_sessions = AsyncMock(return_value=[])
    # A fresh per-app lock table: an asyncio lock binds to the event loop it first waits in.
    monkeypatch.setattr(studio, "_WORKSPACE_ACCEPTANCE_LOCKS", {})
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: Registry())
    monkeypatch.setattr(studio, "_verified_app_bundle", verified_app_bundle)
    monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
    monkeypatch.setattr(studio, "_resolve_studio_artifact_scope", scope)
    monkeypatch.setattr(studio, "_verified_review_snapshots", AsyncMock(return_value=None))
    monkeypatch.setattr(studio, "_build_artifact_review_payload", AsyncMock(return_value={"review": {}}))

    async def accept(child_id, *, registry="registry_1"):
        return await studio.accept_build_artifact_version(
            child_id, body=None, app_id=None, build_registry_id=registry, principal=SimpleNamespace(user_id="owner"),
        )

    return accept


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["stale_parent", "unauthorized_scope", "other_registry"])
async def test_accepting_a_handler_change_rechecks_its_parent_and_authority(monkeypatch, case):
    from fastapi import HTTPException

    store = _Store()
    _accept_records(store, ["child_1"], scope={
        "selected_file_paths": [], "scope_origin": "auto_proposal", "changed_workspace_pack_paths": [HANDLER],
    } if case == "unauthorized_scope" else None, registry="registry_2" if case == "other_registry" else "registry_1")
    if case == "stale_parent":
        store.supersede_parent_with("newer_build")
    accept = _accept_route(monkeypatch, store)
    before = deepcopy(store.records)
    with pytest.raises(HTTPException) as rejected:
        await accept("child_1")
    assert rejected.value.status_code == 409
    assert store.accepted == [] and store.records == before


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted_since", ["same_app_owned_code", "customized_handler"])
async def test_accepting_a_stale_ui_only_child_cannot_revert_app_owned_pack_code(monkeypatch, accepted_since):
    from fastapi import HTTPException

    store = _Store()
    _accept_records(store, ["child_1"], scope={
        "selected_file_paths": [PAGE], "scope_origin": "explicit", "changed_workspace_pack_paths": [],
    })
    store.supersede_parent_with("newer_build")
    if accepted_since == "customized_handler":
        # An authorized handler change was accepted after child_1's parent.
        store.bundle_files = {"newer_build": {HANDLER: "CUSTOMIZED", PAGE: "newer_build"}}
    accept = _accept_route(monkeypatch, store)
    if accepted_since == "same_app_owned_code":
        # The existing rule: a stale UI-only draft is still accepted when it
        # carries the accepted app-owned pack code.
        assert (await accept("child_1"))["accepted"] is True
        assert store.accepted == ["child_1"]
        return
    before = deepcopy(store.records)
    with pytest.raises(HTTPException) as rejected:
        await accept("child_1")
    assert rejected.value.status_code == 409 and HANDLER in rejected.value.detail
    assert store.accepted == [] and store.records == before


@pytest.mark.asyncio
@pytest.mark.parametrize("registry_run", ["this_child", "newer_run"])
async def test_accepting_a_handler_change_whose_run_staled_its_parent(monkeypatch, registry_run):
    """A refinement run marks its own parent stale, so no build is CURRENT during review."""
    from fastapi import HTTPException

    store = _Store()
    _accept_records(store, ["child_1"])
    store.records["parent"] = store.records["parent"].model_copy(update={"lifecycle_status": ArtifactLifecycleStatus.STALE})
    if registry_run == "newer_run":
        # Another refinement began after this child's run, replacing the registry's current run.
        store.registry_run = {"build_id": "build_newer", "phase": "refinement"}
    accept = _accept_route(monkeypatch, store)
    if registry_run == "this_child":
        assert (await accept("child_1"))["accepted"] is True
        assert store.accepted == ["child_1"]
        return
    before = deepcopy(store.records)
    with pytest.raises(HTTPException) as rejected:
        await accept("child_1")
    assert rejected.value.status_code == 409 and "newer build run" in rejected.value.detail
    assert store.accepted == [] and store.records == before


@pytest.mark.asyncio
async def test_concurrent_acceptance_of_two_handler_children_admits_only_one(monkeypatch):
    from fastapi import HTTPException

    store = _Store()
    _accept_records(store, ["child_1", "child_2"])
    accept = _accept_route(monkeypatch, store)
    results = await asyncio.gather(accept("child_1"), accept("child_2"), return_exceptions=True)
    accepted = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, HTTPException)]
    assert len(accepted) == 1 and [error.status_code for error in rejected] == [409], results
    assert len(store.accepted) == 1
    winner, loser = store.accepted[0], ({"child_1", "child_2"} - set(store.accepted)).pop()
    assert store.records[winner].lifecycle_status == ArtifactLifecycleStatus.CURRENT
    assert store.records[loser].lifecycle_status == ArtifactLifecycleStatus.DRAFT


def _with_parent_packs(store, record_id):
    """The child keeps its parent's capability packs, as an admitted child does."""
    record = store.records[record_id]
    metadata = {**record.commit_metadata.metadata, "capability_packs": deepcopy(_packs())}
    store.records[record_id] = record.model_copy(update={
        "commit_metadata": record.commit_metadata.model_copy(update={"metadata": metadata}),
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["handler_child", "ui_child"])
async def test_concurrent_handler_and_stale_ui_only_acceptances_are_serialized(monkeypatch, first):
    """The handler child's run staled the shared parent, so no build is CURRENT during review.

    Each acceptance passes its own checks while the other is still in flight; only the
    per-app acceptance lock makes the second one see the first as CURRENT and refuse.
    """
    from fastapi import HTTPException

    store = _Store()
    _accept_records(store, ["ui_child"], scope={
        "selected_file_paths": [PAGE], "scope_origin": "explicit", "changed_workspace_pack_paths": [],
    })
    _accept_records(store, ["handler_child"])  # the registry's latest run names the handler child
    for child in ("ui_child", "handler_child"):
        _with_parent_packs(store, child)
    store.records["parent"] = store.records["parent"].model_copy(update={"lifecycle_status": ArtifactLifecycleStatus.STALE})
    store.bundle_files = {"handler_child": {HANDLER: "CUSTOMIZED", PAGE: "parent"}}
    accept = _accept_route(monkeypatch, store)
    second = ({"handler_child", "ui_child"} - {first}).pop()
    results = await asyncio.gather(accept(first), accept(second), return_exceptions=True)
    assert isinstance(results[0], dict) and results[0]["accepted"] is True, results
    assert isinstance(results[1], HTTPException) and results[1].status_code == 409, results
    assert store.accepted == [first]
    current = [record_id for record_id, record in store.records.items() if record.lifecycle_status == ArtifactLifecycleStatus.CURRENT]
    assert current == [first] and store.records[second].lifecycle_status == ArtifactLifecycleStatus.DRAFT
    if first == "handler_child":
        # The UI-only sibling carries the starter handler and would have reverted the change.
        assert HANDLER in results[1].detail


# Two hundred text files fill the Studio text decoder's file limit, so an entry after them
# lies past the bound that inline refinement reads within.
_DECODER_FILLER = {f"docs/note_{index:03d}.md": "note\n" for index in range(200)}
PROVENANCE_ONLY = "modules/notes/backend/handler.py"


def _workspace_provenance(path):
    return json.dumps({"packs": [{"materialized_owned_files": [{"path": path, "owner": "workspace"}]}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", [
    "past_file_limit", "over_file_size", "provenance_past_file_limit",
    "unchanged_past_file_limit_in_one", "over_compare_bound", "over_total_compare_bound",
])
async def test_stale_ui_only_child_compares_app_owned_code_past_the_text_decoder_bounds(monkeypatch, layout):
    """Acceptance compares app-owned pack code in full, however large the app bundle grows."""
    from fastapi import HTTPException

    from mozaiksai.hosts import studio

    store = _Store()
    _accept_records(store, ["child_1"], scope={
        "selected_file_paths": [PAGE], "scope_origin": "explicit", "changed_workspace_pack_paths": [],
    })
    store.supersede_parent_with("newer_build")
    oversized = "#" * 250_001
    current, child = {
        "past_file_limit": ({**_DECODER_FILLER, HANDLER: "CUSTOMIZED"}, {**_DECODER_FILLER, HANDLER: "STARTER"}),
        "over_file_size": ({HANDLER: "CUSTOMIZED" + oversized}, {HANDLER: "STARTER" + oversized}),
        "provenance_past_file_limit": (
            {**_DECODER_FILLER, PROVENANCE: _workspace_provenance(PROVENANCE_ONLY), PROVENANCE_ONLY: "CUSTOMIZED"},
            {**_DECODER_FILLER, PROVENANCE: _workspace_provenance(PROVENANCE_ONLY), PROVENANCE_ONLY: "STARTER"},
        ),
        "unchanged_past_file_limit_in_one": ({HANDLER: "CUSTOMIZED"}, {**_DECODER_FILLER, HANDLER: "CUSTOMIZED"}),
        "over_compare_bound": ({HANDLER: "CUSTOMIZED" * 8}, {HANDLER: "CUSTOMIZED" * 8}),
        # Unchanged entries, each within the entry bound, that together pass the total bound.
        "over_total_compare_bound": (
            {PROVENANCE: _workspace_provenance(PROVENANCE_ONLY), HANDLER: "CUSTOMIZED" * 20, PROVENANCE_ONLY: "#" * 200},
            {PROVENANCE: _workspace_provenance(PROVENANCE_ONLY), HANDLER: "CUSTOMIZED" * 20, PROVENANCE_ONLY: "#" * 200},
        ),
    }[layout]
    store.bundle_files = {"newer_build": current, "child_1": child}
    if layout == "over_compare_bound":
        # An entry too large to compare is refused, never skipped.
        monkeypatch.setattr(studio, "_APP_OWNED_CODE_COMPARE_MAX_BYTES", 16)
    if layout == "over_total_compare_bound":
        # So are entries that together are too large to read for one comparison.
        monkeypatch.setattr(studio, "_APP_OWNED_CODE_COMPARE_MAX_TOTAL_BYTES", 300)
    accept = _accept_route(monkeypatch, store)
    if layout == "unchanged_past_file_limit_in_one":
        assert (await accept("child_1"))["accepted"] is True
        assert store.accepted == ["child_1"]
        return
    before = deepcopy(store.records)
    with pytest.raises(HTTPException) as rejected:
        await accept("child_1")
    assert rejected.value.status_code == 409
    assert (PROVENANCE_ONLY if layout.startswith("provenance") else HANDLER) in rejected.value.detail
    assert store.accepted == [] and store.records == before
