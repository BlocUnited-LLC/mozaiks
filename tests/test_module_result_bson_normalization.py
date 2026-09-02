"""Module action results must be JSON-safe at the executor boundary.

Generated app modules return raw Mongo documents; without normalization a
document whose ``_id`` is an ``ObjectId`` reached FastAPI's serializer and
produced a bare HTTP 500 on every list/read action. ``json_safe_bson`` at the
ModuleExecutor success boundary is the single normalization authority.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson
from mozaiksai.core.runtime.composition.module_executor import (
    ModuleExecutor,
    ModuleRequest,
)
from tests.module_authority_test_helpers import trusted_framework_authority


def _request(module: str, action: str, params: dict | None = None) -> ModuleRequest:
    return ModuleRequest(
        module=module,
        action=action,
        params=params or {},
        app_id="app-bson",
        user_id="user-1",
        authority=trusted_framework_authority(),
    )


# ---------------------------------------------------------------------------
# Normalizer unit behavior
# ---------------------------------------------------------------------------


def test_object_id_becomes_stable_hex_string_everywhere() -> None:
    oid = ObjectId()
    nested_oid = ObjectId()
    key_oid = ObjectId()
    document = {
        "_id": oid,
        "items": [{"ref": nested_oid}, (oid, "pair")],
        key_oid: "keyed",
    }

    normalized = json_safe_bson(document)

    assert normalized["_id"] == str(oid)
    assert normalized["items"][0]["ref"] == str(nested_oid)
    assert normalized["items"][1] == [str(oid), "pair"]
    assert normalized[str(key_oid)] == "keyed"
    assert json.loads(json.dumps(normalized))["_id"] == str(oid)


def test_decimal128_is_lossless_and_json_safe() -> None:
    value = Decimal128("1234567890.123456789012345678901234")
    normalized = json_safe_bson({"amount": value})
    assert normalized["amount"] == "1234567890.123456789012345678901234"
    assert Decimal(normalized["amount"]) == value.to_decimal()


def test_non_bson_values_pass_through_unchanged() -> None:
    class _Opaque:
        pass

    stamp = datetime.now(UTC)
    opaque = _Opaque()
    normalized = json_safe_bson(
        {"when": stamp, "opaque": opaque, "n": 3, "flag": True, "none": None}
    )
    # datetime stays owned by the platform's existing encoder; unknown types
    # are never coerced through a repr.
    assert normalized["when"] is stamp
    assert normalized["opaque"] is opaque
    assert normalized["n"] == 3
    assert normalized["flag"] is True
    assert normalized["none"] is None


def test_input_documents_are_not_mutated() -> None:
    oid = ObjectId()
    document = {"_id": oid, "nested": {"ref": oid}, "items": [oid]}
    json_safe_bson(document)
    assert document["_id"] is oid
    assert document["nested"]["ref"] is oid
    assert document["items"][0] is oid


def test_containers_are_handled_deterministically() -> None:
    oid = ObjectId()
    assert json_safe_bson((1, oid)) == [1, str(oid)]
    assert json_safe_bson([{"a": (oid,)}]) == [{"a": [str(oid)]}]
    assert json_safe_bson({}) == {}
    assert json_safe_bson([]) == []


# ---------------------------------------------------------------------------
# Executor boundary behavior
# ---------------------------------------------------------------------------


class _MongoShapedHandler:
    """Returns document shapes exactly as generated repo code produces them."""

    def __init__(self) -> None:
        self.created_id = ObjectId()

    def list_records(self, ctx, **params) -> dict:
        return {
            "items": [
                {"_id": self.created_id, "name": "alpha", "price": Decimal128("9.99")},
                {"_id": ObjectId(), "name": "beta", "owner": {"_id": ObjectId()}},
            ],
            "count": 2,
        }

    def get_record(self, ctx, **params) -> dict:
        return {"_id": self.created_id, "name": "alpha"}


@pytest.mark.asyncio
async def test_executor_results_serialize_after_mongo_shaped_actions() -> None:
    ex = ModuleExecutor()
    handler = _MongoShapedHandler()
    ex.register("records", handler)

    listed = await ex.execute(_request("records", "list_records"))
    assert listed.success is True
    encoded = json.dumps(listed.data)
    assert str(handler.created_id) in encoded
    assert listed.data["items"][0]["_id"] == str(handler.created_id)
    assert listed.data["items"][0]["price"] == "9.99"
    assert listed.data["count"] == 2

    fetched = await ex.execute(_request("records", "get_record"))
    assert fetched.success is True
    json.dumps(fetched.data)
    assert fetched.data["_id"] == str(handler.created_id)


# ---------------------------------------------------------------------------
# Real-Mongo CRUD through the persistence wrapper + executor boundary
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for real Mongo CRUD normalization tests",
)
@pytest.mark.asyncio
async def test_real_mongo_crud_results_are_json_safe_through_executor() -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    client = AsyncIOMotorClient(uri)
    database_name = f"mozaiks_bson_safe_test_{uuid4().hex}"
    collection = client[database_name]["records"]

    class _RealRepoHandler:
        async def create_record(self, ctx, **params) -> dict:
            # Generated services set a string "id" and rely on Mongo to
            # autogenerate the ObjectId _id — the exact defect shape.
            document = {"id": str(uuid4()), "name": params.get("name", "")}
            await collection.insert_one(document)
            return {"created": document["id"]}

        async def list_records(self, ctx, **params) -> dict:
            items = await collection.find({}).to_list(length=50)
            return {"items": items, "count": len(items)}

        async def read_record(self, ctx, **params) -> dict:
            return {"item": await collection.find_one({"id": params["id"]})}

        async def update_record(self, ctx, **params) -> dict:
            await collection.update_one(
                {"id": params["id"]}, {"$set": {"name": params["name"]}}
            )
            return {"item": await collection.find_one({"id": params["id"]})}

        async def delete_record(self, ctx, **params) -> dict:
            result = await collection.delete_one({"id": params["id"]})
            return {"deleted": int(result.deleted_count)}

    try:
        ex = ModuleExecutor()
        ex.register("records", _RealRepoHandler())

        created = await ex.execute(
            _request("records", "create_record", {"name": "alpha"})
        )
        assert created.success is True
        record_id = created.data["created"]

        listed = await ex.execute(_request("records", "list_records"))
        assert listed.success is True
        json.dumps(listed.data)
        assert listed.data["count"] == 1
        assert isinstance(listed.data["items"][0]["_id"], str)

        read = await ex.execute(_request("records", "read_record", {"id": record_id}))
        assert read.success is True
        json.dumps(read.data)
        assert isinstance(read.data["item"]["_id"], str)

        updated = await ex.execute(
            _request("records", "update_record", {"id": record_id, "name": "beta"})
        )
        assert updated.success is True
        json.dumps(updated.data)
        assert updated.data["item"]["name"] == "beta"

        deleted = await ex.execute(
            _request("records", "delete_record", {"id": record_id})
        )
        assert deleted.success is True
        assert deleted.data == {"deleted": 1}
    finally:
        await client.drop_database(database_name)
        client.close()
