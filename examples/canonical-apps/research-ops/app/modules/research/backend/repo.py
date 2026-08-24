class ResearchRepo:
    def __init__(self, ctx):
        self.collection = ctx.persistence.collection("research", "summaries")

    async def save(self, summary):
        await self.collection.insert_one(summary)
        return summary
