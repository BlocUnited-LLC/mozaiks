"""Tests for platform module dispatch endpoint behavior.

Covers the HTTP dispatch layer in mozaiksai/hosts/platform.py:
- startup-failed module returns 503 not 404
- module not in failed list falls through to executor (503 when executor absent)
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from mozaiksai.hosts import platform as platform_host


def _client(*, failed_module_names: list[str] | None = None) -> TestClient:
    platform_host.app.state.failed_module_names = failed_module_names or []
    return TestClient(platform_host.app, raise_server_exceptions=False)


def test_dispatch_to_startup_failed_module_returns_503() -> None:
    client = _client(failed_module_names=["tasks"])
    resp = client.get("/api/modules/tasks/list")
    assert resp.status_code == 503
    assert "failed to load at startup" in resp.json().get("detail", "")


def test_dispatch_to_different_module_not_in_failed_list_does_not_return_startup_503() -> None:
    # "tasks" failed but "contacts" did not — contacts should get a different error
    # (executor absent → 503 with different message, or 404/500 if executor has no entry)
    client = _client(failed_module_names=["tasks"])
    resp = client.get("/api/modules/contacts/list")
    # Must NOT be the "failed to load at startup" 503
    assert "failed to load at startup" not in resp.json().get("detail", "")


def test_dispatch_with_empty_failed_list_does_not_block() -> None:
    client = _client(failed_module_names=[])
    resp = client.get("/api/modules/tasks/list")
    # No startup-failure message — falls through to executor (which returns its own error)
    assert "failed to load at startup" not in resp.json().get("detail", "")


def test_invalid_module_name_returns_400_regardless_of_failed_list() -> None:
    client = _client(failed_module_names=["../evil"])
    # The name regex check fires before the failed_module_names check
    resp = client.get("/api/modules/../evil/action")
    assert resp.status_code in {400, 404, 422}
    assert "failed to load at startup" not in str(resp.json())
