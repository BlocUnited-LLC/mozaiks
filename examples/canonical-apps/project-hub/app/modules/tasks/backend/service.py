from uuid import uuid4

from .policy import task_query
from .repo import TasksRepo


class TasksService:
    def __init__(self, ctx):
        self.repo = TasksRepo(ctx)

    async def create_task(self, params):
        task = {
            "id": str(uuid4()),
            "title": params["title"],
            "project_id": params["project_id"],
            "status": params.get("status", "todo"),
        }
        return await self.repo.create(task)

    async def list_tasks(self, params):
        items = await self.repo.list(task_query(params))
        return {"items": items, "count": len(items)}
