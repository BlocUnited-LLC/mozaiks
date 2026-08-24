from .service import ResearchService


class ResearchHandler:
    async def summarize_source(self, ctx, **params):
        return await ResearchService(ctx).summarize(params)

    async def start_research(self, ctx, **params):
        return await ResearchService(ctx).start(params)
