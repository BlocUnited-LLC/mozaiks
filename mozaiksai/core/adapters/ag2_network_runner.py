"""AG2 1.0 beta Network runner for Mozaiks workflow execution.

This module is the narrow boundary where Mozaiks drives AG2 Network primitives
directly. It does not load workflow YAML or create agents; callers pass already
validated declarations and already constructed AG2 agents.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

from ag2.knowledge import KnowledgeStore, MemoryKnowledgeStore
from ag2.network import (
    EV_CHANNEL_CLOSED,
    EV_CONTEXT_SET,
    EV_PACKET,
    EV_TEXT,
    AgentTarget,
    Envelope,
    Hub,
    HubClient,
    LocalLink,
    Passport,
    Resume,
    Transition,
    TransitionGraph,
    WorkflowAdapter,
)
from ag2.network.client.handlers import default_handler

from mozaiksai.core.adapters.ag2_transition_conditions import BootstrapInitialDispatch
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.context.authority import (
    AGENT_TEXT_WRITER,
    CONTEXT_BRIDGE_WRITER,
    LIVE_USER_CONTEXT_WRITER,
    ContextAuthorityPolicy,
)
from mozaiksai.core.workflow.execution.network_graph import compile_transition_rules_to_graph
from mozaiksai.core.workflow.outputs.runtime_validation import validate_agent_structured_output

_INITIATOR_NAME = "mozaiks_user"


def _json_safe_key(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, Mapping):
        return {
            _json_safe_key(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except TypeError:
            return _json_safe(value.model_dump())
    return str(value)


def _json_safe_dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    serialized = _json_safe(dict(value or {}))
    return serialized if isinstance(serialized, dict) else {}


def _authorized_context_updates(
    updates: Mapping[str, Any] | None,
    *,
    writer_id: str,
    context_authority_policy: ContextAuthorityPolicy | None,
) -> dict[str, Any]:
    if not updates:
        return {}
    safe: dict[str, Any] = {}
    for key, value in updates.items():
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        if context_authority_policy is not None:
            context_authority_policy.require_can_write(clean_key, writer_id=writer_id, operation="set")  # type: ignore[arg-type]
        safe[clean_key] = value
    return safe


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
    agent_text_context_deriver: Callable[[str, str], Mapping[str, Any]] | None = None
    context_authority_policy: ContextAuthorityPolicy | None = None
    # ---------------------------------------------------------------------------
    # AG2 KnowledgeStore injection seam
    # ---------------------------------------------------------------------------
    # Accepts an AG2-native KnowledgeStore instance (ag2.knowledge.KnowledgeStore
    # protocol). When None the runner creates a fresh MemoryKnowledgeStore per
    # Hub — the safe default for local development and test isolation.
    #
    # Lifecycle notes:
    #   - One Hub is created per workflow run (or per live session, kept alive for
    #     paused runs). Hub.close() does NOT close the store; store lifetime is
    #     owned by the caller that constructs it.
    #   - A durable operator-owned store (e.g. SqliteKnowledgeStore or
    #     RedisKnowledgeStore) may be intentionally shared across runs. The caller
    #     is responsible for namespace isolation and lifecycle management.
    #   - Two runs that receive distinct store instances will not share AG2
    #     workflow memory between them.
    #
    # Security notes:
    #   - Injected KnowledgeStore code and storage are trusted operator runtime
    #     configuration. The store can observe AG2 workflow/network memory for
    #     every run that uses it.
    #   - Production credentials (keys, tokens) must not flow into generated app
    #     bundles through this seam; configure the store outside bundle artifacts.
    #   - This seam does not grant production mutation authority over Mozaiks
    #     tenant or platform data; AG2 memory/knowledge controls are engine-level,
    #     not Mozaiks-level authority.
    knowledge_store: KnowledgeStore | None = None


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
    live_run: Any | None = None


class AG2NetworkRunner:
    """Execute one workflow as an AG2 1.0 beta Network workflow channel."""

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
            request.knowledge_store if request.knowledge_store is not None else MemoryKnowledgeStore(),
            ttl_sweep_interval=0,
            expectation_sweep_interval=0,
        )
        turn_failure_listener = _TurnFailureListener()
        hub.register_listener(turn_failure_listener)  # type: ignore[arg-type]
        link = LocalLink(hub)
        hub_clients: list[HubClient] = []
        channel_id: str | None = None
        keep_live_run = False

        try:
            initiator_hc = HubClient(link, hub=hub)
            hub_clients.append(initiator_hc)
            initiator = await initiator_hc.register_human(
                Passport(name=_INITIATOR_NAME),
                resume=Resume(claimed_capabilities=["workflow_start"]),
            )

            agent_clients = {}
            agent_id_by_name: dict[str, str] = {
                _INITIATOR_NAME: initiator.agent_id,
                "user": initiator.agent_id,
            }
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
                _install_context_update_handler(
                    agent=agent,
                    client=client,
                    agent_name=name,
                    agent_text_context_deriver=request.agent_text_context_deriver,
                    context_authority_policy=request.context_authority_policy,
                )
                agent_clients[name] = client
                agent_id_by_name[name] = client.agent_id

            if initial_agent_name not in agent_clients:
                raise ValueError(f"initial agent {initial_agent_name!r} did not register")

            graph = self._compile_graph_with_initiator(
                transition_rules=request.transition_rules,
                initial_agent_name=initial_agent_name,
                agent_id_by_name=agent_id_by_name,
                max_turns=request.max_turns,
                context_authority_policy=request.context_authority_policy,
            )

            safe_context_vars = _json_safe_dict(request.context_variables)

            channel = await initiator.open(
                type="workflow",
                target=[client.agent_id for client in agent_clients.values()],
                knobs={
                    "graph": graph.to_dict(),
                    "context_vars": safe_context_vars,
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
                return {
                    initiator.agent_id: "user",
                    **{client.agent_id: name for name, client in agent_clients.items()},
                }

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
                        context_variables=_json_safe_dict(getattr(state, "context_vars", {}) or {}),
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
                    context_variables=_json_safe_dict(getattr(state, "context_vars", {}) or {}),
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
                        context_variables=_json_safe_dict(getattr(state, "context_vars", {}) or {}),
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
                    result = await _snapshot_result(
                        status=RunStatus.PAUSED,
                        close_reason="awaiting_user_input",
                    )
                    result.live_run = _AG2LiveWorkflowRun(
                        workflow_name=request.workflow_name,
                        chat_id=request.chat_id,
                        app_id=request.app_id,
                        hub=hub,
                        hub_clients=tuple(hub_clients),
                        initiator=initiator,
                        channel=channel,
                        turn_failure_listener=turn_failure_listener,
                        snapshot_result=_snapshot_result,
                        close_timeout_seconds=float(request.close_timeout_seconds or 120.0),
                        context_authority_policy=request.context_authority_policy,
                    )
                    keep_live_run = True
                    return result
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
            if not keep_live_run:
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
        context_authority_policy: ContextAuthorityPolicy | None,
    ) -> TransitionGraph:
        graph = compile_transition_rules_to_graph(
            transition_rules,
            initial_agent_name=initial_agent_name,
            agent_id_by_name=agent_id_by_name,
            max_turns=max_turns,
            context_authority_policy=context_authority_policy,
        )
        initiator_id = agent_id_by_name[_INITIATOR_NAME]
        initial_agent_id = agent_id_by_name[initial_agent_name]
        return TransitionGraph(
            initial_speaker=initiator_id,
            transitions=[
                Transition(
                    when=BootstrapInitialDispatch(
                        source_agent_id=initiator_id,
                    ),
                    then=AgentTarget(initial_agent_id),
                    priority=-1,
                ),
                *graph.transitions,
            ],
            default_target=graph.default_target,
            max_turns=None if graph.max_turns is None else graph.max_turns + 1,
        )


class _AG2LiveWorkflowRun:
    """Process-local handle for an AG2 workflow channel paused on user input."""

    def __init__(
        self,
        *,
        workflow_name: str,
        chat_id: str,
        app_id: str,
        hub: Any,
        hub_clients: Sequence[Any],
        initiator: Any,
        channel: Any,
        turn_failure_listener: _TurnFailureListener,
        snapshot_result: Callable[..., Awaitable[AG2NetworkRunnerResult]],
        close_timeout_seconds: float,
        context_authority_policy: ContextAuthorityPolicy | None,
    ) -> None:
        self.workflow_name = workflow_name
        self.chat_id = chat_id
        self.app_id = app_id
        self.channel_id = str(channel.channel_id)
        self._hub = hub
        self._hub_clients = tuple(hub_clients)
        self._initiator = initiator
        self._channel = channel
        self._turn_failure_listener = turn_failure_listener
        self._snapshot_result: Callable[..., Awaitable[AG2NetworkRunnerResult]] = snapshot_result
        self._close_timeout_seconds = close_timeout_seconds
        self._closed = False
        self._lock = asyncio.Lock()
        self._wal_cursor = 0
        self._context_authority_policy = context_authority_policy

    async def continue_with_user_message(
        self,
        message: str,
        *,
        context_updates: Mapping[str, Any] | None = None,
    ) -> AG2NetworkRunnerResult:
        async with self._lock:
            if self._closed:
                return AG2NetworkRunnerResult(
                    status=RunStatus.FAILED,
                    workflow_name=self.workflow_name,
                    chat_id=self.chat_id,
                    app_id=self.app_id,
                    channel_id=self.channel_id,
                    error="live_ag2_channel_closed",
                )

            prior_wal = await self._hub.read_wal(self.channel_id)
            self._wal_cursor = max(self._wal_cursor, len(prior_wal))
            seen_envelope_ids = {
                str(getattr(envelope, "envelope_id", "") or "")
                for envelope in prior_wal
            }

            if context_updates:
                safe_updates = _authorized_context_updates(
                    context_updates,
                    writer_id=LIVE_USER_CONTEXT_WRITER,
                    context_authority_policy=self._context_authority_policy,
                )
                await self._channel.send(
                    "",
                    event_type=EV_CONTEXT_SET,
                    event_data={"set": _json_safe_dict(safe_updates), "delete": []},
                )
            audience = self._next_agent_audience_for_user_text(str(message or "."))
            await self._channel.send(str(message or "."), audience=audience)

            result = await self._wait_for_settlement(seen_envelope_ids=seen_envelope_ids)
            result.wal = list(result.wal[self._wal_cursor :])
            self._wal_cursor += len(result.wal)
            if result.status is RunStatus.PAUSED:
                result.live_run = self
            else:
                await self.close()
            return result

    async def _wait_for_settlement(
        self,
        *,
        seen_envelope_ids: set[str],
    ) -> AG2NetworkRunnerResult:
        failure_task = asyncio.create_task(
            self._turn_failure_listener.wait_for_failure(self.channel_id)
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._close_timeout_seconds
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    result = await self._snapshot_result(
                        status=RunStatus.PAUSED,
                        close_reason="awaiting_user_input",
                        error=(
                            "workflow channel did not settle within "
                            f"{self._close_timeout_seconds} seconds"
                        ),
                    )
                    result.live_run = self
                    return result

                event_task = asyncio.create_task(
                    self._initiator.wait_for_channel_event(
                        channel_id=self.channel_id,
                        predicate=lambda envelope: envelope.event_type
                        in {EV_CHANNEL_CLOSED, EV_PACKET}
                        and str(getattr(envelope, "envelope_id", "") or "")
                        not in seen_envelope_ids,
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
                    result = await self._snapshot_result(
                        status=RunStatus.FAILED,
                        error=f"AG2 turn failed for {failure['agent_id']}: {failure['error']}",
                    )
                    return result

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
                    return await self._snapshot_result(
                        status=RunStatus.COMPLETED,
                        close_reason=str(event_env.event_data.get("reason") or "") or None,
                    )

                state = self._hub.adapter_state(self.channel_id)
                expected_next_speaker = str(
                    getattr(state, "expected_next_speaker", "") or ""
                ).strip()
                if expected_next_speaker in {"user", self._initiator.agent_id}:
                    return await self._snapshot_result(
                        status=RunStatus.PAUSED,
                        close_reason="awaiting_user_input",
                    )
        finally:
            failure_task.cancel()
            await asyncio.gather(failure_task, return_exceptions=True)

    def _next_agent_audience_for_user_text(self, message: str) -> list[str] | None:
        state = self._hub.adapter_state(self.channel_id)
        next_state = WorkflowAdapter().fold(
            Envelope(
                channel_id=self.channel_id,
                sender_id=self._initiator.agent_id,
                audience=[],
                event_type=EV_TEXT,
                event_data={"text": message},
            ),
            state,
        )
        next_speaker = str(getattr(next_state, "expected_next_speaker", "") or "").strip()
        if not next_speaker:
            # Termination state — send to nobody; the fold closes the channel
            # without any registered agent intercepting the EV_TEXT envelope.
            return []
        if next_speaker in {"user", self._initiator.agent_id}:
            return None
        return [next_speaker]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for hub_client in reversed(self._hub_clients):
            try:
                await hub_client.close()
            except Exception as exc:
                logger.warning(
                    "AG2_LIVE_HUB_CLIENT_CLOSE_FAILED workflow=%s chat=%s: %s",
                    self.workflow_name,
                    self.chat_id,
                    exc,
                )
        try:
            await self._hub.close()
        except Exception as exc:
            logger.warning(
                "AG2_LIVE_HUB_CLOSE_FAILED workflow=%s chat=%s: %s",
                self.workflow_name,
                self.chat_id,
                exc,
            )


def _packet_body_text(event_data: Mapping[str, Any]) -> str:
    body = event_data.get("body")
    if isinstance(body, str):
        return body
    if isinstance(body, (dict, list)):
        return json.dumps(body, ensure_ascii=False, default=str)
    return str(body or "")


def _install_context_update_handler(
    *,
    agent: Any,
    client: Any,
    agent_name: str,
    agent_text_context_deriver: Callable[[str, str], Mapping[str, Any]] | None = None,
    context_authority_policy: ContextAuthorityPolicy | None = None,
) -> None:
    """Commit Mozaiks tool context mutations through AG2 workflow packets.

    AG2 Workflow routing reads ``WorkflowState.context_vars``. Mozaiks tools
    receive ``ContextVariablesBridge`` for ergonomics, so this adapter consumes
    bridge mutations and declarative agent-text state triggers at round end and
    merges them into the outgoing AG2 ``EV_PACKET.context_updates`` payload
    before AG2 selects the next speaker.
    """

    bridge = getattr(agent, "_mozaiks_context_bridge", None)
    has_bridge = (
        bridge is not None
        and callable(getattr(bridge, "clear_context_updates", None))
        and callable(getattr(bridge, "consume_context_updates", None))
    )
    if not has_bridge and agent_text_context_deriver is None:
        return

    async def _handler(envelope: Any) -> None:
        if bridge is not None:
            bridge.clear_context_updates()
        original_send_envelope = client.send_envelope

        async def _send_with_context_updates(out_envelope: Any) -> str:
            if (
                str(getattr(out_envelope, "event_type", "") or "") == EV_PACKET
                and str(getattr(out_envelope, "channel_id", "") or "")
                == str(getattr(envelope, "channel_id", "") or "")
            ):
                bridge_updates = (
                    bridge.consume_context_updates()
                    if bridge is not None
                    else {"set": {}, "delete": []}
                )
                event_data = _json_safe_dict(getattr(out_envelope, "event_data", {}) or {})
                derived_updates: Mapping[str, Any] = {}
                if agent_text_context_deriver is not None:
                    text = _packet_body_text(event_data)
                    if text.strip():
                        try:
                            candidate_updates = agent_text_context_deriver(agent_name, text)
                        except Exception as err:  # pragma: no cover
                            logger.debug(
                                "AG2_AGENT_TEXT_CONTEXT_DERIVE_FAILED agent=%s: %s",
                                agent_name,
                                err,
                            )
                            candidate_updates = {}
                        if isinstance(candidate_updates, Mapping):
                            derived_updates = _authorized_context_updates(
                                candidate_updates,
                                writer_id=AGENT_TEXT_WRITER,
                                context_authority_policy=context_authority_policy,
                            )
                if (
                    bridge_updates.get("set")
                    or bridge_updates.get("delete")
                    or derived_updates
                ):
                    existing = dict(event_data.get("context_updates") or {})
                    bridge_set = _authorized_context_updates(
                        bridge_updates.get("set") or {},
                        writer_id=CONTEXT_BRIDGE_WRITER,
                        context_authority_policy=context_authority_policy,
                    )
                    existing_set = _authorized_context_updates(
                        existing.get("set") or {},
                        writer_id=CONTEXT_BRIDGE_WRITER,
                        context_authority_policy=context_authority_policy,
                    )
                    merged_set = {
                        **_json_safe_dict(existing_set),
                        **_json_safe_dict(derived_updates),
                        **_json_safe_dict(bridge_set),
                    }
                    merged_delete = [
                        *list(existing.get("delete") or []),
                        *list(bridge_updates.get("delete") or []),
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
            if bridge is not None:
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
        "audience": _json_safe(list(getattr(envelope, "audience", None) or [])),
        "event_type": str(getattr(envelope, "event_type", "") or ""),
        "event_data": _json_safe_dict(getattr(envelope, "event_data", {}) or {}),
        "causation_id": _json_safe(getattr(envelope, "causation_id", None)),
    }


def _validate_wal_structured_outputs(
    *,
    wal: Sequence[Any],
    agent_name_by_id: Mapping[str, str],
    structured_registry: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    # AG2 Network WAL packets expose serialized body data, not the original
    # AgentReply. Without that supported AG2 handle this runner cannot invoke
    # AgentReply.content(retries=...) for schema-correction turns. It therefore
    # performs post-hoc structured-output validation and fails the run closed.
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
