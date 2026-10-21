from .service import OutcomeFeedbackService


class OutcomeFeedbackHandler:
    def __init__(self):
        self.service = OutcomeFeedbackService()

    async def record_workflow_feedback(self, ctx, *, ui_event_id: str, outcome_id: str):
        return await self.service.record_workflow_feedback(ctx, ui_event_id=ui_event_id, outcome_id=outcome_id)

    async def list_my_feedback(self, ctx, *, limit: int = 50):
        return await self.service.list_my_feedback(ctx, limit=limit)
