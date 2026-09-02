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
# Closed container domain — exact dict/list/tuple only, decided before any
# iteration or .items() call can execute hostile code
# ---------------------------------------------------------------------------


def test_ordinary_set_and_frozenset_are_rejected() -> None:
    with pytest.raises(ModuleResultNormalizationError, match="no deterministic JSON form"):
        json_safe_bson({"tags": {"a", "b"}})
    with pytest.raises(ModuleResultNormalizationError, match="no deterministic JSON form"):
        json_safe_bson({"tags": frozenset({"a", "b"})})


def test_container_subclasses_are_rejected_without_iteration() -> None:
    executed: list[str] = []

    class _HostileDict(dict):
        def items(self):
            executed.append("dict.items")
            return super().items()

    class _HostileList(list):
        def __iter__(self):
            executed.append("list.iter")
            return super().__iter__()

    class _HostileTuple(tuple):
        def __iter__(self):
            executed.append("tuple.iter")
            return super().__iter__()

    class _HostileSet(set):
        def __iter__(self):
            executed.append("set.iter")
            raise RuntimeError("hostile iteration payload")

    for hostile in (
        _HostileDict({"k": "v"}),
        _HostileList(["v"]),
        _HostileTuple(("v",)),
        _HostileSet({"v"}),
    ):
        with pytest.raises(ModuleResultNormalizationError):
            json_safe_bson({"payload": hostile})
    assert executed == []


def test_custom_mapping_is_rejected_without_items_call() -> None:
    from collections.abc import Iterator, Mapping

    executed: list[str] = []

    class _HostileMapping(Mapping):
        def __getitem__(self, key):  # pragma: no cover - must never run
            executed.append("getitem")
            return "x"

        def __iter__(self) -> Iterator[str]:  # pragma: no cover - must never run
            executed.append("iter")
            raise RuntimeError("hostile mapping payload")

        def __len__(self) -> int:
            return 1

    with pytest.raises(ModuleResultNormalizationError, match="exact"):
        json_safe_bson({"payload": _HostileMapping()})
    assert executed == []


def test_generators_and_arbitrary_iterables_are_rejected() -> None:
    def _gen():  # pragma: no cover - must never be iterated
        yield "leak"

    with pytest.raises(ModuleResultNormalizationError, match="unsupported value type"):
        json_safe_bson({"stream": _gen()})

    class _Iterable:
        def __iter__(self):  # pragma: no cover - must never run
            raise RuntimeError("hostile")

    with pytest.raises(ModuleResultNormalizationError, match="unsupported value type"):
        json_safe_bson({"stream": _Iterable()})


def test_scalar_subclasses_are_outside_the_exact_domain() -> None:
    class _EvilInt(int):
        pass

    class _EvilStr(str):
        pass

    class _EvilFloat(float):
        pass

    for scalar in (_EvilInt(1), _EvilStr("s"), _EvilFloat(1.5)):
        with pytest.raises(ModuleResultNormalizationError):
            json_safe_bson({"v": scalar})


def test_no_set_serialization_path_across_hash_seeds() -> None:
    """Sets fail identically under different PYTHONHASHSEED values.

    If any code path serialized a set, its ordering would vary with the hash
    seed; the contract removes the path entirely, so every seed produces the
    same typed rejection and the seed can never influence wire bytes.
    """
    import subprocess
    import sys

    snippet = (
        "from mozaiksai.core.runtime.composition.bson_safe import "
        "json_safe_bson, ModuleResultNormalizationError\n"
        "try:\n"
        "    json_safe_bson({'tags': {'a', 'b', 'c', 'd', 'e'}})\n"
        "except ModuleResultNormalizationError as exc:\n"
        "    print('REJECTED:' + str(exc))\n"
        "else:\n"
        "    print('ACCEPTED')\n"
    )
    outputs = set()
    for seed in ("0", "1", "424242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", snippet],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        outputs.add(proc.stdout.strip())
    assert len(outputs) == 1
    assert next(iter(outputs)).startswith("REJECTED:")


# ---------------------------------------------------------------------------
# Conversion failures are typed, never raw
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_nonfinite_decimals_fail_closed(bad: str) -> None:
    with pytest.raises(ModuleResultNormalizationError, match="non-finite Decimal"):
        json_safe_bson({"amount": Decimal(bad)})


