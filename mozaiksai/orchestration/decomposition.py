# ==============================================================================
# FILE: orchestration/decomposition.py
# DESCRIPTION: Decomposition strategies for the UniversalOrchestrator.
#
# The orchestrator detects "decomposition points" — moments where a single
# GroupChat should be paused and split into N parallel (or sequential)
# sub-GroupChats.  A DecompositionStrategy encapsulates the detection and
# planning logic.
# ==============================================================================
"""
Decomposition strategy protocol and built-in implementations.

Two built-in strategies ship with core:

* **ConfigDrivenDecomposition** — reads a ``decomposition:`` block from the
  workflow YAML (or ``_pack/workflow_graph.json``) and produces a plan at
  workflow start.  Deterministic — always decomposes the same way.

* **AgentSignalDecomposition** — triggered when an agent emits a
  ``process.decompose_requested`` DomainEvent (or a structured output
  containing a ``PatternSelection``).  Dynamic — decomposition happens
  mid-run based on agent reasoning.

Platform can register additional strategies via the PluginRegistry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ExecutionMode(str, Enum):
    """How sub-tasks should be executed."""
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"


@dataclass(frozen=True)
class SubTask:
    """A single unit of work within a decomposition plan.

    Each sub-task maps to one sub-GroupChat execution.
    """
    workflow_name: str
    initial_message: str | None = None
    initial_agent_override: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Sub-task IDs this sub-task depends on (for DAG ordering).
    depends_on: tuple[str, ...] = ()
    # Optional identifier (auto-generated if omitted).
    task_id: str = ""

    def __post_init__(self) -> None:
        if not self.task_id:
            import uuid
            object.__setattr__(self, "task_id", f"sub_{uuid.uuid4().hex[:8]}")


@dataclass(frozen=True)
class DecompositionPlan:
    """The output of a decomposition strategy.

    Describes *what* sub-tasks to run and *how* to execute them.
    """
    sub_tasks: tuple[SubTask, ...]
    execution_mode: ExecutionMode = ExecutionMode.PARALLEL
    reason: str = ""
    resume_agent: str | None = None
    #: Opaque bag for strategy-specific data the merge step may need.
    strategy_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def task_count(self) -> int:
        return len(self.sub_tasks)


@dataclass
class DecompositionContext:
    """Context passed to a strategy's ``detect`` method.

    Populated by the UniversalOrchestrator from the active run state.
    """
    run_id: str
    workflow_name: str
    app_id: str
    user_id: str
    #: The latest DomainEvent (or raw AG2 event) that might signal decomposition.
    trigger_event: dict[str, Any] | None = None
    #: Workflow YAML config dict (if available).
    workflow_config: dict[str, Any] | None = None
    #: Pack graph config (if available).
    pack_config: dict[str, Any] | None = None
    #: Any runtime context variables currently in scope.
    context_variables: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DecompositionStrategy(Protocol):
    """Pluggable strategy for detecting and planning decomposition."""

    def detect(self, context: DecompositionContext) -> DecompositionPlan | None:
        """Return a DecompositionPlan if the current state warrants decomposition.

        Return ``None`` to indicate "no decomposition needed — continue normally".
        """
        ...


# ---------------------------------------------------------------------------
# Built-in: ConfigDrivenDecomposition
# ---------------------------------------------------------------------------

class ConfigDrivenDecomposition:
    """Reads a ``decomposition`` block from workflow config or pack graph.

    Expected YAML shape (in workflow config)::

        decomposition:
          mode: parallel          # or sequential
          resume_agent: Reviewer
          sub_tasks:
            - workflow: sub_workflow_a
              initial_message: "Handle part A"
            - workflow: sub_workflow_b
              initial_message: "Handle part B"
              depends_on: [sub_workflow_a]   # optional DAG edges

    Or in ``_pack/workflow_graph.json``::

        {
          "nested_chats": [{
            "trigger_agent": "Planner",
            "children": ["sub_a", "sub_b"],
            "resume_agent": "Merger"
          }]
        }
    """

    def detect(self, context: DecompositionContext) -> DecompositionPlan | None:
        # 1. Try workflow YAML decomposition block
        plan = self._from_workflow_config(context)
        if plan:
            return plan
        # 2. Try pack graph nested_chats
        plan = self._from_pack_graph(context)
        if plan:
            return plan
        return None

    # -- private helpers -----------------------------------------------------

    def _from_workflow_config(self, ctx: DecompositionContext) -> DecompositionPlan | None:
        cfg = ctx.workflow_config or {}
        decomp_block = cfg.get("decomposition")
        if not isinstance(decomp_block, dict):
            return None

        raw_tasks = decomp_block.get("sub_tasks") or decomp_block.get("subtasks") or []
        if not isinstance(raw_tasks, list) or not raw_tasks:
            return None

        mode_str = str(decomp_block.get("mode") or "parallel").strip().lower()
        mode = ExecutionMode.SEQUENTIAL if mode_str == "sequential" else ExecutionMode.PARALLEL
        resume_agent = _str_or_none(decomp_block.get("resume_agent"))

        sub_tasks: list[SubTask] = []
        for raw in raw_tasks:
            if not isinstance(raw, dict):
                continue
            wf = _str_or_none(raw.get("workflow") or raw.get("name"))
            if not wf:
                continue
            deps_raw = raw.get("depends_on") or []
            deps = tuple(str(d) for d in deps_raw) if isinstance(deps_raw, list) else ()
            sub_tasks.append(SubTask(
                workflow_name=wf,
                initial_message=_str_or_none(raw.get("initial_message")),
                initial_agent_override=_str_or_none(raw.get("initial_agent")),
                metadata=raw.get("metadata") or {},
                depends_on=deps,
            ))

        if not sub_tasks:
            return None

        return DecompositionPlan(
            sub_tasks=tuple(sub_tasks),
            execution_mode=mode,
            reason="config-driven decomposition",
            resume_agent=resume_agent,
        )

    def _from_pack_graph(self, ctx: DecompositionContext) -> DecompositionPlan | None:
        pack = ctx.pack_config
        if not isinstance(pack, dict):
            return None
        nested = pack.get("nested_chats")
        if not isinstance(nested, list) or not nested:
            return None

        # Use the first nested_chats entry that matches (simple heuristic).
        for entry in nested:
            if not isinstance(entry, dict):
                continue
            children = entry.get("children") or entry.get("workflows") or []
            if not isinstance(children, list) or not children:
                continue

            sub_tasks = []
            for child in children:
                wf = str(child) if isinstance(child, str) else str(child.get("name", "")) if isinstance(child, dict) else ""
                if not wf.strip():
                    continue
                msg = child.get("initial_message") if isinstance(child, dict) else None
                sub_tasks.append(SubTask(
                    workflow_name=wf.strip(),
                    initial_message=msg if isinstance(msg, str) else None,
                ))

            if sub_tasks:
                return DecompositionPlan(
                    sub_tasks=tuple(sub_tasks),
                    execution_mode=ExecutionMode.PARALLEL,
                    reason="pack-graph nested_chats",
                    resume_agent=_str_or_none(entry.get("resume_agent")),
                )

        return None


# ---------------------------------------------------------------------------
# Built-in: AgentSignalDecomposition
# ---------------------------------------------------------------------------

class AgentSignalDecomposition:
    """Triggered by an agent emitting a decomposition signal mid-run.

    The strategy inspects ``context.trigger_event`` for one of:

    1. A ``DomainEvent`` with ``event_type == "process.decompose_requested"``
       whose payload contains ``sub_tasks: [...]``.
    2. A structured output containing a ``PatternSelection`` dict (compatibility
       with the existing ``WorkflowPackCoordinator``).
    """

    #: Event types that trigger decomposition.
    TRIGGER_EVENT_TYPES: frozenset[str] = frozenset({
        "process.decompose_requested",
        "orchestration.decompose",
    })

    def detect(self, context: DecompositionContext) -> DecompositionPlan | None:
        evt = context.trigger_event
        if not isinstance(evt, dict):
            return None

        # Path 1: DomainEvent-based signal
        event_type = evt.get("event_type") or evt.get("type") or ""
        if event_type in self.TRIGGER_EVENT_TYPES:
            return self._from_domain_event(evt, context)

        # Path 2: PatternSelection structured output (backward compat)
        structured = evt.get("structured_data") or evt.get("payload") or {}
        ps = structured.get("PatternSelection") or structured.get("pattern_selection")
        if isinstance(ps, dict) and ps.get("is_multi_workflow"):
            return self._from_pattern_selection(ps, context)

        return None

    # -- private helpers ---------------------------------------------------

    def _from_domain_event(self, evt: dict, ctx: DecompositionContext) -> DecompositionPlan | None:
        payload = evt.get("payload") or {}
        raw_tasks = payload.get("sub_tasks") or payload.get("subtasks") or []
        if not isinstance(raw_tasks, list) or not raw_tasks:
            return None

        mode_str = str(payload.get("mode") or "parallel").strip().lower()
        mode = ExecutionMode.SEQUENTIAL if mode_str == "sequential" else ExecutionMode.PARALLEL

        sub_tasks: list[SubTask] = []
        for raw in raw_tasks:
            if isinstance(raw, str):
                sub_tasks.append(SubTask(workflow_name=raw))
            elif isinstance(raw, dict):
                wf = _str_or_none(raw.get("workflow") or raw.get("name"))
                if wf:
                    sub_tasks.append(SubTask(
                        workflow_name=wf,
                        initial_message=_str_or_none(raw.get("initial_message")),
                        initial_agent_override=_str_or_none(raw.get("initial_agent")),
                        depends_on=tuple(raw.get("depends_on") or []),
                    ))

        if not sub_tasks:
            return None

        return DecompositionPlan(
            sub_tasks=tuple(sub_tasks),
            execution_mode=mode,
            reason=f"agent signal: {evt.get('event_type')}",
            resume_agent=_str_or_none(payload.get("resume_agent")),
        )

    def _from_pattern_selection(self, ps: dict, ctx: DecompositionContext) -> DecompositionPlan | None:
        workflows = ps.get("workflows")
        if not isinstance(workflows, list) or not workflows:
            return None

        sub_tasks: list[SubTask] = []
        for wf in workflows:
            if not isinstance(wf, dict):
                continue
            name = _str_or_none(wf.get("name"))
            if not name:
                continue
            sub_tasks.append(SubTask(
                workflow_name=name,
                initial_message=_str_or_none(wf.get("initial_message")),
                initial_agent_override=_str_or_none(wf.get("initial_agent")),
                metadata=wf.get("metadata") or {},
            ))

        if not sub_tasks:
            return None

        return DecompositionPlan(
            sub_tasks=tuple(sub_tasks),
            execution_mode=ExecutionMode.PARALLEL,
            reason="PatternSelection structured output",
            resume_agent=_str_or_none(ps.get("resume_agent")),
            strategy_metadata={"decomposition_reason": ps.get("decomposition_reason")},
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _str_or_none(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


__all__ = [
    "AgentSignalDecomposition",
    "ConfigDrivenDecomposition",
    "DecompositionContext",
    "DecompositionPlan",
    "DecompositionStrategy",
    "ExecutionMode",
    "SubTask",
]
