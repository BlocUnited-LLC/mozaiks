"""Analytics uses Factory targets for measurement and owner-verified IDs for Studio links."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.auth import UserPrincipal


@pytest.fixture
def portal(monkeypatch):
    from mozaiksai.hosts import studio

    state = {"user": "alice", "host": "studio"}
    record = {"owner_user_id": "alice", "chat_app_id": "studio", "app_id": "notes",
              "build_registry_id": "reg-notes", "name": "Notes"}
    binding = {"dashboard_app_id": "hosted-notes", "target_app_id": "notes", "build_registry_id": "reg-notes"}

    async def get_record(*, owner_user_id, app_id=None, build_registry_id=None):
        owned = owner_user_id == record["owner_user_id"]
        matches = (app_id == record["app_id"] or build_registry_id == record["build_registry_id"])
        return {"app": record if owned and matches else None}

    async def get_bindings(*, host_app_id, owner_user_id, dashboard_app_id=None, build_registry_ids=None):
        selected = dashboard_app_id == "hosted-notes" or (build_registry_ids and "reg-notes" in build_registry_ids)
        return [binding] if host_app_id == "studio" and owner_user_id == "alice" and selected else []

    async def list_apps(*, owner_user_id):
        if owner_user_id != "alice":
            return {"apps": []}
        return {"apps": [record, {**record, "app_id": "old-app", "build_registry_id": "old-reg"}]}

    async def portfolio(apps, window):
        return {"apps": apps}

    def principal():
        return UserPrincipal(user_id=state["user"], roles=["user"], app_id=state["host"],
                             email=None, name=None, scopes=[], raw_claims={}, auth_provenance="token_validated")

    registry = SimpleNamespace(get_app_record=get_record, list_apps=list_apps)
    analytics = SimpleNamespace(app_analytics=AsyncMock(return_value={"metrics": {}}),
                                metric_detail=AsyncMock(return_value={"value": {}}), portfolio=portfolio)
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: registry)
    monkeypatch.setattr(studio, "_get_owner_analytics_service", lambda: analytics)
    monkeypatch.setattr(studio.app.state, "loaded_app_id", "studio", raising=False)
    monkeypatch.setattr(studio.app.state, "list_studio_app_bindings", get_bindings, raising=False)
    monkeypatch.setitem(studio.app.dependency_overrides, studio.require_studio_user, principal)
    return SimpleNamespace(client=TestClient(studio.app), state=state, binding=binding, record=record,
                           analytics=analytics, app=studio.app)


@pytest.mark.parametrize("endpoint,method", [("", "app_analytics"), ("/metrics/mrr", "metric_detail")])
def test_dashboard_and_native_analytics_read_the_same_owned_target(portal, endpoint, method):
    p = portal
    url = "/api/studio/analytics/apps/"
    assert p.client.get(url + "hosted-notes" + endpoint, params={"reference": "dashboard"}).status_code == 200
    assert p.client.get(url + "notes" + endpoint).status_code == 200
    calls = getattr(p.analytics, method).await_args_list
    assert [call.args[0]["app_id"] if method == "app_analytics" else call.kwargs["app"]["app_id"]
            for call in calls] == ["notes", "notes"]
    assert p.client.get(url + "notes" + endpoint, params={"reference": "dashboard"}).status_code == 404
    p.state["user"] = "bob"
    assert p.client.get(url + "hosted-notes" + endpoint, params={"reference": "dashboard"}).status_code == 404
    assert p.client.get(url + "notes" + endpoint).status_code == 404
    assert getattr(p.analytics, method).await_count == 2


def test_portfolio_keeps_metrics_but_only_links_verified_host_bindings(portal):
    p = portal
    response = p.client.get("/api/studio/analytics/portfolio")
    assert response.status_code == 200
    assert [(r["app_id"], r["dashboard_app_id"]) for r in response.json()["apps"]] == [
        ("notes", "hosted-notes"), ("old-app", None),
    ]
    p.state["user"] = "bob"
    assert p.client.get("/api/studio/analytics/portfolio").json()["apps"] == []


@pytest.mark.parametrize("field,value", [("build_registry_id", "wrong"), ("target_app_id", "wrong"),
                                         ("dashboard_app_id", "other"), ("target_app_id", "../bad")])
def test_disagreement_between_registries_fails_closed(portal, field, value):
    p = portal
    p.binding[field] = value
    assert p.client.get("/api/studio/analytics/apps/hosted-notes?reference=dashboard").status_code == 404
    p.analytics.app_analytics.assert_not_awaited()


def test_foreign_host_is_denied_even_for_same_subject(portal):
    p = portal
    p.state["host"] = "foreign"
    assert p.client.get("/api/studio/analytics/apps/hosted-notes?reference=dashboard").status_code == 403
    p.state["host"] = "studio"
    p.record["chat_app_id"] = "foreign"
    assert p.client.get("/api/studio/analytics/apps/hosted-notes?reference=dashboard").status_code == 404
    p.analytics.app_analytics.assert_not_awaited()


def test_stock_saved_app_needs_no_host_projection(portal, monkeypatch):
    p = portal
    monkeypatch.delattr(p.app.state, "list_studio_app_bindings")
    assert p.client.get("/api/studio/analytics/apps/notes?reference=dashboard").status_code == 200
    assert p.client.get("/api/studio/analytics/portfolio").json()["apps"][0]["dashboard_app_id"] == "notes"


def test_outbound_only_host_cannot_treat_a_route_as_a_factory_target(portal, monkeypatch):
    p = portal
    monkeypatch.delattr(p.app.state, "list_studio_app_bindings")
    monkeypatch.setattr(p.app.state, "resolve_studio_dashboard_app_id", AsyncMock(), raising=False)
    assert p.client.get("/api/studio/analytics/apps/notes?reference=dashboard").status_code == 404
    assert p.client.get("/api/studio/analytics/portfolio").json()["apps"][0]["dashboard_app_id"] is None
    assert p.client.get("/api/studio/analytics/apps/notes").status_code == 200


def test_ambiguous_registration_withholds_the_link_and_denies_selection(portal, monkeypatch):
    p = portal
    monkeypatch.setattr(p.app.state, "list_studio_app_bindings", AsyncMock(return_value=[p.binding, p.binding]))
    assert p.client.get("/api/studio/analytics/apps/hosted-notes?reference=dashboard").status_code == 404
    assert p.client.get("/api/studio/analytics/portfolio").json()["apps"][0]["dashboard_app_id"] is None
