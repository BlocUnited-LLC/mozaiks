from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory_app.app.modules.app_registry.backend.repo import AppRegistryRepo
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
    assert {"create_app_record", "update_build_status", "list_apps", "get_app_record", "promote_build"} == actions
    create_props = next(entry for entry in manifest["actions"] if entry["id"] == "create_app_record")["input_schema"]["properties"]
    update_props = next(entry for entry in manifest["actions"] if entry["id"] == "update_build_status")["input_schema"]["properties"]
    assert "build_context_profile" in create_props
    assert "current_build_run" in create_props
    assert "artifact_version_id" in update_props
    assert "current_build_run" in update_props
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
            "name_status": kwargs["name_status"],
            "name_source": kwargs["name_source"],
            "description": kwargs["description"],
            "lifecycle_state": kwargs["lifecycle_state"],
            "build_context_profile": kwargs.get("build_context_profile"),
            "current_build_run": kwargs.get("current_build_run"),
            "build_runs": [kwargs["current_build_run"]] if kwargs.get("current_build_run") else [],
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
            "current_build_run": kwargs.get("current_build_run")
            or {
                **(self.record.get("current_build_run") or {}),
                "status": kwargs["lifecycle_state"],
                "artifact_version_id": kwargs.get("artifact_version_id"),
                "workflow_sequence": kwargs.get("workflow_sequence"),
            },
        }
        self.record["build_runs"] = [self.record["current_build_run"]]
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
    assert app["name_status"] == "named"
    assert app["name_source"] == "manual"

    updated = await service.update_build_status(
        build_registry_id="appreg_1",
        status="review",
        bundle_path="generated/apps/app_1/build_1/app",
    )
    assert updated["success"] is True
    assert updated["app"]["lifecycle_state"] == "review"
    assert updated["app"]["bundle_path"] == "generated/apps/app_1/build_1/app"


@pytest.mark.asyncio
async def test_app_registry_service_creates_provisional_record_without_name() -> None:
    service = AppRegistryService(repo=_FakeRepo())

    created = await service.create_app_record(
        owner_user_id="user_1",
        name=None,
        status="building",
        name_source="provisional",
    )

    app = created["app"]
    assert created["success"] is True
    assert app["app_id"].startswith("draft-app-")
    assert app["name"] is None
    assert app["name_status"] == "provisional"
    assert app["name_source"] == "provisional"


@pytest.mark.asyncio
async def test_app_registry_service_persists_build_context_and_current_run() -> None:
    service = AppRegistryService(repo=_FakeRepo())

    created = await service.create_app_record(
        owner_user_id="user_1",
        name=None,
        status="building",
        chat_app_id="factory-app",
        active_chat_id="chat_1",
        active_workflow_id="ValueEngine",
        build_context_profile={
            "source": "factory_app",
            "workflow_sequence": "build",
            "selected_context_ids": ["mozaikspay"],
        },
        current_build_run={
            "build_id": "build_1",
            "workflow_sequence": "build",
            "status": "building",
            "active_chat_id": "chat_1",
            "active_workflow_id": "ValueEngine",
        },
    )

    app = created["app"]
    assert app["build_context_profile"]["selected_context_ids"] == ["mozaikspay"]
    assert app["current_build_run"]["build_id"] == "build_1"
    assert app["current_build_run"]["active_chat_id"] == "chat_1"

    updated = await service.update_build_status(
        build_registry_id="appreg_1",
        status="review",
        bundle_path="generated/apps/app_1/build_1/app",
        artifact_version_id="av_bundle_1",
        workflow_sequence="build",
    )

    assert updated["app"]["current_build_run"]["status"] == "review"
    assert updated["app"]["current_build_run"]["artifact_version_id"] == "av_bundle_1"
    assert updated["app"]["build_runs"][0]["artifact_version_id"] == "av_bundle_1"


@pytest.mark.asyncio
async def test_app_registry_enriches_legacy_active_chat_with_session_app_id(monkeypatch) -> None:
    repo = AppRegistryRepo.__new__(AppRegistryRepo)

    async def fake_find_chat_session(*, chat_id: str, owner_user_id: str):  # noqa: ANN001
        assert chat_id == "chat_1"
        assert owner_user_id == "user_1"
        return {
            "_id": "chat_1",
            "app_id": "factory-session-app",
            "workflow_name": "ValueEngine",
        }

    monkeypatch.setattr(repo, "_find_chat_session", fake_find_chat_session)
    record = {
        "build_registry_id": "appreg_1",
        "app_id": "generated-build-app",
        "owner_user_id": "user_1",
        "lifecycle_state": "building",
        "active_chat_id": "chat_1",
    }

    enriched = await repo._with_active_chat_fallback(record, owner_user_id="user_1")  # noqa: SLF001

    assert enriched["active_chat_id"] == "chat_1"
    assert enriched["chat_app_id"] == "factory-session-app"
    assert enriched["active_workflow_id"] == "ValueEngine"
    assert record.get("chat_app_id") is None

