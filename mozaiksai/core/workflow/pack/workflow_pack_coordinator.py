# === MOZAIKS-CORE-HEADER ===
# ==============================================================================
# FILE: core/workflow/pack/workflow_pack_coordinator.py
# DESCRIPTION: Unified pack orchestrator — fan-out/fan-in AND sequential journey
#              auto-advance in a single class.
#
# This is the SINGLE ORCHESTRATOR for pack workflows. It handles two modes:
#
#   1. **Parallel MFJ fan-out / fan-in** (via structured_output_ready)
#      Triggered by agent structured outputs that match a per-workflow pack
#      graph trigger. Spawns child workflows, merges results, resumes parent.
#
#   2. **Sequential journey auto-advance** (via run_complete)
#      Triggered when a workflow completes that is part of a journey defined
#      in the global pack config. Advances to the next step in the journey.
#
# Previously these were two separate classes (WorkflowPackCoordinator and
# JourneyOrchestrator). They were merged because:
#   - Both are event-driven responses to the same lifecycle events
#   - Both need transport access, persistence, session registry
#   - Sequential advance is just a degenerate case of fan-out (1 child, no merge)
#   - Single class = single registration point in UnifiedEventDispatcher
#
# For future coding agents:
#   - handle_structured_output_ready → parallel MFJ fan-out
#   - handle_run_complete → first tries fan-in (child completion), then
#     falls back to sequential journey auto-advance
#   - Pack config loading uses config.load_pack_graph() (per-workflow) and
#     config.load_pack_config() (global). Do NOT add inline loaders.
#   - orchestration/events.py helpers are called at lifecycle boundaries
#     for observability. Don't remove them.
# ==============================================================================

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from logs.logging_config import get_core_logger

# WorkflowStatus is imported lazily inside _journey_advance_inner()
# to avoid pulling in the full data.models → persistence → autogen chain
# at import time (breaks lightweight test harnesses).
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.workflow.pack.config import (
    get_journey,
    load_pack_config,
    load_pack_graph,
    normalize_step_groups,
)
from mozaiksai.orchestration.decomposition import (
    AgentSignalDecomposition,
    DecompositionContext,
    DecompositionPlan,
    SubTask,
)
# orchestration.events helpers (emit_decomposition_started, emit_subtask_spawned,
# etc.) are imported lazily at each call site via _emit_event() to avoid pulling
# in core.events → auto_tool_handler → autogen at import time.
from mozaiksai.orchestration.merge import (
    ChildResult,
    ConcatenateMerge,
    DeepMergeMerge,
    FirstSuccessMerge,
    MajorityVoteMerge,
    MergeContext,
    MergeResult,
    MergeStrategy,
    StructuredMerge,
    get_merge_strategy_registry,
)
# MFJ persistence store is imported lazily in __init__ to keep test harnesses
# lightweight (they can pass mfj_store=None to skip MongoDB entirely).
from mozaiksai.core.workflow.pack.mfj_persistence import MFJCompletionStore
from mozaiksai.core.workflow.pack.mfj_observability import (
    MFJObserver,
    MFJSpanContext,
    get_mfj_observer,
)

