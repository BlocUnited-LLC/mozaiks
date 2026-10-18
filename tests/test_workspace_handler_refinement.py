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
from datetime import UTC, datetime, timedelta
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
from mozaiksai.core.artifacts import ArtifactLifecycleStatus, ArtifactValidationStatus
from mozaiksai.core.artifacts.models import (
    ArtifactCommitMetadata,
    BuildRecord,
    RefinementSessionStatus,
)
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
#: When the fixtures' saved parent, AppGenerator's first build (saved as a draft), was accepted
#: through Studio. It is earlier than the record's creation, so its acceptance time reads as its creation.
PARENT_ACCEPTED_AT = "2026-01-01T00:00:00Z"


def _acceptance_evidence(
    record_id, *, app_id, accepted_by="owner", accepted_at=PARENT_ACCEPTED_AT, family="app_bundle",
    transition="studio_accept",
):
    """The evidence an authorized acceptance writes on the record it makes current, with that record's scope."""
    from mozaiksai.control_plane.contracts import build_acceptance_evidence

    scope = SimpleNamespace(id=record_id, app_id=app_id, build_family=family, build_key=family)
    return build_acceptance_evidence(scope, accepted_by=accepted_by, accepted_at=accepted_at, transition=transition)
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
    """The saved parent, accepted through Studio, and every candidate the finalizer persists."""

    def __init__(self, *, app_id=APP_ID, metadata=None):
        self.app_id = app_id
        self.records = {"parent": BuildRecord(
            id="parent", app_id=app_id, build_family="app_bundle", build_key="app_bundle",
            version_number=1, lineage_root_id="parent", lifecycle_status=ArtifactLifecycleStatus.CURRENT,
            commit_metadata={"author_user_id": "owner", "metadata": {
                "capability_packs": deepcopy(_packs()), "acceptance": _acceptance_evidence("parent", app_id=app_id),
                **(metadata or BINDING.model_dump()),
            }},
        )}
        self.created = []
        self.change_requests = {}

    async def get_build_record(self, *, app_id, build_record_id):
        record = self.records.get(build_record_id)
        return record if record is not None and record.app_id == app_id else None

    async def get_change_request(self, *, app_id, change_request_id):
        change = self.change_requests.get(change_request_id)
        return change if change is not None and change.app_id == app_id else None

    async def get_current_build_record_refs(self, *, app_id):
        """The accepted current record per build family, as the artifact store reports it."""
        return {
            record.build_family: record.id for record in self.records.values()
            if record.app_id == app_id and record.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        }

    async def list_build_records(
        self, *, app_id, build_family=None, build_key=None, lifecycle_status=None, updated_since=None, limit=50, **_,
    ):
        """The store's filters (family, key, status, last written at or after ``updated_since``), newest version first."""
        def written_at(record):
            moment = record.updated_at
            return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)

        records = [
            record for record in self.records.values()
            if record.app_id == app_id and (build_family is None or record.build_family == build_family)
            and (build_key is None or record.build_key == build_key)
            and (lifecycle_status is None or record.lifecycle_status == lifecycle_status)
            and (updated_since is None or written_at(record) >= updated_since)
        ]
        return sorted(records, key=lambda record: record.version_number, reverse=True)[:limit]

    def supersede_parent_with(self, record_id):
        """Another accepted build replaced the parent as the app's current build; its acceptance names it."""
        parent = self.records["parent"]
        self.records["parent"] = parent.model_copy(update={"lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED})
        metadata = {
            **parent.commit_metadata.metadata,
            "acceptance": _acceptance_evidence(record_id, app_id=parent.app_id, accepted_at="2026-01-02T00:00:00Z"),
        }
        self.records[record_id] = parent.model_copy(update={
            "id": record_id, "version_number": 2, "lifecycle_status": ArtifactLifecycleStatus.CURRENT,
            "commit_metadata": parent.commit_metadata.model_copy(update={"metadata": metadata}),
        })

    async def create_build_record(self, **kwargs):
        self.created.append(kwargs)
        record = BuildRecord(
            id=f"child_{len(self.created)}", version_number=len(self.created) + 1, lineage_root_id="parent", **kwargs,
        )
        if record.lifecycle_status == ArtifactLifecycleStatus.CURRENT:
            # As the store does: a record saved CURRENT supersedes the CURRENT one of its family and key.
            for record_id, other in list(self.records.items()):
                if (
                    other.build_family == record.build_family and other.build_key == record.build_key
                    and other.lifecycle_status == ArtifactLifecycleStatus.CURRENT
                ):
                    self.records[record_id] = other.model_copy(update={
                        "lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED, "updated_at": record.updated_at,
                    })
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


async def _prepared(harness, baseline, paths, *, extra=None, confirmation_required_paths=None):
    """Mirror Studio: resolve the scope (naming the parent's app-owned pack paths), then bind the run and baseline."""
    from mozaiksai.control_plane.implementations.coding_worker import parent_workspace_pack_paths

    refinement = _refinement(**(extra or {}))
    request = harness.build_coding_request(
        refinement_request=refinement, routing_decision=_routing(refinement),
        payload={"files": {path: baseline[path] for path in paths}},
    )
    if confirmation_required_paths is None:
        confirmation_required_paths = parent_workspace_pack_paths(_packs())
    prepared, decision = await harness.prepare_coding_request(
        request, confirmation_required_paths=confirmation_required_paths,
    )
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
    if code == "PACK_TEMPLATE_OUTPUT_CHANGED":
        # The backstop for any scope Studio did not refuse first names the supported path.
        assert "pack upgrade" in result.error
        assert "upgrade the pack" in result.metadata["candidate_admission"]["supported_path"]
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

    # A caller that names no protected paths still cannot apply an inferred scope.
    prepared, decision = await harness.prepare_coding_request(inferred, confirmation_required_paths=())
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
    # Re-materializing the same pack keeps the admitted handler; a raw copy from a
    # planned worker whose task does not own the handler is ignored.
    caplog.set_level(logging.WARNING, logger="factory_app.workflows.AppGenerator.tools.assemble_app_tasks")
    result = await assemble_app_tasks(context_variables=_revision_context(
        saved, child_files, app_task_batch_items=[{"task_id": "old_worker"}], app_task_batch_results={
            "old_worker": {"code_files": [{"filename": HANDLER, "content": TEMPLATE_HANDLER}]},
        },
    ))
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


class _StudioStore(_Store):
    """The artifact store Studio's trigger and acceptance use, invalidating for real.

    Invalidation marks a record STALE as ``mark_artifact_version_stale`` does
    (anything but ARCHIVED or DELETED, stamping stale_at and updated_at), the
    current refs name CURRENT records only, a run's children are found by
    parent and the run's build_id, acceptance supersedes the CURRENT record of
    the accepted record's family and key and writes the commit metadata it is
    given, and rejection archives the draft. Build records are listed by
    family, key, status and last write, newest version first. ChangeRequests
    and refinement sessions are kept as written and listed by the build they
    were made from, newest first, as the store does; a workflow launch
    rewrites its ChangeRequest's routing decision.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.change_requests = {}
        self.invalidations = []
        self.accepted = []
        self.refinement_sessions = []

    async def create_change_request(self, **kwargs):
        change = SimpleNamespace(id=f"cr_{len(self.change_requests) + 1}", **kwargs)
        self.change_requests[change.id] = change
        return change

    async def get_change_request(self, *, app_id, change_request_id):
        change = self.change_requests.get(change_request_id)
        return change if change is not None and change.app_id == app_id else None

    async def list_change_requests(self, *, app_id, build_record_id=None, limit=50, **_):
        changes = [
            change for change in self.change_requests.values()
            if change.app_id == app_id and (build_record_id is None or change.build_record_id == build_record_id)
        ]
        return list(reversed(changes))[:limit]

    async def update_change_request_router_decision(self, *, app_id, change_request_id, router_decision):
        change = self.change_requests[change_request_id]
        change.router_decision = dict(router_decision)
        return True

    async def invalidate_artifact_version_refs(
        self, *, app_id, artifact_version_refs, affected_artifact_kinds=None, reason, invalidated_by_version_id=None,
    ):
        self.invalidations.append({"refs": dict(artifact_version_refs), "reason": reason})
        staled = []
        for kind in affected_artifact_kinds or list(artifact_version_refs):
            record = self.records.get(artifact_version_refs.get(kind) or "")
            if record is None or record.app_id != app_id or record.lifecycle_status in {
                ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED,
            }:
                continue
            now = datetime.now(UTC)
            self.records[record.id] = record.model_copy(update={
                "lifecycle_status": ArtifactLifecycleStatus.STALE, "invalidation_reason": reason,
                "stale_at": now, "updated_at": now,
            })
            staled.append(record.id)
        return staled

    async def invalidate_artifact_family(self, **kwargs):
        # Build records carry no artifact_kind, so the store's family query matches nothing.
        return 0

    async def list_run_child_build_records(self, *, app_id, parent_build_record_id, build_id, limit=10):
        return [
            record for record in self.records.values()
            if record.app_id == app_id and record.parent_build_record_id == parent_build_record_id
            and record.commit_metadata.metadata.get("build_id") == build_id
        ]

    async def accept_build_record(self, *, app_id, build_record_id, commit_metadata=None):
        await asyncio.sleep(0)  # a store round-trip lets another request interleave
        target = self.records[build_record_id]
        now = datetime.now(UTC)
        for record_id, record in list(self.records.items()):
            if (
                record_id != build_record_id and record.build_family == target.build_family
                and record.build_key == target.build_key and record.lifecycle_status == ArtifactLifecycleStatus.CURRENT
            ):
                self.records[record_id] = record.model_copy(update={
                    "lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED, "updated_at": now,
                })
        updates = {"lifecycle_status": ArtifactLifecycleStatus.CURRENT, "updated_at": now}
        if commit_metadata is not None:
            updates["commit_metadata"] = ArtifactCommitMetadata.model_validate(commit_metadata)
        self.records[build_record_id] = target.model_copy(update=updates)
        self.accepted.append(build_record_id)
        return self.records[build_record_id]

    async def reject_artifact_version(self, *, app_id, artifact_version_id, reason):
        record = self.records[artifact_version_id]
        self.records[artifact_version_id] = record.model_copy(update={
            "lifecycle_status": ArtifactLifecycleStatus.ARCHIVED, "invalidation_reason": reason,
            "updated_at": datetime.now(UTC),
        })
        return True

    async def create_refinement_session(self, **kwargs):
        self.refinement_sessions.append({"id": f"rs_{len(self.refinement_sessions) + 1}", "ended_at": None, **kwargs})
        return SimpleNamespace(id=self.refinement_sessions[-1]["id"])

    async def list_refinement_sessions(
        self, *, app_id, build_record_id=None, result_build_record_id=None, change_request_id=None, limit=20,
    ):
        return [
            SimpleNamespace(**session) for session in reversed(self.refinement_sessions)
            if session["app_id"] == app_id
            and (build_record_id is None or session.get("build_record_id") == build_record_id)
            and (result_build_record_id is None or session.get("result_build_record_id") == result_build_record_id)
            and (change_request_id is None or session.get("change_request_id") == change_request_id)
        ][:limit]

    async def update_refinement_session(self, *, app_id, session_id, status=None, ended_at=None, **_):
        session = next(session for session in self.refinement_sessions if session["id"] == session_id)
        session.update({key: value for key, value in {"status": status, "ended_at": ended_at}.items() if value is not None})
        return True

    def status(self, record_id):
        return self.records[record_id].lifecycle_status

    def children(self):
        return sorted(record_id for record_id, record in self.records.items() if record.parent_build_record_id)

    def current(self):
        return sorted(
            record_id for record_id, record in self.records.items()
            if record.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        )


class _Registry:
    """The app registry's run chain: a new run replaces the current run; completion is compare-and-set."""

    def __init__(self, artifact_version_id="parent"):
        self.record = {
            "build_registry_id": "appreg_1", "app_id": "app_1", "chat_app_id": "factory", "lifecycle_state": "review",
            "current_build_run": {"build_id": "build_0", "artifact_version_id": artifact_version_id},
        }
        self.begun = []
        self.superseded = False  # a completion that loses its compare-and-set
        self.get_app_record = AsyncMock(side_effect=self._get_app_record)
        self.begin_refinement_run = AsyncMock(side_effect=self._begin_refinement_run)
        self.update_build_status = AsyncMock(side_effect=self._update_build_status)

    async def _get_app_record(self, *, owner_user_id, build_registry_id):
        return {"app": deepcopy(self.record)}

    async def _begin_refinement_run(self, *, owner_user_id, app_id, build_registry_id, workflow_name, chat_id=None):
        binding = RunBuildBinding(
            build_registry_id="appreg_1", target_app_id="app_1", build_id=f"run_{len(self.begun) + 1}", phase="refinement",
        )
        self.begun.append(binding.build_id)
        self.record.update(lifecycle_state="building", current_build_run={"build_id": binding.build_id, "phase": "refinement"})
        return binding

    async def _update_build_status(self, *, owner_user_id, build_registry_id, status, expected_build_id=None,
                                   artifact_version_id=None, **_):
        run = self.record["current_build_run"]
        if self.superseded or (expected_build_id is not None and run.get("build_id") != expected_build_id):
            return {"success": False, "app": None}
        self.record["lifecycle_state"] = status
        if artifact_version_id:
            run["artifact_version_id"] = artifact_version_id
        return {"success": True, "app": deepcopy(self.record)}


def _scope_proposer(paths=(HANDLER,)):
    return SimpleNamespace(
        propose=AsyncMock(return_value=ScopeProposal(
            resolution="scoped_files", selected_paths=list(paths), rationale="The send action lives here.",
            confidence=0.95, clarification_question=None, signals=["messages"],
        )),
        materialize_files=AsyncMock(side_effect=lambda **kwargs: dict.fromkeys(paths, TEMPLATE_HANDLER)),
    )


def _page(version):
    return _baseline(app_id="app_1")[PAGE] + f"\n// board revision {version}\n"


def _studio_flow(
    monkeypatch, tmp_path, *, outputs, proposer=None, accepted="parent", registry_current="parent", launches=False,
    first_build=False,
):
    """Studio's real trigger and acceptance routes, the real coding worker, and stateful stores.

    ``outputs`` are the coding provider's file changes, one per run, in order.
    No workflow can launch unless ``launches``: then a routed launch binds a
    registry run as a workflow does, and ``launched`` records it. The parent is
    AppGenerator's first build, already accepted through Studio (CURRENT, with
    the acceptance evidence that acceptance writes); with ``first_build`` it is
    still the draft AppGenerator saved, with no evidence.
    """
    from mozaiksai.control_plane.invalidation import ArtifactInvalidationService
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
    store = _StudioStore(app_id="app_1", metadata={})
    parent = _artifact_version(
        artifact_version_id="parent", zip_path=archive,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT if first_build else ArtifactLifecycleStatus.CURRENT,
    )
    # AppGenerator's first build: saved as a draft with its pack selection; Studio's acceptance made it
    # current and wrote the evidence naming it.
    parent.commit_metadata.metadata["capability_packs"] = deepcopy(_packs())
    if not first_build:
        parent.commit_metadata.metadata["acceptance"] = _acceptance_evidence(
            "parent", app_id="app_1", accepted_by="demo-user",
        )
    store.records["parent"] = parent
    if accepted != "parent":
        store.supersede_parent_with(accepted)
    registry = _Registry(registry_current)
    queued = [dict(changes) for changes in outputs]
    provider = SimpleNamespace(execute=AsyncMock(side_effect=lambda *args, **kwargs: _proposal(queued.pop(0))))
    validate = AsyncMock(return_value=_evidence())
    worker = ScopedRefinementCodingWorker(
        provider=provider, config_loader=_config, candidate_validation_runner=validate, artifact_store=store,
        output_root=tmp_path / "refinements",
    )
    harness = orchestration_control.OrchestrationControlHarness(
        refinement_resolver=RefinementTriggerRouteResolver(classifier=SimpleNamespace(classify=_async_classifier(
            change_class="patch", rationale=REQUEST, confidence=0.95, signals=["messages"],
        ))),
        coding_worker=worker, config_loader=_config,
        # Studio's session refs live under a target-scoped key, so the service finds none.
        artifact_invalidation=ArtifactInvalidationService(session_store=SimpleNamespace(load=AsyncMock(return_value=None))),
        **({"scope_proposer": proposer} if proposer else {}),
    )
    router = SessionRouter(persistence=_FakePersistence())
    launch = AsyncMock(side_effect=AssertionError("no workflow launch"))
    launched = []
    if launches:
        async def prepare(**kwargs):
            routed = kwargs["routing_contribution"]
            return SimpleNamespace(
                workflow_id=routed.workflow_id, journey_id=routed.journey_id,
                routing_decision=SimpleNamespace(
                    requested_workflow_id=routed.workflow_id, explanation=routed.explanation,
                    is_full_restart=routed.is_full_restart, rerouted_by_dependency=False,
                ),
            )

        async def launch_workflow(prepared):
            # The workflow binds a registry run, as resolve_build_binding does for a refinement.
            await registry.begin_refinement_run(
                owner_user_id="demo-user", app_id="factory", build_registry_id="appreg_1",
                workflow_name=prepared.workflow_id,
            )
            launched.append(prepared.workflow_id)
            return SimpleNamespace(
                chat_id=f"chat_{len(launched)}", workflow_id=prepared.workflow_id,
                requested_workflow_id=prepared.workflow_id, journey_id=prepared.journey_id, websocket_url="/ws/x",
                trigger_source="refinement", routing_explanation="", rerouted_by_dependency=False,
            )

        launch = AsyncMock(side_effect=prepare)
        monkeypatch.setattr(studio, "launch_prepared_workflow", launch_workflow)
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: registry)
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *args, **kwargs: ("factory", "demo-user"))
    monkeypatch.setattr(studio, "get_session_router", lambda: router)
    monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
    monkeypatch.setattr(studio, "get_orchestration_control_harness", lambda: harness)
    monkeypatch.setattr(studio, "prepare_routed_workflow_launch", launch)
    monkeypatch.setattr(studio, "_verified_review_snapshots", AsyncMock(return_value=None))
    monkeypatch.setattr(studio, "_build_artifact_review_payload", AsyncMock(return_value={"review": {}}))
    monkeypatch.setattr(studio, "_WORKSPACE_ACCEPTANCE_LOCKS", {})
    client = TestClient(studio.app)

    def trigger(coding_request, *, build_record_id="parent", **payload):
        return client.post("/api/workflows/trigger", json={
            "build_registry_id": "appreg_1", "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {"build_family": "app_bundle", "build_key": "app_bundle",
                                       "build_record_id": build_record_id, "raw_user_request": REQUEST,
                                       "source_surface": "app_workbench"},
                "coding_request": coding_request, **payload,
            },
        })

    def confirm(decision, coding_request=None, action="apply_proposed_scope", build_record_id="parent"):
        return trigger(
            {} if coding_request is None else coding_request, build_record_id=build_record_id,
            change_request_id=decision["change_request_id"], revision_id=decision["revision_id"],
            harness_action={"action_id": action},
        )

    def accept(record_id):
        return client.post(f"/api/studio/build/artifacts/{record_id}/accept", params={"build_registry_id": "appreg_1"})

    def reject(record_id):
        return client.post(f"/api/studio/build/artifacts/{record_id}/reject", params={"build_registry_id": "appreg_1"})

    def promote(record_id):
        return client.post(f"/api/studio/build/artifacts/{record_id}/promote", params={"build_registry_id": "appreg_1"})

    def session():
        return asyncio.run(router.for_target("app_1").get_session_snapshot(app_id="factory", user_id="demo-user"))

    return SimpleNamespace(
        store=store, registry=registry, validate=validate, provider=provider, launch=launch, router=router,
        client=client, trigger=trigger, confirm=confirm, accept=accept, reject=reject, promote=promote,
        session=session, launched=launched,
    )


