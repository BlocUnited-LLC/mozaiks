from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.auth import UserPrincipal

ROOT = Path(__file__).resolve().parents[1]
BINDING = {
    "build_registry_id": "appreg_owned",
    "target_app_id": "notes-owned",
    "build_id": "build_owned",
    "phase": "genesis",
}


@pytest.fixture
def dashboard(monkeypatch):
    from mozaiksai.hosts import studio

    current = {
        "user": "builder",
        "roles": ["user"],
        "binding": dict(BINDING),
        "record_app": "notes-owned",
    }
    calls = []

    async def owned_binding(*, app_id, owner_user_id, chat_id):
        calls.append((app_id, owner_user_id, chat_id))
        if owner_user_id != "builder" or chat_id != "chat_owned":
            return None
        return current["binding"]

    async def owned_record(*, build_registry_id, owner_user_id):
        if build_registry_id != "appreg_owned" or owner_user_id != "builder":
            return {"app": None}
        return {"app": {"app_id": current["record_app"], "chat_app_id": "factory"}}

    async def settings_session(**kwargs):
        return current.get("settings_session")

    def principal():
        return UserPrincipal(
            user_id=current["user"],
            email="builder@example.test",
            name="Builder",
            roles=current["roles"],
            scopes=[],
            raw_claims={},
            app_id="factory",
            auth_provenance="token_validated",
        )

    monkeypatch.setattr(studio, "resolve_app_root", lambda: ROOT / "factory_app/app")
    monkeypatch.setattr(
        studio,
        "_get_app_registry_service",
        lambda: SimpleNamespace(
            repo=SimpleNamespace(get_owned_chat_binding=owned_binding, get_owned_settings_session=settings_session), get_app_record=owned_record
        ),
    )
    studio.app.dependency_overrides[studio.require_studio_user] = principal
    try:
        yield TestClient(studio.app), current, calls
    finally:
        studio.app.dependency_overrides.pop(studio.require_studio_user, None)


def test_owner_without_global_admin_uses_server_bound_dashboard(dashboard):
    client, _, calls = dashboard
    response = client.get("/api/studio/build/dashboard-link?chat_id=chat_owned&app_id=foreign-app")
    assert response.status_code == 200
    assert response.json() == {"path": "/apps/notes-owned/overview"}
    assert calls == [("factory", "builder", "chat_owned")]


def test_host_can_project_its_registration_id_only_after_ownership_checks(dashboard, monkeypatch):
    client, current, _ = dashboard
    seen = []

    async def resolve_app_id(*, host_app_id, owner_user_id, binding):
        seen.append((host_app_id, owner_user_id, binding.model_dump()))
        return "host-registration"

    monkeypatch.setattr(client.app.state, "resolve_studio_dashboard_app_id", resolve_app_id, raising=False)
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_owned&app_id=foreign").json() == {
        "path": "/apps/host-registration/overview"
    }
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_foreign").status_code == 404
    current["record_app"] = "mismatched"
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_owned").status_code == 404
    assert seen == [("factory", "builder", BINDING)]


@pytest.mark.parametrize("mapped_id", [None, "", "../private", "https://outside.example"])
def test_unmapped_host_dashboard_cannot_redirect_to_another_resource(dashboard, monkeypatch, mapped_id):
    client, _, _ = dashboard

    async def resolve_app_id(**_):
        return mapped_id

    monkeypatch.setattr(client.app.state, "resolve_studio_dashboard_app_id", resolve_app_id, raising=False)
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_owned").status_code == 404


def test_missing_context_and_first_build_use_portfolio(dashboard):
    client, current, _ = dashboard
    assert client.get("/api/studio/build/dashboard-link").json() == {"path": "/apps"}
    current["binding"] = {}
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_owned").json() == {
        "path": "/apps"
    }