logger = get_core_logger("workflow_pack_coordinator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_completed_status(value: Any) -> bool:
    """Check whether a run_complete payload's status indicates true completion.

    Accepts None (AG2 doesn't always set status), bool, int (1=completed),
    or common string values. Used by journey auto-advance to avoid advancing
    on failed/cancelled runs.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value) == 1
    if isinstance(value, str):
        return value.strip().lower() in {
            "completed", "complete", "success", "succeeded", "ok", "done",
        }
    return True


# ---------------------------------------------------------------------------
# Enums & value objects
# ---------------------------------------------------------------------------


def _resolve_triggers(pack_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract the trigger list from a pack graph dict (any schema version).

    Resolution order:
        1. ``mid_flight_journeys`` (v3) — converted to flat v2 dicts via
           schema.py so the coordinator's .get() patterns still work.
        2. ``journeys`` (v2).
        3. ``nested_chats`` (legacy, auto-normalized by config.py loader).

    Returns an empty list if no triggers are found.
    """
    # v3
    mfj = pack_cfg.get("mid_flight_journeys")
    if isinstance(mfj, list) and mfj:
        # If already dicts (raw JSON), return as-is.
        # If typed MidFlightJourney objects (rare — only if caller built them),
        # convert via schema helper.
        result: List[Dict[str, Any]] = []
        for item in mfj:
            if isinstance(item, dict):
                result.append(item)
            else:
                # Typed object — convert to dict
                try:
                    from mozaiksai.core.workflow.pack.schema import _mfj_to_v2_dict
                    result.append(_mfj_to_v2_dict(item))
                except Exception:
                    continue
        if result:
            return result

    # v2
    journeys = pack_cfg.get("journeys")
    if isinstance(journeys, list) and journeys:
        return [e for e in journeys if isinstance(e, dict)]

    # legacy (nested_chats may not yet be normalized)
    nc = pack_cfg.get("nested_chats")
    if isinstance(nc, list) and nc:
        return [e for e in nc if isinstance(e, dict)]

    return []


class PartialFailureStrategy(str, Enum):
    """What to do when some (but not all) children fail or time out."""
    RESUME_WITH_AVAILABLE = "resume_with_available"
    FAIL_ALL = "fail_all"
    RETRY_FAILED = "retry_failed"
    PROMPT_USER = "prompt_user"


class MergeMode(str, Enum):
    """Which built-in merge to use (or 'custom' for a registered strategy)."""
    CONCATENATE = "concatenate"
    STRUCTURED = "structured"
    COLLECT_ALL = "collect_all"  # backward compat — raw child_extra dump


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class _MFJCompletionRecord:
    """Tracks a completed MFJ trigger for multi-MFJ sequencing."""
    trigger_id: str
    parent_chat_id: str
    completed_at: datetime
    child_count: int
    all_succeeded: bool
    merge_summary_preview: str = ""


@dataclass
class _ActivePackRun:
    parent_chat_id: str
    parent_workflow_name: str
    app_id: str
    user_id: str
    ws_id: Optional[int]
    resume_agent: Optional[str]
    child_chat_ids: List[str]
    # --- Kernel bridge ---
    decomposition_plan: Optional[DecompositionPlan] = None
    merge_strategy: Optional[MergeStrategy] = None
    # --- MFJ config ---
    trigger_id: Optional[str] = None
    trigger_agent: str = ""
    spawn_mode: str = "workflow"
    timeout_seconds: Optional[float] = None
    on_partial_failure: PartialFailureStrategy = PartialFailureStrategy.RESUME_WITH_AVAILABLE
    timeout_task: Optional[asyncio.Task] = field(default=None, repr=False)
    # --- child task_id → chat_id mapping ---
    task_to_chat: Dict[str, str] = field(default_factory=dict)
    # --- UI event enrichment (Phase 6) ---
    mfj_description: str = ""  # human-readable label from trigger config
    mfj_cycle: int = 0          # 1-indexed cycle number for this parent
    # --- MFJ Observer (Phase 8) ---
    observer_ctx: Optional[MFJSpanContext] = None


# ---------------------------------------------------------------------------
# Input / output contracts
# ---------------------------------------------------------------------------

class FanOutContractError(Exception):
    """Raised when parent context fails pre-fan-out validation."""


class FanInContractError(Exception):
    """Raised when child outputs fail pre-merge validation."""


def _validate_fan_out_context(
    trigger_entry: Dict[str, Any],
    context_vars: Dict[str, Any],
) -> None:
    """Check that the parent context satisfies the trigger's ``required_context`` list.

    ``workflow_graph.json`` trigger entry may include::

        "required_context": ["InterviewTranscript", "PatternSelection"]

    Raises ``FanOutContractError`` if any required key is missing.
    """
    required = trigger_entry.get("required_context")
    if not isinstance(required, list) or not required:
        return
    missing = [k for k in required if k not in context_vars]
    if missing:
        raise FanOutContractError(
            f"Parent context missing required keys for fan-out: {missing}"
        )


def _validate_child_outputs(
    child_results: List[ChildResult],
    trigger_entry: Dict[str, Any],
) -> List[str]:
    """Validate child outputs against ``expected_output_keys`` in trigger config.

    Returns a list of warning strings (empty if all clean).
    """
    expected = trigger_entry.get("expected_output_keys")
    if not isinstance(expected, list) or not expected:
        return []
    warnings: List[str] = []
    for cr in child_results:
        if not cr.success:
            continue
        for key in expected:
            if key not in cr.structured_output:
                warnings.append(
                    f"Child {cr.task_id} ({cr.workflow_name}) missing "
                    f"expected output key '{key}'"
                )
    return warnings


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class WorkflowPackCoordinator:
    """Transport-level coordinator for fan-out / fan-in (MFJ).

    Bridges kernel abstractions with the production transport layer:

    * Uses ``AgentSignalDecomposition`` to produce a ``DecompositionPlan``
      from structured outputs (falling back to raw ``_extract_pack_plan``
      for backward compatibility with older agent outputs).
    * Uses pluggable ``MergeStrategy`` (default: ``ConcatenateMerge``) for
      fan-in aggregation instead of raw MongoDB dumps.
    * Enforces input/output contracts (``required_context``, ``expected_output_keys``).
    * Supports multi-MFJ sequencing via ``requires`` fields on trigger entries.
    * Supports configurable timeout and partial-failure strategies.

    Thread-safety: **not thread-safe**.  One coordinator per event loop.
    """

    def __init__(
        self,
        *,
        default_merge_strategy: Optional[MergeStrategy] = None,
        default_timeout_seconds: Optional[float] = None,
        default_partial_failure: PartialFailureStrategy = PartialFailureStrategy.RESUME_WITH_AVAILABLE,
        mfj_store: Optional[MFJCompletionStore] = None,
    ) -> None:
        # --- Fan-out / fan-in state ---
        self._active_by_parent: Dict[str, _ActivePackRun] = {}
        self._active_by_child: Dict[str, str] = {}
        # MFJ completion history (parent_chat_id → list of completions).
        # This is the hot cache.  MongoDB backing store (if available)
        # provides durability across restarts.
        self._completed_mfjs: Dict[str, List[_MFJCompletionRecord]] = {}
        # MongoDB-backed persistence for MFJ completions.
        # Pass ``mfj_store=None`` in tests to disable persistence.
        self._mfj_store: Optional[MFJCompletionStore] = mfj_store
        # MFJ cycle counter per parent (tracks how many fan-out/fan-in
        # cycles this parent has gone through).  Used for UI enrichment.
        self._mfj_cycle_counter: Dict[str, int] = {}
        # Strategy instances
        self._decomposition_strategy = AgentSignalDecomposition()
        self._default_merge: MergeStrategy = default_merge_strategy or ConcatenateMerge()
        self._default_timeout = default_timeout_seconds
        self._default_partial_failure = default_partial_failure
        # --- MFJ Observer (Phase 8: Observability) ---
        self._observer: MFJObserver = get_mfj_observer()
        # --- Sequential journey auto-advance state ---
        # Per-chat_id idempotency locks to prevent duplicate advances
        # from concurrent run_complete events.
        self._journey_locks: Dict[str, asyncio.Lock] = {}

    # ===================================================================
    # Fan-out: handle_structured_output_ready
    # ===================================================================

    async def handle_structured_output_ready(self, event: Dict[str, Any]) -> None:
        """React to a structured output that may trigger decomposition.

        Uses ``AgentSignalDecomposition.detect()`` to produce a
        ``DecompositionPlan``, then spawns children via transport primitives.
        """
        try:
            agent_name = str(event.get("agent_name") or event.get("agent") or "")
            model_name = str(event.get("model_name") or "")
            structured_data = event.get("structured_data")
            context = event.get("context") or {}
            parent_chat_id = str(context.get("chat_id") or "")
            parent_workflow = str(context.get("workflow_name") or "")
        except Exception as exc:  # pragma: no cover
            logger.debug("[PACK] Malformed structured_output_ready event: %s", exc)
            return

        if not parent_chat_id or not parent_workflow or not agent_name:
            return

        pack_cfg = self._load_pack_graph(parent_workflow)
        if not pack_cfg:
            return

        # Resolve trigger list from v3 (mid_flight_journeys), v2 (journeys),
        # or legacy (nested_chats).  Returns flat dicts in all cases.
        triggers = _resolve_triggers(pack_cfg)
        if not triggers:
            return

        # Only one active MFJ per parent chat at a time.
        if parent_chat_id in self._active_by_parent:
            # Observer: duplicate suppressed (trigger_id resolved below, use "unknown")
            self._observer.on_duplicate_suppressed(
                trigger_id="(active)",
                parent_chat_id=parent_chat_id,
            )
            return

        # Match trigger agent.
        trigger_entry = None
        for entry in triggers:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("trigger_agent") or "") == agent_name:
                trigger_entry = entry
                break
        if not trigger_entry:
            return

        trigger_id = str(trigger_entry.get("id") or f"trigger_{agent_name}")

        # --- Multi-MFJ sequencing: check `requires` ---
        requires = trigger_entry.get("requires")
        if isinstance(requires, list) and requires:
            if not await self._check_mfj_requires(parent_chat_id, requires):
                logger.info(
                    "[PACK] MFJ trigger_id=%s blocked — requires %s not yet completed",
                    trigger_id,
                    requires,
                )
                return

        # --- Build DecompositionPlan via kernel strategy ---
        decomp_ctx = DecompositionContext(
            run_id=parent_chat_id,
            workflow_name=parent_workflow,
            app_id="",  # populated below after transport lookup
            user_id="",
            trigger_event={
                "structured_data": structured_data,
                "agent_name": agent_name,
            },
            pack_config=pack_cfg,
            context_variables=context,
        )

        plan = self._decomposition_strategy.detect(decomp_ctx)

        # Fallback: if strategy doesn't detect, try raw extraction.
        if plan is None:
            raw_plan = self._extract_pack_plan(structured_data)
            if raw_plan and raw_plan.get("is_multi_workflow"):
                plan = self._plan_from_raw(raw_plan, trigger_entry)

        if plan is None or plan.task_count == 0:
            return

        # --- Resolve transport context ---
        spawn_mode = str(trigger_entry.get("spawn_mode") or "workflow").strip().lower()
        if spawn_mode not in ("workflow", "generator_subrun"):
            spawn_mode = "workflow"

        generator_workflow = None
        if spawn_mode == "generator_subrun":
            generator_workflow = str(trigger_entry.get("generator_workflow") or "").strip()
            if not generator_workflow:
                logger.warning(
                    "[PACK] spawn_mode=generator_subrun requires trigger_entry.generator_workflow (parent=%s)",
                    parent_workflow,
                )
                return

        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        if not transport:
            logger.warning("[PACK] SimpleTransport unavailable; cannot spawn")
            return

        parent_conn = transport.connections.get(parent_chat_id) or {}
        app_id = parent_conn.get("app_id")
        user_id = parent_conn.get("user_id")
        ws_id = parent_conn.get("ws_id")

        if not app_id or not user_id:
            logger.debug("[PACK] Missing app_id/user_id for parent chat=%s", parent_chat_id)
            return

        # --- Input contract validation ---
        try:
            _validate_fan_out_context(trigger_entry, context)
        except FanOutContractError as exc:
            logger.warning("[PACK] Fan-out contract failed for parent=%s: %s", parent_chat_id, exc)
            self._observer.on_contract_violation(
                trigger_id=trigger_id,
                parent_chat_id=parent_chat_id,
                violation=str(exc),
            )
            return

        # --- Pre-filter spawnable sub-tasks ---
        spawnable: List[SubTask] = []
        for st in plan.sub_tasks:
            if spawn_mode == "workflow":
                if not (Path("workflows") / st.workflow_name).exists():
                    logger.info("[PACK] Skipping spawn for missing workflow=%s", st.workflow_name)
                    continue
            spawnable.append(st)

        if not spawnable:
            return

        # Resume agent: plan first, then trigger config.
        resume_agent = plan.resume_agent
        if not resume_agent:
            cfg_resume = trigger_entry.get("resume_agent")
            if isinstance(cfg_resume, str) and cfg_resume.strip():
                resume_agent = cfg_resume.strip()

        # --- Resolve merge strategy ---
        merge_mode_str = str(trigger_entry.get("merge_mode") or "concatenate").strip().lower()
        merge_strategy = self._resolve_merge_strategy(merge_mode_str)

        # --- Timeout config ---
        timeout_seconds = trigger_entry.get("timeout_seconds")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            timeout_seconds = self._default_timeout  # may be None (no timeout)

        # --- Partial failure config ---
        pf_str = str(trigger_entry.get("on_partial_failure") or "").strip().lower()
        try:
            on_partial_failure = PartialFailureStrategy(pf_str) if pf_str else self._default_partial_failure
        except ValueError:
            on_partial_failure = self._default_partial_failure

        # --- Pause parent ---
        try:
            await transport.pause_background_workflow(chat_id=parent_chat_id, reason="spawn_children")
        except Exception as exc:
            logger.debug("[PACK] Failed pausing parent chat=%s: %s", parent_chat_id, exc)

        # --- Spawn children ---
        child_chat_ids: List[str] = []
        started_payloads: List[Dict[str, Any]] = []
        task_to_chat: Dict[str, str] = {}

        pm = transport._get_or_create_persistence_manager()

        for st in spawnable:
            target_workflow_name = st.workflow_name
            child_workflow_name = target_workflow_name if spawn_mode == "workflow" else str(generator_workflow)

            # Resolve initial agent.
            initial_agent_override = st.initial_agent_override
            if not initial_agent_override:
                cfg_initial_agent = trigger_entry.get("child_initial_agent")
                if spawn_mode == "generator_subrun" and isinstance(cfg_initial_agent, str) and cfg_initial_agent.strip():
                    initial_agent_override = cfg_initial_agent.strip()

            # Resolve initial message.
            initial_message = st.initial_message
            if not initial_message:
                if spawn_mode == "generator_subrun":
                    wf_desc = st.metadata.get("description", "")
                    if wf_desc:
                        initial_message = f"Generate a new workflow named '{target_workflow_name}'. Description: {wf_desc}"
                    else:
                        initial_message = f"Generate a new workflow named '{target_workflow_name}'."

            # Generate chat_id.
            if spawn_mode == "generator_subrun":
                new_chat_id = f"chat_gen_{target_workflow_name}_{uuid.uuid4().hex[:8]}"
            else:
                new_chat_id = f"chat_{child_workflow_name}_{uuid.uuid4().hex[:8]}"

            extra_fields: Dict[str, Any] = {
                "parent_chat_id": parent_chat_id,
                "parent_workflow_name": parent_workflow,
                "spawn_mode": spawn_mode,
                "spawn_trigger_agent": agent_name,
                "spawn_trigger_id": trigger_id,
                "task_id": st.task_id,
            }

            if spawn_mode == "generator_subrun":
                wf_meta: Dict[str, Any] = dict(st.metadata)
                wf_meta["name"] = target_workflow_name
                pattern_selection = {
                    "is_multi_workflow": False,
                    "resume_agent": None,
                    "decomposition_reason": None,
                    "pack_name": target_workflow_name,
                    "workflows": [wf_meta],
                }
                extra_fields.update(
                    {
                        "is_child_workflow": True,
                        "generated_workflow_name": target_workflow_name,
                        "generated_workflow_description": st.metadata.get("description"),
                        "PatternSelection": pattern_selection,
                        "pattern_selection": pattern_selection,
                        "current_workflow_index": 0,
                        "InterviewTranscript": initial_message,
                    }
                )

            await pm.create_chat_session(
                chat_id=new_chat_id,
                app_id=str(app_id),
                workflow_name=str(child_workflow_name),
                user_id=str(user_id),
                extra_fields=extra_fields,
            )

            # Session registry.
            try:
                if ws_id is not None:
                    from mozaiksai.core.transport.session_registry import session_registry

                    session_registry.add_workflow(
                        ws_id=ws_id,
                        chat_id=new_chat_id,
                        workflow_name=str(child_workflow_name),
                        app_id=str(app_id),
                        user_id=str(user_id),
                        auto_activate=False,
                    )
            except Exception:
                pass

            child_chat_ids.append(new_chat_id)
            task_to_chat[st.task_id] = new_chat_id
            started_payloads.append(
                {
                    "chat_id": new_chat_id,
                    "workflow_name": str(child_workflow_name),
                    "app_id": str(app_id),
                    "user_id": str(user_id),
                    "task_id": st.task_id,
                    **({"generated_workflow_name": target_workflow_name} if spawn_mode == "generator_subrun" else {}),
                }
            )

            transport._background_tasks[new_chat_id] = asyncio.create_task(
                transport._run_workflow_background(
                    chat_id=new_chat_id,
                    workflow_name=str(child_workflow_name),
                    app_id=str(app_id),
                    user_id=str(user_id),
                    ws_id=ws_id,
                    initial_message=initial_message,
                    initial_agent_name_override=initial_agent_override,
                )
            )
            self._active_by_child[new_chat_id] = parent_chat_id

        if not child_chat_ids:
            await self._resume_parent(
                transport=transport,
                parent_chat_id=parent_chat_id,
                parent_workflow=parent_workflow,
                app_id=str(app_id),
                user_id=str(user_id),
                ws_id=int(ws_id) if isinstance(ws_id, int) else None,
                resume_agent=resume_agent,
            )
            return

        # Compute MFJ cycle number (1-indexed) for UI progress.
        mfj_cycle = self._mfj_cycle_counter.get(parent_chat_id, 0) + 1
        self._mfj_cycle_counter[parent_chat_id] = mfj_cycle
        mfj_description = str(trigger_entry.get("description") or "")

        active_run = _ActivePackRun(
            parent_chat_id=parent_chat_id,
            parent_workflow_name=parent_workflow,
            app_id=str(app_id),
            user_id=str(user_id),
            ws_id=int(ws_id) if isinstance(ws_id, int) else None,
            resume_agent=resume_agent,
            child_chat_ids=child_chat_ids,
            decomposition_plan=plan,
            merge_strategy=merge_strategy,
            trigger_id=trigger_id,
            trigger_agent=agent_name,
            spawn_mode=spawn_mode,
            timeout_seconds=timeout_seconds,
            on_partial_failure=on_partial_failure,
            task_to_chat=task_to_chat,
            mfj_description=mfj_description,
            mfj_cycle=mfj_cycle,
        )
        self._active_by_parent[parent_chat_id] = active_run

        # --- Observer: fan-out started + per-child spawn ---
        obs_ctx = self._observer.on_fan_out_started(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            child_count=len(child_chat_ids),
            merge_mode=merge_mode_str,
            timeout_seconds=timeout_seconds,
            workflow_name=parent_workflow,
            cycle=mfj_cycle,
        )
        for sp in started_payloads:
            self._observer.on_child_spawned(
                obs_ctx,
                task_id=sp.get("task_id", ""),
                workflow_name=sp.get("workflow_name", ""),
            )
        self._observer.on_fan_out_completed(obs_ctx)
        active_run.observer_ctx = obs_ctx

        # --- Start timeout watchdog ---
        if timeout_seconds and timeout_seconds > 0:
            active_run.timeout_task = asyncio.create_task(
                self._timeout_watchdog(parent_chat_id, timeout_seconds)
            )

        # --- Notify UI ---
        try:
            await transport.send_event_to_ui(
                {
                    "type": "chat.workflow_batch_started",
                    "data": {
                        "parent_chat_id": parent_chat_id,
                        "parent_workflow_name": parent_workflow,
                        "resume_agent": resume_agent,
                        "count": len(started_payloads),
                        "workflows": started_payloads,
                        "timeout_seconds": timeout_seconds,
                        "on_partial_failure": on_partial_failure.value,
                        # Phase 6 enrichment
                        "trigger_id": trigger_id,
                        "mfj_description": mfj_description,
                        "mfj_cycle": mfj_cycle,
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                parent_chat_id,
            )
        except Exception:
            pass

        # --- Emit kernel orchestration events for observability ---
        try:
            from mozaiksai.orchestration.events import (
                emit_decomposition_started,
                emit_subtask_spawned,
            )
            emit_decomposition_started(
                run_id=parent_chat_id,
                workflow_name=parent_workflow,
                task_count=len(started_payloads),
                execution_mode="parallel",
                reason=plan.reason if plan else "structured_output trigger",
                sub_tasks=[
                    {"task_id": sp.get("task_id", ""), "workflow_name": sp.get("workflow_name", "")}
                    for sp in started_payloads
                ],
            )
            for sp in started_payloads:
                emit_subtask_spawned(
                    parent_run_id=parent_chat_id,
                    task_id=sp.get("task_id", ""),
                    workflow_name=sp.get("workflow_name", ""),
                    child_run_id=sp.get("chat_id", ""),
                )
        except Exception:
            pass  # Observability events must never block orchestration.

        logger.info(
            "[PACK] Spawned %d child workflows for parent chat=%s "
            "trigger=%s agent=%s model=%s merge=%s timeout=%s",
            len(child_chat_ids),
            parent_chat_id,
            trigger_id,
            agent_name,
            model_name,
            merge_mode_str,
            timeout_seconds,
        )

    # ===================================================================
    # Fan-in: handle_run_complete
    # ===================================================================

    async def handle_run_complete(self, payload: Dict[str, Any]) -> None:
        """Called when transport emits a run_complete envelope for any chat.

        Two-phase dispatch:
          1. If this chat_id is a known MFJ fan-out child → fan-in merge path.
          2. Otherwise → sequential journey auto-advance path.

        Phase 1 collects child results, applies ``MergeStrategy``, writes
        merged output to parent session, records MFJ completion, resumes parent.

        Phase 2 loads the global pack config, finds the journey containing
        the completed workflow, and spawns the next step(s).
        """
        try:
            chat_id = str(payload.get("chat_id") or "")
        except Exception:
            return
        if not chat_id:
            return

        # --- Phase 1: Fan-in child completion ---
        parent_chat_id = self._active_by_child.get(chat_id)
        if parent_chat_id:
            await self._handle_fan_in_completion(chat_id, parent_chat_id)
            return

        # --- Phase 2: Sequential journey auto-advance ---
        # Only advance on truly completed runs (not failed/cancelled).
        if not _is_completed_status(payload.get("status")):
            return
        await self._handle_journey_auto_advance(payload, chat_id)

    # ===================================================================
    # Fan-in: child completion → merge → resume parent
    # ===================================================================

    async def _handle_fan_in_completion(self, chat_id: str, parent_chat_id: str) -> None:
        """Process a completed MFJ child: merge results when all done, resume parent."""
        active = self._active_by_parent.get(parent_chat_id)
        if not active:
            return

        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        if not transport:
            return

        # Check if ALL children are done.
        all_done = True
        done_count = 0
        for child_id in list(active.child_chat_ids):
            t = transport._background_tasks.get(child_id)
            if t and not t.done():
                all_done = False
            else:
                done_count += 1

        total_children = len(active.child_chat_ids)
        child_index = active.child_chat_ids.index(chat_id) + 1 if chat_id in active.child_chat_ids else done_count

        if not all_done:
            # --- Emit per-child completion event for UI progress ---
            try:
                await transport.send_event_to_ui(
                    {
                        "type": "chat.workflow_child_completed",
                        "data": {
                            "parent_chat_id": parent_chat_id,
                            "child_chat_id": chat_id,
                            "child_index": child_index,
                            "child_total": total_children,
                            "done_count": done_count,
                            "trigger_id": active.trigger_id or "",
                            "mfj_cycle": active.mfj_cycle,
                            "success": True,
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    parent_chat_id,
                )
            except Exception:
                pass
            return

        # --- All children done: emit fan-in started event ---
        try:
            await transport.send_event_to_ui(
                {
                    "type": "chat.mfj_fan_in_started",
                    "data": {
                        "parent_chat_id": parent_chat_id,
                        "child_total": total_children,
                        "trigger_id": active.trigger_id or "",
                        "mfj_description": active.mfj_description,
                        "mfj_cycle": active.mfj_cycle,
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                parent_chat_id,
            )
        except Exception:
            pass

        # Cancel timeout watchdog.
        if active.timeout_task and not active.timeout_task.done():
            active.timeout_task.cancel()

        # Cleanup indexes.
        for child_id in list(active.child_chat_ids):
            self._active_by_child.pop(child_id, None)
        self._active_by_parent.pop(parent_chat_id, None)

        # --- Build ChildResult list from MongoDB ---
        pm = transport._get_or_create_persistence_manager()
        child_results = await self._collect_child_results(active, pm)

        # --- Observer: fan-in started ---
        obs_ctx = active.observer_ctx or MFJSpanContext()
        self._observer.on_fan_in_started(
            obs_ctx,
            available_count=len(child_results),
            total_count=len(active.child_chat_ids),
            trigger_id=active.trigger_id or "",
            parent_chat_id=active.parent_chat_id,
        )

        # --- Output contract validation (warnings only) ---
        trigger_entry = self._find_trigger_entry(active)
        if trigger_entry:
            warnings = _validate_child_outputs(child_results, trigger_entry)
            for w in warnings:
                logger.warning("[PACK] Output contract: %s", w)

        # --- Apply MergeStrategy ---
        merge_strategy = active.merge_strategy or self._default_merge
        merge_result = self._apply_merge(active, child_results, merge_strategy)

        # --- Write merge result to parent session ---
        try:
            await pm.patch_session_fields(
                chat_id=active.parent_chat_id,
                app_id=active.app_id,
                fields={
                    "child_results": merge_result.merged_data,
                    "merge_summary": merge_result.summary_message[:4000],
                    "merge_all_succeeded": merge_result.all_succeeded,
                    "merge_succeeded_count": merge_result.succeeded_count,
                    "merge_failed_count": merge_result.failed_count,
                },
            )
            logger.info(
                "[PACK] Merged %d child results into parent chat=%s "
                "(succeeded=%d failed=%d strategy=%s)",
                len(child_results),
                active.parent_chat_id,
                merge_result.succeeded_count,
                merge_result.failed_count,
                type(merge_strategy).__name__,
            )
        except Exception as exc:
            logger.warning(
                "[PACK] Fan-in merge write failed for parent=%s: %s",
                active.parent_chat_id,
                exc,
            )

        # --- Record MFJ completion for multi-MFJ sequencing ---
        if active.trigger_id:
            await self._record_mfj_completion(
                parent_chat_id=active.parent_chat_id,
                trigger_id=active.trigger_id,
                child_count=len(child_results),
                all_succeeded=merge_result.all_succeeded,
                child_chat_ids=list(active.child_chat_ids),
                merge_summary_preview=merge_result.summary_message[:200],
            )

        # --- Emit kernel orchestration events for observability ---
        try:
            from mozaiksai.orchestration.events import (
                emit_decomposition_completed,
                emit_merge_completed,
            )
            emit_decomposition_completed(
                run_id=active.parent_chat_id,
                workflow_name=active.parent_workflow_name,
                total=len(child_results),
                succeeded=merge_result.succeeded_count,
                failed=merge_result.failed_count,
            )
            emit_merge_completed(
                run_id=active.parent_chat_id,
                workflow_name=active.parent_workflow_name,
                all_succeeded=merge_result.all_succeeded,
                summary_preview=merge_result.summary_message[:500],
            )
        except Exception:
            pass  # Observability events must never block orchestration.

        # --- Resume parent ---
        await self._resume_parent(
            transport=transport,
            parent_chat_id=active.parent_chat_id,
            parent_workflow=active.parent_workflow_name,
            app_id=active.app_id,
            user_id=active.user_id,
            ws_id=active.ws_id,
            resume_agent=active.resume_agent,
            merge_summary=merge_result.summary_message,
            trigger_id=active.trigger_id,
            mfj_cycle=active.mfj_cycle,
            succeeded_count=merge_result.succeeded_count,
            failed_count=merge_result.failed_count,
        )

        # --- Observer: fan-in completed + cycle done ---
        self._observer.on_fan_in_completed(
            obs_ctx,
            strategy=type(merge_strategy).__name__,
            succeeded=merge_result.succeeded_count,
            failed=merge_result.failed_count,
            trigger_id=active.trigger_id or "",
            parent_chat_id=active.parent_chat_id,
            workflow_name=active.parent_workflow_name,
        )
        self._observer.on_cycle_completed(
            obs_ctx,
            success=merge_result.all_succeeded,
            trigger_id=active.trigger_id or "",
            parent_chat_id=active.parent_chat_id,
        )

    # ===================================================================
    # Sequential journey auto-advance (merged from JourneyOrchestrator)
    # ===================================================================

    async def _handle_journey_auto_advance(self, payload: Dict[str, Any], chat_id: str) -> None:
        """Auto-advance to the next step in a sequential journey.

        When a workflow completes that belongs to a journey defined in the
        global pack config, this method:
          1. Resolves the journey and current step index
          2. Waits for all parallel workflows in the current group to complete
          3. Validates prerequisites for the next group
          4. Creates sessions and spawns all workflows in the next group
          5. Emits context_switched event for UI navigation

        Idempotency: uses an asyncio.Lock per chat_id to prevent duplicate
        advances from concurrent run_complete events.
        """
        pack = load_pack_config()
        if not pack:
            return

        workflow_name = str(payload.get("workflow_name") or payload.get("workflow") or "").strip()
        app_id = str(payload.get("app_id") or payload.get("app") or "").strip()
        user_id = str(payload.get("user_id") or payload.get("user") or "").strip()

        # Best-effort: infer missing fields from transport connection metadata.
        conn, transport = await self._get_transport_conn(chat_id)
        if conn:
            workflow_name = workflow_name or str(conn.get("workflow_name") or "").strip()
            app_id = app_id or str(conn.get("app_id") or "").strip()
            user_id = user_id or str(conn.get("user_id") or "").strip()

        if not workflow_name or not app_id or not user_id:
            return
        if not transport or not conn:
            return

        websocket = conn.get("websocket")
        ws_id = conn.get("ws_id")
        if websocket is None or ws_id is None:
            return

        # Use idempotency lock to prevent duplicate advances.
        lock = self._journey_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            try:
                await self._journey_advance_inner(
                    chat_id=chat_id,
                    pack=pack,
                    workflow_name=workflow_name,
                    app_id=app_id,
                    user_id=user_id,
                    ws_id=ws_id,
                    conn=conn,
                    transport=transport,
                )
            except Exception as exc:
                logger.error("[JOURNEY] auto-advance failed for chat=%s: %s", chat_id, exc, exc_info=True)

    async def _journey_advance_inner(
        self,
        *,
        chat_id: str,
        pack: Dict[str, Any],
        workflow_name: str,
        app_id: str,
        user_id: str,
        ws_id: Any,
        conn: Dict[str, Any],
        transport: Any,
    ) -> None:
        """Core journey auto-advance logic (runs under idempotency lock)."""
        from mozaiksai.core.workflow.pack.gating import validate_pack_prereqs

        pm = transport._get_or_create_persistence_manager()
        coll = await pm._coll()

        # Load the session document to get journey metadata.
        doc = await coll.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)},
            projection={
                "_id": 1,
                "workflow_name": 1,
                "status": 1,
                "journey_id": 1,
                "journey_key": 1,
                "journey_step_index": 1,
            },
        )

        journey_key = str((doc or {}).get("journey_key") or "").strip()
        journey_id = str((doc or {}).get("journey_id") or "").strip()

        # Resolve journey: first by tagged key, then by inference.
        journey = get_journey(pack, journey_key) if journey_key else None
        if not journey:
            inferred = self._infer_unique_auto_advance_journey(pack, workflow_name)
            if inferred:
                journey = inferred
                journey_key = str(journey.get("id") or "").strip()

        if not journey:
            return

        groups = normalize_step_groups(journey.get("steps"))
        if not groups:
            return

        # Find which group the completed workflow belongs to.
        current_group_index: Optional[int] = None
        for idx, group in enumerate(groups):
            if workflow_name in group:
                current_group_index = idx
                break
        if current_group_index is None:
            return
        if current_group_index >= len(groups) - 1:
            return  # Already at last step — nothing to advance to.

        # Ensure we have a journey_id for correlating parallel groups.
        if not journey_id:
            journey_id = str(uuid.uuid4())
            try:
                await coll.update_one(
                    {"_id": chat_id, **build_app_scope_filter(app_id)},
                    {
                        "$set": {
                            "journey_id": journey_id,
                            "journey_key": journey_key or str(journey.get("id") or "").strip(),
                            "journey_step_index": int(current_group_index),
                            "journey_total_steps": len(groups),
                        }
                    },
                )
            except Exception:
                return

        # If this is a parallel group, wait for ALL workflows in the group to complete.
        current_group = groups[current_group_index]
        # Lazy import — see top-of-file comment on why WorkflowStatus
        # is not a top-level import.
        from mozaiksai.core.data.models import WorkflowStatus as _WS
        for wf in current_group:
            try:
                doc_done = await coll.find_one(
                    {
                        "journey_id": journey_id,
                        "journey_step_index": int(current_group_index),
                        "workflow_name": wf,
                        "status": int(_WS.COMPLETED),
                        **build_app_scope_filter(app_id),
                    },
                    projection={"_id": 1, "status": 1},
                    sort=[("completed_at", -1), ("created_at", -1)],
                )
                if not doc_done:
                    return  # Not all group members done yet.
            except Exception:
                return

        next_group_index = current_group_index + 1
        next_group = groups[next_group_index]
        if not next_group:
            return

        # Mark completed workflow in session registry.
        try:
            from mozaiksai.core.transport.session_registry import session_registry

            session_registry.complete_workflow(ws_id, chat_id)
        except Exception:
            pass

        # Validate prerequisites and spawn next-group workflows.
        spawned: List[tuple[str, str, bool]] = []  # (workflow_name, chat_id, created_new)
        for wf in next_group:
            ok, prereq_error = await validate_pack_prereqs(
                app_id=app_id,
                user_id=user_id,
                workflow_name=wf,
                persistence=pm,
            )
            if not ok:
                await transport.send_event_to_ui(
                    {
                        "type": "chat.error",
                        "data": {
                            "message": prereq_error or "Prerequisites not met",
                            "error_code": "WORKFLOW_PREREQS_NOT_MET",
                            "workflow_name": wf,
                            "chat_id": chat_id,
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    chat_id,
                )
                return

            # Check for existing session (idempotency — avoid duplicate sessions).
            existing_next = await coll.find_one(
                {
                    "journey_id": journey_id,
                    "journey_step_index": int(next_group_index),
                    "workflow_name": wf,
                    **build_app_scope_filter(app_id),
                },
                projection={"_id": 1, "status": 1},
                sort=[("created_at", -1)],
            )
            next_chat_id = (
                str(existing_next.get("_id"))
                if isinstance(existing_next, dict) and existing_next.get("_id")
                else ""
            )
            created_new = False
            if not next_chat_id:
                next_chat_id = str(uuid.uuid4())
                await pm.create_chat_session(
                    chat_id=next_chat_id,
                    app_id=app_id,
                    workflow_name=wf,
                    user_id=user_id,
                    extra_fields={
                        "journey_id": journey_id,
                        "journey_key": journey_key or str(journey.get("id") or "").strip(),
                        "journey_step_index": int(next_group_index),
                        "journey_total_steps": len(groups),
                    },
                )
                created_new = True
            spawned.append((wf, next_chat_id, created_new))

            # Alias the WebSocket connection so the new chat can send messages.
            self._ensure_connection_alias(
                transport=transport,
                source_conn=conn,
                target_chat_id=next_chat_id,
                workflow_name=wf,
                app_id=app_id,
                user_id=user_id,
            )
            await self._flush_pre_connection_buffers(transport=transport, chat_id=next_chat_id)

        if not spawned:
            return

        # Register in session registry. Last workflow is the primary/active one.
        from mozaiksai.core.transport.session_registry import session_registry

        primary_workflow, primary_chat_id, _ = spawned[-1]
        session_registry.add_workflow(
            ws_id=ws_id,
            chat_id=primary_chat_id,
            workflow_name=primary_workflow,
            app_id=app_id,
            user_id=user_id,
            auto_activate=True,
        )
        for wf, cid, _ in spawned:
            if cid == primary_chat_id:
                continue
            session_registry.add_workflow(
                ws_id=ws_id,
                chat_id=cid,
                workflow_name=wf,
                app_id=app_id,
                user_id=user_id,
                auto_activate=False,
            )

        # Notify UI about context switch.
        await transport.send_event_to_ui(
            {
                "type": "chat.context_switched",
                "data": {
                    "from_chat_id": chat_id,
                    "to_chat_id": primary_chat_id,
                    "workflow_name": primary_workflow,
                    "app_id": app_id,
                    "journey_id": journey_id,
                    "journey_key": journey_key,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            chat_id,
        )

        # Start all workflows in the next group concurrently.
        for wf, cid, _ in spawned:
            try:
                transport._background_tasks[cid] = asyncio.create_task(
                    transport._run_workflow_background(
                        chat_id=cid,
                        workflow_name=wf,
                        app_id=app_id,
                        user_id=user_id,
                        ws_id=ws_id,
                        initial_message=None,
                        initial_agent_name_override=None,
                    )
                )
            except Exception:
                continue

        logger.info(
            "[JOURNEY] Auto-advanced from %s (chat=%s) to step %d: %s",
            workflow_name,
            chat_id,
            next_group_index,
            [wf for wf, _, _ in spawned],
        )

    # ===================================================================
    # Journey helpers
    # ===================================================================

    def _infer_unique_auto_advance_journey(
        self,
        pack: Dict[str, Any],
        workflow_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Find a journey that contains ``workflow_name`` (if exactly one matches).

        Used when the session document doesn't have journey_key metadata,
        which happens when the user started the workflow directly rather
        than through a journey-aware start endpoint.
        """
        wf = str(workflow_name or "").strip()
        if not wf:
            return None
        candidates = []
        journeys = pack.get("journeys") or []
        if not isinstance(journeys, list):
            return None
        for j in journeys:
            if not isinstance(j, dict):
                continue
            groups = normalize_step_groups(j.get("steps"))
            if not groups:
                continue
            if any(wf in group for group in groups):
                candidates.append(j)
        if len(candidates) == 1:
            return candidates[0]
        return None

    @staticmethod
    def _ensure_connection_alias(
        *,
        transport: Any,
        source_conn: Dict[str, Any],
        target_chat_id: str,
        workflow_name: str,
        app_id: str,
        user_id: str,
    ) -> None:
        """Copy WebSocket connection metadata from source to target chat_id.

        This allows the next-step workflow to send messages over the same
        WebSocket without requiring the client to reconnect.
        """
        if not target_chat_id:
            return
        websocket = source_conn.get("websocket")
        ws_id = source_conn.get("ws_id")
        if websocket is None or ws_id is None:
            return

        existing = transport.connections.get(target_chat_id)
        if not isinstance(existing, dict):
            existing = {}

        frontend_context = existing.get("frontend_context") or source_conn.get("frontend_context")

        transport.connections[target_chat_id] = {
            **existing,
            "websocket": websocket,
            "user_id": user_id,
            "workflow_name": workflow_name,
            "app_id": app_id,
            "active": True,
            "ws_id": ws_id,
        }
        if frontend_context and isinstance(frontend_context, dict):
            transport.connections[target_chat_id]["frontend_context"] = frontend_context

    @staticmethod
    async def _flush_pre_connection_buffers(*, transport: Any, chat_id: str) -> None:
        """Flush any events that were buffered before the connection alias was set up."""
        try:
            buffers = getattr(transport, "_pre_connection_buffers", None)
            if not isinstance(buffers, dict):
                return
            buffered = buffers.pop(chat_id, None)
            if not buffered or not isinstance(buffered, list):
                return
            for msg in buffered:
                try:
                    await transport._queue_message_with_backpressure(chat_id, msg)
                except Exception:
                    continue
            try:
                await transport._flush_message_queue(chat_id)
            except Exception:
                return
        except Exception:
            return

    @staticmethod
    async def _get_transport_conn(chat_id: str) -> tuple[Optional[Dict[str, Any]], Any]:
        """Resolve transport instance and connection metadata for a chat_id."""
        try:
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            conn = transport.connections.get(chat_id) or {}
            return (conn if isinstance(conn, dict) and conn else None), transport
        except Exception:
            return None, None

    # ===================================================================
    # Timeout watchdog
    # ===================================================================

    async def _timeout_watchdog(self, parent_chat_id: str, timeout_seconds: float) -> None:
        """Wait ``timeout_seconds``, then apply partial-failure strategy."""
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return  # children finished before timeout — normal path

        active = self._active_by_parent.get(parent_chat_id)
        if not active:
            return

        logger.warning(
            "[PACK] Timeout (%.1fs) reached for parent chat=%s — applying %s",
            timeout_seconds,
            parent_chat_id,
            active.on_partial_failure.value,
        )

        # Observer: timeout event
        obs_ctx = active.observer_ctx or MFJSpanContext()
        self._observer.on_timeout(
            obs_ctx,
            timeout_seconds=timeout_seconds,
            strategy=active.on_partial_failure.value,
            trigger_id=active.trigger_id or "",
            parent_chat_id=parent_chat_id,
        )

        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        if not transport:
            return

        await self._handle_partial_failure(active, transport)

    async def _handle_partial_failure(
        self,
        active: _ActivePackRun,
        transport: Any,
    ) -> None:
        """Apply the configured partial-failure strategy."""

        strategy = active.on_partial_failure

        if strategy == PartialFailureStrategy.FAIL_ALL:
            # Cancel all children and resume parent with error.
            for child_id in list(active.child_chat_ids):
                try:
                    await transport.pause_background_workflow(
                        chat_id=child_id, reason="timeout_fail_all"
                    )
                except Exception:
                    pass
            # Cleanup and resume with failure info.
            await self._finalize_with_available(active, transport, timed_out=True)

        elif strategy == PartialFailureStrategy.RESUME_WITH_AVAILABLE:
            # Cancel still-running children, merge what we have.
            for child_id in list(active.child_chat_ids):
                t = transport._background_tasks.get(child_id)
                if t and not t.done():
                    try:
                        await transport.pause_background_workflow(
                            chat_id=child_id, reason="timeout_resume_available"
                        )
                    except Exception:
                        pass
            await self._finalize_with_available(active, transport, timed_out=True)

        elif strategy == PartialFailureStrategy.PROMPT_USER:
            # Emit event for UI to handle; don't auto-resume.
            try:
                await transport.send_event_to_ui(
                    {
                        "type": "chat.mfj_timeout_prompt",
                        "data": {
                            "parent_chat_id": active.parent_chat_id,
                            "parent_workflow_name": active.parent_workflow_name,
                            "timeout_seconds": active.timeout_seconds,
                            "children_pending": [
                                cid for cid in active.child_chat_ids
                                if (transport._background_tasks.get(cid) or asyncio.Future()).done() is False
                            ],
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    active.parent_chat_id,
                )
            except Exception:
                pass
            # Don't cleanup — wait for user action or further completions.

        elif strategy == PartialFailureStrategy.RETRY_FAILED:
            # For now, retry == resume_with_available (retry logic is P2).
            logger.info("[PACK] retry_failed not fully implemented — falling back to resume_with_available")
            await self._finalize_with_available(active, transport, timed_out=True)

    async def _finalize_with_available(
        self,
        active: _ActivePackRun,
        transport: Any,
        *,
        timed_out: bool = False,
    ) -> None:
        """Collect whatever results are available, merge, resume parent."""
        # Cancel timeout watchdog if still running.
        if active.timeout_task and not active.timeout_task.done():
            active.timeout_task.cancel()

        # Cleanup indexes.
        for child_id in list(active.child_chat_ids):
            self._active_by_child.pop(child_id, None)
        self._active_by_parent.pop(active.parent_chat_id, None)

        pm = transport._get_or_create_persistence_manager()
        child_results = await self._collect_child_results(active, pm)

        # --- Observer: fan-in started (partial/timeout path) ---
        obs_ctx = active.observer_ctx or MFJSpanContext()
        self._observer.on_fan_in_started(
            obs_ctx,
            available_count=len(child_results),
            total_count=len(active.child_chat_ids),
            trigger_id=active.trigger_id or "",
            parent_chat_id=active.parent_chat_id,
        )

        merge_strategy = active.merge_strategy or self._default_merge
        merge_result = self._apply_merge(active, child_results, merge_strategy)

        # Annotate that this was a partial/timeout result.
        partial_note = " [PARTIAL — timed out]" if timed_out else ""
        summary = merge_result.summary_message + partial_note

        try:
            await pm.patch_session_fields(
                chat_id=active.parent_chat_id,
                app_id=active.app_id,
                fields={
                    "child_results": merge_result.merged_data,
                    "merge_summary": summary[:4000],
                    "merge_all_succeeded": merge_result.all_succeeded,
                    "merge_succeeded_count": merge_result.succeeded_count,
                    "merge_failed_count": merge_result.failed_count,
                    "merge_timed_out": timed_out,
                },
            )
        except Exception as exc:
            logger.warning("[PACK] Partial merge write failed: %s", exc)

        if active.trigger_id:
            await self._record_mfj_completion(
                parent_chat_id=active.parent_chat_id,
                trigger_id=active.trigger_id,
                child_count=len(child_results),
                all_succeeded=merge_result.all_succeeded,
                merge_summary_preview=summary[:200],
            )

        await self._resume_parent(
            transport=transport,
            parent_chat_id=active.parent_chat_id,
            parent_workflow=active.parent_workflow_name,
            app_id=active.app_id,
            user_id=active.user_id,
            ws_id=active.ws_id,
            resume_agent=active.resume_agent,
            merge_summary=summary,
            trigger_id=active.trigger_id,
            mfj_cycle=active.mfj_cycle,
            succeeded_count=merge_result.succeeded_count,
            failed_count=merge_result.failed_count,
        )

        # --- Observer: fan-in completed + cycle done (partial path) ---
        self._observer.on_fan_in_completed(
            obs_ctx,
            strategy=type(merge_strategy).__name__,
            succeeded=merge_result.succeeded_count,
            failed=merge_result.failed_count,
            trigger_id=active.trigger_id or "",
            parent_chat_id=active.parent_chat_id,
            workflow_name=active.parent_workflow_name,
        )
        self._observer.on_cycle_completed(
            obs_ctx,
            success=merge_result.all_succeeded,
            trigger_id=active.trigger_id or "",
            parent_chat_id=active.parent_chat_id,
        )

    # ===================================================================
    # Multi-MFJ sequencing
    # ===================================================================

    async def _check_mfj_requires(
        self,
        parent_chat_id: str,
        requires: List[str],
    ) -> bool:
        """Return True if all required MFJ trigger_ids have completed for this parent.

        Read-through strategy:
          1. Check the in-memory ``_completed_mfjs`` cache first (hot path).
          2. On cache miss (some requires not found), query MongoDB via
             ``MFJCompletionStore`` and merge the result into cache.
          3. Return True only if ALL required trigger_ids are satisfied.

        If MongoDB is unavailable, falls back to in-memory only.
        """
        # Hot path: check in-memory cache.
        completed = self._completed_mfjs.get(parent_chat_id, [])
        completed_ids = {r.trigger_id for r in completed}
        if all(req_id in completed_ids for req_id in requires):
            return True

        # Cache miss — attempt read-through from MongoDB.
        if self._mfj_store is not None:
            try:
                db_ids = await self._mfj_store.load_completed_trigger_ids(
                    parent_chat_id
                )
                # Merge DB results into in-memory cache so future checks
                # don't hit Mongo again for the same parent.
                if db_ids:
                    for tid in db_ids:
                        if tid not in completed_ids:
                            # Create a lightweight record for the cache.
                            self._completed_mfjs.setdefault(
                                parent_chat_id, []
                            ).append(
                                _MFJCompletionRecord(
                                    trigger_id=tid,
                                    parent_chat_id=parent_chat_id,
                                    completed_at=datetime.now(timezone.utc),
                                    child_count=0,  # unavailable from projection
                                    all_succeeded=True,
                                )
                            )
                    completed_ids |= db_ids
            except Exception as exc:
                logger.warning(
                    "[PACK] MongoDB read-through failed for parent=%s: %s",
                    parent_chat_id,
                    exc,
                )

        return all(req_id in completed_ids for req_id in requires)

    async def _record_mfj_completion(
        self,
        *,
        parent_chat_id: str,
        trigger_id: str,
        child_count: int,
        all_succeeded: bool,
        child_chat_ids: Optional[List[str]] = None,
        merge_summary_preview: str,
    ) -> None:
        """Record that an MFJ trigger completed (for requires checking).

        Write-through strategy:
          1. Write to in-memory ``_completed_mfjs`` cache (always succeeds).
          2. Write to MongoDB via ``MFJCompletionStore`` (best-effort; logged
             on failure, never blocks orchestration).
        """
        now = datetime.now(timezone.utc)
        record = _MFJCompletionRecord(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            completed_at=now,
            child_count=child_count,
            all_succeeded=all_succeeded,
            merge_summary_preview=merge_summary_preview,
        )
        self._completed_mfjs.setdefault(parent_chat_id, []).append(record)
        logger.info(
            "[PACK] Recorded MFJ completion: trigger_id=%s parent=%s children=%d ok=%s",
            trigger_id,
            parent_chat_id,
            child_count,
            all_succeeded,
        )

        # Write-through to MongoDB (fire-and-forget — don't block resume).
        if self._mfj_store is not None:
            await self._mfj_store.write_completion(
                parent_chat_id=parent_chat_id,
                trigger_id=trigger_id,
                completed_at=now,
                child_count=child_count,
                all_succeeded=all_succeeded,
                child_chat_ids=child_chat_ids,
                merge_summary_preview=merge_summary_preview,
            )

    # ===================================================================
    # Recovery (process restart)
    # ===================================================================

    async def recover_from_persistence(self) -> int:
        """Reload MFJ completion records from MongoDB on startup.

        Call this once when the coordinator is first instantiated (e.g., in
        ``SimpleTransport.startup``).  It:
          1. Ensures MongoDB indexes exist.
          2. Loads recently-completed MFJ records from the
             ``MFJCompletions`` collection.
          3. Populates the in-memory ``_completed_mfjs`` cache so that
             in-flight ``requires`` checks work after a restart.

        Returns the number of parent sessions recovered.
        """
        if self._mfj_store is None:
            return 0

        try:
            await self._mfj_store.ensure_indexes()
        except Exception as exc:
            logger.warning("[PACK] MFJ index ensure failed: %s", exc)

        try:
            parent_ids = await self._mfj_store.load_paused_parent_ids()
            if not parent_ids:
                return 0

            bulk = await self._mfj_store.load_completions_for_parents(parent_ids)

            recovered = 0
            for pid, docs in bulk.items():
                existing_ids = {
                    r.trigger_id
                    for r in self._completed_mfjs.get(pid, [])
                }
                for doc in docs:
                    tid = doc.get("trigger_id", "")
                    if tid and tid not in existing_ids:
                        self._completed_mfjs.setdefault(pid, []).append(
                            _MFJCompletionRecord(
                                trigger_id=tid,
                                parent_chat_id=pid,
                                completed_at=doc.get(
                                    "completed_at",
                                    datetime.now(timezone.utc),
                                ),
                                child_count=doc.get("child_count", 0),
                                all_succeeded=doc.get("all_succeeded", True),
                                merge_summary_preview=doc.get(
                                    "merge_summary_preview", ""
                                ),
                            )
                        )
                        existing_ids.add(tid)
                recovered += 1

            logger.info(
                "[PACK] Recovered MFJ completions for %d parent sessions",
                recovered,
            )
            return recovered
        except Exception as exc:
            logger.warning("[PACK] MFJ recovery failed: %s", exc)
            return 0

    # ===================================================================
    # Child result collection
    # ===================================================================

    async def _collect_child_results(
        self,
        active: _ActivePackRun,
        pm: Any,
    ) -> List[ChildResult]:
        """Build ``ChildResult`` objects from MongoDB for all children."""
        results: List[ChildResult] = []
        chat_to_task = {v: k for k, v in active.task_to_chat.items()}

        for child_id in active.child_chat_ids:
            task_id = chat_to_task.get(child_id, child_id)
            try:
                child_extra = await pm.fetch_chat_session_extra_context(
                    chat_id=child_id,
                    app_id=active.app_id,
                )
            except Exception as exc:
                logger.warning("[PACK] Failed fetching child extra for %s: %s", child_id, exc)
                child_extra = None

            # Try to determine child workflow name from session or active state.
            wf_name = ""
            if active.decomposition_plan:
                for st in active.decomposition_plan.sub_tasks:
                    if active.task_to_chat.get(st.task_id) == child_id:
                        wf_name = st.workflow_name
                        break

            # Determine success: child task completed without exception.
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            success = True
            error_msg: Optional[str] = None
            if transport:
                t = transport._background_tasks.get(child_id)
                if t and t.done():
                    try:
                        t.result()  # raises if task failed
                    except asyncio.CancelledError:
                        success = False
                        error_msg = "cancelled (timeout or parent abort)"
                    except Exception as exc:
                        success = False
                        error_msg = str(exc)

            results.append(ChildResult(
                task_id=task_id,
                workflow_name=wf_name or child_id,
                run_id=child_id,
                text_output="",
                structured_output=child_extra if isinstance(child_extra, dict) else {},
                success=success,
                error=error_msg,
                metadata={"parent_chat_id": active.parent_chat_id},
            ))

        return results

    # ===================================================================
    # Merge
    # ===================================================================

    def _apply_merge(
        self,
        active: _ActivePackRun,
        child_results: List[ChildResult],
        merge_strategy: MergeStrategy,
    ) -> MergeResult:
        """Apply a MergeStrategy to child results."""
        ctx = MergeContext(
            parent_run_id=active.parent_chat_id,
            parent_workflow_name=active.parent_workflow_name,
            child_results=child_results,
            parent_context_variables={},
            strategy_metadata=(
                active.decomposition_plan.strategy_metadata
                if active.decomposition_plan
                else {}
            ),
        )
        try:
            return merge_strategy.merge(ctx)
        except Exception as exc:
            logger.error("[PACK] Merge failed, falling back to raw collect: %s", exc)
            # Fallback: just concatenate what we have.
            return ConcatenateMerge().merge(ctx)

    def _resolve_merge_strategy(self, mode: str) -> MergeStrategy:
        """Resolve a merge mode string to a MergeStrategy instance.

        Lookup order:
          1. ``custom:<name>`` syntax → registry lookup by <name>
          2. Built-in name → registry lookup (concatenate, structured,
             deep_merge, first_success, majority_vote, collect_all)
          3. Fallback → ConcatenateMerge

        The registry contains both built-in and user-registered strategies.
        """
        # Handle "custom:my_strategy" syntax for user-registered strategies.
        if mode.startswith("custom:"):
            custom_name = mode[len("custom:"):].strip()
            if custom_name:
                registry = get_merge_strategy_registry()
                cls = registry.get(custom_name)
                if cls is not None:
                    return cls()
                logger.warning(
                    "[PACK] Custom merge strategy '%s' not found in registry; "
                    "falling back to concatenate",
                    custom_name,
                )
            return ConcatenateMerge()

        # collect_all is internal (not in the shared registry).
        if mode == "collect_all":
            return _CollectAllMerge()

        # Look up in registry (covers all built-ins + any user additions).
        registry = get_merge_strategy_registry()
        cls = registry.get(mode)
        if cls is not None:
            return cls()

        # Default: concatenate
        return ConcatenateMerge()

    # ===================================================================
    # Internals — config loading delegates to config module
    # ===================================================================

    @staticmethod
    def _load_pack_graph(workflow_name: str) -> Optional[Dict[str, Any]]:
        """Load per-workflow pack graph via the consolidated config module."""
        return load_pack_graph(workflow_name)

    def _extract_pack_plan(self, structured_data: Any) -> Optional[Dict[str, Any]]:
        """Extract a normalized plan from structured outputs (backward compat).

        Supports PatternSelectionOutput shape:
          {"PatternSelection": {"is_multi_workflow": bool, "workflows": [...], "resume_agent": ...}}
        """
        if not isinstance(structured_data, dict):
            return None

        ps = structured_data.get("PatternSelection")
        if isinstance(ps, dict):
            return ps

        ps2 = structured_data.get("pattern_selection")
        if isinstance(ps2, dict):
            return ps2

        return None

    def _plan_from_raw(
        self,
        raw_plan: Dict[str, Any],
        trigger_entry: Dict[str, Any],
    ) -> Optional[DecompositionPlan]:
        """Convert a raw PatternSelection dict to a DecompositionPlan (backward compat)."""
        workflows = raw_plan.get("workflows")
        if not isinstance(workflows, list) or not workflows:
            return None

        sub_tasks: List[SubTask] = []
        for wf in workflows:
            if not isinstance(wf, dict):
                continue
            name = wf.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            sub_tasks.append(SubTask(
                workflow_name=name.strip(),
                initial_message=wf.get("initial_message") if isinstance(wf.get("initial_message"), str) else None,
                initial_agent_override=wf.get("initial_agent") if isinstance(wf.get("initial_agent"), str) else None,
                metadata={k: v for k, v in wf.items() if k not in ("name", "initial_message", "initial_agent")},
            ))

        if not sub_tasks:
            return None

        resume_agent = None
        raw_resume = raw_plan.get("resume_agent")
        if isinstance(raw_resume, str) and raw_resume.strip():
            resume_agent = raw_resume.strip()

        return DecompositionPlan(
            sub_tasks=tuple(sub_tasks),
            reason="PatternSelection (backward compat)",
            resume_agent=resume_agent,
            strategy_metadata={"decomposition_reason": raw_plan.get("decomposition_reason")},
        )

    def _find_trigger_entry(self, active: _ActivePackRun) -> Optional[Dict[str, Any]]:
        """Reload the trigger entry from pack graph for contract validation."""
        pack_cfg = self._load_pack_graph(active.parent_workflow_name)
        if not pack_cfg:
            return None
        triggers = _resolve_triggers(pack_cfg)
        for entry in triggers:
            if str(entry.get("trigger_agent") or "") == active.trigger_agent:
                return entry
        return None

    async def _resume_parent(
        self,
        *,
        transport: Any,
        parent_chat_id: str,
        parent_workflow: str,
        app_id: str,
        user_id: str,
        ws_id: Optional[int],
        resume_agent: Optional[str],
        merge_summary: Optional[str] = None,
        # Phase 6 UI enrichment (optional — not all callers have this data)
        trigger_id: Optional[str] = None,
        mfj_cycle: int = 0,
        succeeded_count: int = 0,
        failed_count: int = 0,
    ) -> None:
        """Resume the parent GroupChat after fan-in."""
        # Emit kernel event before resume for observability.
        try:
            from mozaiksai.orchestration.events import emit_parent_resuming
            emit_parent_resuming(
                run_id=parent_chat_id,
                workflow_name=parent_workflow,
                resume_agent=resume_agent or "",
            )
        except Exception:
            pass

        # Cancel any running parent task first (idempotent).
        try:
            await transport.pause_background_workflow(chat_id=parent_chat_id, reason="resume_restart")
        except Exception:
            pass

        # Start orchestration again; it will resume from persisted history.
        transport._background_tasks[parent_chat_id] = asyncio.create_task(
            transport._run_workflow_background(
                chat_id=parent_chat_id,
                workflow_name=str(parent_workflow),
                app_id=str(app_id),
                user_id=str(user_id),
                ws_id=ws_id,
                initial_message=None,
                initial_agent_name_override=resume_agent,
            )
        )

        try:
            await transport.send_event_to_ui(
                {
                    "type": "chat.workflow_resumed",
                    "data": {
                        "chat_id": parent_chat_id,
                        "workflow_name": parent_workflow,
                        "resume_agent": resume_agent,
                        "merge_summary_preview": (merge_summary or "")[:500],
                        # Phase 6 enrichment
                        "trigger_id": trigger_id or "",
                        "mfj_cycle": mfj_cycle,
                        "succeeded_count": succeeded_count,
                        "failed_count": failed_count,
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                parent_chat_id,
            )
        except Exception:
            pass

        logger.info(
            "[PACK] Resumed parent chat=%s workflow=%s resume_agent=%s",
            parent_chat_id,
            parent_workflow,
            resume_agent,
        )


# ---------------------------------------------------------------------------
# Built-in: collect_all merge (backward compat — raw child_extra dump)
# ---------------------------------------------------------------------------

class _CollectAllMerge:
    """Backward-compatible merge: dumps raw child ``extra_context`` keyed by task_id."""

    def merge(self, context: MergeContext) -> MergeResult:
        merged: Dict[str, Any] = {}
        all_ok = True
        for r in context.child_results:
            merged[r.task_id] = r.structured_output
            if not r.success:
                all_ok = False

        ok = sum(1 for r in context.child_results if r.success)
        total = len(context.child_results)
        summary = f"Collected {ok}/{total} child results (raw collect_all mode)."

        return MergeResult(
            summary_message=summary,
            merged_data=merged,
            child_results=tuple(context.child_results),
            all_succeeded=all_ok,
        )


__all__ = [
    "FanInContractError",
    "FanOutContractError",
    "MergeMode",
    "PartialFailureStrategy",
    "WorkflowPackCoordinator",
]
