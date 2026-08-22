from __future__ import annotations

from typing import Any

import pytest

from mozaiksai.core.runtime.app.definition import AppDefinition
from mozaiksai.core.runtime.app.loader import AppLoadResult
from mozaiksai.core.runtime.persistence import apply_database_indexes, collection_name_for
from mozaiksai.core.runtime.persistence.indexes import (
    DatabaseIndexApplyError,
    DataContractIndexRunResult,
)
from mozaiksai.core.runtime.persistence.mongo import (
    DEFAULT_APP_DATABASE_NAME,
    MongoPersistenceContext,
)


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    async def to_list(self, length: int | None = None):
        return list(self.rows)


class FakeMongoCollection:
    def __init__(self) -> None:
        self.index_rows: list[dict[str, Any]] = []
        self.create_index_calls: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []
        self.insert_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.list_indexes_error: Exception | None = None
        self.create_index_error: Exception | None = None
        self.persist_created_index = True

    def list_indexes(self):
        if self.list_indexes_error is not None:
            raise self.list_indexes_error
        return FakeCursor(self.index_rows)

    async def create_index(self, keys: list[tuple[str, int]], **kwargs: Any):
        if self.create_index_error is not None:
            raise self.create_index_error
        self.create_index_calls.append((keys, kwargs))
        name = str(kwargs.get("name") or "_".join(field for field, _ in keys))
        if self.persist_created_index:
            self.index_rows.append(
                {"name": name, "key": dict(keys), **{key: value for key, value in kwargs.items() if key != "name"}}
            )
        return name

    async def insert_one(self, document: dict[str, Any]):
        self.insert_calls.append(document)
        return {"inserted_id": document.get("_id")}

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False):
        self.update_calls.append((query, update))
        return {"matched_count": 1}


class FakeMotorCollectionWithCallableSubcollection(FakeMongoCollection):
    @property
    def ensure_indexes(self):  # noqa: ANN201
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("MotorCollection object is not callable")


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeMongoCollection] = {}

    def __getitem__(self, name: str) -> FakeMongoCollection:
        if name not in self.collections:
            self.collections[name] = FakeMongoCollection()
        return self.collections[name]


class FakeMongoClient:
    def __init__(self) -> None:
        self.databases: dict[str, FakeDatabase] = {}

    def __getitem__(self, name: str) -> FakeDatabase:
        if name not in self.databases:
            self.databases[name] = FakeDatabase()
        return self.databases[name]


def _intent(indexes: list[dict[str, Any]] | None = None, *, collection_name: str = "projects") -> dict[str, Any]:
    collection: dict[str, Any] = {
        "name": collection_name,
        "scope": "app",
        "ownership": {"surface_id": "projects", "surface_kind": "module"},
        "fields": [{"name": "app_id", "type": "string", "required": True}],
    }
    if indexes is not None:
        collection["indexes"] = indexes
    return {
        "version": "1",
        "app_id": "app_1",
        "surfaces": [
            {
                "surface_id": "projects",
                "surface_kind": "module",
                "collections": [collection],
            }
        ],
        "shared_collections": [],
        "policies": {"default_scope_field": "app_id"},
    }


def _context(client: FakeMongoClient | None = None) -> tuple[MongoPersistenceContext, FakeMongoClient]:
    fake_client = client or FakeMongoClient()
    return MongoPersistenceContext(app_id="app_1", app_slug="app", client=fake_client), fake_client


def _collection(client: FakeMongoClient, *, entity_name: str = "projects") -> FakeMongoCollection:
    name = collection_name_for(app_id="app_1", app_slug="app", module_id="projects", entity_name=entity_name)
    return client[DEFAULT_APP_DATABASE_NAME][name]


@pytest.mark.asyncio
async def test_apply_database_indexes_noops_when_intent_is_none() -> None:
    result = await apply_database_indexes(None, app_id="app_1")

    assert result.success is True
    assert result.verified == 0


@pytest.mark.asyncio
async def test_apply_database_indexes_skips_collections_without_indexes() -> None:
    context, client = _context()

    result = await apply_database_indexes(_intent(indexes=None), persistence=context)

    assert result.success is True
    assert result.verified == 0
    assert client.databases == {}


@pytest.mark.asyncio
async def test_apply_database_indexes_applies_single_field_index() -> None:
    context, client = _context()

    result = await apply_database_indexes(
        _intent([{"name": "project_id_idx", "keys": [{"field": "project_id", "order": 1}]}]),
        persistence=context,
    )

    assert result.created == 1
    assert result.verified == 1
    assert _collection(client).create_index_calls == [([("project_id", 1)], {"name": "project_id_idx"})]


