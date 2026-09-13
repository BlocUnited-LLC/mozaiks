"""Studio ask_context hook summarizes the owner's app registry."""

from __future__ import annotations

import pytest

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.platform.ask_context import studio_ask_context


@pytest.mark.asyncio
async def test_studio_ask_context_summarizes_apps(monkeypatch):
    records = [
        {"name": "Lending Circle", "lifecycle_state": "draft", "app_id": "app-lend"},
        {"name": "Recipe Box", "lifecycle_state": "draft", "app_id": "app-recipes"},
        {"name": None, "lifecycle_state": "live", "app_id": "app-anon"},
    ]

    async def fake_list_apps(self, *, owner_user_id: str):
        assert owner_user_id == "user_1"
        return {"apps": records}

    monkeypatch.setattr(AppRegistryService, "list_apps", fake_list_apps)

    context = await studio_ask_context(app_id="app_1", user_id="user_1")

    assert context["Workspace apps"] == "3 total — 2 draft, 1 live"
    assert context["Most recently updated apps"] == (
        "Lending Circle (draft); Recipe Box (draft); app-anon (live)"
    )


@pytest.mark.asyncio
async def test_studio_ask_context_reports_empty_workspace(monkeypatch):
    async def fake_list_apps(self, *, owner_user_id: str):
        return {"apps": []}

    monkeypatch.setattr(AppRegistryService, "list_apps", fake_list_apps)

    context = await studio_ask_context(app_id="app_1", user_id="user_1")

    assert context == {"Workspace apps": "none created yet"}


@pytest.mark.asyncio
async def test_studio_ask_context_reports_in_flight_build(monkeypatch):
    """Studio build sessions are target-scoped, so the runtime's default-scope
    session snapshot never sees them. The registry record is what tells the ask
    agent which build the user is actually working in."""
    records = [
        {
            "name": "Lending Circle",
            "lifecycle_state": "building",
            "app_id": "draft-build-25ae77cae2ef",
            "active_workflow_id": "ValueEngine",
            "active_chat_id": "chat_build_1",
        },
        {"name": "Recipe Box", "lifecycle_state": "draft", "app_id": "app-recipes"},
    ]

    async def fake_list_apps(self, *, owner_user_id: str):
        return {"apps": records}

    monkeypatch.setattr(AppRegistryService, "list_apps", fake_list_apps)

    context = await studio_ask_context(app_id="app_1", user_id="user_1")

    assert context["Build in progress"] == "Lending Circle — ValueEngine (building)"


@pytest.mark.asyncio
async def test_studio_ask_context_omits_build_line_when_nothing_in_flight(monkeypatch):
    records = [
        {"name": "Archived App", "lifecycle_state": "archived", "app_id": "app-old",
         "active_workflow_id": "ValueEngine"},
        {"name": "Live App", "lifecycle_state": "active", "app_id": "app-live",
         "active_workflow_id": "AppGenerator"},
    ]

    async def fake_list_apps(self, *, owner_user_id: str):
        return {"apps": records}

    monkeypatch.setattr(AppRegistryService, "list_apps", fake_list_apps)

    context = await studio_ask_context(app_id="app_1", user_id="user_1")

    assert "Build in progress" not in context
    assert context["Workspace apps"] == "2 total — 1 active, 1 archived"
