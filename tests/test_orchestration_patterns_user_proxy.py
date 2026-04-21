from __future__ import annotations

from tests.import_utils import import_module_directly

_patterns_mod = import_module_directly("mozaiksai.core.workflow.orchestration_patterns")


def test_ensure_user_proxy_disables_cli_prompts_when_human_loop_is_false(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeUserProxyAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.name = kwargs["name"]

    monkeypatch.setattr(_patterns_mod, "UserProxyAgent", _FakeUserProxyAgent)

    agents, user_proxy, human_in_loop = _patterns_mod._ensure_user_proxy(
        agents={},
        config={"human_in_the_loop": False},
        workflow_startup_mode="UserDriven",
        llm_config={},
        human_in_loop=False,
    )

    assert human_in_loop is False
    assert user_proxy is not None
    assert agents["user"] is user_proxy
    assert captured["human_input_mode"] == "NEVER"