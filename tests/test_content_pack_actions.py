"""Editorial action behavior against bounded app-scoped in-memory persistence.

These tests exercise shipped service/repository code and executor contracts;
they do not establish MongoDB, browser, or fresh-generation acceptance.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority
from mozaiksai.core.runtime.composition.module_executor import (
    ModuleExecutor,
    ModuleInputValidationError,
    ModuleRecordNotFoundError,
    ModuleRequest,
)
from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
    load_module_smoke_fixtures,
)
from tests.content_smoke_fixtures import content_smoke_fixtures

PACK = Path(__file__).resolve().parents[1] / "factory_app/build_context/content"
BACKEND = PACK / "templates/modules/content/backend"


def test_request_contracts_and_authored_fixtures_use_the_supported_closed_profile():
    manifest = (PACK / "templates/modules/content/module.yaml").read_text(encoding="utf-8")
    actions = yaml.safe_load(manifest)["actions"]
    for action in actions:
        import_closed_contract_schema(action["input_schema"])
    auth = yaml.safe_load((PACK.parent / "webapp_builder/templates/config/auth.yaml").read_text(
        encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
    auth["authorization"] = {"roles": [{"id": "content_editor", "permissions": ["content.manage"]}]}
    fixtures = load_module_smoke_fixtures({
        "modules/content/module.yaml": manifest,
        "modules/content/contracts/fixtures.yaml": content_smoke_fixtures("content_editor"),
        "config/auth.yaml": yaml.safe_dump(auth),
        "data/contract.json": (PACK / "templates/data/contract.json").read_text(encoding="utf-8"),
    })
    assert set(fixtures) == {("content", action) for action in ("create_entry", "update_entry", "archive_entry")}


class MemoryPersistence:
    """A test collection with app-isolated storage and the repo's bounded operations."""

    def __init__(self, app_id, store=None):
        self.app_id = app_id
        self.store = {} if store is None else store
        self.calls = []

    def collection(self, module, entity):
        assert (module, entity) == ("content", "entries")
        self.calls.append((module, entity))
        return self

    @property
    def rows(self):
        return self.store.setdefault(self.app_id, [])

    @staticmethod
    def _matches(row, query):
        return all(row.get(key) > value["$gt"] if isinstance(value, dict)
                   else row.get(key) == value for key, value in query.items())

    async def find_many(self, query, *, limit, sort, projection):
        rows = [row for row in self.rows if self._matches(row, query)]
        for key, direction in reversed(sort):
            rows.sort(key=lambda row: row[key], reverse=direction < 0)
        return deepcopy(rows[:limit])

    async def find_one(self, query, projection=None):
        return next((deepcopy(row) for row in self.rows if self._matches(row, query)), None)

    async def insert_one(self, record):
        if any(row["slug"] == record["slug"] for row in self.rows):
            raise DuplicateKeyError("duplicate slug")
        self.rows.append(deepcopy(record))
        return SimpleNamespace(inserted_id=record["entry_id"])

    async def update_one(self, query, update):
        row = next((row for row in self.rows if self._matches(row, query)), None)
        if row is None:
            return SimpleNamespace(matched_count=0)
        slug = update["$set"].get("slug", row["slug"])
        if any(other is not row and other["slug"] == slug for other in self.rows):
            raise DuplicateKeyError("duplicate slug")
        row.update(deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=1)


@pytest.fixture(scope="module")
def backend():
    name = "_content_pack_action_test_backend"
    spec = importlib.util.spec_from_file_location(name, BACKEND / "__init__.py", submodule_search_locations=[str(BACKEND)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    try:
        yield SimpleNamespace(service=importlib.import_module(name + ".service"),
                              handler=importlib.import_module(name + ".handler"))
    finally:
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                del sys.modules[key]


def context(*, manage=True, app_id="app-a", store=None):
    return SimpleNamespace(app_id=app_id, user_id="editor" if manage else None,
                           permissions=["content.manage"] if manage else [],
                           persistence=MemoryPersistence(app_id, store), emit=AsyncMock())


async def create(service, ctx, *, slug="field-notes"):
    return (await service.create_entry(ctx, title="Field notes", slug=slug, summary="Observations", body="Plain text"))["entry"]


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["Field Notes!", "An extended title " * 10, "日本語"])
async def test_omitted_slugs_are_safe_and_unique_for_repeated_titles(backend, title):
    service, ctx = backend.service.ContentService(), context()
    first = (await service.create_entry(ctx, title=title))["entry"]
    second = (await service.create_entry(ctx, title=title))["entry"]
    schema = yaml.safe_load((PACK / "templates/modules/content/module.yaml").read_text(encoding="utf-8"))["actions"][2]
    assert validate_json_schema({"title": title}, schema["input_schema"]) is None
    assert first["title"] == second["title"] == title.strip()
    assert first["slug"] != second["slug"]
    for entry in (first, second):
        assert len(entry["slug"]) <= 100
        assert entry["slug"].endswith(entry["entry_id"].removeprefix("ent_"))
        assert validate_json_schema({"entry": entry}, schema["output_schema"]) is None
    assert len(ctx.persistence.rows) == ctx.emit.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["", "Upper Case", "two--hyphens", "slash/segment", "x" * 101])
