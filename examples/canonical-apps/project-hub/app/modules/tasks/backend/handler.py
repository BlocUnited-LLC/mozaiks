from .service import TasksService


class TasksHandler:
    async def create_task(self, ctx, **params):
        return await TasksService(ctx).create_task(params)

    async def list_tasks(self, ctx, **params):
        return await TasksService(ctx).list_tasks(params)
