"""Task DAG primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .agent_spec import AgentSpec


@dataclass(slots=True, kw_only=True)
class TaskNode:
    """A single task in the execution graph."""

    task_id: str
    task_type: str
    prompt: str | None = None
    inputs: dict[str, object] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    priority: int = 0
    max_retries: int = 0
    agent: AgentSpec | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class TaskEdge:
    """A dependency edge from one task output to another task input."""

    from_task_id: str
    to_task_id: str
    output_key: str | None = None
    input_key: str | None = None


@dataclass(slots=True, kw_only=True)
class TaskDAG:
    """Directed acyclic graph of tasks."""

    nodes: list[TaskNode]
    edges: list[TaskEdge] = field(default_factory=list)
    dag_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, object] = field(default_factory=dict)

    def get_node(self, task_id: str) -> TaskNode | None:
        for node in self.nodes:
            if node.task_id == task_id:
                return node
        return None

    def get_dependencies(self, task_id: str) -> list[str]:
        return [edge.from_task_id for edge in self.edges if edge.to_task_id == task_id]

    def get_dependents(self, task_id: str) -> list[str]:
        return [edge.to_task_id for edge in self.edges if edge.from_task_id == task_id]

    def get_root_tasks(self) -> list[str]:
        dependent_targets = {edge.to_task_id for edge in self.edges}
        return [node.task_id for node in self.nodes if node.task_id not in dependent_targets]

    def get_leaf_tasks(self) -> list[str]:
        upstream_sources = {edge.from_task_id for edge in self.edges}
        return [node.task_id for node in self.nodes if node.task_id not in upstream_sources]


class DAGValidationError(ValueError):
    """Raised when a DAG fails structural validation."""


class DAGValidator:
    """Validates graph references and acyclicity."""

    def validate_or_raise(self, dag: TaskDAG) -> None:
        node_ids = {node.task_id for node in dag.nodes}
        if len(node_ids) != len(dag.nodes):
            raise DAGValidationError("Duplicate task_id values are not allowed.")

        for edge in dag.edges:
            if edge.from_task_id not in node_ids:
                raise DAGValidationError(
                    f"Edge references unknown source task '{edge.from_task_id}'."
                )
            if edge.to_task_id not in node_ids:
                raise DAGValidationError(
                    f"Edge references unknown target task '{edge.to_task_id}'."
                )

        self._assert_acyclic(dag)

    def _assert_acyclic(self, dag: TaskDAG) -> None:
        adjacency: dict[str, list[str]] = {node.task_id: [] for node in dag.nodes}
        for edge in dag.edges:
            adjacency[edge.from_task_id].append(edge.to_task_id)

        unvisited, visiting, visited = 0, 1, 2
        state: dict[str, int] = {node.task_id: unvisited for node in dag.nodes}
        stack: list[str] = []

        def visit(task_id: str) -> None:
            if state[task_id] == visited:
                return
            if state[task_id] == visiting:
                start = stack.index(task_id)
                cycle = " -> ".join(stack[start:] + [task_id])
                raise DAGValidationError(f"Cycle detected: {cycle}")

            state[task_id] = visiting
            stack.append(task_id)
            for child in adjacency[task_id]:
                visit(child)
            stack.pop()
            state[task_id] = visited

        for node in dag.nodes:
            if state[node.task_id] == unvisited:
                visit(node.task_id)
