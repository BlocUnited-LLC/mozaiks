"""AG2-WP-016: the runner asks AG2's adapter again whether a user-bound packet closes.

``_accepted_packet_closes_channel`` calls ``on_accepted`` a second time, outside
the hub's accept, for every packet that hands the turn to the user. Every pause
goes through that call, and so does a packet that AG2 closes the channel on
in the same accept (the user is the next speaker and ``max_turns`` is reached).

The hub applies that close only after its listeners return, and the runner sees
the packet when it is dispatched, before then. Whether the second call or the
``is_terminal()`` early return decides depends on which task runs first. With
the in-memory store the transition always lands first, so ordinary runs never
reach the closing outcome of the second call. These tests hold the hub's
``on_envelope_posted`` hook open until the runner has decided, which forces it.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest
from ag2.network import Hub

import mozaiksai.core.adapters.ag2_network_runner as runner_module
from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.ports.orchestration import RunStatus
from tests.test_ag2_network_execution_alignment import _DeterministicAgent
from tests.test_ag2_network_idle_deadline import _run_on_virtual_clock

RULES = [
    {"source_agent": "Validator", "target_agent": "user", "transition_type": "after_turn",
     "transition_target": "RevertToUserTarget"},
    {"source_agent": "user", "target_agent": "Validator", "transition_type": "after_turn"},
]
# Long enough for any runner decision; on the virtual clock it costs nothing.
GATE_TIMEOUT_SECONDS = 60.0


class _Decisions:
    """What the runner decided for each user-bound packet, and the gate's view of it."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.decided: dict[str, asyncio.Event] = {}
        self.gate_timeouts = 0

    def event_for(self, envelope_id: str) -> asyncio.Event:
        return self.decided.setdefault(envelope_id, asyncio.Event())


@pytest.fixture
def decisions(monkeypatch: pytest.MonkeyPatch) -> _Decisions:
    """Hold every packet's close until the runner decides it, and record each decision."""
    recorded = _Decisions()
    decide = runner_module._accepted_packet_closes_channel

    class _DecisionGatedHub(Hub):
        # Hub._fan_out calls this on the hub itself before any listener, and the
        # hub applies the accept's channel transition only after fan-out returns.
        async def on_envelope_posted(self, envelope: Any, metadata: Any) -> None:
            if str(envelope.event_type) != "ag2.packet":
                return
            try:
                await asyncio.wait_for(
                    recorded.event_for(envelope.envelope_id).wait(), timeout=GATE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                recorded.gate_timeouts += 1

    async def recording_decide(hub: Any, channel_id: str, envelope: Any) -> bool:
        open_on_entry = not (await hub.get_channel(channel_id)).is_terminal()
        state_before = copy.deepcopy(hub.adapter_state(channel_id))
        closes = await decide(hub, channel_id, envelope)
        recorded.records.append({
            "envelope_id": envelope.envelope_id,
            "open_on_entry": open_on_entry,
            "closes": closes,
            "state_unchanged": hub.adapter_state(channel_id) == state_before,
        })
        recorded.event_for(envelope.envelope_id).set()
        return closes

    monkeypatch.setattr(runner_module, "Hub", _DecisionGatedHub)
    monkeypatch.setattr(runner_module, "_accepted_packet_closes_channel", recording_decide)
    return recorded


async def _run(max_turns: int, *, continue_once: bool) -> tuple[list[Any], _DeterministicAgent]:
    validator = _DeterministicAgent("Validator", "Running validation checks.")
    first = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="AcceptRecall", chat_id=f"accept-recall-{max_turns}", app_id="accept-recall-app",
        agents={"Validator": validator}, transition_rules=RULES,
        initial_agent_name="Validator", initial_message="Begin.", max_turns=max_turns,
    ))
    results = [first]
    if continue_once:
        assert first.live_run is not None, first.error
        results.append(await first.live_run.continue_with_user_message("Proceed."))
    for result in results:
        if result.live_run is not None:
            await result.live_run.close()
    return results, validator


@pytest.mark.parametrize(
    ("max_turns", "continue_once"),
    [
        pytest.param(1, False, id="initial-run"),
        pytest.param(3, True, id="continuation"),
    ],
)
def test_a_user_bound_packet_ag2_closes_on_settles_on_the_close_through_the_recall(
    decisions: _Decisions, max_turns: int, continue_once: bool,
) -> None:
    results, validator = _run_on_virtual_clock(_run(max_turns, continue_once=continue_once))

    assert decisions.gate_timeouts == 0
    *pauses, ending = results
    assert [result.status for result in pauses] == [RunStatus.PAUSED] * len(pauses)
    assert ending.status is RunStatus.FAILED, ending
    assert ending.close_reason == "max_turns"
    assert ending.live_run is None
    assert len(validator.ask_calls) == len(results)

    # The closing packet was decided by the re-call while the channel was still
    # open: the is_terminal() early return did not fire, on_accepted said CLOSED.
    *paused_decisions, closing = decisions.records
    assert closing == {
        "envelope_id": closing["envelope_id"], "open_on_entry": True, "closes": True, "state_unchanged": True,
    }
    assert [(d["open_on_entry"], d["closes"]) for d in paused_decisions] == [(True, False)] * len(pauses)
    assert all(d["state_unchanged"] for d in decisions.records)


def test_every_user_bound_pause_is_decided_by_the_recall_without_changing_adapter_state(
    decisions: _Decisions,
) -> None:
    results, validator = _run_on_virtual_clock(_run(4, continue_once=True))

    assert decisions.gate_timeouts == 0
    assert [result.status for result in results] == [RunStatus.PAUSED, RunStatus.PAUSED]
    assert [result.close_reason for result in results] == ["awaiting_user_input"] * 2
    assert len(validator.ask_calls) == 2
    assert [(d["open_on_entry"], d["closes"], d["state_unchanged"]) for d in decisions.records] == [
        (True, False, True),
        (True, False, True),
    ]
