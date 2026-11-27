"""Re-admit the original planner turn against the new, independently hydrated build."""

from __future__ import annotations

import json
from typing import Any

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    MAX_PLAN_REVIEW_ATTEMPTS,
    review_app_build_plan,
)
from factory_app.workflows.AppGenerator.tools.hydrate_app_revision_context import (
    discard_executable_plan,
)
from mozaiksai.core.adapters.ag2_stream_storage import stream_id_for_run
from mozaiksai.core.data.persistence.namespaces import RuntimeCollections
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.session.build_binding import RetryPlanSource
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.task_batches import (
    admit_task_batch_start,
    task_evidence_digest,
    task_inventory_digest,
)

# Only the runner's two admission digests are needed from the prior batch.
PLAN_SOURCE_PROJECTION = dict.fromkeys((
    "app_id", "ag2_stream_id", "workflow_model", "workflow_model_rebind_pending", "workflow_run_model",
    "app_plan_attempts", "app_plan_ready", "app_plan_outcome", "app_build_plan", "app_task_batch_items",
    "app_task_batch_results._meta.input_digests.app_build_plan", "app_task_batch_results._meta.task_inventory_digest",
), 1)


def admitted_source(row: dict[str, Any]) -> RetryPlanSource | None:
    plan, rows = row.get("app_build_plan"), row.get("app_task_batch_items")
    attempts = row.get("app_plan_attempts")
    if (type(attempts) is not int or not 1 <= attempts <= MAX_PLAN_REVIEW_ATTEMPTS
            or any(row.get(key) for key in ("workflow_model", "workflow_model_rebind_pending", "workflow_run_model"))
            or not row.get("app_id") or not row.get("_id")
            or row.get("ag2_stream_id") != str(stream_id_for_run(row["app_id"], row["_id"]))
            or row.get("app_plan_ready") is not True or row.get("app_plan_outcome") != "ready"
            or not isinstance(plan, dict) or not isinstance(rows, list) or not rows):
        return None
    results = row.get("app_task_batch_results")
    meta = results.get("_meta") if isinstance(results, dict) else None
    if not isinstance(meta, dict) or not isinstance(meta.get("input_digests"), dict):
        return None
    plan_digest = task_evidence_digest(plan)
    if (meta["input_digests"].get("app_build_plan") != plan_digest
            or meta.get("task_inventory_digest") != task_inventory_digest(rows)):
        return None
    return RetryPlanSource(chat_id=row["_id"], plan_sha256=plan_digest, dispatch_sha256=task_evidence_digest(rows))


def _matches(context: Any, source: RetryPlanSource) -> bool:
    return (context.get("app_plan_ready") is True and context.get("app_plan_outcome") == "ready"
            and task_evidence_digest(detach(context.get("app_build_plan"))) == source.plan_sha256
            and task_evidence_digest(detach(context.get("app_task_batch_items"))) == source.dispatch_sha256)


async def readmit_retry_plan(context_variables: Any = None) -> dict[str, Any]:
    if context_variables is None or context_variables.get("retry_plan_source") is None:
        return {"status": "skipped"}
    source = RetryPlanSource.model_validate(detach(context_variables.get("retry_plan_source")))
    if any(context_variables.get(key) for key in (
        "app_plan_ready", "app_build_plan", "app_plan_attempts", "app_task_batch_items", "app_task_batch_results",
    )):
        raise ValueError("A prior app plan may only be admitted into a fresh, empty retry")
    scope = {key: detach(context_variables.get(key)) for key in (
        "app_id", "user_id", "run_build_binding", "change_request_id", "revision_id", "revision_source_artifact_version_id",
        "design_docs_artifact_version_id", "subscription_contract_artifact_version_id", "workflow_bundle_artifact_version_id",
        "refinement_request", "change_intent", "impact_set",
    )}
    if not all(scope.values()) or context_variables.get("workflow_name") != "AppGenerator":
        raise ValueError("A retry plan requires the verified app source and all reviewed upstream inputs")
    sessions = await AG2PersistenceManager()._coll()
    prior = await sessions.find_one({"_id": source.chat_id, "workflow_name": "AppGenerator", "status": 2,
                                     "app_id": scope["app_id"], "user_id": scope["user_id"]},
                                    {**PLAN_SOURCE_PROJECTION, **dict.fromkeys(scope, 1)})
    stream = str(stream_id_for_run(scope["app_id"], source.chat_id))
    if (not prior or any(prior.get(k) != v for k, v in scope.items()) or admitted_source(prior) != source):
        raise ValueError("Saved plan source no longer matches the admitted build and dispatch")
    limit = MAX_PLAN_REVIEW_ATTEMPTS + 1
    events = await sessions.database[RuntimeCollections.AG2_STREAM_EVENTS].find(
        {"stream_id": stream, "app_id": scope["app_id"], "event_class": "ModelResponse",
         "event_payload.message.metadata.source": "ag2_network_wal",
         "event_payload.message.metadata.agent_name": "AppPlanAgent"},
        {"event_payload.message.content": 1, "sequence": 1},
    ).sort("sequence", 1).limit(limit).to_list(length=limit)
    if not 0 < len(events) <= MAX_PLAN_REVIEW_ATTEMPTS or len(events) != prior["app_plan_attempts"]:
        raise ValueError("Saved planner history does not match its bounded review attempts")
    matched = []
    for event in events:
        payload = json.loads(event["event_payload"]["message"]["content"])
        if not isinstance(payload, dict) or set(payload) != {"AppBuildPlan"}:
            raise ValueError("Invalid saved structured planner response")
        trial = ContextVariablesBridge(context_variables.to_dict())
        review_app_build_plan(AppBuildPlan=payload["AppBuildPlan"], context_variables=trial)
        if _matches(trial, source):
            matched.append(payload["AppBuildPlan"])
    if len(matched) != 1:
        raise ValueError("Expected exactly one original planner response to pass current review with the identical dispatch")
    review_app_build_plan(AppBuildPlan=matched[0], context_variables=context_variables)
    if not _matches(context_variables, source):
        discard_executable_plan(context_variables)
        raise ValueError("The new review differs from the pinned plan or dispatch")
    admit_task_batch_start(context_variables, workflow_name="AppGenerator", trigger_agent="AppPlanAgent", plan_context_key="app_build_plan")
    return {"status": "admitted", "source_chat_id": source.chat_id, "plan_sha256": source.plan_sha256,
            "dispatch_sha256": source.dispatch_sha256, "task_count": len(context_variables.get("app_task_batch_items"))}
