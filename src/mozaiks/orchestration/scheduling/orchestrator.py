"""Vendor-neutral orchestrator implementation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, AsyncIterator

from ..domain.dag import DAGValidator, TaskDAG, TaskNode
from ..domain.events import (
    CanonicalEvent,
    MessageDelta,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunResumed,
    RunRunning,
    RunStarted,
    StructuredOutputInvalid,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    TaskRetrying,
    TaskStarted,
    ToolCallCandidate,
    ToolCalled,
    ToolError as ToolErrorEvent,
    ToolPolicyDecision,
    ToolResult as ToolResultEvent,
    UIToolCompleted,
    UIToolFailed,
    UIToolInputSubmitted,
    UIToolRequested,
)
from ..domain.runtime_context import RuntimeContext
from ..domain.state import RunCheckpoint, RunExecutionState, RunPhase, TaskStatus
from ..domain.tool_spec import ToolCall, ToolSpec
from ..interfaces.agent_factory import AgentFactory
from ..interfaces.ports import CheckpointStorePort, EventSinkPort
from ..interfaces.tool_binder import ToolBinder
from ..interfaces.vendor_event_adapter import VendorEventAdapter
from ..tools import parse_auto_tool_call
from ..tools.policy_engine import ToolAutoInvokePolicyEngine, ToolPolicyConfig
from ..tools.structured_output import StructuredOutputEnforcer
from ..tools.ui_flow import is_ui_blocking
from .resume import CheckpointCoordinator
from .scheduler import DeterministicScheduler
from .state_machine import OrchestratorStateMachine


class AwaitingUIToolInput(RuntimeError):
    """Raised to pause deterministic execution until UI input is submitted."""

    def __init__(self, *, ui_tool_id: str, tool_name: str) -> None:
        self.ui_tool_id = ui_tool_id
        self.tool_name = tool_name
        super().__init__(f"awaiting_ui_tool_input:{tool_name}:{ui_tool_id}")


class DefaultOrchestrator:
    """Coordinates DAG scheduling, agent execution, tool calls, and resume."""

    def __init__(
        self,
        *,
        agent_factory: AgentFactory,
        tool_binder: ToolBinder,
        vendor_event_adapter: VendorEventAdapter,
        event_sink: EventSinkPort,
        checkpoint_store: CheckpointStorePort,
        scheduler: DeterministicScheduler | None = None,
        tool_catalog: dict[str, ToolSpec] | None = None,
        max_parallel_tasks: int = 1,
        checkpoint_coordinator: CheckpointCoordinator | None = None,
        tool_policy_engine: ToolAutoInvokePolicyEngine | None = None,
        default_tool_policy: ToolPolicyConfig | None = None,
        structured_output_enforcer: StructuredOutputEnforcer | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._tool_binder = tool_binder
        self._event_sink = event_sink
        self._checkpoint_store = checkpoint_store
        self._vendor_event_adapter = vendor_event_adapter
        self._scheduler = scheduler or DeterministicScheduler()
        self._tool_catalog = tool_catalog or {}
        self._checkpoint_coordinator = checkpoint_coordinator or CheckpointCoordinator(
            checkpoint_store
        )
        self._tool_policy_engine = tool_policy_engine or ToolAutoInvokePolicyEngine()
        self._default_tool_policy = default_tool_policy or ToolPolicyConfig()
        self._structured_output_enforcer = structured_output_enforcer or StructuredOutputEnforcer()
        self._dag_validator = DAGValidator()
        self._lifecycle = OrchestratorStateMachine()
        self._max_parallel_tasks = max_parallel_tasks
        self._cancelled_runs: set[str] = set()

    async def cancel(self, run_id: str) -> None:
        self._cancelled_runs.add(run_id)

    async def start(
        self,
        plan: TaskDAG,
        run_id: str,
        initial_input: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> AsyncIterator[CanonicalEvent]:
        self._dag_validator.validate_or_raise(plan)
        self._cancelled_runs.discard(run_id)
        self._tool_policy_engine.reset_run(run_id)

        state = self._checkpoint_coordinator.create_initial_state(
            plan=plan,
            run_id=run_id,
            initial_input=initial_input,
            metadata=metadata,
        )
        state.awaiting_ui_input = False

        started = RunStarted(run_id=state.run_id, input_payload=state.initial_input)
        yield await self._record(started)
        await self._checkpoint_coordinator.save(plan=plan, state=state)

        self._lifecycle.transition_run(state, RunPhase.RUNNING)
        yield await self._record(RunRunning(run_id=state.run_id))

        async for event in self._drive(
            plan=plan,
            state=state,
            runtime_context=runtime_context,
        ):
            yield event

    async def resume(
        self,
        plan: TaskDAG,
        checkpoint: RunCheckpoint,
        runtime_context: RuntimeContext | None = None,
    ) -> AsyncIterator[CanonicalEvent]:
        self._dag_validator.validate_or_raise(plan)
        self._cancelled_runs.discard(checkpoint.run_id)

        state = self._checkpoint_coordinator.from_checkpoint(
            plan=plan,
            checkpoint=checkpoint,
        )
        state.awaiting_ui_input = False

        resumed = RunResumed(run_id=state.run_id, checkpoint_id=checkpoint.checkpoint_id)
        yield await self._record(resumed)
        yield await self._record(RunRunning(run_id=state.run_id))

        async for event in self._drive(
            plan=plan,
            state=state,
            runtime_context=runtime_context,
        ):
            yield event

    async def _drive(
        self,
        *,
        plan: TaskDAG,
        state: RunExecutionState,
        runtime_context: RuntimeContext | None,
    ) -> AsyncIterator[CanonicalEvent]:
        while True:
            if state.cancel_requested or state.run_id in self._cancelled_runs:
                state.cancel_requested = True
                cancelled_task_ids = self._mark_pending_tasks_cancelled(state)
                for task_id in cancelled_task_ids:
                    cancelled_task = TaskCancelled(
                        run_id=state.run_id,
                        task_id=task_id,
                    )
                    yield await self._record(cancelled_task)
                self._lifecycle.transition_run(state, RunPhase.CANCELLED)
                cancelled = RunCancelled(run_id=state.run_id)
                yield await self._record(cancelled)
                await self._checkpoint_coordinator.save(plan=plan, state=state)
                return

            if self._is_run_complete(state):
                self._lifecycle.transition_run(state, RunPhase.COMPLETED)
                completed = RunCompleted(
                    run_id=state.run_id,
                    outputs=self._collect_outputs(plan, state),
                )
                yield await self._record(completed)
                await self._checkpoint_coordinator.save(plan=plan, state=state)
                return

            schedule = self._scheduler.schedule(
                plan,
                state.task_states,
                max_parallel_tasks=self._max_parallel_tasks,
            )

            if not schedule.ready_tasks:
                self._lifecycle.transition_run(state, RunPhase.FAILED)
                if schedule.failed_tasks:
                    reason = "One or more tasks failed permanently."
                else:
                    blocked = ",".join(sorted(schedule.blocked_tasks))
                    reason = (
                        "No schedulable tasks remain. "
                        f"Blocked: {blocked or 'none'}"
                    )
                failed = RunFailed(run_id=state.run_id, error=reason)
                yield await self._record(failed)
                await self._checkpoint_coordinator.save(plan=plan, state=state)
                return

            for node in schedule.ready_tasks:
                async for event in self._execute_task(
                    plan=plan,
                    node=node,
                    state=state,
                    runtime_context=runtime_context,
                ):
                    yield event

                if state.awaiting_ui_input:
                    return

                if state.cancel_requested or state.run_id in self._cancelled_runs:
                    break

                if state.task_states[node.task_id].phase == TaskStatus.FAILED:
                    self._lifecycle.transition_run(state, RunPhase.FAILED)
                    failed = RunFailed(
                        run_id=state.run_id,
                        error=f"Task '{node.task_id}' failed permanently.",
                    )
                    yield await self._record(failed)
                    await self._checkpoint_coordinator.save(plan=plan, state=state)
                    return

    async def _execute_task(
        self,
        *,
        plan: TaskDAG,
        node: TaskNode,
        state: RunExecutionState,
        runtime_context: RuntimeContext | None,
    ) -> AsyncIterator[CanonicalEvent]:
        task_state = state.task_states[node.task_id]
        self._lifecycle.transition_task(task_state, TaskStatus.RUNNING)
        task_state.attempts += 1

        task_input = self._build_task_input(plan=plan, node=node, state=state)
        task_state.inputs = deepcopy(task_input)

        started = TaskStarted(
            run_id=state.run_id,
            task_id=node.task_id,
            task_type=node.task_type,
            inputs=task_input,
        )
        yield await self._record(started)

        try:
            await self._bind_tools_for_task(node)
            outputs: dict[str, Any] = {}
            agent = await self._agent_factory.create(node.agent)

            async for vendor_event in agent.run(node, task_input):
                normalized_events = self._vendor_event_adapter.normalize(
                    vendor_event,
                    run_id=state.run_id,
                    task_id=node.task_id,
                )
                for canonical_event in normalized_events:
                    yield await self._record(canonical_event)

                    if isinstance(canonical_event, MessageDelta):
                        outputs["message"] = canonical_event.delta
                        auto_call = parse_auto_tool_call(
                            canonical_event.delta,
                            task_id=node.task_id,
                        )
                        if auto_call is not None:
                            candidate = ToolCallCandidate(
                                run_id=state.run_id,
                                task_id=node.task_id,
                                tool_name=auto_call.tool_name,
                                arguments=auto_call.arguments,
                                source="message_auto_invoke",
                            )
                            async for tool_event in self._handle_tool_candidate(
                                run_id=state.run_id,
                                task_id=node.task_id,
                                node=node,
                                candidate=candidate,
                                emit_candidate_event=True,
                                emit_called_event=True,
                                runtime_context=runtime_context,
                            ):
                                if (
                                    isinstance(tool_event, ToolResultEvent)
                                    or isinstance(tool_event, UIToolCompleted)
                                ) and tool_event.success:
                                    outputs[
                                        f"tool:{tool_event.tool_name}"
                                    ] = tool_event.output
                                yield tool_event

                    if isinstance(canonical_event, ToolCallCandidate):
                        async for tool_event in self._handle_tool_candidate(
                            run_id=state.run_id,
                            task_id=node.task_id,
                            node=node,
                            candidate=canonical_event,
                            emit_candidate_event=False,
                            emit_called_event=True,
                            runtime_context=runtime_context,
                        ):
                            if (
                                isinstance(tool_event, ToolResultEvent)
                                or isinstance(tool_event, UIToolCompleted)
                            ) and tool_event.success:
                                outputs[f"tool:{tool_event.tool_name}"] = tool_event.output
                            yield tool_event

                    if isinstance(canonical_event, ToolCalled):
                        candidate = ToolCallCandidate(
                            run_id=state.run_id,
                            task_id=node.task_id,
                            tool_name=canonical_event.tool_name,
                            arguments=canonical_event.arguments,
                            source="adapter_tool_called",
                        )
                        async for tool_event in self._handle_tool_candidate(
                            run_id=state.run_id,
                            task_id=node.task_id,
                            node=node,
                            candidate=candidate,
                            emit_candidate_event=False,
                            emit_called_event=False,
                            runtime_context=runtime_context,
                        ):
                            if (
                                isinstance(tool_event, ToolResultEvent)
                                or isinstance(tool_event, UIToolCompleted)
                            ) and tool_event.success:
                                outputs[f"tool:{tool_event.tool_name}"] = tool_event.output
                            yield tool_event

                    if isinstance(canonical_event, ToolResultEvent):
                        outputs[f"tool:{canonical_event.tool_name}"] = canonical_event.output

            schema = self._task_output_schema(node)
            if schema is not None:
                validation = self._structured_output_enforcer.validate(
                    payload=outputs,
                    schema=schema,
                )
                if not validation.valid:
                    self._lifecycle.transition_task(task_state, TaskStatus.FAILED)
                    task_state.error = "structured_output_invalid"
                    invalid_event = StructuredOutputInvalid(
                        run_id=state.run_id,
                        task_id=node.task_id,
                        errors=validation.errors,
                        schema=schema,
                    )
                    yield await self._record(invalid_event)
                    failed = TaskFailed(
                        run_id=state.run_id,
                        task_id=node.task_id,
                        attempt=task_state.attempts,
                        error=task_state.error,
                    )
                    yield await self._record(failed)
                    await self._checkpoint_coordinator.save(plan=plan, state=state)
                    return
                outputs = validation.normalized_output or outputs

            self._lifecycle.transition_task(task_state, TaskStatus.COMPLETED)
            task_state.outputs = outputs
            task_state.error = None

            completed = TaskCompleted(
                run_id=state.run_id,
                task_id=node.task_id,
                outputs=outputs,
            )
            yield await self._record(completed)
            await self._checkpoint_coordinator.save(plan=plan, state=state)

        except AwaitingUIToolInput as exc:
            self._lifecycle.transition_task(task_state, TaskStatus.PENDING)
            task_state.error = str(exc)
            state.awaiting_ui_input = True
            await self._checkpoint_coordinator.save(plan=plan, state=state)

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            task_state.error = error

            if task_state.attempts <= node.max_retries:
                self._lifecycle.transition_task(task_state, TaskStatus.PENDING)
                retry = TaskRetrying(
                    run_id=state.run_id,
                    task_id=node.task_id,
                    attempt=task_state.attempts,
                    reason=error,
                )
                yield await self._record(retry)
            else:
                self._lifecycle.transition_task(task_state, TaskStatus.FAILED)
                failed = TaskFailed(
                    run_id=state.run_id,
                    task_id=node.task_id,
                    attempt=task_state.attempts,
                    error=error,
                )
                yield await self._record(failed)

            await self._checkpoint_coordinator.save(plan=plan, state=state)

    async def _handle_tool_candidate(
        self,
        *,
        run_id: str,
        task_id: str,
        node: TaskNode,
        candidate: ToolCallCandidate,
        emit_candidate_event: bool,
        emit_called_event: bool,
        runtime_context: RuntimeContext | None,
    ) -> AsyncIterator[CanonicalEvent]:
        if emit_candidate_event:
            yield await self._record(candidate)

        policy_config = self._resolve_policy_for_task(node)
        decision = self._tool_policy_engine.evaluate(
            run_id=run_id,
            task_id=task_id,
            tool_name=candidate.tool_name,
            config=policy_config,
        )

        decision_event = ToolPolicyDecision(
            run_id=run_id,
            task_id=task_id,
            tool_name=candidate.tool_name,
            decision=decision.decision,
            reason=decision.reason,
            call_index_task=decision.call_index_task,
            call_index_run=decision.call_index_run,
        )
        yield await self._record(decision_event)

        if not decision.allow:
            return

        call = ToolCall(
            tool_name=candidate.tool_name,
            arguments=candidate.arguments,
            task_id=task_id,
        )
        async for tool_event in self._invoke_tool(
            run_id=run_id,
            task_id=task_id,
            call=call,
            emit_called_event=emit_called_event,
            runtime_context=runtime_context,
        ):
            yield tool_event

    async def _invoke_tool(
        self,
        *,
        run_id: str,
        task_id: str,
        call: ToolCall,
        emit_called_event: bool,
        runtime_context: RuntimeContext | None,
    ) -> AsyncIterator[CanonicalEvent]:
        result = await self._tool_binder.invoke(
            call,
            run_id=run_id,
            runtime_context=runtime_context,
        )
        event_tool_id = str(result.metadata.get("tool_id", call.tool_name))
        event_execution_mode = str(
            result.metadata.get("execution_mode", "in_process")
        )
        is_ui_tool = self._is_ui_tool_result(result.metadata)
        ui_tool_id = str(result.metadata.get("ui_tool_id") or event_tool_id)
        ui_component = self._ui_component(result.metadata)
        ui_mode = self._ui_mode(result.metadata)
        ui_blocking = self._ui_blocking(result.metadata)
        awaiting_input = self._awaiting_ui_input(result.metadata)
        ui_submission_received = self._ui_submission_received(result.metadata)
        if emit_called_event:
            if is_ui_tool:
                called_event = UIToolRequested(
                    run_id=run_id,
                    task_id=task_id,
                    tool_name=call.tool_name,
                    ui_tool_id=ui_tool_id,
                    tool_id=event_tool_id,
                    execution_mode=event_execution_mode,
                    blocking=ui_blocking,
                    awaiting_input=awaiting_input,
                    arguments=call.arguments,
                    component=ui_component,
                    mode=ui_mode,
                )
            else:
                called_event = ToolCalled(
                    run_id=run_id,
                    task_id=task_id,
                    tool_name=call.tool_name,
                    tool_id=event_tool_id,
                    execution_mode=event_execution_mode,
                    arguments=call.arguments,
                )
            yield await self._record(called_event)

        if is_ui_tool and ui_submission_received:
            submitted_event = UIToolInputSubmitted(
                run_id=run_id,
                task_id=task_id,
                tool_name=call.tool_name,
                ui_tool_id=ui_tool_id,
                tool_id=event_tool_id,
                execution_mode=event_execution_mode,
                input_payload=self._ui_submission_payload(result.metadata),
                component=ui_component,
                mode=ui_mode,
            )
            yield await self._record(submitted_event)

        if is_ui_tool and awaiting_input:
            raise AwaitingUIToolInput(
                ui_tool_id=ui_tool_id,
                tool_name=call.tool_name,
            )

        if result.success:
            if is_ui_tool:
                result_event = UIToolCompleted(
                    run_id=run_id,
                    task_id=task_id,
                    tool_name=result.tool_name,
                    ui_tool_id=ui_tool_id,
                    tool_id=event_tool_id,
                    execution_mode=event_execution_mode,
                    success=True,
                    output=result.output,
                    error=None,
                    component=ui_component,
                    mode=ui_mode,
                    metadata=result.metadata,
                )
            else:
                result_event = ToolResultEvent(
                    run_id=run_id,
                    task_id=task_id,
                    tool_name=result.tool_name,
                    tool_id=event_tool_id,
                    execution_mode=event_execution_mode,
                    success=True,
                    output=result.output,
                    error=None,
                    metadata=result.metadata,
                )
            yield await self._record(result_event)
            return

        if is_ui_tool:
            error_event = UIToolFailed(
                run_id=run_id,
                task_id=task_id,
                tool_name=result.tool_name,
                ui_tool_id=ui_tool_id,
                tool_id=event_tool_id,
                execution_mode=event_execution_mode,
                error=result.error or "tool_execution_failed",
                component=ui_component,
                mode=ui_mode,
                metadata=result.metadata,
            )
        else:
            error_event = ToolErrorEvent(
                run_id=run_id,
                task_id=task_id,
                tool_name=result.tool_name,
                tool_id=event_tool_id,
                execution_mode=event_execution_mode,
                error=result.error or "tool_execution_failed",
                metadata=result.metadata,
            )
        yield await self._record(error_event)

    def _is_ui_tool_result(self, metadata: dict[str, Any]) -> bool:
        raw_ui_flag = metadata.get("is_ui_tool")
        if isinstance(raw_ui_flag, bool):
            return raw_ui_flag
        if isinstance(raw_ui_flag, str):
            return raw_ui_flag.strip().lower() in {"1", "true", "yes", "on"}
        raw_tool_type = metadata.get("tool_type")
        if raw_tool_type is not None:
            normalized = str(raw_tool_type).strip().lower().replace("-", "_")
            if normalized in {"ui_tool", "ui"}:
                return True
        return False

    def _ui_blocking(self, metadata: dict[str, Any]) -> bool:
        raw_blocking = metadata.get("ui_blocking")
        if raw_blocking is None:
            raw_ui = metadata.get("ui")
            if isinstance(raw_ui, dict):
                raw_blocking = raw_ui.get("blocking")
        return is_ui_blocking(raw_blocking)

    def _awaiting_ui_input(self, metadata: dict[str, Any]) -> bool:
        raw_value = metadata.get("awaiting_input")
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on"}
        return False

    def _ui_submission_received(self, metadata: dict[str, Any]) -> bool:
        raw_value = metadata.get("ui_submission_received")
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on"}
        return False

    def _ui_submission_payload(self, metadata: dict[str, Any]) -> dict[str, Any]:
        raw_payload = metadata.get("ui_submission")
        if isinstance(raw_payload, dict):
            return dict(raw_payload)
        return {}

    def _ui_component(self, metadata: dict[str, Any]) -> str | None:
        raw_component = metadata.get("ui_component")
        if raw_component is None:
            raw_ui = metadata.get("ui")
            if isinstance(raw_ui, dict):
                raw_component = raw_ui.get("component")
        if raw_component is None:
            return None
        return str(raw_component)

    def _ui_mode(self, metadata: dict[str, Any]) -> str | None:
        raw_mode = metadata.get("ui_mode")
        if raw_mode is None:
            raw_ui = metadata.get("ui")
            if isinstance(raw_ui, dict):
                raw_mode = raw_ui.get("mode")
        if raw_mode is None:
            return None
        return str(raw_mode)

    async def _bind_tools_for_task(self, node: TaskNode) -> None:
        if not node.tools:
            await self._tool_binder.bind([])
            return

        specs = [
            self._tool_catalog.get(
                name,
                ToolSpec(name=name, description=f"Tool {name}", auto_invoke=True),
            )
            for name in node.tools
        ]
        await self._tool_binder.bind(specs)

    def _resolve_policy_for_task(self, node: TaskNode) -> ToolPolicyConfig:
        policy = ToolPolicyConfig(
            allowlist=set(self._default_tool_policy.allowlist)
            if self._default_tool_policy.allowlist is not None
            else None,
            denylist=set(self._default_tool_policy.denylist),
            max_calls_per_task=self._default_tool_policy.max_calls_per_task,
            max_calls_per_run=self._default_tool_policy.max_calls_per_run,
        )

        if node.tools:
            task_tools = set(node.tools)
            if policy.allowlist is None:
                policy.allowlist = task_tools
            else:
                policy.allowlist = policy.allowlist.intersection(task_tools)

        raw_policy = node.metadata.get("tool_policy")
        if not isinstance(raw_policy, dict):
            return policy

        allowlist = raw_policy.get("allowlist")
        if isinstance(allowlist, (list, tuple, set)):
            allow_set = {str(name) for name in allowlist}
            if policy.allowlist is None:
                policy.allowlist = allow_set
            else:
                policy.allowlist = policy.allowlist.intersection(allow_set)

        denylist = raw_policy.get("denylist")
        if isinstance(denylist, (list, tuple, set)):
            policy.denylist.update(str(name) for name in denylist)

        max_calls_per_task = raw_policy.get("max_calls_per_task")
        if isinstance(max_calls_per_task, int):
            policy.max_calls_per_task = max_calls_per_task

        max_calls_per_run = raw_policy.get("max_calls_per_run")
        if isinstance(max_calls_per_run, int):
            policy.max_calls_per_run = max_calls_per_run

        return policy

    def _task_output_schema(self, node: TaskNode) -> dict[str, Any] | None:
        schema = node.metadata.get("output_schema")
        if isinstance(schema, dict):
            return schema
        return None

    def _mark_pending_tasks_cancelled(self, state: RunExecutionState) -> list[str]:
        cancelled_task_ids: list[str] = []
        for task_id in sorted(state.task_states):
            task_state = state.task_states[task_id]
            if task_state.phase in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                continue
            if task_state.phase != TaskStatus.CANCELLED:
                self._lifecycle.transition_task(task_state, TaskStatus.CANCELLED)
            task_state.cancel_requested = True
            cancelled_task_ids.append(task_id)
        return cancelled_task_ids

    def _is_run_complete(self, state: RunExecutionState) -> bool:
        return all(
            task.phase == TaskStatus.COMPLETED for task in state.task_states.values()
        )

    def _collect_outputs(
        self,
        plan: TaskDAG,
        state: RunExecutionState,
    ) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        for task_id in plan.get_leaf_tasks():
            outputs[task_id] = state.task_states[task_id].outputs
        return outputs

    def _build_task_input(
        self,
        *,
        plan: TaskDAG,
        node: TaskNode,
        state: RunExecutionState,
    ) -> dict[str, Any]:
        task_input: dict[str, Any] = dict(state.initial_input)
        task_input.update(node.inputs)
        if node.prompt is not None:
            task_input["prompt"] = node.prompt

        for edge in plan.edges:
            if edge.to_task_id != node.task_id:
                continue

            source_outputs = state.task_states[edge.from_task_id].outputs
            if edge.output_key:
                if edge.output_key in source_outputs:
                    key = edge.input_key or edge.output_key
                    task_input[key] = source_outputs[edge.output_key]
            else:
                task_input.update(source_outputs)

        return task_input

    async def _record(self, event: CanonicalEvent) -> CanonicalEvent:
        await self._event_sink.append(event)
        return event
