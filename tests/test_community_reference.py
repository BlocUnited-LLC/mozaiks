"""Common Ground contracts and dispatch; Mongo I/O is mocked at the driver only.

Live HTTP/OIDC/browser and durable Mongo acceptance are a separate evidence gate.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import yaml

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_app_contracts
from mozaiksai.core.account import account_data_registry
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence import collection_name_for
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from mozaiksai.core.runtime.persistence.intent_loader import (
    validate_complete_data_contract_ownership,
)
from mozaiksai.core.runtime.persistence.migrations import load_data_migrations
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceContext
from mozaiksai.core.validation import scan_functional_generated_app
from tests.module_authority_test_helpers import enforce_authority

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "examples/canonical-apps/community"
APP = REFERENCE / "app"
APP_ID = "common-ground"
SCOPES = ("user_posts.read", "user_posts.create", "user_posts.react")


def _refresh_module():
    spec = importlib.util.spec_from_file_location(
        "community_refresh", REFERENCE / "scripts/refresh_social_reference.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_social_derivative_has_no_drift():
    refresh = _refresh_module()
    assert refresh.refresh_reference(check=True) == []
    assert not (APP / ".mozaiks/pack_provenance.json").exists()
    assert {path.name for path in (APP / "modules/user_posts/contracts").iterdir()} == {"events.yaml"}


def test_reference_manifest_only_narrows_canonical_public_visibility_and_description():
    source = ROOT / "factory_app/build_context/social/templates/modules/user_posts/module.yaml"
    expected = yaml.safe_load(source.read_text(encoding="utf-8"))
    for action in expected["actions"]:
        if action["id"] in {"create_post", "list_posts"}:
            action["input_schema"]["properties"]["visibility"]["enum"] = ["public"]
        if action["id"] == "create_post":
            action["description"] = "Create a public post readable by other members."
            action["input_schema"]["properties"]["visibility"]["description"] = (
                "Defaults to public. This reference supports public posts only."
            )
    actual = yaml.safe_load((APP / "modules/user_posts/module.yaml").read_text(encoding="utf-8"))
    assert actual == expected


def test_refresh_check_detects_drift_without_mutation_or_deletion(tmp_path):
    refresh = _refresh_module()
    contract = tmp_path / "app/data/contract.json"
    contract.parent.mkdir(parents=True)
    contract.write_bytes((APP / "data/contract.json").read_bytes())
    refresh.refresh_reference(check=False, reference_root=tmp_path)
    handler = tmp_path / "app/modules/user_posts/backend/handler.py"
    handler.write_text("# drift\n", encoding="utf-8")
    unrelated = tmp_path / "app/ui/custom.js"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("// owned elsewhere\n", encoding="utf-8")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    assert refresh.refresh_reference(check=True, reference_root=tmp_path) == [
        "modules/user_posts/backend/handler.py",
    ]
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_reference_contracts_and_migration_are_closed_and_consistent():
    files = {
        path.relative_to(APP).as_posix(): path.read_text(encoding="utf-8")
        for path in APP.rglob("*") if path.is_file() and "__pycache__" not in path.parts
    }
    assert scan_app_contracts(files) == []
    assert scan_functional_generated_app(files) == []
    contract = json.loads(files["data/contract.json"])
    validate_complete_data_contract_ownership(contract)
    assert contract["app_id"] == APP_ID
    collections = contract["surfaces"][0]["collections"]
    assert {item["name"] for item in collections} == {"posts", "comments", "reactions"}
    migrations = load_data_migrations(APP)
    assert len(migrations) == 1
    expected_operations = []
    for collection in collections:
        assert collection["tenancy"] == "app_wide" and collection["owner_field"] is None
        fields = {field["name"] for field in collection["fields"]}
        target = {"module_id": "user_posts", "entity_name": collection["name"]}
        expected_operations.append({"type": "ensure_collection", **target})
        for index in collection["indexes"]:
            assert index["keys"][0] == ["app_id", 1]
            assert {key for key, _order in index["keys"]} <= fields
            expected_operations.append({"type": "ensure_index", **target, "index": index})
    assert migrations[0]["operations"] == expected_operations
    reactions = next(item for item in collections if item["name"] == "reactions")
    assert any(index.get("unique") and index["keys"] == [
        ["app_id", 1], ["post_id", 1], ["user_id", 1],
    ] for index in reactions["indexes"])
    auth = yaml.safe_load(files["config/auth.yaml"])
    assert auth["frontend"]["default_scopes"] == ["openid", "profile", "email", *SCOPES]
    assert auth["strategy"] == "oidc" and auth["auth_required"] is True


@pytest_asyncio.fixture
async def dispatch(monkeypatch):
    monkeypatch.setattr(account_data_registry, "_handlers", {})
    loaded = await AppLoader.load(str(APP))
    assert loaded.failed_module_names == []
    assert {module.name for module in loaded.modules} == {"user_posts"}
    collections = {}
    for name in ("posts", "comments", "reactions"):
        collection = MagicMock(name=f"raw_mongo_{name}")
        collection.insert_one = AsyncMock(return_value=SimpleNamespace(inserted_id="document"))
        collection.find_one = AsyncMock(return_value=None)
        collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
        collection.delete_one = AsyncMock(return_value=SimpleNamespace(deleted_count=1))
        collection.delete_many = AsyncMock(return_value=SimpleNamespace(deleted_count=1))
        collection.count_documents = AsyncMock(return_value=0)
        cursor = MagicMock(name=f"raw_mongo_cursor_{name}")
        cursor.sort.return_value = cursor
        cursor.limit.return_value = cursor
        cursor.to_list = AsyncMock(return_value=[])
        collection.find.return_value = cursor
        collection.aggregate.return_value = cursor
        collections[name] = collection
    database = {
        collection_name_for(app_id=APP_ID, module_id="user_posts", entity_name=name): collection
        for name, collection in collections.items()
    }
    emitter = AsyncMock()
    executor = ModuleExecutor(
        data_contract=loaded.data_contract,
        persistence_client={"community_test": database},
        persistence_database="community_test",
        platform_hooks=PlatformHookRegistry(),
        audit_logger=SimpleNamespace(log_module_action=AsyncMock()),
        event_emitter=emitter,
    )
    for module in loaded.modules:
        executor.register_loaded_module(module)
    persistence = MongoPersistenceContext(
        app_id=APP_ID, user_id="alice", principal=PersistencePrincipal(user_id="alice"),
        data_contract=loaded.data_contract, client={"community_test": database}, database_name="community_test",
    )
    return SimpleNamespace(
        executor=executor, collections=collections, emitter=emitter,
        persistence=persistence, database=database,
    )


def _request(action, params, *, user="alice", permissions=SCOPES):
    return ModuleRequest(
        module="user_posts", action=action, params=params, app_id=APP_ID,
        user_id=user, authority=enforce_authority(*permissions, actor_id=user),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["private", "friends", "all", None, {"$ne": "public"}])
async def test_non_public_visibility_is_rejected_before_storage(dispatch, visibility):
    for action, params in (
        ("create_post", {"body": "Hello", "visibility": visibility}),
        ("list_posts", {"visibility": visibility}),
    ):
        result = await dispatch.executor.execute(_request(action, params))
        assert not result.success and result.error_code == "INVALID_PARAMS"
    assert dispatch.collections["posts"].mock_calls == []
    dispatch.emitter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"body": "Hello", "author_id": "bob"},
    {"body": "Hello", "app_id": "another-app"},
])
async def test_identity_fields_cannot_be_supplied_as_action_inputs(dispatch, params):
    result = await dispatch.executor.execute(_request("create_post", params))
    assert not result.success and result.error_code == "INVALID_PARAMS"
    dispatch.collections["posts"].insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_missing_create_scope_is_denied_before_storage(dispatch):
    result = await dispatch.executor.execute(_request(
        "create_post", {"body": "Hello"}, permissions=("user_posts.read",),
    ))
    assert not result.success and result.error_code == "PERMISSION_DENIED"
    dispatch.collections["posts"].insert_one.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("user", ["alice", "bob"])
@pytest.mark.parametrize("visibility", [{}, {"visibility": "public"}])
async def test_public_post_uses_authenticated_author_and_app_scope(dispatch, user, visibility):
    result = await dispatch.executor.execute(_request("create_post", {"body": " Hello ", **visibility}, user=user))
    assert result.success and result.data["success"]
    document = dispatch.collections["posts"].insert_one.await_args.args[0]
    assert document["app_id"] == APP_ID
    assert document["author_id"] == user
    assert document["visibility"] == "public"
    assert document["body"] == "Hello"
    assert dispatch.emitter.await_args.args[0] == "domain.social.post.published"


@pytest.mark.asyncio
async def test_comment_uses_second_user_identity_and_runtime_scope(dispatch):
    dispatch.collections["posts"].find_one.return_value = {
        "post_id": "post", "author_id": "alice", "status": "published", "visibility": "public",
    }
    result = await dispatch.executor.execute(_request(
        "add_comment", {"post_id": "post", "body": "Thanks!"}, user="bob",
    ))
    assert result.success and result.data["success"]
    document = dispatch.collections["comments"].insert_one.await_args.args[0]
    assert document["app_id"] == APP_ID
    assert document["author_id"] == "bob"
    assert document["post_id"] == "post"
    assert dispatch.collections["posts"].update_one.await_args.args == (
        {"app_id": APP_ID, "post_id": "post"}, {"$inc": {"comment_count": 1}},
    )
    assert dispatch.emitter.await_args.args[0] == "domain.social.post.commented"


@pytest.mark.asyncio
async def test_reaction_upsert_and_toggle_preserve_runtime_app_and_user_filters(dispatch):
    posts, reactions = dispatch.collections["posts"], dispatch.collections["reactions"]
    posts.find_one.return_value = {"post_id": "post", "author_id": "alice", "status": "published", "visibility": "public"}
    reactions.count_documents.return_value = 1
    added = await dispatch.executor.execute(_request(
        "react_to_post", {"post_id": "post", "reaction_type": "like"}, user="bob",
    ))
    assert added.success and added.data == {"success": True, "action": "added", "reaction_count": 1}
    query, update = reactions.update_one.await_args.args
    assert query == {"app_id": APP_ID, "post_id": "post", "user_id": "bob"}
    assert reactions.update_one.await_args.kwargs["upsert"] is True
    assert update["$set"]["user_id"] == "bob"
    assert "app_id" not in update["$set"]  # The Mongo upsert equality filter owns this scope.
    assert posts.update_one.await_args.args == (
        {"app_id": APP_ID, "post_id": "post"}, {"$set": {"reaction_count": 1}},
    )

    reactions.find_one.return_value = {"post_id": "post", "user_id": "bob", "reaction_type": "like"}
    reactions.count_documents.return_value = 0
    removed = await dispatch.executor.execute(_request(
        "react_to_post", {"post_id": "post", "reaction_type": "like"}, user="bob",
    ))
    assert removed.success and removed.data == {"success": True, "action": "removed", "reaction_count": 0}
    assert reactions.delete_one.await_args.args[0] == query


@pytest.mark.asyncio
async def test_public_list_filters_reach_real_persistence_with_one_app_scope(dispatch):
    result = await dispatch.executor.execute(_request("list_posts", {"visibility": "public"}))
    assert result.success and result.data == {"posts": [], "count": 0, "next_cursor": None}
    assert dispatch.collections["posts"].find.call_args.args[0] == {
        "app_id": APP_ID, "visibility": "public",
        "status": {"$eq": "published", "$not": {"$type": "array"}},
        "$or": [
            {"visibility": {"$eq": "public", "$not": {"$type": "array"}}},
            {"author_id": {"$eq": "alice", "$not": {"$type": "array"}}},
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("action,collection,identifier", [
    ("delete_post", "posts", "post_id"),
    ("delete_comment", "comments", "comment_id"),
])
async def test_only_author_can_delete_through_real_executor(dispatch, action, collection, identifier):
    record = {identifier: "record", "post_id": "post", "author_id": "alice", "status": "published"}
    raw = dispatch.collections[collection]
    raw.find_one.return_value = record
    denied = await dispatch.executor.execute(_request(action, {identifier: "record"}, user="bob"))
    assert denied.success and denied.data["success"] is False
    assert "permission" in denied.data["error"]
    raw.update_one.assert_not_called()
    raw.delete_one.assert_not_called()
    dispatch.emitter.assert_not_called()

    allowed = await dispatch.executor.execute(_request(action, {identifier: "record"}, user="alice"))
    assert allowed.success and allowed.data == {"success": True}
    mutation = raw.update_one if action == "delete_post" else raw.delete_one
    mutation.assert_awaited_once()
    assert mutation.await_args.args[0] == {"app_id": APP_ID, identifier: "record"}
    dispatch.emitter.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,params", [
    ("list_posts", {"visibility": "public"}),
    ("list_user_posts", {"user_id": "alice"}),
    ("list_comments", {"post_id": "missing"}),
    ("get_post", {"post_id": "missing"}),
    ("get_reaction_summary", {"post_id": "missing"}),
    ("create_post", {"body": " "}),
    ("add_comment", {"post_id": "missing", "body": "Hello"}),
])
async def test_empty_and_error_outputs_match_declared_schema(dispatch, caplog, action, params):
    result = await dispatch.executor.execute(_request(action, params))
    assert result.success, result.error
    assert "MODULE_OUTPUT_INVALID" not in caplog.text


@pytest.mark.asyncio
async def test_account_export_and_delete_reach_actual_declared_collections(dispatch):
    for name, raw in dispatch.collections.items():
        owner = "user_id" if name == "reactions" else "author_id"
        raw.find.return_value.to_list.return_value = [{
            "_id": "driver-only", "app_id": APP_ID, owner: "alice", "body": name,
        }]
    exported = await account_data_registry.export_all(
        app_id=APP_ID, user_id="alice", db=dispatch.database, persistence=dispatch.persistence,
    )
    deleted = await account_data_registry.delete_all(
        app_id=APP_ID, user_id="alice", db=dispatch.database, persistence=dispatch.persistence,
    )
    assert set(exported) == {"posts", "post_reactions", "post_comments"}
    for rows in exported.values():
        assert len(rows) == 1 and "_id" not in rows[0]
    assert deleted == {"user_posts": {
        "posts_deleted": 1, "reactions_deleted": 1, "comments_deleted": 1, "deleted_count": 3,
    }}
    for name, raw in dispatch.collections.items():
        owner = "user_id" if name == "reactions" else "author_id"
        assert raw.find.call_args.args[0] == {"app_id": APP_ID, owner: "alice"}
        assert raw.delete_many.await_args.args[0] == {"app_id": APP_ID, owner: "alice"}


@pytest.mark.asyncio
@pytest.mark.parametrize("app_id,user_id", [("another-app", "alice"), (APP_ID, "bob")])
async def test_account_handler_rejects_other_app_or_user_before_storage(dispatch, app_id, user_id):
    exported = await account_data_registry.export_all(
        app_id=app_id, user_id=user_id, db=dispatch.database, persistence=dispatch.persistence,
    )
    assert set(exported) == {"user_posts.__error__"}
    assert "another" in exported["user_posts.__error__"]
    deleted = await account_data_registry.delete_all(
        app_id=app_id, user_id=user_id, db=dispatch.database, persistence=dispatch.persistence,
    )
    assert "another" in deleted["user_posts"]["error"]
    for raw in dispatch.collections.values():
        raw.find.assert_not_called()
        raw.delete_many.assert_not_called()


@pytest.mark.asyncio
async def test_account_export_paginates_without_losing_scope_or_exposing_mongo_ids(dispatch):
    from bson import ObjectId

    identifiers = sorted(ObjectId() for _ in range(101))
    records = [{"_id": value, "author_id": "alice", "body": str(index)} for index, value in enumerate(identifiers)]
    raw = dispatch.collections["posts"]
    raw.find.return_value.to_list.side_effect = [records[:100], records[100:]]

    exported = await account_data_registry.export_all(
        app_id=APP_ID, user_id="alice", db=dispatch.database, persistence=dispatch.persistence,
    )

    assert [row["body"] for row in exported["posts"]] == [str(index) for index in range(101)]
    assert all("_id" not in row for row in exported["posts"])
    assert [call.args[0] for call in raw.find.call_args_list] == [
        {"app_id": APP_ID, "author_id": "alice"},
        {"app_id": APP_ID, "author_id": "alice", "_id": {"$gt": identifiers[99]}},
    ]
    assert all(call.args == ([("_id", 1)],) for call in raw.find.return_value.sort.call_args_list)
