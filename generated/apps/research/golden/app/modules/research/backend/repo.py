class ResearchRepo:
    def __init__(self, ctx):
        self.collection = ctx.persistence.collection("research", "research_results")

    async def list_results(self):
        cursor = self.collection.find({})
        return await cursor.to_list(length=100)

    async def save(self, record):
        await self.collection.insert_one(record)
        return record