def _studio_route(monkeypatch, tmp_path, *, files_payload, proposer=None, accepted="parent", registry_current="parent"):
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}], proposer=proposer,
        accepted=accepted, registry_current=registry_current,
    )
    flow.response = flow.trigger(files_payload)
    return flow


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
        # The run stored a reviewable child of the parent, so the parent is invalidated.
        assert route.store.invalidations == [{"refs": {"app_bundle": "parent"}, "reason": "change_request:cr_1"}]
        assert route.store.status("parent") == ArtifactLifecycleStatus.STALE
        return
    assert body["coding_worker"]["status"] == "failed"
    assert body["coding_worker"]["metadata"]["candidate_admission"]["code"] == "STALE_REFINEMENT_BASELINE"
    assert body["coding_worker"]["metadata"]["candidate_admission"]["accepted_build_record_id"] == "newer_build"
    route.validate.assert_not_awaited()
    assert route.store.created == [] and status["status"] == "needs_revision"
    # Nothing was stored, so nothing is invalidated: the newer accepted build stays current.
    assert route.store.invalidations == [] and route.store.current() == ["newer_build"]


def test_failed_inline_run_does_not_block_a_later_authorized_handler_patch(monkeypatch, tmp_path):
    """A run rejected before it stored anything leaves the parent current, so an explicit retry proceeds.

    The first run's handler passes a privileged service flag and the extension
    grammar rejects it before validation. The retry, sent by the user with the
    same explicit scope, stores the child; acceptance then makes it current.
    """
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: PRIVILEGED_HANDLER}, {HANDLER: NORMALIZING_HANDLER}],
    )
    first = flow.trigger({"files": {HANDLER: ""}})
    assert first.status_code == 200, first.text
    failed = first.json()["coding_worker"]
    assert failed["status"] == "failed"
    assert failed["metadata"]["candidate_admission"]["code"] == "WORKSPACE_HANDLER_EXTENSION_REJECTED"
    assert "build_record_id" not in failed["metadata"]
    flow.validate.assert_not_awaited()
    assert flow.store.children() == []
    # The attempt is recorded, but the parent was not changed and is not invalidated.
    assert flow.store.change_requests["cr_1"].router_decision["execution_mode"] == "coding_worker"
    assert [session["status"].value for session in flow.store.refinement_sessions] == ["failed"]
    assert flow.registry.record["lifecycle_state"] == "needs_revision"
    assert flow.store.invalidations == [] and flow.store.current() == ["parent"]

    retry = flow.trigger({"files": {HANDLER: ""}})
    assert retry.status_code == 200, retry.text
    body = retry.json()
    assert body["coding_worker"]["status"] == "validated", body["coding_worker"]
    child = flow.store.records[body["coding_worker"]["metadata"]["build_record_id"]]
    assert _archive_files(child)[HANDLER] == NORMALIZING_HANDLER
    assert child.parent_build_record_id == "parent"
    assert flow.store.invalidations == [{"refs": {"app_bundle": "parent"}, "reason": "change_request:cr_2"}]

    accepted = flow.accept(child.id)
    assert accepted.status_code == 200, accepted.text
    assert flow.store.current() == [child.id] and flow.store.status("parent") == ArtifactLifecycleStatus.STALE
    # One explicit retry, no rebuild.
    assert flow.provider.execute.await_count == 2 and flow.registry.begun == ["run_1", "run_2"]
    flow.launch.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped", "pending", "lookup_error"])
def test_a_stored_child_invalidates_its_parent_unless_its_validation_failed(monkeypatch, tmp_path, outcome):
    """The store, not the run's status, decides whether the run changed its build.

    A PASSED child awaits review. A PENDING or SKIPPED child can never be
    accepted (acceptance admits only PASSED and nothing validates a stored
    child again) but it is not "nothing changed": it is unresolved, so the
    parent is invalidated and the response says so. A FAILED child is terminal
    and durable: the parent stays current. A failed lookup is unresolved too.
    """
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {HANDLER: NORMALIZING_HANDLER}, {HANDLER: NORMALIZING_HANDLER}],
    )
    if outcome in {"failed", "skipped", "pending"}:
        flow.validate.return_value = {
            **_evidence(), "validation_status": outcome, "errors": ["candidate checks did not pass"] if outcome == "failed" else [],
            "app_validation_result": {"validation_status": outcome, "validation_strategy": "docker"},
        }
    elif outcome == "lookup_error":
        # The store cannot say what the run left, so the parent is treated as changed.
        flow.store.list_run_child_build_records = AsyncMock(side_effect=RuntimeError("store unavailable"))
    response = flow.trigger({"files": {PAGE: ""}})
    assert response.status_code == 200, response.text
    body = response.json()
    child = flow.store.records[body["coding_worker"]["metadata"]["build_record_id"]]
    assert child.parent_build_record_id == "parent" and child.lifecycle_status == ArtifactLifecycleStatus.DRAFT
    assert child.validation_status == (
        ArtifactValidationStatus.PASSED if outcome in {"passed", "lookup_error"} else ArtifactValidationStatus(outcome)
    )
    unresolved = body["unresolved_change"]
    if outcome == "failed":
        assert body["coding_worker"]["status"] == "failed" and unresolved is None
        assert flow.store.invalidations == [] and flow.store.current() == ["parent"]
        refused = flow.accept(child.id)
        assert refused.status_code == 409 and "validation_status='failed'" in refused.json()["detail"], refused.text
        # The accepted build stayed current, so an authorized handler change is still admitted against it.
        flow.validate.return_value = _evidence()
        retry = flow.trigger({"files": {HANDLER: ""}})
        assert retry.status_code == 200 and retry.json()["coding_worker"]["status"] == "validated", retry.text
        return
    assert flow.store.invalidations == [{"refs": {"app_bundle": "parent"}, "reason": "change_request:cr_1"}]
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE and flow.store.current() == []
    if outcome == "passed":
        assert unresolved is None
        return
    if outcome == "lookup_error":
        assert unresolved["reason"] == "child_lookup_failed" and unresolved["baseline_invalidation_recorded"] is True
        return
    # Unresolved, stated explicitly: in the response and on the run's refinement session.
    assert body["coding_worker"]["status"] == "planned"
    assert unresolved["reason"] == f"validation_{outcome}" and unresolved["draft_build_record_id"] == child.id
    assert unresolved["baseline_build_record_id"] == "parent" and "reject the draft" in unresolved["detail"]
    assert "stays stale" in unresolved["detail"] and "workflow route" in unresolved["detail"]
    (session,) = flow.store.refinement_sessions
    assert session["status"] == RefinementSessionStatus.PENDING and session["metadata"]["unresolved_change"] == unresolved
    # It never becomes current: acceptance refuses it.
    refused = flow.accept(child.id)
    assert refused.status_code == 409 and "validation_status='passed' is required" in refused.json()["detail"], refused.text
    # While the draft is open, a handler change from the stale parent is refused, before validation.
    flow.validate.return_value = _evidence()
    blocked = flow.trigger({"files": {HANDLER: ""}}).json()["coding_worker"]
    admission = blocked["metadata"]["candidate_admission"]
    assert admission["code"] == "STALE_REFINEMENT_BASELINE", blocked
    assert f"draft {child.id} made from the parent is draft with validation {outcome}" in admission["stale_baseline_refusal"]
    assert flow.validate.await_count == 1
    # Rejecting it is the supported next step: the retry, bound to the same parent, is admitted and accepted.
    assert flow.reject(child.id).status_code == 200
    retry = flow.trigger({"files": {HANDLER: ""}}).json()["coding_worker"]
    assert retry["status"] == "validated", (retry.get("error"), retry["metadata"].get("candidate_admission"))
    retried = flow.store.records[retry["metadata"]["build_record_id"]]
    assert retried.parent_build_record_id == "parent" and _archive_files(retried)[HANDLER] == NORMALIZING_HANDLER
    assert flow.accept(retried.id).status_code == 200
    assert flow.store.current() == [retried.id] and flow.store.status(child.id) == ArtifactLifecycleStatus.ARCHIVED


def _run_child(
    record_id, *, parent="parent", build_id="run_1", lifecycle=ArtifactLifecycleStatus.DRAFT,
    validation=ArtifactValidationStatus.PASSED,
):
    return BuildRecord(
        id=record_id, app_id="app_1", build_family="app_bundle", build_key="app_bundle", version_number=2,
        parent_build_record_id=parent, lineage_root_id="parent", lifecycle_status=lifecycle,
        validation_status=validation, commit_metadata={"metadata": {"build_id": build_id}},
    )


@pytest.mark.parametrize(("status", "reported", "stored", "effect"), [
    # The run reports success, but the store holds no child of it: nothing can be accepted.
    ("validated", None, [], (False, None)),
    # The run reports failure after it stored a child whose validation passed.
    ("failed", None, ["passed"], (True, None)),
    # The run reports success or failure; the store holds a child that is pending or was skipped: unresolved.
    ("planned", None, ["pending"], (True, "validation_pending")),
    ("failed", None, ["skipped"], (True, "validation_skipped")),
    # The run reports success, the store holds only a child whose validation failed: terminal, no change.
    ("validated", None, ["failed"], (False, None)),
    # The run names a child the store does not return.
    ("validated", "child_1", [], (True, "reported_child_missing")),
    ("failed", "child_1", [], (True, "reported_child_missing")),
    # Records of another run, or of another parent, are not this run's output.
    ("validated", None, ["other_run", "other_parent"], (False, None)),
    # A rejected child can never become current.
    ("validated", None, ["archived"], (False, None)),
])
def test_the_store_not_the_run_status_decides_whether_an_inline_run_changed_its_build(status, reported, stored, effect):
    from mozaiksai.hosts import studio

    children = {
        "passed": _run_child("child_1"),
        "pending": _run_child("child_1", validation=ArtifactValidationStatus.PENDING),
        "skipped": _run_child("child_1", validation=ArtifactValidationStatus.SKIPPED),
        "failed": _run_child("child_1", validation=ArtifactValidationStatus.FAILED),
        "other_run": _run_child("child_2", build_id="run_0"),
        "other_parent": _run_child("child_3", parent="sibling"),
        "archived": _run_child("child_4", lifecycle=ArtifactLifecycleStatus.ARCHIVED),
    }
    store = SimpleNamespace(list_run_child_build_records=AsyncMock(return_value=[children[name] for name in stored]))
    result = SimpleNamespace(status=status, metadata={"build_record_id": reported} if reported else {})
    binding = RunBuildBinding(build_registry_id="appreg_1", target_app_id="app_1", build_id="run_1", phase="refinement")
    found = asyncio.run(studio._inline_run_baseline_effect(
        store, app_id="app_1", parent_build_record_id="parent", binding=binding, result=result,
    ))
    assert (found.changed, found.unresolved) == effect


def test_a_run_that_reports_failure_after_storing_a_passed_child_invalidates_the_parent(monkeypatch, tmp_path):
    """The worker stores the child, then its archive identity check fails: the run reports failure, the child stays."""
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}])
    create = flow.store.create_build_record

    async def saved_under_another_digest(**kwargs):
        record = await create(**kwargs)
        manifest = [entry.model_copy(update={"sha256": "0" * 64}) for entry in record.files_manifest]
        flow.store.records[record.id] = record.model_copy(update={"files_manifest": manifest})
        return flow.store.records[record.id]

    flow.store.create_build_record = saved_under_another_digest
    response = flow.trigger({"files": {PAGE: ""}})
    assert response.status_code == 200, response.text
    coding = response.json()["coding_worker"]
    assert coding["status"] == "failed" and coding["error"].startswith("ARTIFACT_PERSISTENCE_FAILED"), coding
    assert "build_record_id" not in coding["metadata"]
    (child,) = (flow.store.records[record_id] for record_id in flow.store.children())
    assert child.validation_status == ArtifactValidationStatus.PASSED and child.lifecycle_status == ArtifactLifecycleStatus.DRAFT
    # A PASSED draft can still be accepted, so the build changed although the run reported failure.
    assert flow.store.invalidations == [{"refs": {"app_bundle": "parent"}, "reason": "change_request:cr_1"}]
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE


