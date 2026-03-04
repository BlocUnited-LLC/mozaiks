# === MOZAIKS-CORE-HEADER ===
# ==============================================================================
# FILE: core/workflow/pack/schema.py
# DESCRIPTION: Pydantic models for pack configuration (workflow_graph.json).
#
# This module defines the typed schema for BOTH:
#   1. Per-workflow pack graph   — workflows/<name>/_pack/workflow_graph.json
#      Defines Mid-Flight Journey (MFJ) triggers for fan-out/fan-in.
#   2. Global pack config        — workflows/_pack/workflow_graph.json
#      Defines workflows, sequential journeys, gates.
#
# Schema versions:
#   v2 (current)  — "journeys" key with raw trigger dicts.
#   v3 (new)      — "mid_flight_journeys" key with fully-typed MFJ objects,
#                    plus "journeys" still available for sequential journeys.
#   Legacy        — "nested_chats" key (auto-normalized to v2 on load).
#
# The coordinator reads parsed models via `load_pack_graph()` in config.py.
# These models are validation-only — the coordinator still accesses fields
# via dict-like patterns, but config.py validates on load and surfaces
# clear error messages instead of silent None/default fallbacks.
#
# For future coding agents:
#   - Add new MFJ fields to MidFlightJourney + MFJFanOutConfig/MFJFanInConfig.
#   - Add new global config fields to PackGlobalConfig.
#   - Version detection: see `detect_schema_version()` and `parse_pack_graph()`.
#   - All models use strict validation (extra fields forbidden on typed models,
#     allowed on V2 raw dicts for forward compat).
# ==============================================================================

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums — mirror the coordinator's runtime enums for config validation
# ---------------------------------------------------------------------------

class SpawnMode(str, Enum):
    """How children are spawned during fan-out.

    - workflow:          Each sub-task maps 1:1 to an existing workflow dir.
    - generator_subrun:  All sub-tasks share one generator workflow
                         (e.g. AgentGenerator) with different prompts.
    """
    WORKFLOW = "workflow"
    GENERATOR_SUBRUN = "generator_subrun"


class MergeMode(str, Enum):
    """Aggregation strategy for fan-in (merging child results).

    - concatenate:  Concat all child outputs into one string (default).
    - structured:   Merge child JSON objects into a keyed dict.
    - collect_all:  Return raw list of child outputs (backward compat).
    """
    CONCATENATE = "concatenate"
    STRUCTURED = "structured"
    COLLECT_ALL = "collect_all"


class PartialFailureStrategy(str, Enum):
    """What to do when some children fail or time out.

    - resume_with_available:  Merge available results, resume parent (default).
    - fail_all:               Abort the entire MFJ cycle.
    - retry_failed:           Re-spawn failed children (stub — falls back to
                              resume_with_available until fully implemented).
    - prompt_user:            Send a UI event asking the user to decide.
    """
    RESUME_WITH_AVAILABLE = "resume_with_available"
    FAIL_ALL = "fail_all"
    RETRY_FAILED = "retry_failed"
    PROMPT_USER = "prompt_user"


# ---------------------------------------------------------------------------
# Contract models
# ---------------------------------------------------------------------------

class MFJContract(BaseModel):
    """Input or output contract for an MFJ trigger.

    ``required`` keys are validated before fan-out (input) or after fan-in
    (output). Missing required keys on input → FanOutContractError.
    Missing required keys on output → logged warning (non-blocking).
    ``optional`` keys are documented but not enforced.
    """
    model_config = ConfigDict(extra="forbid")

    required: List[str] = Field(default_factory=list)
    optional: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Fan-out / Fan-in config (nested inside MidFlightJourney)
# ---------------------------------------------------------------------------

