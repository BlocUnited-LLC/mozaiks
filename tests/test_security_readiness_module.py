from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from factory_app.app.modules.security_readiness.backend.handler import SecurityReadinessModule
from factory_app.app.modules.security_readiness.backend.repo import SecurityReadinessRepo
from factory_app.app.modules.security_readiness.backend.schemas import (
    build_finding_document,
    summarize_findings,
)
from factory_app.app.modules.security_readiness.backend.service import SecurityReadinessService


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


class _FakeCtx:
    user_id = "user_1"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


class _FakeRepo:
    def __init__(self, stored: list[dict[str, Any]] | None = None) -> None:
        self.stored = stored or []
        self.inserted: list[dict[str, Any]] = []
        self.last_query: dict[str, Any] | None = None

    async def insert_findings(self, ctx: Any, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:  # noqa: ANN401
        self.inserted.extend(findings)
        self.stored.extend(findings)
        return findings

    async def list_findings(self, ctx: Any, *, query: dict[str, Any], limit: int) -> list[dict[str, Any]]:  # noqa: ANN401
        self.last_query = query
        matches = [
            item
            for item in self.stored
            if all(item.get(key) == value for key, value in query.items())
        ]
        return matches[:limit]

    async def update_status(
        self,
        ctx: Any,  # noqa: ANN401
        *,
        query: dict[str, Any],
        status: str,
        note: str | None,
        updated_by: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        self.last_query = query
        for item in self.stored:
            if all(item.get(key) == value for key, value in query.items()):
                item.update(
                    {
                        "status": status,
                        "status_note": note,
                        "status_updated_by": updated_by,
                        "status_updated_at": updated_at,
                        "updated_at": updated_at,
                    }
                )
                return item
        return None


class _WrapperStyleCollection:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.find_many_calls: list[dict[str, Any]] = []

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False) -> None:
        self.update_calls.append({"query": query, "update": update, "upsert": upsert})
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                row.update(update.get("$set", {}))
                return
        if upsert:
            self.rows.append(dict(update.get("$set", {})))

    async def find_many(self, query: dict[str, Any], *, limit: int = 100, sort=None, projection=None) -> list[dict[str, Any]]:
        self.find_many_calls.append({"query": query, "limit": limit, "sort": sort, "projection": projection})
        rows = [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in query.items())
        ]
        return rows[:limit]

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return row
        return None


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


def test_security_readiness_module_yaml_contract() -> None:
    module_root = _workspace() / "factory_app" / "app" / "modules" / "security_readiness"
    manifest = yaml.safe_load((module_root / "module.yaml").read_text(encoding="utf-8"))
    action_ids = {action["id"] for action in manifest["actions"]}

    assert manifest["module"]["id"] == "security_readiness"
    assert manifest["module"]["handler"] == "backend.handler:SecurityReadinessModule"
    assert action_ids == {
        "record_assessment",
        "list_findings",
        "get_summary",
        "update_finding_status",
    }
    assert (module_root / "contracts" / "events.yaml").exists()
    assert (module_root / "backend" / "handler.py").exists()
    assert (module_root / "backend" / "service.py").exists()
    assert (module_root / "backend" / "repo.py").exists()
    assert (module_root / "backend" / "policy.py").exists()
    assert (module_root / "backend" / "schemas.py").exists()

    record = next(action for action in manifest["actions"] if action["id"] == "record_assessment")
    finding_schema = record["input_schema"]["properties"]["findings"]["items"]
    assert finding_schema["additionalProperties"] is False
    assert "compliance_readiness" in finding_schema["properties"]["control_area"]["enum"]