async def test_explicit_invalid_create_slug_is_rejected_before_persistence(backend, slug):
    ctx = context()
    with pytest.raises(ModuleInputValidationError):
        await backend.service.ContentService().create_entry(ctx, title="Valid title", slug=slug)
    assert ctx.persistence.calls == []
    ctx.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_publish_archive_and_public_visibility(backend):
    service = backend.service.ContentService()
    editor = context()
    public = context(manage=False, store=editor.persistence.store)
    entry = await create(service, editor)
    assert entry["status"] == "draft"
    assert set(entry) == {"entry_id", "title", "slug", "summary", "body", "status", "created_at", "updated_at"}
    assert (await service.list_entries(public))["entries"] == []
    assert (await service.list_entries(public, status="draft"))["entries"] == []
    assert (await service.list_entries(editor, status="draft"))["entries"] == [{k: v for k, v in entry.items() if k != "body"}]
    with pytest.raises(ModuleRecordNotFoundError):
        await service.get_entry(public, entry_id=entry["entry_id"])
    published = (await service.update_entry(editor, entry_id=entry["entry_id"], status="published"))["entry"]
    assert (await service.get_entry(public, entry_id=entry["entry_id"]))["entry"] == published
    listed = (await service.list_entries(public, status="draft"))["entries"]
    assert listed == [{k: v for k, v in published.items() if k != "body"}]
    assert "body" not in listed[0]
    archived = (await service.archive_entry(editor, entry_id=entry["entry_id"]))["entry"]
    assert archived["status"] == "archived" and len(editor.persistence.rows) == 1
    assert (await service.list_entries(public))["entries"] == []
    with pytest.raises(ModuleRecordNotFoundError):
        await service.get_entry(public, entry_id=entry["entry_id"])
    assert (await service.get_entry(editor, entry_id=entry["entry_id"]))["entry"] == archived
    assert (await service.archive_entry(editor, entry_id=entry["entry_id"]))["entry"] == archived
    assert [call.args[0] for call in editor.emit.await_args_list] == [
        "domain.content.entry_created", "domain.content.entry_updated", "domain.content.entry_archived",
    ]
    assert editor.persistence.calls and set(editor.persistence.calls) == {("content", "entries")}


@pytest.mark.asyncio
@pytest.mark.parametrize("action,params", [
    ("create_entry", {"title": "Denied", "slug": "denied"}),
    ("update_entry", {"entry_id": "ent_" + "1" * 32, "title": "Denied"}),
    ("archive_entry", {"entry_id": "ent_" + "1" * 32}),
])
async def test_writes_require_exact_manage_permission_before_persistence(backend, action, params):
    ctx = context(manage=False)
    ctx.permissions = ["ops.admin"]
    with pytest.raises(PermissionError):
        await getattr(backend.service.ContentService(), action)(ctx, **params)
    assert ctx.persistence.calls == []
    ctx.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_anonymous_context_cannot_upgrade_itself_with_a_permission_string(backend):
    service, ctx = backend.service.ContentService(), context(manage=False)
    ctx.permissions = ["content.manage"]
    with pytest.raises(PermissionError):
        await create(service, ctx)
    assert ctx.persistence.calls == []
    ctx.persistence.rows.append({"entry_id": "ent_" + "1" * 32, "title": "Private", "slug": "private",
                                 "summary": "", "body": "", "status": "draft", "created_at": "same", "updated_at": "same"})
    assert (await service.list_entries(ctx))["entries"] == []
    with pytest.raises(ModuleRecordNotFoundError):
        await service.get_entry(ctx, entry_id="ent_" + "1" * 32)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{}, {"title": " "}, {"slug": "Upper Case"}, {"body": None},
                                    {"status": "unknown"}, {"created_at": "spoofed"}, {"user_id": "spoofed"},
                                    {"app_id": "another"}, {"title": "x" * 201}])