def test_a_child_another_run_stored_does_not_make_a_failed_run_a_change(monkeypatch, tmp_path):
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: PRIVILEGED_HANDLER}])
    parent = flow.store.records["parent"]
    flow.store.records["earlier_child"] = parent.model_copy(update={
        "id": "earlier_child", "version_number": 2, "parent_build_record_id": "parent",
        "lifecycle_status": ArtifactLifecycleStatus.DRAFT, "validation_status": ArtifactValidationStatus.PASSED,
        "commit_metadata": parent.commit_metadata.model_copy(update={
            "metadata": {**parent.commit_metadata.metadata, "build_id": "run_earlier"},
        }),
    })
    response = flow.trigger({"files": {HANDLER: ""}})
    assert response.status_code == 200, response.text
    coding = response.json()["coding_worker"]
    assert coding["status"] == "failed" and coding["metadata"]["candidate_admission"]["code"] == "WORKSPACE_HANDLER_EXTENSION_REJECTED"
    # This run stored nothing; the earlier run's child is not its output.
    assert flow.store.children() == ["earlier_child"]
    assert flow.store.invalidations == [] and flow.store.current() == ["parent"]


def test_studio_inferred_handler_scope_returns_the_confirmation_decision(monkeypatch, tmp_path):
    route = _studio_route(monkeypatch, tmp_path, files_payload={}, proposer=_scope_proposer())
    assert route.response.status_code == 200, route.response.text
    body = route.response.json()
    assert body["execution_mode"] == "harness_decision"
    decision = body["harness_decision"]
    assert decision["decision_type"] == "clarify_scope" and decision["selected_paths"] == [HANDLER]
    assert decision["metadata"]["confirmation_required_paths"] == [HANDLER]
    assert "apply_proposed_scope" in [action["action_id"] for action in decision["actions"]]
    route.registry.begin_refinement_run.assert_not_awaited()
    assert route.store.created == []
    # A question changes nothing: the parent stays the app's current build.
    assert route.store.invalidations == [] and route.store.current() == ["parent"]


def test_confirmed_handler_scope_applies_to_the_still_current_build_and_is_accepted(monkeypatch, tmp_path):
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}], proposer=_scope_proposer())
    proposed = flow.trigger({})
    assert proposed.status_code == 200, proposed.text
    decision = proposed.json()
    assert decision["harness_decision"]["decision_type"] == "clarify_scope"
    assert flow.store.invalidations == [] and flow.store.current() == ["parent"]
    assert flow.registry.begun == []
    # The attempt's history is still written: the ChangeRequest and the pending decision.
    assert flow.store.change_requests["cr_1"].router_decision["execution_mode"] == "harness_decision"
    assert flow.session()["pending_harness_decision"]["selected_paths"] == [HANDLER]

    confirmed = flow.confirm(decision)
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["coding_worker"]
    assert result["status"] == "validated", result
    child = flow.store.records[result["metadata"]["build_record_id"]]
    assert child.commit_metadata.metadata["refinement_scope"] == {
        "selected_file_paths": [HANDLER], "scope_origin": "confirmed", "changed_workspace_pack_paths": [HANDLER],
    }
    assert _archive_files(child)[HANDLER] == NORMALIZING_HANDLER
    # The confirmed run stored the child, so the parent is invalidated under the proposal's ChangeRequest.
    assert flow.store.invalidations == [{"refs": {"app_bundle": "parent"}, "reason": "change_request:cr_1"}]
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE
    flow.provider.execute.assert_awaited_once()

    accepted = flow.accept(child.id)
    assert accepted.status_code == 200, accepted.text
    assert flow.store.accepted == [child.id] and flow.store.current() == [child.id]
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE


def _forge_pending_scope(flow, paths, *, build_record_id=None):
    """Plant a pending decision as POST /api/session/decisions/pending does, naming other files or another build."""
    from mozaiksai.core.session.model import PendingDecisionAction, PendingHarnessDecision

    planted = deepcopy({**flow.session()["pending_harness_decision"], "selected_paths": list(paths)})
    if build_record_id is not None:
        planted["trigger_payload"]["refinement_request"]["build_record_id"] = build_record_id
    planted.pop("created_at", None)
    planted["actions"] = [PendingDecisionAction(**action) for action in planted["actions"]]
    asyncio.run(flow.router.for_target("app_1").mark_pending_harness_decision(
        app_id="factory", user_id="demo-user", pending_decision=PendingHarnessDecision(**planted),
    ))


@pytest.mark.parametrize("case", [
    "dismissed", "unconfirmed", "invalid_input", "files_outside_scope", "forged_scope", "forged_build",
])
def test_declined_or_unauthorized_scope_grants_nothing_and_invalidates_nothing(monkeypatch, tmp_path, case):
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}], proposer=_scope_proposer())
    decision = flow.trigger({}).json()
    assert decision["harness_decision"]["decision_type"] == "clarify_scope"
    if case == "dismissed":
        # What POST /api/session/decisions/resolve with accepted=false does.
        asyncio.run(flow.router.for_target("app_1").resolve_pending_harness_decision(
            app_id="factory", user_id="demo-user", decision_id=decision["change_request_id"], accepted=False,
        ))
        response = flow.confirm(decision)
        assert response.status_code == 409 and "no longer current" in response.json()["detail"], response.text
    elif case == "unconfirmed":
        again = flow.trigger({})
        assert again.json()["harness_decision"]["decision_type"] == "clarify_scope"
    elif case == "invalid_input":
        assert flow.confirm(decision, coding_request="files").status_code == 400
        assert flow.confirm(decision, coding_request={"validation_strategy": "unsupported"}).status_code == 400
        # Invalid input never consumed the decision.
        assert flow.session()["pending_harness_decision"]["selected_paths"] == [HANDLER]
    elif case == "files_outside_scope":
        response = flow.confirm(decision, coding_request={"files": {PAGE: ""}})
        assert response.status_code == 409 and "do not match" in response.json()["detail"], response.text
    elif case == "forged_scope":
        # A decision written through the session API is not the scope the server proposed.
        _forge_pending_scope(flow, [PAGE])
        response = flow.confirm(decision, coding_request={"files": {PAGE: ""}})
        assert response.status_code == 409 and "change request" in response.json()["detail"], response.text
        assert flow.session()["pending_harness_decision"]["selected_paths"] == [PAGE]
    else:
        # The same files, but on a build the change request was not proposed for.
        flow.store.records["other_build"] = flow.store.records["parent"].model_copy(
            update={"id": "other_build", "lifecycle_status": ArtifactLifecycleStatus.DRAFT},
        )
        _forge_pending_scope(flow, [HANDLER], build_record_id="other_build")
        response = flow.confirm(decision, build_record_id="other_build")
        assert response.status_code == 409 and "change request" in response.json()["detail"], response.text
        assert flow.store.status("other_build") == ArtifactLifecycleStatus.DRAFT
    assert flow.store.invalidations == [] and flow.store.current() == ["parent"]
    assert flow.registry.begun == [] and flow.store.children() == []
    flow.provider.execute.assert_not_awaited()


def test_an_accepted_change_rejects_older_confirmations_and_candidates(monkeypatch, tmp_path):
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}], proposer=_scope_proposer(),
    )
    older_decision = flow.trigger({}).json()
    assert older_decision["harness_decision"]["decision_type"] == "clarify_scope"
    older, newer = (flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"] for _ in range(2))
    older_id, newer_id = older["metadata"]["build_record_id"], newer["metadata"]["build_record_id"]
    assert flow.accept(newer_id).status_code == 200
    assert flow.store.current() == [newer_id]

    # The older candidate carries the same app-owned code but would revert the newer accepted page.
    stale_candidate = flow.accept(older_id)
    assert stale_candidate.status_code == 409, stale_candidate.text
    assert "earlier build" in stale_candidate.json()["detail"]
    assert flow.store.current() == [newer_id] and flow.store.status(older_id) == ArtifactLifecycleStatus.DRAFT

    # The decision proposed against the old parent is refused before it is consumed or run.
    stale_confirmation = flow.confirm(older_decision)
    assert stale_confirmation.status_code == 409, stale_confirmation.text
    assert "Request the change again" in stale_confirmation.json()["detail"]
    assert flow.session()["pending_harness_decision"]["decision_id"] == older_decision["change_request_id"]
    assert flow.registry.begun == ["run_1", "run_2"] and flow.provider.execute.await_count == 2
    assert flow.store.accepted == [newer_id]
    # The proposal invalidated nothing; each run that stored a reviewable child invalidated the parent.
    assert [item["reason"] for item in flow.store.invalidations] == ["change_request:cr_2", "change_request:cr_3"]


@pytest.mark.parametrize("competitor", ["failed_attempt", "page_child", "accepted_page_child"])
def test_a_run_started_after_confirmation_keeps_the_stale_child_out(monkeypatch, tmp_path, competitor):
    second = {HANDLER: PRIVILEGED_HANDLER} if competitor == "failed_attempt" else {PAGE: _page(1)}
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}, second], proposer=_scope_proposer(),
    )
    decision = flow.trigger({}).json()
    confirmed = flow.confirm(decision).json()["coding_worker"]
    assert confirmed["status"] == "validated", confirmed
    handler_child = confirmed["metadata"]["build_record_id"]
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE

    competing = flow.trigger({"files": dict.fromkeys(second, "")}).json()["coding_worker"]
    if competitor == "failed_attempt":
        assert competing["status"] == "failed" and "build_record_id" not in competing["metadata"]
        assert len(flow.store.invalidations) == 1
    else:
        assert competing["status"] == "validated", competing
        if competitor == "accepted_page_child":
            assert flow.accept(competing["metadata"]["build_record_id"]).status_code == 200

    rejected = flow.accept(handler_child)
    assert rejected.status_code == 409, rejected.text
    assert handler_child not in flow.store.accepted
    assert flow.store.status(handler_child) == ArtifactLifecycleStatus.DRAFT
    # Nothing restores the parent the earlier run invalidated.
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE
    assert flow.store.current() == (
        [competing["metadata"]["build_record_id"]] if competitor == "accepted_page_child" else []
    )


def test_confirmation_is_single_use_and_its_child_is_accepted_once(monkeypatch, tmp_path):
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}], proposer=_scope_proposer())
    decision = flow.trigger({}).json()
    first = flow.confirm(decision)
    assert first.status_code == 200 and first.json()["coding_worker"]["status"] == "validated", first.text
    replay = flow.confirm(decision)
    assert replay.status_code == 409, replay.text
    assert flow.provider.execute.await_count == 1 and flow.registry.begun == ["run_1"]
    child = first.json()["coding_worker"]["metadata"]["build_record_id"]
    assert flow.store.children() == [child] and len(flow.store.invalidations) == 1
    assert flow.accept(child).status_code == 200
    duplicate = flow.accept(child)
    assert duplicate.status_code == 409, duplicate.text
    assert flow.store.accepted == [child]


def test_an_orphaned_child_of_an_uncertain_run_is_not_replayed_or_accepted(monkeypatch, tmp_path):
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}], proposer=_scope_proposer())
    decision = flow.trigger({}).json()
    # The run stores its child, then loses the registry's compare-and-set while registering it.
    flow.registry.superseded = True
    lost = flow.confirm(decision)
    assert lost.status_code == 503, lost.text
    assert len(flow.store.children()) == 1
    orphan = flow.store.children()[0]
    assert flow.store.status(orphan) == ArtifactLifecycleStatus.DRAFT
    # The run raised, so nothing was invalidated, and the consumed decision is not replayed.
    assert flow.store.invalidations == [] and flow.store.current() == ["parent"]
    flow.registry.superseded = False
    replay = flow.confirm(decision)
    assert replay.status_code == 409, replay.text
    assert flow.provider.execute.await_count == 1
    # The registry's run chain never named the orphan, so it cannot be accepted as if registered.
    refused = flow.accept(orphan)
    assert refused.status_code == 409 and "newer build run" in refused.json()["detail"], refused.text
    assert flow.store.accepted == [] and flow.store.current() == ["parent"]


def test_sibling_candidates_cannot_both_become_current(monkeypatch, tmp_path):
    import httpx

    from mozaiksai.hosts import studio

    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}])
    siblings = [
        flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["metadata"]["build_record_id"] for _ in range(2)
    ]

    async def accept_both():
        transport = httpx.ASGITransport(app=studio.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await asyncio.gather(*(
                client.post(f"/api/studio/build/artifacts/{record_id}/accept", params={"build_registry_id": "appreg_1"})
                for record_id in siblings
            ))

    responses = asyncio.run(accept_both())
    assert sorted(response.status_code for response in responses) == [200, 409], [r.text for r in responses]
    assert len(flow.store.accepted) == 1 and flow.store.current() == flow.store.accepted
    loser = (set(siblings) - set(flow.store.accepted)).pop()
    assert flow.store.status(loser) == ArtifactLifecycleStatus.DRAFT


def _low_confidence_page_proposer(paths=(PAGE,)):
    """An inferred page scope Studio asks about first (low confidence), outside app-owned pack code."""
    return SimpleNamespace(
        propose=AsyncMock(return_value=ScopeProposal(
            resolution="scoped_files", selected_paths=list(paths), rationale="The board lives here.",
            confidence=0.6, clarification_question=None, signals=["board"],
        )),
        materialize_files=AsyncMock(side_effect=lambda **kwargs: {path: _baseline(app_id="app_1")[path] for path in paths}),
    )


def test_a_decision_proposed_for_an_already_stale_build_can_be_confirmed(monkeypatch, tmp_path):
    """The proposal saves the build's state on its ChangeRequest; an unchanged STALE build is still confirmable."""
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}], proposer=_low_confidence_page_proposer(),
    )
    earlier = flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["metadata"]["build_record_id"]
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE and flow.store.current() == []
    decision = flow.trigger({}).json()
    assert decision["harness_decision"]["decision_type"] == "clarify_scope", decision
    saved = flow.store.change_requests[decision["change_request_id"]].router_decision["baseline_state"]
    assert saved["build_record_id"] == "parent" and saved["lifecycle_status"] == "stale"
    assert saved["invalidation_reason"] == "change_request:cr_1"

    confirmed = flow.confirm(decision)
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["coding_worker"]
    assert result["status"] == "validated", result
    child = result["metadata"]["build_record_id"]
    assert flow.store.records[child].commit_metadata.metadata["refinement_scope"]["scope_origin"] == "confirmed"
    assert flow.accept(child).status_code == 200
    assert flow.store.current() == [child] and flow.store.status(earlier) == ArtifactLifecycleStatus.DRAFT


@pytest.mark.parametrize("case", [
    "restaled_after_proposal", "newer_current_build", "removed", "current_lookup_failed", "no_baseline_state",
])
def test_a_confirmation_is_refused_only_when_its_build_changed_or_was_replaced(monkeypatch, tmp_path, case):
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}, {PAGE: _page(3)}],
        proposer=_low_confidence_page_proposer(),
    )
    earlier = flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["metadata"]["build_record_id"]
    decision = flow.trigger({}).json()
    assert decision["harness_decision"]["decision_type"] == "clarify_scope", decision
    change = flow.store.change_requests[decision["change_request_id"]]
    expected = None
    if case == "restaled_after_proposal":
        # Another run stores a reviewable child of the same build after the proposal.
        assert flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["status"] == "validated"
        expected = "has changed since the proposal"
    elif case == "newer_current_build":
        # A child made before the proposal is accepted after it, so a newer build is current.
        assert flow.accept(earlier).status_code == 200
        expected = "A newer build of this app was accepted"
    elif case == "removed":
        # The build itself was archived after the proposal: it is no longer a baseline at all.
        flow.store.records["parent"] = flow.store.records["parent"].model_copy(
            update={"lifecycle_status": ArtifactLifecycleStatus.ARCHIVED},
        )
        expected = "was rejected or removed"
    elif case == "current_lookup_failed":
        # The family's CURRENT records cannot be read: unresolved, never "nothing is current".
        flow.store.list_build_records = AsyncMock(side_effect=RuntimeError("store unavailable"))
        expected = "could not be checked against the app's current build"
    else:
        # A ChangeRequest with no saved state of its build (one recorded before Studio saved it) cannot
        # show whether the build changed, even when the build was never written since: refused, never
        # compared by some other rule.
        del change.router_decision["baseline_state"]
        assert flow.store.records["parent"].updated_at < datetime.now(UTC)
        expected = "saved no state of its build"
    response = flow.confirm(decision)
    if expected is None:
        assert response.status_code == 200 and response.json()["coding_worker"]["status"] == "validated", response.text
        return
    assert response.status_code == 409 and expected in response.json()["detail"], response.text
    assert "Request the change again" in response.json()["detail"]
    # Refused before the decision was used: it is still pending, and no run began for it.
    assert flow.session()["pending_harness_decision"]["decision_id"] == decision["change_request_id"]
    assert flow.provider.execute.await_count == (2 if case == "restaled_after_proposal" else 1)


