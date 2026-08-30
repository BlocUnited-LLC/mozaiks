"""Tests for /api/me/preferences persistence."""
from __future__ import annotations

# ruff: noqa: I001

from copy import deepcopy
from pathlib import Path

import pytest


class _Collection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def find_one(self, query, *_args, **_kwargs):
        if "_id" in query:
            doc = self.docs.get(query["_id"])
            return deepcopy(doc) if doc is not None else None
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                return deepcopy(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        doc_id = query["_id"]
        exists = doc_id in self.docs
        if not exists and not upsert:
            return None

        doc = deepcopy(self.docs.get(doc_id, {"_id": doc_id}))
        if not exists:
            doc.update(deepcopy(update.get("$setOnInsert", {})))
        doc.update(deepcopy(update.get("$set", {})))
        doc["_id"] = doc_id
        self.docs[doc_id] = doc
        return None


@pytest.mark.asyncio
async def test_profile_preferences_round_trip(monkeypatch):
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    preferences = _Collection()

    async def _fake_preferences():
        return preferences

    monkeypatch.setattr(platform_app, "_account_preferences_collection", _fake_preferences)

    principal = UserPrincipal(
        user_id="user_7",
        email="user7@example.com",
        name="User Seven",
        roles=["member"],
        scopes=[],
        raw_claims={},
        app_id="app_market",
    )

    saved = await platform_app.update_current_user_preferences(
        body=platform_app.ProfilePreferencesUpdateRequest(
            settings={"theme": "dark", "density": "compact"}
        ),
        app_id=None,
        principal=principal,
    )
    loaded = await platform_app.get_current_user_preferences(app_id=None, principal=principal)

    assert saved["app_id"] == "app_market"
    assert saved["user_id"] == "user_7"
    assert saved["settings"] == {"theme": "dark", "density": "compact"}
    assert loaded == saved


def test_profile_doc_id_uses_app_and_user():
    from mozaiksai.hosts.platform import _profile_doc_id

    assert _profile_doc_id("app-1", "user-1") == "app-1:user-1"


def test_profile_page_edits_preferences_through_host_contract() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "chat-ui" / "src" / "pages" / "ProfilePage.jsx"
    ).read_text(encoding="utf-8")

    assert "method: 'PUT'" in source
    assert "api && typeof api.getHttpBaseUrl === 'function'" in source
    assert "VITE_API_URL" in source
