import json
from hashlib import sha256

from mozaiksai.core.workflow.outcome_feedback import resolve_workflow_feedback

from . import repo


def require_actor(ctx) -> str:
    actor = getattr(ctx, "user_id", None)
    if not isinstance(actor, str) or not actor or actor == "anonymous":
        raise PermissionError("Authenticated feedback owner required")
    return actor


def feedback_run(ctx) -> tuple[str, str]:
    require_actor(ctx)
    authority = getattr(ctx, "dispatch_authority", None)
    provenance = getattr(ctx, "dispatch_provenance", None)
    if (
        getattr(authority, "kind", None) != "workflow"
        or getattr(provenance, "surface", None) != "workflow_tool"
        or not getattr(provenance, "workflow_run_id", None)
        or not getattr(provenance, "workflow_name", None)
    ):
        raise PermissionError("Feedback must originate in an authenticated workflow tool")
    return provenance.workflow_run_id, provenance.workflow_name


class OutcomeFeedbackService:
    async def record_workflow_feedback(self, ctx, *, ui_event_id: str, outcome_id: str):
        chat_id, workflow_name = feedback_run(ctx)
        evidence = await resolve_workflow_feedback(
            app_id=ctx.app_id, chat_id=chat_id, user_id=ctx.user_id,
            ui_event_id=ui_event_id, outcome_id=outcome_id,
        )
        if evidence is None or evidence.workflow_name != workflow_name:
            raise PermissionError("Authenticated feedback receipt not found")
        scope = [evidence.app_id, evidence.user_id, evidence.chat_id, evidence.outcome_id]
        identity = sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()
        inserted = await repo.insert_once(
            ctx, record={**evidence.model_dump(mode="json"), "feedback_id": identity},
        )
        return {"success": True, "feedback_id": identity, "duplicate": not inserted}

    async def list_my_feedback(self, ctx, *, limit: int = 50):
        require_actor(ctx)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        return {"records": await repo.list_mine(ctx, limit=limit)}
