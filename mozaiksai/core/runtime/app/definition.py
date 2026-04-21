from __future__ import annotations

"""Discovered app definition schema.

An app declares product intent in app.json. Runtime composition is discovered
from owner manifests and directories:
  - workflows/* for AI workflows
  - operations/*/operation.yaml for deterministic CRUD/action operations
  - pages/*.yaml or pages/*/page.yaml for persistent UI pages
  - Execution mode derived from the above

This is the runtime contract for the composition layer. In Phase 1
only `ai_only` mode is fully wired. Operation and page support land in Phase 2.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    """How the runtime should initialize itself for this app.

    ai_only           — Only AI workflows. No operations, no app pages.
                        Current default for all platform bundles.
    operations_only   — Only CRUD operations. No AI workflows. (Phase 2)
    full              — Both AI workflows and CRUD operations. (Phase 2)
    static            — No executors declared (static site / docs only). (Phase 2)
    """
    AI_ONLY = "ai_only"
    OPERATIONS_ONLY = "operations_only"
    FULL = "full"
    STATIC = "static"


class WorkflowRef(BaseModel):
    """Reference to a discovered workflow."""
    name: str
    path: Optional[str] = None  # defaults to platform/workflows/{name}/


class OperationRef(BaseModel):
    """Reference to a discovered operation."""
    name: str
    path: Optional[str] = None  # defaults to platform/operations/{name}/


class PageRef(BaseModel):
    """Reference to a discovered UI page."""
    name: str
    path: Optional[str] = None  # defaults to platform/pages/{name}/


class AppFeatureFlags(BaseModel):
    """Feature flags derived from what the app declares."""
    ai: bool = False
    operations: bool = False  # Phase 2
    ui: bool = False          # Phase 2


class AppDefinition(BaseModel):
    """Parsed app.json metadata plus discovered bundle composition.

    `AppLoader` builds this model. App authors should not hand-maintain the
    workflows/operations/pages lists here.
    """
    name: str
    version: str = "1.0"
    description: Optional[str] = None

    workflows: List[WorkflowRef] = Field(default_factory=list)
    operations: List[OperationRef] = Field(default_factory=list)  # Phase 2
    pages: List[PageRef] = Field(default_factory=list)            # Phase 2

    # Arbitrary extra config (theme, feature flags, etc.)
    config: Dict[str, Any] = Field(default_factory=dict)

    @property
    def feature_flags(self) -> AppFeatureFlags:
        return AppFeatureFlags(
            ai=bool(self.workflows),
            operations=bool(self.operations),
            ui=bool(self.pages),
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        flags = self.feature_flags
        if flags.ai and flags.operations:
            return ExecutionMode.FULL
        if flags.ai:
            return ExecutionMode.AI_ONLY
        if flags.operations:
            return ExecutionMode.OPERATIONS_ONLY
        return ExecutionMode.STATIC
