# ==============================================================================
# FILE: orchestration/merge.py
# DESCRIPTION: Merge strategies for the UniversalOrchestrator.
#
# After parallel (or sequential) sub-GroupChats complete, the orchestrator
# must merge their results into a single context for the parent GroupChat
# to resume.  A MergeStrategy encapsulates that logic.
# ==============================================================================
"""
Merge strategy protocol and built-in implementations.

Built-in strategies:

* **ConcatenateMerge** — appends all child outputs (text or structured)
  into a single summary message for the parent.

* **StructuredMerge** — merges structured outputs by key, producing a
  combined dict.  Useful when each sub-chat produces a well-defined JSON
  schema and the parent needs a unified view.

Platform can register additional strategies via the PluginRegistry.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChildResult:
    """The output of a single sub-GroupChat execution.

    Populated by the GroupChatPool after each sub-run completes.
    """
    task_id: str
    workflow_name: str
    run_id: str
    #: The final text output / summary from the sub-chat.
    text_output: str = ""
    #: Any structured output produced by the sub-chat.
    structured_output: dict[str, Any] = field(default_factory=dict)
    #: Whether the sub-run completed successfully.
    success: bool = True
    #: Error message if the sub-run failed.
    error: str | None = None
    #: Opaque metadata from the sub-run.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MergeResult:
    """The combined output of all sub-GroupChats.

    Used by the UniversalOrchestrator to build the resume message for the parent.
    """
    #: Human-readable summary suitable as the resume message.
    summary_message: str
    #: Merged structured data (union of all child structured outputs).
    merged_data: dict[str, Any] = field(default_factory=dict)
    #: Per-child results preserved for audit / logging.
    child_results: tuple[ChildResult, ...] = ()
    #: Whether all children succeeded.
    all_succeeded: bool = True
    #: Strategy-specific metadata.
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.child_results if not r.success)

    @property
    def succeeded_count(self) -> int:
        return sum(1 for r in self.child_results if r.success)


@dataclass
class MergeContext:
    """Context passed to a strategy's ``merge`` method."""
    parent_run_id: str
    parent_workflow_name: str
    child_results: list[ChildResult]
    #: Context variables from the parent run (before decomposition).
    parent_context_variables: dict[str, Any] = field(default_factory=dict)
    #: Strategy metadata from the DecompositionPlan.
    strategy_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class MergeStrategy(Protocol):
    """Pluggable strategy for merging sub-GroupChat results."""

    def merge(self, context: MergeContext) -> MergeResult:
        """Combine child results into a single MergeResult for the parent."""
        ...


# ---------------------------------------------------------------------------
# Built-in: ConcatenateMerge
# ---------------------------------------------------------------------------

class ConcatenateMerge:
    """Concatenates all child text outputs into a single summary.

    Format::

        ## Sub-task Results

        ### sub_workflow_a (task_001) ✅
        <child text output>

        ### sub_workflow_b (task_002) ✅
        <child text output>

        ---
        **Summary:** 2/2 sub-tasks completed successfully.
    """

    def __init__(self, *, header: str = "Sub-task Results", separator: str = "\n\n---\n\n") -> None:
        self._header = header
        self._separator = separator

    def merge(self, context: MergeContext) -> MergeResult:
        sections: list[str] = []
        merged_data: dict[str, Any] = {}
        all_ok = True

        for result in context.child_results:
            status = "✅" if result.success else "❌"
            section = f"### {result.workflow_name} ({result.task_id}) {status}\n"
            if result.text_output:
                section += result.text_output
            elif result.error:
                section += f"**Error:** {result.error}"
            else:
                section += "(no output)"
            sections.append(section)

            if result.structured_output:
                merged_data[result.task_id] = result.structured_output

            if not result.success:
                all_ok = False

        total = len(context.child_results)
        ok_count = sum(1 for r in context.child_results if r.success)
        footer = f"**Summary:** {ok_count}/{total} sub-tasks completed successfully."

        body = self._separator.join(sections)
        summary = f"## {self._header}\n\n{body}\n\n---\n{footer}"

        return MergeResult(
            summary_message=summary,
            merged_data=merged_data,
            child_results=tuple(context.child_results),
            all_succeeded=all_ok,
        )


# ---------------------------------------------------------------------------
# Built-in: StructuredMerge
# ---------------------------------------------------------------------------

class StructuredMerge:
    """Merges child structured outputs into a unified dict keyed by task_id.

    If a child has no structured output, its text output is stored under a
    ``_text`` key.  Summary message is a compact JSON representation.
    """

    def __init__(self, *, include_text_fallback: bool = True) -> None:
        self._include_text = include_text_fallback

    def merge(self, context: MergeContext) -> MergeResult:
        merged: dict[str, Any] = {}
        all_ok = True

        for result in context.child_results:
            entry: dict[str, Any] = {
                "workflow_name": result.workflow_name,
                "success": result.success,
            }
            if result.structured_output:
                entry["data"] = result.structured_output
            elif self._include_text and result.text_output:
                entry["data"] = {"_text": result.text_output}
            if result.error:
                entry["error"] = result.error

            merged[result.task_id] = entry

            if not result.success:
                all_ok = False

        total = len(context.child_results)
        ok_count = sum(1 for r in context.child_results if r.success)

        # Compact summary for the resume message
        try:
            summary_text = json.dumps(merged, indent=2, default=str)
        except Exception:
            summary_text = str(merged)

        summary = (
            f"Merged results from {total} sub-tasks ({ok_count} succeeded):\n"
            f"```json\n{summary_text}\n```"
        )

        return MergeResult(
            summary_message=summary,
            merged_data=merged,
            child_results=tuple(context.child_results),
            all_succeeded=all_ok,
        )


__all__ = [
    "ChildResult",
    "ConcatenateMerge",
    "MergeContext",
    "MergeResult",
    "MergeStrategy",
    "StructuredMerge",
]