async def test_invalid_updates_cannot_touch_storage_or_emit(backend, changes):
    ctx = context()
    with pytest.raises(ModuleInputValidationError):
        await backend.service.ContentService().update_entry(ctx, entry_id="ent_" + "1" * 32, **changes)
    assert ctx.persistence.calls == []
    ctx.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_field_updates_preserve_omitted_fields_and_server_identity(backend):
    service, ctx = backend.service.ContentService(), context()
    entry = await create(service, ctx)
    changed = (await service.update_entry(ctx, entry_id=entry["entry_id"], summary="", body=""))["entry"]
    assert changed["summary"] == changed["body"] == ""
    for field in ("entry_id", "slug", "title", "status", "created_at"):
        assert changed[field] == entry[field]


@pytest.mark.asyncio
async def test_unique_slugs_and_app_scope_are_enforced_by_context_storage(backend):
    service, ctx = backend.service.ContentService(), context()
    entry = await create(service, ctx)
    with pytest.raises(ModuleInputValidationError):
        await create(service, ctx)
    second = await create(service, ctx, slug="second")
    with pytest.raises(ModuleInputValidationError):
        await service.update_entry(ctx, entry_id=second["entry_id"], slug=entry["slug"])
    other = context(app_id="app-b", store=ctx.persistence.store)
    with pytest.raises(ModuleRecordNotFoundError):
        await service.update_entry(other, entry_id=entry["entry_id"], title="Wrong app")
    with pytest.raises(ModuleRecordNotFoundError):
        await service.archive_entry(other, entry_id=entry["entry_id"])
    assert (await create(service, other))["slug"] == entry["slug"]
    assert len(ctx.persistence.rows) == 2 and len(other.persistence.rows) == 1
    assert ctx.emit.await_count == 2


@pytest.mark.asyncio
async def test_cursor_pages_are_stable_for_equal_timestamps_and_filter_before_limit(backend):
    service, ctx = backend.service.ContentService(), context(manage=False)
    for n in (4, 2, 1, 3):
        ctx.persistence.rows.append({"entry_id": "ent_" + f"{n:032x}", "title": str(n), "slug": str(n),
                                     "summary": "", "body": "", "status": "draft" if n == 2 else "published",
                                     "created_at": "same", "updated_at": "same"})
    first = await service.list_entries(ctx, limit=2)
    second = await service.list_entries(ctx, limit=2, after=first["next_cursor"])
    assert [row["title"] for row in first["entries"]] == ["1", "3"]
    assert [row["title"] for row in second["entries"]] == ["4"]
    assert first["count"] == 2 and second["count"] == 1 and second["next_cursor"] is None
    for value in (0, -1, 51, True, "2"):
        with pytest.raises(ModuleInputValidationError):
            await service.list_entries(ctx, limit=value)
    with pytest.raises(ModuleInputValidationError):
        await service.list_entries(ctx, after="invalid")


@pytest.mark.asyncio
async def test_repository_failure_propagates_without_claiming_success_or_emitting(backend):
    ctx = context()
    repo = SimpleNamespace(insert=AsyncMock(side_effect=RuntimeError("database unavailable")))
    with pytest.raises(RuntimeError, match="database unavailable"):
        await create(backend.service.ContentService(repo), ctx)
    ctx.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_rejects_missing_permissions_and_undeclared_input(backend):
    manifest = yaml.safe_load((PACK / "templates/modules/content/module.yaml").read_text(encoding="utf-8"))
    actions = {row["id"]: row for row in manifest["actions"]}
    executor = ModuleExecutor()
    executor.register("content", backend.handler.ContentHandler(),
                      action_method_map={key: row["handler_method"] for key, row in actions.items()},
                      action_permissions={key: row["permissions"] for key, row in actions.items()},
                      action_schemas={key: {"input": row["input_schema"], "output": row["output_schema"]}
                                      for key, row in actions.items()})
    for permissions, params, expected in [
        ((), {"title": "Denied", "slug": "denied"}, "PERMISSION_DENIED"),
        (("content.manage",), {"title": "Invalid", "slug": "valid", "app_id": "spoofed"}, "INVALID_PARAMS"),
    ]:
        result = await executor.execute(ModuleRequest(
            module="content", action="create_entry", app_id="app-a", params=params,
            authority=ModuleDispatchAuthority(kind="authenticated_user", permission_mode="enforce",
                                              reason="Content pack test", permissions=permissions),
        ), context=context())
        assert not result.success and result.error_code == expected
    ctx = context()
    created = await create(backend.service.ContentService(), ctx)
    assert validate_json_schema({"entry": created}, actions["create_entry"]["output_schema"]) is None