@pytest.mark.asyncio
async def test_apply_database_indexes_supports_literal_contract_collection() -> None:
    context, client = _context()
    intent = _intent(
        [{"name": "workspace_id_idx", "keys": [{"field": "workspace_id", "order": 1}]}],
        collection_name="memberships",
    )
    intent["surfaces"][0]["collections"][0]["mongo_collection"] = "hosted_workspace_memberships"

    result = await apply_database_indexes(intent, persistence=context)

    assert result.created == 1
    assert result.verified == 1
    collection = client[DEFAULT_APP_DATABASE_NAME]["hosted_workspace_memberships"]
    assert collection.create_index_calls == [([("workspace_id", 1)], {"name": "workspace_id_idx"})]


@pytest.mark.asyncio
async def test_apply_database_indexes_ignores_motor_callable_subcollection_proxy() -> None:
    raw_collection = FakeMotorCollectionWithCallableSubcollection()

    class LiteralContext:
        def literal_collection(self, name: str) -> FakeMotorCollectionWithCallableSubcollection:
            return raw_collection

    intent = _intent(
        [{"name": "workspace_id_idx", "keys": [{"field": "workspace_id", "order": 1}]}],
        collection_name="memberships",
    )
    intent["surfaces"][0]["collections"][0]["mongo_collection"] = "hosted_workspace_memberships"

    result = await apply_database_indexes(intent, persistence=LiteralContext())  # type: ignore[arg-type]

    assert result.created == 1
    assert result.verified == 1
    assert raw_collection.create_index_calls == [([("workspace_id", 1)], {"name": "workspace_id_idx"})]


@pytest.mark.asyncio
async def test_apply_database_indexes_applies_compound_index() -> None:
    context, client = _context()

    await apply_database_indexes(
        _intent(
            [
                {
                    "name": "owner_created_at",
                    "keys": [{"field": "owner_id", "order": 1}, {"field": "created_at", "order": -1}],
                }
            ]
        ),
        persistence=context,
    )

    assert _collection(client).create_index_calls[0] == (
        [("owner_id", 1), ("created_at", -1)],
        {"name": "owner_created_at"},
    )


@pytest.mark.asyncio
async def test_apply_database_indexes_applies_unique_index() -> None:
    context, client = _context()

    await apply_database_indexes(
        _intent([{"name": "project_id_unique", "keys": [{"field": "project_id", "order": 1}], "unique": True}]),
        persistence=context,
    )

    assert _collection(client).create_index_calls[0] == (
        [("project_id", 1)],
        {"unique": True, "name": "project_id_unique"},
    )


@pytest.mark.asyncio
async def test_apply_database_indexes_supports_list_key_format() -> None:
    context, client = _context()

    await apply_database_indexes(
        _intent([{"name": "status_created_at", "keys": [["status", 1], ["created_at", -1]]}]),
        persistence=context,
    )

    assert _collection(client).create_index_calls[0] == (
        [("status", 1), ("created_at", -1)],
        {"name": "status_created_at"},
    )


@pytest.mark.asyncio
async def test_apply_database_indexes_uses_collection_name_for_app_module_entity() -> None:
    context, client = _context()

    await apply_database_indexes(
        _intent([{"name": "project_id_idx", "keys": [["project_id", 1]]}]),
        persistence=context,
    )

    expected = collection_name_for(app_id="app_1", app_slug="app", module_id="projects", entity_name="projects")
    assert expected in client[DEFAULT_APP_DATABASE_NAME].collections


@pytest.mark.asyncio
async def test_apply_database_indexes_does_not_create_duplicate_named_indexes_when_called_twice() -> None:
    context, client = _context()
    intent = _intent([{"name": "project_id_idx", "keys": [["project_id", 1]]}])

    await apply_database_indexes(intent, persistence=context)
    await apply_database_indexes(intent, persistence=context)

    assert len(_collection(client).create_index_calls) == 1


