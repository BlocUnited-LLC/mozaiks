from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scripts import smoke_integration_readiness

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_manual_smoke_script_uses_neutral_provider_only() -> None:
    source = (REPO_ROOT / "scripts/smoke_integration_readiness.py").read_text(encoding="utf-8")

    assert "analytics_provider" in source
    assert "managed_analytics" in source
    assert "payment_provider" not in source


def test_manual_smoke_round_trip_passes_without_secret_leak() -> None:
    result = asyncio.run(smoke_integration_readiness.run_smoke())
    rendered = json.dumps(result, sort_keys=True)

    assert result["success"] is True
    assert result["blocked_status"] == "blocked"
    assert result["ready_status"] == "ready"
    assert result["assertions"] == {
        "blocked_before_prompt": True,
        "emitted_integration_required": True,
        "readiness_ready_after_submit": True,
        "connector_secret_available": True,
        "public_config_saved": True,
        "vault_received_secret": True,
        "generated_output_safe": True,
    }
    assert result["first_request"]["event_type"] == "integration.required"
    assert result["first_request"]["integration_id"] == "analytics_provider"
    assert result["first_request"]["provider"] == "managed_analytics"
    assert result["first_request"]["secret_fields"][0]["name"] == "api_key"
    assert [field["name"] for field in result["first_request"]["non_secret_fields"]] == [
        "endpoint_url",
        "workspace_id",
    ]
    assert result["connector"]["public_config"] == {
        "endpoint_url": "https://analytics.example.test",
        "workspace_id": "demo-workspace",
    }
    assert "test-secret-value" not in rendered


def test_manual_smoke_cli_json_output_is_redacted(capsys) -> None:
    exit_code = smoke_integration_readiness.main(["--json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert "test-secret-value" not in captured.out

