from .service import ProjectsService


class ProjectsHandler:
    async def create_project(self, ctx, **params):
        return await ProjectsService(ctx).create_project(params)

    async def list_projects(self, ctx, **params):
        return await ProjectsService(ctx).list_projects(params)
