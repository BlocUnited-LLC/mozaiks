from .service import EntitlementDispatchService


class EntitlementDispatchHandler:
    async def assign_subscription(self, ctx, **params):
        return await EntitlementDispatchService().assign_subscription(ctx, **params)

    async def deactivate_subscription(self, ctx, **params):
        return await EntitlementDispatchService().deactivate_subscription(ctx, **params)
