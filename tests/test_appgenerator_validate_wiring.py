from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


def _load_validate_wiring_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "tools"
        / "validate_wiring.py"
    )
    module_name = "tests.appgenerator_validate_wiring_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_wiring_module = _load_validate_wiring_module()


def _context(endpoint: str) -> dict:
    return {
        "app_pages": [
            {
                "name": "Tickets",
                "sections": [
                    {
                        "id": "ticket-table",
                        "config": {
                            "api_endpoint": endpoint,
                        },
                    }
                ],
            }
        ],
        "app_build_plan": {
            "capability_packs": [
                {"module_id": "tickets", "actions": ["list_tickets"]},
            ]
        },
    }


def test_validate_wiring_normalizes_canonical_module_endpoint() -> None:
    result = asyncio.run(
        validate_wiring_module.validate_wiring(
            _context("/api/modules/tickets/list_tickets")
        )
    )

    assert result["passed"] is True
    assert result["wired"] == [
        {
            "page": "Tickets",
            "section": "ticket-table",
            "endpoint": "/api/modules/tickets/list_tickets",
        }
    ]
    assert result["platform_endpoints"] == []


def test_validate_wiring_accepts_platform_account_usage_endpoint() -> None:
    result = asyncio.run(
        validate_wiring_module.validate_wiring(
            _context("/api/me/usage")
        )
    )

    assert result["passed"] is True
    assert result["wired"] == []
    assert result["platform_endpoints"] == [
        {
            "page": "Tickets",
            "section": "ticket-table",
            "endpoint": "/api/me/usage",
        }
    ]
    assert result["checks"][0]["details"]["platform_endpoint_count"] == 1


def test_validate_wiring_rejects_api_endpoint_query_string() -> None:
    result = asyncio.run(
        validate_wiring_module.validate_wiring(
            _context("/api/modules/tickets/list_tickets?limit=12")
        )
    )

    assert result["passed"] is False
    assert result["invalid_endpoints"][0]["endpoint"] == "/api/modules/tickets/list_tickets?limit=12"
    assert "query strings" in result["failed_tests"][0]["error"]


def test_validate_wiring_reads_generated_files_from_context() -> None:
    result = asyncio.run(
        validate_wiring_module.validate_wiring(
            {
                "generated_files": {
                    "ui/pages/tickets.yaml": """
name: Tickets
sections:
- id: ticket-table
  config:
    api_endpoint: /api/modules/tickets/list_tickets
""",
                    "modules/tickets/module.yaml": """
module:
  id: tickets
actions:
- id: list_tickets
  handler_method: list_tickets
""",
                }
            }
        )
    )

    assert result["passed"] is True
    assert result["wired"][0]["endpoint"] == "/api/modules/tickets/list_tickets"


def test_validate_wiring_rejects_generated_file_endpoint_without_module_action() -> None:
    result = asyncio.run(
        validate_wiring_module.validate_wiring(
            {
                "generated_files": {
                    "ui/pages/tickets.yaml": """
name: Tickets
sections:
- id: ticket-table
  config:
    api_endpoint: /api/tickets
""",
                    "modules/tickets/module.yaml": """
module:
  id: tickets
actions:
- id: list_tickets
  handler_method: list_tickets
""",
                }
            }
        )
    )

    assert result["passed"] is False
    assert result["orphaned_pages"][0]["endpoint"] == "/api/tickets"