@pytest.mark.parametrize("retry", ["explicit", "confirmed"])
def test_rejecting_a_handler_draft_leaves_its_stale_parent_open_to_an_authorized_retry(monkeypatch, tmp_path, retry):
    """Rejection archives only the draft, so its parent stays STALE and no build is CURRENT.

    The parent's stale marker is this app's own handler run, whose draft is now
    rejected, and nothing else is open, in flight or changed, so the retried
    handler change, bound to the same parent, is admitted without any record
    being made current again, and its child is accepted.
    """
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}, {HANDLER: NORMALIZING_HANDLER}],
        proposer=_scope_proposer(),
    )
    first = flow.confirm(flow.trigger({}).json()).json()["coding_worker"]
    assert first["status"] == "validated", first
    rejected = first["metadata"]["build_record_id"]
    assert flow.reject(rejected).status_code == 200
    assert flow.store.status(rejected) == ArtifactLifecycleStatus.ARCHIVED
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE and flow.store.current() == []

    if retry == "explicit":
        response = flow.trigger({"files": {HANDLER: ""}})
    else:
        again = flow.trigger({}).json()
        assert again["harness_decision"]["decision_type"] == "clarify_scope", again
        response = flow.confirm(again)
    assert response.status_code == 200, response.text
    result = response.json()["coding_worker"]
    assert result["status"] == "validated", result
    child = flow.store.records[result["metadata"]["build_record_id"]]
    assert child.parent_build_record_id == "parent" and _archive_files(child)[HANDLER] == NORMALIZING_HANDLER
    assert child.commit_metadata.metadata["refinement_scope"]["scope_origin"] == retry
    # Admission changed no record: the parent is still STALE, now under the retry's change request.
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE and flow.store.current() == []
    accepted = flow.accept(child.id)
    assert accepted.status_code == 200, accepted.text
    assert flow.store.current() == [child.id] and flow.store.status(rejected) == ArtifactLifecycleStatus.ARCHIVED


def test_a_pending_reviewable_child_keeps_its_stale_parent_closed_to_another_pack_change(monkeypatch, tmp_path):
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}, {HANDLER: NORMALIZING_HANDLER}],
        proposer=_scope_proposer(),
    )
    pending = flow.confirm(flow.trigger({}).json()).json()["coding_worker"]["metadata"]["build_record_id"]
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE and flow.store.current() == []
    blocked = flow.trigger({"files": {HANDLER: ""}})
    assert blocked.status_code == 200, blocked.text
    coding = blocked.json()["coding_worker"]
    assert coding["status"] == "failed" and coding["metadata"]["candidate_admission"]["code"] == "STALE_REFINEMENT_BASELINE"
    # Admitted before validation: nothing was validated or stored, and nothing more was invalidated.
    assert flow.validate.await_count == 1 and flow.store.children() == [pending]
    assert len(flow.store.invalidations) == 1


@pytest.mark.parametrize("case", ["never_accepted_draft", "newer_accepted_build", "superseded"])
def test_a_stale_parent_is_admitted_only_as_the_latest_accepted_build(monkeypatch, tmp_path, case):
    pages = [{PAGE: _page(1)}] if case == "superseded" else [{PAGE: _page(1)}, {PAGE: _page(2)}]
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[*pages, {HANDLER: NORMALIZING_HANDLER}, {HANDLER: NORMALIZING_HANDLER}],
        accepted="newer_build" if case == "superseded" else "parent",
    )

    def page_child(build_record_id):
        result = flow.trigger({"files": {PAGE: ""}}, build_record_id=build_record_id).json()["coding_worker"]
        assert result["status"] == "validated", result
        return result["metadata"]["build_record_id"]

    def handler_attempt(build_record_id):
        response = flow.trigger({"files": {HANDLER: ""}}, build_record_id=build_record_id)
        assert response.status_code == 200, response.text
        return response.json()["coding_worker"]

    if case == "superseded":
        # The parent was replaced by a newer accepted build, which a rejected change then left stale.
        assert flow.store.status("parent") == ArtifactLifecycleStatus.SUPERSEDED
        assert flow.reject(page_child("newer_build")).status_code == 200
        refused, admitted = "parent", None
    else:
        first = page_child("parent")
        if case == "newer_accepted_build":
            # Accepted after the parent, then left stale by a rejected change of its own.
            assert flow.accept(first).status_code == 200
        assert flow.reject(page_child(first)).status_code == 200
        refused, admitted = (first, None) if case == "never_accepted_draft" else ("parent", first)
    assert flow.store.current() == []
    attempt = handler_attempt(refused)
    assert attempt["status"] == "failed", attempt
    assert attempt["metadata"].get("candidate_admission", {}).get("code") == "STALE_REFINEMENT_BASELINE", attempt
    # Each case is refused for its own reason, not only by some other check. Studio reads the
    # registry before a run only from a STALE build, so a SUPERSEDED parent is refused at that read.
    _assert_stale_refusal(attempt, {
        "never_accepted_draft": "nothing shows the parent was ever accepted",
        "newer_accepted_build": f"build {admitted} was accepted after the parent",
        "superseded": "no server read of the app registry was made before this run began",
    }[case])
    if admitted is not None:
        # The newer accepted build is the one a pack change is admitted against.
        retried = handler_attempt(admitted)
        assert retried["status"] == "validated", retried
        assert flow.store.records[retried["metadata"]["build_record_id"]].parent_build_record_id == admitted


def _with_marker(store, record_id, marker):
    """Replace the record's acceptance marker (None removes it), as a record written by another writer would carry."""
    record = store.records[record_id]
    metadata = {key: value for key, value in record.commit_metadata.metadata.items() if key != "acceptance"}
    if marker is not None:
        metadata["acceptance"] = marker
    store.records[record_id] = record.model_copy(update={
        "commit_metadata": record.commit_metadata.model_copy(update={"metadata": metadata}),
    })


#: What acceptance wrote before its evidence named the accepted build: no app, family, key, record or transition.
UNSCOPED_MARKER = {"accepted_by": "demo-user", "accepted_at": PARENT_ACCEPTED_AT}


def test_a_stale_build_accepted_before_evidence_named_it_is_not_shown_accepted_even_with_an_accepted_session(
    monkeypatch, tmp_path,
):
    """Only acceptance evidence naming the build shows a stale build was accepted.

    A build accepted before acceptance named its build keeps that older marker
    and an ACCEPTED refinement session, but neither is evidence: the stale
    build is refused as unverifiable, with the way forward, before validation.
    """
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}, {HANDLER: NORMALIZING_HANDLER}])
    first = flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["metadata"]["build_record_id"]
    assert flow.accept(first).status_code == 200
    evidence = flow.store.records[first].commit_metadata.metadata["acceptance"]
    assert evidence["build_record_id"] == first and evidence["transition"] == "studio_accept"
    _with_marker(flow.store, first, UNSCOPED_MARKER)
    sessions = [session for session in flow.store.refinement_sessions if session.get("result_build_record_id") == first]
    assert [session["status"] for session in sessions] == [RefinementSessionStatus.ACCEPTED]
    second = flow.trigger({"files": {PAGE: ""}}, build_record_id=first).json()["coding_worker"]["metadata"]["build_record_id"]
    assert flow.reject(second).status_code == 200
    attempt = flow.trigger({"files": {HANDLER: ""}}, build_record_id=first).json()["coding_worker"]
    _assert_stale_refusal(attempt, "nothing shows the parent was ever accepted: no acceptance evidence names it")
    assert "accept a new build of the app" in attempt["metadata"]["candidate_admission"]["stale_baseline_refusal"]
    assert flow.validate.await_count == 2


@pytest.mark.parametrize("marker", ["none", "unscoped", "copied", "other_app", "other_key", "other_record"])
def test_a_first_build_is_not_shown_accepted_once_stale_by_anything_but_evidence_naming_it(monkeypatch, tmp_path, marker):
    """A stale first build is accepted work only by the evidence Studio's acceptance wrote on it.

    Its pack selection, a marker an earlier genesis save wrote, and evidence
    copied from another record, or naming another app, key or record, show
    nothing: the pack-code retry from it is refused before validation.
    """
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 2)
    _rejected_handler_draft(flow)
    own = flow.store.records["parent"].commit_metadata.metadata["acceptance"]
    _with_marker(flow.store, "parent", {
        "none": None,
        "unscoped": UNSCOPED_MARKER,
        "copied": _acceptance_evidence("another_build", app_id="app_1", accepted_by="demo-user"),
        "other_app": {**own, "app_id": "another_app"},
        "other_key": {**own, "build_key": "app_intelligence_workspace"},
        "other_record": {**own, "build_record_id": "child_1"},
    }[marker])
    parent = flow.store.records["parent"]
    assert parent.parent_build_record_id is None and parent.commit_metadata.metadata["capability_packs"] == _packs()
    _assert_stale_refusal(_handler_attempt(flow), "nothing shows the parent was ever accepted")
    assert flow.validate.await_count == 1


def test_a_first_build_is_a_draft_until_the_owner_accepts_it_through_studio(monkeypatch, tmp_path):
    """AppGenerator saves its first build as a draft; Studio's acceptance is the transition that writes the evidence.

    Before acceptance no reader takes the draft for accepted: it cannot be
    promoted, it is no accepted base, and an app-owned pack change made from
    it is refused. Accepting it through the Studio route as the owner makes it
    current and writes evidence naming its app, build family and key, record,
    principal, time and transition. Refinement then uses that accepted
    baseline: a confirmed handler change, and after its draft is rejected, an
    explicit retry from the stale accepted baseline, accepted in turn.
    """
    from mozaiksai.control_plane.implementations.coding_worker import (
        _acceptance_of,
        _accepted_at,
        newer_accepted_work,
    )

    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 3, proposer=_scope_proposer(), first_build=True,
    )
    draft = flow.store.records["parent"]
    assert draft.parent_build_record_id is None and draft.lifecycle_status == ArtifactLifecycleStatus.DRAFT
    assert "acceptance" not in draft.commit_metadata.metadata
    assert _accepted_at(draft) is None and _acceptance_of(draft) is None
    assert asyncio.run(newer_accepted_work(flow.store, app_id="app_1", record=draft)) is None
    refused = flow.promote("parent")
    assert refused.status_code == 409 and "Only accepted current" in refused.json()["detail"], refused.text
    # A handler change from the unaccepted draft is refused at admission, before validation.
    blocked = _handler_attempt(flow)
    assert blocked["metadata"]["candidate_admission"]["code"] == "STALE_REFINEMENT_BASELINE", blocked
    assert flow.validate.await_count == 0 and flow.store.status("parent") == ArtifactLifecycleStatus.DRAFT

    before = datetime.now(UTC)
    accepted = flow.accept("parent")
    assert accepted.status_code == 200, accepted.text
    record = flow.store.records["parent"]
    assert record.lifecycle_status == ArtifactLifecycleStatus.CURRENT and flow.store.accepted == ["parent"]
    evidence = dict(record.commit_metadata.metadata["acceptance"])
    accepted_at = datetime.fromisoformat(evidence.pop("accepted_at").replace("Z", "+00:00"))
    assert before <= accepted_at <= datetime.now(UTC)
    # accepted_by is the request's authenticated principal: sign-in is off here, so the development principal.
    assert evidence == {
        "app_id": "app_1", "build_family": "app_bundle", "build_key": "app_bundle", "build_record_id": "parent",
        "accepted_by": "anonymous", "transition": "studio_accept",
    }
    assert isinstance(_accepted_at(record), datetime)

    # Confirmation: the inferred handler scope waits for the owner, who confirms it on the accepted baseline.
    asked = flow.trigger({}).json()
    assert asked["harness_decision"]["decision_type"] == "clarify_scope", asked
    confirmed = flow.confirm(asked).json()["coding_worker"]
    assert confirmed["status"] == "validated", confirmed
    # Retry: the confirmed draft is rejected, and an explicit retry is admitted from the stale accepted baseline.
    assert flow.reject(confirmed["metadata"]["build_record_id"]).status_code == 200
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE and flow.store.current() == []
    retry = _handler_attempt(flow)
    assert retry["status"] == "validated", (retry.get("error"), retry["metadata"].get("candidate_admission"))
    child = retry["metadata"]["build_record_id"]
    assert flow.store.records[child].parent_build_record_id == "parent"
    assert flow.accept(child).status_code == 200
    assert flow.store.current() == [child]
    assert flow.store.records[child].commit_metadata.metadata["acceptance"]["build_record_id"] == child


def test_a_first_build_draft_is_not_accepted_over_the_apps_accepted_build(monkeypatch, tmp_path):
    """A parentless draft goes through the same lineage guard: with an accepted build current, it is refused."""
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[])
    parent = flow.store.records["parent"]
    metadata = {key: value for key, value in parent.commit_metadata.metadata.items() if key != "acceptance"}
    flow.store.records["regenerated"] = parent.model_copy(update={
        "id": "regenerated", "version_number": 2, "lifecycle_status": ArtifactLifecycleStatus.DRAFT,
        "commit_metadata": parent.commit_metadata.model_copy(update={"metadata": metadata}),
    })
    refused = flow.accept("regenerated")
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "This version is a new build with no parent, made outside the line of the app's current build" in detail
    assert "build parent is the current build" in detail
    assert flow.store.accepted == [] and flow.store.current() == ["parent"]
    assert flow.store.status("regenerated") == ArtifactLifecycleStatus.DRAFT


@pytest.mark.parametrize("saved_state", ["draft", "current"])
def test_acceptance_reads_a_stale_build_without_evidence_from_the_state_its_change_request_saved(
    monkeypatch, tmp_path, saved_state,
):
    """A stale build outside the version's lineage, without evidence, is decided from its change request's saved state.

    ``draft``: it was a draft never accepted, so the version is accepted.
    ``current``: it was accepted at a time nothing records (accepted before
    acceptance named its build), so acceptance cannot be verified: 409 with
    the cause and the way forward, and nothing is accepted.
    """
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}])
    child = _page_draft(flow, "parent")
    later = datetime.now(UTC) + timedelta(seconds=5)
    flow.store.records["elsewhere"] = BuildRecord(
        id="elsewhere", app_id="app_1", build_family="app_bundle", build_key="app_bundle", version_number=9,
        parent_build_record_id="older", lineage_root_id="older", lifecycle_status=ArtifactLifecycleStatus.STALE,
        invalidation_reason="change_request:cr_elsewhere", created_at=later, updated_at=later,
        commit_metadata={"metadata": {"acceptance": dict(UNSCOPED_MARKER)}},
    )
    flow.store.change_requests["cr_elsewhere"] = SimpleNamespace(
        id="cr_elsewhere", app_id="app_1", build_record_id="elsewhere", created_by_user_id="demo-user",
        router_decision={"execution_mode": "coding_worker", "baseline_state": {
            "build_record_id": "elsewhere", "lifecycle_status": saved_state, "invalidation_reason": None,
        }},
    )
    response = flow.accept(child)
    if saved_state == "draft":
        assert response.status_code == 200, response.text
        assert flow.store.current() == [child]
        return
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "This version's lineage could not be verified against the app's accepted builds" in detail
    assert (
        "build elsewhere is stale, it carries no acceptance evidence naming it, and change request cr_elsewhere "
        "that staled it began while it was current, not a draft, so its acceptance cannot be verified; refine the "
        "app from build elsewhere or a build made from it instead"
    ) in detail
    assert flow.store.accepted == [] and flow.store.status(child) == ArtifactLifecycleStatus.DRAFT


