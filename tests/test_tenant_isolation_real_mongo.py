"""Cross-tenant denial, proved against a real MongoDB rather than a filter object.

The existing helper tests assert that `build_app_scope_filter` *builds* the right
query. These assert that the datastore *returns nothing* when one tenant reaches
for another's records — the claim an enterprise security review actually asks
about.

Run with MOZAIKS_RUN_REAL_MONGO_TESTS=1 and a reachable MONGO_URI.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from mozaiksai.core.multitenant import (
    build_app_scope_filter,
    dual_write_app_scope,
    normalize_app_id,
)

RUN_ENV = "MOZAIKS_RUN_REAL_MONGO_TESTS"
LEGACY_RUN_ENV = "MOZAIKS_RUN_MONGO_INTEGRATION"

pytestmark = pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1" and os.getenv(LEGACY_RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run the real-Mongo tenant isolation proof",
)


def _mongo_uri() -> str:
    for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL"):
        value = os.getenv(name)
        if value:
            return value
    return "mongodb://localhost:27017"


@pytest.fixture
async def collection():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(_mongo_uri(), serverSelectionTimeoutMS=5000)
    database = client[f"mozaiks_isolation_{uuid4().hex[:12]}"]
    try:
        yield database["records"]
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.fixture
def tenants() -> tuple[str, str]:
    run = uuid4().hex[:8]
    return f"app_a_{run}", f"app_b_{run}"


async def _seed(collection, app_id: str, secret: str) -> None:
    await collection.insert_one(
        dual_write_app_scope({"secret": secret, "kind": "record"}, app_id)
    )


class TestCrossTenantReads:
    async def test_tenant_cannot_read_another_tenants_document(self, collection, tenants):
        app_a, app_b = tenants
        await _seed(collection, app_a, "a-only")
        await _seed(collection, app_b, "b-only")

        visible = await collection.find(build_app_scope_filter(app_a)).to_list(length=100)

        assert [doc["secret"] for doc in visible] == ["a-only"]

    async def test_scoped_find_one_never_returns_a_foreign_document(self, collection, tenants):
        app_a, app_b = tenants
        await _seed(collection, app_b, "b-only")

        assert await collection.find_one(build_app_scope_filter(app_a)) is None

    async def test_scoped_count_excludes_foreign_documents(self, collection, tenants):
        app_a, app_b = tenants
        for index in range(3):
            await _seed(collection, app_b, f"b-{index}")
        await _seed(collection, app_a, "a-0")

        assert await collection.count_documents(build_app_scope_filter(app_a)) == 1

    async def test_blank_app_id_reads_nothing_rather_than_everything(self, collection, tenants):
        app_a, app_b = tenants
        await _seed(collection, app_a, "a-only")
        await _seed(collection, app_b, "b-only")

        # A dropped tenant id must fail closed. Returning the whole collection
        # here is the classic multi-tenant breach.
        for blank in ("", "   ", None):
            found = await collection.find(build_app_scope_filter(blank)).to_list(length=100)
            assert found == [], f"blank app_id {blank!r} matched {len(found)} documents"

    async def test_a_tenant_id_that_looks_like_a_query_operator_is_not_interpreted(
        self, collection, tenants
    ):
        app_a, _ = tenants
        await _seed(collection, app_a, "a-only")

        # Mongo compares this as a literal string; it must not behave as $ne/$gt.
        hostile = {"$ne": None}
        found = await collection.find({"app_id": normalize_app_id(hostile)}).to_list(length=100)

        assert found == []


class TestCrossTenantWrites:
    async def test_scoped_update_cannot_modify_another_tenants_document(self, collection, tenants):
        app_a, app_b = tenants
        await _seed(collection, app_b, "b-only")

        result = await collection.update_many(
            build_app_scope_filter(app_a), {"$set": {"secret": "overwritten"}}
        )

        assert result.modified_count == 0
        survivor = await collection.find_one(build_app_scope_filter(app_b))
        assert survivor["secret"] == "b-only"

    async def test_scoped_delete_cannot_remove_another_tenants_document(self, collection, tenants):
        app_a, app_b = tenants
        await _seed(collection, app_b, "b-only")

        result = await collection.delete_many(build_app_scope_filter(app_a))

        assert result.deleted_count == 0
        assert await collection.count_documents(build_app_scope_filter(app_b)) == 1

    async def test_every_written_document_carries_its_tenant_tag(self, collection, tenants):
        app_a, _ = tenants
        await _seed(collection, app_a, "a-only")

        # An untagged document is invisible to every tenant filter but visible
        # to any query that forgets one.
        untagged = await collection.count_documents({"app_id": {"$exists": False}})
        assert untagged == 0