@pytest.mark.asyncio
async def test_apply_database_indexes_accepts_exact_materialized_definition() -> None:
    context, client = _context()
    raw = _collection(client)
    raw.index_rows.append(
        {
            "name": "refund_lookup",
            "key": {"tenant_ref": 1, "checkout_ref": -1},
            "unique": True,
            "sparse": True,
            "partialFilterExpression": {
                "state": {"$in": ["requested", "pending"]},
                "kind": "refund",
            },
            "collation": {"locale": "en", "strength": 2, "version": "57.1"},
            "hidden": True,
            "expireAfterSeconds": 900,
        }
    )
    intent = _intent(
        [
            {
                "name": "refund_lookup",
                "keys": [["tenant_ref", 1], ["checkout_ref", -1]],
                "unique": True,
                "sparse": True,
                "partialFilterExpression": {
                    "kind": "refund",
                    "state": {"$in": ["requested", "pending"]},
                },
                "collation": {"strength": 2, "locale": "en"},
                "hidden": True,
                "expireAfterSeconds": 900,
            }
        ]
    )

    result = await apply_database_indexes(intent, persistence=context)

    assert result.created == 0
    assert result.verified == 1
    assert raw.create_index_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("materialized", "expected_detail"),
    [
        ({"key": {"tenant_ref": 1, "checkout_ref": 1}}, "keys expected"),
        ({"key": {"tenant_ref": 1}, "unique": False}, "unique expected=True actual=False"),
        ({"key": {"tenant_ref": 1}, "sparse": False}, "sparse expected=True actual=False"),
        (
            {"key": {"tenant_ref": 1}, "partialFilterExpression": {"state": "captured"}},
            "partialFilterExpression",
        ),
        ({"key": {"tenant_ref": 1}, "expireAfterSeconds": 60}, "expireAfterSeconds"),
        ({"key": {"tenant_ref": 1}, "hidden": False}, "hidden expected=True actual=False"),
        ({"key": {"tenant_ref": 1}, "collation": {"locale": "fr"}}, "collation"),
    ],
)
async def test_apply_database_indexes_rejects_hostile_same_name_mismatch(
    materialized: dict[str, Any], expected_detail: str
) -> None:
    context, client = _context()
    raw = _collection(client)
    raw.index_rows.append({"name": "refund_guard", **materialized})
    declared: dict[str, Any] = {
        "name": "refund_guard",
        "keys": [["tenant_ref", 1]],
        "unique": True,
        "sparse": True,
        "partialFilterExpression": {"state": "refundable"},
        "expireAfterSeconds": 300,
        "hidden": True,
        "collation": {"locale": "en", "strength": 2},
    }

    with pytest.raises(DatabaseIndexApplyError, match=expected_detail):
        await apply_database_indexes(_intent([declared]), persistence=context)

    assert raw.create_index_calls == []
    assert raw.index_rows == [{"name": "refund_guard", **materialized}]


@pytest.mark.asyncio
async def test_apply_database_indexes_rejects_same_keys_other_name_with_incompatible_options() -> (
    None
):
    context, client = _context()
    raw = _collection(client)
    raw.index_rows.append({"name": "checkout_lookup_legacy", "key": {"checkout_ref": 1}})

    with pytest.raises(
        DatabaseIndexApplyError, match="under another name.*unique expected=True actual=False"
    ):
        await apply_database_indexes(
            _intent(
                [{"name": "refund_checkout_unique", "keys": [["checkout_ref", 1]], "unique": True}]
            ),
            persistence=context,
        )

    assert raw.create_index_calls == []


@pytest.mark.asyncio
async def test_apply_database_indexes_rejects_same_keys_under_compatible_other_name() -> None:
    context, client = _context()
    raw = _collection(client)
    raw.index_rows.append(
        {"name": "legacy_checkout_unique", "key": {"checkout_ref": 1}, "unique": True}
    )

    with pytest.raises(
        DatabaseIndexApplyError,
        match="under another name.*name expected='refund_checkout_unique' actual='legacy_checkout_unique'",
    ):
        await apply_database_indexes(
            _intent(
                [{"name": "refund_checkout_unique", "keys": [["checkout_ref", 1]], "unique": True}]
            ),
            persistence=context,
        )

    assert raw.create_index_calls == []


@pytest.mark.asyncio
async def test_apply_database_indexes_rejects_incompatible_alias_even_when_named_index_matches() -> (
    None
):
    context, client = _context()
    raw = _collection(client)
    raw.index_rows.extend(
        [
            {"name": "refund_checkout_unique", "key": {"checkout_ref": 1}, "unique": True},
            {"name": "legacy_checkout_lookup", "key": {"checkout_ref": 1}},
        ]
    )

    with pytest.raises(
        DatabaseIndexApplyError, match="legacy_checkout_lookup.*unique expected=True actual=False"
    ):
        await apply_database_indexes(
            _intent(
                [{"name": "refund_checkout_unique", "keys": [["checkout_ref", 1]], "unique": True}]
            ),
            persistence=context,
        )


