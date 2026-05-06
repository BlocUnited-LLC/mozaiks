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
                            {"integrations": ["Stripe", "SendGrid"]},
                        ]
                    }
                ]
            },
        }
    )

    async def fake_inventory(app_id, *, required_services=None):
        return {
            "required_services": ["sendgrid", "stripe"],
            "ready_services": ["sendgrid", "stripe"],
            "known_services": ["sendgrid", "stripe"],
            "missing_required_services": [],
            "known_but_unready_required_services": [],
            "entirely_missing_required_services": [],
            "status_buckets": {"active": ["sendgrid", "stripe"]},
            "display_names": {"sendgrid": "SendGrid", "stripe": "Stripe"},
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
    assert sorted(result["services_collected"]) == ["sendgrid", "stripe"]
    assert context_variables.data["ready_connector_services"] == ["sendgrid", "stripe"]
    assert context_variables.data["missing_connector_services"] == []
