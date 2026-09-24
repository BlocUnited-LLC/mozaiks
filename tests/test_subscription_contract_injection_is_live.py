"""The subscription contract injector must read the context agents actually have.

`inject_subscription_contract_context` is registered three times in
AppGenerator's middleware.yaml and renders `[SUBSCRIPTION CONTRACT CONTEXT]`
for AppPlanAgent, AppSchemaAgent, ConfigMiddlewareAgent, PatternAgent and
WorkflowBundleBuilderAgent. It resolves the contract from either
`subscription_contract` or `subscription_contract_artifact`, which is exactly
the two-source lookup the planning prompts were later patched to ask the agent
to perform by hand.

It has never run. `_context_data` read `getattr(context, "data", None)`, and
#300 renamed the bridge's backing store to `__data` specifically to stop
callers reaching past the authority policy. On every live build it returned
`{}`, `_find_contract` saw nothing, and the hook injected nothing -- silently,
because an empty context is indistinguishable from an app with no contract.

Two acceptance runs at OSS 95a2a584 and 6b1dd2f9 show the cost. Both had:

    subscription_contract          => None
    subscription_contract_artifact => {... 'contract_required': True ...}
    "SUBSCRIPTION CONTRACT" mentions in the whole server log: 0

The first declared a `subscription_config` task; the second did not, on the
same code. That is what a coin flip looks like: #716 had asked the agent to do
the resolution itself, because the deterministic path that should have handed
it a resolved contract was dead.

Fourth instance of one root cause: code reading live workflow context through
an accessor that only works on a plain dict, with tests that pass because they
pass plain dicts. See #708 (`isinstance(..., dict)`), #712 (`_context_get`
without `detach`), and the `.data` reads swept there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from factory_app.workflows._shared.subscription_contract_context import (
    _context_data,
    _find_contract,
    inject_subscription_contract_context,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge

ROOT = Path(__file__).resolve().parents[1]

CONTRACT = {
    "contract_required": True,
    "subscription_config_file": {"plans": [{"plan_id": "pro", "label": "Pro", "capabilities": ["x"]}]},
}
# The live shape: state carries nothing, the artifact carries the contract.
LIVE_CONTEXT = {
    "subscription_contract": None,
    "subscription_contract_artifact": {"metadata": {"summary_payload": CONTRACT}},
}


class _Agent:
    """Enough of an agent for the hook: it appends to the system message."""

    def __init__(self, context: Any, name: str = "AppPlanAgent") -> None:
        self.context_variables = context
        self.name = name
        self.system_message = "You plan app builds."

    def update_system_message(self, value: str) -> None:
        self.system_message = value


def test_the_injector_reads_a_real_bridge() -> None:
    """The regression: this returned {} on every live build."""
    data = _context_data(_Agent(ContextVariablesBridge(dict(LIVE_CONTEXT))))
    assert set(data) >= {"subscription_contract", "subscription_contract_artifact"}, (
        "the hook read context.data, which the bridge does not expose; it saw an empty "
        "context and injected nothing on every real run"
    )


def test_the_contract_resolves_from_the_artifact_through_a_bridge() -> None:
    """End to end through the container production uses, not a dict stand-in."""
    data = _context_data(_Agent(ContextVariablesBridge(dict(LIVE_CONTEXT))))
    resolved = _find_contract(data)
    assert resolved is not None, "state is null and the artifact holds the contract"
    assert resolved["contract_required"] is True


def test_a_plain_dict_context_still_works() -> None:
    """Direct-dict callers must keep working; this widens the accessor, not narrows it."""
    data = _context_data(_Agent(dict(LIVE_CONTEXT)))
    assert _find_contract(data) is not None


def test_no_contract_still_injects_nothing() -> None:
    """The fix must not start injecting an empty section for unmonetized apps."""
    bridge = ContextVariablesBridge({"subscription_contract": None, "subscription_contract_artifact": None})
    assert _find_contract(_context_data(_Agent(bridge))) is None


def test_the_hook_emits_the_section_for_a_planner() -> None:
    """The observable symptom: zero 'SUBSCRIPTION CONTRACT' lines in two live runs.

    The hook mutates `messages` in place, so the assertion is on what the agent
    would actually have been shown.
    """
    agent = _Agent(ContextVariablesBridge(dict(LIVE_CONTEXT)))
    inject_subscription_contract_context(agent, [])
    rendered = agent.system_message
    assert "SUBSCRIPTION CONTRACT" in rendered, (
        "the section the planning prompts point at must actually render; two live runs "
        "contained zero occurrences of it"
    )
    assert "pro" in rendered, "and it must carry the contract, not an empty header"


def test_an_unmonetized_planner_gets_no_section() -> None:
    """The fix must not start injecting a section where there is no contract."""
    agent = _Agent(ContextVariablesBridge({"subscription_contract": None, "subscription_contract_artifact": None}))
    messages: list[dict[str, Any]] = [{"role": "user", "content": "plan the app"}]
    inject_subscription_contract_context(agent, messages)
    assert "SUBSCRIPTION CONTRACT" not in "\n".join(
        str(message.get("content") or "") for message in messages
    )


def test_a_non_target_agent_gets_no_section() -> None:
    """Only the declared planner/builder agents receive it."""
    agent = _Agent(ContextVariablesBridge(dict(LIVE_CONTEXT)), name="SomeOtherAgent")
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]
    inject_subscription_contract_context(agent, messages)
    assert "SUBSCRIPTION CONTRACT" not in "\n".join(
        str(message.get("content") or "") for message in messages
    )


def test_the_hook_is_still_registered() -> None:
    """Guard the dependency: a fixed accessor is useless if nothing calls it."""
    middleware = yaml.safe_load(
        (ROOT / "factory_app/workflows/AppGenerator/middleware.yaml").read_text(encoding="utf-8")
    )
    assert "inject_subscription_contract_context" in str(middleware), (
        "AppGenerator must still register the injector the planning prompts rely on"
    )
