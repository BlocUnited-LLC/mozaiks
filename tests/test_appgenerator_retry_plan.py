from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory_app.workflows.AppGenerator.tools import retry_plan
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.adapters.ag2_stream_storage import stream_id_for_run
from mozaiksai.core.data.persistence.namespaces import RuntimeCollections
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.task_batches import (
    take_task_batch_start,
    task_evidence_digest,
    task_inventory_digest,
)
from mozaiksai.hosts import studio
from tests.test_app_plan_review import _revision_with_form
from tests.test_continuous_deterministic_materialization import _load_models
from tests.test_factory_pack_selection import _enable_managed
from tests.test_session_launcher import _MemoryCollection


@pytest.fixture
def source(monkeypatch, tmp_path):
    _enable_managed(tmp_path, monkeypatch)
    _load_models()
    raw, seed, _, _ = _revision_with_form("update_report", reachable=False)
    binding = RunBuildBinding.model_validate({**seed.snapshot()["run_build_binding"], "phase": "refinement",
                                              "refinement_model": "openai"})
    scope = {
        "workflow_name": "AppGenerator", "app_id": "host", "user_id": "user", "run_build_binding": binding.model_dump(),
        "change_request_id": "change", "revision_id": "revision", "revision_source_artifact_version_id": "v4",
        "design_docs_artifact_version_id": "design", "subscription_contract_artifact_version_id": "subscription",
        "workflow_bundle_artifact_version_id": "workflow", "refinement_request": "Edit an existing record.",
        "change_intent": {"change_class": "feature"}, "impact_set": {"workflow_sequence": "design_revision"},
    }
    ctx = create_context_container({**seed.snapshot(), **scope})
    initial = ctx.to_dict()
    result = review_app_build_plan(AppBuildPlan=deepcopy(raw), context_variables=ctx)
    assert result["outcome"] == "ready", result
    plan, rows = ctx.to_dict()["app_build_plan"], ctx.to_dict()["app_task_batch_items"]
    prior = {**scope, "_id": "prior", "ag2_stream_id": str(stream_id_for_run("host", "prior")),
             "app_plan_ready": True, "app_plan_attempts": 1, "app_plan_outcome": "ready", "app_build_plan": plan,
             "app_task_batch_items": rows, "status": 2, "app_task_batch_results": {
                 "_meta": {"input_digests": {"app_build_plan": task_evidence_digest(plan)},
                           "task_inventory_digest": task_inventory_digest(rows)},
                 "failed-output": {"untrusted": "never-replayed"},
             }}
    marker = retry_plan.admitted_source(prior)
    assert marker is not None
    return prior, initial, raw, marker


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing-attempts", "bool-attempts", "stream", "worker-digest", "model-override", "malformed-receipt",
                                   "sibling", "provider", "owner", "upstream", "no-parent"])