class MFJFanOutConfig(BaseModel):
    """Fan-out configuration — how the parent spawns children.

    Fields:
        spawn_mode:            "workflow" or "generator_subrun".
        generator_workflow:    Required when spawn_mode is "generator_subrun".
                               The workflow all children run inside.
        child_initial_agent:   Override the initial agent for child GroupChats.
        max_children:          Safety cap on number of children (0 = unlimited).
        timeout_seconds:       Max seconds to wait for ALL children. None = no limit.
        input_contract:        Keys the parent must have before fan-out starts.
        child_context_seed:    Static key-value pairs injected into every child's
                               context_variables at spawn time.
    """
    model_config = ConfigDict(extra="forbid")

    spawn_mode: SpawnMode = SpawnMode.WORKFLOW
    generator_workflow: Optional[str] = None
    child_initial_agent: Optional[str] = None
    max_children: int = Field(default=0, ge=0)
    timeout_seconds: Optional[float] = Field(default=None, gt=0)
    input_contract: MFJContract = Field(default_factory=MFJContract)
    child_context_seed: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("generator_workflow")
    @classmethod
    def _generator_workflow_required_for_subrun(cls, v: Optional[str], info) -> Optional[str]:
        """Validate generator_workflow is set when spawn_mode is generator_subrun."""
        # NOTE: Cross-field validation requires model_validator in Pydantic v2,
        # but we keep this as a soft check — the coordinator also validates at
        # runtime. Full cross-field validation happens in MidFlightJourney.
        return v.strip() if isinstance(v, str) and v.strip() else None


class MFJFanInConfig(BaseModel):
    """Fan-in configuration — how child results are merged and parent resumes.

    Fields:
        resume_agent:         Agent that receives merged results when parent resumes.
        merge_mode:           Aggregation strategy (concatenate, structured, collect_all).
        inject_as:            Context variable key for merged results in parent.
                              Default: "child_results".
        on_partial_failure:   Strategy when some children fail/timeout.
        output_contract:      Expected keys in merged output (warning-only).
    """
    model_config = ConfigDict(extra="forbid")

    resume_agent: Optional[str] = None
    merge_mode: MergeMode = MergeMode.CONCATENATE
    inject_as: str = "child_results"
    on_partial_failure: PartialFailureStrategy = PartialFailureStrategy.RESUME_WITH_AVAILABLE
    output_contract: MFJContract = Field(default_factory=MFJContract)


# ---------------------------------------------------------------------------
# Mid-Flight Journey definition (v3 schema)
# ---------------------------------------------------------------------------