@pytest.mark.parametrize(
    "user,roles", [("joined-member", ["user"]), ("foreign-owner", ["admin"]), ("other-builder", [])]
)
def test_members_and_foreign_global_admins_cannot_resolve_owned_build(dashboard, user, roles):
    client, current, _ = dashboard
    current.update(user=user, roles=roles)
    response = client.get("/api/studio/build/dashboard-link?chat_id=chat_owned")
    assert response.status_code == 404
    assert "notes-owned" not in response.text


@pytest.mark.parametrize(
    "binding",
    [None, {**BINDING, "target_app_id": "foreign-app"}, {**BINDING, "build_id": "../bad"}],
)
def test_missing_corrupt_and_mismatched_bindings_fail_closed(dashboard, binding):
    client, current, _ = dashboard
    current["binding"] = binding
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_owned").status_code == 404


def test_unowned_chat_and_invalid_selector_are_refused(dashboard):
    client, _, _ = dashboard
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_foreign").status_code == 404
    assert client.get("/api/studio/build/dashboard-link?chat_id=../invalid").status_code == 422


def test_builder_routes_keep_auth_without_global_admin_guard():
    import json

    routes = json.loads((ROOT / "factory_app/app/ui/route_manifest.json").read_text())["pages"]
    portfolio = next(route for route in routes if route["path"] == "/apps")
    assert "requiresRole" not in portfolio["meta"]
    for route in routes:
        if route.get("meta", {}).get("navigation", {}).get("group") == "app-studio":
            assert "requiresRole" not in route["meta"]
    # Workspace pages backed by admin-only data keep the admin guard.
    for path in ("/usage", "/integrations", "/support", "/settings/ai-models"):
        workspace_page = next(route for route in routes if route["path"] == path)
        assert workspace_page["meta"]["requiresRole"] == "admin"


def test_anonymous_cannot_resolve_dashboard_link():
    from mozaiksai.hosts import studio

    def anonymous():
        return UserPrincipal(
            user_id="anonymous",
            email=None,
            name=None,
            roles=[],
            scopes=[],
            raw_claims={},
            app_id="factory",
            auth_provenance="anonymous",
        )

    studio.app.dependency_overrides[studio.require_user_scope] = anonymous
    try:
        client = TestClient(studio.app)
        response = client.get("/api/studio/build/dashboard-link?chat_id=chat_owned")
        assert response.status_code == 403
    finally:
        studio.app.dependency_overrides.pop(studio.require_user_scope, None)


def test_host_bound_builder_can_read_shared_manifest_but_not_select_foreign_app(
    dashboard, monkeypatch
):
    from mozaiksai.hosts import studio

    client, _, _ = dashboard
    monkeypatch.setattr(studio.app.state, "loaded_app_id", "factory", raising=False)
    response = client.get("/api/studio/dashboard")
    assert response.status_code == 200
    assert response.json()["resolved"]["app_id"] is None
    assert client.get("/api/studio/dashboard?app_id=foreign-app").status_code == 403


@pytest.mark.parametrize("kind,suffix", [("branding", "branding"), ("subscription", "billing")])
def test_settings_return_uses_owned_host_mapping_and_finite_saved_kind(dashboard, monkeypatch, kind, suffix):
    client, current, _ = dashboard
    current["binding"] = {**BINDING, "phase": "refinement"}
    current["settings_session"] = {"trigger_meta": {"settings_selection": {
        "kind": kind, "dashboard_app_id": "stale-host-alias", "build_registry_id": "appreg_owned",
        "build_id": "selected-build", "app_bundle_id": "selected-bundle", "artifact_version_id": "selected-input",
    }}}
    async def mapped(**kwargs):
        return "host-registration"
    monkeypatch.setattr(client.app.state, "resolve_studio_dashboard_app_id", mapped, raising=False)
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_owned").json() == {"path": f"/apps/host-registration/{suffix}"}
    current["settings_session"]["trigger_meta"]["settings_selection"]["kind"] = "https://outside.invalid"
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_owned").json() == {"path": "/apps/host-registration/overview"}
    current["user"] = "other-owner"
    assert client.get("/api/studio/build/dashboard-link?chat_id=chat_owned").status_code == 404
