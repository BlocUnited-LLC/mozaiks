from uuid import uuid4

from mozaiksai.core.runtime.composition.module_executor import (
    ModuleInputValidationError,
    ModuleRecordNotFoundError,
)
from mozaiksai.core.runtime.persistence import is_unique_constraint_violation

from . import schemas
from .policy import require_manage, visibility_query
from .repo import ContentRepo


class ContentService:
    def __init__(self, repo=None):
        self.repo = repo if repo is not None else ContentRepo()

    async def list_entries(self, ctx, *, limit=20, after=None, status=None):
        page_size = schemas.validate_limit(limit)
        if after is not None:
            schemas.validate_entry_id(after)
        if status is not None:
            schemas.validate_status(status)
        rows = await self.repo.list(
            ctx, query=visibility_query(ctx, status=status), limit=page_size + 1, after=after,
        )
        entries = [schemas.serialize_summary(row) for row in rows[:page_size]]
        return {"entries": entries, "count": len(entries),
                "next_cursor": entries[-1]["entry_id"] if len(rows) > page_size else None}

    async def get_entry(self, ctx, *, entry_id):
        schemas.validate_entry_id(entry_id)
        record = await self.repo.get(ctx, entry_id=entry_id, query=visibility_query(ctx))
        if record is None:
            raise ModuleRecordNotFoundError("Content entry not found")
        return {"entry": schemas.serialize_entry(record)}

    async def create_entry(self, ctx, *, title, slug=None, summary="", body=""):
        require_manage(ctx)
        supplied = {"title": title, "summary": summary, "body": body}
        if slug is not None:
            supplied["slug"] = slug
        fields = schemas.validate_fields(supplied, creating=True)
        entry_id = "ent_" + uuid4().hex
        fields.setdefault("slug", schemas.generated_slug(fields["title"], entry_id))
        now = schemas.timestamp_now()
        record = {**fields, "entry_id": entry_id, "status": "draft", "created_at": now, "updated_at": now}
        try:
            await self.repo.insert(ctx, record=record)
        except Exception as error:
            if not is_unique_constraint_violation(error):
                raise
            raise ModuleInputValidationError("A content entry with this slug already exists") from error
        await ctx.emit("domain.content.entry_created", {"entry_id": record["entry_id"], "status": record["status"]})
        return {"entry": schemas.serialize_entry(record)}

    async def update_entry(self, ctx, *, entry_id, **changes):
        require_manage(ctx)
        schemas.validate_entry_id(entry_id)
        fields = schemas.validate_fields(changes)
        if await self.repo.get(ctx, entry_id=entry_id) is None:
            raise ModuleRecordNotFoundError("Content entry not found")
        try:
            record = await self.repo.update(ctx, entry_id=entry_id, changes={**fields, "updated_at": schemas.timestamp_now()})
        except Exception as error:
            if not is_unique_constraint_violation(error):
                raise
            raise ModuleInputValidationError("A content entry with this slug already exists") from error
        if record is None:
            raise ModuleRecordNotFoundError("Content entry not found")
        await ctx.emit("domain.content.entry_updated", {"entry_id": record["entry_id"], "status": record["status"]})
        return {"entry": schemas.serialize_entry(record)}

    async def archive_entry(self, ctx, *, entry_id):
        require_manage(ctx)
        schemas.validate_entry_id(entry_id)
        existing = await self.repo.get(ctx, entry_id=entry_id)
        if existing is None:
            raise ModuleRecordNotFoundError("Content entry not found")
        if existing["status"] == "archived":
            return {"entry": schemas.serialize_entry(existing)}
        record = await self.repo.update(ctx, entry_id=entry_id, changes={"status": "archived", "updated_at": schemas.timestamp_now()})
        if record is None:
            raise ModuleRecordNotFoundError("Content entry not found")
        await ctx.emit("domain.content.entry_archived", {"entry_id": record["entry_id"], "status": record["status"]})
        return {"entry": schemas.serialize_entry(record)}
