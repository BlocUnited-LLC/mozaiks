from uuid import uuid4

from .policy import incident_query
from .repo import IncidentsRepo


class IncidentsService:
    def __init__(self, ctx):
        self.repo = IncidentsRepo(ctx)

    async def create(self, params):
        incident = {
            "id": str(uuid4()),
            "title": params["title"],
            "status": params.get("status", "open"),
        }
        return await self.repo.create(incident)

    async def list(self, params):
        items = await self.repo.list(incident_query(params))
        return {"items": items, "count": len(items)}