def test_a_child_carrying_its_parents_evidence_is_not_an_accepted_base(monkeypatch, tmp_path):
    """Evidence copied into a never-accepted draft does not make it accepted, so older work is still refused.

    ``copied`` is a draft of the parent made after its sibling was accepted.
    If the parent's evidence copied into it counted, ``older`` (a draft of
    ``copied``) would be ordered from ``copied``, which was made after that
    acceptance, so the sibling's acceptance would be missed. Evidence counts
    only on the record it names, so the base is the parent and the acceptance
    is refused.
    """
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}, {PAGE: _page(3)}, {PAGE: _page(4)}])
    sibling = flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["metadata"]["build_record_id"]
    assert flow.accept(sibling).status_code == 200
    _page_draft(flow, sibling)  # the accepted sibling is marked stale by a draft of its own
    copied = flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["metadata"]["build_record_id"]
    _with_marker(flow.store, copied, deepcopy(flow.store.records["parent"].commit_metadata.metadata["acceptance"]))
    older = _page_draft(flow, copied)  # refining the draft marks it stale
    assert flow.store.status(copied) == ArtifactLifecycleStatus.STALE
    refused = flow.accept(older)
    assert refused.status_code == 409, refused.text
    assert f"build {sibling} was accepted after build parent" in refused.json()["detail"]
    assert flow.store.status(older) == ArtifactLifecycleStatus.DRAFT


def test_an_imported_genesis_build_without_evidence_keeps_its_stale_paths_blocked_naming_the_owner(monkeypatch, tmp_path):
    """Brownfield Genesis acceptance (owned by Genesis import) writes no acceptance evidence yet.

    Once such a build is stale, a pack-code retry from it is refused with a
    diagnostic that names that dependency; nothing infers its acceptance.
    """
    from mozaiksai.hosts import studio

    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 2)
    monkeypatch.setattr(studio, "require_accepted_genesis_baseline", AsyncMock(return_value=None))
    _with_marker(flow.store, "parent", None)
    record = flow.store.records["parent"]
    flow.store.records["parent"] = record.model_copy(update={"commit_metadata": record.commit_metadata.model_copy(update={
        "metadata": {**record.commit_metadata.metadata, "bundle_mode": "brownfield_genesis_import"},
    })})
    _rejected_handler_draft(flow)
    attempt = _handler_attempt(flow)
    _assert_stale_refusal(attempt, "build parent is an imported Genesis build")
    assert "accept_genesis_build_record, owned by Genesis import" in (
        attempt["metadata"]["candidate_admission"]["stale_baseline_refusal"]
    )
    assert flow.validate.await_count == 1


def test_a_current_build_outside_its_lineage_still_refuses_the_retried_handler_child_at_acceptance(monkeypatch, tmp_path):
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}, {HANDLER: NORMALIZING_HANDLER}, {PAGE: _page(1)}],
        proposer=_scope_proposer(),
    )
    rejected = flow.confirm(flow.trigger({}).json()).json()["coding_worker"]["metadata"]["build_record_id"]
    assert flow.reject(rejected).status_code == 200
    retried = flow.trigger({"files": {HANDLER: ""}}).json()["coding_worker"]
    assert retried["status"] == "validated", retried
    # Meanwhile a UI change from the same parent is accepted: it is the app's current build now.
    page = flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["metadata"]["build_record_id"]
    assert flow.accept(page).status_code == 200
    refused = flow.accept(retried["metadata"]["build_record_id"])
    assert refused.status_code == 409, refused.text
    assert "no longer the app's accepted current build" in refused.json()["detail"]
    assert flow.store.current() == [page]
    assert flow.store.status(retried["metadata"]["build_record_id"]) == ArtifactLifecycleStatus.DRAFT


def _handler_attempt(flow, build_record_id="parent", **coding_request):
    response = flow.trigger({"files": {HANDLER: ""}, **coding_request}, build_record_id=build_record_id)
    assert response.status_code == 200, response.text
    return response.json()["coding_worker"]


def _assert_stale_refusal(attempt, expected):
    admission = attempt["metadata"].get("candidate_admission") or {}
    assert attempt["status"] == "failed" and admission.get("code") == "STALE_REFINEMENT_BASELINE", attempt
    assert expected in admission["stale_baseline_refusal"], admission
    assert "build_record_id" not in attempt["metadata"]


def _rejected_handler_draft(flow):
    """An explicit handler change stores a reviewable child, which staled its parent; the owner rejects it."""
    first = _handler_attempt(flow)
    assert first["status"] == "validated", first
    rejected = first["metadata"]["build_record_id"]
    assert flow.reject(rejected).status_code == 200
    assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE and flow.store.current() == []
    return rejected


def _workflow_proposer():
    """A scope proposal that the change is broader than a patch: Studio offers the recommended workflow."""
    return SimpleNamespace(
        propose=AsyncMock(return_value=ScopeProposal(
            resolution="workflow", selected_paths=[], rationale="This reaches past one file.",
            confidence=0.9, clarification_question=None, signals=["messages"],
        )),
        materialize_files=AsyncMock(side_effect=lambda **kwargs: dict.fromkeys(kwargs["selected_paths"], TEMPLATE_HANDLER)),
    )


def _upstream(
    record_id, *, family="design_docs", lifecycle=ArtifactLifecycleStatus.CURRENT, created_at=None, updated_at=None,
    acceptance=None,
):
    """An input record, saved as given: with no ``updated_at``, last written when it was created."""
    times = {} if created_at is None else {"created_at": created_at, "updated_at": updated_at or created_at}
    return BuildRecord(
        id=record_id, app_id="app_1", build_family=family, build_key=family, version_number=1,
        lineage_root_id=record_id, lifecycle_status=lifecycle, **times,
        commit_metadata={"metadata": {} if acceptance is None else {"acceptance": acceptance}},
    )


def test_each_rejected_handler_draft_leaves_its_parent_open_to_the_next_bound_retry(monkeypatch, tmp_path):
    """Rejection after rejection: each retry's stale marker is the app's own closed run, followed back to CURRENT.

    The second retry's change request began while the parent was already
    stale under the first one, so admission follows that saved state back to
    the change request that began while the parent was CURRENT; every link
    ran inline, stored a draft and had it rejected.
    """
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 3)
    first = _rejected_handler_draft(flow)
    second = _handler_attempt(flow)
    assert second["status"] == "validated", second
    assert flow.store.records["parent"].invalidation_reason == "change_request:cr_2"
    assert flow.store.change_requests["cr_2"].router_decision["baseline_state"]["invalidation_reason"] == "change_request:cr_1"
    assert flow.reject(second["metadata"]["build_record_id"]).status_code == 200
    third = _handler_attempt(flow)
    assert third["status"] == "validated", third
    child = flow.store.records[third["metadata"]["build_record_id"]]
    assert child.parent_build_record_id == "parent"
    assert flow.accept(child.id).status_code == 200
    assert flow.store.current() == [child.id]
    assert {flow.store.status(record) for record in (first, second["metadata"]["build_record_id"])} == {
        ArtifactLifecycleStatus.ARCHIVED,
    }


_UNATTRIBUTABLE_CASES = {
    # Competing work: another run of the app is building as this run begins, even if the client claims otherwise.
    "run_in_flight": "another run of this app was building when this run began",
    "registry_unreadable": "the app registry could not be read before this run began",
    # Another unresolved child, from another run, or open further down the lineage.
    "open_pending_child": "made from the parent is draft with validation pending, not rejected",
    "open_descendant": "made further down from the parent, is still open",
    # A draft refined in review is stale and can no longer be rejected, but a workflow launched from it is
    # unresolved for good: only a newly accepted build lets app-owned code change again.
    "workflow_from_refined_draft": (
        "build refined_draft made from the parent is stale, and change request cr_from_draft made from it "
        "may have launched a workflow (workflow), whose outcome is not on record; accept a new build of the app"
    ),
    # A newer accepted application: accepted after the parent, outside its lineage, and since left stale.
    "accepted_after_parent": "build accepted_elsewhere was accepted after the parent",
    # A stale build outside the lineage, without evidence, that the state its change request saved shows was
    # current: accepted at a time nothing records, so it is never passed over as never accepted.
    "stale_unverified_elsewhere": (
        "build unverified_elsewhere is stale, it carries no acceptance evidence naming it, and change request "
        "cr_unverified that staled it began while it was current, not a draft, so its acceptance cannot be verified"
    ),
    # The same for a stale build made from the parent: open, not closed.
    "stale_unverified_child": (
        "build unverified_child made from the parent is stale, it carries no acceptance evidence naming it, and change "
        "request cr_unverified that staled it began while it was current"
    ),
    # Evidence names the parent, but its time cannot be read.
    "unknown_acceptance_time": "the parent's acceptance time is unknown",
    # The marker cannot be attributed to this app's own closed run. A change request recorded before change
    # requests saved their build's state fails closed, naming the cause and the way forward.
    "no_saved_state": (
        "saved no state of it when it began: it was recorded before change requests saved the state of their "
        "build, so what it changed cannot be resolved; accept a new build of the app"
    ),
    "no_run_on_record": "has no inline run on record",
    "another_user": "was made by another user",
    "stale_before_it_began": "the parent was already stale when change request cr_1 began",
    # The saved state is of another build: the cause named is that, not a change request recorded before states.
    "state_of_another_build": (
        "change request cr_1 that invalidated the parent saved the state of build another_build, not of the parent"
    ),
    # Genuine upstream invalidation: an input changed after the parent was accepted, or is stale.
    "input_accepted_since": "input design_docs dd_2 became current after the parent was accepted",
    # Saved before the parent was accepted but made current (no evidence) only after: its last write decides.
    "input_made_current_since": "input design_docs dd_2 became current after the parent was accepted",
    "input_stale": "input design_docs is stale with no current build",
    # Evidence names the input, but its time cannot be read: when it became current is unknown.
    "input_current_since_unknown": "when input design_docs dd_2 became current is unknown",
    "recorded_input_replaced": "input design_docs dd_1 the parent was built from is no longer current",
    # Unreadable or unbounded records refuse.
    "lookup_error": "could not be read (RuntimeError)",
    "scan_bound": "the build family has more than 1 records to check",
}


@pytest.mark.parametrize("case", sorted(_UNATTRIBUTABLE_CASES))
def test_a_rejected_drafts_stale_parent_is_refused_unless_nothing_else_is_open_in_flight_or_changed(
    monkeypatch, tmp_path, case,
):
    """Each condition of stale-parent admission refuses on its own, before validation, changing no record."""
    from mozaiksai.control_plane.implementations import coding_worker

    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 2)
    rejected = _rejected_handler_draft(flow)
    store, parent = flow.store, flow.store.records["parent"]
    change = store.change_requests["cr_1"]
    later = datetime.now(UTC) + timedelta(seconds=5)
    extra = {}
    if case == "run_in_flight":
        flow.registry.record["lifecycle_state"] = "building"
        extra = {"registry_run_state": "idle"}  # a client value is not the server's read
    elif case == "registry_unreadable":
        reads = []

        async def unreadable_at_run_start(**kwargs):
            # The request's scope check reads the registry first; the read before the run fails.
            reads.append(kwargs)
            if len(reads) == 2:
                raise RuntimeError("registry unavailable")
            return {"app": deepcopy(flow.registry.record)}

        flow.registry.get_app_record.side_effect = unreadable_at_run_start
    elif case == "open_pending_child":
        store.records["pending_child"] = _run_child(
            "pending_child", build_id="run_other", validation=ArtifactValidationStatus.PENDING,
        )
    elif case == "open_descendant":
        store.records["grandchild"] = _run_child("grandchild", parent=rejected, build_id="run_other")
    elif case == "workflow_from_refined_draft":
        # Staled by that workflow route, whose change request kept the draft's state when it began.
        store.records["refined_draft"] = _run_child(
            "refined_draft", build_id="run_other", lifecycle=ArtifactLifecycleStatus.STALE,
        ).model_copy(update={"invalidation_reason": "change_request:cr_from_draft"})
        store.change_requests["cr_from_draft"] = SimpleNamespace(
            id="cr_from_draft", app_id="app_1", build_record_id="refined_draft", created_by_user_id="demo-user",
            router_decision={"execution_mode": "workflow", "baseline_state": {
                "build_record_id": "refined_draft", "lifecycle_status": "draft", "invalidation_reason": None,
            }},
        )
    elif case == "accepted_after_parent":
        # Accepted while nothing was CURRENT (by an acceptance made before this check), from a build
        # outside the parent's lineage, then left stale by a refinement of its own: CURRENT shows nothing.
        store.records["accepted_elsewhere"] = BuildRecord(
            id="accepted_elsewhere", app_id="app_1", build_family="app_bundle", build_key="app_bundle",
            version_number=9, parent_build_record_id="older", lineage_root_id="older",
            lifecycle_status=ArtifactLifecycleStatus.STALE, validation_status=ArtifactValidationStatus.PASSED,
            commit_metadata={"metadata": {"acceptance": _acceptance_evidence(
                "accepted_elsewhere", app_id="app_1", accepted_by="demo-user", accepted_at=later.isoformat(),
            )}},
            created_at=later, updated_at=later + timedelta(seconds=1),
        )
    elif case in {"stale_unverified_elsewhere", "stale_unverified_child"}:
        # The child is saved under another build key, so only the check of what was made from the parent reads it.
        record_id = "unverified_elsewhere" if case == "stale_unverified_elsewhere" else "unverified_child"
        store.records[record_id] = BuildRecord(
            id=record_id, app_id="app_1", build_family="app_bundle", version_number=9,
            build_key="app_bundle" if case == "stale_unverified_elsewhere" else "app_bundle_variant",
            parent_build_record_id="older" if case == "stale_unverified_elsewhere" else "parent",
            lineage_root_id="older", lifecycle_status=ArtifactLifecycleStatus.STALE,
            invalidation_reason="change_request:cr_unverified", created_at=later, updated_at=later + timedelta(seconds=1),
        )
        store.change_requests["cr_unverified"] = SimpleNamespace(
            id="cr_unverified", app_id="app_1", build_record_id=record_id, created_by_user_id="demo-user",
            router_decision={"execution_mode": "coding_worker", "baseline_state": {
                "build_record_id": record_id, "lifecycle_status": "current", "invalidation_reason": None,
            }},
        )
    elif case == "unknown_acceptance_time":
        evidence = {**parent.commit_metadata.metadata["acceptance"], "accepted_at": "not a time"}
        store.records["parent"] = parent = parent.model_copy(update={"commit_metadata": parent.commit_metadata.model_copy(
            update={"metadata": {**parent.commit_metadata.metadata, "acceptance": evidence}},
        )})
    elif case == "state_of_another_build":
        change.router_decision["baseline_state"] = {
            **change.router_decision["baseline_state"], "build_record_id": "another_build",
        }
    elif case == "no_saved_state":
        del change.router_decision["baseline_state"]
    elif case == "no_run_on_record":
        store.refinement_sessions.clear()
    elif case == "another_user":
        change.created_by_user_id = "another-user"
    elif case == "stale_before_it_began":
        change.router_decision["baseline_state"] = {
            **change.router_decision["baseline_state"], "lifecycle_status": "stale", "invalidation_reason": "manual",
        }
    elif case == "input_accepted_since":
        store.records["dd_2"] = _upstream("dd_2", created_at=later)
    elif case == "input_made_current_since":
        store.records["dd_2"] = _upstream("dd_2", created_at=parent.created_at - timedelta(hours=1), updated_at=later)
    elif case == "input_stale":
        store.records["dd_1"] = _upstream("dd_1", lifecycle=ArtifactLifecycleStatus.STALE)
    elif case == "input_current_since_unknown":
        store.records["dd_2"] = _upstream("dd_2", created_at=parent.created_at - timedelta(hours=1), acceptance=(
            _acceptance_evidence("dd_2", app_id="app_1", accepted_at="not a time", family="design_docs")
        ))
    elif case == "recorded_input_replaced":
        store.records["parent"] = parent = parent.model_copy(update={"canonical_inputs_version": {"design_docs": "dd_1"}})
        store.records["dd_1"] = _upstream("dd_1", lifecycle=ArtifactLifecycleStatus.SUPERSEDED)
        store.records["dd_2"] = _upstream("dd_2", created_at=parent.created_at)
    elif case == "lookup_error":
        store.list_change_requests = AsyncMock(side_effect=RuntimeError("store unavailable"))
    else:
        monkeypatch.setattr(coding_worker, "_STALE_PARENT_FAMILY_SCAN_LIMIT", 1)
    records_before = {record_id: record.model_dump() for record_id, record in store.records.items()}
    validations = flow.validate.await_count
    _assert_stale_refusal(_handler_attempt(flow, **extra), _UNATTRIBUTABLE_CASES[case])
    # Refused at admission: nothing was validated or stored, and no record was changed.
    assert flow.validate.await_count == validations
    assert {record_id: record.model_dump() for record_id, record in store.records.items()} == records_before
    assert store.records["parent"].invalidation_reason == "change_request:cr_1"
    assert [record_id for record_id in store.current() if store.records[record_id].build_family == "app_bundle"] == []


