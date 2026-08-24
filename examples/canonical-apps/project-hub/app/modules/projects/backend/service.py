from uuid import uuid4

from .policy import project_query
from .repo import ProjectsRepo


class ProjectsService:
    def __init__(self, ctx):
        self.repo = ProjectsRepo(ctx)

    async def create_project(self, params):
        project = {
            "id": str(uuid4()),
            "name": params["name"],
            "status": params.get("status", "planned"),
        }
        return await self.repo.create(project)

    async def list_projects(self, params):
        items = await self.repo.list(project_query(params))
        return {"items": items, "count": len(items)}
