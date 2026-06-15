from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.workflow.generator_support import connector_request

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Context:
    def __init__(self, data: dict):
        self.data = dict(data)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value


@pytest.mark.asyncio
async def test_collect_missing_connector_needs_prompts_and_persists(monkeypatch) -> None:
    ui_calls = []
    metadata_calls = []
    store_calls = []

    async def fake_inventory(app_id: str, required_services=None, store=None):
        del store
        ready = []
        if store_calls:
            ready = [call["service"] for call in store_calls]
        required = list(required_services or [])
        return {
            "required_services": required,
            "ready_services": ready,
            "known_services": ready,
            "missing_required_services": sorted(set(required) - set(ready)),
            "known_but_unready_required_services": [],
            "entirely_missing_required_services": sorted(set(required) - set(ready)),
            "status_buckets": {"active": ready},
            "display_names": {"email_provider": "Email Provider"},
            "connectors": [],
            "app_id": app_id,
        }

    async def fake_use_ui_tool(component, payload, *, chat_id=None, workflow_name=None):
        ui_calls.append(
            {
                "component": component,
                "payload": payload,
                "chat_id": chat_id,
                "workflow_name": workflow_name,
            }
        )
        return {
            "status": "submitted",
                "ui_event_id": "evt-email",
                "data": {
                    "status": "submitted",
                    "submissionTime": "2026-05-15T00:00:00Z",
                    "services": [
                        {
                            "service": "email_provider",
                            "fields": {
                                "api_key": "secret-inline-value",
                                "sender_domain": "example.test",
                            },
                        }
                    ],
                },
            }

    async def fake_record_connector_metadata(**kwargs):
        metadata_calls.append(kwargs)
        return {"saved": True, "connector": {"service": kwargs["service"], "secret_available": False}}

    async def fake_store_connector(**kwargs):
        store_calls.append(kwargs)
        return {
            "success": True,
            "metadata_saved": True,
            "record": {"service": kwargs["service"], "secret_available": True},
        }

    monkeypatch.setattr(connector_request, "get_connector_inventory", fake_inventory)
    monkeypatch.setattr(connector_request, "use_ui_tool", fake_use_ui_tool)
    monkeypatch.setattr(connector_request, "record_connector_metadata", fake_record_connector_metadata)
    monkeypatch.setattr(connector_request, "store_connector", fake_store_connector)

    context = _Context(
        {
            "workflow_name": "AppGenerator",
            "chat_id": "chat-test",
            "app_id": "app-test",
            "user_id": "user-test",
            "app_build_plan": {
                "external_integrations": [
                    {
                        "name": "Email Provider",
                        "service": "email_provider",
                        "kind": "api_key",
                        "purpose": "Transactional email delivery",
                        "required_at": "validation_time",
                        "required_fields": [
                            {
                                "name": "api_key",
                                "label": "API Key",
                                "type": "secret",
                                "required": True,
                                "frontend_safe": False,
                            },
                            {
                                "name": "sender_domain",
                                "label": "Sender Domain",
                                "type": "text",
                                "required": True,
                                "frontend_safe": True,
                            },
                        ],
                    }
                ]
            },
        }
    )

    result = await connector_request.collect_missing_connector_needs(context_variables=context)

    assert result["status"] == "ready"
    assert result["unresolved_required_services"] == []
    assert context.data["integration_readiness_status"] == "ready"
    assert context.data["ready_connector_services"] == ["email_provider"]
    assert ui_calls[0]["component"] == "AgentAPIKeysBundleInput"
    payload = ui_calls[0]["payload"]
    assert payload["event_type"] == "integration.required"
    assert payload["integrations_url"] == "/apps/app-test/integrations"
    assert payload["integration_requests"][0]["event_type"] == "integration.required"
    assert payload["integration_requests"][0]["secret_fields"][0]["name"] == "api_key"
    assert payload["integration_requests"][0]["non_secret_fields"][0]["name"] == "sender_domain"
    assert "secret-inline-value" not in repr(payload)
    assert metadata_calls[0]["service"] == "email_provider"
    assert metadata_calls[0]["provider"] == "email_provider"
    assert metadata_calls[0]["integration_id"] == "email_provider"
    assert metadata_calls[0]["public_config"] == {"sender_domain": "example.test"}
    assert store_calls[0]["secret_value"] == "secret-inline-value"
    assert store_calls[0]["provider"] == "email_provider"
    assert store_calls[0]["integration_id"] == "email_provider"
    assert store_calls[0]["public_config"] == {"sender_domain": "example.test"}
    assert "secret-inline-value" not in repr(result)


