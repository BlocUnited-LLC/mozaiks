class ProjectsRepo:
    def __init__(self, ctx):
        self.collection = ctx.persistence.collection("projects", "projects")

    async def create(self, project):
        await self.collection.insert_one(project)
        return project

    async def list(self, query):
        return await self.collection.find_many(query, limit=100)
