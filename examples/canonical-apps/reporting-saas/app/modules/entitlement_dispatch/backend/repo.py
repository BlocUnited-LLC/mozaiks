from mozaiksai.core.runtime.persistence import app_data_from_context


class EntitlementDispatchRepo:
    async def activate(self, ctx, *, user_id, plan_id):
        collection = app_data_from_context(ctx).collection("billing.subscriptions")
        await collection.update_one(
            {"app_id": ctx.app_id, "user_id": user_id},
            {"$set": {"app_id": ctx.app_id, "user_id": user_id, "plan_id": plan_id, "status": "active"}},
            upsert=True,
        )

    async def deactivate(self, ctx, *, user_id):
        collection = app_data_from_context(ctx).collection("billing.subscriptions")
        result = await collection.update_one(
            {"app_id": ctx.app_id, "user_id": user_id, "status": "active"},
            {"$set": {"status": "cancelled"}},
        )
        return bool(result.modified_count)
