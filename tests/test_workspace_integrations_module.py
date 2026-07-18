"""Tests for the workspace_integrations module — policy, service, and catalog shape."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from factory_app.app.modules.workspace_integrations.backend.handler import (
    WorkspaceIntegrationsModule,
)
from factory_app.app.modules.workspace_integrations.backend.policy import (
    derive_status,
    is_catalog_only_mode,
)
from factory_app.app.modules.workspace_integrations.backend.repo import WorkspaceIntegrationsRepo
from factory_app.app.modules.workspace_integrations.backend.schemas import (
    CATALOG_BY_ID,
    DECLARATIONS_ENTITY,
    INTEGRATIONS_CATALOG,
    build_declaration_document,
    build_declaration_response,
    build_integration_response,
)
from factory_app.app.modules.workspace_integrations.backend.service import (
    WorkspaceIntegrationsService,
)


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


# ── catalog shape ─────────────────────────────────────────────────────────────

def test_integrations_catalog_has_required_fields() -> None:
    for entry in INTEGRATIONS_CATALOG:
        assert "id" in entry, f"missing id: {entry}"
        assert "name" in entry
        assert "category" in entry
        assert "required_secrets" in entry
        assert isinstance(entry["required_secrets"], list)
        assert "optional_secrets" in entry


def test_catalog_by_id_matches_catalog_list() -> None:
    for entry in INTEGRATIONS_CATALOG:
        assert entry["id"] in CATALOG_BY_ID
        assert CATALOG_BY_ID[entry["id"]] is entry


def test_catalog_covers_expected_integrations() -> None:
    ids = set(CATALOG_BY_ID)
    # Core integrations that the catalog must include
    assert "mozaikspay" in ids
    assert "payment_provider" not in ids
    assert "openai" in ids
    assert "resend" in ids
    assert "twilio" in ids
    assert "github" in ids


def test_build_context_catalog_matches_runtime_catalog() -> None:
    catalog_path = _workspace() / "factory_app" / "build_context" / "integrations" / "catalog.yaml"
    payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    yaml_entries = payload["integrations"]
    yaml_ids = {entry["id"] for entry in yaml_entries}

    assert yaml_ids == set(CATALOG_BY_ID)
    assert "mozaikspay" in yaml_ids
    assert "payment_provider" not in yaml_ids

    yaml_mozaikspay = next(entry for entry in yaml_entries if entry["id"] == "mozaikspay")
    runtime_mozaikspay = CATALOG_BY_ID["mozaikspay"]
    assert yaml_mozaikspay["required_secrets"] == runtime_mozaikspay["required_secrets"]
    assert yaml_mozaikspay["optional_secrets"] == runtime_mozaikspay["optional_secrets"]
    assert [entry["id"] for entry in yaml_entries] == [entry["id"] for entry in INTEGRATIONS_CATALOG]


def test_integrations_catalog_orders_payments_after_ai() -> None:
    assert [entry["id"] for entry in INTEGRATIONS_CATALOG[:3]] == [
        "openai",
        "anthropic",
        "mozaikspay",
    ]
    assert [entry["category"] for entry in INTEGRATIONS_CATALOG[:3]] == ["ai", "ai", "payments"]


def test_mozaikspay_catalog_is_default_removable_monetization_integration() -> None:
    spec = CATALOG_BY_ID["mozaikspay"]
    assert spec["category"] == "payments"
    assert spec["default_for"] == ["monetized_app"]
    assert spec["removable_default"] is True
    assert set(spec["required_secrets"]) == {
        "MOZAIKSPAY_API_BASE",
        "MOZAIKSPAY_API_KEY",
    }


# ── module.yaml contract ──────────────────────────────────────────────────────

def test_workspace_integrations_module_yaml_contract() -> None:
    module_root = _workspace() / "factory_app" / "app" / "modules" / "workspace_integrations"
    manifest = yaml.safe_load((module_root / "module.yaml").read_text(encoding="utf-8"))
    action_ids = {a["id"] for a in manifest["actions"]}

    assert manifest["module"]["id"] == "workspace_integrations"
    assert manifest["module"]["handler"] == "backend.handler:WorkspaceIntegrationsModule"
    assert {
        "list_integrations",
        "get_integration",
        "set_integration_note",
        "declare_app_integration_needs",
        "list_app_integration_needs",
        "upsert_app_integration_need",
        "delete_app_integration_need",
        "save_workspace_connector",
        "list_workspace_connectors",
        "delete_workspace_connector",
    } == action_ids
    assert (module_root / "contracts" / "events.yaml").exists()
    assert (module_root / "backend" / "handler.py").exists()
    assert (module_root / "backend" / "service.py").exists()
    assert (module_root / "backend" / "repo.py").exists()
    assert (module_root / "backend" / "policy.py").exists()
    assert (module_root / "backend" / "schemas.py").exists()

    by_id = {a["id"]: a for a in manifest["actions"]}
    assert "workspace_id" not in set(by_id["list_workspace_connectors"]["input_schema"].get("required") or [])
    assert "workspace_id" not in set(by_id["delete_workspace_connector"]["input_schema"].get("required") or [])
    assert "workspace_id" not in set(by_id["save_workspace_connector"]["input_schema"].get("required") or [])


# ── policy: derive_status ─────────────────────────────────────────────────────

def test_derive_status_no_required_secrets_returns_configured() -> None:
    status, missing = derive_status([])
    assert status == "configured"
    assert missing == []


def test_derive_status_all_secrets_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKSPAY_API_BASE", "https://pay.mozaiks.test")
    status, missing = derive_status(["MOZAIKSPAY_API_BASE"])
    assert status == "configured"
    assert missing == []


def test_derive_status_all_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    status, missing = derive_status(["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"])
    assert status == "missing"
    assert set(missing) == {"TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"}


def test_derive_status_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACabc")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    status, missing = derive_status(["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"])
    assert status == "partial"
    assert missing == ["TWILIO_AUTH_TOKEN"]


def test_derive_status_catalog_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_INTEGRATIONS_REGISTRY_MODE", "catalog_only")
    monkeypatch.delenv("MOZAIKSPAY_API_BASE", raising=False)
    status, missing = derive_status(["MOZAIKSPAY_API_BASE"])
    assert status == "unknown"
    assert missing == []


def test_is_catalog_only_mode_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOZAIKS_INTEGRATIONS_REGISTRY_MODE", raising=False)
    assert is_catalog_only_mode() is False


def test_is_catalog_only_mode_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_INTEGRATIONS_REGISTRY_MODE", "catalog_only")
    assert is_catalog_only_mode() is True


# ── build_integration_response ────────────────────────────────────────────────

def test_build_integration_response_never_exposes_secret_values() -> None:
    spec = CATALOG_BY_ID["mozaikspay"]
    response = build_integration_response(
        spec,
        status="partial",
        missing_secrets=["MOZAIKSPAY_API_KEY"],
    )
    assert response["id"] == "mozaikspay"
    assert response["status"] == "partial"
    # secrets list contains presence booleans, not values
    secrets = response.get("secrets", [])
    assert len(secrets) > 0
    for s in secrets:
        assert "value" not in s
        assert "present" in s
    # the missing secret is marked not present; the other is present
    missing_entry = next(s for s in secrets if s["name"] == "MOZAIKSPAY_API_KEY")
    assert missing_entry["present"] is False
    present_entry = next(s for s in secrets if s["name"] == "MOZAIKSPAY_API_BASE")
    assert present_entry["present"] is True


def test_build_integration_response_note_included() -> None:
    spec = CATALOG_BY_ID["openai"]
    response = build_integration_response(spec, status="configured", missing_secrets=[], note="rotated 2026-06")
    assert response["note"] == "rotated 2026-06"


def test_build_integration_response_missing_has_setup_url() -> None:
    spec = CATALOG_BY_ID["resend"]
    response = build_integration_response(spec, status="missing", missing_secrets=["RESEND_API_KEY"])
    assert "setup_url" in response
    assert "resend" in response["setup_url"]


# ── service ───────────────────────────────────────────────────────────────────

class _FakeRepo:
    def __init__(self, notes: list[dict[str, Any]] | None = None) -> None:
        self._notes: list[dict[str, Any]] = notes or []
        self.upserted: dict[str, Any] | None = None

    async def get_all_notes(self, ctx: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        return self._notes

    async def get_note(self, ctx: Any, integration_id: str) -> dict[str, Any] | None:  # noqa: ANN401
        for n in self._notes:
            if n.get("integration_id") == integration_id:
                return n
        return None

    async def upsert_note(self, ctx: Any, **kwargs: Any) -> None:  # noqa: ANN401
        self.upserted = kwargs


class _FakeUsageDeclarationsRepo:
    def __init__(self, usage_counts: dict[str, int] | None = None) -> None:
        self.usage_counts = usage_counts or {}
        self.requested_catalog_ids: list[str] | None = None

    async def get_catalog_usage_counts(self, *, catalog_ids: list[str] | None = None) -> dict[str, int]:
        self.requested_catalog_ids = catalog_ids
        if not catalog_ids:
            return self.usage_counts
        return {key: value for key, value in self.usage_counts.items() if key in catalog_ids}


class _FakeCtx:
    user_id = "user_1"
    tenant_id = None
    workspace_id = None


class _FakeConnectorActionService:
    def __init__(self) -> None:
        self.workspace_ids: list[str] = []

    async def list_workspace_connectors(self, *, workspace_id: str) -> dict[str, Any]:
        self.workspace_ids.append(workspace_id)
        return {"connectors": [], "total": 0}

    async def delete_workspace_connector(self, *, workspace_id: str, service: str) -> dict[str, Any]:
        self.workspace_ids.append(workspace_id)
        return {"deleted": True, "service": service, "secret_deleted": False}


@pytest.mark.asyncio
async def test_handler_workspace_connector_actions_default_to_context_workspace() -> None:
    ctx = _FakeCtx()
    ctx.workspace_id = "workspace_123"
    service = _FakeConnectorActionService()
    module = WorkspaceIntegrationsModule(service=service)  # type: ignore[arg-type]

    await module.list_workspace_connectors(ctx)
    await module.delete_workspace_connector(ctx, service="openai")

    assert service.workspace_ids == ["workspace_123", "workspace_123"]


class _WrapperStyleCollection:
    def __init__(self) -> None:
        self.find_many_calls: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return {"integration_id": query.get("integration_id"), "note": "saved"}

    async def find_many(self, query: dict[str, Any], *, limit: int = 50, sort=None, projection=None) -> list[dict[str, Any]]:
        self.find_many_calls.append({"query": query, "limit": limit, "sort": sort, "projection": projection})
        return [
            {"integration_id": "openai", "note": "shared platform key"},
            {"integration_id": "mozaikspay", "note": "production account"},
        ]

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False) -> None:
        self.update_call = {"query": query, "update": update, "upsert": upsert}


class _WrapperStylePersistence:
    def __init__(self) -> None:
        self.collection_handle = _WrapperStyleCollection()
        self.collection_calls: list[tuple[str, str]] = []

    def collection(self, module_id: str, entity_name: str) -> _WrapperStyleCollection:
        self.collection_calls.append((module_id, entity_name))
        return self.collection_handle


class _WrapperStyleCtx:
    def __init__(self) -> None:
        self.persistence = _WrapperStylePersistence()


@pytest.mark.asyncio
async def test_service_list_integrations_returns_all_catalog_entries() -> None:
    service = WorkspaceIntegrationsService(repo=_FakeRepo())
    result = await service.list_integrations(_FakeCtx())
    assert len(result["integrations"]) == len(INTEGRATIONS_CATALOG)
    summary = result["summary"]
    assert summary["total"] == len(INTEGRATIONS_CATALOG)
    assert summary["configured"] + summary["partial"] + summary["missing"] == summary["total"]


@pytest.mark.asyncio
async def test_service_list_integrations_includes_app_usage_count() -> None:
    declarations_repo = _FakeUsageDeclarationsRepo({"mozaikspay": 2})
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=declarations_repo)  # type: ignore[arg-type]

    result = await service.list_integrations(_FakeCtx())

    mozaikspay = next(item for item in result["integrations"] if item["id"] == "mozaikspay")
    openai = next(item for item in result["integrations"] if item["id"] == "openai")
    assert mozaikspay["app_usage_count"] == 2
    assert openai["app_usage_count"] == 0
    assert result["summary"]["used"] == 1
    assert declarations_repo.requested_catalog_ids == [entry["id"] for entry in INTEGRATIONS_CATALOG]


@pytest.mark.asyncio
async def test_workspace_integrations_repo_uses_module_persistence_wrapper() -> None:
    ctx = _WrapperStyleCtx()
    repo = WorkspaceIntegrationsRepo()

    notes = await repo.get_all_notes(ctx)  # type: ignore[arg-type]

    assert notes == [
        {"integration_id": "openai", "note": "shared platform key"},
        {"integration_id": "mozaikspay", "note": "production account"},
    ]
    assert ctx.persistence.collection_calls == [("workspace_integrations", "integration_notes")]
    assert ctx.persistence.collection_handle.find_many_calls == [
        {
            "query": {},
            "limit": 100,
            "sort": [("integration_id", 1)],
            "projection": None,
        }
    ]


@pytest.mark.asyncio
async def test_service_list_integrations_category_filter() -> None:
    service = WorkspaceIntegrationsService(repo=_FakeRepo())
    result = await service.list_integrations(_FakeCtx(), category="payments")
    for item in result["integrations"]:
        assert item["category"] == "payments"


@pytest.mark.asyncio
async def test_service_list_integrations_attaches_notes() -> None:
    service = WorkspaceIntegrationsService(
        repo=_FakeRepo(notes=[{"integration_id": "mozaikspay", "note": "production key"}])
    )
    result = await service.list_integrations(_FakeCtx())
    mozaikspay = next(i for i in result["integrations"] if i["id"] == "mozaikspay")
    assert mozaikspay["note"] == "production key"


@pytest.mark.asyncio
async def test_service_get_integration_unknown_returns_none() -> None:
    service = WorkspaceIntegrationsService(repo=_FakeRepo())
    result = await service.get_integration(_FakeCtx(), integration_id="not_a_real_service")
    assert result["integration"] is None


@pytest.mark.asyncio
async def test_service_set_integration_note_persists() -> None:
    repo = _FakeRepo()
    service = WorkspaceIntegrationsService(repo=repo)
    result = await service.set_integration_note(
        _FakeCtx(), integration_id="mozaikspay", note="rotate quarterly", user_id="user_1"
    )
    assert result["success"] is True
    assert result["integration_id"] == "mozaikspay"
    assert repo.upserted is not None
    assert repo.upserted["integration_id"] == "mozaikspay"
    assert repo.upserted["note"] == "rotate quarterly"


@pytest.mark.asyncio
async def test_service_set_integration_note_unknown_raises() -> None:
    service = WorkspaceIntegrationsService(repo=_FakeRepo())
    with pytest.raises(ValueError, match="Unknown integration"):
        await service.set_integration_note(
            _FakeCtx(), integration_id="not_real", note="test", user_id="user_1"
        )


# ── declaration schemas ───────────────────────────────────────────────────────

def test_build_declaration_document_shape() -> None:
    doc = build_declaration_document(
        app_id="app_123",
        service="resend",
        catalog_id="resend",
        display_name="Resend",
        kind="api_key",
        purpose="Transactional email",
        required_at="runtime",
        optional=False,
        workspace_status="missing",
        connector_status="not_configured",
        declared_at="2026-07-11T00:00:00Z",
    )
    assert doc["app_id"] == "app_123"
    assert doc["service"] == "resend"
    assert doc["catalog_id"] == "resend"
    assert doc["workspace_status"] == "missing"
    assert doc["connector_status"] == "not_configured"
    assert doc["optional"] is False


def test_build_declaration_response_setup_url_only_when_missing() -> None:
    doc = build_declaration_document(
        app_id="app_1",
        service="twilio",
        catalog_id="twilio",
        workspace_status="missing",
        connector_status="not_configured",
        declared_at="2026-07-11T00:00:00Z",
    )
    response = build_declaration_response(doc)
    assert response["setup_url"] is not None
    assert "twilio" in response["setup_url"]


def test_build_declaration_response_no_setup_url_when_configured() -> None:
    doc = build_declaration_document(
        app_id="app_1",
        service="openai",
        catalog_id="openai",
        workspace_status="configured",
        connector_status="ready",
        declared_at="2026-07-11T00:00:00Z",
    )
    response = build_declaration_response(doc)
    assert response["setup_url"] is None


def test_build_declaration_response_no_setup_url_for_uncatalogued() -> None:
    doc = build_declaration_document(
        app_id="app_1",
        service="custom_crm",
        catalog_id=None,
        workspace_status=None,
        connector_status="not_configured",
        declared_at="2026-07-11T00:00:00Z",
    )
    response = build_declaration_response(doc)
    assert response["setup_url"] is None


def test_build_declaration_response_includes_removable_default_metadata() -> None:
    doc = build_declaration_document(
        app_id="app_1",
        service="mozaikspay",
        catalog_id="mozaikspay",
        display_name="Mozaiks Pay",
        workspace_status="missing",
        connector_status="not_configured",
        defaulted=True,
        removable=True,
        source="monetization_default",
        required_fields=[{"name": "client_secret", "type": "secret", "frontend_safe": False}],
        declared_at="2026-07-11T00:00:00Z",
    )
    response = build_declaration_response(doc)
    assert response["defaulted"] is True
    assert response["removable"] is True
    assert response["source"] == "monetization_default"
    assert response["required_fields"][0]["name"] == "client_secret"


def test_declarations_entity_constant_is_set() -> None:
    assert DECLARATIONS_ENTITY == "integration_declarations"


# ── declaration service ───────────────────────────────────────────────────────

class _FakeDeclarationsRepo:
    def __init__(self, stored: list[dict[str, Any]] | None = None) -> None:
        self._stored: list[dict[str, Any]] = stored or []
        self.upserted: list[dict[str, Any]] | None = None

    async def upsert_declarations(self, *, app_id: str, declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.upserted = declarations
        for declaration in declarations:
            service = declaration.get("service")
            self._stored = [
                existing
                for existing in self._stored
                if not (existing.get("app_id") == app_id and existing.get("service") == service)
            ]
            self._stored.append(declaration)
        return declarations

    async def get_for_app(self, *, app_id: str) -> list[dict[str, Any]]:
        return [d for d in self._stored if d.get("app_id") == app_id]

    async def soft_delete_declaration(
        self,
        *,
        app_id: str,
        service: str,
        removed_by: str | None = None,
    ) -> bool:
        self._stored = [
            existing
            for existing in self._stored
            if not (existing.get("app_id") == app_id and existing.get("service") == service)
        ]
        self._stored.append(
            {
                "app_id": app_id,
                "service": service,
                "removed": True,
                "removed_by": removed_by,
            }
        )
        return True


@pytest.mark.asyncio
async def test_declare_app_integration_needs_persists_docs() -> None:
    decl_repo = _FakeDeclarationsRepo()
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)
    result = await service.declare_app_integration_needs(
        app_id="app_abc",
        needs=[
            {"service": "resend", "kind": "api_key", "purpose": "Email delivery", "optional": False},
            {"service": "twilio", "kind": "api_key", "purpose": "SMS", "optional": True},
        ],
        declared_at="2026-07-11T00:00:00Z",
    )
    assert result["saved"] == 2
    assert result["app_id"] == "app_abc"
    assert decl_repo.upserted is not None
    services = {d["service"] for d in decl_repo.upserted}
    assert services == {"resend", "twilio"}


@pytest.mark.asyncio
async def test_upsert_app_integration_need_persists_single_record() -> None:
    decl_repo = _FakeDeclarationsRepo()
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)
    result = await service.upsert_app_integration_need(
        app_id="app_pay",
        need={
            "service": "mozaikspay",
            "catalog_id": "mozaikspay",
            "defaulted": True,
            "removable": True,
            "source": "monetization_default",
        },
        declared_at="2026-07-11T00:00:00Z",
    )
    assert result["saved"] == 1
    assert result["declaration"]["service"] == "mozaikspay"
    assert result["declaration"]["defaulted"] is True
    assert decl_repo.upserted is not None
    assert decl_repo.upserted[0]["removable"] is True


@pytest.mark.asyncio
async def test_delete_app_integration_need_hides_removed_declaration() -> None:
    stored = [
        build_declaration_document(
            app_id="app_pay",
            service="mozaikspay",
            catalog_id="mozaikspay",
            defaulted=True,
            removable=True,
            declared_at="2026-07-11T00:00:00Z",
        )
    ]
    decl_repo = _FakeDeclarationsRepo(stored=stored)
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)

    deleted = await service.delete_app_integration_need(
        app_id="app_pay",
        service="mozaikspay",
        user_id="operator_1",
    )
    listed = await service.list_app_integration_needs(app_id="app_pay")

    assert deleted["deleted"] is True
    assert listed["declarations"] == []


@pytest.mark.asyncio
async def test_declare_app_integration_needs_skips_empty_service() -> None:
    decl_repo = _FakeDeclarationsRepo()
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)
    result = await service.declare_app_integration_needs(
        app_id="app_abc",
        needs=[{"service": "", "kind": "api_key"}],
        declared_at="2026-07-11T00:00:00Z",
    )
    assert result["saved"] == 0


@pytest.mark.asyncio
async def test_declare_app_integration_needs_empty_list_returns_zero() -> None:
    decl_repo = _FakeDeclarationsRepo()
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)
    result = await service.declare_app_integration_needs(
        app_id="app_abc", needs=[], declared_at="2026-07-11T00:00:00Z"
    )
    assert result["saved"] == 0


@pytest.mark.asyncio
async def test_list_app_integration_needs_returns_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed one catalog-matched (resend) and one uncatalogued (custom_crm) declaration
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    stored = [
        build_declaration_document(
            app_id="app_xyz",
            service="resend",
            catalog_id="resend",
            workspace_status="configured",  # will be overwritten by live derive_status
            connector_status="not_configured",
            declared_at="2026-07-11T00:00:00Z",
        ),
        build_declaration_document(
            app_id="app_xyz",
            service="custom_crm",
            catalog_id=None,
            workspace_status=None,
            connector_status="not_configured",
            declared_at="2026-07-11T00:00:00Z",
        ),
    ]
    decl_repo = _FakeDeclarationsRepo(stored=stored)
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)
    result = await service.list_app_integration_needs(app_id="app_xyz")

    assert result["app_id"] == "app_xyz"
    decls = result["declarations"]
    assert len(decls) == 2

    resend_decl = next(d for d in decls if d["service"] == "resend")
    # workspace_status is live-derived: resend key is absent so status should be "missing"
    assert resend_decl["workspace_status"] == "missing"
    assert resend_decl["setup_url"] is not None

    crm_decl = next(d for d in decls if d["service"] == "custom_crm")
    # uncatalogued service — workspace_status stays None
    assert crm_decl["workspace_status"] is None
    assert crm_decl["setup_url"] is None


@pytest.mark.asyncio
async def test_list_app_integration_needs_summary_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    stored = [
        build_declaration_document(
            app_id="app_s",
            service="resend",
            catalog_id="resend",
            workspace_status="configured",
            connector_status="not_configured",
            optional=False,
            declared_at="2026-07-11T00:00:00Z",
        ),
        build_declaration_document(
            app_id="app_s",
            service="twilio",
            catalog_id="twilio",
            workspace_status="configured",
            connector_status="not_configured",
            optional=True,
            declared_at="2026-07-11T00:00:00Z",
        ),
    ]
    decl_repo = _FakeDeclarationsRepo(stored=stored)
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)
    result = await service.list_app_integration_needs(app_id="app_s")
    summary = result["summary"]
    assert summary["total"] == 2
    assert summary["required"] == 1   # resend is required, twilio is optional
    # both are missing from workspace, but only resend is required → blocking = 1
    assert summary["blocking"] == 1


@pytest.mark.asyncio
async def test_list_app_integration_needs_does_not_block_catalog_service_configured_by_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    stored = [
        build_declaration_document(
            app_id="app_env",
            service="openai",
            catalog_id="openai",
            workspace_status="missing",
            connector_status="not_configured",
            optional=False,
            declared_at="2026-07-11T00:00:00Z",
        )
    ]
    decl_repo = _FakeDeclarationsRepo(stored=stored)
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)

    result = await service.list_app_integration_needs(app_id="app_env")

    declaration = result["declarations"][0]
    assert declaration["workspace_status"] == "configured"
    assert declaration["connector_status"] == "not_configured"
    assert result["summary"]["blocking"] == 0


@pytest.mark.asyncio
async def test_list_app_integration_needs_treats_ready_workspace_connector_as_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    stored = [
        build_declaration_document(
            app_id="app_connector",
            service="resend",
            catalog_id="resend",
            workspace_status="missing",
            connector_status="not_configured",
            optional=False,
            declared_at="2026-07-11T00:00:00Z",
        )
    ]
    decl_repo = _FakeDeclarationsRepo(stored=stored)
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)

    async def fake_list_workspace_connectors(*, workspace_id: str) -> dict[str, Any]:
        assert workspace_id == "workspace_1"
        return {
            "connectors": [
                {
                    "service": "resend",
                    "ready": True,
                    "configured": True,
                    "health": {"status": "configured"},
                }
            ],
            "total": 1,
        }

    service.list_workspace_connectors = fake_list_workspace_connectors  # type: ignore[method-assign]

    result = await service.list_app_integration_needs(app_id="app_connector", workspace_id="workspace_1")

    declaration = result["declarations"][0]
    assert declaration["workspace_status"] == "configured"
    assert declaration["connector_status"] == "ready"
    assert declaration["workspace_connector_status"] == "ready"
    assert declaration.get("setup_url") is None
    assert result["summary"]["blocking"] == 0


@pytest.mark.asyncio
async def test_list_app_integration_needs_empty_app_returns_empty() -> None:
    decl_repo = _FakeDeclarationsRepo()
    service = WorkspaceIntegrationsService(repo=_FakeRepo(), declarations_repo=decl_repo)
    result = await service.list_app_integration_needs(app_id="no_such_app")
    assert result["declarations"] == []
    assert result["summary"]["total"] == 0
