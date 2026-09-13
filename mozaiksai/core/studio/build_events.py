"""Public wire contract for the Factory's existing build lifecycle callback."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from mozaiksai.core.session.build_binding import BuildIdentity


class BuildEventArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_version_id: BuildIdentity = Field(alias="artifactVersionId")
    bundle_path: str | None = Field(default=None, alias="bundlePath")
    export_download_url: str = Field(alias="exportDownloadUrl", min_length=1)


class BuildLifecycleEvent(BaseModel):
    """A notification, not permission to run provider operations or publish an app.

    Only the documented camel-case wire names are accepted. Generation evidence
    remains owned and validated by the existing Factory evaluation contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: Literal["build.started", "build.completed", "build.failed"] = Field(alias="eventType")
    status: Literal["started", "completed", "failed"]
    idempotency_key: str = Field(alias="idempotencyKey")
    app_id: BuildIdentity = Field(alias="appId")
    target_app_id: BuildIdentity = Field(alias="targetAppId")
    build_id: BuildIdentity = Field(alias="buildId")
    build_registry_id: BuildIdentity = Field(alias="buildRegistryId")
    phase: Literal["genesis", "refinement"]
    user_id: str = Field(alias="userId", min_length=1, max_length=200)
    chat_id: BuildIdentity = Field(alias="chatId")
    execution_id: str | None = Field(default=None, alias="executionId")
    workflow_name: str = Field(alias="workflowName", min_length=1, max_length=200)
    timestamp: AwareDatetime
    app_name: str | None = Field(default=None, alias="appName", max_length=200)
    journey_id: str | None = Field(default=None, alias="journeyId")
    journey_instance_id: str | None = Field(default=None, alias="journeyInstanceId")
    journey_position: int | None = Field(default=None, alias="journeyPosition", ge=0)
    journey_total_steps: int | None = Field(default=None, alias="journeyTotalSteps", ge=1)
    artifacts: BuildEventArtifacts | None = None
    build_evidence: dict[str, Any] | None = Field(default=None, alias="buildEvidence")
    error: str | None = None

    @model_validator(mode="after")
    def coherent_identity(self) -> Self:
        if self.event_type != f"build.{self.status}":
            raise ValueError("Build event type and status disagree")
        if self.app_id == self.target_app_id:
            raise ValueError("The generated target must differ from the execution host")
        expected = f"build:{self.app_id}:{self.build_id}:{self.event_type}"
        if self.idempotency_key != expected:
            raise ValueError("Build event idempotency key does not match its identity")
        return self
