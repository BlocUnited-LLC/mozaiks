def _entries(ctx):
    return ctx.persistence.collection("content", "entries")


class ContentRepo:
    async def list(self, ctx, *, query, limit, after=None):
        if after is not None:
            query = {**query, "entry_id": {"$gt": after}}
        return await _entries(ctx).find_many(query, limit=limit, sort=[("entry_id", 1)], projection={"_id": 0})

    async def get(self, ctx, *, entry_id, query=None):
        return await _entries(ctx).find_one({**(query or {}), "entry_id": entry_id}, {"_id": 0})

    async def insert(self, ctx, *, record):
        await _entries(ctx).insert_one(record)

    async def update(self, ctx, *, entry_id, changes):
        await _entries(ctx).update_one({"entry_id": entry_id}, {"$set": changes})
        return await self.get(ctx, entry_id=entry_id)
