from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.results import InsertOneResult

from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal, PersistenceScopeError
from mozaiksai.core.runtime.persistence.mongo import (
    MongoPersistenceCollection,
    MongoPersistenceContext,
)
from mozaiksai.core.runtime.persistence.ownership import CollectionOwnership
from mozaiksai.core.runtime.persistence.request_scope import bind_persistence_principal
from mozaiksai.core.runtime.persistence.workflow_result import (
    ResultRecordSubject,
    capture_result_subject,
    commit_workflow_result,
)
from tests.test_runtime_persistence_ownership import contract

_OWNER = PersistencePrincipal("owner", tenant_id="tenant", workspace_id="workspace")
_SOURCE = {"module_id": "tasks", "action_id": "create_task", "result_record_key": "task_id"}
_SUBJECT = {"app_id": "app", "module_id": "tasks", "action_id": "create_task", "record_key": "task_id",
            "collection": "tasks", "record_id": "task-1"}


def fake_collection(principal=_OWNER, *, acknowledged=True):
    raw = SimpleNamespace(
        name="tasks_physical", database=SimpleNamespace(name="test"),
        insert_one=AsyncMock(return_value=InsertOneResult(ObjectId(), acknowledged)),
        update_one=AsyncMock(),
    )
    return MongoPersistenceCollection(
        app_id="app", collection=raw, ownership=CollectionOwnership("per_user", "author"),
        tenant_id="tenant", workspace_id="workspace", principal=principal,
        module_id="tasks", logical_collection="tasks",
    ), raw


@pytest.mark.asyncio
async def test_subject_only_comes_from_one_matching_acknowledged_insert_in_this_dispatch():
    rows, raw = fake_collection()
    with bind_persistence_principal("app", _OWNER, **_SOURCE):
        with pytest.raises(PersistenceScopeError):
            capture_result_subject(**_SUBJECT)
        await rows.insert_one({"task_id": "task-1", "body": "private text"})
        for key in ("app_id", "module_id", "action_id", "collection", "record_id", "record_key"):
            with pytest.raises(PersistenceScopeError):
                capture_result_subject(**{**_SUBJECT, key: "other"})
        subject = capture_result_subject(**_SUBJECT)
        assert ResultRecordSubject.model_validate_json(subject.model_dump_json()) == subject
        assert "private text" not in subject.model_dump_json()
        assert subject.native_id == str(raw.insert_one.return_value.inserted_id)
        assert subject.user_id == _OWNER.user_id
        with bind_persistence_principal("app", _OWNER, **_SOURCE), pytest.raises(PersistenceScopeError):
            capture_result_subject(**_SUBJECT)
        with pytest.raises(PersistenceScopeError):
            await rows.insert_one({"task_id": "task-2"})
        assert raw.insert_one.await_count == 1
    with pytest.raises(PersistenceScopeError):
        capture_result_subject(**_SUBJECT)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["two_inserts", "unacknowledged", "development", "unbound", "wrong_scope", "update"])
async def test_ambiguous_or_unbound_operations_mint_no_subject(case):
    principal = PersistencePrincipal("owner", source="development") if case == "development" else _OWNER
    rows, raw = fake_collection(principal, acknowledged=case != "unacknowledged")
    with bind_persistence_principal(
        "app", _OWNER if case == "wrong_scope" else principal, **({} if case == "unbound" else _SOURCE),
    ):
        if case == "wrong_scope":
            rows._principal = PersistencePrincipal("other")
        if case == "update":
            await rows.update_one({"task_id": "task-1"}, {"$set": {"title": "updated"}})
        else:
            await rows.insert_one({"task_id": "task-1"})
        if case == "two_inserts":
            await rows.insert_one({"task_id": "task-2"})
        with pytest.raises(PersistenceScopeError):
            capture_result_subject(**_SUBJECT)
    if case in {"development", "unbound", "wrong_scope"}:
        assert "_mz_workflow_result" not in raw.insert_one.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["insert", "set", "unset", "rename", "upsert"])
async def test_owned_repository_cannot_forge_or_clear_result_metadata(kind):
    rows, raw = fake_collection()
    marker = "_mz_workflow_result"
    with pytest.raises(PersistenceScopeError):
        if kind == "insert":
            await rows.insert_one({marker: {"origin": "forged"}})
        elif kind == "upsert":
            await rows.update_one({"$and": [{marker + ".origin": "forged"}]}, {"$set": {"title": "x"}}, upsert=True)
        elif kind == "rename":
            await rows.update_one({}, {"$rename": {"body": marker + ".origin"}})
        else:
            await rows.update_one({}, {"$" + kind: {marker + ".receipt": "x"}})
    raw.insert_one.assert_not_awaited()
    raw.update_one.assert_not_awaited()


