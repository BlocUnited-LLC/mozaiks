from .service import ResearchService


class ResearchHandler:
    def __init__(self):
        self.service = ResearchService()

    async def list_results(self, ctx, **params):
        return await self.service.list_results(ctx, **params)

    async def execute_research(self, ctx, **params):
        return await self.service.execute_research(ctx, **params)
