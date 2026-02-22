"""Tool auto-invoke policy enforcement."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, kw_only=True)
class ToolPolicyConfig:
    allowlist: set[str] | None = None
    denylist: set[str] = field(default_factory=set)
    max_calls_per_task: int | None = None
    max_calls_per_run: int | None = None


@dataclass(slots=True, kw_only=True)
class ToolPolicyDecisionResult:
    tool_name: str
    decision: str
    reason: str
    allow: bool
    call_index_task: int
    call_index_run: int


class ToolAutoInvokePolicyEngine:
    """Deterministic policy engine for candidate tool calls."""

    def __init__(self) -> None:
        self._run_counts: dict[str, int] = {}
        self._task_counts: dict[tuple[str, str], int] = {}

    def reset_run(self, run_id: str) -> None:
        """Clear policy counters for a run."""
        self._run_counts.pop(run_id, None)
        keys = [key for key in self._task_counts if key[0] == run_id]
        for key in keys:
            self._task_counts.pop(key, None)

    def evaluate(
        self,
        *,
        run_id: str,
        task_id: str,
        tool_name: str,
        config: ToolPolicyConfig,
    ) -> ToolPolicyDecisionResult:
        run_count = self._run_counts.get(run_id, 0)
        task_key = (run_id, task_id)
        task_count = self._task_counts.get(task_key, 0)

        if tool_name in config.denylist:
            return ToolPolicyDecisionResult(
                tool_name=tool_name,
                decision="deny",
                reason="denylist_block",
                allow=False,
                call_index_task=task_count + 1,
                call_index_run=run_count + 1,
            )

        if config.allowlist is not None and tool_name not in config.allowlist:
            return ToolPolicyDecisionResult(
                tool_name=tool_name,
                decision="deny",
                reason="not_in_allowlist",
                allow=False,
                call_index_task=task_count + 1,
                call_index_run=run_count + 1,
            )

        if config.max_calls_per_task is not None and task_count >= config.max_calls_per_task:
            return ToolPolicyDecisionResult(
                tool_name=tool_name,
                decision="deny",
                reason="max_calls_per_task_exceeded",
                allow=False,
                call_index_task=task_count + 1,
                call_index_run=run_count + 1,
            )

        if config.max_calls_per_run is not None and run_count >= config.max_calls_per_run:
            return ToolPolicyDecisionResult(
                tool_name=tool_name,
                decision="deny",
                reason="max_calls_per_run_exceeded",
                allow=False,
                call_index_task=task_count + 1,
                call_index_run=run_count + 1,
            )

        task_next = task_count + 1
        run_next = run_count + 1
        self._task_counts[task_key] = task_next
        self._run_counts[run_id] = run_next
        return ToolPolicyDecisionResult(
            tool_name=tool_name,
            decision="allow",
            reason="allowed",
            allow=True,
            call_index_task=task_next,
            call_index_run=run_next,
        )
