from __future__ import annotations

from typing import Any

import pytest

from factory_app.workflows.AppGenerator.tools import save_integration_manifest as manifest_module


class _Context:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class _FakeDeclarationsRepo:
    instances: list[_FakeDeclarationsRepo] = []

    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []
        self.__class__.instances.append(self)

    async def upsert_declarations(
        self,
        *,
        app_id: str,
        declarations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        self.saved = declarations
        return declarations


@pytest.mark.asyncio
async def test_save_integration_manifest_defaults_mozaikspay_for_subscription_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeDeclarationsRepo.instances = []
    monkeypatch.setattr(manifest_module, "IntegrationDeclarationsRepo", _FakeDeclarationsRepo)
    monkeypatch.delenv("MOZAIKSPAY_API_BASE", raising=False)
    monkeypatch.delenv("MOZAIKSPAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("MOZAIKSPAY_CLIENT_SECRET", raising=False)

    result = await manifest_module.save_integration_manifest(
        _Context(
            {
                "app_id": "app_monetized",
                "integration_needs": [],
                "app_build_plan": {"revenue_model": "subscriptions"},
            }
        )
    )

    assert result["saved"] == 1
    saved = _FakeDeclarationsRepo.instances[0].saved
    assert saved[0]["service"] == "mozaikspay"
    assert saved[0]["defaulted"] is True
    assert saved[0]["removable"] is True
    assert saved[0]["optional"] is True
    assert saved[0]["source"] == "monetization_default"


@pytest.mark.asyncio
async def test_save_integration_manifest_defaults_mozaikspay_for_required_subscription_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeDeclarationsRepo.instances = []
    monkeypatch.setattr(manifest_module, "IntegrationDeclarationsRepo", _FakeDeclarationsRepo)

    result = await manifest_module.save_integration_manifest(
        _Context(
            {
                "app_id": "app_contract_required",
                "integration_needs": [],
                "subscription_contract": {"contract_required": True},
            }
        )
    )

    assert result["saved"] == 1
    assert _FakeDeclarationsRepo.instances[0].saved[0]["service"] == "mozaikspay"


@pytest.mark.asyncio
async def test_save_integration_manifest_does_not_default_mozaikspay_for_custom_money_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeDeclarationsRepo.instances = []
    monkeypatch.setattr(manifest_module, "IntegrationDeclarationsRepo", _FakeDeclarationsRepo)

    result = await manifest_module.save_integration_manifest(
        _Context(
            {
                "app_id": "app_custom_money",
                "integration_needs": [],
                "monetization_enabled": True,
                "app_build_plan": {
                    "revenue_model": "custom",
                    "monetization_plan": {"subscription_contract_requirement": "not_required"},
                },
            }
        )
    )

    assert result["saved"] == 0
    assert _FakeDeclarationsRepo.instances == []


@pytest.mark.asyncio
async def test_save_integration_manifest_does_not_default_mozaikspay_for_free_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeDeclarationsRepo.instances = []
    monkeypatch.setattr(manifest_module, "IntegrationDeclarationsRepo", _FakeDeclarationsRepo)

    result = await manifest_module.save_integration_manifest(
        _Context(
            {
                "app_id": "app_free",
                "integration_needs": [],
                "app_build_plan": {"revenue_model": "free"},
            }
        )
    )

    assert result["saved"] == 0
    assert _FakeDeclarationsRepo.instances == []
