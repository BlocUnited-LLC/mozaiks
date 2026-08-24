class IncidentsRepo:
    def __init__(self, ctx):
        self.collection = ctx.persistence.collection("incidents", "incidents")

    async def create(self, incident):
        await self.collection.insert_one(incident)
        return incident

    async def list(self, query):
        return await self.collection.find_many(query, limit=100)