def test_build_finding_document_never_stores_secret_values() -> None:
    doc = build_finding_document(
        app_id="app_1",
        owner_user_id="user_1",
        finding={
            "title": "Secrets contract names required env handles",
            "description": "The generated app should declare secret names, not values.",
            "severity": "high",
            "control_area": "secrets",
            "evidence_ref": "artifact://validation/security",
            "remediation": "Add app/security/secrets.yaml entries for required handles.",
        },
        source="validation",
        assessed_at="2026-09-11T00:00:00Z",
        now="2026-09-11T00:00:00Z",
        artifact_version_id="artifact_1",
    )

    assert doc["finding_id"].startswith("sr_")
    assert doc["owner_user_id"] == "user_1"
    assert doc["severity"] == "high"
    assert doc["control_area"] == "secrets"
    assert "secret_value" not in doc
    assert "credential" not in doc


def test_summarize_findings_counts_open_blockers() -> None:
    summary = summarize_findings(
        [
            {"status": "open", "severity": "critical", "control_area": "auth"},
            {"status": "open", "severity": "high", "control_area": "secrets"},
            {"status": "accepted", "severity": "high", "control_area": "deployment"},
            {"status": "resolved", "severity": "medium", "control_area": "permissions"},
        ]
    )

    assert summary["total"] == 4
    assert summary["open"] == 2
    assert summary["accepted"] == 1
    assert summary["resolved"] == 1
    assert summary["blocking"] == 2
    assert summary["highest_open_severity"] == "critical"
    assert summary["by_control_area"]["secrets"] == 1


@pytest.mark.asyncio
async def test_record_assessment_persists_findings_and_emits_event() -> None:
    ctx = _FakeCtx()
    repo = _FakeRepo()
    service = SecurityReadinessService(repo=repo)  # type: ignore[arg-type]

    result = await service.record_assessment(
        ctx,
        app_id="app_1",
        build_id="build_1",
        source="validation",
        findings=[
            {
                "title": "Protected route needs auth",
                "severity": "high",
                "status": "open",
                "control_area": "auth",
            }
        ],
    )

    assert result["success"] is True
    assert result["saved"] == 1
    assert result["summary"]["blocking"] == 1
    assert repo.inserted[0]["source"] == "validation"
    assert ctx.events == [
        (
            "domain.security_readiness.assessment_recorded",
            {
                "app_id": "app_1",
                "build_id": "build_1",
                "saved": 1,
            },
        )
    ]


@pytest.mark.asyncio
async def test_handler_delegates_to_service() -> None:
    ctx = _FakeCtx()
    module = SecurityReadinessModule(service=SecurityReadinessService(repo=_FakeRepo()))  # type: ignore[arg-type]

    result = await module.record_assessment(
        ctx,
        app_id="app_1",
        findings=[
            {
                "title": "OIDC auth is required",
                "severity": "medium",
                "control_area": "auth",
            }
        ],
    )

    assert result["success"] is True
    assert result["saved"] == 1


@pytest.mark.asyncio
async def test_list_findings_scopes_to_user_and_filters() -> None:
    repo = _FakeRepo(
        stored=[
            {
                "finding_id": "sr_1",
                "app_id": "app_1",
                "owner_user_id": "user_1",
                "status": "open",
                "severity": "high",
                "control_area": "auth",
            },
            {
                "finding_id": "sr_2",
                "app_id": "app_1",
                "owner_user_id": "other_user",
                "status": "open",
                "severity": "high",
                "control_area": "auth",
            },
        ]
    )
    service = SecurityReadinessService(repo=repo)  # type: ignore[arg-type]

    result = await service.list_findings(
        _FakeCtx(),
        app_id="app_1",
        status="open",
        severity="high",
        control_area="auth",
    )

    assert result["count"] == 1
    assert result["findings"][0]["finding_id"] == "sr_1"
    assert repo.last_query == {
        "app_id": "app_1",
        "owner_user_id": "user_1",
        "status": "open",
        "severity": "high",
        "control_area": "auth",
    }