@pytest.mark.parametrize("competing", ["run_building", "open_draft", "input_changed"])
def test_a_confirmed_retry_on_an_unchanged_stale_parent_is_still_refused_for_competing_work(
    monkeypatch, tmp_path, competing,
):
    """A confirmation proposed after the rejection is unchanged, so it proceeds, and admission still refuses.

    The decision was proposed on the stale parent and nothing wrote that build
    since, so the confirmation is not refused as changed; the work that
    competes with it (another run building, another open draft, an input
    accepted since) is what admission refuses, before validation.
    """
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 2, proposer=_scope_proposer(),
    )
    _rejected_handler_draft(flow)
    asked = flow.trigger({}).json()
    assert asked["harness_decision"]["decision_type"] == "clarify_scope", asked
    if competing == "run_building":
        flow.registry.record["lifecycle_state"] = "building"
        expected = "another run of this app was building"
    elif competing == "open_draft":
        flow.store.records["open_child"] = _run_child(
            "open_child", build_id="run_other", validation=ArtifactValidationStatus.SKIPPED,
        )
        expected = "draft open_child made from the parent is draft with validation skipped"
    else:
        flow.store.records["dd_2"] = _upstream("dd_2", created_at=datetime.now(UTC) + timedelta(seconds=5))
        expected = "input design_docs dd_2 became current after the parent was accepted"
    confirmed = flow.confirm(asked)
    assert confirmed.status_code == 200, confirmed.text
    _assert_stale_refusal(confirmed.json()["coding_worker"], expected)
    assert flow.validate.await_count == 1 and flow.store.records["parent"].invalidation_reason == "change_request:cr_1"


def test_an_input_the_parent_recorded_is_compatible_while_it_stays_current(monkeypatch, tmp_path):
    """A recorded input that is still CURRENT is the parent's own input, however recently it became current."""
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 2)
    _rejected_handler_draft(flow)
    parent = flow.store.records["parent"]
    flow.store.records["parent"] = parent.model_copy(update={"canonical_inputs_version": {"design_docs": "dd_1"}})
    flow.store.records["dd_1"] = _upstream("dd_1", created_at=datetime.now(UTC) + timedelta(seconds=5))
    retry = _handler_attempt(flow)
    assert retry["status"] == "validated", (retry.get("error"), retry["metadata"].get("candidate_admission"))


def test_a_workflow_on_the_baseline_keeps_its_stale_marker_unresolved_for_pack_code(monkeypatch, tmp_path):
    """A launched (or failed, or abandoned) workflow is genuine invalidation: no pack-code retry is admitted after it.

    Its change request is recorded as a workflow route, so a confirmation on
    the unchanged stale build is not refused as changed (it follows its
    route), but its pack scope is refused at admission, and a workflow route
    on the same build launches as before.
    """
    proposer = _workflow_proposer()
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 2, proposer=proposer, launches=True)
    offered = flow.trigger({}).json()
    assert offered["harness_decision"]["decision_type"] == "fallback_workflow", offered
    launched = flow.confirm(offered, action="run_recommended_workflow")
    assert launched.status_code == 200 and launched.json()["execution_mode"] == "workflow", launched.text
    assert flow.launched == ["AppGenerator"]
    assert flow.store.records["parent"].invalidation_reason == "change_request:cr_1"
    assert flow.store.change_requests["cr_1"].router_decision["execution_mode"] == "workflow"
    # The workflow run has ended (or was abandoned): nothing is building, but its outcome is not on record.
    flow.registry.record["lifecycle_state"] = "needs_revision"

    explicit = _handler_attempt(flow)
    _assert_stale_refusal(explicit, "change request cr_1 that invalidated the parent may have launched a workflow")

    proposer.propose.return_value = _scope_proposer().propose.return_value
    asked = flow.trigger({}).json()
    assert asked["harness_decision"]["decision_type"] == "clarify_scope", asked
    confirmed = flow.confirm(asked)
    # The build is unchanged since the proposal, so the confirmation proceeds down its route ...
    assert confirmed.status_code == 200, confirmed.text
    # ... and pack-code admission still refuses the stale marker it cannot attribute.
    _assert_stale_refusal(confirmed.json()["coding_worker"], "may have launched a workflow")
    assert flow.store.children() == [] and flow.validate.await_count == 0

    proposer.propose.return_value = _workflow_proposer().propose.return_value
    again = flow.trigger({}).json()
    assert again["harness_decision"]["decision_type"] == "fallback_workflow", again
    relaunched = flow.confirm(again, action="run_recommended_workflow")
    assert relaunched.status_code == 200 and flow.launched == ["AppGenerator", "AppGenerator"], relaunched.text


def test_a_failed_workflow_launch_from_the_baseline_leaves_its_outcome_unresolved(monkeypatch, tmp_path):
    """A launch that raised after its change request was recorded as a workflow route is uncertain: refused."""
    from mozaiksai.hosts import studio

    proposer = _workflow_proposer()
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}] * 3, proposer=proposer, launches=True)
    rejected = _rejected_handler_draft(flow)
    offered = flow.trigger({}).json()
    assert offered["harness_decision"]["decision_type"] == "fallback_workflow", offered

    async def failing_launch(prepared):
        raise RuntimeError("launch failed")

    monkeypatch.setattr(studio, "launch_prepared_workflow", failing_launch)
    with pytest.raises(RuntimeError, match="launch failed"):
        flow.confirm(offered, action="run_recommended_workflow")
    # The failed launch invalidated nothing: the parent's marker is still the rejected draft's run.
    assert flow.store.records["parent"].invalidation_reason == "change_request:cr_1"
    assert flow.store.change_requests[offered["change_request_id"]].router_decision["execution_mode"] == "workflow"
    _assert_stale_refusal(
        _handler_attempt(flow), f"change request {offered['change_request_id']} made from the parent may have launched a workflow",
    )
    assert flow.store.status(rejected) == ArtifactLifecycleStatus.ARCHIVED


@pytest.mark.parametrize("refinement", ["rejected", "left_open"])
def test_a_handler_draft_refined_in_review_does_not_lock_its_parent_once_the_refinement_is_resolved(
    monkeypatch, tmp_path, refinement,
):
    """AppReview's "request changes" refines the draft under review, which marks that draft stale.

    A stale draft can be neither rejected nor accepted and never becomes
    current, so once nothing made from it is open it no longer holds its parent:
    a handler retry from the parent is admitted and its child accepted. While
    the refinement is still open the retry is refused, naming that open draft,
    and rejecting it is the supported next step.
    """
    flow = _studio_flow(
        monkeypatch, tmp_path,
        outputs=[{HANDLER: NORMALIZING_HANDLER}, {PAGE: _page(1)}, {HANDLER: NORMALIZING_HANDLER},
                 {HANDLER: NORMALIZING_HANDLER}],
    )
    first = _handler_attempt(flow)
    assert first["status"] == "validated", first
    handler_draft = first["metadata"]["build_record_id"]
    tweak = flow.trigger({"files": {PAGE: ""}}, build_record_id=handler_draft).json()["coding_worker"]
    assert tweak["status"] == "validated", tweak
    refined = tweak["metadata"]["build_record_id"]
    assert flow.store.status(handler_draft) == ArtifactLifecycleStatus.STALE
    # The refined draft is out of reach of review: it cannot be rejected or accepted.
    for action in (flow.reject, flow.accept):
        response = action(handler_draft)
        assert response.status_code == 409 and "Only draft" in response.json()["detail"], response.text
    if refinement == "left_open":
        _assert_stale_refusal(_handler_attempt(flow), f"draft {refined}, made further down from the parent, is still open")
    assert flow.reject(refined).status_code == 200
    retry = _handler_attempt(flow)
    assert retry["status"] == "validated", (retry.get("error"), retry["metadata"].get("candidate_admission"))
    child = flow.store.records[retry["metadata"]["build_record_id"]]
    assert child.parent_build_record_id == "parent" and _archive_files(child)[HANDLER] == NORMALIZING_HANDLER
    accepted = flow.accept(child.id)
    assert accepted.status_code == 200, accepted.text
    assert flow.store.current() == [child.id]
    assert flow.store.status(handler_draft) == ArtifactLifecycleStatus.STALE


def _promotion_index(flow, tmp_path, monkeypatch):
    """The App Intelligence index a promotion's background job saves, through the real indexer.

    Promotion indexes the restored workspace with the default
    ``AppIntelligenceIndexRequest``: its own build key, saved CURRENT. Context
    registration past the app bundle record is stubbed.
    """
    from mozaiksai.control_plane import app_intelligence
    from mozaiksai.hosts import studio

    workspace = tmp_path / "promoted" / "app"
    (workspace / "ui").mkdir(parents=True)
    (workspace / "ui" / "board.jsx").write_text("export default function Board() { return null; }\n", encoding="utf-8")
    (workspace / "main.py").write_text("print('ready')\n", encoding="utf-8")
    registration = SimpleNamespace(
        app_context_version_id="acv_1", indexed_file_count=2, source_context_artifact_version_id="sc_1",
        app_intelligence_artifact_version_id="ai_1", app_context_artifact_version_id="ac_1",
        graph_artifact_version_id="g_1", scan_health={}, health_report={}, warnings=[],
    )
    monkeypatch.setattr(app_intelligence, "register_app_intelligence_context", AsyncMock(return_value=registration))
    request = studio.AppIntelligenceIndexRequest(workspace_root=str(workspace))
    result = asyncio.run(app_intelligence.index_workspace_app_intelligence(
        app_id="app_1", workspace_root=workspace, artifact_store=flow.store, workspace_key=request.workspace_key,
        source_workflow="studio_app_intelligence_index", make_current=request.make_current,
        generated_artifacts_root=tmp_path / "generated",
    ))
    index = flow.store.records[result.app_bundle_artifact_version_id]
    assert (index.build_family, index.build_key, index.parent_build_record_id) == (
        "app_bundle", "app_intelligence_workspace", None,
    )
    assert index.lifecycle_status == ArtifactLifecycleStatus.CURRENT
    assert index.commit_metadata.metadata["bundle_mode"] == "app_intelligence_workspace_index"
    # Saved under its own key, it supersedes nothing: the promoted build stays CURRENT beside it.
    assert flow.store.current() == sorted(["parent", index.id])
    return index.id


@pytest.mark.parametrize("step", ["accept_page_child", "confirm_proposal", "accept_handler_child", "handler_retry"])
def test_a_promotions_app_intelligence_index_is_not_taken_for_the_apps_build(monkeypatch, tmp_path, step):
    """Acceptance, confirmation and admission read the app's build in its own build family and key.

    After a promotion the App Intelligence index is CURRENT in the
    ``app_bundle`` family under ``app_intelligence_workspace``, a key that
    accepting a refinement child (``app_bundle``) never supersedes, so it is not
    the app's current build and no refinement of the promoted build is refused
    because of it.
    """
    outputs = [{PAGE: _page(1)}] if step in {"accept_page_child", "confirm_proposal"} else [{HANDLER: NORMALIZING_HANDLER}] * 2
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=outputs,
        proposer=_low_confidence_page_proposer() if step == "confirm_proposal" else None,
    )
    index = _promotion_index(flow, tmp_path, monkeypatch)
    if step == "confirm_proposal":
        decision = flow.trigger({}).json()
        assert decision["harness_decision"]["decision_type"] == "clarify_scope", decision
        response = flow.confirm(decision)
    else:
        response = flow.trigger({"files": dict.fromkeys(outputs[0], "")})
    assert response.status_code == 200, response.text
    result = response.json()["coding_worker"]
    assert result["status"] == "validated", result
    child = result["metadata"]["build_record_id"]
    if step == "handler_retry":
        assert flow.reject(child).status_code == 200
        assert flow.store.status("parent") == ArtifactLifecycleStatus.STALE and flow.store.current() == [index]
        retry = _handler_attempt(flow)
        assert retry["status"] == "validated", (retry.get("error"), retry["metadata"].get("candidate_admission"))
        child = retry["metadata"]["build_record_id"]
    accepted = flow.accept(child)
    assert accepted.status_code == 200, accepted.text
    assert flow.store.current() == sorted([child, index])


def _accepted_handler_build(flow):
    """An authorized handler change from the parent, accepted: the app's latest accepted build."""
    accepted = _handler_attempt(flow)
    assert accepted["status"] == "validated", accepted
    accepted_id = accepted["metadata"]["build_record_id"]
    assert flow.accept(accepted_id).status_code == 200
    return accepted_id


def _page_draft(flow, build_record_id):
    """A reviewable page draft of ``build_record_id``; storing it marks that build stale."""
    result = flow.trigger({"files": {PAGE: ""}}, build_record_id=build_record_id).json()["coding_worker"]
    assert result["status"] == "validated", result
    assert flow.store.status(build_record_id) == ArtifactLifecycleStatus.STALE
    return result["metadata"]["build_record_id"]


@pytest.mark.parametrize("accepted", ["handler_change", "sibling_page_change"])
def test_an_older_candidate_cannot_replace_an_accepted_build_after_that_build_is_marked_stale(
    monkeypatch, tmp_path, accepted,
):
    """A newer accepted build that its own review draft marked stale is still newer accepted work.

    Nothing is CURRENT while that draft awaits review, so acceptance reads
    acceptance evidence, not only the CURRENT status. ``handler_change``: a
    page candidate made afterwards from the older parent would revert the
    accepted handler change. ``sibling_page_change``: two page siblings of the
    parent, one accepted. Either older candidate is refused, and the accepted
    build's own draft is still accepted.
    """
    if accepted == "handler_change":
        flow = _studio_flow(
            monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}, {PAGE: _page(1)}, {PAGE: _page(2)}],
        )
        accepted_id = _accepted_handler_build(flow)
        own_draft = _page_draft(flow, accepted_id)
        older = _page_draft(flow, "parent")
        assert _archive_files(flow.store.records[older])[HANDLER] == TEMPLATE_HANDLER
    else:
        flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}, {PAGE: _page(3)}])
        accepted_id, older = _page_draft(flow, "parent"), _page_draft(flow, "parent")
        assert flow.accept(accepted_id).status_code == 200
        own_draft = _page_draft(flow, accepted_id)
    assert flow.store.current() == []

    refused = flow.accept(older)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "earlier build than the app's latest accepted build" in detail
    assert f"build {accepted_id} was accepted after build parent" in detail
    assert flow.store.status(older) == ArtifactLifecycleStatus.DRAFT and flow.store.current() == []

    kept = flow.accept(own_draft)
    assert kept.status_code == 200, kept.text
    assert flow.store.current() == [own_draft]
    if accepted == "handler_change":
        assert _archive_files(flow.store.records[own_draft])[HANDLER] == NORMALIZING_HANDLER