@pytest.mark.asyncio
async def test_apply_database_indexes_rejects_conflicting_declarations_before_any_write() -> None:
    context, client = _context()

    with pytest.raises(
        DatabaseIndexApplyError, match="Conflicting declared indexes.*ordered key pattern"
    ):
        await apply_database_indexes(
            _intent(
                [
                    {"name": "lookup", "keys": [["reference", 1]]},
                    {"name": "unique_lookup", "keys": [["reference", 1]], "unique": True},
                ]
            ),
            persistence=context,
        )

    assert _collection(client).create_index_calls == []


@pytest.mark.asyncio
async def test_apply_database_indexes_creates_with_all_options_and_rereads() -> None:
    context, client = _context()
    declared = {
        "name": "checkout_refund_guard",
        "keys": [["tenant_ref", 1], ["checkout_ref", -1]],
        "unique": True,
        "sparse": True,
        "partialFilterExpression": {"kind": "refund"},
        "collation": {"locale": "en", "strength": 2},
        "expireAfterSeconds": 120,
        "hidden": True,
    }

    result = await apply_database_indexes(_intent([declared]), persistence=context)

    assert result.created == result.verified == 1
    keys, options = _collection(client).create_index_calls[0]
    assert keys == [("tenant_ref", 1), ("checkout_ref", -1)]
    assert options == {key: value for key, value in declared.items() if key != "keys"}


@pytest.mark.asyncio
async def test_apply_database_indexes_propagates_inspection_failure() -> None:
    context, client = _context()
    _collection(client).list_indexes_error = RuntimeError("inspection unavailable")

    with pytest.raises(DatabaseIndexApplyError, match="inspection unavailable"):
        await apply_database_indexes(
            _intent([{"name": "idx", "keys": [["field", 1]]}]), persistence=context
        )


@pytest.mark.asyncio
async def test_apply_database_indexes_propagates_creation_failure() -> None:
    context, client = _context()
    _collection(client).create_index_error = RuntimeError("create denied")

    with pytest.raises(DatabaseIndexApplyError, match="create denied"):
        await apply_database_indexes(
            _intent([{"name": "idx", "keys": [["field", 1]]}]), persistence=context
        )


@pytest.mark.asyncio
async def test_apply_database_indexes_fails_when_post_creation_verification_is_missing() -> None:
    context, client = _context()
    _collection(client).persist_created_index = False

    with pytest.raises(DatabaseIndexApplyError, match="missing after creation"):
        await apply_database_indexes(
            _intent([{"name": "idx", "keys": [["field", 1]]}]), persistence=context
        )


@pytest.mark.asyncio
async def test_apply_database_indexes_missing_module_id_fails_clearly() -> None:
    context, _ = _context()
    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])
    intent["surfaces"][0]["surface_id"] = ""
    intent["surfaces"][0]["collections"][0]["ownership"] = {}

    with pytest.raises(DatabaseIndexApplyError, match="module_id is required"):
        await apply_database_indexes(intent, persistence=context)


@pytest.mark.asyncio
async def test_apply_database_indexes_missing_entity_name_fails_clearly() -> None:
    context, _ = _context()

    with pytest.raises(DatabaseIndexApplyError, match="entity_name is required"):
        await apply_database_indexes(
            _intent([{"name": "idx", "keys": [["field", 1]]}], collection_name=""),
            persistence=context,
        )


@pytest.mark.asyncio
async def test_apply_database_indexes_invalid_index_shape_fails_clearly() -> None:
    context, _ = _context()

    with pytest.raises(DatabaseIndexApplyError, match="keys must be a non-empty list"):
        await apply_database_indexes(_intent([{"name": "idx", "keys": []}]), persistence=context)


@pytest.mark.asyncio
async def test_apply_database_indexes_does_not_write_or_update_documents() -> None:
    context, client = _context()

    await apply_database_indexes(_intent([{"name": "idx", "keys": [["field", 1]]}]), persistence=context)

    collection = _collection(client)
    assert collection.insert_calls == []
    assert collection.update_calls == []