@pytest.mark.skipif(os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1", reason="requires local Mongo")
@pytest.mark.asyncio
@pytest.mark.parametrize("supplied_id", [None, "a" * 24])
async def test_result_is_record_bound_and_atomic_across_concurrent_retry_and_lost_ack(supplied_id):
    database = f"mz_result_test_{uuid4().hex}"
    client = AsyncIOMotorClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=3000)

    def storage(owner):
        return MongoPersistenceContext(
            app_id="app", tenant_id=owner.tenant_id, workspace_id=owner.workspace_id,
            client=client, database_name=database, data_contract=contract(), principal=owner,
        )

    source = storage(_OWNER)
    rows = source.collection("tasks", "Task")
    document = {"task_id": "task-1", "body": "keep", "tags": ["first", "second"]}
    if supplied_id:
        document["_id"] = supplied_id
    try:
        await rows.insert_one({"task_id": "task-1", "decoy": True})
        with bind_persistence_principal("app", _OWNER, **_SOURCE):
            inserted = await rows.insert_one(document)
            subject = capture_result_subject(**_SUBJECT)
        args = dict(persistence=source, subject=subject, module_id="tasks", action_id="save_summary",
                    collection="tasks", invocation_id="invocation-a", allowed_fields=("summary", "tag"),
                    values={"summary": "review", "tag": "done"})
        attacker = PersistencePrincipal("other", tenant_id="tenant", workspace_id="workspace")
        for overrides in [
            {"persistence": storage(attacker)}, {"subject": subject.model_copy(update={"record_id": "other"})},
            {"subject": subject.model_copy(update={"native_id": str(ObjectId())})},
            {"subject": subject.model_copy(update={"row_nonce": "0" * 32})},
            {"module_id": "other"}, {"collection": "other"},
            *[{"allowed_fields": (field,), "values": {field: "other"}}
              for field in ("created_by", "user_id", "task_id", "_id", "x.y", "$set", "_mz_workflow_result")],
        ]:
            with pytest.raises(PersistenceScopeError):
                await commit_workflow_result(**{**args, **overrides})
        outcomes = await asyncio.gather(*(commit_workflow_result(**args) for _ in range(10)))
        assert outcomes.count("applied") == 1 and outcomes.count("already_applied") == 9
        assert await commit_workflow_result(**args) == "already_applied"
        for conflict in ({"invocation_id": "another"}, {"action_id": "other"}, {"values": {"summary": "changed", "tag": "done"}}):
            with pytest.raises(PersistenceScopeError):
                await commit_workflow_result(**{**args, **conflict})
        persisted = client[database][subject.physical_collection]
        assert await persisted.count_documents({"summary": {"$exists": True}}) == 1
        row = await persisted.find_one({"_id": inserted.inserted_id})
        assert row["body"] == "keep" and row["summary"] == "review" and row["tag"] == "done"
        assert row["_mz_workflow_result"]["receipt"]["invocation_id"] == "invocation-a"
        for projection in (None, {"_id": 0}, {"_mz_workflow_result.origin": 1, "summary": 1}):
            found = await rows.find_one({"_id": inserted.inserted_id}, projection)
            assert "_mz_workflow_result" not in found and found["summary"] == "review"
            listed = await rows.find_many({"_id": inserted.inserted_id}, projection=projection)
            assert listed == [found]
        projection = {"_id": 0, "copy": "$$ROOT", "alias": "$_mz_workflow_result", "text": "$summary"}
        found = await rows.find_one({"_id": inserted.inserted_id}, projection)
        assert "alias" not in found and "_mz_workflow_result" not in found["copy"] and found["text"] == "review"
        assert await rows.find_many({"_id": inserted.inserted_id}, projection=projection) == [found]
        assert await rows.aggregate([{"$match": {"_id": inserted.inserted_id}}, {"$project": projection}]) == [found]
        for selector, expected in (({"$slice": 1}, ["first"]), ({"$elemMatch": {"$eq": "second"}}, ["second"])):
            mixed = {"_id": 0, "tags": selector, "name": "$summary", "copy": "$$ROOT",
                     "internal": {"$getField": "_mz_workflow_result"}, "explicit": "$$ROOT._mz_workflow_result",
                     "literal": {"$literal": "$$ROOT"}}
            found = await rows.find_one({"_id": inserted.inserted_id}, mixed)
            assert found["tags"] == expected and found["name"] == "review" and found["literal"] == "$$ROOT"
            assert "internal" not in found and "explicit" not in found and "_mz_workflow_result" not in found["copy"]
            assert await rows.find_many({"_id": inserted.inserted_id}, projection=mixed) == [found]
        # $slice can evaluate an expression as well as select a field's elements.
        keys = {"$map": {"input": {"$objectToArray": "$$ROOT"}, "in": "$$this.k"}}
        found = await rows.find_one({"_id": inserted.inserted_id}, {"_id": 0, "keys": {"$slice": [keys, 50]}})
        assert "summary" in found["keys"] and "_mz_workflow_result" not in found["keys"]
        # A deleted row's grant cannot apply to a replacement with the very same native and domain IDs.
        await rows.delete_one({"_id": inserted.inserted_id})
        await rows.insert_one({**document, "_id": inserted.inserted_id})
        with pytest.raises(PersistenceScopeError):
            await commit_workflow_result(**args)
        assert await persisted.count_documents({"summary": {"$exists": True}}) == 0
    finally:
        await client.drop_database(database)
        client.close()
