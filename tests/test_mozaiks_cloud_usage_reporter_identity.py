"""The generated usage reporter must read and send only its bound app's metrics."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from factory_app.build_context.mozaiks_cloud.templates.modules.cloud_usage_reporter.backend import (
    handler,
    reporter,
)


@pytest.fixture(autouse=True)
def clear_reporter_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MOZAIKS_CLOUD_APP_ID",
        "MOZAIKS_APP_ID",
        "MOZAIKS_CLOUD_USAGE_REPORTING",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def reporter_dependencies(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    observed: dict[str, list[str]] = {"client_app_ids": [], "metrics_app_ids": [], "posts": []}

    class FakeClient:
        def __init__(self, *, app_id: str) -> None:
            observed["client_app_ids"].append(app_id)

        async def is_configured(self) -> bool:
            return True

        async def post_usage_rollups(self, rollups, *, idempotency_key: str):
            observed["posts"].append(idempotency_key)
            return {"accepted": len(rollups)}

    class FakeMetrics:
        def __init__(self, ctx) -> None:
            observed["metrics_app_ids"].append(ctx.app_id)

        async def usage_rollup(self, *, since):
            return [{"period_start": "2026-10-09", "granularity": "day", "page_views": 1}]

    client_module = ModuleType("app.services.integrations.mozaiks_cloud_usage_client")
    client_module.MozaiksCloudUsageClient = FakeClient
    monkeypatch.setitem(sys.modules, client_module.__name__, client_module)

    from mozaiksai.core.metrics import app_metrics

    monkeypatch.setattr(app_metrics, "AppMetrics", FakeMetrics)
    return observed


@pytest.mark.asyncio
async def test_hosted_cloud_app_id_scopes_reporter_and_connector(
    monkeypatch: pytest.MonkeyPatch, reporter_dependencies: dict[str, list[str]],
) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_APP_ID", "hosted-app")

    result = await reporter.UsageReporterService().report_once()

    assert result["sent"] == 1
    assert reporter_dependencies["client_app_ids"] == ["hosted-app"]
    assert reporter_dependencies["metrics_app_ids"] == ["hosted-app"]
    assert len(reporter_dependencies["posts"]) == 1


@pytest.mark.asyncio
async def test_standalone_runtime_app_id_can_scope_reporter(
    monkeypatch: pytest.MonkeyPatch, reporter_dependencies: dict[str, list[str]],
) -> None:
    monkeypatch.setenv("MOZAIKS_APP_ID", "standalone-app")

    result = await reporter.UsageReporterService().report_once()

    assert result["sent"] == 1
    assert reporter_dependencies["client_app_ids"] == ["standalone-app"]
    assert reporter_dependencies["metrics_app_ids"] == ["standalone-app"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cloud_id", "runtime_id", "reason"),
    [(None, None, "app_identity_missing"), ("app-one", "app-two", "app_identity_mismatch")],
)
async def test_missing_or_conflicting_identity_never_reads_or_sends(
    monkeypatch: pytest.MonkeyPatch,
    reporter_dependencies: dict[str, list[str]],
    cloud_id: str | None,
    runtime_id: str | None,
    reason: str,
) -> None:
    if cloud_id:
        monkeypatch.setenv("MOZAIKS_CLOUD_APP_ID", cloud_id)
    if runtime_id:
        monkeypatch.setenv("MOZAIKS_APP_ID", runtime_id)

    result = await reporter.UsageReporterService().report_once()

    assert result == {"sent": 0, "reason": reason}
    assert reporter_dependencies == {"client_app_ids": [], "metrics_app_ids": [], "posts": []}


@pytest.mark.asyncio
async def test_reporting_off_never_reads_or_sends(
    monkeypatch: pytest.MonkeyPatch, reporter_dependencies: dict[str, list[str]],
) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_APP_ID", "hosted-app")
    monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_REPORTING", "0")

    assert await reporter.UsageReporterService().report_once() == {"sent": 0, "reason": "disabled"}
    assert reporter_dependencies == {"client_app_ids": [], "metrics_app_ids": [], "posts": []}


@pytest.mark.asyncio
async def test_status_requires_environment_identity_to_match_loaded_app(
    monkeypatch: pytest.MonkeyPatch, reporter_dependencies: dict[str, list[str]],
) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_APP_ID", "hosted-app")
    status = await handler.CloudUsageReporterHandler().get_reporter_status(
        SimpleNamespace(app_id="other-app")
    )

    assert status == {
        "enabled": True,
        "configured": False,
        "reporting": False,
        "reason": "app_identity_mismatch",
    }
    assert reporter_dependencies["client_app_ids"] == []


@pytest.mark.asyncio
async def test_status_reports_ready_for_matching_hosted_identity(
    monkeypatch: pytest.MonkeyPatch, reporter_dependencies: dict[str, list[str]],
) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_APP_ID", "hosted-app")
    status = await handler.CloudUsageReporterHandler().get_reporter_status(
        SimpleNamespace(app_id="hosted-app")
    )

    assert status == {"enabled": True, "configured": True, "reporting": True}
    assert reporter_dependencies["client_app_ids"] == ["hosted-app"]
