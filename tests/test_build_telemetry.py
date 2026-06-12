from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from mozaiksai.core import telemetry


def _hash_build_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def test_build_workflow_payload_anonymizes_build_id_and_clamps_metrics() -> None:
    payload = telemetry.build_workflow_payload(
        workflow_name="AppGenerator",
        final_status="completed",
        build_registry_id="raw-build-registry-id",
        refinement_cycles=-2,
        quality_gate_blocks=-3,
        duration_seconds=-10.5,
        domain_tags=["crm", "support"],
        mozaiks_version="test-version",
    )

    serialized = json.dumps(payload)
    assert payload["schema_version"] == "mozaiks.telemetry.v1"
    assert payload["workflow_name"] == "AppGenerator"
    assert payload["final_status"] == "completed"
    assert payload["build_id_hash"] == _hash_build_id("raw-build-registry-id")
    assert "raw-build-registry-id" not in serialized
    assert payload["refinement_cycles"] == 0
    assert payload["quality_gate_blocks"] == 0
    assert payload["duration_seconds"] == 0.0
    assert payload["domain_tags"] == ["crm", "support"]
    assert payload["mozaiks_version"] == "test-version"
    assert payload["timestamp"]


def test_build_satisfaction_payload_anonymizes_build_id_and_clamps_rating() -> None:
    payload = telemetry.build_satisfaction_payload(
        rating=99,
        build_registry_id="raw-build-registry-id",
        sequence_id="build",
    )

    serialized = json.dumps(payload)
    assert payload["schema_version"] == "mozaiks.telemetry.v1"
    assert payload["event"] == "build_satisfaction"
    assert payload["rating"] == 5
    assert payload["build_id_hash"] == _hash_build_id("raw-build-registry-id")
    assert "raw-build-registry-id" not in serialized
    assert payload["sequence_id"] == "build"


@pytest.mark.asyncio
async def test_emit_build_telemetry_noops_when_endpoint_is_unset(monkeypatch) -> None:
    import httpx

    monkeypatch.delenv("MOZAIKS_TELEMETRY_ENDPOINT", raising=False)
    monkeypatch.setenv("MOZAIKS_TELEMETRY_ENABLED", "true")

    def fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("httpx.AsyncClient should not be constructed when telemetry endpoint is unset")

    monkeypatch.setattr(httpx, "AsyncClient", fail_if_called)

    await telemetry.emit_build_telemetry({"event": "build.completed"})


@pytest.mark.asyncio
async def test_emit_build_telemetry_posts_hmac_signed_payload(monkeypatch) -> None:
    import httpx

    calls: list[dict[str, object]] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> None:
            calls.append({"url": url, "content": content, "headers": headers, "timeout": self.timeout})

    monkeypatch.setenv("MOZAIKS_TELEMETRY_ENDPOINT", "https://telemetry.example.test/v1/build")
    monkeypatch.setenv("MOZAIKS_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("MOZAIKS_TELEMETRY_SECRET", "shared-secret")
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    payload = {"schema_version": "mozaiks.telemetry.v1", "final_status": "completed"}
    await telemetry.emit_build_telemetry(payload)

    expected_content = json.dumps(payload, separators=(",", ":")).encode()
    expected_signature = hmac.new(b"shared-secret", expected_content, hashlib.sha256).hexdigest()
    assert calls == [
        {
            "url": "https://telemetry.example.test/v1/build",
            "content": expected_content,
            "headers": {
                "Content-Type": "application/json",
                "X-Mozaiks-Signature": expected_signature,
            },
            "timeout": 3.0,
        }
    ]
