"""Continue Factory app review after an inline refinement settles."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from anyio import CancelScope

from factory_app.workflows.AppReview.tools.review_context import saved_review_context
from mozaiksai.core.artifacts import ArtifactLifecycleStatus, ArtifactValidationStatus
from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.runtime.persistence.distributed_lock import chat_execution_lease
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.session.launcher import (
    create_routed_chat_session,
    validate_context_for_workflow,
)
from mozaiksai.core.session.model import TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution
from mozaiksai.core.workflow.ui_tools import _await_on_transport_loop


def _pending_workbench(source: dict[str, Any], *, source_chat_id: str, baseline_id: str,
                       binding: RunBuildBinding) -> str:
    """Select the one durable response-required workbench for this source version."""
    calls = (source.get("workflow_ui_state") or {}).get("tool_calls") or {}
    if source.get("status") != WorkflowStatus.IN_PROGRESS:
        raise ValueError("The source generation is no longer awaiting review")
    matches: list[str] = []
    for call in calls.values():
        payload = call.get("payload") or {}
        if (call.get("tool_name") == "AppWorkbench"
                and call.get("tool_call_status") == "pending"
                and call.get("awaiting_response") is True
                and call.get("tool_call_completed") is False
                and payload.get("artifact_version_id") == baseline_id
                and payload.get("source_chat_id") == source_chat_id
                and payload.get("build_registry_id") == binding.build_registry_id
                and payload.get("target_app_id") == binding.target_app_id
                and isinstance(call.get("tool_call_id"), str) and call["tool_call_id"]):
            matches.append(call["tool_call_id"])
    if len(matches) != 1:
        raise ValueError("The source generation has no unique pending workbench for this version")
    return matches[0]


async def _settle_superseded_workbench(*, app_id: str, user_id: str, source_chat_id: str, event_id: str,
                                     baseline_id: str, persistence: Any) -> None:
    """Wake only the selected source waiter; this is not a user approval."""
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = await SimpleTransport.get_instance()

    async def settle() -> None:
        connection = transport.connections.get(source_chat_id) or {}
        if any(connection.get(key) not in (None, value) for key, value in (("app_id", app_id), ("user_id", user_id))):
            raise ValueError("The source workbench transport belongs to another owner")
        metadata = transport._ui_tool_metadata.get(event_id)
        if metadata is None:
            # Missing local metadata says nothing about another process. The
            # existing execution lease excludes a running/resuming source.
            async with chat_execution_lease(app_id=app_id, chat_id=source_chat_id):
                source = await (await persistence._coll()).find_one(
                    {"_id": source_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                    {"workflow_name": 1, "status": 1, "run_build_binding": 1,
                     "workflow_ui_state.tool_calls": 1, "app_bundle_export_outcome": 1},
                )
                if not source or source.get("workflow_name") != "AppGenerator":
                    raise ValueError("The source workbench is no longer available")
                if source.get("status") == WorkflowStatus.FAILED and source.get("app_bundle_export_outcome") == "superseded":
                    return
                if _pending_workbench(
                    source, source_chat_id=source_chat_id, baseline_id=baseline_id,
                    binding=RunBuildBinding.model_validate(source.get("run_build_binding")),
                ) != event_id:
                    raise ValueError("The source workbench changed before settlement")
                await persistence.persist_context_variables(
                    chat_id=source_chat_id, app_id=app_id, workflow_name="AppGenerator",
                    variables={"app_bundle_export_outcome": "superseded", "download_status": "superseded",
                               "app_download_ready": False, "generated_app_dir": None, "generated_app_zip": None,
                               "download_result": None,
                               "app_build_failure_message": "This generation was superseded by its refinement review."},
                )
                if not await persistence.mark_chat_failed(source_chat_id, app_id=app_id):
                    raise ValueError("The source workbench could not be closed")
            return
        if metadata.get("chat_id") != source_chat_id:
            raise ValueError("The source workbench transport belongs to another chat")
        if transport._is_tool_call_response_resolved(event_id):
            return
        if (metadata.get("tool_name") != "AppWorkbench"
                or metadata.get("workflow_name") not in (None, "AppGenerator")):
            raise ValueError("The source workbench transport has a different tool or workflow")
        submitted = await transport.submit_tool_call_response(event_id, {
            "status": "superseded", "approved": False,
            "data": {"artifact_version_id": baseline_id},
        })
        if not submitted:
            raise ValueError("The source workbench transport did not accept settlement")

    await _await_on_transport_loop(transport, settle())


async def continue_inline_app_review(
    *, app_id: str, user_id: str, source_chat_id: str | None,
    binding: RunBuildBinding, baseline_id: str, result: Any,
    registry: Any, artifact_store: Any, persistence: Any, session_router: Any,
) -> dict[str, Any] | None:
    """Prepare one new review run; its websocket remains the only launch owner.

    A review run cannot acquire another build's immutable binding. Only an
    owned review or pending generation workbench gets a successor, bound to the
    settled inline run. The generation source rechecks current-build admission
    before starting an export or reporting completion.
    This creates session state, never executes AG2 or allocates a preview.
    """
    if not source_chat_id:
        return None
    source = await (await persistence._coll()).find_one(
        {"_id": source_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
        {"workflow_name": 1, "run_build_binding": 1, "workflow_ui_state.tool_calls": 1, "status": 1},
    )
    if not source:
        raise ValueError("Source review session is no longer available")
    if source.get("workflow_name") not in {"AppReview", "AppGenerator"}:
        return None
    source_binding = RunBuildBinding.model_validate(source.get("run_build_binding"))
    if (source_binding.build_registry_id != binding.build_registry_id
            or source_binding.target_app_id != binding.target_app_id):
        raise ValueError("Source review belongs to a different app")

    source_event_id = None
    if source["workflow_name"] == "AppGenerator":
        baseline = await artifact_store.get_build_record(
            app_id=binding.target_app_id, build_record_id=baseline_id,
        )
        if (baseline is None or baseline.app_id != binding.target_app_id
                or baseline.id != baseline_id
                or baseline.build_family != "app_bundle"
                or baseline.source_workflow != "AppGenerator"
                or baseline.source_chat_id != source_chat_id
                or baseline.commit_metadata.author_user_id != user_id
                or any((baseline.commit_metadata.metadata or {}).get(key) != value
                       for key, value in source_binding.model_dump().items())):
            raise ValueError("The workbench baseline does not belong to the source generation")

    record = (await registry.get_app_record(
        owner_user_id=user_id, build_registry_id=binding.build_registry_id,
    )).get("app")
    current = (record or {}).get("current_build_run") or {}
    if (not record or record.get("chat_app_id") != app_id
            or record.get("app_id") != binding.target_app_id
            or current.get("build_id") != binding.build_id
            or current.get("phase") != binding.phase
            or record.get("lifecycle_state") not in {"review", "needs_revision"}):
        raise ValueError("The completed refinement has been superseded")

    candidate_id = (result.metadata or {}).get("build_record_id")
    version = await artifact_store.get_build_record(
        app_id=binding.target_app_id, build_record_id=candidate_id or baseline_id,
    )
    if (version is None or version.app_id != binding.target_app_id
            or version.build_family != "app_bundle"
            or version.lifecycle_status in {ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED}):
        raise ValueError("The review artifact is unavailable")
    metadata = version.commit_metadata.metadata or {}
    if (metadata.get("build_registry_id") != binding.build_registry_id
            or metadata.get("target_app_id") != binding.target_app_id
            or version.commit_metadata.author_user_id != user_id):
        raise ValueError("The review artifact belongs to a different app")
    if candidate_id and (
        version.parent_build_record_id != baseline_id
        or any(metadata.get(key) != value for key, value in binding.model_dump().items())
        or current.get("artifact_version_id") != version.id
    ):
        raise ValueError("The saved candidate does not belong to the settled refinement")
    validated = result.status in {"validated", "success"}
    if validated and (
        not candidate_id or record["lifecycle_state"] != "review"
        or version.lifecycle_status not in {ArtifactLifecycleStatus.DRAFT, ArtifactLifecycleStatus.CURRENT}
        or version.validation_status != ArtifactValidationStatus.PASSED
        or version.app_validation_status != "passed"
    ):
        raise ValueError("The saved candidate has not passed validation")

    successor_id = record.get("active_chat_id") if record.get("active_workflow_id") == "AppReview" else None
    if successor_id == source_chat_id:
        successor_id = None
    if successor_id:
        successor = await (await persistence._coll()).find_one(
            {"_id": successor_id, "user_id": user_id, **build_app_scope_filter(app_id)},
            {"workflow_name": 1, "run_build_binding": 1, "artifact_version_id": 1, "trigger_meta": 1, "status": 1},
        )
        trigger = (successor or {}).get("trigger_meta") or {}
        if (not successor or successor.get("workflow_name") != "AppReview"
                or successor.get("status") != WorkflowStatus.IN_PROGRESS
                or successor.get("run_build_binding") != binding.model_dump()
                or successor.get("artifact_version_id") != version.id
                or trigger.get("source_chat_id") != source_chat_id
                or trigger.get("source_artifact_version_id") != baseline_id):
            raise ValueError("Another review has already opened for this refinement")
        if source["workflow_name"] == "AppGenerator":
            source_event_id = trigger.get("source_tool_call_id")
            if not isinstance(source_event_id, str) or not source_event_id:
                raise ValueError("The saved review has no verified workbench handoff")
    elif source["workflow_name"] == "AppGenerator":
        source_event_id = _pending_workbench(
            source, source_chat_id=source_chat_id, baseline_id=baseline_id, binding=source_binding,
        )

    notice = None if validated else (
        "The changes need another revision before activation. Describe what to fix in chat."
        if candidate_id else
        "The last edit did not produce a saved draft. Your previous draft is still selected; describe a narrower change to retry."
    )
    context = validate_context_for_workflow("AppReview", {
        **saved_review_context(version, lifecycle_state=record["lifecycle_state"]),
        # A parent's advisory report does not certify its child.
        "security_readiness_summary": {}, "review_notice": notice,
    })
    allowed, reason = await get_platform_hooks().call_chat_prereqs(
        app_id, user_id, "AppReview", persistence,
    )
    if not allowed:
        raise ValueError(reason or "Review prerequisites not met")
    router = session_router.for_target(binding.target_app_id)
    decision = await router.route_trigger(
        TriggerInput(app_id=app_id, user_id=user_id, trigger_source="transition", workflow_id="AppReview"),
        contribution=TriggerRoutingContribution(workflow_id="AppReview", require_exact_route=True),
    )
    chat_id = successor_id or str(uuid4())
    verified_binding = await registry.resolve_build_binding(
        owner_user_id=user_id, app_id=app_id, chat_id=chat_id, workflow_name="AppReview",
        build_registry_id=binding.build_registry_id, persisted_binding=binding.model_dump(),
    )
    selected = bool(successor_id)
    try:
        if not successor_id:
            await create_routed_chat_session(
                workflow_id="AppReview", app_id=app_id, user_id=user_id, chat_id=chat_id,
                context_variables=context, persistence_manager=persistence,
                session_fields={"run_build_binding": verified_binding.model_dump()},
                trigger_meta={"trigger_source": "transition", "transition_id": "app_review",
                              "requested_workflow_id": "AppReview", "resolved_workflow_id": "AppReview",
                              "source_chat_id": source_chat_id, "source_artifact_version_id": baseline_id,
                              **({"source_tool_call_id": source_event_id} if source_event_id else {})},
            )
        # Once the successor wins, cancellation must not strand its old live
        # waiter. No response is submitted before durable selection succeeds.
        with CancelScope(shield=True):
            saved = await registry.update_build_status(
                owner_user_id=user_id, build_registry_id=binding.build_registry_id,
                expected_build_id=binding.build_id, expected_lifecycle_state=record["lifecycle_state"],
                expected_artifact_version_id=current.get("artifact_version_id"),
                expected_active_chat_id=record.get("active_chat_id"),
                status=record["lifecycle_state"], active_chat_id=chat_id, active_workflow_id="AppReview",
            )
            if not saved["success"]:
                raise ValueError("The refinement changed before its review could open")
            selected = True
            await router.bind_workflow_session(
                app_id=app_id, user_id=user_id, workflow_id="AppReview", chat_id=chat_id,
                journey_id=decision.journey_id,
            )
            if source_event_id:
                await _settle_superseded_workbench(
                    app_id=app_id, user_id=user_id, source_chat_id=source_chat_id, event_id=source_event_id,
                    baseline_id=baseline_id, persistence=persistence,
                )
    except BaseException:
        # An unselected session is an orphan. A selected successor stays
        # recoverable: the next attempt verifies and reuses that exact session.
        if not selected:
            with CancelScope(shield=True):
                await persistence.mark_chat_failed(chat_id, app_id=app_id)
        raise
    return {
        "chat_id": chat_id, "workflow_id": "AppReview",
        "websocket_url": f"/ws/AppReview/{app_id}/{chat_id}/{user_id}",
        "app_id": app_id, "target_app_id": binding.target_app_id,
        "build_registry_id": binding.build_registry_id, "artifact_version_id": version.id,
        "artifact_kind": version.build_family, "artifact_key": version.build_key,
        "source_chat_id": source_chat_id, "notice": notice,
        "lifecycle_state": record["lifecycle_state"],
    }