async def test_source_selected_only_from_corroborated_native_retry_chain(source, fault):
    prior, _, _, marker = source
    binding = RunBuildBinding.model_validate(prior["run_build_binding"])
    scope = {k: deepcopy(prior[k]) for k in ("app_id", "user_id", "run_build_binding", "status", "workflow_name",
                                            "revision_source_artifact_version_id", "design_docs_artifact_version_id")}
    scope["journey_parent_chat_id"] = "reviewed-workflow-chat"
    now = datetime.now(UTC)
    prior.update(journey_parent_chat_id="reviewed-workflow-chat", created_at=now, failed_at=now)
    child = {**deepcopy(scope), "_id": studio._retry_chat_id("host", "user", "prior", binding),
             "created_at": now + timedelta(minutes=1), "failed_at": now + timedelta(minutes=2),
             "trigger_meta": {"trigger_source": "manual", "retry_of_chat_id": "prior"}}
    if fault == "missing-attempts":
        prior.pop("app_plan_attempts")
    elif fault == "bool-attempts":
        prior["app_plan_attempts"] = True
    elif fault == "stream":
        prior["ag2_stream_id"] = str(stream_id_for_run("host", "another"))
    elif fault == "worker-digest":
        prior["app_task_batch_results"]["_meta"]["input_digests"]["app_build_plan"] = "unrelated"
    elif fault == "model-override":
        child["workflow_model"] = "gemini"
    elif fault == "malformed-receipt":
        prior["app_task_batch_results"] = "unreadable"
    elif fault == "sibling":
        child["trigger_meta"]["retry_of_chat_id"] = "different"
    elif fault == "provider":
        prior["run_build_binding"]["refinement_model"] = "gemini"
    elif fault == "owner":
        prior["user_id"] = "another"
    elif fault == "upstream":
        prior["design_docs_artifact_version_id"] = "different"
    elif fault == "no-parent":
        prior.pop("journey_parent_chat_id")
    db = _MemoryCollection()
    db._docs.update({child["_id"]: child, "prior": prior})
    answer = await studio._select_retry_plan(db, failed=child, scope=scope, binding=binding)
    assert answer == (None if fault else marker)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "no-marker", "scope", "tampered-dispatch", "changed-turn",
                                   "ambiguous-turn", "old-results", "changed-design"])
async def test_retry_reviews_original_ag2_turn_against_new_inputs_and_never_replays_results(source, monkeypatch, fault):
    prior, initial, raw, marker = source
    ctx = create_context_container({**initial, "retry_plan_source": marker.model_dump()})
    if fault == "no-marker":
        ctx.remove("retry_plan_source")
    elif fault == "scope":
        ctx.set("revision_id", "other-revision")
    elif fault == "tampered-dispatch":
        prior["app_task_batch_items"][0]["initial_message"] = "unreviewed instruction"
    elif fault == "changed-turn":
        raw["build_tasks"][0]["depends_on"] = ["unregistered_task"]
    elif fault == "old-results":
        ctx.set("app_task_batch_results", {"old": "result"})
    elif fault == "changed-design":
        ctx.set("experience_spec", {"pages": [{"name": "Changed page", "route": "/foreign"}]})
    event = {"sequence": 1, "event_payload": {"message": {"content": json.dumps({"AppBuildPlan": raw})}}}
    events = [event]
    if fault == "ambiguous-turn":
        prior["app_plan_attempts"] = 2
        events.append({**event, "sequence": 2})
    sessions = _MemoryCollection()
    sessions._docs["prior"] = prior
    coll = MagicMock()
    coll.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=events)
    sessions.database = {RuntimeCollections.AG2_STREAM_EVENTS: coll}
    monkeypatch.setattr(AG2PersistenceManager, "_coll", AsyncMock(return_value=sessions))
    if fault == "no-marker":
        assert await retry_plan.readmit_retry_plan(ctx) == {"status": "skipped"}
        coll.find.assert_not_called()
    elif fault:
        with pytest.raises(ValueError):
            await retry_plan.readmit_retry_plan(ctx)
        assert not ctx.get("app_plan_ready")
        assert take_task_batch_start(ctx, workflow_name="AppGenerator", required=False) is None
    else:
        assert (await retry_plan.readmit_retry_plan(ctx))["status"] == "admitted"
        assert ctx.to_dict()["app_build_plan"] == prior["app_build_plan"]
        assert ctx.to_dict()["app_task_batch_items"] == prior["app_task_batch_items"]
        assert ctx.get("app_plan_attempts") == 1 and not ctx.get("app_task_batch_results")
        admission = take_task_batch_start(ctx, workflow_name="AppGenerator", required=True)
        assert admission.trigger_agent == "AppPlanAgent" and admission.plan_context_key == "app_build_plan"
        query = coll.find.call_args.args[0]
        assert query["stream_id"] == prior["ag2_stream_id"] and query["app_id"] == "host"
        assert query["event_payload.message.metadata.agent_name"] == "AppPlanAgent"
