"""Server-owned association between a workflow run and its build artifacts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

BuildIdentity = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]


class BuildTargetReference(BaseModel):
    """Untrusted selectors; ownership must be resolved by the host."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    build_registry_id: BuildIdentity | None = None
    source_chat_id: BuildIdentity | None = None


class RunBuildBinding(BaseModel):
    """Persisted in ChatSessions, never accepted as workflow caller input.

    Execution app/user/chat identity remains on the enclosing session. A build
    ID is shared by the workflows of one build, not by later refinements.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    build_registry_id: BuildIdentity
    target_app_id: BuildIdentity
    build_id: BuildIdentity
    phase: Literal["genesis", "refinement"]
