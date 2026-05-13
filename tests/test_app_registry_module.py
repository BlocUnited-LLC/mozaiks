from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory_app.app.modules.app_registry.backend.service import AppRegistryService


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def test_app_registry_module_contract_is_present() -> None:
    module_root = _workspace() / "factory_app" / "app" / "modules" / "app_registry"
    contracts_dir = module_root / "contracts"
    manifest = yaml.safe_load((module_root / "module.yaml").read_text(encoding="utf-8"))
    actions = {entry["id"] for entry in manifest["actions"]}

    assert manifest["module"]["id"] == "app_registry"
    assert manifest["module"]["handler"] == "backend.handler:AppRegistryModule"
    assert {"create_app_record", "update_build_status", "list_apps", "get_app_record"} == actions
    assert contracts_dir.exists()
    assert (contracts_dir / "events.yaml").exists()
    assert (module_root / "backend" / "handler.py").exists()


class _FakeRepo:
    def __init__(self) -> None:
        self.record = None

    async def upsert_app_record(self, **kwargs):  # noqa: ANN003
        self.record = {
            "build_registry_id": "appreg_1",
            "app_id": kwargs["app_id"],
            "owner_user_id": kwargs["owner_user_id"],
            "name": kwargs["name"],
            "description": kwargs["description"],
            "lifecycle_state": kwargs["lifecycle_state"],
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        }
        return self.record

    async def update_lifecycle_state(self, **kwargs):  # noqa: ANN003
        if self.record is None:
            return None
        self.record = {
            **self.record,
            "build_registry_id": kwargs["build_registry_id"],
            "lifecycle_state": kwargs["lifecycle_state"],
            "bundle_path": kwargs.get("bundle_path"),
        }
        return self.record

    async def list_apps_for_user(self, *, owner_user_id: str):
        if self.record and self.record["owner_user_id"] == owner_user_id:
            return [self.record]
        return []

    async def get_by_app_id(self, *, app_id: str):
        if self.record and self.record["app_id"] == app_id:
            return self.record
        return None

    async def get_by_build_registry_id(self, *, build_registry_id: str):
        if self.record and self.record["build_registry_id"] == build_registry_id:
            return self.record
        return None


@pytest.mark.asyncio
async def test_app_registry_service_creates_and_updates_lifecycle_records() -> None:
    service = AppRegistryService(repo=_FakeRepo())

    created = await service.create_app_record(
        owner_user_id="user_1",
        name="Investor Memo Builder",
        status="draft",
    )
    app = created["app"]
    assert created["success"] is True
    assert app["build_registry_id"] == "appreg_1"
    assert app["app_id"].startswith("investor-memo-builder-")
    assert app["lifecycle_state"] == "draft"

    updated = await service.update_build_status(
        build_registry_id="appreg_1",
        status="review",
        bundle_path="generated/apps/app_1/build_1/app",
    )
    assert updated["success"] is True
    assert updated["app"]["lifecycle_state"] == "review"
    assert updated["app"]["bundle_path"] == "generated/apps/app_1/build_1/app"