def test_pydantic_model_with_unserializable_field_fails_typed() -> None:
    from typing import Any as _Any

    from pydantic import ConfigDict

    class _Holder(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        payload: _Any

    with pytest.raises(ModuleResultNormalizationError, match="could not be serialized"):
        json_safe_bson({"doc": _Holder(payload=object())})


def test_pydantic_model_with_throwing_serializer_fails_typed() -> None:
    from pydantic import field_serializer

    class _Throwing(BaseModel):
        name: str

        @field_serializer("name")
        def _boom(self, value: str) -> str:
            raise RuntimeError("serializer exploded with SECRET-DETAILS")

    with pytest.raises(ModuleResultNormalizationError) as excinfo:
        json_safe_bson({"doc": _Throwing(name="x")})
    assert "SECRET-DETAILS" not in str(excinfo.value)


def test_malformed_utf8_bytes_fail_typed() -> None:
    with pytest.raises(ModuleResultNormalizationError, match="not valid UTF-8"):
        json_safe_bson({"blob": b"\xff\xfe\xfa"})


def test_enum_with_unsupported_value_fails_typed() -> None:
    class _Weird(Enum):
        MEMBER = object()

    with pytest.raises(ModuleResultNormalizationError, match="unsupported value type"):
        json_safe_bson({"kind": _Weird.MEMBER})


def test_enum_with_set_value_fails_typed() -> None:
    class _SetValued(Enum):
        MEMBER = frozenset({"a"})

    with pytest.raises(ModuleResultNormalizationError):
        json_safe_bson({"kind": _SetValued.MEMBER})


def test_unexpected_conversion_exception_is_wrapped_without_contents() -> None:
    class _HostileDatetime(datetime):
        def isoformat(self, *args, **kwargs):  # pragma: no cover - bypassed
            raise RuntimeError("SECRET-CONTENT")

    # Unbound datetime.isoformat is used, so the override never runs and the
    # value converts through the base implementation.
    normalized = json_safe_bson(
        {"when": _HostileDatetime(2026, 9, 2, tzinfo=UTC)}
    )
    assert normalized["when"].startswith("2026-09-02")


def test_top_level_wrapper_never_leaks_and_only_raises_typed_error() -> None:
    class _RaisingEnum(Enum):
        MEMBER = "ok"

        @property
        def value(self):  # noqa: PLR0206 - deliberate hostile property
            raise RuntimeError("SECRET-ENUM-CONTENT")

    with pytest.raises(ModuleResultNormalizationError) as excinfo:
        json_safe_bson({"kind": _RaisingEnum.MEMBER})
    message = str(excinfo.value)
    assert "SECRET-ENUM-CONTENT" not in message
    assert "RuntimeError" in message


# ---------------------------------------------------------------------------
# Exact FastAPI wire-byte parity
# ---------------------------------------------------------------------------


def _wire_bytes(value) -> bytes:
    """The executor's exact wire render (must match JSONResponse.render)."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "é"},
        {"emoji": "🚀", "mixed": ["é", {"k": "ü"}], "n": 3, "f": 1.5, "none": None},
        {"nested": {"a": [1, 2, 3], "b": "plain ascii"}},
        [],
        {},
        ["é", "combining é"],
    ],
    ids=["latin-accent", "emoji-mixed", "nested", "empty-list", "empty-dict", "unicode-list"],
)
def test_measured_bytes_equal_actual_starlette_response_bytes(payload) -> None:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/echo")
    def _echo():
        return JSONResponse(content=payload)

    with TestClient(app) as client:
        body = client.get("/echo").content
    assert _wire_bytes(payload) == body
    assert len(_wire_bytes(payload)) == len(body)


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


class _SizedHandler:
    """Returns a payload whose exact wire bytes are controllable."""

    def __init__(self, payload) -> None:
        self._payload = payload

    def sized(self, ctx, **params):
        return self._payload


def _exact_wire_size(value) -> int:
    return len(
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")
        ).encode("utf-8")
    )


@pytest.mark.asyncio
async def test_result_at_exact_limit_passes_and_one_byte_over_fails(monkeypatch) -> None:
    # Multi-byte characters make encoded-string length diverge from byte
    # length; the gate must measure the actual UTF-8 wire bytes.
    payload = {"text": "é" * 10}
    limit = _exact_wire_size(payload)

    monkeypatch.setenv("MODULE_RESPONSE_MAX_BYTES", str(limit))
    ex = ModuleExecutor()
    ex.register("sized", _SizedHandler(payload))
    at_limit = await ex.execute(_request("sized", "sized"))
    assert at_limit.success is True

    monkeypatch.setenv("MODULE_RESPONSE_MAX_BYTES", str(limit - 1))
    ex2 = ModuleExecutor()
    ex2.register("sized", _SizedHandler(payload))
    over = await ex2.execute(_request("sized", "sized"))
    assert over.success is False
    assert over.error_code == "RESPONSE_TOO_LARGE"


@pytest.mark.asyncio
async def test_unsupported_result_is_not_json_safe_never_a_size_outcome(monkeypatch) -> None:
    monkeypatch.setenv("MODULE_RESPONSE_MAX_BYTES", "1")
    ex = ModuleExecutor()
    ex.register("sized", _SizedHandler({"handle": object()}))
    result = await ex.execute(_request("sized", "sized"))
    assert result.success is False
    assert result.error_code == "MODULE_RESULT_NOT_JSON_SAFE"


def test_bson_int64_converts_to_exact_int() -> None:
    from bson.int64 import Int64

    normalized = json_safe_bson({"count": Int64(9_007_199_254_740_993)})
    assert normalized["count"] == 9_007_199_254_740_993
    assert type(normalized["count"]) is int


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
