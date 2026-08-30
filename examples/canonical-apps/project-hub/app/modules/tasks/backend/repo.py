class TasksRepo:
    def __init__(self, ctx):
        self.collection = ctx.persistence.collection("tasks", "tasks")

    async def create(self, task):
        await self.collection.insert_one(task)
        return task

    async def list(self, query):
        return await self.collection.find_many(query, limit=100)
