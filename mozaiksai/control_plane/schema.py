from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ControlPlaneToolDefinition


class ControlPlaneProfileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ControlPlaneHarnessManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation: str = Field(min_length=1)
    supported_trigger_sources: list[str] = Field(default_factory=list)


class ControlPlaneCheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    prompt_id: Optional[str] = None
    tool_ids: list[str] = Field(default_factory=list)
    ui_tool_ids: list[str] = Field(default_factory=list)


class ControlPlaneManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.control_plane.v1"]
    profile: ControlPlaneProfileInfo
    harness: ControlPlaneHarnessManifest
    checkpoints: list[ControlPlaneCheckpointManifest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_checkpoints(self) -> "ControlPlaneManifest":
        checkpoint_ids = [checkpoint.id for checkpoint in self.checkpoints]
        if len(checkpoint_ids) != len(set(checkpoint_ids)):
            raise ValueError("control_plane.yaml checkpoint ids must be unique")
        checkpoint_events = [checkpoint.event for checkpoint in self.checkpoints]
        if len(checkpoint_events) != len(set(checkpoint_events)):
            raise ValueError("control_plane.yaml checkpoint event values must be unique")
        return self


class ControlPlanePromptDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ControlPlanePromptsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.control_plane.prompts.v1"]
    prompts: list[ControlPlanePromptDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_prompt_ids(self) -> "ControlPlanePromptsManifest":
        prompt_ids = [prompt.id for prompt in self.prompts]
        if len(prompt_ids) != len(set(prompt_ids)):
            raise ValueError("prompts.yaml prompt ids must be unique")
        return self


class ControlPlaneToolsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.control_plane.tools.v1"]
    tools: list[ControlPlaneToolDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_tool_ids(self) -> "ControlPlaneToolsManifest":
        tool_ids = [tool.id for tool in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tools.yaml tool ids must be unique")
        return self


class ControlPlaneScopePolicyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_selected_paths: int = Field(default=3, ge=1, le=20)
    auto_apply_max_paths: int = Field(default=1, ge=1, le=20)
    overflow_behavior: Literal["clarify", "workflow"] = "clarify"

    @model_validator(mode="after")
    def _validate_thresholds(self) -> "ControlPlaneScopePolicyManifest":
        if self.auto_apply_max_paths > self.max_selected_paths:
            raise ValueError("scope.auto_apply_max_paths must be <= scope.max_selected_paths")
        return self


class ControlPlanePoliciesManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.control_plane.policies.v1"] = "mozaiks.control_plane.policies.v1"
    scope: ControlPlaneScopePolicyManifest = Field(default_factory=ControlPlaneScopePolicyManifest)


class LoadedControlPlanePack(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    path: Path
    manifest: ControlPlaneManifest
    prompts: ControlPlanePromptsManifest
    tools: ControlPlaneToolsManifest
    policies: ControlPlanePoliciesManifest = Field(default_factory=ControlPlanePoliciesManifest)

    def prompt_by_id(self, prompt_id: str) -> Optional[ControlPlanePromptDefinition]:
        for prompt in self.prompts.prompts:
            if prompt.id == prompt_id:
                return prompt
        return None

    def tool_by_id(self, tool_id: str) -> Optional[ControlPlaneToolDefinition]:
        for tool in self.tools.tools:
            if tool.id == tool_id:
                return tool
        return None

    def checkpoint_by_id(self, checkpoint_id: str) -> Optional[ControlPlaneCheckpointManifest]:
        for checkpoint in self.manifest.checkpoints:
            if checkpoint.id == checkpoint_id:
                return checkpoint
        return None

    def checkpoint_by_event(self, event_name: str) -> Optional[ControlPlaneCheckpointManifest]:
        for checkpoint in self.manifest.checkpoints:
            if checkpoint.event == event_name:
                return checkpoint
        return None
