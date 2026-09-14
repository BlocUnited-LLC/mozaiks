"""Generated support and messaging packs work together under enforced authority."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from tests.module_authority_test_helpers import enforce_authority

PACKS = Path(__file__).resolve().parents[1] / "factory_app" / "build_context"


def _materialize(root: Path) -> None:
    for pack, module_id in (("messaging", "messages"), ("support", "support")):
        shutil.copytree(
            PACKS / pack / "templates" / "modules" / module_id,
            root / "modules" / module_id,
        )


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict) and "$ne" in expected:
            if actual == expected["$ne"]:
                return False
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


class _Collection:
    def __init__(self):
        self.rows = []

    async def insert_one(self, record):
        self.rows.append(dict(record))

    async def find_one(self, query):
        return next((row for row in self.rows if _matches(row, query)), None)

    async def find_many(self, query, *, limit=50, sort=None):
        rows = [row for row in self.rows if _matches(row, query)]
        for field, direction in reversed(sort or []):
            rows.sort(key=lambda row: row.get(field) or "", reverse=direction < 0)
        return rows[:limit]

    async def update_one(self, query, update, **_kwargs):
        row = await self.find_one(query)
        if row is None:
            return SimpleNamespace(matched_count=0)
        row.update(update["$set"])
        return SimpleNamespace(matched_count=1)


class _Persistence:
    def __init__(self):
        self.collections = {}

    def collection(self, module_id, entity_name):
        return self.collections.setdefault((module_id, entity_name), _Collection())


def _executor(root: Path) -> ModuleExecutor:
    loader = ModuleLoader(str(root))
    executor = ModuleExecutor()
    for module_id in ("messages", "support"):
        module = loader.load(module_id)
        executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_permissions=module.action_permissions_map,
            action_schemas=module.action_schemas_map,
        )
    return executor


async def _dispatch(executor, persistence, user_id, permissions, module, action, params):
    authority = enforce_authority(*permissions, actor_id=user_id)
    context = ModuleContext(
        app_id="app_1",
        workspace_id="workspace_1",
        user_id=user_id,
        permissions=list(permissions),
        persistence=persistence,
    )
    return await executor.execute(
        ModuleRequest(
            module=module,
            action=action,
            params=params,
            app_id="app_1",
            workspace_id="workspace_1",
            user_id=user_id,
            authority=authority,
        ),
        context=context,
    )


@pytest.mark.asyncio
async def test_submit_only_user_creates_reads_and_replies_through_ticket_actions(tmp_path):
    _materialize(tmp_path)
    executor = _executor(tmp_path)
    persistence = _Persistence()
    submit = ["support.submit"]

    created = await _dispatch(
        executor, persistence, "alice", submit, "support", "create_support_request",
        {"message": "Please help with my account", "severity": "low"},
    )
    assert created.success is True
    assert created.data["success"] is True
    request = created.data["request"]
    thread_id = request["message_thread_id"]
    assert thread_id
    assert "message" not in request
    assert persistence.collection("messages", "threads").rows[0]["related_id"] == request["request_id"]
    assert [item["body"] for item in persistence.collection("messages", "messages").rows] == [
        "Please help with my account"
    ]

    read = await _dispatch(
        executor, persistence, "alice", submit, "support", "get_support_conversation",
        {"request_id": request["request_id"]},
    )
    assert read.success is True
    assert [item["body"] for item in read.data["messages"]] == ["Please help with my account"]

    replied = await _dispatch(
        executor, persistence, "alice", submit, "support", "reply_support_request",
        {"request_id": request["request_id"], "body": "I also have the error code"},
    )
    assert replied.success is True
    assert replied.data["success"] is True
    assert replied.data["message"]["thread_id"] == thread_id

    operator = await _dispatch(
        executor, persistence, "operator", ["support.submit", "support.manage"],
        "support", "reply_support_request",
        {"request_id": request["request_id"], "body": "We are checking this now"},
    )
    assert operator.success is True
    assert operator.data["message"]["sender_role"] == "operator"
    owner_after_reply = await _dispatch(
        executor, persistence, "alice", submit, "support", "get_support_conversation",
        {"request_id": request["request_id"]},
    )
    assert [item["body"] for item in owner_after_reply.data["messages"]] == [
        "Please help with my account", "I also have the error code", "We are checking this now"
    ]

    stranger = await _dispatch(
        executor, persistence, "bob", submit, "support", "get_support_conversation",
        {"request_id": request["request_id"]},
    )
    assert stranger.success is False
    assert stranger.error_code == "PERMISSION_DENIED"

    generic_read = await _dispatch(
        executor, persistence, "alice", ["messages.read"], "messages", "get_thread",
        {"thread_id": thread_id},
    )
    assert generic_read.success is True
    assert generic_read.data["thread"] is None
    generic_send = await _dispatch(
        executor, persistence, "alice", ["messages.write"], "messages", "send_message",
        {"thread_id": thread_id, "body": "bypass"},
    )
    assert generic_send.success is True
    assert generic_send.data["success"] is False
    assert len(persistence.collection("messages", "messages").rows) == 3
    generic_create = await _dispatch(
        executor, persistence, "alice", ["messages.write"], "messages", "create_thread",
        {"thread_type": "support"},
    )
    assert generic_create.success is False
    assert generic_create.error_code == "INVALID_PARAMS"


def test_loader_binds_sibling_messages_to_each_workspace(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _materialize(first)
    _materialize(second)

    first_support = ModuleLoader(str(first)).load("support")
    first_service = first_support.handler._service.messages
    second_support = ModuleLoader(str(second)).load("support")
    second_service = second_support.handler._service.messages

    assert Path(first_service.create_thread.__code__.co_filename).is_relative_to(first)
    assert Path(second_service.create_thread.__code__.co_filename).is_relative_to(second)
    assert type(first_service) is not type(second_service)