@pytest.mark.asyncio
async def test_collect_missing_connector_needs_reuses_ready_connectors(monkeypatch) -> None:
    async def fake_inventory(app_id: str, required_services=None, store=None):
        del app_id, store
        required = list(required_services or [])
        return {
            "required_services": required,
            "ready_services": ["analytics_provider"],
            "known_services": ["analytics_provider"],
            "missing_required_services": [],
            "status_buckets": {"active": ["analytics_provider"]},
            "display_names": {"analytics_provider": "Analytics Provider"},
            "connectors": [{"service": "analytics_provider", "secret_available": True}],
        }

    async def fail_use_ui_tool(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("ready connector should not prompt inline")

    monkeypatch.setattr(connector_request, "get_connector_inventory", fake_inventory)
    monkeypatch.setattr(connector_request, "use_ui_tool", fail_use_ui_tool)

    context = _Context(
        {
            "workflow_name": "AppGenerator",
            "app_id": "app-test",
            "app_build_plan": {
                "capability_packs": [
                    {
                        "capability_pack_id": "notifications",
                        "required_integrations": ["analytics_provider"],
                    }
                ]
            },
        }
    )

    result = await connector_request.collect_missing_connector_needs(context_variables=context)

    assert result["status"] == "ready"
    assert result["request_result"] is None
    assert context.data["ready_connector_services"] == ["analytics_provider"]


def test_collect_integration_needs_dedupes_task_batch_results() -> None:
    context = _Context(
        {
            "app_build_plan": {
                "build_tasks": [
                    {
                        "task_id": "payments",
                        "integration_needs": [
                            {
                                "service": "payment_provider",
                                "purpose": "Payment adapter validation",
                                "required_at": "validation_time",
                            }
                        ],
                    }
                ]
            },
            "app_task_batch_results": {
                "task_results": {
                    "payments_task": {
                        "integration_needs": [
                            {
                                "service": "Payment Provider",
                                "purpose": "Webhook verification",
                                "required_at": "runtime",
                            }
                        ]
                    },
                    "email_task": {
                        "integration_needs": [
                            {
                                "service": "email_provider",
                                "purpose": "Email delivery",
                                "required_at": "runtime",
                            }
                        ]
                    },
                }
            },
        }
    )

    needs = connector_request.collect_integration_needs(context)

    assert [need["service"] for need in needs] == ["email_provider", "payment_provider"]
    payment = next(need for need in needs if need["service"] == "payment_provider")
    assert payment["required_at"] == "validation_time"
    assert len(payment["required_by"]) == 2


def test_collect_integration_needs_supports_structured_hosted_pack_requirements() -> None:
    context = _Context(
        {
            "app_build_plan": {
                "capability_packs": [
                    {
                        "capability_pack_id": "mozaikspay",
                        "capability_source": "hosted_pack",
                        "required_integrations": [
                            {
                                "service": "mozaikspay",
                                "provider": "mozaikspay",
                                "display_name": "MozaiksPay",
                                "kind": "api_key",
                                "purpose": "Connect to hosted subscriptions.",
                                "required_at": "runtime",
                                "required_fields": [
                                    {"name": "api_base", "type": "url", "frontend_safe": True},
                                    {"name": "client_id", "type": "text", "frontend_safe": True},
                                    {"name": "client_secret", "type": "secret", "frontend_safe": False},
                                ],
                            }
                        ],
                    }
                ]
            }
        }
    )

    needs = connector_request.collect_integration_needs(context)

    assert [need["service"] for need in needs] == ["mozaikspay"]
    need = needs[0]
    assert need["display_name"] == "MozaiksPay"
    assert need["provider"] == "mozaikspay"
    assert {field["name"] for field in need["required_fields"]} == {
        "api_base",
        "client_id",
        "client_secret",
    }

    payload = connector_request.build_integration_request_payload(
        services=needs,
        agent_message_id="request-mozaikspay",
        context_variables=context,
    )[0]
    assert [field["name"] for field in payload["secret_fields"]] == ["client_secret"]
    assert {field["name"] for field in payload["non_secret_fields"]} == {"api_base", "client_id"}


def test_build_integration_request_payload_is_frontend_safe() -> None:
    payload = connector_request.build_integration_request_payload(
        services=[
            {
                "service": "storage_provider",
                "display_name": "Storage Provider",
                "purpose": "Store generated files.",
                "required_fields": [
                    {"name": "access_token", "type": "secret", "frontend_safe": False},
                    {"name": "bucket_url", "type": "url", "frontend_safe": True},
                ],
            }
        ],
        agent_message_id="request-1",
        context_variables=_Context(
            {
                "workflow_name": "AppGenerator",
                "run_id": "run-1",
                "current_build_task_id": "task-storage",
            }
        ),
    )

    request = payload[0]
    assert request["event_type"] == "integration.required"
    assert request["integration_id"] == "storage_provider"
    assert request["secret_fields"] == [
        {
            "name": "access_token",
            "label": "Access Token",
            "type": "secret",
            "required": True,
            "frontend_safe": False,
        }
    ]
    assert request["non_secret_fields"][0]["name"] == "bucket_url"
    assert request["resume"]["workflow_name"] == "AppGenerator"
    assert "secret-value" not in repr(request)


def test_integrations_route_ownership_is_app_scoped() -> None:
    admin_registry = (REPO_ROOT / "factory_app/app/admin/admin_registry.yaml").read_text(encoding="utf-8")
    route_manifest = (REPO_ROOT / "factory_app/app/ui/route_manifest.json").read_text(encoding="utf-8")
    admin_index = (REPO_ROOT / "factory_app/app/admin/index.js").read_text(encoding="utf-8")
    docs = (REPO_ROOT / "docs/architecture/app/integrations-workflow.md").read_text(encoding="utf-8")

    assert "path: /integrations" not in admin_registry
    assert "path: /apps/:appId/integrations" in admin_registry
    assert '"/apps/:appId/integrations"' in route_manifest
    assert '"/integrations"' not in route_manifest
    assert "registerComponent('AppIntegrationsPage'" in admin_index
    assert "integration.required" in docs
    assert "/apps/{appId}/integrations" in docs
    assert "bare workspace `/integrations` route is intentionally not a first-class" in docs
    assert "Secret fields are write-only" in docs


def test_appgenerator_guidance_declares_generic_integration_requests() -> None:
    agents = (REPO_ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8")
    structured_outputs = (
        REPO_ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
    ).read_text(encoding="utf-8")

    assert "integration.required" in agents
    assert "required_fields" in structured_outputs
    assert "frontend_safe" in structured_outputs
    assert "email_provider" in structured_outputs
    assert "analytics_provider" in structured_outputs

