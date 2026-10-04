"""Canonical social pack privacy through real loading, dispatch, and persistence.

Only the raw Mongo driver is mocked. Friendship membership is not implemented:
friends, private, and unrecognized visibility remain readable by the author only.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_managed_capability_templates,
)
from mozaiksai.core.account import account_data_registry
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence import collection_name_for
from tests.module_authority_test_helpers import enforce_authority

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "examples/canonical-apps/community/app"
APP_ID = "common-ground"
SCOPES = ("user_posts.read", "user_posts.create", "user_posts.react")
VISIBILITIES = ("public", "private", "friends", "unknown", None)
POST_ACTIONS = (
    "get_post", "add_comment", "react_to_post", "list_comments", "get_reaction_summary",
)
DENIED_RESULTS = {
    "get_post": {"post": None},
    "add_comment": {"success": False, "error": "Post not found.", "comment": None},
    "react_to_post": {"success": False, "error": "Post not found.", "action": "none", "reaction_count": 0},
    "list_comments": {"comments": [], "count": 0, "next_cursor": None},
    "get_reaction_summary": {"total": 0, "by_type": {}, "viewer_reaction": None},
}


def _post(visibility="public", **overrides):
    row = {
        "app_id": APP_ID, "post_id": "post", "author_id": "alice",
        "status": "published", "body": "A post", "created_at": "2026-10-04T12:00:00Z",
    }
    if visibility is not None:
        row["visibility"] = visibility
    return {**row, **overrides}


def _field_matches(value, expected):
    """Mongo equality also matches an element of an array-valued field."""
    if not isinstance(expected, dict):
        return value == expected or isinstance(value, list) and expected in value
    assert set(expected) <= {"$eq", "$not", "$type", "$lt", "$gt"}, expected
    for operator, operand in expected.items():
        if operator == "$eq" and not _field_matches(value, operand):
            return False
        if operator == "$not" and _field_matches(value, operand):
            return False
        if operator == "$type":
            assert operand == "array", operand
            if not isinstance(value, list):
                return False
        if operator == "$lt" and (value is None or not value < operand):
            return False
        if operator == "$gt" and (value is None or not value > operand):
            return False
    return True


def _matches(row, query):
    """Evaluate just the driver query operators exercised here, never privacy policy."""
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, clause) for clause in expected):
                return False
        elif key == "$and":
            if not all(_matches(row, clause) for clause in expected):
                return False
        elif not _field_matches(row.get(key), expected):
            return False
    return True


def _cursor(rows):
    selected = deepcopy(rows)
    cursor = MagicMock()

    def sort(keys):
        for key, order in reversed(keys):
            selected.sort(key=lambda row: row[key], reverse=order < 0)
        return cursor

    def limit(size):
        del selected[size:]
        return cursor

    cursor.sort.side_effect = sort
    cursor.limit.side_effect = limit
    cursor.to_list = AsyncMock(side_effect=lambda **_kwargs: deepcopy(selected))
    return cursor


def _driver(rows):
    raw = MagicMock()
    raw.find_one = AsyncMock(side_effect=lambda query, *_args, **_kwargs: next(
        (deepcopy(row) for row in rows if _matches(row, query)), None,
    ))
    raw.find.side_effect = lambda query, *_args, **_kwargs: _cursor([
        row for row in rows if _matches(row, query)
    ])
    raw.insert_one = AsyncMock(return_value=SimpleNamespace(inserted_id="inserted"))
    raw.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
    raw.delete_one = AsyncMock(return_value=SimpleNamespace(deleted_count=1))
    raw.count_documents = AsyncMock(return_value=1)
    raw.aggregate.return_value = _cursor([{"_id": "like", "count": 1}])
    return raw


@pytest_asyncio.fixture
async def dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(account_data_registry, "_handlers", {})
    app = tmp_path / "app"
    for relative in ("app.json", "config/auth.yaml"):
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REFERENCE / relative).read_bytes())
    contract = json.loads((REFERENCE / "data/contract.json").read_text(encoding="utf-8"))
    posts = next(item for item in contract["surfaces"][0]["collections"] if item["name"] == "posts")
    visibility = next(field for field in posts["fields"] if field["name"] == "visibility")
    visibility["enum"] = ["public", "friends", "private"]
    (app / "data").mkdir()
    (app / "data/contract.json").write_text(json.dumps(contract), encoding="utf-8")
    rendered = resolve_managed_capability_templates([{
        "id": "social", "capability_source": "generated_module",
        "pack_source_path": str(ROOT / "factory_app/build_context/social"),
    }])
    for item in rendered:
        name = item["filename"]
        if name.startswith("modules/user_posts/backend/") or name in {
            "modules/user_posts/module.yaml", "modules/user_posts/contracts/events.yaml",
        }:
            target = app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
    loaded = await AppLoader.load(str(app))
    assert loaded.failed_module_names == []
    assert {module.name for module in loaded.modules} == {"user_posts"}
    records = {name: [] for name in ("posts", "comments", "reactions")}
    collections = {name: _driver(rows) for name, rows in records.items()}
    database = {
        collection_name_for(app_id=APP_ID, module_id="user_posts", entity_name=name): raw
        for name, raw in collections.items()
    }
    emitter = AsyncMock()
    executor = ModuleExecutor(
        data_contract=loaded.data_contract,
        persistence_client={"social_privacy_test": database},
        persistence_database="social_privacy_test",
        platform_hooks=PlatformHookRegistry(),
        audit_logger=SimpleNamespace(log_module_action=AsyncMock()),
        event_emitter=emitter,
    )
    for module in loaded.modules:
        executor.register_loaded_module(module)
    return SimpleNamespace(executor=executor, records=records, collections=collections, emitter=emitter)


async def _execute(dispatch, action, params, *, user="bob"):
    result = await dispatch.executor.execute(ModuleRequest(
        module="user_posts", action=action, params=params, app_id=APP_ID, user_id=user,
        authority=enforce_authority(*SCOPES, actor_id=user),
    ))
    assert result.success, result.error
    return result.data


def _params(action):
    return {"post_id": "post", **({"body": "A comment"} if action == "add_comment" else {})}


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["private", "friends", "unknown", None, "deleted", "missing", "foreign-app"])
@pytest.mark.parametrize("action", POST_ACTIONS)
async def test_unreadable_parent_denies_all_post_actions_before_secondary_access(dispatch, visibility, action):
    if visibility != "missing":
        overrides = {"status": "deleted"} if visibility == "deleted" else {}
        if visibility == "foreign-app":
            overrides["app_id"] = "another-app"
        dispatch.records["posts"].append(_post(
            "public" if visibility in {"deleted", "foreign-app"} else visibility, **overrides,
        ))
    # Existing related data must not reveal even counts or viewer reaction state.
    dispatch.records["comments"].append({"app_id": APP_ID, "post_id": "post", "body": "Private comment"})
    dispatch.records["reactions"].append({
        "app_id": APP_ID, "post_id": "post", "user_id": "bob", "reaction_type": "like",
    })

    assert await _execute(dispatch, action, _params(action)) == DENIED_RESULTS[action]

    posts = dispatch.collections["posts"]
    posts.find_one.assert_awaited_once()
    assert posts.find_one.await_args.args[0] == {"app_id": APP_ID, "post_id": "post"}
    assert [call[0] for call in posts.mock_calls] == ["find_one"]
    assert dispatch.collections["comments"].mock_calls == []
    assert dispatch.collections["reactions"].mock_calls == []
    dispatch.emitter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", POST_ACTIONS)
async def test_deleted_post_is_unreadable_even_to_author(dispatch, action):
    dispatch.records["posts"].append(_post("private", status="deleted"))
    assert await _execute(dispatch, action, _params(action), user="alice") == DENIED_RESULTS[action]
    assert dispatch.collections["comments"].mock_calls == []
    assert dispatch.collections["reactions"].mock_calls == []
    dispatch.collections["posts"].update_one.assert_not_called()
    dispatch.emitter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("user,visibility", [("alice", value) for value in VISIBILITIES] + [("bob", "public")])
@pytest.mark.parametrize("action", POST_ACTIONS)
async def test_author_access_and_public_access_survive_privacy_gate(dispatch, user, visibility, action):
    post = _post(visibility)
    dispatch.records["posts"].append(post)
    comment = {"app_id": APP_ID, "post_id": "post", "body": "Visible comment", "created_at": "2026-10-04"}
    dispatch.records["comments"].append(comment)

    data = await _execute(dispatch, action, _params(action), user=user)

    assert dispatch.collections["posts"].find_one.await_args.args[0] == {"app_id": APP_ID, "post_id": "post"}
    if action == "get_post":
        assert data == {"post": post}
    elif action == "list_comments":
        assert data == {"comments": [comment], "count": 1, "next_cursor": None}
        assert dispatch.collections["comments"].find.call_args.args[0] == {"app_id": APP_ID, "post_id": "post"}
    elif action == "get_reaction_summary":
        assert data == {"total": 1, "by_type": {"like": 1}, "viewer_reaction": None}
        assert dispatch.collections["reactions"].aggregate.call_args.args[0][:2] == [
            {"$match": {"app_id": APP_ID}}, {"$match": {"post_id": "post"}},
        ]
    elif action == "add_comment":
        assert data["success"] and data["comment"]["author_id"] == user
        assert dispatch.collections["comments"].insert_one.await_args.args[0]["app_id"] == APP_ID
    else:
        assert data == {"success": True, "action": "added", "reaction_count": 1}
        assert dispatch.collections["reactions"].update_one.await_args.args[0] == {
            "app_id": APP_ID, "post_id": "post", "user_id": user,
        }
    if action in {"add_comment", "react_to_post"}:
        dispatch.emitter.assert_awaited_once()
    else:
        dispatch.emitter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("user", ["alice", "bob"])
@pytest.mark.parametrize("action,params", [
    ("list_posts", {}),
    ("list_posts", {"visibility": "all"}),
    ("list_posts", {"visibility": "private"}),
    ("list_posts", {"visibility": "public"}),
    ("list_posts", {"author_id": "alice", "visibility": "all"}),
    ("list_user_posts", {"user_id": "alice"}),
    ("list_user_posts", {}),
])
async def test_list_filters_only_narrow_author_or_public_with_app_scope(dispatch, user, action, params):
    rows = [_post(value, post_id=f"alice-{value}") for value in VISIBILITIES]
    rows.extend([
        _post("private", post_id="bob-private", author_id="bob"),
        _post("public", post_id="deleted", status="deleted"),
        _post("public", post_id="foreign-app", app_id="another-app"),
    ])
    dispatch.records["posts"].extend(rows)
    expected = {"alice-public"}
    expected |= ({f"alice-{value}" for value in VISIBILITIES} if user == "alice" else {"bob-private"})
    author = params.get("author_id") if action == "list_posts" else params.get("user_id", user)
    if author:
        expected = {identifier for identifier in expected if identifier.startswith(f"{author}-")}
    visibility = params.get("visibility")
    if visibility and visibility != "all":
        expected = {identifier for identifier in expected if identifier.endswith(f"-{visibility}")}

    data = await _execute(dispatch, action, params, user=user)

    assert {row["post_id"] for row in data["posts"]} == expected
    assert data["count"] == len(expected) and data["next_cursor"] is None
    query = dispatch.collections["posts"].find.call_args.args[0]
    assert query["app_id"] == APP_ID
    assert query["status"] == {"$eq": "published", "$not": {"$type": "array"}}
    assert query["$or"] == [
        {"visibility": {"$eq": "public", "$not": {"$type": "array"}}},
        {"author_id": {"$eq": user, "$not": {"$type": "array"}}},
    ]
    if author:
        assert query["author_id"] == author
    if visibility and visibility != "all":
        assert query["visibility"] == visibility
    else:
        assert "visibility" not in query
    dispatch.emitter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("user", ["alice", "bob"])
@pytest.mark.parametrize("action", ["list_posts", "list_user_posts", "get_post"])
@pytest.mark.parametrize("malformed", [
    {"visibility": ["private", "public"]},
    {"author_id": ["alice", "bob"], "visibility": "private"},
    {"status": ["published", "deleted"]},
])
async def test_array_elements_cannot_grant_stored_post_access(dispatch, user, action, malformed):
    post = _post(**malformed)
    dispatch.records["posts"].append(post)
    params = {"post_id": "post"} if action == "get_post" else (
        {"user_id": "alice"} if action == "list_user_posts" else {"visibility": "all"}
    )
    # Nonpublic/malformed visibility does not take away the scalar author's access.
    allowed = user == "alice" and isinstance(malformed.get("visibility"), list)

    data = await _execute(dispatch, action, params, user=user)

    if action == "get_post":
        assert data == {"post": post if allowed else None}
    else:
        assert data == {"posts": [post] if allowed else [], "count": int(allowed), "next_cursor": None}
    assert dispatch.collections["comments"].mock_calls == []
    assert dispatch.collections["reactions"].mock_calls == []
    dispatch.collections["posts"].update_one.assert_not_called()
    dispatch.emitter.assert_not_called()


@pytest.mark.asyncio
async def test_hidden_newer_posts_do_not_consume_public_pagination(dispatch):
    dispatch.records["posts"].extend([
        _post("private", post_id="hidden-newest", created_at="2026-10-04T16:00:00Z"),
        _post("friends", post_id="hidden-next", created_at="2026-10-04T15:00:00Z"),
        _post("public", post_id="first", created_at="2026-10-04T14:00:00Z"),
        _post("public", post_id="second", created_at="2026-10-04T13:00:00Z"),
    ])
    first = await _execute(dispatch, "list_posts", {"visibility": "all", "limit": 1})
    assert [row["post_id"] for row in first["posts"]] == ["first"]
    assert first["next_cursor"] == "2026-10-04T14:00:00Z"
    second = await _execute(dispatch, "list_posts", {"limit": 1, "before": first["next_cursor"]})
    assert [row["post_id"] for row in second["posts"]] == ["second"]
    assert second["next_cursor"] is None
    query = dispatch.collections["posts"].find.call_args.args[0]
    assert query["app_id"] == APP_ID and query["created_at"] == {"$lt": first["next_cursor"]}
    assert "$or" in query


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["public", "friends", "private"])
async def test_canonical_pack_still_accepts_explicit_visibility_on_create(dispatch, visibility):
    data = await _execute(dispatch, "create_post", {"body": "A post", "visibility": visibility}, user="alice")
    assert data["success"] and data["post"]["visibility"] == visibility
    row = dispatch.collections["posts"].insert_one.await_args.args[0]
    assert (row["app_id"], row["author_id"], row["visibility"]) == (APP_ID, "alice", visibility)


@pytest.mark.asyncio
@pytest.mark.parametrize("action,collection,identifier", [
    ("delete_post", "posts", "post_id"), ("delete_comment", "comments", "comment_id"),
])
async def test_privacy_does_not_remove_author_deletion(dispatch, action, collection, identifier):
    dispatch.records["posts"].append(_post("private"))
    dispatch.records["comments"].append({
        "app_id": APP_ID, "post_id": "post", "comment_id": "comment", "author_id": "alice",
    })
    value = "post" if action == "delete_post" else "comment"
    denied = await _execute(dispatch, action, {identifier: value}, user="bob")
    assert denied["success"] is False and "permission" in denied["error"]
    for raw in dispatch.collections.values():
        raw.update_one.assert_not_called()
        raw.delete_one.assert_not_called()
    dispatch.emitter.assert_not_called()

    assert await _execute(dispatch, action, {identifier: value}, user="alice") == {"success": True}
    raw = dispatch.collections[collection]
    mutation = raw.update_one if action == "delete_post" else raw.delete_one
    mutation.assert_awaited_once()
    assert mutation.await_args.args[0] == {"app_id": APP_ID, identifier: value}
    dispatch.emitter.assert_awaited_once()
