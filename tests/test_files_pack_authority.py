"""Real module HTTP/Mongo proof for shipped file ownership and public metadata.

Only trusted repository templates run in the sanitized child; generated/runtime
imports and platform globals cannot escape into the parent pytest process.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke
from factory_app.workflows.AppGenerator.tools.app_validation import _write_files_to_dir
from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
from tests.test_app_runtime_smoke import mongo as _mongo
from tests.test_capability_pack_proof import _assemble

mongo = _mongo


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "read_and_list", "delete_private", "delete_public",
    "owner_array", "public_array", "public_string",
])
async def test_files_authority_through_real_module(case, tmp_path, mongo):
    files, _context, _identities = await _assemble("files")
    app_root = tmp_path / "app"
    _write_files_to_dir(app_root, files)
    database = f"{smoke._SMOKE_DATABASE_PREFIX}files_authority_{uuid4().hex[:16]}"
    environment = smoke.child_environment()
    environment["USERPROFILE" if os.name == "nt" else "HOME"] = str(tmp_path)
    request = {"app_root": str(app_root), "mongo_uri": mongo.uri,
               "database_name": database, "case": case}
    try:
        completed = await asyncio.to_thread(
            subprocess.run, [sys.executable, str(Path(__file__).resolve()), "--probe"],
            input=json.dumps(request), capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=environment, cwd=tmp_path, timeout=90,
        )
    finally:
        await mongo.client.drop_database(database)
    assert completed.returncode == 0, completed.stderr[-6000:]
    assert any(event == {"event": "done", "case": case}
               for event in smoke._events(completed.stdout)), completed.stdout[-2000:]


async def _call(run, method, module_name, action, user, params):
    status, body, summary = await run.call(method, module_name, action, user, params)
    if status == 200:
        module = next(module for module in run.load.modules if module.name == module_name)
        diagnostic = validate_json_schema(body, module.action_schemas_map[action]["output"])
        assert diagnostic is None, (module_name, action, diagnostic)
    return status, body, summary


async def _upload(run, user, *, public=False):
    status, body, summary = await _call(run, "POST", "files", "upload_file", user, {
        "filename": "proof.txt", "mime_type": "text/plain", "size_bytes": 3,
        "storage_url": "https://storage.invalid/proof.txt", "is_public": public,
    })
    assert status == 200 and body["success"] is True, summary
    assert body["file"]["created_by"] == user.user_id
    return body["file"]["file_id"]


async def _get(run, user, file_id):
    status, body, summary = await _call(run, "POST", "files", "get_file", user, {"file_id": file_id})
    assert status == 200, summary
    return body


def _collection(run):
    from mozaiksai.core.runtime.persistence import MongoPersistenceContext

    persistence = MongoPersistenceContext(app_id=run.app_id, data_contract=run.load.data_contract,
                                          client=run.client, database_name=run.database_name)
    return run.database[persistence.collection_name("files", "records")]


async def _read_and_list(run):
    owner, other = run.principal("owner"), run.principal("other")
    module = next(module for module in run.load.modules if module.name == "files")
    output_schema = module.action_schemas_map["get_file"]["output"]
    for invalid in (False, 1, "private", []):
        assert validate_json_schema({"file": invalid}, output_schema) is not None
    private = await _upload(run, owner)
    public = await _upload(run, owner, public=True)
    other_file = await _upload(run, other)
    before = await run._data_snapshot()
    assert (await _get(run, owner, private))["file"]["file_id"] == private
    assert await _get(run, other, private) == await _get(run, other, "missing-file") == {"file": None}
    assert (await _get(run, other, public))["file"]["file_id"] == public
    for user, expected in ((owner, {private, public}), (other, {other_file})):
        status, body, summary = await _call(run, "POST", "files", "list_files", user, {})
        assert status == 200, summary
        assert {item["file_id"] for item in body["files"]} == expected
    assert before == await run._data_snapshot()

    # Public metadata in another app remains invisible through the real app scope.
    collection = _collection(run)
    foreign = dict(await collection.find_one({"app_id": run.app_id, "file_id": public}))
    foreign.pop("_id")
    foreign.update(app_id="foreign-app", file_id="foreign-public")
    inserted = await collection.insert_one(foreign)
    foreign_before = await collection.find_one({"_id": inserted.inserted_id})
    assert await _get(run, owner, "foreign-public") == {"file": None}
    status, body, _summary = await _call(run, "POST", "files", "delete_file", owner, {"file_id": "foreign-public"})
    assert status == 200 and body["success"] is False
    assert await collection.find_one({"_id": inserted.inserted_id}) == foreign_before


async def _delete(run, *, public):
    owner, other = run.principal("owner"), run.principal("other")
    file_id = await _upload(run, owner, public=public)
    before = await run._data_snapshot()
    status, body, summary = await _call(run, "POST", "files", "delete_file", other, {"file_id": file_id})
    assert status == 200 and body == {"success": False, "error": "file not found"}, summary
    assert await run._data_snapshot() == before
    assert (await _get(run, owner, file_id))["file"]["file_id"] == file_id

    status, body, summary = await _call(run, "POST", "files", "delete_file", owner, {"file_id": file_id})
    assert status == 200 and body == {"success": True}, summary
    after = await run._data_snapshot()
    stored = next(row for rows in after.values() for row in rows if row.get("file_id") == file_id)
    assert stored["deleted"] is True and stored["deleted_by"] == owner.user_id
    assert await _get(run, owner, file_id) == await _get(run, other, file_id) == {"file": None}
    status, body, _summary = await _call(run, "POST", "files", "delete_file", owner, {"file_id": file_id})
    assert status == 200 and body["success"] is False
    assert await run._data_snapshot() == after
    denied = next(row.to_dict() for row in run.outcomes
                  if row.to_dict()["check"] == "role.files.delete_file.non_holder_denied")
    assert denied["status"] == "passed" and denied["details"]["records_unchanged"] is True


async def _malformed_authority(run, case):
    owner, other = run.principal("owner"), run.principal("other")
    file_id = await _upload(run, owner)
    collection = _collection(run)
    if case == "owner_array":
        mutation, caller = {"created_by": [owner.user_id]}, owner
    else:
        mutation = {"is_public": [True] if case == "public_array" else "true"}
        caller = other
    await collection.update_one({"app_id": run.app_id, "file_id": file_id}, {"$set": mutation})
    before = await run._data_snapshot()
    assert await _get(run, caller, file_id) == {"file": None}
    status, body, summary = await _call(run, "POST", "files", "list_files", caller, {})
    assert status == 200 and body["files"] == [], summary
    status, body, summary = await _call(run, "POST", "files", "delete_file", caller, {"file_id": file_id})
    assert status == 200 and body == {"success": False, "error": "file not found"}, summary
    assert await run._data_snapshot() == before


async def _probe(request, initial_environment):
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(request["mongo_uri"], serverSelectionTimeoutMS=2000)
    run = smoke._SmokeRun(Path(request["app_root"]), client, request["database_name"],
                         lambda *_args, **_kwargs: None)
    try:
        assert await smoke._boot(run, initial_environment)
        if request["case"] == "read_and_list":
            await _read_and_list(run)
        elif request["case"] in {"delete_private", "delete_public"}:
            await _delete(run, public=request["case"] == "delete_public")
        else:
            await _malformed_authority(run, request["case"])
    finally:
        if run.http is not None:
            await run.http.aclose()
        client.close()


if __name__ == "__main__":
    assert sys.argv[1:] == ["--probe"]
    initial_environment = set(os.environ)
    request = json.loads(sys.stdin.read())
    channel = sys.stdout
    sys.stdout = sys.stderr
    asyncio.run(_probe(request, initial_environment))
    channel.write(smoke._EVENT_PREFIX + json.dumps({"event": "done", "case": request["case"]}) + "\n")
