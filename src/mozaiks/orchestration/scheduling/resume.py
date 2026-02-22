"""Run state initialization and checkpoint restoration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..domain.agent_spec import AgentSpec
from ..domain.dag import TaskDAG, TaskEdge, TaskNode
from ..domain.state import (
    RunCheckpoint,
    RunExecutionState,
    RunPhase,
    TaskExecutionState,
    TaskStatus,
)
from ..interfaces.ports import CheckpointStorePort

CHECKPOINT_VERSION = "2.0.0-phase2a"


def serialize_dag(plan: TaskDAG) -> dict[str, Any]:
    """Serialize a task DAG to a checkpoint-stable payload."""
    return {
        "dag_id": plan.dag_id,
        "metadata": deepcopy(plan.metadata),
        "nodes": [
            {
                "task_id": node.task_id,
                "task_type": node.task_type,
                "prompt": node.prompt,
                "inputs": deepcopy(node.inputs),
                "tools": list(node.tools),
                "priority": node.priority,
                "max_retries": node.max_retries,
                "agent": _serialize_agent_spec(node.agent),
                "metadata": deepcopy(node.metadata),
            }
            for node in plan.nodes
        ],
        "edges": [
            {
                "from_task_id": edge.from_task_id,
                "to_task_id": edge.to_task_id,
                "output_key": edge.output_key,
                "input_key": edge.input_key,
            }
            for edge in plan.edges
        ],
    }


def deserialize_dag(snapshot: dict[str, Any]) -> TaskDAG:
    """Restore a task DAG from serialized checkpoint payload."""
    nodes = [
        TaskNode(
            task_id=str(node["task_id"]),
            task_type=str(node["task_type"]),
            prompt=_safe_optional_string(node.get("prompt")),
            inputs=dict(node.get("inputs", {})),
            tools=tuple(str(tool) for tool in node.get("tools", [])),
            priority=int(node.get("priority", 0)),
            max_retries=int(node.get("max_retries", 0)),
            agent=_deserialize_agent_spec(node.get("agent")),
            metadata=dict(node.get("metadata", {})),
        )
        for node in snapshot.get("nodes", [])
    ]
    edges = [
        TaskEdge(
            from_task_id=str(edge["from_task_id"]),
            to_task_id=str(edge["to_task_id"]),
            output_key=_safe_optional_string(edge.get("output_key")),
            input_key=_safe_optional_string(edge.get("input_key")),
        )
        for edge in snapshot.get("edges", [])
    ]
    return TaskDAG(
        nodes=nodes,
        edges=edges,
        dag_id=str(snapshot.get("dag_id") or "restored"),
        metadata=dict(snapshot.get("metadata", {})),
    )


class CheckpointCoordinator:
    """Maps between in-memory run state and persisted checkpoint snapshots."""

    def __init__(
        self,
        checkpoint_store: CheckpointStorePort,
        *,
        checkpoint_version: str = CHECKPOINT_VERSION,
    ) -> None:
        self._checkpoint_store = checkpoint_store
        self._checkpoint_version = checkpoint_version

    def create_initial_state(
        self,
        *,
        plan: TaskDAG,
        run_id: str,
        initial_input: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RunExecutionState:
        tasks = {node.task_id: TaskExecutionState() for node in plan.nodes}
        return RunExecutionState(
            run_id=run_id,
            dag_id=plan.dag_id,
            phase=RunPhase.STARTED,
            task_states=tasks,
            initial_input=dict(initial_input or {}),
            metadata=dict(metadata or {}),
        )

    async def save(
        self,
        *,
        plan: TaskDAG,
        state: RunExecutionState,
        checkpoint_key: str = "latest",
    ) -> RunCheckpoint:
        checkpoint = self.to_checkpoint(
            plan=plan,
            state=state,
            checkpoint_key=checkpoint_key,
        )
        await self._checkpoint_store.save(checkpoint)
        return checkpoint

    async def load(self, run_id: str) -> RunCheckpoint | None:
        return await self._checkpoint_store.load(run_id)

    def to_checkpoint(
        self,
        *,
        plan: TaskDAG,
        state: RunExecutionState,
        checkpoint_key: str = "latest",
    ) -> RunCheckpoint:
        return RunCheckpoint(
            run_id=state.run_id,
            dag_id=state.dag_id,
            checkpoint_version=self._checkpoint_version,
            checkpoint_key=checkpoint_key,
            run_phase=state.phase,
            task_states={
                task_id: _copy_task_state(task_state)
                for task_id, task_state in state.task_states.items()
            },
            initial_input=deepcopy(state.initial_input),
            cancel_requested=state.cancel_requested,
            dag_snapshot=serialize_dag(plan),
            metadata=deepcopy(state.metadata),
        )

    def from_checkpoint(
        self,
        *,
        plan: TaskDAG,
        checkpoint: RunCheckpoint,
    ) -> RunExecutionState:
        if checkpoint.checkpoint_version != self._checkpoint_version:
            raise ValueError(
                "Unsupported checkpoint version "
                f"'{checkpoint.checkpoint_version}', expected '{self._checkpoint_version}'."
            )

        restored_tasks: dict[str, TaskExecutionState] = {}
        for node in plan.nodes:
            checkpoint_state = checkpoint.task_states.get(node.task_id)
            if checkpoint_state is None:
                restored_tasks[node.task_id] = TaskExecutionState()
                continue

            restored_state = _copy_task_state(checkpoint_state)
            if restored_state.phase in (TaskStatus.RUNNING, TaskStatus.CANCELLED):
                # Resume treats interrupted/cancelled in-flight tasks as pending deterministic retries.
                restored_state.phase = TaskStatus.PENDING
            restored_tasks[node.task_id] = restored_state

        return RunExecutionState(
            run_id=checkpoint.run_id,
            dag_id=plan.dag_id,
            phase=RunPhase.RUNNING,
            task_states=restored_tasks,
            initial_input=deepcopy(checkpoint.initial_input),
            metadata=deepcopy(checkpoint.metadata),
            cancel_requested=False,
        )


def _copy_task_state(task_state: TaskExecutionState) -> TaskExecutionState:
    return TaskExecutionState(
        phase=task_state.phase,
        attempts=task_state.attempts,
        inputs=deepcopy(task_state.inputs),
        outputs=deepcopy(task_state.outputs),
        error=task_state.error,
        cancel_requested=task_state.cancel_requested,
    )


def _serialize_agent_spec(spec: AgentSpec | None) -> dict[str, Any] | None:
    if spec is None:
        return None
    return {
        "name": spec.name,
        "system_prompt": spec.system_prompt,
        "model": spec.model,
        "metadata": deepcopy(spec.metadata),
    }


def _deserialize_agent_spec(raw: object) -> AgentSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return AgentSpec()
    return AgentSpec(
        name=str(raw.get("name") or "default-agent"),
        system_prompt=str(raw.get("system_prompt") or "You are a helpful assistant."),
        model=_safe_optional_string(raw.get("model")),
        metadata=dict(raw.get("metadata", {})),
    )


def _safe_optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
