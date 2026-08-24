from .service import IncidentsService


class IncidentsHandler:
    async def create_incident(self, ctx, **params):
        return await IncidentsService(ctx).create(params)

    async def list_incidents(self, ctx, **params):
        return await IncidentsService(ctx).list(params)
