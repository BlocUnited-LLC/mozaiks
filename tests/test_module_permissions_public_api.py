"""Public permission mapping has identical HTTP and socket semantics."""

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from mozaiksai.core.auth import UserPrincipal, WebSocketUser
from mozaiksai.core.runtime import app as app_api
from mozaiksai.core.runtime.app import resolve_module_permissions, validate_app_auth_contract


@pytest.fixture
def loaded_app():
    document = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "factory_app/app/config/auth.yaml").read_text(encoding="utf-8"),
    )
    document["authorization"] = {"roles": [
        {"id": "reader", "permissions": ["records.read", "records.list"]},
        {"id": "admin", "permissions": ["records.manage"]},
    ]}
    return SimpleNamespace(state=SimpleNamespace(
        auth_contract=validate_app_auth_contract(document), app_admin_emails=("owner@example.test",),
    ))


def test_public_export_is_the_canonical_resolver():
    from mozaiksai.core.runtime.app.module_permissions import (
        resolve_module_permissions as canonical,
    )

    assert "resolve_module_permissions" in app_api.__all__
    assert resolve_module_permissions is canonical


@pytest.mark.parametrize("changes,expected", [
    ({"roles": ["reader"]}, ["records.read", "records.list"]),
    ({"roles": ["unknown"]}, ["records.read"]),
    ({"roles": ["reader"], "auth_provenance": "anonymous"}, ["records.read"]),
    ({"email": "owner@example.test", "email_verified": False}, ["records.read"]),
    ({"email": "owner@example.test", "email_verified": True}, ["records.read", "records.manage"]),
    ({"email": "other@example.test", "email_verified": True}, ["records.read"]),
    ({"roles": ["reader"], "auth_provenance": "local_development"}, ["records.read", "records.list"]),
])
def test_http_and_socket_grants_match_without_mutating_identity(loaded_app, changes, expected):
    for principal_type in (UserPrincipal, WebSocketUser):
        principal = principal_type(**{
            "user_id": "viewer", "email": None, "name": None, "roles": [],
            "scopes": ["records.read", "records.read"], "raw_claims": {},
            "auth_provenance": "token_validated", "provider": "test", **changes,
        })
        before = asdict(principal)
        assert resolve_module_permissions(principal, app=loaded_app) == expected
        assert asdict(principal) == before
        assert principal.app_id is principal.tenant_id is principal.workspace_id is None


@pytest.mark.parametrize("principal_type", [UserPrincipal, WebSocketUser])
def test_loaded_app_grants_do_not_leak_to_another_app(loaded_app, principal_type):
    principal = principal_type(
        user_id="viewer", email="owner@example.test", name=None, roles=["reader"],
        scopes=["records.read"], raw_claims={}, auth_provenance="token_validated", email_verified=True,
    )
    assert resolve_module_permissions(principal, app=loaded_app) == ["records.read", "records.list", "records.manage"]
    other_app = SimpleNamespace(state=SimpleNamespace())
    assert resolve_module_permissions(principal, app=other_app) == ["records.read"]
    assert resolve_module_permissions(None, app=loaded_app) == []
    claimant = SimpleNamespace(scopes=["records.read"], roles=["admin"], is_authenticated=True)
    assert resolve_module_permissions(claimant, app=loaded_app) == ["records.read"]
