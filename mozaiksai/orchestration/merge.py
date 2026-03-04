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

* **DeepMergeMerge** — deep-merges child structured outputs into a single
  flat dict (last-write-wins for duplicate keys, deterministic child order
  by task_id).  Useful when children produce disjoint key spaces.

* **FirstSuccessMerge** — returns the output of the first successfully
  completed child (sorted by task_id for determinism).  Useful for
  redundant-execution patterns where only one answer is needed.

* **MajorityVoteMerge** — returns the most common structured output across
  children.  Useful for consensus / evaluation patterns where multiple
  agents solve the same problem and the mode answer is selected.

Platform can register additional strategies via the MergeStrategyRegistry.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
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
    "DeepMergeMerge",
    "FirstSuccessMerge",
    "MajorityVoteMerge",
    "MergeContext",
    "MergeResult",
    "MergeStrategy",
    "MergeStrategyRegistry",
    "StructuredMerge",
    "get_merge_strategy_registry",
    "merge_strategy",
    "reset_merge_strategy_registry",
]


# ---------------------------------------------------------------------------
# Built-in: DeepMergeMerge
# ---------------------------------------------------------------------------

class DeepMergeMerge:
    """Deep-merges child structured outputs into a single flat dict.

    Children are processed in deterministic order (sorted by ``task_id``).
    For duplicate keys across children, last-write-wins.  Nested dicts are
    recursively merged; non-dict values overwrite.

    Use case: children produce disjoint key spaces (e.g., one child generates
    "frontend" keys, another generates "backend" keys) and the parent needs
    a unified object.

    Config: ``merge_mode: "deep_merge"``
    """

    @staticmethod
    def _deep_merge(base: dict, overlay: dict) -> dict:
        """Recursively merge *overlay* into *base* (mutates base)."""
        for key, value in overlay.items():
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
                DeepMergeMerge._deep_merge(base[key], value)
            else:
                base[key] = value
        return base

    def merge(self, context: MergeContext) -> MergeResult:
        merged: dict[str, Any] = {}
        all_ok = True

        # Deterministic order: sort children by task_id.
        sorted_results = sorted(context.child_results, key=lambda r: r.task_id)

        for result in sorted_results:
            if result.structured_output and isinstance(result.structured_output, dict):
                self._deep_merge(merged, result.structured_output)
            if not result.success:
                all_ok = False

        total = len(context.child_results)
        ok_count = sum(1 for r in context.child_results if r.success)
        summary = (
            f"Deep-merged {ok_count}/{total} child outputs "
            f"into {len(merged)} top-level keys."
        )

        return MergeResult(
            summary_message=summary,
            merged_data=merged,
            child_results=tuple(context.child_results),
            all_succeeded=all_ok,
        )


# ---------------------------------------------------------------------------
# Built-in: FirstSuccessMerge
# ---------------------------------------------------------------------------

class FirstSuccessMerge:
    """Returns the output of the first successfully completed child.

    Children are sorted by ``task_id`` for deterministic winner selection.
    If no child succeeded, returns an empty merge with ``all_succeeded=False``.

    Use case: redundant execution — spawn the same task to multiple agents
    and take whichever finishes successfully first.  The "first" is
    deterministic by ``task_id`` sort order (actual arrival order is not
    tracked at the merge layer; the fan-in waits for all children).

    Config: ``merge_mode: "first_success"``
    """

    def merge(self, context: MergeContext) -> MergeResult:
        sorted_results = sorted(context.child_results, key=lambda r: r.task_id)

        winner: ChildResult | None = None
        for result in sorted_results:
            if result.success:
                winner = result
                break

        if winner is None:
            total = len(context.child_results)
            return MergeResult(
                summary_message=f"No child succeeded out of {total}.",
                merged_data={},
                child_results=tuple(context.child_results),
                all_succeeded=False,
            )

        total = len(context.child_results)
        summary = (
            f"Selected first successful child: {winner.workflow_name} "
            f"({winner.task_id}) out of {total} candidates."
        )

        return MergeResult(
            summary_message=summary,
            merged_data=winner.structured_output or {},
            child_results=tuple(context.child_results),
            all_succeeded=all(r.success for r in context.child_results),
            metadata={"winner_task_id": winner.task_id},
        )


# ---------------------------------------------------------------------------
# Built-in: MajorityVoteMerge
# ---------------------------------------------------------------------------