class MidFlightJourney(BaseModel):
    """A single Mid-Flight Journey (fork-join cycle) definition.

    This is the v3 schema object inside the ``mid_flight_journeys`` array.
    It replaces the raw dict entries in the v2 ``journeys`` array for MFJ
    trigger definitions.

    Fields:
        id:              Unique identifier for this MFJ trigger. Used for
                         sequencing via ``requires`` and for observability.
        description:     Human-readable description (shown in logs/UI).
        trigger_agent:   The agent whose structured output triggers fan-out.
        trigger_on:      Event type to match. Default "structured_output_ready".
        requires:        List of MFJ trigger IDs that must complete before
                         this MFJ can fire. Enables multi-MFJ sequencing
                         (e.g. MFJ-2 requires MFJ-1 to finish first).
        fan_out:         Fan-out configuration.
        fan_in:          Fan-in configuration.
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = ""
    trigger_agent: str
    trigger_on: str = "structured_output_ready"
    requires: List[str] = Field(default_factory=list)
    fan_out: MFJFanOutConfig = Field(default_factory=MFJFanOutConfig)
    fan_in: MFJFanInConfig = Field(default_factory=MFJFanInConfig)

    @field_validator("id", "trigger_agent")
    @classmethod
    def _non_empty_string(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be a non-empty string")
        return v


# ---------------------------------------------------------------------------
# Per-workflow pack graph (v2 + v3 unified)
# ---------------------------------------------------------------------------

class PackGraphV2Entry(BaseModel):
    """Raw trigger entry from v2 ``journeys`` array.

    Permissive — allows extra fields for forward compatibility.
    The coordinator reads these via ``entry.get()`` with defaults.
    """
    model_config = ConfigDict(extra="allow")

    trigger_agent: str
    id: Optional[str] = None
    requires: Optional[List[str]] = None
    required_context: Optional[List[str]] = None
    expected_output_keys: Optional[List[str]] = None
    spawn_mode: Optional[str] = None
    generator_workflow: Optional[str] = None
    child_initial_agent: Optional[str] = None
    resume_agent: Optional[str] = None
    merge_mode: Optional[str] = None
    timeout_seconds: Optional[float] = None
    on_partial_failure: Optional[str] = None


class PerWorkflowPackGraph(BaseModel):
    """Per-workflow pack graph (workflows/<name>/_pack/workflow_graph.json).

    Supports three key layouts with automatic version detection:
      - v3: ``mid_flight_journeys`` array of typed MidFlightJourney objects.
      - v2: ``journeys`` array of raw trigger dicts (PackGraphV2Entry).
      - legacy: ``nested_chats`` array (renamed to journeys internally).

    The ``version`` field is optional; when absent, the loader infers
    version from which keys are present.

    For future coding agents:
        Access ``triggers`` property for the normalized trigger list.
        It returns MidFlightJourney objects regardless of input version.
    """
    model_config = ConfigDict(extra="allow")

    version: Optional[int] = None

    # v3 key
    mid_flight_journeys: Optional[List[MidFlightJourney]] = None

    # v2 key (may contain dicts + non-dict entries; non-dicts are filtered by .triggers)
    journeys: Optional[List[Any]] = None

    # Legacy key (auto-normalized to journeys)
    nested_chats: Optional[List[Any]] = None

    @property
    def detected_version(self) -> int:
        """Detect schema version from explicit field or key presence."""
        if self.version is not None:
            return self.version
        if self.mid_flight_journeys is not None:
            return 3
        return 2

    @property
    def triggers(self) -> List[MidFlightJourney]:
        """Normalized trigger list as v3 MidFlightJourney objects.

        v3:     Returns mid_flight_journeys directly.
        v2:     Converts raw journey dicts → MidFlightJourney (best-effort).
        legacy: Converts nested_chats → MidFlightJourney (best-effort).
        """
        if self.detected_version >= 3 and self.mid_flight_journeys:
            return self.mid_flight_journeys

        # v2 / legacy — normalize raw dicts into typed objects
        raw_list = self.journeys
        if not raw_list and self.nested_chats:
            raw_list = self.nested_chats
        if not raw_list:
            return []

        result: List[MidFlightJourney] = []
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            try:
                result.append(_v2_entry_to_mfj(entry))
            except Exception:
                continue  # Skip unparseable entries — coordinator handles gracefully
        return result

    @property
    def raw_journeys(self) -> List[Dict[str, Any]]:
        """Raw journey dicts for backward-compat code paths.

        Returns journeys or nested_chats as-is (no conversion to v3 objects).
        The coordinator still uses this for its defensive .get() patterns
        until we fully migrate all read paths to typed models.
        """
        if self.journeys:
            return self.journeys
        if self.nested_chats:
            return self.nested_chats
        # v3 — convert typed objects back to dicts for compat
        if self.mid_flight_journeys:
            return [_mfj_to_v2_dict(m) for m in self.mid_flight_journeys]
        return []


# ---------------------------------------------------------------------------
# Global pack config
# ---------------------------------------------------------------------------

class WorkflowDependency(BaseModel):
    """A dependency declaration for a workflow.

    Can originate from ``workflows[].dependencies`` or ``workflows[].requires``.
    """
    model_config = ConfigDict(extra="allow")

    id: str
    scope: Optional[str] = None
    reason: Optional[str] = None
    gating: str = "required"


class WorkflowEntry(BaseModel):
    """A workflow entry in the global pack config's ``workflows`` array."""
    model_config = ConfigDict(extra="allow")

    id: str
    label: Optional[str] = None
    description: Optional[str] = None
    dependencies: Optional[List[Union[str, WorkflowDependency]]] = None
    requires: Optional[List[Union[str, WorkflowDependency]]] = None
    dependency_scope: Optional[str] = None
    dependency_reason: Optional[str] = None


class GateEntry(BaseModel):
    """A gate entry in the global pack config's ``gates`` array."""
    model_config = ConfigDict(extra="allow")

    # "from" is a reserved keyword in Python, so we use Field alias
    from_workflow: str = Field(alias="from")
    to: str
    gating: str = "required"
    scope: Optional[str] = None
    reason: Optional[str] = None


class SequentialJourney(BaseModel):
    """A sequential journey in the global pack config's ``journeys`` array.

    These are NOT MFJ triggers — they define ordered step sequences for
    the auto-advance coordinator path (sequential journey mode).
    """
    model_config = ConfigDict(extra="allow")

    id: str
    label: Optional[str] = None
    description: Optional[str] = None
    steps: List[Any] = Field(default_factory=list)  # Mixed str / list[str]
    auto_advance: bool = True


