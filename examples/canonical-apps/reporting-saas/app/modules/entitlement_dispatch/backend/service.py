from .repo import EntitlementDispatchRepo


class EntitlementDispatchService:
    def __init__(self):
        self.repo = EntitlementDispatchRepo()

    async def assign_subscription(self, ctx, *, user_id, plan_id):
        await self.repo.activate(ctx, user_id=user_id, plan_id=plan_id)
        return {"activated": True, "plan_id": plan_id}

    async def deactivate_subscription(self, ctx, *, user_id):
        deactivated = await self.repo.deactivate(ctx, user_id=user_id)
        return {"deactivated": deactivated}