def test_a_chained_draft_of_the_latest_accepted_build_is_accepted(monkeypatch, tmp_path):
    """A draft of a draft of the latest accepted build is in its lineage, however stale those links are."""
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}, {PAGE: _page(1)}, {PAGE: _page(2)}],
    )
    accepted_id = _accepted_handler_build(flow)
    first_draft = _page_draft(flow, accepted_id)
    chained = _page_draft(flow, first_draft)
    assert flow.store.current() == []
    accepted = flow.accept(chained)
    assert accepted.status_code == 200, accepted.text
    assert flow.store.current() == [chained]
    assert _archive_files(flow.store.records[chained])[HANDLER] == NORMALIZING_HANDLER


def test_a_confirmation_proposed_before_a_newer_acceptance_is_refused_after_that_build_is_marked_stale(
    monkeypatch, tmp_path,
):
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{HANDLER: NORMALIZING_HANDLER}, {PAGE: _page(1)}, {PAGE: _page(2)}],
        proposer=_low_confidence_page_proposer(),
    )
    handler = _handler_attempt(flow)
    assert handler["status"] == "validated", handler
    accepted_id = handler["metadata"]["build_record_id"]
    decision = flow.trigger({}).json()
    assert decision["harness_decision"]["decision_type"] == "clarify_scope", decision
    # The handler change is accepted after the proposal, then marked stale by its own review draft.
    assert flow.accept(accepted_id).status_code == 200
    _page_draft(flow, accepted_id)
    assert flow.store.current() == []

    refused = flow.confirm(decision)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "A newer build of this app was accepted" in detail and "Request the change again" in detail
    assert f"build {accepted_id} was accepted after build parent" in detail
    # Refused before the decision was used: still pending, and no run began for it.
    assert flow.session()["pending_harness_decision"]["decision_id"] == decision["change_request_id"]
    assert flow.provider.execute.await_count == 2


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
async def test_generator_owned_pack_outputs_are_not_template_owned(tmp_path, monkeypatch):
    import shutil

    from factory_app.workflows.AppGenerator.tools import (
        resolve_managed_capability_templates as resolver,
    )
    from mozaiksai.control_plane.implementations.coding_worker import (
        pack_output_owners,
        parent_workspace_pack_paths,
    )
    from tests.test_messaging_pack_persistence_composition import _context as pack_context

    # The saved parent names the pack by its catalog path; this catalog ships the modified messaging pack.
    factory_root = tmp_path / "factory_app"
    root = factory_root / "build_context" / "messaging"
    shutil.copytree(PACK, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setattr(resolver, "resolve_factory_app_root", lambda: factory_root)
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
            if record.lifecycle_status == ArtifactLifecycleStatus.CURRENT and (
                record.build_family, record.build_key,
            ) == (target.build_family, target.build_key):
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
    before = deepcopy(store.records)
    with pytest.raises(HTTPException) as rejected:
        await accept("child_1")
    assert rejected.value.status_code == 409
    if accepted_since == "same_app_owned_code":
        # The app-owned code comparison passes, but newer_build is not in the
        # draft's lineage: accepting it would revert what newer_build changed.
        assert HANDLER not in rejected.value.detail and "earlier build" in rejected.value.detail
    else:
        assert HANDLER in rejected.value.detail
    assert store.accepted == [] and store.records == before


@pytest.mark.asyncio
@pytest.mark.parametrize("registry_run", ["this_child", "newer_run"])
async def test_accepting_a_handler_change_whose_run_staled_its_parent(monkeypatch, registry_run):
    """The run that stored this child marked its own parent stale, so no build is CURRENT during review.

    A run that stores no reviewable child leaves its parent current; see
    test_failed_inline_run_does_not_block_a_later_authorized_handler_patch.
    """
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
@pytest.mark.parametrize("lineage", ["draft_of_current", "missing_ancestor", "longer_than_bound"])
async def test_a_draft_made_from_a_draft_of_the_current_build_is_accepted_only_through_a_verified_lineage(
    monkeypatch, lineage,
):
    """child_2 was made from draft_1, a review draft of the still-current parent.

    The parent stays CURRENT when draft_1's run stored a child that failed
    validation. Acceptance follows child_2's parent links back to the current
    build; a link that cannot be read, or a chain past the bound, refuses.
    """
    from fastapi import HTTPException

    from mozaiksai.core.artifacts import ArtifactValidationStatus
    from mozaiksai.hosts import studio

    store = _Store()
    _accept_records(store, ["draft_1", "child_2"], scope={
        "selected_file_paths": [PAGE], "scope_origin": "explicit", "changed_workspace_pack_paths": [],
    })
    store.records["child_2"] = store.records["child_2"].model_copy(update={"parent_build_record_id": "draft_1"})
    store.records["draft_1"] = store.records["draft_1"].model_copy(
        update={"validation_status": ArtifactValidationStatus.FAILED},
    )
    if lineage == "missing_ancestor":
        del store.records["draft_1"]
    if lineage == "longer_than_bound":
        monkeypatch.setattr(studio, "_ACCEPTANCE_LINEAGE_MAX_DEPTH", 1)
    accept = _accept_route(monkeypatch, store)
    if lineage == "draft_of_current":
        assert (await accept("child_2"))["accepted"] is True
        assert store.accepted == ["child_2"] and store.records["parent"].lifecycle_status == ArtifactLifecycleStatus.SUPERSEDED
        return
    before = deepcopy(store.records)
    with pytest.raises(HTTPException) as rejected:
        await accept("child_2")
    assert rejected.value.status_code == 409 and "lineage could not be verified" in rejected.value.detail
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
    before = deepcopy(store.records)
    with pytest.raises(HTTPException) as rejected:
        await accept("child_1")
    assert rejected.value.status_code == 409
    if layout == "unchanged_past_file_limit_in_one":
        # The full comparison found the app-owned code unchanged; the draft is
        # refused only because newer_build is not in its lineage.
        assert HANDLER not in rejected.value.detail and "earlier build" in rejected.value.detail
    else:
        assert (PROVENANCE_ONLY if layout.startswith("provenance") else HANDLER) in rejected.value.detail
    assert store.accepted == [] and store.records == before


# --- Acceptance evidence and newer accepted work: every fail-closed branch decides on its own. ---

_T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _at(seconds):
    return _T0 + timedelta(seconds=seconds)


def _evidence_record(
    record_id, *, parent=None, status=ArtifactLifecycleStatus.STALE, accepted=None, marker=None, created=0,
    written=None, version=1, staled_by=None, metadata=None,
):
    """An app bundle record.

    ``accepted`` is the time (seconds after T0, or a raw value) of the
    acceptance evidence naming it; ``marker`` is any other acceptance marker it
    carries. ``staled_by`` is the change request its ``invalidation_reason`` names.
    """
    metadata = dict(metadata or {})
    if accepted is not None:
        stamp = _at(accepted).isoformat() if isinstance(accepted, int | float) else accepted
        metadata["acceptance"] = _acceptance_evidence(record_id, app_id=APP_ID, accepted_by="demo-user", accepted_at=stamp)
    if marker is not None:
        metadata["acceptance"] = marker
    return BuildRecord(
        id=record_id, app_id=APP_ID, build_family="app_bundle", build_key="app_bundle", version_number=version,
        parent_build_record_id=parent, lineage_root_id=parent or record_id, lifecycle_status=status,
        validation_status=ArtifactValidationStatus.PASSED, commit_metadata={"metadata": metadata},
        invalidation_reason=f"change_request:{staled_by}" if staled_by else None,
        created_at=_at(created), updated_at=_at(created if written is None else written),
    )


def _staling_change(change_id, record_id, status, *, prior=None, state_of=None):
    """A change request made from ``record_id`` that saved its state as ``status`` (None: saved no state)."""
    decision = {"execution_mode": "coding_worker"}
    if status is not None:
        decision["baseline_state"] = {
            "build_record_id": state_of or record_id, "lifecycle_status": status,
            "invalidation_reason": f"change_request:{prior}" if prior else None,
        }
    return SimpleNamespace(
        id=change_id, app_id=APP_ID, build_record_id=record_id, created_by_user_id="demo-user", router_decision=decision,
    )


def _evidence_store(*items):
    store = _Store()
    store.records = {item.id: item for item in items if isinstance(item, BuildRecord)}
    store.change_requests = {item.id: item for item in items if not isinstance(item, BuildRecord)}
    return store


_STALE, _DRAFT = ArtifactLifecycleStatus.STALE, ArtifactLifecycleStatus.DRAFT
_CURRENT, _SUPERSEDED = ArtifactLifecycleStatus.CURRENT, ArtifactLifecycleStatus.SUPERSEDED
_ACCEPTED_BASE = [
    _evidence_record("p", accepted=1, written=5), _evidence_record("v", parent="p", status=_DRAFT, created=6),
]


def _stale_elsewhere(**options):
    """A stale build outside v's lineage, written after p was accepted, staled by change request cr_x."""
    return _evidence_record("x", created=2, written=7, staled_by="cr_x", **options)


_NEWER_WORK_CASES = {
    # No build in the lineage shows acceptance, and another build was accepted: it cannot be ordered.
    "accepted_without_a_base": (
        [_evidence_record("p", status=_STALE, written=5), _evidence_record("v", parent="p", status=_DRAFT, created=6),
         _evidence_record("x", status=_STALE, accepted=3, created=1, written=4)],
        ("unverified", "nothing in this build's lineage shows an acceptance to order it against"),
    ),
    # Evidence naming the build, whose time cannot be read.
    "unknown_acceptance_time": (
        [*_ACCEPTED_BASE, _evidence_record("x", accepted="not a time", created=2, written=6)],
        ("unverified", "when build x was accepted, relative to build p, is unknown"),
    ),
    # Superseded, so accepted once, with no evidence saying when, and not a build the base was made from.
    "superseded_without_evidence": (
        [*_ACCEPTED_BASE, _evidence_record("x", status=_SUPERSEDED, created=2, written=7)],
        ("unverified", "build x was superseded, so it was once accepted, but no acceptance evidence names it"),
    ),
    # The build the base was made from, superseded by the base's own acceptance (written just after its stamp).
    "superseded_without_evidence_ancestor": (
        [_evidence_record("a", status=_SUPERSEDED, written=10),
         _evidence_record("b", parent="a", status=_CURRENT, accepted=9, created=5, written=10, version=2),
         _evidence_record("v", parent="b", status=_DRAFT, created=11, version=3)],
        None,
    ),
    # Another first build, superseded at the instant the base's acceptance made it current.
    "superseded_when_the_base_was_accepted": (
        [_evidence_record("x", status=_SUPERSEDED, written=5),
         _evidence_record("g", status=_CURRENT, accepted=5, created=1, written=5, version=2),
         _evidence_record("v", parent="g", status=_DRAFT, created=6, version=3)],
        None,
    ),
    # A stale draft without evidence (refined in review): the state its change request saved shows a draft.
    "stale_draft_never_accepted": (
        [*_ACCEPTED_BASE, _stale_elsewhere(parent="p"), _staling_change("cr_x", "x", "draft")],
        None,
    ),
    # Refined twice in review: the second change began while it was already stale, so the first one decides.
    "stale_draft_refined_twice": (
        [*_ACCEPTED_BASE, _stale_elsewhere(parent="p"), _staling_change("cr_x", "x", "stale", prior="cr_1"),
         _staling_change("cr_1", "x", "draft")],
        None,
    ),
    # Evidence copied from another build, or naming another app, shows nothing: the saved state decides.
    "stale_draft_with_copied_evidence": (
        [*_ACCEPTED_BASE, _stale_elsewhere(marker=_acceptance_evidence("p", app_id=APP_ID, accepted_by="demo-user")),
         _staling_change("cr_x", "x", "draft")],
        None,
    ),
    "stale_with_copied_evidence_once_current": (
        [*_ACCEPTED_BASE, _stale_elsewhere(marker=_acceptance_evidence("p", app_id=APP_ID, accepted_by="demo-user")),
         _staling_change("cr_x", "x", "current")],
        ("unverified", "build x is stale, it carries no acceptance evidence naming it, and change request cr_x that "
                       "staled it began while it was current, not a draft, so its acceptance cannot be verified"),
    ),
    "stale_with_evidence_of_another_app_once_current": (
        [*_ACCEPTED_BASE, _stale_elsewhere(marker=_acceptance_evidence("x", app_id="another-app", accepted_by="demo-user")),
         _staling_change("cr_x", "x", "current")],
        ("unverified", "began while it was current, not a draft"),
    ),
    # A build accepted before acceptance evidence named its build, later staled: never passed over.
    "stale_accepted_before_evidence": (
        [*_ACCEPTED_BASE, _stale_elsewhere(marker=UNSCOPED_MARKER), _staling_change("cr_x", "x", "current")],
        ("unverified", "refine the app from build x or a build made from it instead"),
    ),
    "stale_superseded_when_its_change_began": (
        [*_ACCEPTED_BASE, _stale_elsewhere(), _staling_change("cr_x", "x", "superseded")],
        ("unverified", "began while it was superseded, not a draft"),
    ),
    "stale_without_saved_state": (
        [*_ACCEPTED_BASE, _stale_elsewhere(), _staling_change("cr_x", "x", None)],
        ("unverified", "change request cr_x that staled it saved no state of it when it began"),
    ),
    "stale_with_the_state_of_another_build": (
        [*_ACCEPTED_BASE, _stale_elsewhere(), _staling_change("cr_x", "x", "draft", state_of="y")],
        ("unverified", "change request cr_x that staled it saved no state of it when it began"),
    ),
    "stale_by_no_change_request": (
        [*_ACCEPTED_BASE, _evidence_record("x", created=2, written=7)],
        ("unverified", "its invalidation names no change request that saved its state"),
    ),
    "stale_by_a_missing_change_request": (
        [*_ACCEPTED_BASE, _stale_elsewhere()],
        ("unverified", "change request cr_x that staled it was not found"),
    ),
    "stale_by_a_change_request_of_another_build": (
        [*_ACCEPTED_BASE, _stale_elsewhere(), _staling_change("cr_x", "y", "draft")],
        ("unverified", "change request cr_x that staled it is not one made from it"),
    ),
    "stale_invalidations_past_depth": (
        [*_ACCEPTED_BASE, _stale_elsewhere(), _staling_change("cr_x", "x", "stale", prior="cr_1"),
         _staling_change("cr_1", "x", "draft")],
        ("unverified", "its invalidations go back more than 1 change requests"),
    ),
    "stale_state_lookups_past_budget": (
        [*_ACCEPTED_BASE, _stale_elsewhere(), _staling_change("cr_x", "x", "stale", prior="cr_1"),
         _staling_change("cr_1", "x", "draft")],
        ("unverified", "more than 1 change requests would have to be read to show it was never accepted"),
    ),
    # Brownfield Genesis acceptance writes no evidence: blocked, naming the dependency.
    "stale_imported_genesis": (
        [*_ACCEPTED_BASE, _stale_elsewhere(metadata={"bundle_mode": "brownfield_genesis_import"}),
         _staling_change("cr_x", "x", "current")],
        ("unverified", "build x is an imported Genesis build, and accepting imported Genesis (accept-genesis, "
                       "BuildRecordStore.accept_genesis_build_record, owned by Genesis import) records no acceptance "
                       "evidence yet"),
    ),
    "lineage_of_an_imported_genesis_without_evidence": (
        [_evidence_record("g", status=_STALE, written=5, metadata={"bundle_mode": "brownfield_genesis_import"}),
         _evidence_record("v", parent="g", status=_DRAFT, created=6),
         _evidence_record("x", status=_STALE, accepted=3, created=1, written=4)],
        ("unverified", "to order it against (build g is an imported Genesis build"),
    ),
    # Evidence copied into a stale draft of the lineage (or naming another record) is no accepted base: the
    # base stays the parent, so a sibling accepted after it is newer work, although the draft was made later.
    "copied_evidence_is_no_base": (
        [_evidence_record("p", accepted=1, written=4),
         _evidence_record("d", parent="p", created=10, written=11, marker=_acceptance_evidence(
             "p", app_id=APP_ID, accepted_by="demo-user", accepted_at=_at(1).isoformat(),
         )),
         _evidence_record("v", parent="d", status=_DRAFT, created=12),
         _evidence_record("x", parent="p", accepted=5, created=2, written=6)],
        ("accepted", "build x was accepted after build p"),
    ),
    "evidence_of_another_record_is_no_base": (
        [_evidence_record("p", accepted=1, written=4),
         _evidence_record("d", parent="p", created=10, written=11, marker=_acceptance_evidence(
             "another_build", app_id=APP_ID, accepted_by="demo-user", accepted_at=_at(10).isoformat(),
         )),
         _evidence_record("v", parent="d", status=_DRAFT, created=12),
         _evidence_record("x", parent="p", accepted=5, created=2, written=6)],
        ("accepted", "build x was accepted after build p"),
    ),
    # A CURRENT base without evidence is ordered from its creation, the earliest it can have become current.
    "current_base_without_evidence_ordered_from_creation": (
        [_evidence_record("b", status=_CURRENT, created=0, written=10),
         _evidence_record("v", parent="b", status=_DRAFT, created=11),
         _evidence_record("x", status=_STALE, accepted=5, created=4, written=12)],
        ("accepted", "build x was accepted after build b"),
    ),
    # A first build is a draft with no base: accepted only while nothing else of its scope is accepted.
    "first_build_draft_alone": ([_evidence_record("v", status=_DRAFT)], None),
    "first_build_draft_beside_a_current_build": (
        [_evidence_record("v", status=_DRAFT, created=6), _evidence_record("c", status=_CURRENT, accepted=2, created=1)],
        ("current", "build c is the current build"),
    ),
    "lineage_cycle": (
        [_evidence_record("v", parent="a", status=_DRAFT), _evidence_record("a", parent="v", status=_DRAFT)],
        ("unverified", "its lineage returns to build v"),
    ),
    "missing_ancestor": (
        [_evidence_record("v", parent="gone", status=_DRAFT)],
        ("unverified", "build gone in its lineage was not found"),
    ),
    "lineage_past_bound": (
        [_evidence_record("v", parent="d1", status=_DRAFT), _evidence_record("d1", parent="d2", status=_DRAFT),
         _evidence_record("d2", parent="d3", status=_DRAFT), _evidence_record("d3", status=_DRAFT)],
        ("unverified", "its lineage has more than 2 builds before one that was accepted"),
    ),
    "current_past_bound": (
        [_evidence_record("c1", status=_CURRENT), _evidence_record("c2", status=_CURRENT),
         _evidence_record("v", parent="c1", status=_DRAFT, created=6)],
        ("unverified", "more than 1 builds of app_bundle/app_bundle are current"),
    ),
    "written_past_bound": (
        [*_ACCEPTED_BASE, _evidence_record("x", status=_STALE, created=2, written=7)],
        ("unverified", "more than 1 builds of app_bundle/app_bundle were written since its accepted base"),
    ),
}


@pytest.mark.parametrize("case", sorted(_NEWER_WORK_CASES))
def test_newer_accepted_work_orders_only_what_acceptance_evidence_shows(monkeypatch, case):
    from mozaiksai.control_plane.implementations import coding_worker

    records, expected = _NEWER_WORK_CASES[case]
    if case.endswith("_past_bound") and case != "lineage_past_bound":
        monkeypatch.setattr(coding_worker, "_NEWER_ACCEPTED_WORK_SCAN_LIMIT", 1)
    if case == "stale_invalidations_past_depth":
        monkeypatch.setattr(coding_worker, "_STALE_PARENT_ATTRIBUTION_DEPTH", 1)
    if case == "stale_state_lookups_past_budget":
        monkeypatch.setattr(coding_worker, "_STALE_STATE_LOOKUP_LIMIT", 1)
    store = _evidence_store(*records)
    found = asyncio.run(coding_worker.newer_accepted_work(
        store, app_id=APP_ID, record=store.records["v"], max_depth=2 if case == "lineage_past_bound" else 32,
    ))
    if expected is None:
        assert found is None, found
        return
    assert found is not None and found.kind == expected[0] and expected[1] in found.reason, found


def test_a_stale_build_whose_saved_state_cannot_be_read_propagates_the_lookup_error():
    """Deciding a stale build from its change request reads the store; a failed read is never "never accepted"."""
    from mozaiksai.control_plane.implementations import coding_worker

    store = _evidence_store(*_ACCEPTED_BASE, _stale_elsewhere(), _staling_change("cr_x", "x", "draft"))
    store.get_change_request = AsyncMock(side_effect=RuntimeError("store unavailable"))
    with pytest.raises(RuntimeError, match="store unavailable"):
        asyncio.run(coding_worker.newer_accepted_work(store, app_id=APP_ID, record=store.records["v"]))


def test_only_evidence_naming_the_record_shows_that_it_was_accepted():
    from mozaiksai.control_plane.implementations.coding_worker import (
        _ACCEPTED_AT_UNKNOWN,
        _accepted_at,
    )

    def record(metadata):
        return BuildRecord(
            id="g", app_id=APP_ID, build_family="app_bundle", build_key="app_bundle", version_number=1,
            lineage_root_id="g", lifecycle_status=_STALE, commit_metadata={"metadata": metadata}, created_at=_at(5),
        )

    def evidence(**changes):
        return {**_acceptance_evidence("g", app_id=APP_ID, accepted_by="demo-user", accepted_at=_at(10).isoformat()), **changes}

    assert _accepted_at(record({"acceptance": evidence()})) == _at(10)
    assert _accepted_at(record({"acceptance": evidence(transition="staged_refinement_accept")})) == _at(10)
    # Evidence never dates acceptance before the record was created.
    assert _accepted_at(record({"acceptance": evidence(accepted_at="2026-10-01T00:00:00Z")})) == _at(5)
    # The shape of a parentless first build is not evidence, nor is a marker that does not name the record:
    # one written before acceptance named its build, one copied from another record, or one naming another
    # app, build family or build key.
    assert _accepted_at(record({"capability_packs": []})) is None
    assert _accepted_at(record({"capability_packs": _packs()})) is None
    assert _accepted_at(record({"acceptance": dict(UNSCOPED_MARKER)})) is None
    assert _accepted_at(record({"acceptance": "accepted"})) is None
    for scope in (
        {"build_record_id": "parent"}, {"app_id": "another-app"}, {"build_family": "workflow_bundle"},
        {"build_key": "app_intelligence_workspace"}, {"build_record_id": None},
    ):
        assert _accepted_at(record({"acceptance": evidence(**scope)})) is None, scope
    # Evidence naming the record whose time, principal or transition cannot be read: accepted at an unknown time.
    for unreadable in ({"accepted_at": "not a time"}, {"accepted_by": None}, {"accepted_by": " "},
                       {"transition": "genesis_save"}, {"transition": None}):
        assert _accepted_at(record({"acceptance": evidence(**unreadable)})) is _ACCEPTED_AT_UNKNOWN, unreadable


def test_acceptance_refuses_a_version_whose_lineage_shows_no_acceptance_to_order_newer_work_against(
    monkeypatch, tmp_path,
):
    """A first build accepted before acceptance evidence named its build is no accepted base once stale."""
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}, {PAGE: _page(3)}])
    _with_marker(flow.store, "parent", UNSCOPED_MARKER)

    def page_child(build_record_id):
        result = flow.trigger({"files": {PAGE: ""}}, build_record_id=build_record_id).json()["coding_worker"]
        assert result["status"] == "validated", result
        return result["metadata"]["build_record_id"]

    first = page_child("parent")
    # Nothing else was accepted yet, so there is nothing newer to order: accepted.
    assert flow.accept(first).status_code == 200
    page_child(first)  # a refinement of the accepted build marks it stale
    sibling = page_child("parent")
    refused = flow.accept(sibling)
    assert refused.status_code == 409, refused.text
    assert "to order it against" in refused.json()["detail"]
    assert flow.store.status(sibling) == ArtifactLifecycleStatus.DRAFT and flow.store.accepted == [first]


