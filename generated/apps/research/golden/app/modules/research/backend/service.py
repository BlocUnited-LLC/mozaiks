from .repo import ResearchRepo
from .schemas import research_request


class ResearchService:
    async def list_results(self, ctx, **_params):
        return {"results": await ResearchRepo(ctx).list_results()}

    async def execute_research(self, ctx, *, query, **_params):
        record = research_request(query=query)
        await ResearchRepo(ctx).save(record)
        return {"research_id": record["research_id"], "status": record["status"]}
