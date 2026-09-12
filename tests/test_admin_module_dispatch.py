"""Tests for the admin module dispatch boundary (ADR-0001).

Covers /api/admin/modules/{module}/{action}:
- token-validated operator principals only (anonymous/dev personas 401,
  regardless of the role strings they carry)
- deny-by-default operator role allowlist (403 for non-operators)
- only admin_internal actions dispatch (internal/public/unknown 404)
- always enforce-mode authenticated_user authority (module.yaml permissions
  enforced; never trusted_bypass, including auth-disabled local mode)
- audited provenance surface + params digest, never raw params
- the public module route is unchanged (admin_internal stays 404 there) and
  fails closed when the surface map is missing
"""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.auth.dependencies import UserPrincipal, optional_user
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.hosts import platform as platform_host


class _BillingOpsHandler:
    async def settle(self, ctx, **params):
        return {"settled": True, "actor": ctx.user_id, "params": params}

    async def authority(self, ctx):
        authority = ctx.dispatch_authority
        provenance = ctx.dispatch_provenance
        return {
            "authority_kind": authority.kind if authority else None,
            "permission_mode": authority.permission_mode if authority else None,
            "permissions": list(authority.permissions) if authority else None,
            "surface": provenance.surface if provenance else None,
        }


_ACTIONS = {"settle": "settle", "authority": "authority"}
_PERMISSIONS = {"settle": ["billing.admin"], "authority": []}


def _principal(
    *,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
    provenance: str = "token_validated",
) -> UserPrincipal:
    return UserPrincipal(
        user_id="operator-1",
        email=None,
        name=None,
        roles=list(roles or []),
        scopes=list(scopes or []),
        raw_claims={},
        auth_provenance=provenance,
    )


def _client(
    *,
    action_surfaces: dict[str, dict[str, str | None]] | None,
    principal: UserPrincipal | None = None,
) -> TestClient:
    executor = ModuleExecutor()
    executor.register(
        "billing_ops",
        _BillingOpsHandler(),
        action_method_map=_ACTIONS,
        action_permissions=_PERMISSIONS,
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    platform_host.app.state.executor_registry = registry
    platform_host.app.state.failed_module_names = []
    if action_surfaces is None:
        if hasattr(platform_host.app.state, "module_action_surfaces"):
            delattr(platform_host.app.state, "module_action_surfaces")
    else:
        platform_host.app.state.module_action_surfaces = action_surfaces
    if principal is not None:
        platform_host.app.dependency_overrides[optional_user] = lambda: principal
    return TestClient(platform_host.app, raise_server_exceptions=False)


_ADMIN_SURFACES = {"billing_ops": {"settle": "admin_internal", "authority": "admin_internal"}}


_STATE_KEYS = ("executor_registry", "failed_module_names", "module_action_surfaces")
_ABSENT = object()


@pytest.fixture(autouse=True)
def _clean_platform_state():
    """Snapshot and restore the shared platform app singleton around each test
    — this file runs alphabetically early, and leaked executor/surface state
    breaks later suites that reuse platform_host.app."""
    saved = {
        key: getattr(platform_host.app.state, key, _ABSENT) for key in _STATE_KEYS
    }
    yield
    platform_host.app.dependency_overrides.pop(optional_user, None)
    for key, value in saved.items():
        if value is _ABSENT:
            if hasattr(platform_host.app.state, key):
                delattr(platform_host.app.state, key)
        else:
            setattr(platform_host.app.state, key, value)


# ---------------------------------------------------------------------------
# Principal gate
# ---------------------------------------------------------------------------


def test_admin_route_rejects_anonymous_principal_401() -> None:
    """Auth-disabled anonymous dispatch is refused: admin actions were never
    HTTP-dispatchable in local no-auth mode and remain that way."""
    client = _client(action_surfaces=_ADMIN_SURFACES)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 401


def test_admin_route_rejects_dev_persona_carrying_operator_role_401() -> None:
    """Role strings alone never authenticate: dev personas carry them freely."""
    persona = _principal(roles=["platform_operator"], provenance="dev_override")
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=persona)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 401


