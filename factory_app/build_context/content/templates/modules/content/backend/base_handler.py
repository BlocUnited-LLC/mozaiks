"""Template-owned content dispatch. DO NOT EDIT generated copies."""

from .service import ContentService


class ContentBaseHandler:
    def __init__(self):
        self.service = ContentService()

    async def list_entries(self, ctx, *, limit=20, after=None, status=None):
        return await self.service.list_entries(ctx, limit=limit, after=after, status=status)

    async def get_entry(self, ctx, *, entry_id):
        return await self.service.get_entry(ctx, entry_id=entry_id)

    async def create_entry(self, ctx, *, title, slug=None, summary="", body=""):
        return await self.service.create_entry(ctx, title=title, slug=slug, summary=summary, body=body)

    async def update_entry(self, ctx, *, entry_id, **changes):
        return await self.service.update_entry(ctx, entry_id=entry_id, **changes)

    async def archive_entry(self, ctx, *, entry_id):
        return await self.service.archive_entry(ctx, entry_id=entry_id)
