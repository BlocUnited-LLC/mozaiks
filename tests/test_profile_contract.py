from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest


class _Collection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def find_one(self, query, *_args, **_kwargs):
        doc = self.docs.get(query["_id"])
        return deepcopy(doc) if doc is not None else None

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
async def test_platform_profile_contract_uses_host_defaults_for_local_dev(monkeypatch):
    from mozaiksai.hosts import platform as platform_app
    from mozaiksai.core.auth.dependencies import UserPrincipal

    profiles = _Collection()

    async def _fake_profiles():
        return profiles

    monkeypatch.setattr(platform_app, "_account_profile_collection", _fake_profiles)

    principal = UserPrincipal(
        user_id="anonymous",
        email=None,
        name=None,
        roles=[],
        scopes=[],
        raw_claims={},
    )

    result = await platform_app.get_current_user_profile(app_id=None, principal=principal)

    assert result["app_id"] == platform_app._resolve_default_app_id()
    assert result["user_id"] == platform_app._DEFAULT_PROFILE_USER_ID
    assert result["username"] == platform_app._DEFAULT_PROFILE_USER_ID
    assert result["display_name"] == platform_app._DEFAULT_PROFILE_USER_ID


@pytest.mark.asyncio
async def test_platform_profile_contract_persists_display_name(monkeypatch):
    from mozaiksai.hosts import platform as platform_app
    from mozaiksai.core.auth.dependencies import UserPrincipal

    profiles = _Collection()

    async def _fake_profiles():
        return profiles

    monkeypatch.setattr(platform_app, "_account_profile_collection", _fake_profiles)

    principal = UserPrincipal(
        user_id="user_1",
        email="user@example.com",
        name="User Example",
        roles=["admin"],
        scopes=[],
        raw_claims={},
        app_id="app_1",
    )

    result = await platform_app.update_current_user_profile(
        body=platform_app.ProfileUpdateRequest(display_name="Builder"),
        app_id=None,
        principal=principal,
    )

    assert result["app_id"] == "app_1"
    assert result["user_id"] == "user_1"
    assert result["display_name"] == "Builder"
    assert result["roles"] == ["admin"]


@pytest.mark.asyncio
async def test_platform_profile_preferences_are_app_scoped(monkeypatch):
    from mozaiksai.hosts import platform as platform_app
    from mozaiksai.core.auth.dependencies import UserPrincipal

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
        body=platform_app.ProfilePreferencesUpdateRequest(settings={"theme": "dark", "density": "compact"}),
        app_id=None,
        principal=principal,
    )
    loaded = await platform_app.get_current_user_preferences(app_id=None, principal=principal)

    assert saved["app_id"] == "app_market"
    assert saved["user_id"] == "user_7"
    assert saved["settings"] == {"theme": "dark", "density": "compact"}
    assert loaded == saved


@pytest.mark.asyncio
async def test_platform_shell_config_injects_profile_route():
    from mozaiksai.hosts import platform as platform_app

    shell_config = await platform_app.build_shell_config(surface="studio")
    pages = {page.get("path"): page for page in shell_config.get("pages", [])}
    header_paths = {
        page.get("path")
        for page in (shell_config.get("header") or {}).get("pages", [])
        if isinstance(page, dict)
    }

    assert "/profile" in pages
    assert pages["/profile"]["component"] == "ProfilePage"
    assert pages["/profile"]["meta"]["requiresAuth"] is True
    assert "/profile" not in header_paths


def test_profile_page_edits_preferences_through_host_contract() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "chat-ui" / "src" / "pages" / "ProfilePage.jsx"
    ).read_text(encoding="utf-8")

    assert "/api/me/preferences" in source
    assert "method: 'PUT'" in source
    assert "Preferences must be valid JSON." in source
    assert "Host-owned account preferences scoped to this app." in source
    assert "api && typeof api.getHttpBaseUrl === 'function'" in source
    assert "VITE_API_URL" in source
