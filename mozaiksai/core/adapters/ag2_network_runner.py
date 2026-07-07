"""AG2 beta Network runner for Mozaiks workflow execution.

This module is the narrow boundary where Mozaiks drives AG2 Network primitives
directly. It does not load workflow YAML or create agents; callers pass already
validated declarations and already constructed AG2 agents.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

from autogen.beta.knowledge import MemoryKnowledgeStore
from autogen.beta.network import (
    EV_CHANNEL_CLOSED,
    EV_PACKET,
    AgentTarget,
    FromSpeaker,
    Hub,
    HubClient,
    LocalLink,
    Passport,
    Resume,
    Transition,
    TransitionGraph,
)
from autogen.beta.network.client.handlers import default_handler

from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.execution.network_graph import compile_transition_rules_to_graph
from mozaiksai.core.workflow.outputs.runtime_validation import validate_agent_structured_output

_INITIATOR_NAME = "mozaiks_user"


@dataclass(slots=True)
class AG2NetworkRunnerRequest:
    """Already-loaded workflow execution inputs for the AG2 Network runner."""

    workflow_name: str
    chat_id: str
    app_id: str
    agents: Mapping[str, Any]
    transition_rules: Sequence[Mapping[str, Any]]
    initial_agent_name: str
    initial_message: str
    context_variables: Mapping[str, Any] = field(default_factory=dict)
    structured_registry: Mapping[str, Any] = field(default_factory=dict)
    max_turns: int | None = None
    close_timeout_seconds: float = 120.0
    attach_network_plugin: bool = True


@dataclass(slots=True)
class AG2NetworkRunnerResult:
    """Serializable summary of one AG2 Network workflow channel run."""

    status: RunStatus
    workflow_name: str
    chat_id: str
    app_id: str
    channel_id: str | None = None
    close_reason: str | None = None
    context_variables: dict[str, Any] = field(default_factory=dict)
    structured_outputs: list[dict[str, Any]] = field(default_factory=list)
    agent_name_by_id: dict[str, str] = field(default_factory=dict)
    wal: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class AG2NetworkRunner:
    """Execute one workflow as an AG2 beta Network workflow channel."""

    async def run(self, request: AG2NetworkRunnerRequest) -> AG2NetworkRunnerResult:
        # Bind workflow-level trace context so all tasks spawned during this
        # run inherit the workflow name and chat ID without parameter threading.
        from mozaiksai.core.tracing.context import bind_trace_id
        bind_trace_id(
            request.chat_id,
            workflow_name=request.workflow_name,
            chat_id=request.chat_id,
            app_id=request.app_id,
        )

        initial_agent_name = str(request.initial_agent_name or "").strip()
        validation_error = self._validate_request(request, initial_agent_name)
        if validation_error:
            return AG2NetworkRunnerResult(
                status=RunStatus.FAILED,
                workflow_name=request.workflow_name,
                chat_id=request.chat_id,
                app_id=request.app_id,
                error=validation_error,
            )

        hub = await Hub.open(
            MemoryKnowledgeStore(),
            ttl_sweep_interval=0,
            expectation_sweep_interval=0,
        )
        turn_failure_listener = _TurnFailureListener()
        hub.register_listener(turn_failure_listener)  # type: ignore[arg-type]
        link = LocalLink(hub)
        hub_clients: list[HubClient] = []
        channel_id: str | None = None

        try:
            initiator_hc = HubClient(link, hub=hub)
            hub_clients.append(initiator_hc)
            initiator = await initiator_hc.register_human(
                Passport(name=_INITIATOR_NAME),
                resume=Resume(claimed_capabilities=["workflow_start"]),
            )

            agent_clients = {}
            agent_id_by_name: dict[str, str] = {_INITIATOR_NAME: initiator.agent_id}
            for agent_name, agent in request.agents.items():
                name = str(agent_name or "").strip()
                if not name:
                    continue
                hub_client = HubClient(link, hub=hub)
                hub_clients.append(hub_client)
                client = await hub_client.register(
                    agent,
                    Passport(name=name),
                    Resume(claimed_capabilities=[name]),
                    attach_plugin=request.attach_network_plugin,
                )
                _install_context_update_handler(agent=agent, client=client)
                agent_clients[name] = client
                agent_id_by_name[name] = client.agent_id

            if initial_agent_name not in agent_clients:
                raise ValueError(f"initial agent {initial_agent_name!r} did not register")

            graph = self._compile_graph_with_initiator(
                transition_rules=request.transition_rules,
                initial_agent_name=initial_agent_name,
                agent_id_by_name=agent_id_by_name,
                max_turns=request.max_turns,
            )

            channel = await initiator.open(
                type="workflow",
                target=[client.agent_id for client in agent_clients.values()],
                knobs={
                    "graph": graph.to_dict(),
                    "context_vars": dict(request.context_variables or {}),
                },
            )
            channel_id = channel.channel_id
            await channel.send(
                request.initial_message or ".",
                audience=[agent_clients[initial_agent_name].agent_id],
            )
            failure_task = asyncio.create_task(
                turn_failure_listener.wait_for_failure(channel.channel_id)
            )

            def _agent_names() -> dict[str, str]:
                return {client.agent_id: name for name, client in agent_clients.items()}

            async def _snapshot_result(
                *,
                status: RunStatus,
                close_reason: str | None = None,
                error: str | None = None,
            ) -> AG2NetworkRunnerResult:
                state = hub.adapter_state(channel.channel_id)
                wal = await hub.read_wal(channel.channel_id)
                agent_name_by_id = _agent_names()
                structured_outputs, validation_error = _validate_wal_structured_outputs(
                    wal=wal,
                    agent_name_by_id=agent_name_by_id,
                    structured_registry=request.structured_registry,
                )
                if validation_error:
                    return AG2NetworkRunnerResult(
                        status=RunStatus.FAILED,
                        workflow_name=request.workflow_name,
                        chat_id=request.chat_id,
                        app_id=request.app_id,
                        channel_id=channel.channel_id,
                        close_reason=close_reason,
                        context_variables=dict(getattr(state, "context_vars", {}) or {}),
                        structured_outputs=structured_outputs,
                        agent_name_by_id=agent_name_by_id,
                        wal=[_envelope_to_dict(envelope) for envelope in wal],
                        error=validation_error,
                    )
                return AG2NetworkRunnerResult(
                    status=status,
                    workflow_name=request.workflow_name,
                    chat_id=request.chat_id,
                    app_id=request.app_id,
                    channel_id=channel.channel_id,
                    close_reason=close_reason,
                    context_variables=dict(getattr(state, "context_vars", {}) or {}),
                    structured_outputs=structured_outputs,
                    agent_name_by_id=agent_name_by_id,
                    wal=[_envelope_to_dict(envelope) for envelope in wal],
                    error=error,
                )

            loop = asyncio.get_running_loop()
            deadline = loop.time() + float(request.close_timeout_seconds or 120.0)

            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError

                event_task = asyncio.create_task(
                    initiator.wait_for_channel_event(
                        channel_id=channel.channel_id,
                        predicate=lambda envelope: envelope.event_type in {EV_CHANNEL_CLOSED, EV_PACKET},
                        timeout=remaining,
                    )
                )

                done, pending = await asyncio.wait(
                    {event_task, failure_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if failure_task in done:
                    event_task.cancel()
                    await asyncio.gather(event_task, return_exceptions=True)
                    failure = failure_task.result()
                    wal = await hub.read_wal(channel.channel_id)
                    state = hub.adapter_state(channel.channel_id)
                    agent_name_by_id = _agent_names()
                    failed_agent = agent_name_by_id.get(
                        failure["agent_id"], failure["agent_id"]
                    )
                    return AG2NetworkRunnerResult(
                        status=RunStatus.FAILED,
                        workflow_name=request.workflow_name,
                        chat_id=request.chat_id,
                        app_id=request.app_id,
                        channel_id=channel.channel_id,
                        context_variables=dict(getattr(state, "context_vars", {}) or {}),
                        agent_name_by_id=agent_name_by_id,
                        wal=[_envelope_to_dict(envelope) for envelope in wal],
                        error=f"AG2 turn failed for {failed_agent}: {failure['error']}",
                    )

                if failure_task in pending:
                    pending.remove(failure_task)

                try:
                    event_env = event_task.result()
                finally:
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)

                if event_env.event_type == EV_CHANNEL_CLOSED:
                    failure_task.cancel()
                    await asyncio.gather(failure_task, return_exceptions=True)
                    return await _snapshot_result(
                        status=RunStatus.COMPLETED,
                        close_reason=str(event_env.event_data.get("reason") or "") or None,
                    )

                state = hub.adapter_state(channel.channel_id)
                expected_next_speaker = str(getattr(state, "expected_next_speaker", "") or "").strip()
                if expected_next_speaker in {"user", initiator.agent_id}:
                    failure_task.cancel()
                    await asyncio.gather(failure_task, return_exceptions=True)
                    return await _snapshot_result(
                        status=RunStatus.PAUSED,
                        close_reason="awaiting_user_input",
                    )
        except TimeoutError:
            wal = await hub.read_wal(channel_id) if channel_id else []
            return AG2NetworkRunnerResult(
                status=RunStatus.PAUSED,
                workflow_name=request.workflow_name,
                chat_id=request.chat_id,
                app_id=request.app_id,
                channel_id=channel_id,
                wal=[_envelope_to_dict(envelope) for envelope in wal],
                error=f"workflow channel did not close within {request.close_timeout_seconds} seconds",
            )
        except Exception as exc:
            wal = await hub.read_wal(channel_id) if channel_id else []
            logger.error(
                "AG2 network run failed workflow=%s chat=%s: %s",
                request.workflow_name,
                request.chat_id,
                exc,
                exc_info=True,
            )
            return AG2NetworkRunnerResult(
                status=RunStatus.FAILED,
                workflow_name=request.workflow_name,
                chat_id=request.chat_id,
                app_id=request.app_id,
                channel_id=channel_id,
                wal=[_envelope_to_dict(envelope) for envelope in wal],
                error="internal_error",
            )
        finally:
            for hub_client in reversed(hub_clients):
                try:
                    await hub_client.close()
                except Exception as _hc_exc:
                    logger.warning(
                        "AG2_HUB_CLIENT_CLOSE_FAILED workflow=%s chat=%s: %s",
                        request.workflow_name,
                        request.chat_id,
                        _hc_exc,
                    )
            try:
                await hub.close()
            except Exception as _hub_exc:
                logger.warning(
                    "AG2_HUB_CLOSE_FAILED workflow=%s chat=%s: %s",
                    request.workflow_name,
                    request.chat_id,
                    _hub_exc,
                )

    @staticmethod
    def _validate_request(request: AG2NetworkRunnerRequest, initial_agent_name: str) -> str | None:
        if not request.agents:
            return "AG2NetworkRunner requires at least one agent"
        if not initial_agent_name:
            return "AG2NetworkRunner requires initial_agent_name"
        if initial_agent_name not in request.agents:
            return f"initial agent {initial_agent_name!r} is not registered"
        return None

    @staticmethod
    def _compile_graph_with_initiator(
        *,
        transition_rules: Sequence[Mapping[str, Any]],
        initial_agent_name: str,
        agent_id_by_name: Mapping[str, str],
        max_turns: int | None,
    ) -> TransitionGraph:
        graph = compile_transition_rules_to_graph(
            transition_rules,
            initial_agent_name=initial_agent_name,
            agent_id_by_name=agent_id_by_name,
            max_turns=max_turns,
        )
        initiator_id = agent_id_by_name[_INITIATOR_NAME]
        initial_agent_id = agent_id_by_name[initial_agent_name]
        return TransitionGraph(
            initial_speaker=initiator_id,
            transitions=[
                Transition(
                    when=FromSpeaker(initiator_id),
                    then=AgentTarget(initial_agent_id),
                    priority=-1,
                ),
                *graph.transitions,
            ],
            default_target=graph.default_target,
            max_turns=None if graph.max_turns is None else graph.max_turns + 1,
        )


def _install_context_update_handler(*, agent: Any, client: Any) -> None:
    """Commit Mozaiks tool context mutations through AG2 workflow packets.

    AG2 Workflow routing reads ``WorkflowState.context_vars``. Mozaiks tools
    receive ``ContextVariablesBridge`` for ergonomics, so this adapter consumes
    bridge mutations at round end and merges them into the outgoing AG2
    ``EV_PACKET.context_updates`` payload before AG2 selects the next speaker.
    """

    bridge = getattr(agent, "_mozaiks_context_bridge", None)
    if bridge is None:
        return
    if not callable(getattr(bridge, "clear_context_updates", None)):
        return
    if not callable(getattr(bridge, "consume_context_updates", None)):
        return

    async def _handler(envelope: Any) -> None:
        bridge.clear_context_updates()
        original_send_envelope = client.send_envelope

        async def _send_with_context_updates(out_envelope: Any) -> str:
            if (
                str(getattr(out_envelope, "event_type", "") or "") == EV_PACKET
                and str(getattr(out_envelope, "channel_id", "") or "")
                == str(getattr(envelope, "channel_id", "") or "")
            ):
                updates = bridge.consume_context_updates()
                if updates.get("set") or updates.get("delete"):
                    event_data = dict(getattr(out_envelope, "event_data", {}) or {})
                    existing = dict(event_data.get("context_updates") or {})
                    merged_set = {
                        **dict(existing.get("set") or {}),
                        **dict(updates.get("set") or {}),
                    }
                    merged_delete = [
                        *list(existing.get("delete") or []),
                        *list(updates.get("delete") or []),
                    ]
                    event_data["context_updates"] = {
                        "set": merged_set,
                        "delete": list(dict.fromkeys(str(item) for item in merged_delete)),
                    }
                    out_envelope.event_data = event_data
            return await original_send_envelope(out_envelope)  # type: ignore[no-any-return]

        client.send_envelope = _send_with_context_updates
        try:
            await default_handler(envelope, client)
        finally:
            client.send_envelope = original_send_envelope
            bridge.clear_context_updates()

    client.on_envelope(_handler)


class _TurnFailureListener:
    """Capture AG2 turn failures so the Mozaiks runner can fail promptly."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._failures: list[dict[str, str]] = []

    async def on_turn_failed(
        self,
        channel_id: str,
        agent_id: str,
        envelope_id: str,
        exc: BaseException,
    ) -> None:
        self._failures.append(
            {
                "channel_id": str(channel_id or ""),
                "agent_id": str(agent_id or ""),
                "envelope_id": str(envelope_id or ""),
                "error": str(exc),
            }
        )
        self._event.set()

    async def wait_for_failure(self, channel_id: str) -> dict[str, str]:
        channel_key = str(channel_id or "")
        while True:
            for failure in self._failures:
                if failure["channel_id"] == channel_key:
                    return failure
            await self._event.wait()
            self._event.clear()


