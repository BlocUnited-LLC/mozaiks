from __future__ import annotations

import asyncio

from factory_app.workflows.AgentGenerator.tools import collect_api_keys


class _FakeContextVariables:
    def __init__(self, data):
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def test_collect_api_keys_skips_prompt_when_required_integrations_are_already_ready(monkeypatch) -> None:
    context_variables = _FakeContextVariables(
        {
            "workflow_name": "AgentGenerator",
            "chat_id": "chat-1",
            "app_id": "app-1",
            "action_plan": {
                "phases": [
                    {
                        "agents": [
                            {"integrations": ["Payment Provider", "Email Provider"]},
                        ]
                    }
                ]
            },
        }
    )

    async def fake_inventory(app_id, *, required_services=None):
        return {
            "required_services": ["email_provider", "payment_provider"],
            "ready_services": ["email_provider", "payment_provider"],
            "known_services": ["email_provider", "payment_provider"],
            "missing_required_services": [],
            "known_but_unready_required_services": [],
            "entirely_missing_required_services": [],
            "status_buckets": {"active": ["email_provider", "payment_provider"]},
            "display_names": {"email_provider": "Email Provider", "payment_provider": "Payment Provider"},
            "connectors": [],
        }

    async def fail_request_bundle(**kwargs):
        raise AssertionError("request_api_keys_bundle should not be called when all required connectors are ready")

    monkeypatch.setattr(collect_api_keys, "get_connector_inventory", fake_inventory)
    import sys
    module_name = "factory_app.workflows.AgentGenerator.tools.request_api_key"
    module = type(sys)(module_name)
    module.request_api_keys_bundle = fail_request_bundle
    monkeypatch.setitem(sys.modules, module_name, module)

    result = asyncio.run(collect_api_keys.collect_api_keys_from_action_plan(context_variables))

    assert result["status"] == "already_configured"
    assert sorted(result["services_collected"]) == ["email_provider", "payment_provider"]
    assert context_variables.data["ready_connector_services"] == ["email_provider", "payment_provider"]
    assert context_variables.data["missing_connector_services"] == []


def test_collect_api_keys_uses_shared_connector_bundle(monkeypatch) -> None:
    context_variables = _FakeContextVariables(
        {
            "workflow_name": "AgentGenerator",
            "chat_id": "chat-1",
            "app_id": "app-1",
            "action_plan": {
                "phases": [
                    {
                        "agents": [
                            {"integrations": ["Payment Provider"]},
                        ]
                    }
                ]
            },
        }
    )
    calls = []

    async def fake_inventory(app_id, *, required_services=None):
        if calls:
            return {
                "required_services": ["payment_provider"],
                "ready_services": ["payment_provider"],
                "known_services": ["payment_provider"],
                "missing_required_services": [],
                "known_but_unready_required_services": [],
                "entirely_missing_required_services": [],
                "status_buckets": {"active": ["payment_provider"]},
                "display_names": {"payment_provider": "Payment Provider"},
                "connectors": [],
            }
        return {
            "required_services": list(required_services or []),
            "ready_services": [],
            "known_services": [],
            "missing_required_services": ["payment_provider"],
            "known_but_unready_required_services": [],
            "entirely_missing_required_services": ["payment_provider"],
            "status_buckets": {},
            "display_names": {"payment_provider": "Payment Provider"},
            "connectors": [],
        }

    async def fake_request_bundle(**kwargs):
        calls.append(kwargs)
        return {
            "status": "success",
            "services": [
                {
                    "service": "payment_provider",
                    "display_name": "Payment Provider",
                    "status": "success",
                    "has_key": True,
                    "metadata_saved": True,
                    "key_length": 12,
                }
            ],
            "missing_required": [],
        }

    class FakeTransport:
        async def handle_user_input_from_api(self, **kwargs):
            return None

    async def fake_get_transport_instance():
        return FakeTransport()

    monkeypatch.setattr(collect_api_keys, "get_connector_inventory", fake_inventory)
    monkeypatch.setattr(collect_api_keys, "request_connector_bundle", fake_request_bundle)
    monkeypatch.setattr(collect_api_keys.SimpleTransport, "get_instance", fake_get_transport_instance)

    result = asyncio.run(collect_api_keys.collect_api_keys_from_action_plan(context_variables))

    assert result["status"] == "complete"
    assert result["services_collected"] == ["payment_provider"]
    assert calls[0]["services"][0]["service"] == "payment_provider"
    assert context_variables.data["api_keys_bundle_status"] == "success"
