"""Closed JSON transport contract at the ModuleExecutor success boundary.

No ModuleExecutor success may later fail at FastAPI serialization: results are
normalized into a closed value domain, non-string mapping keys and unsupported
values fail closed with MODULE_RESULT_NOT_JSON_SAFE, and the size gate
measures the exact strict-encoded UTF-8 bytes.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128
from pydantic import BaseModel

from mozaiksai.core.runtime.composition.bson_safe import (
    MAX_RESULT_DEPTH,
    ModuleResultNormalizationError,
    json_safe_bson,
)
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
# Value domain
# ---------------------------------------------------------------------------


def test_object_id_values_become_stable_hex_strings() -> None:
    oid = ObjectId()
    nested = ObjectId()
    document = {"_id": oid, "items": [{"ref": nested}, (oid, "pair")]}
    normalized = json_safe_bson(document)
    assert normalized["_id"] == str(oid)
    assert normalized["items"][0]["ref"] == str(nested)
    assert normalized["items"][1] == [str(oid), "pair"]
    json.dumps(normalized, allow_nan=False)


def test_decimal128_is_lossless() -> None:
    value = Decimal128("1234567890.123456789012345678901234")
    normalized = json_safe_bson({"amount": value})
    assert Decimal(normalized["amount"]) == value.to_decimal()


def test_fastapi_equivalent_wire_semantics_preserved() -> None:
    class _Kind(Enum):
        ACTIVE = "active"

    class _Doc(BaseModel):
        name: str
        when: datetime

    stamp = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    normalized = json_safe_bson(
        {
            "when": stamp,
            "day": date(2026, 9, 2),
            "uid": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "price": Decimal("9.90"),
            "count": Decimal("3"),
            "kind": _Kind.ACTIVE,
            "model": _Doc(name="a", when=stamp),
            "blob": b"text-bytes",
            "tags": {"a"},
        }
    )
    assert normalized["when"] == stamp.isoformat()
    assert normalized["day"] == "2026-09-02"
    assert normalized["uid"] == "12345678-1234-5678-1234-567812345678"
    assert normalized["price"] == float(Decimal("9.90"))
    assert normalized["count"] == 3
    assert normalized["kind"] == "active"
    assert normalized["model"]["name"] == "a"
    assert normalized["blob"] == "text-bytes"
    assert normalized["tags"] == ["a"]
    json.dumps(normalized, allow_nan=False)


def test_input_documents_are_not_mutated() -> None:
    oid = ObjectId()
    document = {"_id": oid, "nested": {"ref": oid}, "items": [oid]}
    json_safe_bson(document)
    assert document["_id"] is oid
    assert document["nested"]["ref"] is oid
    assert document["items"][0] is oid


# ---------------------------------------------------------------------------
# Closed key domain — collisions are impossible because keys must be strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        ObjectId(),
        1,
        True,
        None,
        ("a", "b"),
        b"k",
        object(),
    ],
    ids=["objectid", "int", "bool", "none", "tuple", "bytes", "custom"],
)
def test_non_string_mapping_keys_fail_closed(key) -> None:
    with pytest.raises(ModuleResultNormalizationError, match="keys must be strings"):
        json_safe_bson({key: "value"})


def test_objectid_key_never_collides_with_equivalent_string_key() -> None:
    oid = ObjectId()
    with pytest.raises(ModuleResultNormalizationError):
        json_safe_bson({oid: "a", str(oid): "b"})


def test_integer_key_never_collides_with_string_key() -> None:
    with pytest.raises(ModuleResultNormalizationError):
        json_safe_bson({1: "a", "1": "b"})


def test_none_key_never_collides_with_null_string_key() -> None:
    with pytest.raises(ModuleResultNormalizationError):
        json_safe_bson({None: "a", "null": "b"})


# ---------------------------------------------------------------------------
# Unsupported values, non-finite floats, cycles, and depth
# ---------------------------------------------------------------------------


def test_unsupported_value_fails_closed_without_repr_leak() -> None:
    class _Secret:
        def __repr__(self) -> str:  # pragma: no cover - must never be called
            return "SECRET-CONTENT"

    with pytest.raises(ModuleResultNormalizationError) as excinfo:
        json_safe_bson({"item": _Secret()})
    assert "SECRET-CONTENT" not in str(excinfo.value)
    assert "_Secret" in str(excinfo.value)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_floats_fail_closed(bad) -> None:
    with pytest.raises(ModuleResultNormalizationError, match="non-finite float"):
        json_safe_bson({"x": bad})


def test_cyclic_list_fails_closed() -> None:
    items: list = []
    items.append(items)
    with pytest.raises(ModuleResultNormalizationError, match="cyclic"):
        json_safe_bson(items)


def test_cyclic_mapping_fails_closed() -> None:
    document: dict = {}
    document["self"] = document
    with pytest.raises(ModuleResultNormalizationError, match="cyclic"):
        json_safe_bson(document)


def test_excessive_nesting_fails_closed() -> None:
    value: dict = {"leaf": True}
    for _ in range(MAX_RESULT_DEPTH + 5):
        value = {"nested": value}
    with pytest.raises(ModuleResultNormalizationError, match="nesting"):
        json_safe_bson(value)


def test_shared_noncyclic_child_is_encoded_twice_deterministically() -> None:
    child = {"ref": ObjectId()}
    normalized = json_safe_bson({"a": child, "b": child})
    assert normalized["a"] == normalized["b"]
    assert normalized["a"] is not normalized["b"]


# ---------------------------------------------------------------------------
# Executor boundary behavior
# ---------------------------------------------------------------------------


class _MongoShapedHandler:
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

    def bad_keys(self, ctx, **params) -> dict:
        return {1: "collides"}

    def bad_value(self, ctx, **params) -> dict:
        return {"handle": object()}

    def cyclic(self, ctx, **params) -> dict:
        document: dict = {}
        document["self"] = document
        return document

    def huge(self, ctx, **params) -> dict:
        return {"blob": "x" * 20_000_000}


@pytest.mark.asyncio
async def test_executor_results_serialize_after_mongo_shaped_actions() -> None:
    ex = ModuleExecutor()
    handler = _MongoShapedHandler()
    ex.register("records", handler)

    listed = await ex.execute(_request("records", "list_records"))
    assert listed.success is True
    json.dumps(listed.data, allow_nan=False)
    assert listed.data["items"][0]["_id"] == str(handler.created_id)
    assert listed.data["items"][0]["price"] == "9.99"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["bad_keys", "bad_value", "cyclic"])
async def test_invalid_transport_results_return_typed_error(action: str) -> None:
    ex = ModuleExecutor()
    ex.register("records", _MongoShapedHandler())
    result = await ex.execute(_request("records", action))
    assert result.success is False
    assert result.error_code == "MODULE_RESULT_NOT_JSON_SAFE"
    # The action completed; this is a transport-contract failure, and no raw
    # object contents leak into the client-facing message.
    assert "object" not in (result.error or "").lower() or "JSON-safe" in result.error


@pytest.mark.asyncio
async def test_oversized_normalized_response_is_rejected_by_exact_bytes() -> None:
    ex = ModuleExecutor()
    ex.register("records", _MongoShapedHandler())
    result = await ex.execute(_request("records", "huge"))
    assert result.success is False
    assert result.error_code == "RESPONSE_TOO_LARGE"


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
    database_name = f"mozaiks_bson_safe_test_{uuid.uuid4().hex}"
    collection = client[database_name]["records"]

    class _RealRepoHandler:
        async def create_record(self, ctx, **params) -> dict:
            document = {"id": str(uuid.uuid4()), "name": params.get("name", "")}
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
        json.dumps(listed.data, allow_nan=False)
        assert isinstance(listed.data["items"][0]["_id"], str)

        read = await ex.execute(_request("records", "read_record", {"id": record_id}))
        assert read.success is True
        assert isinstance(read.data["item"]["_id"], str)

        updated = await ex.execute(
            _request("records", "update_record", {"id": record_id, "name": "beta"})
        )
        assert updated.success is True
        assert updated.data["item"]["name"] == "beta"

        deleted = await ex.execute(
            _request("records", "delete_record", {"id": record_id})
        )
        assert deleted.success is True
        assert deleted.data == {"deleted": 1}
    finally:
        await client.drop_database(database_name)
        client.close()
