"""App staff see only declared entitled panels, with signed backend auth unchanged."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from mozaiksai.core.admin.router import build_admin_config, is_app_admin_path
from mozaiksai.core.auth import UserPrincipal
from mozaiksai.core.runtime.app.auth_contract import validate_app_auth_contract
from tests.test_admin_email_verified import _token  # noqa: F401
from tests.test_admin_email_verified import admin_client as admin_client

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "support-app"


@pytest.fixture
def support_portal(admin_client, monkeypatch):  # noqa: F811 - shared signed-auth fixture
    import os

    root = Path(os.environ["PLATFORM_PATH"])
    shutil.copytree(ROOT / "factory_app/build_context/support/templates/modules/support", root / "modules/support")
    pages = [
        {"id": "overview", "label": "Overview", "path": "/admin"},
        {"id": "support", "label": "Support", "path": "/admin/support"},
        {"id": "disabled", "label": "Disabled", "path": "/admin/disabled", "enabled": False},
        {"id": "studio", "label": "Studio", "path": "/apps/private", "scope": "workspace"},
    ]
    (root / "admin").mkdir()
    (root / "admin/admin_registry.yaml").write_text(yaml.safe_dump({"pages": pages}), encoding="utf-8")
    manifest = root / "modules/support/contracts/admin.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    for id, page, permission in [
        ("empty", "support", None), ("secret", "support", "records.secret"),
        ("disabled", "disabled", "support.manage"), ("studio", "studio", "support.manage"),
    ]:
        data["panels"].append({"id": id, "label": id, "page": page, "renderer": "custom_component",
                               "component": "HiddenPanel", "permissions": [permission] if permission else []})
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    auth = yaml.safe_load((ROOT / "factory_app/app/config/auth.yaml").read_text(encoding="utf-8"))
    auth["authorization"] = {"roles": [
        {"id": "support_team", "permissions": ["support.submit", "support.manage"]},
        {"id": "user", "permissions": ["support.submit"]},
    ]}
    app = admin_client.app
    app.state.loaded_app_id = APP_ID
    app.state.auth_contract = validate_app_auth_contract(auth)
    app.state.app_admin_emails = ()
    return admin_client, root


def headers(*, roles=None, scopes="", app_id=APP_ID):
    return {"Authorization": "Bearer " + _token(
        email="staff@example.test", email_verified=True, roles=roles or ["user"],
        app_id=app_id, scope=scopes, scp=scopes,
    )}


def test_config_projects_configured_support_team_without_admin_or_runtime_access(support_portal):
    client, _ = support_portal
    auth = headers(roles=["support_team"])
    response = client.get("/api/admin/config", headers=auth)
    assert response.status_code == 200, response.text
    config = response.json()
    assert config["is_admin"] is False
    assert config["runtime_panels"] == []
    assert config["entry_path"] == "/admin/support"
    assert [page["id"] for page in config["pages"]] == ["support"]
    assert [panel["id"] for panel in config["module_panels"]] == ["support-inbox"]
    for path in ["health", "stats", "runs", "usage"]:
        assert client.get("/api/admin/" + path, headers=auth).status_code == 403


def test_read_only_support_scope_is_an_explicit_alternative(support_portal):
    client, _ = support_portal
    response = client.get("/api/admin/config", headers=headers(scopes="support.submit support.read"))
    assert response.status_code == 200, response.text
    assert [panel["id"] for panel in response.json()["module_panels"]] == ["support-inbox"]


@pytest.mark.parametrize("scopes", ["", "support.submit", "support.manage", "support.read"])
def test_customer_and_incomplete_support_grants_do_not_discover_admin(support_portal, scopes):
    client, _ = support_portal
    response = client.get("/api/admin/config", headers=headers(roles=["unconfigured"], scopes=scopes))
    assert response.status_code == 403, response.text
    assert set(response.json()) == {"detail"}


def test_foreign_app_token_is_denied_even_with_support_permissions(support_portal):
    client, _ = support_portal
    response = client.get("/api/admin/config", headers=headers(roles=["support_team"], app_id="another-app"))
    assert response.status_code == 403, response.text


def test_only_declared_enabled_app_pages_can_admit_staff(support_portal):
    client, root = support_portal
    (root / "admin/admin_registry.yaml").write_text(yaml.safe_dump({"pages": [
        {"id": "support", "label": "Support", "path": "/admin/support", "enabled": False},
    ]}), encoding="utf-8")
    assert client.get("/api/admin/config", headers=headers(roles=["support_team"])).status_code == 403


def test_app_admin_keeps_the_existing_full_portal(support_portal):
    client, _ = support_portal
    owner = {"Authorization": "Bearer " + _token(email_verified=True, app_id=APP_ID)}
    response = client.get("/api/admin/config", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["is_admin"] is True
    assert response.json()["runtime_panels"]
    assert response.json()["entry_path"] == "/admin"
    assert client.get("/api/admin/health", headers=owner).status_code == 200


def test_unauthenticated_scope_strings_cannot_discover_the_portal(support_portal):
    client, _ = support_portal
    principal = UserPrincipal(user_id="visitor", email=None, name=None, roles=["support_team"],
                              scopes=["support.submit", "support.manage"], raw_claims={})
    with pytest.raises(HTTPException, match="Authentication required"):
        build_admin_config(principal, app=client.app)


@pytest.mark.parametrize("path", ["https://example.org/admin", "//example.org/admin", "/administrator", "/admin/..", "/admin/%2e%2e", "/admin/support?x=1", "/admin/support#x", "/apps/private"])
def test_admin_entry_rejects_external_or_ambiguous_paths(path):
    assert not is_app_admin_path(path)


def test_stock_support_manifest_permission_alternatives_match_generator_schema():
    from mozaiksai.core.runtime.app.module_loader import ModuleAdminManifest

    source = ROOT / "factory_app/build_context/support/templates/modules/support/contracts/admin.yaml"
    panel = ModuleAdminManifest.model_validate(yaml.safe_load(source.read_text(encoding="utf-8"))).panels[0]
    assert panel.permissions == ["support.submit"]
    assert panel.permissions_any == ["support.read", "support.manage"]
    schema = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    assert schema["models"]["ModuleAdminPanel"]["fields"]["permissions_any"]["items"] == "str"


def test_current_profile_projects_only_staff_entry_without_adding_admin_role(support_portal, monkeypatch):
    from mozaiksai.hosts import platform

    client, _ = support_portal

    async def profile(principal, *, app_id, user_id):
        return {"user_id": user_id, "app_id": "stored-app", "admin_entry": "/admin/forged",
                "tenant_id": "stored-tenant", "workspace_id": "stored-workspace", "scopes": ["unearned"]}

    monkeypatch.setattr(platform, "_ensure_account_profile", profile)
    client.app.add_api_route("/api/me", platform.get_current_user_profile, methods=["GET"])
    scoped_token = _token(email="staff@example.test", email_verified=True, roles=["support_team"],
                          app_id=APP_ID, tid="verified-tenant", workspace_id="verified-workspace",
                          scope="records.read", scp="records.read")
    response = client.get("/api/me", headers={"Authorization": "Bearer " + scoped_token})
    assert response.status_code == 200, response.text
    assert response.json()["admin_entry"] == "/admin/support"
    assert response.json()["roles"] == ["support_team"]
    assert response.json()["app_id"] == APP_ID
    assert response.json()["tenant_id"] == "verified-tenant"
    assert response.json()["workspace_id"] == "verified-workspace"
    assert response.json()["scopes"] == ["records.read"]
    customer = client.get("/api/me", headers=headers())
    assert customer.status_code == 200, customer.text
    assert customer.json()["admin_entry"] is None
    assert customer.json()["roles"] == ["user"]
