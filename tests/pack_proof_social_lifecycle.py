"""Explicit Social post semantics through the existing trusted HTTP/Mongo probe.

These assertions supplement the generic physical-delete observer. They do not
change its result or establish downstream reaction delivery.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from mozaiksai.core.runtime.persistence import MongoPersistenceContext

# Test-app grants are deliberate; they are never collected from candidate code.
SOCIAL_PROOF_ROLES = {
    "social_member": ["user_posts.read", "user_posts.create"],
    "social_moderator": ["user_posts.read", "user_posts.create", "user_posts.moderate"],
    "social_admin": ["user_posts.read", "user_posts.create", "user_posts.admin"],
    "social_moderate_without_create": ["user_posts.read", "user_posts.moderate"],
    "admin": ["user_posts.read", "user_posts.create"],
    "moderator": ["user_posts.read", "user_posts.create"],
}
SOCIAL_SEMANTIC_CHECKS = frozenset(f"semantic.social.posts.{name}" for name in (
    "published_control", "foreign_member_denied", "admin_name_denied",
    "moderator_name_denied", "missing_create_denied", "author_delete",
    "author_tombstone", "author_hidden", "moderator_delete",
    "moderator_tombstone", "moderator_hidden", "admin_delete",
    "admin_tombstone", "admin_hidden", "foreign_app_denied",
))


async def prove_social_post_lifecycle(run: Any) -> None:
    """Observe real action responses and persisted records, with fixed role sets."""
    def record(name: str, passed: bool, message: str) -> None:
        check = f"semantic.social.posts.{name}"
        assert check in SOCIAL_SEMANTIC_CHECKS
        run.record(check, passed, message, coverage="social_post_lifecycle")
        if not passed:
            raise AssertionError(f"Social semantic assertion failed: {check}: {message}")

    def principal(label: str, role: str) -> Any:
        base = run.principal(f"social {label}")
        value = replace(base, roles=(role,), exact_roles=True)
        run.principals[value.label] = value
        return value

    author = principal("author", "social_member")
    foreign = principal("foreign member", "social_member")
    moderator = principal("moderator", "social_moderator")
    admin = principal("admin", "social_admin")
    readers = (author, foreign, admin)
    names = MongoPersistenceContext(
        app_id=run.app_id, database_name=run.database_name,
        client=run.client, data_contract=run.load.data_contract,
    )
    collection = run.database[names.collection_name("user_posts", "posts")]

    async def call(action: str, actor: Any, params: dict[str, Any]) -> tuple[int, Any]:
        status, body, _summary = await run.call("POST", "user_posts", action, actor, params)
        return status, body

    async def create(label: str) -> dict[str, Any]:
        text = f"Social lifecycle {label} {uuid4().hex}"
        status, body = await call("create_post", author, {"body": text, "visibility": "public"})
        assert status == 200 and isinstance(body, dict) and body.get("success") is True, (status, body)
        post = body.get("post") or {}
        identifier = post.get("post_id")
        assert isinstance(identifier, str) and identifier
        stored = await collection.find_one({"app_id": run.app_id, "post_id": identifier})
        assert stored and stored["author_id"] == author.user_id
        assert stored["body"] == text and stored["status"] == "published"
        return stored

    async def visible(identifier: str, actor: Any) -> tuple[bool, bool, bool]:
        status, body = await call("get_post", actor, {"post_id": identifier})
        assert status == 200 and isinstance(body, dict) and "post" in body, (status, body)
        assert body["post"] is None or isinstance(body["post"], dict) and body["post"].get("post_id") == identifier
        detail = isinstance(body["post"], dict) and body["post"].get("post_id") == identifier
        lists = []
        for action, params in (
            ("list_posts", {"limit": 99}),
            ("list_user_posts", {"user_id": author.user_id, "limit": 99}),
        ):
            status, body = await call(action, actor, params)
            assert status == 200 and isinstance(body, dict) and isinstance(body.get("posts"), list), (status, body)
            assert body.get("count") == len(body["posts"])
            lists.append(any(item.get("post_id") == identifier for item in body["posts"]))
        return detail, *lists

    target = await create("author target")
    control = await create("retained visible control")
    target_id, control_id = target["post_id"], control["post_id"]
    before_visible = [await visible(target_id, actor) for actor in readers]
    record("published_control", all(all(row) for row in before_visible),
           "The author's published target is visible through detail and both lists to all three readers.")

    for name, actor, expected_status in (
        ("foreign_member_denied", foreign, 200),
        ("admin_name_denied", principal("admin role name only", "admin"), 200),
        ("moderator_name_denied", principal("moderator role name only", "moderator"), 200),
        ("missing_create_denied", principal("moderate without create", "social_moderate_without_create"), 403),
    ):
        before = await collection.find_one({"app_id": run.app_id, "post_id": target_id})
        event_count = len(run.events)
        status, body = await call("delete_post", actor, {"post_id": target_id})
        after = await collection.find_one({"app_id": run.app_id, "post_id": target_id})
        denied = status == expected_status and (status == 403 or body.get("success") is False)
        record(name, denied and after == before and len(run.events) == event_count,
               "Deletion was refused at the expected boundary; the exact record and emitted events are unchanged.")

    async def delete_and_check(name: str, actor: Any, post: dict[str, Any]) -> None:
        identifier = post["post_id"]
        status, body = await call("delete_post", actor, {"post_id": identifier})
        record(f"{name}_delete", status == 200 and isinstance(body, dict) and body.get("success") is True,
               "The admitted actor successfully deleted the post through its mounted HTTP action.")
        stored = await collection.find_one({"app_id": run.app_id, "post_id": identifier})
        updated = stored.get("updated_at") if stored else None
        try:
            timestamp_valid = isinstance(updated, str) and datetime.fromisoformat(updated) >= datetime.fromisoformat(post["updated_at"])
        except (TypeError, ValueError):
            timestamp_valid = False
        retained = bool(stored) and {
            key: value for key, value in stored.items() if key not in {"status", "updated_at"}
        } == {key: value for key, value in post.items() if key not in {"status", "updated_at"}}
        record(f"{name}_tombstone", retained and stored["status"] == "deleted" and timestamp_valid,
               "The same app/author/id/body record remains as a deleted tombstone with a valid update timestamp.")
        observations = [(await visible(identifier, reader), await visible(control_id, reader)) for reader in readers]
        record(f"{name}_hidden", all(not any(deleted) and all(kept) for deleted, kept in observations),
               "Deleted detail and both list results are empty for author/member/admin while the control remains visible.")

    await delete_and_check("author", author, target)
    await delete_and_check("moderator", moderator, await create("moderator target"))
    await delete_and_check("admin", admin, await create("admin target"))

    foreign_app = f"social-proof-foreign-{uuid4().hex}"
    foreign_names = MongoPersistenceContext(
        app_id=foreign_app, database_name=run.database_name,
        client=run.client, data_contract=run.load.data_contract,
    )
    foreign_collection = run.database[foreign_names.collection_name("user_posts", "posts")]
    foreign_record = {**control, "app_id": foreign_app, "post_id": f"foreign-{uuid4().hex}"}
    await foreign_collection.insert_one(foreign_record)
    status, body = await call("delete_post", admin, {"post_id": foreign_record["post_id"]})
    after = await foreign_collection.find_one({"_id": foreign_record["_id"]})
    hidden = [await visible(foreign_record["post_id"], reader) for reader in readers]
    record("foreign_app_denied", status == 200 and body.get("success") is False and after == foreign_record
           and all(not any(row) for row in hidden),
           "An admitted administrator cannot read or delete the foreign app's persisted record.")