def test_admin_route_rejects_authenticated_non_operator_403() -> None:
    principal = _principal(roles=["member"], scopes=["billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=principal)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 403


def test_admin_route_operator_allowlist_is_env_configurable(monkeypatch) -> None:
    monkeypatch.setenv("MOZAIKS_ADMIN_DISPATCH_ROLES", "ops_admin")
    default_role = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=default_role)
    assert client.post("/api/admin/modules/billing_ops/settle", json={}).status_code == 403

    platform_host.app.dependency_overrides[optional_user] = lambda: _principal(
        roles=["ops_admin"], scopes=["billing.admin"]
    )
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Surface gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface", ["internal", "public", "public_readonly"])
def test_admin_route_404_for_non_admin_surfaces(surface: str) -> None:
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(
        action_surfaces={"billing_ops": {"settle": surface}},
        principal=operator,
    )
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 404


def test_admin_route_404_for_unknown_action() -> None:
    operator = _principal(roles=["platform_operator"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    resp = client.post("/api/admin/modules/billing_ops/nonexistent", json={})
    assert resp.status_code == 404


def test_admin_route_404_when_surface_map_missing() -> None:
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(action_surfaces=None, principal=operator)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Enforce-mode authority
# ---------------------------------------------------------------------------


def test_admin_route_enforces_module_permissions_403() -> None:
    """An operator without the action's declared permission is denied by
    ModuleExecutor — proving enforce mode, not trusted bypass."""
    operator = _principal(roles=["platform_operator"], scopes=["other.scope"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "PERMISSION_DENIED"


def test_admin_route_dispatches_for_operator_with_permission() -> None:
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    resp = client.post(
        "/api/admin/modules/billing_ops/settle",
        json={"params": {"period": "2026-09"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["settled"] is True
    assert body["actor"] == "operator-1"
    assert body["params"] == {"period": "2026-09"}


def test_admin_route_authority_is_authenticated_user_enforce() -> None:
    """The dispatch authority is never trusted_bypass on the admin lane —
    asserted from inside the handler via ctx.dispatch_authority."""
    operator = _principal(roles=["platform_operator"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    resp = client.post("/api/admin/modules/billing_ops/authority", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["authority_kind"] == "authenticated_user"
    assert body["permission_mode"] == "enforce"
    assert body["surface"] == "http_admin_module_dispatch"


def test_admin_route_is_post_only() -> None:
    """GET would put admin params raw into URL/proxy logs, defeating the
    digest-only audit design."""
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    resp = client.get("/api/admin/modules/billing_ops/authority")
    assert resp.status_code == 405


def test_admin_route_rejects_query_param_token() -> None:
    """The access_token query fallback is a WebSocket affordance; operator
    tokens must never ride in URLs."""
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    resp = client.post(
        "/api/admin/modules/billing_ops/settle?access_token=abc", json={}
    )
    assert resp.status_code == 401


def test_admin_route_scopes_never_satisfy_operator_gate() -> None:
    """OAuth scopes are client-requestable at mint time; only ROLES gate."""
    scoped = _principal(roles=[], scopes=["platform_operator", "billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=scoped)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 403


def test_gate_denials_are_audited(monkeypatch) -> None:
    """Probing the operator boundary must be visible in the audit trail."""
    from mozaiksai.core.audit import audit_logger as audit_logger_module

    recorded: list[dict] = []

    class _Recorder:
        async def log_admin_access(self, **kw):
            recorded.append(kw)

    monkeypatch.setattr(audit_logger_module, "get_audit_logger", lambda: _Recorder())
    monkeypatch.setattr(
        "mozaiksai.hosts.routers.admin_modules.get_audit_logger", lambda: _Recorder()
    )
    non_operator = _principal(roles=["member"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=non_operator)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={})
    assert resp.status_code == 403
    assert recorded and recorded[-1]["outcome"] == "denied_not_operator"
    assert recorded[-1]["actor_id"] == "operator-1"


def test_scope_hook_crash_fails_closed_on_admin_lane() -> None:
    """A crashed narrowing hook must deny privileged dispatch, not widen it."""
    from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks

    def _crashing_hook(**kw):
        raise RuntimeError("narrowing hook exploded")

    hooks = get_platform_hooks()
    hooks._module_scope_resolver_hooks.append(_crashing_hook)
    try:
        operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
        client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
        resp = client.post("/api/admin/modules/billing_ops/settle", json={})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Operator scope resolution failed"
    finally:
        hooks._module_scope_resolver_hooks.remove(_crashing_hook)


def test_api_surface_vocabulary_is_closed() -> None:
    """A misspelled surface must fail module load, not silently dispatch as an
    ordinary authenticated action on the public route."""
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ActionDef

    def _action(surface: str) -> dict:
        return {
            "id": "probe",
            "description": "probe",
            "handler_method": "probe",
            "api_surface": surface,
        }

    ActionDef.model_validate(_action("admin_internal"))
    with pytest.raises(ValidationError):
        ActionDef.model_validate(_action("Admin_Internal"))
    with pytest.raises(ValidationError):
        ActionDef.model_validate(_action("semi_public"))


def test_admin_route_audit_carries_surface_and_params_digest(monkeypatch) -> None:
    captured: list = []
    original = ModuleExecutor._build_dispatch_audit

    def _capture(self, request, authority, provenance, permission_check, entitlement_check, **kw):
        audit = original(self, request, authority, provenance, permission_check, entitlement_check, **kw)
        captured.append(audit)
        return audit

    monkeypatch.setattr(ModuleExecutor, "_build_dispatch_audit", _capture)
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    params = {"period": "2026-09"}
    resp = client.post("/api/admin/modules/billing_ops/settle", json={"params": params})
    assert resp.status_code == 200
    assert captured, "dispatch audit was not built"
    audit = captured[-1]
    assert audit.audit_tags.get("surface") == "http_admin_module_dispatch"
    expected = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert audit.audit_tags.get("params_digest") == expected
    serialized = json.dumps(audit.to_dict(), default=str)
    assert "2026-09" not in serialized, "raw params leaked into the audit record"


def test_policy_denial_preserves_provenance_audit_tags(monkeypatch) -> None:
    """Policy tags merge with — never replace — the surface/digest tags."""
    from mozaiksai.core.runtime.composition import module_executor as me
    from mozaiksai.core.runtime.composition.module_authority import (
        ModuleExecutionPolicyDecision,
    )

    emitted: list = []
    original_emit = me.ModuleExecutor._emit_dispatch_audit

    async def _capture(self, audit, *, error=None):
        emitted.append(audit)

    monkeypatch.setattr(me.ModuleExecutor, "_emit_dispatch_audit", _capture)

    class _DenyHooks:
        async def call_before_module_execution(self, policy_input):
            return ModuleExecutionPolicyDecision(
                allowed=False, reason="policy says no", audit_tags={"policy": "x"}
            )

    monkeypatch.setattr(me, "get_platform_hooks", lambda: _DenyHooks())
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={"params": {"a": 1}})
    assert resp.status_code == 403
    assert emitted, "policy denial emitted no audit"
    tags = emitted[-1].audit_tags
    assert tags.get("policy") == "x"
    assert tags.get("surface") == "http_admin_module_dispatch"
    assert tags.get("params_digest")
    assert original_emit is not None


def test_validation_failure_paths_are_audited(monkeypatch) -> None:
    """Previously-unaudited terminal paths now emit exactly one audit —
    representative case: input-schema rejection."""
    from mozaiksai.core.runtime.composition import module_executor as me

    emitted: list = []

    async def _capture(self, audit, *, error=None):
        emitted.append((audit, error))

    monkeypatch.setattr(me.ModuleExecutor, "_emit_dispatch_audit", _capture)
    executor = me.ModuleExecutor()
    executor.register(
        "billing_ops",
        _BillingOpsHandler(),
        action_method_map=_ACTIONS,
        action_permissions=_PERMISSIONS,
        action_schemas={
            "settle": {"input": {"type": "object", "required": ["period"], "properties": {"period": {"type": "string"}}}}
        },
    )
    from mozaiksai.core.runtime.composition.executor_registry import (
        ExecutorRegistry as _Reg,
    )

    registry = _Reg()
    registry.register(executor)
    platform_host.app.state.executor_registry = registry
    platform_host.app.state.failed_module_names = []
    platform_host.app.state.module_action_surfaces = _ADMIN_SURFACES
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    platform_host.app.dependency_overrides[optional_user] = lambda: operator
    client = TestClient(platform_host.app, raise_server_exceptions=False)
    resp = client.post("/api/admin/modules/billing_ops/settle", json={"params": {}})
    assert resp.status_code == 400
    assert emitted, "input-invalid path emitted no audit"
    audit, error = emitted[-1]
    assert error == "INVALID_PARAMS"
    assert audit.outcome == "failed"
    assert audit.audit_tags.get("surface") == "http_admin_module_dispatch"


# ---------------------------------------------------------------------------
# The public route is unchanged — and now fails closed
# ---------------------------------------------------------------------------


def test_public_route_still_404_for_admin_internal_even_for_operator() -> None:
    """The boundary must not weaken the public module route (ADR-0001)."""
    operator = _principal(roles=["platform_operator"], scopes=["billing.admin"])
    client = _client(action_surfaces=_ADMIN_SURFACES, principal=operator)
    resp = client.post("/api/modules/billing_ops/settle", json={})
    assert resp.status_code == 404


def test_public_route_fails_closed_when_surface_map_missing() -> None:
    """A host that mounts the modules router without platform assembly has no
    surface truth; dispatch must refuse rather than fail open."""
    client = _client(action_surfaces=None)
    resp = client.get("/api/modules/billing_ops/settle")
    assert resp.status_code == 404
