from __future__ import annotations

from tests.import_utils import import_module_directly

_a2a = import_module_directly("mozaiksai.core.workflow.agents.a2a")


def test_load_a2a_agent_specs_filters_invalid_and_disabled_entries() -> None:
    workflow_config = {
        "a2a": {
            "agents": [
                {
                    "name": "RemoteWriter",
                    "url": "https://example.com/agents/writer",
                    "max_reconnects": 5,
                    "polling_interval": 1.25,
                    "client": {
                        "streaming": False,
                        "polling": True,
                        "accepted_output_modes": ["text/plain"],
                    },
                },
                {"name": "DisabledRemote", "url": "https://example.com/agents/off", "enabled": False},
                {"name": "MissingUrl"},
                {"url": "https://example.com/agents/missing-name"},
                "not-a-dict",
            ]
        }
    }

    specs = _a2a.load_a2a_agent_specs(workflow_config)

    assert list(specs.keys()) == ["RemoteWriter"]
    spec = specs["RemoteWriter"]
    assert spec.name == "RemoteWriter"
    assert spec.url == "https://example.com/agents/writer"
    assert spec.max_reconnects == 5
    assert spec.polling_interval == 1.25
    assert spec.client.get("streaming") is False
    assert spec.client.get("polling") is True
    assert spec.client.get("accepted_output_modes") == ["text/plain"]


