# === MOZAIKS-CORE-HEADER ===
# FILE: mozaiksai/core/workflow/pack/merge.py
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
import json
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


class MajorityVoteMerge(MergeStrategy):
    """Return the most common successful output across children."""

    @property
    def name(self) -> str:
        return "majority_vote"

    def merge(self, children: List[ChildResult]) -> MergeResult:
        voters = [c for c in children if c.success]
        failed = len(children) - len(voters)
        if not voters:
            return MergeResult(
                merged={},
                strategy_used=self.name,
                child_count=len(children),
                failed_count=failed if failed > 0 else len(children),
            )

        counts: Dict[str, int] = {}
        payload_by_key: Dict[str, Dict[str, Any]] = {}
        first_workflow_by_key: Dict[str, str] = {}
        for child in sorted(voters, key=lambda c: c.workflow_name):
            canonical = json.dumps(child.context, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            counts[canonical] = counts.get(canonical, 0) + 1
            payload_by_key.setdefault(canonical, dict(child.context))
            first_workflow_by_key.setdefault(canonical, child.workflow_name)

        winner = max(
            counts.items(),
            key=lambda kv: (kv[1], first_workflow_by_key.get(kv[0], ""), kv[0]),
        )[0]
        return MergeResult(
            merged=payload_by_key.get(winner, {}),
            strategy_used=self.name,
            child_count=len(children),
            failed_count=failed,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_STRATEGIES: Dict[str, MergeStrategy] = {}


def _ensure_registry() -> Dict[str, MergeStrategy]:
    if not _STRATEGIES:
        for cls in (
            CollectAllMerge,
            ConcatenateMerge,
            MergeBundlesMerge,
            FirstSuccessMerge,
            MajorityVoteMerge,
        ):
            instance = cls()
            _STRATEGIES[instance.name] = instance
    return _STRATEGIES


def get_merge_strategy(name: str) -> MergeStrategy:
    """Resolve a strategy by canonical name.

    Supports built-ins and `custom:<name>` registry lookups.
    """
    registry = _ensure_registry()
    key = str(name or "").strip()
    if not key:
        raise ValueError("aggregation strategy name is required")

    if key.startswith("custom:"):
        custom_name = key.split(":", 1)[1].strip()
        if not custom_name:
            raise ValueError("custom aggregation strategy must be custom:<name>")
        strategy = registry.get(custom_name)
        if strategy is None:
            raise ValueError(f"custom aggregation strategy is not registered: {custom_name}")
        return strategy

    strategy = registry.get(key)
    if strategy is None:
        raise ValueError(f"unknown aggregation strategy: {key}")
    return strategy


def register_merge_strategy(strategy: MergeStrategy, *, replace: bool = False) -> None:
    """Register a custom merge strategy at runtime."""
    registry = _ensure_registry()
    name = str(strategy.name or "").strip()
    if not name:
        raise ValueError("merge strategy name must be non-empty")
    if name in registry and not replace:
        raise ValueError(f"merge strategy already registered: {name}")
    registry[name] = strategy


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
    "MajorityVoteMerge",
    "get_merge_strategy",
    "register_merge_strategy",
]
