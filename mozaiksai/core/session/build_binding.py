"""Server-owned association between a workflow run and its build artifacts."""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, StringConstraints, model_serializer

BuildIdentity = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
RefinementModel = Literal["gemini", "openai"]


def refinement_model_config(model: RefinementModel) -> dict[str, str]:
    if model == "gemini":
        return {"api_type": "google", "model": "gemini-3.8-flash"}
    if model == "openai":
        return {"api_type": "openai", "model": "gpt-5-nano"}
    raise ValueError("Unknown refinement model")


class BuildTargetReference(BaseModel):
    """Untrusted selectors; ownership must be resolved by the host."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    build_registry_id: BuildIdentity | None = None
    source_chat_id: BuildIdentity | None = None


class RetryPlanSource(BaseModel):
    """Creation-only reference to an admitted plan and its exact dispatch rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chat_id: BuildIdentity
    plan_sha256: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]
    dispatch_sha256: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]


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
    refinement_model: RefinementModel | None = None

    @model_serializer(mode="wrap")
    def _serialize_binding(self, handler: Any) -> dict[str, Any]:
        payload = cast(dict[str, Any], handler(self))
        if self.refinement_model is None:
            payload.pop("refinement_model", None)
        return payload