@pytest.mark.asyncio
async def test_apply_database_indexes_does_not_mark_migrations_applied() -> None:
    context, client = _context()

    await apply_database_indexes(_intent([{"name": "idx", "keys": [["field", 1]]}]), persistence=context)

    db = client[DEFAULT_APP_DATABASE_NAME]
    assert not any("migration" in name.lower() for name in db.collections)


@pytest.mark.asyncio
async def test_platform_startup_applies_indexes_when_data_contract_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    monkeypatch.setenv("MONGO_URI", "mongodb://configured.invalid")

    calls: list[dict[str, Any]] = []
    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Intent Test", version="1.0"),
            modules=[],
            data_contract=intent,
            data_entities_by_key={},
        )

    async def fake_apply(data_contract, *, app_id=None):
        calls.append({"intent": data_contract, "app_id": app_id})
        return DataContractIndexRunResult(
            items=[], planned=1, created=1, skipped=0, conflicts=0, verified=1, dry_run=False, success=True
        )

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", fake_apply)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())

    await platform._platform_startup()

    assert calls == [{"intent": intent, "app_id": "app_1"}]


@pytest.mark.asyncio
async def test_platform_startup_without_data_contract_does_not_require_index_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.hosts import platform

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Local App", version="1.0"),
            modules=[],
            data_contract=None,
            data_entities_by_key={},
        )

    async def must_not_apply(_intent, *, app_id=None):
        raise AssertionError("non-persistent startup must not apply indexes")

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", must_not_apply)
    monkeypatch.setattr(platform, "load_data_migrations", lambda _root: [])
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())

    await platform._platform_startup()

    assert platform.app.state.database_index_readiness is None


@pytest.mark.asyncio
async def test_platform_startup_local_best_effort_with_contract_skips_index_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.hosts import platform

    for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL", "ENV", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Local App", version="1.0"),
            modules=[],
            data_contract=intent,
            data_entities_by_key={},
        )

    async def must_not_apply(_intent, *, app_id=None):
        raise AssertionError("persistence-disabled startup must not apply indexes")

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", must_not_apply)
    monkeypatch.setattr(platform, "load_data_migrations", lambda _root: [])
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())

    await platform._platform_startup()

    assert platform.app.state.database_index_readiness is None


@pytest.mark.asyncio
async def test_platform_startup_awaited_index_failure_cannot_be_swallowed_by_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    monkeypatch.setenv("MONGO_URI", "mongodb://configured.invalid")

    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Intent Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            data_contract=intent,
            data_entities_by_key={},
        )

    async def fail_apply(_intent, *, app_id=None):
        raise RuntimeError("index failure")

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.delenv("MOZAIKS_DATABASE_STARTUP_POLICY", raising=False)
    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", fail_apply)
    monkeypatch.setattr(platform, "load_data_migrations", lambda _root: [])
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())
    with pytest.raises(platform.DatabaseStartupError, match="Database indexes are not ready"):
        await platform._platform_startup()


@pytest.mark.asyncio
async def test_platform_startup_does_not_report_index_readiness_before_awaited_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from mozaiksai.hosts import platform

    monkeypatch.setenv("MONGO_URI", "mongodb://configured.invalid")

    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])
    verification_started = asyncio.Event()
    release_verification = asyncio.Event()

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(
                name="Readiness Test", version="1.0", config={"appId": "app_1"}
            ),
            modules=[],
            data_contract=intent,
            data_entities_by_key={},
        )

    async def delayed_apply(_intent, *, app_id=None):
        verification_started.set()
        await release_verification.wait()
        return DataContractIndexRunResult(
            items=[],
            planned=1,
            created=1,
            skipped=0,
            conflicts=0,
            verified=1,
            dry_run=False,
            success=True,
        )

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", delayed_apply)
    monkeypatch.setattr(platform, "load_data_migrations", lambda _root: [])
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())

    startup = asyncio.create_task(platform._platform_startup())
    await verification_started.wait()
    assert startup.done() is False
    assert platform.app.state.database_index_readiness is None

    release_verification.set()
    await startup
    assert platform.app.state.database_index_readiness.verified == 1


@pytest.mark.asyncio
async def test_platform_startup_required_index_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Intent Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            data_contract=intent,
            data_entities_by_key={},
        )

    async def fail_apply(_intent, *, app_id=None):
        raise RuntimeError("index failure")

    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "required")
    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", fail_apply)

    with pytest.raises(platform.DatabaseStartupError, match="Database indexes are not ready"):
        await platform._platform_startup()
