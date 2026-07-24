"""Tests for the AppGenerator check_workspace_integrations factory tool."""
from __future__ import annotations

import pytest

from factory_app.app.modules.workspace_integrations.backend.schemas import INTEGRATIONS_CATALOG
from factory_app.workflows.AppGenerator.tools.check_workspace_integrations import (
    check_workspace_integrations,
)


class _Context:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = dict(data)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

# ── full catalog (no filter) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_all_catalog_entries_when_no_ids() -> None:
    result = await check_workspace_integrations(integration_ids=None)
    total = (
        len(result["available"])
        + len(result["partial"])
        + len(result["missing"])
        + len(result["unknown"])
    )
    assert total == len(INTEGRATIONS_CATALOG)
    assert result["not_in_catalog"] == []


@pytest.mark.asyncio
async def test_empty_list_returns_full_catalog() -> None:
    result = await check_workspace_integrations(integration_ids=[])
    total = (
        len(result["available"])
        + len(result["partial"])
        + len(result["missing"])
        + len(result["unknown"])
    )
    assert total == len(INTEGRATIONS_CATALOG)


# ── filtered by id ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_not_in_catalog_returned_for_unknown_ids() -> None:
    result = await check_workspace_integrations(integration_ids=["mozaikspay", "my_custom_one"])
    assert "my_custom_one" in result["not_in_catalog"]
    # mozaikspay should appear in one of the status buckets
    all_ids = {e["id"] for bucket in ("available", "partial", "missing", "unknown") for e in result[bucket]}
    assert "mozaikspay" in all_ids


@pytest.mark.asyncio
async def test_filtered_check_only_returns_requested_ids() -> None:
    result = await check_workspace_integrations(integration_ids=["openai", "resend"])
    all_ids = {e["id"] for bucket in ("available", "partial", "missing", "unknown") for e in result[bucket]}
    assert all_ids <= {"openai", "resend"}
    assert result["not_in_catalog"] == []


# ── configured status when env vars present ───────────────────────────────────

@pytest.mark.asyncio
async def test_mozaikspay_configured_when_secrets_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKSPAY_API_BASE", "https://pay.mozaiks.test")
    monkeypatch.setenv("MOZAIKSPAY_API_KEY", "mzk_test_key")
    result = await check_workspace_integrations(integration_ids=["mozaikspay"])
    available_ids = [e["id"] for e in result["available"]]
    assert "mozaikspay" in available_ids


@pytest.mark.asyncio
async def test_missing_status_when_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    result = await check_workspace_integrations(integration_ids=["twilio"])
    missing_ids = [e["id"] for e in result["missing"]]
    assert "twilio" in missing_ids
    twilio_entry = next(e for e in result["missing"] if e["id"] == "twilio")
    assert "missing_secrets" in twilio_entry
    assert len(twilio_entry["missing_secrets"]) > 0
    assert "setup_url" in twilio_entry


@pytest.mark.asyncio
async def test_workspace_connector_can_make_prechat_integration_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    async def fake_inventory(*, scope, scope_id, required_services=None):
        assert scope == "workspace"
        assert scope_id == "workspace_1"
        assert required_services == ["resend"]
        return {
            "connectors": [
                {
                    "service": "resend",
                    "ready": True,
                    "health": {"status": "healthy", "health_check_supported": True},
                }
            ],
        }

    monkeypatch.setattr(
        "factory_app.workflows.AppGenerator.tools.check_workspace_integrations.get_connector_inventory",
        fake_inventory,
    )

    result = await check_workspace_integrations(
        integration_ids=["resend"],
        context_variables=_Context({"workspace_id": "workspace_1"}),
    )

    assert result["missing"] == []
    assert result["partial"] == []
    assert result["available"][0]["id"] == "resend"
    assert result["available"][0]["source"] == "workspace_connector"
    assert result["available"][0]["connector_status"] == "ready"
    assert result["available"][0]["health_status"] == "healthy"


@pytest.mark.asyncio
async def test_unready_workspace_connector_stays_partial_until_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MONGO_URI", raising=False)

    async def fake_inventory(*, scope, scope_id, required_services=None):
        del scope, scope_id, required_services
        return {
            "connectors": [
                {
                    "service": "mongodb",
                    "ready": False,
                    "health": {"status": "pending_validation", "health_check_supported": True},
                }
            ],
        }

    monkeypatch.setattr(
        "factory_app.workflows.AppGenerator.tools.check_workspace_integrations.get_connector_inventory",
        fake_inventory,
    )

    result = await check_workspace_integrations(
        integration_ids=["mongodb"],
        context_variables={"workspace_id": "workspace_1"},
    )

    assert result["available"] == []
    assert result["missing"] == []
    assert result["partial"][0]["id"] == "mongodb"
    assert result["partial"][0]["connector_status"] == "partial"
    assert result["partial"][0]["health_status"] == "pending_validation"


# ── catalog_only mode ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_catalog_only_mode_returns_unknown_for_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_INTEGRATIONS_REGISTRY_MODE", "catalog_only")
    result = await check_workspace_integrations(integration_ids=["mozaikspay", "openai"])
    # In catalog_only mode no secrets can be read — everything with required secrets → unknown
    assert result["available"] == []
    assert result["partial"] == []
    assert result["missing"] == []
    all_unknown_ids = {e["id"] for e in result["unknown"]}
    assert "mozaikspay" in all_unknown_ids
    assert "openai" in all_unknown_ids


# ── result shape ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_result_has_all_expected_keys() -> None:
    result = await check_workspace_integrations(integration_ids=["mozaikspay"])
    assert set(result.keys()) >= {"available", "partial", "missing", "unknown", "not_in_catalog"}


@pytest.mark.asyncio
async def test_missing_entries_include_setup_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await check_workspace_integrations(integration_ids=["openai"])
    if result["missing"]:
        for entry in result["missing"]:
            assert "setup_url" in entry
            assert entry["setup_url"].startswith("/integrations/")