class MajorityVoteMerge:
    """Returns the most common structured output across children.

    Outputs are compared by their JSON-serialized canonical form (sorted
    keys, deterministic serialization).  The most frequent output wins.
    Ties are broken by the earliest ``task_id`` alphabetically.

    Use case: consensus evaluation — multiple agents solve the same problem,
    and the most-agreed-upon answer is selected.

    Config: ``merge_mode: "majority_vote"``
    """

    @staticmethod
    def _canonical(output: dict[str, Any]) -> str:
        """Produce a deterministic string for comparison."""
        try:
            return json.dumps(output, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(sorted(output.items()))

    def merge(self, context: MergeContext) -> MergeResult:
        # Build canonical → (count, first_task_id, original_output) map.
        vote_counts: Counter[str] = Counter()
        canonical_to_output: dict[str, dict[str, Any]] = {}
        canonical_to_task: dict[str, str] = {}  # earliest task_id per canonical

        sorted_results = sorted(context.child_results, key=lambda r: r.task_id)

        for result in sorted_results:
            if not result.success:
                continue
            output = result.structured_output or {}
            canon = self._canonical(output)
            vote_counts[canon] += 1
            if canon not in canonical_to_output:
                canonical_to_output[canon] = output
                canonical_to_task[canon] = result.task_id

        if not vote_counts:
            total = len(context.child_results)
            return MergeResult(
                summary_message=f"No successful children to vote on (0/{total}).",
                merged_data={},
                child_results=tuple(context.child_results),
                all_succeeded=False,
            )

        # Find the winner: highest count, tiebreak by earliest task_id.
        max_count = max(vote_counts.values())
        candidates = [
            canon for canon, count in vote_counts.items()
            if count == max_count
        ]
        # Tiebreak: earliest task_id alphabetically.
        winner_canon = min(candidates, key=lambda c: canonical_to_task[c])
        winner_output = canonical_to_output[winner_canon]
        winner_count = vote_counts[winner_canon]

        total = len(context.child_results)
        ok_count = sum(1 for r in context.child_results if r.success)
        summary = (
            f"Majority vote: {winner_count}/{ok_count} successful children "
            f"agreed (total: {total}).  Winner from task "
            f"{canonical_to_task[winner_canon]}."
        )

        return MergeResult(
            summary_message=summary,
            merged_data=winner_output,
            child_results=tuple(context.child_results),
            all_succeeded=all(r.success for r in context.child_results),
            metadata={
                "winner_task_id": canonical_to_task[winner_canon],
                "vote_count": winner_count,
                "total_voters": ok_count,
            },
        )


# ---------------------------------------------------------------------------
# Merge Strategy Registry
# ---------------------------------------------------------------------------

class MergeStrategyRegistry:
    """Registry for merge strategies — both built-in and custom.

    Built-in strategies are registered at import time.  Workflows can
    register custom strategies via ``merge_strategy()`` decorator or
    direct ``register()`` call.

    Lookup: ``registry.get("concatenate")`` returns a ``MergeStrategy``
    **class** (not an instance).  The coordinator instantiates it.

    For custom strategies in workflow configs, use the syntax:
        ``merge_mode: "custom:my_strategy_name"``
    The coordinator strips the ``custom:`` prefix and looks up the name.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, type] = {}

    def register(self, name: str, strategy_cls: type, *, replace: bool = False) -> type:
        """Register a merge strategy class under a name.

        Args:
            name: Lookup key (e.g., "concatenate", "my_custom_merge").
            strategy_cls: Class implementing `MergeStrategy` protocol.
            replace: If True, overwrite existing registration.

        Returns:
            The strategy class (for decorator chaining).
        """
        if not name:
            raise ValueError("Strategy name is required.")
        if not replace and name in self._strategies:
            raise ValueError(f"Merge strategy '{name}' already registered.")
        self._strategies[name] = strategy_cls
        return strategy_cls

    def get(self, name: str) -> type | None:
        """Look up a strategy class by name.  Returns None if not found."""
        return self._strategies.get(name)

    def list(self) -> list[str]:
        """Return sorted list of registered strategy names."""
        return sorted(self._strategies.keys())

    def _register_builtins(self) -> None:
        """Register all built-in strategies.  Called once at singleton init."""
        builtins = {
            "concatenate": ConcatenateMerge,
            "structured": StructuredMerge,
            "deep_merge": DeepMergeMerge,
            "first_success": FirstSuccessMerge,
            "majority_vote": MajorityVoteMerge,
        }
        for name, cls in builtins.items():
            self._strategies[name] = cls


import threading

_MERGE_REGISTRY: MergeStrategyRegistry | None = None
_MERGE_LOCK = threading.Lock()


def get_merge_strategy_registry() -> MergeStrategyRegistry:
    """Return a singleton merge strategy registry with built-ins pre-registered."""
    global _MERGE_REGISTRY
    if _MERGE_REGISTRY is None:
        with _MERGE_LOCK:
            if _MERGE_REGISTRY is None:
                reg = MergeStrategyRegistry()
                reg._register_builtins()
                _MERGE_REGISTRY = reg
    return _MERGE_REGISTRY


def reset_merge_strategy_registry() -> None:
    """Reset singleton (for tests).  Re-registers builtins on next access."""
    global _MERGE_REGISTRY
    with _MERGE_LOCK:
        _MERGE_REGISTRY = None


def merge_strategy(
    name: str | None = None,
) -> Any:
    """Decorator to register a custom merge strategy class.

    Usage::

        @merge_strategy("my_custom_merge")
        class MyCustomMerge:
            def merge(self, context: MergeContext) -> MergeResult:
                ...

    In workflow config::

        merge_mode: "custom:my_custom_merge"
    """
    def decorator(cls: type) -> type:
        resolved_name = name or cls.__name__
        registry = get_merge_strategy_registry()
        registry.register(resolved_name, cls)
        setattr(cls, "__merge_strategy_name__", resolved_name)
        return cls
    return decorator
