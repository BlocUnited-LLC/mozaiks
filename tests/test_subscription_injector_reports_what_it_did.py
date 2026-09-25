"""A prompt hook that never speaks cannot be verified from a live run.

`inject_subscription_contract_context` read an attribute the live context
container does not expose and injected nothing on every build until #718. It
was silent about both outcomes, so nothing in any server log distinguished
"this app has no subscription contract" from "this hook cannot read context".

Two acceptance runs after the fix still could not answer "did it fire?".
Grepping the log for the rendered section returns nothing either way, because
agent system messages are never logged:

    [OUTPUT FORMAT]         0 occurrences
    [CAPABILITY DIRECTORY]  0 occurrences
    [SUBSCRIPTION CONTRACT CONTEXT]  0 occurrences

Absence of the marker proved nothing, and a case-insensitive grep matched only
prose inside the contract's own rationale -- which was briefly misread as
confirmation that the hook had run.

So the hook now reports both outcomes, and the skip line carries the number of
context keys it managed to read. That is what separates the two skips:

    skipped ... context_keys=2  subscription_contract=null artifact=null   real: unmonetized
    skipped ... context_keys=0                                             broken reader

`context_keys=0` is the exact signature of the #718 defect. Only a test can
catch a dead reader before a build; only a log line can catch it during one.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from factory_app.workflows._shared.subscription_contract_context import (
    inject_subscription_contract_context,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge

CONTRACT = {
    "contract_required": True,
    "subscription_config_file": {"plans": [{"plan_id": "free"}, {"plan_id": "pro"}]},
}
LIVE_CONTEXT = {
    "subscription_contract": None,
    "subscription_contract_artifact": {"metadata": {"summary_payload": CONTRACT}},
}


class _Agent:
    def __init__(self, context: Any, name: str = "AppPlanAgent") -> None:
        self.context_variables = context
        self.name = name
        self.system_message = "base"

    def update_system_message(self, value: str) -> None:
        self.system_message = value


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "SUBSCRIPTION_CONTRACT_CONTEXT" in r.getMessage()]


def test_an_injection_is_announced(caplog: pytest.LogCaptureFixture) -> None:
    """The run evidence that has been missing: proof the section reached an agent."""
    with caplog.at_level(logging.INFO):
        inject_subscription_contract_context(_Agent(ContextVariablesBridge(dict(LIVE_CONTEXT))), [])
    logged = _lines(caplog)
    assert logged, "a live run must be able to show this hook fired"
    assert "injected" in logged[0]
    assert "agent=AppPlanAgent" in logged[0]
    assert "plans=2" in logged[0], "carry enough to tell a real contract from an empty one"


def test_a_genuinely_unmonetized_app_says_so(caplog: pytest.LogCaptureFixture) -> None:
    bridge = ContextVariablesBridge({"subscription_contract": None, "subscription_contract_artifact": None})
    with caplog.at_level(logging.INFO):
        inject_subscription_contract_context(_Agent(bridge), [])
    logged = _lines(caplog)
    assert logged and "skipped" in logged[0]
    assert "context_keys=2" in logged[0], (
        "an unmonetized app still read its context; the key count is what separates it "
        "from a reader that cannot see anything"
    )


def test_a_dead_reader_is_distinguishable_from_no_contract(caplog: pytest.LogCaptureFixture) -> None:
    """The #718 signature. This is the line that would have caught it during a build."""

    class _Unreadable:
        name = "AppPlanAgent"
        system_message = "base"
        context_variables = None

    with caplog.at_level(logging.INFO):
        inject_subscription_contract_context(_Unreadable(), [])
    logged = _lines(caplog)
    assert logged and "context_keys=0" in logged[0], (
        "a reader that sees nothing must look different in the log from an app that "
        "simply has no contract; those were indistinguishable while the hook was dead"
    )


def test_a_non_target_agent_is_not_announced(caplog: pytest.LogCaptureFixture) -> None:
    """Five agents receive this section; the rest should not add log noise."""
    with caplog.at_level(logging.INFO):
        inject_subscription_contract_context(
            _Agent(ContextVariablesBridge(dict(LIVE_CONTEXT)), name="SomeOtherAgent"), []
        )
    assert not _lines(caplog)


def test_the_injection_still_happens() -> None:
    """Logging must not have displaced the behaviour it reports on."""
    agent = _Agent(ContextVariablesBridge(dict(LIVE_CONTEXT)))
    inject_subscription_contract_context(agent, [])
    assert "SUBSCRIPTION CONTRACT" in agent.system_message