def _envelope_to_dict(envelope: Any) -> dict[str, Any]:
    return {
        "envelope_id": str(getattr(envelope, "envelope_id", "") or ""),
        "channel_id": str(getattr(envelope, "channel_id", "") or ""),
        "sender_id": str(getattr(envelope, "sender_id", "") or ""),
        "audience": list(getattr(envelope, "audience", None) or []),
        "event_type": str(getattr(envelope, "event_type", "") or ""),
        "event_data": dict(getattr(envelope, "event_data", {}) or {}),
        "causation_id": getattr(envelope, "causation_id", None),
    }


def _validate_wal_structured_outputs(
    *,
    wal: Sequence[Any],
    agent_name_by_id: Mapping[str, str],
    structured_registry: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    structured_outputs: list[dict[str, Any]] = []
    if not structured_registry:
        return structured_outputs, None

    for envelope in wal:
        if str(getattr(envelope, "event_type", "") or "") != EV_PACKET:
            continue
        agent_name = agent_name_by_id.get(str(getattr(envelope, "sender_id", "") or ""))
        if not agent_name:
            continue
        event_data = getattr(envelope, "event_data", {}) or {}
        if not isinstance(event_data, Mapping):
            continue
        validation = validate_agent_structured_output(
            agent_name=agent_name,
            reply=event_data.get("body"),
            structured_registry=structured_registry,
        )
        if validation is None:
            continue
        if not validation.validation_passed or validation.structured_data is None:
            return structured_outputs, (
                f"structured output validation failed for {agent_name}: {validation.error}"
            )
        structured_outputs.append(
            {
                "agent": agent_name,
                "model_name": validation.model_name,
                "structured_data": validation.structured_data,
            }
        )
    return structured_outputs, None


__all__ = [
    "AG2NetworkRunner",
    "AG2NetworkRunnerRequest",
    "AG2NetworkRunnerResult",
]
