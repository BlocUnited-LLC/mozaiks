"""
Service-layer unit tests for the messaging pack contacts module.

Covers ContactsService logic using in-memory repo fakes — no live DB or LLM calls.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from factory_app.build_context.messaging.templates.modules.contacts.backend.service import (
    ContactsService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(*, user_id: str = "user-1") -> SimpleNamespace:
    ctx = SimpleNamespace(user_id=user_id)
    ctx.emit = AsyncMock()
    return ctx


def _contact(
    user_id: str = "user-1",
    contact_user_id: str = "user-2",
    status: str = "active",
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> dict[str, Any]:
    return {
        "contact_id": f"c-{contact_user_id}",
        "user_id": user_id,
        "contact_user_id": contact_user_id,
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
    }


class FakeContactRepo:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self._docs: list[dict[str, Any]] = [deepcopy(d) for d in (docs or [])]

    async def list(
        self,
        ctx,
        *,
        query: dict[str, Any],
        limit: int,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [deepcopy(d) for d in self._docs]
        # Apply query filters
        for key, val in query.items():
            rows = [r for r in rows if r.get(key) == val]
        # Apply cursor
        if before:
            rows = [r for r in rows if (r.get("created_at") or "") < before]
        # Sort created_at desc
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return rows[:limit]

    async def get(
        self, ctx, *, user_id: str, contact_user_id: str
    ) -> dict[str, Any] | None:
        for d in self._docs:
            if d["user_id"] == user_id and d["contact_user_id"] == contact_user_id:
                return deepcopy(d)
        return None

    async def insert(self, ctx, *, doc: dict[str, Any]) -> None:
        self._docs.append(deepcopy(doc))

    async def update(
        self, ctx, *, user_id: str, contact_user_id: str, updates: dict[str, Any]
    ) -> int:
        for d in self._docs:
            if d["user_id"] == user_id and d["contact_user_id"] == contact_user_id:
                d.update(updates)
                return 1
        return 0


def _service(docs: list[dict[str, Any]] | None = None) -> ContactsService:
    return ContactsService(contacts=FakeContactRepo(docs))


# ---------------------------------------------------------------------------
# add_contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_contact_inserts_new_contact() -> None:
    svc = _service()
    ctx = _ctx(user_id="user-1")
    result = await svc.add_contact(ctx, contact_user_id="user-2")
    assert result["success"] is True
    ctx.emit.assert_awaited_once()
    event, payload = ctx.emit.call_args[0]
    assert event == "app.contacts.contact.added"
    assert payload["contact_user_id"] == "user-2"


@pytest.mark.asyncio
async def test_add_contact_rejects_self() -> None:
    svc = _service()
    result = await svc.add_contact(_ctx(user_id="user-1"), contact_user_id="user-1")
    assert result["success"] is False
    assert "yourself" in result["error"]


@pytest.mark.asyncio
async def test_add_contact_rejects_empty_target() -> None:
    svc = _service()
    result = await svc.add_contact(_ctx(), contact_user_id="")
    assert result["success"] is False
    assert "contact_user_id" in result["error"]


@pytest.mark.asyncio
async def test_add_contact_rejects_duplicate_active() -> None:
    svc = _service([_contact(user_id="user-1", contact_user_id="user-2", status="active")])
    result = await svc.add_contact(_ctx(user_id="user-1"), contact_user_id="user-2")
    assert result["success"] is False
    assert "already exists" in result["error"]


@pytest.mark.asyncio
async def test_add_contact_reactivates_removed_contact() -> None:
    svc = _service([_contact(user_id="user-1", contact_user_id="user-2", status="removed")])
    result = await svc.add_contact(_ctx(user_id="user-1"), contact_user_id="user-2")
    assert result["success"] is True
    ctx = _ctx(user_id="user-1")
    await svc.add_contact(ctx, contact_user_id="user-3")  # another add to confirm repo state


@pytest.mark.asyncio
async def test_add_contact_requires_user_id() -> None:
    svc = _service()
    ctx = SimpleNamespace(user_id="", emit=AsyncMock())
    result = await svc.add_contact(ctx, contact_user_id="user-2")
    assert result["success"] is False


# ---------------------------------------------------------------------------
# remove_contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_contact_sets_status_removed() -> None:
    svc = _service([_contact(user_id="user-1", contact_user_id="user-2")])
    ctx = _ctx(user_id="user-1")
    result = await svc.remove_contact(ctx, contact_user_id="user-2")
    assert result["success"] is True
    ctx.emit.assert_awaited_once()
    event, _ = ctx.emit.call_args[0]
    assert event == "app.contacts.contact.removed"


@pytest.mark.asyncio
async def test_remove_contact_rejects_nonexistent() -> None:
    svc = _service()
    result = await svc.remove_contact(_ctx(), contact_user_id="user-2")
    assert result["success"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_remove_contact_rejects_already_removed() -> None:
    svc = _service([_contact(user_id="user-1", contact_user_id="user-2", status="removed")])
    result = await svc.remove_contact(_ctx(user_id="user-1"), contact_user_id="user-2")
    assert result["success"] is False
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# get_contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_contact_returns_true_when_active() -> None:
    svc = _service([_contact(user_id="user-1", contact_user_id="user-2")])
    result = await svc.get_contact(_ctx(user_id="user-1"), contact_user_id="user-2")
    assert result["is_contact"] is True
    assert result["contact"] is not None


@pytest.mark.asyncio
async def test_get_contact_returns_false_when_removed() -> None:
    svc = _service([_contact(user_id="user-1", contact_user_id="user-2", status="removed")])
    result = await svc.get_contact(_ctx(user_id="user-1"), contact_user_id="user-2")
    assert result["is_contact"] is False
    assert result["contact"] is None


@pytest.mark.asyncio
async def test_get_contact_returns_false_when_missing() -> None:
    svc = _service()
    result = await svc.get_contact(_ctx(), contact_user_id="user-99")
    assert result["is_contact"] is False


# ---------------------------------------------------------------------------
# list_contacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_contacts_returns_active_only() -> None:
    svc = _service([
        _contact(contact_user_id="user-2", status="active"),
        _contact(contact_user_id="user-3", status="removed"),
    ])
    result = await svc.list_contacts(_ctx())
    cids = [c["contact_user_id"] for c in result["contacts"]]
    assert "user-2" in cids
    assert "user-3" not in cids


@pytest.mark.asyncio
async def test_list_contacts_returns_next_cursor_when_more() -> None:
    docs = [
        _contact(contact_user_id=f"user-{i}", created_at=f"2026-01-0{i}T00:00:00+00:00")
        for i in range(2, 7)  # 5 docs
    ]
    svc = _service(docs)
    result = await svc.list_contacts(_ctx(), limit=3)
    assert len(result["contacts"]) == 3
    assert result["next_cursor"] is not None


@pytest.mark.asyncio
async def test_list_contacts_no_cursor_on_last_page() -> None:
    docs = [_contact(contact_user_id=f"user-{i}") for i in range(2, 4)]
    svc = _service(docs)
    result = await svc.list_contacts(_ctx(), limit=10)
    assert result["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_contacts_before_cursor_paginates() -> None:
    docs = [
        _contact(contact_user_id="user-2", created_at="2026-01-03T00:00:00+00:00"),
        _contact(contact_user_id="user-3", created_at="2026-01-02T00:00:00+00:00"),
        _contact(contact_user_id="user-4", created_at="2026-01-01T00:00:00+00:00"),
    ]
    svc = _service(docs)
    # First page gets user-2 (newest), cursor = 2026-01-03
    page1 = await svc.list_contacts(_ctx(), limit=1)
    assert page1["contacts"][0]["contact_user_id"] == "user-2"
    cursor = page1["next_cursor"]
    # Second page: before cursor, should get user-3
    page2 = await svc.list_contacts(_ctx(), limit=1, before=cursor)
    assert page2["contacts"][0]["contact_user_id"] == "user-3"


@pytest.mark.asyncio
async def test_list_contacts_empty_when_no_user_id() -> None:
    svc = _service([_contact()])
    ctx = SimpleNamespace(user_id="", emit=AsyncMock())
    result = await svc.list_contacts(ctx)
    assert result["contacts"] == []
    assert result["next_cursor"] is None