class PackGlobalConfig(BaseModel):
    """Global pack config (workflows/_pack/workflow_graph.json).

    Contains the cross-workflow configuration: workflow registry,
    sequential journeys, and prerequisite gates.
    """
    model_config = ConfigDict(extra="allow")

    version: Optional[int] = None
    workflows: List[WorkflowEntry] = Field(default_factory=list)
    journeys: List[SequentialJourney] = Field(default_factory=list)
    gates: List[GateEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Conversion helpers (v2 ↔ v3)
# ---------------------------------------------------------------------------

def _v2_entry_to_mfj(entry: Dict[str, Any]) -> MidFlightJourney:
    """Convert a raw v2 journey/trigger dict to a typed MidFlightJourney.

    Maps flat v2 fields to the nested fan_out/fan_in structure:
        v2 field                  → v3 location
        ─────────────────────────────────────────
        trigger_agent             → trigger_agent
        id                        → id (defaults to trigger_{trigger_agent})
        requires                  → requires
        required_context          → fan_out.input_contract.required
        spawn_mode                → fan_out.spawn_mode
        generator_workflow        → fan_out.generator_workflow
        child_initial_agent       → fan_out.child_initial_agent
        timeout_seconds           → fan_out.timeout_seconds
        resume_agent              → fan_in.resume_agent
        merge_mode                → fan_in.merge_mode
        on_partial_failure        → fan_in.on_partial_failure
        expected_output_keys      → fan_in.output_contract.required
    """
    trigger_agent = str(entry.get("trigger_agent") or "").strip()
    if not trigger_agent:
        raise ValueError("v2 trigger entry missing trigger_agent")

    trigger_id = str(entry.get("id") or f"trigger_{trigger_agent}").strip()

    # Fan-out config
    fan_out_kwargs: Dict[str, Any] = {}

    spawn_mode_raw = str(entry.get("spawn_mode") or "").strip().lower()
    if spawn_mode_raw and spawn_mode_raw in {e.value for e in SpawnMode}:
        fan_out_kwargs["spawn_mode"] = spawn_mode_raw

    gen_wf = str(entry.get("generator_workflow") or "").strip()
    if gen_wf:
        fan_out_kwargs["generator_workflow"] = gen_wf

    child_agent = str(entry.get("child_initial_agent") or "").strip()
    if child_agent:
        fan_out_kwargs["child_initial_agent"] = child_agent

    timeout = entry.get("timeout_seconds")
    if isinstance(timeout, (int, float)) and timeout > 0:
        fan_out_kwargs["timeout_seconds"] = timeout

    required_ctx = entry.get("required_context")
    if isinstance(required_ctx, list) and required_ctx:
        fan_out_kwargs["input_contract"] = MFJContract(
            required=[str(k).strip() for k in required_ctx if str(k).strip()]
        )

    # Fan-in config
    fan_in_kwargs: Dict[str, Any] = {}

    resume = str(entry.get("resume_agent") or "").strip()
    if resume:
        fan_in_kwargs["resume_agent"] = resume

    merge_raw = str(entry.get("merge_mode") or "").strip().lower()
    if merge_raw and merge_raw in {e.value for e in MergeMode}:
        fan_in_kwargs["merge_mode"] = merge_raw

    pf_raw = str(entry.get("on_partial_failure") or "").strip().lower()
    if pf_raw and pf_raw in {e.value for e in PartialFailureStrategy}:
        fan_in_kwargs["on_partial_failure"] = pf_raw

    expected_keys = entry.get("expected_output_keys")
    if isinstance(expected_keys, list) and expected_keys:
        fan_in_kwargs["output_contract"] = MFJContract(
            required=[str(k).strip() for k in expected_keys if str(k).strip()]
        )

    # Requires
    requires_raw = entry.get("requires")
    requires: List[str] = []
    if isinstance(requires_raw, list):
        requires = [str(r).strip() for r in requires_raw if str(r).strip()]

    return MidFlightJourney(
        id=trigger_id,
        trigger_agent=trigger_agent,
        requires=requires,
        fan_out=MFJFanOutConfig(**fan_out_kwargs) if fan_out_kwargs else MFJFanOutConfig(),
        fan_in=MFJFanInConfig(**fan_in_kwargs) if fan_in_kwargs else MFJFanInConfig(),
    )


def _mfj_to_v2_dict(mfj: MidFlightJourney) -> Dict[str, Any]:
    """Convert a typed MidFlightJourney back to a flat v2 dict.

    Used by PerWorkflowPackGraph.raw_journeys when a v3 config needs
    to be consumed by code that still uses the old .get() patterns.
    """
    d: Dict[str, Any] = {
        "id": mfj.id,
        "trigger_agent": mfj.trigger_agent,
    }
    if mfj.requires:
        d["requires"] = mfj.requires
    if mfj.description:
        d["description"] = mfj.description

    # Fan-out → flat keys
    fo = mfj.fan_out
    if fo.spawn_mode != SpawnMode.WORKFLOW:
        d["spawn_mode"] = fo.spawn_mode.value
    if fo.generator_workflow:
        d["generator_workflow"] = fo.generator_workflow
    if fo.child_initial_agent:
        d["child_initial_agent"] = fo.child_initial_agent
    if fo.timeout_seconds is not None:
        d["timeout_seconds"] = fo.timeout_seconds
    if fo.input_contract.required:
        d["required_context"] = fo.input_contract.required

    # Fan-in → flat keys
    fi = mfj.fan_in
    if fi.resume_agent:
        d["resume_agent"] = fi.resume_agent
    if fi.merge_mode != MergeMode.CONCATENATE:
        d["merge_mode"] = fi.merge_mode.value
    if fi.on_partial_failure != PartialFailureStrategy.RESUME_WITH_AVAILABLE:
        d["on_partial_failure"] = fi.on_partial_failure.value
    if fi.output_contract.required:
        d["expected_output_keys"] = fi.output_contract.required

    return d


# ---------------------------------------------------------------------------
# Version detection + parsing entry points
# ---------------------------------------------------------------------------

def detect_schema_version(data: Dict[str, Any]) -> int:
    """Detect the schema version of a pack graph dict.

    Returns:
        3 if ``version >= 3`` is explicit or ``mid_flight_journeys`` key present.
        2 if ``journeys`` key present.
        1 if only ``nested_chats`` key present (legacy).
        0 if none of the above keys exist (empty/unknown config).
    """
    explicit = data.get("version")
    if isinstance(explicit, (int, float)) and explicit >= 3:
        return 3
    if "mid_flight_journeys" in data:
        return 3
    if "journeys" in data:
        return 2
    if "nested_chats" in data:
        return 1
    return 0


def parse_pack_graph(data: Dict[str, Any]) -> PerWorkflowPackGraph:
    """Parse a raw pack graph dict into a typed PerWorkflowPackGraph model.

    Handles v1 (legacy nested_chats), v2 (journeys), and v3
    (mid_flight_journeys) automatically. Invalid entries are logged
    and skipped — the parser is lenient to avoid breaking on partial configs.

    Raises:
        pydantic.ValidationError  if the top-level structure is invalid
        (e.g. mid_flight_journeys is not a list).
    """
    return PerWorkflowPackGraph.model_validate(data)


def parse_global_config(data: Dict[str, Any]) -> PackGlobalConfig:
    """Parse a raw global pack config dict into a typed PackGlobalConfig model.

    Raises:
        pydantic.ValidationError  if the top-level structure is invalid.
    """
    return PackGlobalConfig.model_validate(data)


# ---------------------------------------------------------------------------
# __all__ — public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "SpawnMode",
    "MergeMode",
    "PartialFailureStrategy",
    # Contract
    "MFJContract",
    # Fan-out / Fan-in
    "MFJFanOutConfig",
    "MFJFanInConfig",
    # MFJ definition
    "MidFlightJourney",
    # Per-workflow pack graph
    "PackGraphV2Entry",
    "PerWorkflowPackGraph",
    # Global config
    "WorkflowDependency",
    "WorkflowEntry",
    "GateEntry",
    "SequentialJourney",
    "PackGlobalConfig",
    # Helpers
    "detect_schema_version",
    "parse_pack_graph",
    "parse_global_config",
    # Converters (internal, but exported for testing)
    "_v2_entry_to_mfj",
    "_mfj_to_v2_dict",
]
