class ReportsRepo:
    def __init__(self, ctx):
        self.collection = ctx.persistence.collection("reports", "reports")

    async def list(self, query):
        return await self.collection.find_many(query, limit=100)
