# === MOZAIKS-CORE-HEADER ===
# ==============================================================================
# FILE: core/workflow/pack/__init__.py
# DESCRIPTION: Public API surface for the pack subsystem.
#
# Re-exports from:
#   config.py  — pack config loading, journey/workflow accessors, gating logic
#   schema.py  — Pydantic models for pack config validation (v2/v3 schemas)
#   mfj_persistence.py — MongoDB-backed persistence for MFJ completion records
#   mfj_observability.py — Structured logging, OTel tracing, metrics for MFJ
#   gating.py  — DB-backed prerequisite validation
#   workflow_pack_coordinator.py — fan-out/fan-in + sequential journey advance
#
# For future coding agents:
#   - All pack config loading lives in config.py. No other loaders.
#   - Pydantic models live in schema.py. Import models from there.
#   - graph.py was consolidated into config.py (load_pack_graph, workflow_has_journeys).
#   - journey_orchestrator.py was merged into workflow_pack_coordinator.py.
# ==============================================================================

from .config import (
    compute_required_gates,
    get_journey,
    get_pack_config_path,
    get_workflow_entry,
    infer_auto_journey_for_start,
    journey_next_step,
    list_journeys,
    list_workflow_ids,
    load_pack_config,
    load_pack_graph,
    normalize_step_groups,
    workflow_has_journeys,
)
from .gating import list_workflow_availability, validate_pack_prereqs
from .mfj_persistence import MFJCompletionStore
from .mfj_observability import MFJObserver, MFJSpanContext, get_mfj_observer
from .schema import (
    MergeMode as SchemaMergeMode,
    MFJContract,
    MFJFanInConfig,
    MFJFanOutConfig,
    MidFlightJourney,
    PackGlobalConfig,
    PackGraphV2Entry,
    PartialFailureStrategy as SchemaPartialFailureStrategy,
    PerWorkflowPackGraph,
    SequentialJourney,
    SpawnMode,
    WorkflowDependency,
    WorkflowEntry,
    detect_schema_version,
    parse_global_config,
    parse_pack_graph,
)

__all__ = [
    "compute_required_gates",
    "get_journey",
    "get_pack_config_path",
    "get_workflow_entry",
    "infer_auto_journey_for_start",
    "journey_next_step",
    "list_journeys",
    "list_workflow_availability",
    "list_workflow_ids",
    "load_pack_config",
    "load_pack_graph",
    "normalize_step_groups",
    "validate_pack_prereqs",
    "workflow_has_journeys",
    # MFJ persistence
    "MFJCompletionStore",
    # MFJ observability
    "MFJObserver",
    "MFJSpanContext",
    "get_mfj_observer",
    # Schema models
    "detect_schema_version",
    "MFJContract",
    "MFJFanInConfig",
    "MFJFanOutConfig",
    "MidFlightJourney",
    "PackGlobalConfig",
    "PackGraphV2Entry",
    "parse_global_config",
    "parse_pack_graph",
    "PerWorkflowPackGraph",
    "SchemaMergeMode",
    "SchemaPartialFailureStrategy",
    "SequentialJourney",
    "SpawnMode",
    "WorkflowDependency",
    "WorkflowEntry",
]