def test_acceptance_refuses_when_the_apps_current_builds_cannot_be_read(monkeypatch, tmp_path):
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}])
    child = flow.trigger({"files": {PAGE: ""}}).json()["coding_worker"]["metadata"]["build_record_id"]
    listed = flow.store.list_build_records

    async def current_unreadable(**kwargs):
        if kwargs.get("lifecycle_status") == ArtifactLifecycleStatus.CURRENT:
            raise RuntimeError("store unavailable")
        return await listed(**kwargs)

    flow.store.list_build_records = current_unreadable
    refused = flow.accept(child)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "The app's current build could not be read. No version was accepted."
    assert flow.store.accepted == [] and flow.store.status(child) == ArtifactLifecycleStatus.DRAFT


@pytest.mark.parametrize("unreadable", ["lineage_link", "stale_build_state"])
def test_acceptance_refuses_when_its_lineage_or_a_stale_builds_saved_state_cannot_be_read(
    monkeypatch, tmp_path, unreadable,
):
    """A read that newer accepted work needs fails: 409, never "nothing newer"; nothing is accepted.

    ``lineage_link``: the version's parent cannot be read. ``stale_build_state``:
    a stale build outside the lineage, without evidence, is decided from its
    change request, which cannot be read.
    """
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[{PAGE: _page(1)}, {PAGE: _page(2)}, {PAGE: _page(3)}])
    child = _page_draft(flow, "parent")
    store = flow.store
    if unreadable == "lineage_link":
        read = store.get_build_record

        async def parent_unreadable(*, app_id, build_record_id):
            if build_record_id == "parent":
                raise RuntimeError("store unavailable")
            return await read(app_id=app_id, build_record_id=build_record_id)

        store.get_build_record = parent_unreadable
    else:
        # Another draft of the parent, refined in review: stale without evidence, decided from its change request.
        other = _page_draft(flow, "parent")
        _page_draft(flow, other)
        assert store.status(other) == ArtifactLifecycleStatus.STALE
        store.get_change_request = AsyncMock(side_effect=RuntimeError("store unavailable"))
    refused = flow.accept(child)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == (
        "This version's lineage could not be verified against the app's accepted builds. No version was accepted."
    )
    assert store.accepted == [] and store.status(child) == ArtifactLifecycleStatus.DRAFT


@pytest.mark.asyncio
async def test_admission_refuses_when_the_apps_current_build_cannot_be_read(tmp_path):
    baseline, store = _baseline(), _Store()
    harness, validate = _harness(store, tmp_path, {HANDLER: NORMALIZING_HANDLER})
    request, _ = await _prepared(harness, baseline, [HANDLER])
    store.list_build_records = AsyncMock(side_effect=RuntimeError("store unavailable"))
    result = await harness.execute_coding_request(request)
    admission = result.metadata["candidate_admission"]
    assert result.status == "failed" and admission["code"] == "STALE_REFINEMENT_BASELINE", result
    assert admission["stale_baseline_refusal"] == "the app's current build could not be read (RuntimeError)"
    validate.assert_not_awaited()
    assert store.created == []


TEMPLATE_PAGE = "ui/pages/custom/Messages.jsx"
TEMPLATE_BASE_HANDLER = "modules/messages/backend/base_handler.py"


@pytest.mark.parametrize("scope", ["explicit", "inferred"])
def test_a_patch_that_would_edit_only_template_owned_pack_files_is_refused_before_the_provider_runs(
    monkeypatch, tmp_path, scope,
):
    """No refinement can change a template-owned pack file, so a patch that would apply to such files only is refused.

    ``explicit``: the user names the file, refused before classification.
    ``inferred``: the scope proposer selects it with enough confidence that the
    decision auto-applies; the classifier and proposer have run. The coding
    provider, validation and the registry run have not.
    """
    proposer = _scope_proposer((TEMPLATE_PAGE,)) if scope == "inferred" else None
    flow = _studio_flow(
        monkeypatch, tmp_path, outputs=[{TEMPLATE_PAGE: "export default function Messages() { return null; }\n"}],
        proposer=proposer,
    )
    response = flow.trigger({"files": {TEMPLATE_PAGE: ""}} if scope == "explicit" else {})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert f"only template-owned capability pack files ({TEMPLATE_PAGE})" in detail
    assert "customize the app through its own files" in detail
    assert "upgrade the pack" in detail and "No files were changed" in detail
    if proposer is not None:
        proposer.propose.assert_awaited_once()
    flow.provider.execute.assert_not_awaited()
    flow.validate.assert_not_awaited()
    assert flow.registry.begun == [] and flow.store.children() == [] and flow.store.change_requests == {}
    assert flow.store.invalidations == [] and flow.store.current() == ["parent"]


def _template_page_proposer(resolution, confidence=0.95):
    return SimpleNamespace(
        propose=AsyncMock(return_value=ScopeProposal(
            resolution=resolution, selected_paths=[TEMPLATE_PAGE], rationale="The messages page lives here.",
            confidence=confidence, clarification_question="Which surface should change?", signals=["messages"],
        )),
        materialize_files=AsyncMock(side_effect=lambda **kwargs: {
            path: _baseline(app_id="app_1")[path] for path in kwargs["selected_paths"]
        }),
    )


@pytest.mark.parametrize(("proposal", "decision_type"), [
    (("clarify", 0.95), "clarify_scope"),
    (("workflow", 0.9), "fallback_workflow"),
    (("scoped_files", 0.6), "clarify_scope"),
])
def test_a_decision_that_applies_nothing_is_not_refused_for_a_template_owned_path(
    monkeypatch, tmp_path, proposal, decision_type,
):
    """A clarification or a workflow route applies no patch, so a template-owned path in its proposal is not refused.

    A low-confidence scope that offers to apply the template page is refused
    only when the owner confirms that apply, before the decision is used.
    """
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[], proposer=_template_page_proposer(*proposal))
    response = flow.trigger({})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "harness_decision"
    assert body["harness_decision"]["decision_type"] == decision_type
    assert body["harness_decision"]["selected_paths"] == [TEMPLATE_PAGE]
    flow.provider.execute.assert_not_awaited()
    assert flow.registry.begun == [] and flow.store.invalidations == [] and flow.store.current() == ["parent"]
    if "apply_proposed_scope" not in [action["action_id"] for action in body["harness_decision"]["actions"]]:
        return
    confirmed = flow.confirm(body)
    assert confirmed.status_code == 409, confirmed.text
    assert f"only template-owned capability pack files ({TEMPLATE_PAGE})" in confirmed.json()["detail"]
    assert flow.session()["pending_harness_decision"]["decision_id"] == body["change_request_id"]
    flow.provider.execute.assert_not_awaited()
    assert flow.registry.begun == [] and flow.store.children() == []


@pytest.mark.parametrize("changed", ["editable_file_only", "template_file_too"])
def test_a_scope_holding_template_files_beside_editable_ones_is_left_to_admission(monkeypatch, tmp_path, changed):
    """A scope that includes a template-owned file only beside files the patch may change is not refused early.

    The template base handler gives the provider its context; when the
    candidate leaves it unchanged the patch is admitted, and when it changes
    it, candidate admission refuses it (``PACK_TEMPLATE_OUTPUT_CHANGED``).
    """
    base = _baseline(app_id="app_1")[TEMPLATE_BASE_HANDLER]
    output = {HANDLER: NORMALIZING_HANDLER}
    if changed == "template_file_too":
        output[TEMPLATE_BASE_HANDLER] = base + "\n# edited\n"
    flow = _studio_flow(monkeypatch, tmp_path, outputs=[output])
    response = flow.trigger({"files": {HANDLER: "", TEMPLATE_BASE_HANDLER: ""}})
    assert response.status_code == 200, response.text
    coding = response.json()["coding_worker"]
    flow.provider.execute.assert_awaited_once()
    if changed == "editable_file_only":
        assert coding["status"] == "validated", coding
        child = flow.store.records[coding["metadata"]["build_record_id"]]
        assert _archive_files(child)[HANDLER] == NORMALIZING_HANDLER and _archive_files(child)[TEMPLATE_BASE_HANDLER] == base
        return
    admission = coding["metadata"]["candidate_admission"]
    assert coding["status"] == "failed" and admission["code"] == "PACK_TEMPLATE_OUTPUT_CHANGED", coding
    assert admission["paths"] == [TEMPLATE_BASE_HANDLER] and "upgrade the pack" in admission["supported_path"]
    flow.validate.assert_not_awaited()
    assert flow.store.children() == [] and flow.store.current() == ["parent"]
