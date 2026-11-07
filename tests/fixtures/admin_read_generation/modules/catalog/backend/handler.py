class CatalogModule:
    async def list_inventory(self, ctx):
        return {"items": [{"name": "Example", "status": "ready"}]}

    async def add_entry(self, ctx):
        return {"created": True}

    async def delete_entry(self, ctx):
        return {"deleted": True}
