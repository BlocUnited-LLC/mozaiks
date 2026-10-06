"""Continue Factory app review after an inline refinement settles."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from anyio import CancelScope

from mozaiksai.core.artifacts import ArtifactLifecycleStatus, ArtifactValidationStatus
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.session.launcher import (
    create_routed_chat_session,
    validate_context_for_workflow,
)
from mozaiksai.core.session.model import TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution


async def continue_inline_app_review(
    *, app_id: str, user_id: str, source_chat_id: str | None,
    binding: RunBuildBinding, baseline_id: str, result: Any,
    registry: Any, artifact_store: Any, persistence: Any, session_router: Any,
) -> dict[str, Any] | None:
    """Prepare one new review run; its websocket remains the only launch owner.

    A review run cannot acquire another build's immutable binding. Only an
    owned AppReview source gets a successor, bound to the settled inline run.
    This creates session state, never executes AG2 or allocates a preview.
    """
    if not source_chat_id:
        return None
    source = await (await persistence._coll()).find_one(
        {"_id": source_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
        {"workflow_name": 1, "run_build_binding": 1},
    )
    if not source:
        raise ValueError("Source review session is no longer available")
    if source.get("workflow_name") != "AppReview":
        return None
    source_binding = RunBuildBinding.model_validate(source.get("run_build_binding"))
    if (source_binding.build_registry_id != binding.build_registry_id
            or source_binding.target_app_id != binding.target_app_id):
        raise ValueError("Source review belongs to a different app")

    record = (await registry.get_app_record(
        owner_user_id=user_id, build_registry_id=binding.build_registry_id,
    )).get("app")
    current = (record or {}).get("current_build_run") or {}
    if (not record or record.get("chat_app_id") != app_id
            or record.get("app_id") != binding.target_app_id
            or current.get("build_id") != binding.build_id
            or record.get("lifecycle_state") not in {"review", "needs_revision"}):
        raise ValueError("The completed refinement has been superseded")
    if (record.get("active_workflow_id") == "AppReview"
            and record.get("active_chat_id") not in {None, source_chat_id}):
        raise ValueError("Another review has already opened for this refinement")

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
            or metadata.get("target_app_id") != binding.target_app_id):
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
        or version.validation_status != ArtifactValidationStatus.PASSED
        or version.app_validation_status != "passed"
    ):
        raise ValueError("The saved candidate has not passed validation")

    notice = None if validated else (
        "The changes need another revision before activation. Describe what to fix in chat."
        if candidate_id else
        "The last edit did not produce a saved draft. Your previous draft is still selected; describe a narrower change to retry."
    )
    acceptance = metadata.get("app_bundle_acceptance") or {}
    context = validate_context_for_workflow("AppReview", {
        "artifact_kind": version.build_family, "artifact_key": version.build_key,
        "artifact_version_id": version.id, "bundle_path": metadata.get("workspace_dir"),
        "lifecycle_state": record["lifecycle_state"],
        "app_validation_status": version.app_validation_status,
        "app_validation_strategy_used": version.app_validation_strategy,
        "app_bundle_acceptance_status": acceptance.get("status"),
        "integration_tests_passed": acceptance.get("passed"),
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
    chat_id = str(uuid4())
    verified_binding = await registry.resolve_build_binding(
        owner_user_id=user_id, app_id=app_id, chat_id=chat_id, workflow_name="AppReview",
        build_registry_id=binding.build_registry_id, persisted_binding=binding.model_dump(),
    )
    await create_routed_chat_session(
        workflow_id="AppReview", app_id=app_id, user_id=user_id, chat_id=chat_id,
        context_variables=context, persistence_manager=persistence,
        session_fields={"run_build_binding": verified_binding.model_dump()},
        trigger_meta={"trigger_source": "transition", "transition_id": "app_review",
                      "requested_workflow_id": "AppReview", "resolved_workflow_id": "AppReview",
                      "source_chat_id": source_chat_id},
    )
    try:
        saved = await registry.update_build_status(
            owner_user_id=user_id, build_registry_id=binding.build_registry_id,
            expected_build_id=binding.build_id, expected_lifecycle_state=record["lifecycle_state"],
            expected_active_chat_id=record.get("active_chat_id"),
            status=record["lifecycle_state"], active_chat_id=chat_id, active_workflow_id="AppReview",
        )
        if not saved["success"]:
            raise ValueError("The refinement changed before its review could open")
    except BaseException:
        # No websocket URL has been handed out and no model was launched.
        with CancelScope(shield=True):
            await persistence.mark_chat_failed(chat_id, app_id=app_id)
        raise
    await router.bind_workflow_session(
        app_id=app_id, user_id=user_id, workflow_id="AppReview", chat_id=chat_id,
        journey_id=decision.journey_id,
    )
    return {
        "chat_id": chat_id, "workflow_id": "AppReview",
        "websocket_url": f"/ws/AppReview/{app_id}/{chat_id}/{user_id}",
        "app_id": app_id, "target_app_id": binding.target_app_id,
        "build_registry_id": binding.build_registry_id, "artifact_version_id": version.id,
        "artifact_kind": version.build_family, "artifact_key": version.build_key,
        "source_chat_id": source_chat_id, "notice": notice,
        "lifecycle_state": record["lifecycle_state"],
    }