@pytest.mark.asyncio
async def test_update_finding_status_scopes_and_emits_event() -> None:
    ctx = _FakeCtx()
    repo = _FakeRepo(
        stored=[
            {
                "finding_id": "sr_1",
                "app_id": "app_1",
                "owner_user_id": "user_1",
                "status": "open",
                "severity": "high",
                "control_area": "auth",
            }
        ]
    )
    service = SecurityReadinessService(repo=repo)  # type: ignore[arg-type]

    result = await service.update_finding_status(ctx, finding_id="sr_1", status="accepted", note="Known local-only route.")

    assert result["success"] is True
    assert result["finding"]["status"] == "accepted"
    assert result["finding"]["status_note"] == "Known local-only route."
    assert repo.last_query == {"finding_id": "sr_1", "owner_user_id": "user_1"}
    assert ctx.events[0][0] == "domain.security_readiness.finding_status_updated"
    assert ctx.events[0][1]["status"] == "accepted"


@pytest.mark.asyncio
async def test_repo_uses_canonical_module_persistence_wrapper() -> None:
    ctx = _WrapperStyleCtx()
    repo = SecurityReadinessRepo()

    await repo.insert_findings(
        ctx,  # type: ignore[arg-type]
        [
            {
                "finding_id": "sr_1",
                "app_id": "app_1",
                "owner_user_id": "user_1",
                "status": "open",
                "updated_at": "2026-09-11T00:00:00Z",
            }
        ],
    )
    listed = await repo.list_findings(
        ctx,  # type: ignore[arg-type]
        query={"app_id": "app_1", "owner_user_id": "user_1"},
        limit=10,
    )

    assert listed[0]["finding_id"] == "sr_1"
    assert ctx.persistence.collection_calls == [
        ("security_readiness", "findings"),
        ("security_readiness", "findings"),
    ]


@pytest.mark.asyncio
async def test_same_scanner_rule_cannot_overwrite_another_project_or_owner() -> None:
    ctx = _FakeCtx()
    ctx.persistence = _WrapperStylePersistence()
    service = SecurityReadinessService()
    finding = {"finding_id": "auth:missing", "title": "Auth missing", "severity": "high", "control_area": "auth"}
    for project in ("project_a", "project_b", "project_a"):
        await service.record_assessment(
            ctx, app_id="factory-host", build_registry_id=project,
            build_id="build_1", artifact_version_id="artifact_1", findings=[finding],
        )
    ctx.user_id = "other_user"
    await service.record_assessment(
        ctx, app_id="factory-host", build_registry_id="project_a",
        build_id="build_1", artifact_version_id="artifact_1", findings=[finding],
    )
    rows = ctx.persistence.collection_handle.rows
    assert len(rows) == 3
    assert len({row["finding_id"] for row in rows}) == 3
    ctx.user_id = "user_1"
    a = await service.list_findings(ctx, app_id="factory-host", build_registry_id="project_a")
    b = await service.list_findings(ctx, app_id="factory-host", build_registry_id="project_b")
    assert a["count"] == b["count"] == 1
    assert a["findings"][0]["finding_id"] != b["findings"][0]["finding_id"]
    assert a["findings"][0]["owner_user_id"] == "user_1"


@pytest.mark.asyncio
async def test_registry_filter_applies_before_limit_and_scopes_summary() -> None:
    common = {"app_id": "factory-host", "owner_user_id": "user_1", "severity": "high", "status": "open"}
    repo = _FakeRepo(stored=[
        {**common, "build_registry_id": "other_project"} for _ in range(251)
    ] + [{**common, "build_registry_id": "project_a"}, common])
    module = SecurityReadinessModule(SecurityReadinessService(repo=repo))
    listed = await module.list_findings(_FakeCtx(), app_id="factory-host", build_registry_id="project_a", limit=1)
    summary = await module.get_summary(_FakeCtx(), app_id="factory-host", build_registry_id="project_a")
    assert listed["count"] == summary["summary"]["total"] == 1
    assert listed["findings"][0]["build_registry_id"] == "project_a"
    assert repo.last_query == {"app_id": "factory-host", "owner_user_id": "user_1", "build_registry_id": "project_a"}
