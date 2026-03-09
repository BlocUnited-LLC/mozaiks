# === MOZAIKS-CORE-HEADER ===
# FILE: core/workflow/pack/merge.py
# DESCRIPTION: Pluggable merge strategies for fan-in result aggregation.
#
# When N child GroupChats complete, their outputs must be combined before
# injecting into the parent's context_variables.  The merge strategy
# determines HOW they are combined.
#
# Strategies are declared in workflow_graph.json per-trigger entry:
#   "aggregation_strategy": "collect_all" | "merge_bundles" | "concatenate"
# ==============================================================================
"""Fan-in merge strategies for WorkflowPackCoordinator."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ChildResult:
    """Output from a single child GroupChat after completion."""

    child_chat_id: str
    workflow_name: str
    # The child's final extra_context from MongoDB (what the child produced).
    context: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None


@dataclass
class MergeResult:
    """Output of a merge strategy — what gets injected into the parent."""

    merged: Dict[str, Any]
    strategy_used: str
    child_count: int
    failed_count: int = 0


# ---------------------------------------------------------------------------
# Abstract strategy
# ---------------------------------------------------------------------------


class MergeStrategy(ABC):
    """Protocol for fan-in result aggregation."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name (matches workflow_graph.json value)."""
        ...

    @abstractmethod
    def merge(self, children: List[ChildResult]) -> MergeResult:
        """Combine child results into a single merged dict.

        Failed children (success=False) are included in the list so strategies
        can decide how to handle them (skip, include error, etc.).
        """
        ...


# ---------------------------------------------------------------------------
# Built-in strategies
# ---------------------------------------------------------------------------


class CollectAllMerge(MergeStrategy):
    """Return all child outputs keyed by workflow_name.

    Result shape::

        {
            "BillingWorkflow": { ...child context... },
            "AuthWorkflow": { ...child context... },
            "_failed": ["SomeOtherWorkflow"]
        }

    Best for: planning phases where each domain's output is reviewed independently.
    """

    @property
    def name(self) -> str:
        return "collect_all"

    def merge(self, children: List[ChildResult]) -> MergeResult:
        merged: Dict[str, Any] = {}
        failed: List[str] = []
        for child in children:
            if child.success:
                merged[child.workflow_name] = child.context
            else:
                failed.append(child.workflow_name)
        if failed:
            merged["_failed"] = failed
        return MergeResult(
            merged=merged,
            strategy_used=self.name,
            child_count=len(children),
            failed_count=len(failed),
        )


class ConcatenateMerge(MergeStrategy):
    """Flat-merge all child contexts into one dict.

    Last-write-wins on key collisions (deterministic by sorted workflow_name).

    Best for: simple accumulation where children produce disjoint keys.
    """

    @property
    def name(self) -> str:
        return "concatenate"

    def merge(self, children: List[ChildResult]) -> MergeResult:
        merged: Dict[str, Any] = {}
        failed = 0
        # Sort by workflow_name for deterministic last-write-wins
        for child in sorted(children, key=lambda c: c.workflow_name):
            if child.success:
                merged.update(child.context)
            else:
                failed += 1
        return MergeResult(
            merged=merged,
            strategy_used=self.name,
            child_count=len(children),
            failed_count=failed,
        )


class MergeBundlesMerge(MergeStrategy):
    """Deep-merge child outputs into a single dict.

    Uses recursive dict merge — nested dicts are merged, lists are concatenated,
    scalars follow last-write-wins.

    Best for: file generation where outputs should form one unified bundle.
    """

    @property
    def name(self) -> str:
        return "merge_bundles"

    def merge(self, children: List[ChildResult]) -> MergeResult:
        merged: Dict[str, Any] = {}
        failed = 0
        for child in sorted(children, key=lambda c: c.workflow_name):
            if child.success:
                _deep_merge(merged, child.context)
            else:
                failed += 1
        return MergeResult(
            merged=merged,
            strategy_used=self.name,
            child_count=len(children),
            failed_count=failed,
        )


class FirstSuccessMerge(MergeStrategy):
    """Return the first child that completed successfully.

    Best for: redundant execution where you want the fastest result.
    """

    @property
    def name(self) -> str:
        return "first_success"

    def merge(self, children: List[ChildResult]) -> MergeResult:
        for child in children:
            if child.success:
                return MergeResult(
                    merged=child.context,
                    strategy_used=self.name,
                    child_count=len(children),
                    failed_count=sum(1 for c in children if not c.success),
                )
        # All failed
        return MergeResult(
            merged={},
            strategy_used=self.name,
            child_count=len(children),
            failed_count=len(children),
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_STRATEGIES: Dict[str, MergeStrategy] = {}


def _ensure_registry() -> Dict[str, MergeStrategy]:
    if not _STRATEGIES:
        for cls in (CollectAllMerge, ConcatenateMerge, MergeBundlesMerge, FirstSuccessMerge):
            instance = cls()
            _STRATEGIES[instance.name] = instance
    return _STRATEGIES


def get_merge_strategy(name: str) -> MergeStrategy:
    """Resolve a strategy by name (from workflow_graph.json).

    Falls back to ``collect_all`` if the name is unrecognized.
    """
    registry = _ensure_registry()
    return registry.get(name, registry["collect_all"])


def register_merge_strategy(strategy: MergeStrategy) -> None:
    """Register a custom merge strategy at runtime."""
    _ensure_registry()[strategy.name] = strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    """Recursively merge ``override`` into ``base`` in place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        elif key in base and isinstance(base[key], list) and isinstance(value, list):
            base[key] = base[key] + value
        else:
            base[key] = value


__all__ = [
    "ChildResult",
    "MergeResult",
    "MergeStrategy",
    "CollectAllMerge",
    "ConcatenateMerge",
    "MergeBundlesMerge",
    "FirstSuccessMerge",
    "get_merge_strategy",
    "register_merge_strategy",
]
