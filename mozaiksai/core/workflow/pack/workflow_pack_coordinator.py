# === MOZAIKS-CORE-HEADER ===
# FILE: core/workflow/pack/workflow_pack_coordinator.py
# DESCRIPTION: Runtime fan-out/fan-in coordinator for per-workflow mid-flight journeys.
# ==============================================================================

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from logs.logging_config import get_core_logger
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id
from mozaiksai.core.workflow.pack.config import load_workflow_pack_graph
from mozaiksai.core.workflow.pack.mfj_observability import (
    MFJObservationContext,
    MFJObserver,
    get_mfj_observer,
)
from mozaiksai.core.workflow.pack.mfj_persistence import MFJCompletionStore
from mozaiksai.core.workflow.pack.merge import (
    ChildResult,
    MergeResult,
    MergeStrategy,
    get_merge_strategy,
)
from mozaiksai.core.workflow.pack.resume_contract import (
    MFJ_RESUME_CONTEXT_KEYS,
    build_resume_context_payload,
)
from mozaiksai.core.workflow.pack.schema import MFJContract, MidFlightJourney

logger = get_core_logger("workflow_pack_coordinator")


@dataclass
class _ChildRunState:
    """State for a single MFJ child task across retries."""

    task_key: str
    spec: Dict[str, Any]
    chat_id: str
    retries: int = 0


@dataclass
class _ActivePackRun:
    """Active MFJ cycle state keyed by parent chat id."""

    parent_chat_id: str
    parent_workflow_name: str
    app_id: str
    user_id: str
    ws_id: Optional[int]
    trigger: MidFlightJourney
    trigger_agent: str
    merge_strategy: MergeStrategy
    on_partial_failure: str
    max_retry_rounds: int
    mfj_cycle: int
    parent_context_snapshot: Dict[str, Any]
    structured_data_snapshot: Dict[str, Any]
    observer_ctx: Optional[MFJObservationContext] = None
    child_runs: Dict[str, _ChildRunState] = field(default_factory=dict)  # task_key -> state
    child_chat_to_task: Dict[str, str] = field(default_factory=dict)  # chat_id -> task_key
    _timeout_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _retry_round: int = 0
    _started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def trigger_id(self) -> str:
        return self.trigger.id

    @property
    def child_chat_ids(self) -> List[str]:
        return [state.chat_id for state in self.child_runs.values()]

    @property
    def fan_out_timeout_seconds(self) -> int:
        return int(self.trigger.fan_out.timeout_seconds)

    @property
    def fan_in_timeout_seconds(self) -> int:
        return int(self.trigger.fan_in.timeout_seconds)

    @property
    def output_contract(self) -> MFJContract:
        return self.trigger.output_contract

    @property
    def resume_agent(self) -> str:
        return self.trigger.fan_in.resume_agent

    @property
    def resume_entry_agent(self) -> str:
        return self.trigger.fan_in.resume_entry_agent

    @property
    def inject_as(self) -> str:
        return self.trigger.fan_in.inject_as


class WorkflowPackCoordinator:
    """Runtime-level coordinator for per-workflow mid-flight journeys."""

    def __init__(
        self,
        *,
        completion_store: Optional[MFJCompletionStore] = None,
        observer: Optional[MFJObserver] = None,
        max_retry_rounds: int = 1,
    ) -> None:
        self._active_by_parent: Dict[str, _ActivePackRun] = {}
        self._active_by_child: Dict[str, str] = {}
        self._completed_mfjs: Dict[str, set[str]] = {}  # parent_chat_id -> {trigger_id}
        self._mfj_cycle_counter: Dict[str, int] = {}  # parent_chat_id -> cycle number
        self._completion_store = completion_store or MFJCompletionStore()
        self._observer = observer or get_mfj_observer()
        self._max_retry_rounds = max(0, int(max_retry_rounds))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def handle_structured_output_ready(self, event: Dict[str, Any]) -> None:
        try:
            agent_name = str(event.get("agent_name") or event.get("agent") or "").strip()
            structured_data = event.get("structured_data")
            context = event.get("context") if isinstance(event.get("context"), dict) else {}
            parent_chat_id = str(context.get("chat_id") or "").strip()
            parent_workflow = str(context.get("workflow_name") or "").strip()
        except Exception:
            return

        if not agent_name or not parent_chat_id or not parent_workflow:
            return

        # At most one active MFJ per parent chat.
        if parent_chat_id in self._active_by_parent:
            return

        pack_graph = load_workflow_pack_graph(parent_workflow)
        if pack_graph is None:
            return

        trigger = self._find_trigger(pack_graph.mid_flight_journeys, agent_name)
        if trigger is None:
            return

        parent_ctx = context.get("context_variables")
        if not isinstance(parent_ctx, dict):
            parent_ctx = {}

        app_id = str(context.get("app_id") or "").strip()
        if not app_id:
            # Best effort: resolve from runtime connection metadata.
            try:
                from mozaiksai.core.transport.simple_transport import SimpleTransport

                transport = await SimpleTransport.get_instance()
                if transport is not None:
                    conn = transport.connections.get(parent_chat_id) or {}
                    app_id = str(conn.get("app_id") or "").strip()
            except Exception:
                app_id = ""
        app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not app_id:
            return

        if not await self._check_requires(app_id, parent_chat_id, trigger.requires):
            logger.info(
                "[PACK] Trigger blocked by requires chain parent=%s trigger=%s requires=%s completed=%s",
                parent_chat_id,
                trigger.id,
                trigger.requires,
                sorted(self._completed_mfjs.get(parent_chat_id, set())),
            )
            return

        missing = self._validate_input_contract(parent_ctx, trigger.fan_out.input_contract)
        if missing:
            self._observer.on_contract_violation(
                parent_chat_id=parent_chat_id,
                trigger_id=trigger.id,
                missing=missing,
                contract="input_contract",
            )
            logger.warning(
                "[PACK] Input contract missing keys parent=%s trigger=%s missing=%s",
                parent_chat_id,
                trigger.id,
                missing,
            )
            return

        child_specs = self._extract_child_specs(structured_data)
        if not child_specs:
            return

        max_children = int(trigger.fan_out.max_children)
        child_specs = child_specs[:max_children]

        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        if transport is None:
            logger.warning("[PACK] SimpleTransport unavailable; cannot spawn children")
            return

        parent_conn = transport.connections.get(parent_chat_id) or {}
        user_id = str(parent_conn.get("user_id") or context.get("user_id") or "").strip()
        ws_id_raw = parent_conn.get("ws_id")
        ws_id = int(ws_id_raw) if isinstance(ws_id_raw, int) else None
        if not user_id:
            return

        # Pause parent before spawning children.
        try:
            await transport.pause_background_workflow(chat_id=parent_chat_id, reason=f"mfj:{trigger.id}:fan_out")
        except Exception as exc:
            logger.debug("[PACK] Failed pausing parent chat=%s: %s", parent_chat_id, exc)

        try:
            merge_strategy = get_merge_strategy(trigger.fan_in.aggregation_strategy)
        except Exception as exc:
            logger.warning(
                "[PACK] Invalid aggregation strategy parent=%s trigger=%s strategy=%s: %s",
                parent_chat_id,
                trigger.id,
                trigger.fan_in.aggregation_strategy,
                exc,
            )
            return
        cycle = self._mfj_cycle_counter.get(parent_chat_id, 0) + 1
        self._mfj_cycle_counter[parent_chat_id] = cycle

        observer_ctx = self._observer.start_cycle(
            trigger_id=trigger.id,
            parent_chat_id=parent_chat_id,
            workflow_name=parent_workflow,
        )
        self._observer.on_fan_out_started(
            observer_ctx,
            child_count=len(child_specs),
            spawn_mode=trigger.fan_out.spawn_mode,
        )

        active = _ActivePackRun(
            parent_chat_id=parent_chat_id,
            parent_workflow_name=parent_workflow,
            app_id=app_id,
            user_id=user_id,
            ws_id=ws_id,
            trigger=trigger,
            trigger_agent=agent_name,
            merge_strategy=merge_strategy,
            on_partial_failure=trigger.fan_in.on_partial_failure,
            max_retry_rounds=self._max_retry_rounds,
            mfj_cycle=cycle,
            parent_context_snapshot=dict(parent_ctx),
            structured_data_snapshot=structured_data if isinstance(structured_data, dict) else {},
            observer_ctx=observer_ctx,
        )

        started_payloads: List[Dict[str, Any]] = []
        for spec in child_specs:
            child = await self._spawn_child_run(
                transport=transport,
                active=active,
                child_spec=spec,
            )
            if child is None:
                continue

            task_key, child_state, payload = child
            active.child_runs[task_key] = child_state
            active.child_chat_to_task[child_state.chat_id] = task_key
            self._active_by_child[child_state.chat_id] = parent_chat_id
            started_payloads.append(payload)
            if active.observer_ctx is not None:
                self._observer.on_child_spawned(
                    active.observer_ctx,
                    child_chat_id=child_state.chat_id,
                    task_key=task_key,
                )

        if not active.child_runs:
            await self._resume_parent(
                transport=transport,
                active=active,
                merged_payload={},
                succeeded_count=0,
                failed_count=0,
                resume_nonce="",
            )
            if active.observer_ctx is not None:
                self._observer.on_cycle_completed(active.observer_ctx)
            return

        self._active_by_parent[parent_chat_id] = active
        if active.fan_out_timeout_seconds > 0:
            active._timeout_task = asyncio.create_task(
                self._timeout_watchdog(parent_chat_id, active.fan_out_timeout_seconds)
            )

        await self._emit_ui_event(
            transport=transport,
            target_chat_id=parent_chat_id,
            event_type="chat.workflow_batch_started",
            data={
                "parent_chat_id": parent_chat_id,
                "parent_workflow_name": parent_workflow,
                "trigger_id": active.trigger_id,
                "mfj_cycle": active.mfj_cycle,
                "resume_agent": active.resume_agent,
                "resume_entry_agent": active.resume_entry_agent,
                "count": len(started_payloads),
                "workflows": started_payloads,
            },
        )

    async def handle_run_complete(self, payload: Dict[str, Any]) -> None:
        chat_id = str(payload.get("chat_id") or "").strip()
        if not chat_id:
            return

        parent_chat_id = self._active_by_child.get(chat_id)
        if not parent_chat_id:
            return

        active = self._active_by_parent.get(parent_chat_id)
        if active is None:
            return

        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        if transport is None:
            return

        status = str(payload.get("status") or "completed").strip().lower()
        success = status not in {"failed", "error", "cancelled"}
        if active.observer_ctx is not None:
            self._observer.on_child_completed(active.observer_ctx, child_chat_id=chat_id, success=success)

        await self._emit_ui_event(
            transport=transport,
            target_chat_id=active.parent_chat_id,
            event_type="chat.workflow_child_completed",
            data={
                "parent_chat_id": active.parent_chat_id,
                "trigger_id": active.trigger_id,
                "mfj_cycle": active.mfj_cycle,
                "child_chat_id": chat_id,
                "child_index": self._child_index(active, chat_id),
                "child_total": len(active.child_runs),
                "status": status,
                "success": success,
            },
        )

        all_done = True
        for child_chat_id in active.child_chat_ids:
            task = transport._background_tasks.get(child_chat_id)
            if task and not task.done():
                all_done = False
                break
        if not all_done:
            return

        await self._finalize_active_run(transport=transport, active=active, reason="all_children_done")

    # ------------------------------------------------------------------
    # Trigger resolution and contracts
    # ------------------------------------------------------------------

    @staticmethod
    def _find_trigger(mfjs: Sequence[MidFlightJourney], agent_name: str) -> Optional[MidFlightJourney]:
        for entry in mfjs:
            if entry.trigger_on != "structured_output":
                continue
            if entry.trigger_agent == agent_name:
                return entry
        return None

    @staticmethod
    def _validate_input_contract(parent_context: Dict[str, Any], contract: MFJContract) -> List[str]:
        missing: List[str] = []
        for key in contract.required:
            if key not in parent_context:
                missing.append(key)
        return missing

    async def _check_requires(
        self,
        app_id: str,
        parent_chat_id: str,
        required_trigger_ids: Sequence[str],
    ) -> bool:
        required = [str(req or "").strip() for req in required_trigger_ids if str(req or "").strip()]
        if not required:
            return True

        completed = self._completed_mfjs.setdefault(parent_chat_id, set())
        missing = [req for req in required if req not in completed]
        if not missing:
            return True

        loaded = await self._completion_store.load_completed_trigger_ids(
            app_id=app_id,
            parent_chat_id=parent_chat_id,
        )
        if loaded:
            completed.update(loaded)

        return all(req in completed for req in required)

    @staticmethod
    def _extract_child_specs(structured_data: Any) -> List[Dict[str, Any]]:
        """Extract child run specs from canonical trigger structured output."""
        if not isinstance(structured_data, dict):
            return []

        raw_workflows = structured_data.get("workflows")
        if not isinstance(raw_workflows, list):
            return []

        specs: List[Dict[str, Any]] = []
        for idx, raw in enumerate(raw_workflows):
            if isinstance(raw, str):
                name = raw.strip()
                if not name:
                    continue
                specs.append({"name": name, "description": None, "task_index": idx})
                continue

            if not isinstance(raw, dict):
                continue

            name = raw.get("name")
            if not isinstance(name, str) or not name.strip():
                continue

            spec: Dict[str, Any] = {
                "name": name.strip(),
                "description": raw.get("description"),
                "initial_message": raw.get("initial_message"),
                "initial_agent": raw.get("initial_agent"),
                "task_index": idx,
            }
            specs.append(spec)
        return specs

    # ------------------------------------------------------------------
    # Child spawning
    # ------------------------------------------------------------------

    async def _spawn_child_run(
        self,
        *,
        transport: Any,
        active: _ActivePackRun,
        child_spec: Dict[str, Any],
    ) -> Optional[Tuple[str, _ChildRunState, Dict[str, Any]]]:
        trigger = active.trigger
        spec_name = str(child_spec.get("name") or "").strip()
        if not spec_name:
            return None

        task_index = child_spec.get("task_index")
        task_suffix = f"_{task_index}" if isinstance(task_index, int) else ""
        task_key = f"{spec_name}{task_suffix}"

        spawn_mode = trigger.fan_out.spawn_mode
        child_workflow_to_run = (
            spec_name
            if spawn_mode == "workflow"
            else str(trigger.fan_out.generator_workflow or "").strip()
        )
        if not child_workflow_to_run:
            return None
        if spawn_mode == "workflow" and not self._workflow_exists(child_workflow_to_run):
            logger.info("[PACK] Missing child workflow '%s'; skipped", child_workflow_to_run)
            return None

        child_chat_id = (
            f"chat_gen_{spec_name}_{uuid.uuid4().hex[:8]}"
            if spawn_mode == "generator_subrun"
            else f"chat_{child_workflow_to_run}_{uuid.uuid4().hex[:8]}"
        )

        initial_agent_override = child_spec.get("initial_agent")
        if not isinstance(initial_agent_override, str) or not initial_agent_override.strip():
            initial_agent_override = trigger.fan_out.child_initial_agent
        initial_agent_override = initial_agent_override.strip() if isinstance(initial_agent_override, str) else None

        initial_message = child_spec.get("initial_message")
        if not isinstance(initial_message, str) or not initial_message.strip():
            if spawn_mode == "generator_subrun":
                description = child_spec.get("description")
                initial_message = (
                    f"Generate workflow '{spec_name}'. Description: {description.strip()}"
                    if isinstance(description, str) and description.strip()
                    else f"Generate workflow '{spec_name}'."
                )
            else:
                initial_message = None
        else:
            initial_message = initial_message.strip()

        extra_fields = self._build_child_extra_context(
            active=active,
            child_spec=child_spec,
        )

        pm = transport._get_or_create_persistence_manager()
        await pm.create_chat_session(
            chat_id=child_chat_id,
            app_id=active.app_id,
            workflow_name=child_workflow_to_run,
            user_id=active.user_id,
            extra_fields=extra_fields,
        )

        try:
            if active.ws_id is not None:
                from mozaiksai.core.transport.session_registry import session_registry

                session_registry.add_workflow(
                    ws_id=active.ws_id,
                    chat_id=child_chat_id,
                    workflow_name=child_workflow_to_run,
                    app_id=active.app_id,
                    user_id=active.user_id,
                    auto_activate=False,
                )
        except Exception:
            pass

        transport._background_tasks[child_chat_id] = asyncio.create_task(
            transport._run_workflow_background(
                chat_id=child_chat_id,
                workflow_name=child_workflow_to_run,
                app_id=active.app_id,
                user_id=active.user_id,
                ws_id=active.ws_id,
                initial_message=initial_message,
                initial_agent_name_override=initial_agent_override,
            )
        )

        state = _ChildRunState(
            task_key=task_key,
            spec=dict(child_spec),
            chat_id=child_chat_id,
            retries=0,
        )
        payload = {
            "task_key": task_key,
            "chat_id": child_chat_id,
            "workflow_name": child_workflow_to_run,
            "generated_workflow_name": spec_name if spawn_mode == "generator_subrun" else None,
            "app_id": active.app_id,
            "user_id": active.user_id,
        }
        return task_key, state, payload

    def _build_child_extra_context(
        self,
        *,
        active: _ActivePackRun,
        child_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        trigger = active.trigger
        extra: Dict[str, Any] = {
            "parent_chat_id": active.parent_chat_id,
            "parent_workflow_name": active.parent_workflow_name,
            "mfj_id": trigger.id,
            "mfj_cycle": active.mfj_cycle,
            "is_child_workflow": True,
            "spawn_mode": trigger.fan_out.spawn_mode,
            "generated_workflow_name": child_spec.get("name"),
            "mfj_trigger_output": active.structured_data_snapshot,
            "mfj_child_spec": child_spec,
        }

        for key, value in trigger.fan_out.child_context_seed.items():
            extra[key] = value

        include_keys = list(trigger.fan_out.input_contract.required) + list(trigger.fan_out.input_contract.optional)
        for key in include_keys:
            if key in active.parent_context_snapshot:
                extra[key] = active.parent_context_snapshot[key]
        return extra

    @staticmethod
    def _workflow_exists(workflow_name: str) -> bool:
        wf = str(workflow_name or "").strip()
        if not wf:
            return False
        root = Path(__file__).resolve()
        for parent in [root] + list(root.parents):
            candidate = parent / "platform" / "workflows" / wf
            if candidate.exists() and candidate.is_dir():
                return True
        return False

    # ------------------------------------------------------------------
    # Finalization and fan-in
    # ------------------------------------------------------------------

    async def _timeout_watchdog(self, parent_chat_id: str, timeout_seconds: int) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return

        active = self._active_by_parent.get(parent_chat_id)
        if active is None:
            return

        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        if transport is None:
            return

        if active.observer_ctx is not None:
            self._observer.on_timeout(active.observer_ctx, timeout_seconds=timeout_seconds)

        for child_chat_id in active.child_chat_ids:
            task = transport._background_tasks.get(child_chat_id)
            if task and not task.done():
                task.cancel()

        await self._finalize_active_run(transport=transport, active=active, reason="timeout")

    async def _finalize_active_run(self, *, transport: Any, active: _ActivePackRun, reason: str) -> None:
        if active._timeout_task and not active._timeout_task.done():
            active._timeout_task.cancel()

        if active.observer_ctx is not None:
            self._observer.on_fan_in_started(
                active.observer_ctx,
                child_count=len(active.child_runs),
                reason=reason,
            )

        await self._emit_ui_event(
            transport=transport,
            target_chat_id=active.parent_chat_id,
            event_type="chat.mfj_fan_in_started",
            data={
                "parent_chat_id": active.parent_chat_id,
                "trigger_id": active.trigger_id,
                "mfj_cycle": active.mfj_cycle,
                "child_count": len(active.child_runs),
                "reason": reason,
            },
        )

        child_results = await self._collect_child_results(active, transport)
        if not child_results:
            merged_result = MergeResult(
                merged={},
                strategy_used=active.merge_strategy.name,
                child_count=0,
                failed_count=0,
            )
            await self._complete_and_resume(transport=transport, active=active, merge_result=merged_result)
            return

        missing_outputs = self._validate_output_contract(child_results, active.output_contract)
        if missing_outputs:
            self._observer.on_contract_violation(
                parent_chat_id=active.parent_chat_id,
                trigger_id=active.trigger_id,
                missing=missing_outputs,
                contract="output_contract",
            )
            logger.warning(
                "[PACK] Output contract missing parent=%s trigger=%s missing=%s",
                active.parent_chat_id,
                active.trigger_id,
                missing_outputs,
            )

        merged_result = active.merge_strategy.merge(child_results)

        if merged_result.failed_count > 0 and active.on_partial_failure == "retry_failed":
            if active._retry_round < active.max_retry_rounds:
                respawned = await self._retry_failed_children(
                    transport=transport,
                    active=active,
                    child_results=child_results,
                )
                if respawned > 0:
                    active._retry_round += 1
                    if active.fan_out_timeout_seconds > 0:
                        active._timeout_task = asyncio.create_task(
                            self._timeout_watchdog(active.parent_chat_id, active.fan_out_timeout_seconds)
                        )
                    return

        if merged_result.failed_count > 0:
            if active.on_partial_failure == "fail_all":
                merged_result.merged = {
                    "_mfj_error": "partial_failure",
                    "_failed_count": merged_result.failed_count,
                    "_trigger_id": active.trigger_id,
                    "_available": merged_result.merged,
                }
            elif active.on_partial_failure == "prompt_user":
                await self._emit_ui_event(
                    transport=transport,
                    target_chat_id=active.parent_chat_id,
                    event_type="chat.mfj_timeout_prompt",
                    data={
                        "parent_chat_id": active.parent_chat_id,
                        "trigger_id": active.trigger_id,
                        "mfj_cycle": active.mfj_cycle,
                        "failed_count": merged_result.failed_count,
                        "total_count": merged_result.child_count,
                    },
                )

        await self._complete_and_resume(transport=transport, active=active, merge_result=merged_result)

    async def _complete_and_resume(self, *, transport: Any, active: _ActivePackRun, merge_result: MergeResult) -> None:
        succeeded_count = max(0, merge_result.child_count - merge_result.failed_count)
        failed_count = max(0, merge_result.failed_count)
        resume_nonce = uuid.uuid4().hex
        resume_context = build_resume_context_payload(
            trigger_id=active.trigger_id,
            cycle=active.mfj_cycle,
            inject_as=active.inject_as,
            resume_entry_agent=active.resume_entry_agent,
            resume_target_agent=active.resume_agent,
            resume_nonce=resume_nonce,
            succeeded_count=succeeded_count,
            failed_count=failed_count,
        )

        await self._inject_merged_context(
            transport=transport,
            parent_chat_id=active.parent_chat_id,
            app_id=active.app_id,
            inject_as=active.inject_as,
            merged=merge_result.merged,
            resume_context=resume_context,
        )

        await self._record_mfj_completion(
            app_id=active.app_id,
            parent_chat_id=active.parent_chat_id,
            trigger_id=active.trigger_id,
            mfj_cycle=active.mfj_cycle,
            child_count=merge_result.child_count,
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            child_chat_ids=active.child_chat_ids,
            summary=merge_result.merged,
        )

        if active.observer_ctx is not None:
            self._observer.on_fan_in_completed(
                active.observer_ctx,
                succeeded_count=succeeded_count,
                failed_count=failed_count,
                strategy=merge_result.strategy_used,
            )

        await self._resume_parent(
            transport=transport,
            active=active,
            merged_payload=merge_result.merged,
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            resume_nonce=resume_nonce,
        )

        if active.observer_ctx is not None:
            self._observer.on_cycle_completed(active.observer_ctx)
        self._cleanup_active(active)

    async def _collect_child_results(self, active: _ActivePackRun, transport: Any) -> List[ChildResult]:
        results: List[ChildResult] = []
        pm = transport._get_or_create_persistence_manager()
        coll = await pm._coll()

        for child_state in active.child_runs.values():
            child_chat_id = child_state.chat_id
            task_key = child_state.task_key
            try:
                doc = await coll.find_one(
                    {"_id": child_chat_id, **build_app_scope_filter(active.app_id)},
                    projection={"_id": 1, "status": 1},
                )
                status = doc.get("status") if isinstance(doc, dict) else None
                success = bool(status == 1 or str(status).lower() == "completed")
                extra_ctx = await pm.fetch_chat_session_extra_context(
                    chat_id=child_chat_id,
                    app_id=active.app_id,
                )
                context = dict(extra_ctx) if isinstance(extra_ctx, dict) else {}

                # Enrich child context with latest structured outputs when available.
                # This gives fan-in/output contracts direct access to child agent payloads
                # without requiring each workflow to write custom persistence fields.
                try:
                    gather_latest = getattr(pm, "gather_latest_agent_jsons", None)
                    if callable(gather_latest):
                        latest_json = await gather_latest(
                            chat_id=child_chat_id,
                            app_id=active.app_id,
                        )
                        if isinstance(latest_json, dict) and latest_json:
                            context.setdefault("mfj_child_outputs", latest_json)
                            first_model = next(
                                (value for value in latest_json.values() if isinstance(value, dict)),
                                None,
                            )
                            if isinstance(first_model, dict):
                                for field_key, field_value in first_model.items():
                                    context.setdefault(field_key, field_value)
                except Exception as enrich_err:
                    logger.debug(
                        "[PACK] Structured output enrichment skipped child=%s reason=%s",
                        child_chat_id,
                        enrich_err,
                    )

                results.append(
                    ChildResult(
                        child_chat_id=child_chat_id,
                        workflow_name=task_key,
                        context=context,
                        success=success,
                        error=None if success else "child_failed",
                    )
                )
            except Exception as exc:
                results.append(
                    ChildResult(
                        child_chat_id=child_chat_id,
                        workflow_name=task_key,
                        success=False,
                        error=str(exc),
                    )
                )
        return results

    @staticmethod
    def _validate_output_contract(child_results: Sequence[ChildResult], contract: MFJContract) -> List[str]:
        missing: List[str] = []
        required = list(contract.required)
        if not required:
            return missing
        for child in child_results:
            if not child.success:
                continue
            for key in required:
                if key not in child.context:
                    missing.append(f"{child.workflow_name}.{key}")
        return missing

    async def _retry_failed_children(
        self,
        *,
        transport: Any,
        active: _ActivePackRun,
        child_results: Sequence[ChildResult],
    ) -> int:
        failed_task_keys = {child.workflow_name for child in child_results if not child.success}
        if not failed_task_keys:
            return 0

        respawned = 0
        for task_key in failed_task_keys:
            state = active.child_runs.get(task_key)
            if state is None:
                continue

            old_chat = state.chat_id
            self._active_by_child.pop(old_chat, None)
            active.child_chat_to_task.pop(old_chat, None)

            child = await self._spawn_child_run(
                transport=transport,
                active=active,
                child_spec=state.spec,
            )
            if child is None:
                continue

            _, new_state, _payload = child
            new_state.retries = state.retries + 1
            active.child_runs[task_key] = new_state
            active.child_chat_to_task[new_state.chat_id] = task_key
            self._active_by_child[new_state.chat_id] = active.parent_chat_id
            respawned += 1

            if active.observer_ctx is not None:
                self._observer.on_child_spawned(
                    active.observer_ctx,
                    child_chat_id=new_state.chat_id,
                    task_key=task_key,
                )
        return respawned

    async def _inject_merged_context(
        self,
        *,
        transport: Any,
        parent_chat_id: str,
        app_id: str,
        inject_as: str,
        merged: Dict[str, Any],
        resume_context: Dict[str, Any],
    ) -> None:
        key = str(inject_as or "").strip()
        if not key:
            return
        pm = transport._get_or_create_persistence_manager()
        coll = await pm._coll()
        updates: Dict[str, Any] = {key: merged, "last_updated_at": datetime.now(timezone.utc)}
        for r_key, r_value in (resume_context or {}).items():
            updates[str(r_key)] = r_value
        await coll.update_one(
            {"_id": parent_chat_id, **build_app_scope_filter(app_id)},
            {"$set": updates},
        )

    async def _resume_parent(
        self,
        *,
        transport: Any,
        active: _ActivePackRun,
        merged_payload: Dict[str, Any],
        succeeded_count: int,
        failed_count: int,
        resume_nonce: str,
    ) -> None:
        existing = transport._background_tasks.get(active.parent_chat_id)
        if existing and not existing.done():
            return

        transport._background_tasks[active.parent_chat_id] = asyncio.create_task(
            transport._run_workflow_background(
                chat_id=active.parent_chat_id,
                workflow_name=active.parent_workflow_name,
                app_id=active.app_id,
                user_id=active.user_id,
                ws_id=active.ws_id,
                initial_message=None,
                initial_agent_name_override=active.resume_entry_agent,
            )
        )

        await self._emit_ui_event(
            transport=transport,
            target_chat_id=active.parent_chat_id,
            event_type="chat.workflow_resumed",
            data={
                "parent_chat_id": active.parent_chat_id,
                "workflow_name": active.parent_workflow_name,
                "trigger_id": active.trigger_id,
                "mfj_cycle": active.mfj_cycle,
                "resume_agent": active.resume_agent,
                "resume_entry_agent": active.resume_entry_agent,
                "inject_as": active.inject_as,
                "succeeded_count": int(max(0, succeeded_count)),
                "failed_count": int(max(0, failed_count)),
                "resume_nonce": str(resume_nonce),
                "resume_contract_keys": list(MFJ_RESUME_CONTEXT_KEYS),
                "merged_preview_keys": sorted(list(merged_payload.keys()))[:20],
            },
        )

    async def _emit_ui_event(
        self,
        *,
        transport: Any,
        target_chat_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        try:
            await transport.send_event_to_ui(
                {
                    "type": str(event_type),
                    "data": dict(data),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                target_chat_id,
            )
        except Exception:
            return

    @staticmethod
    def _child_index(active: _ActivePackRun, child_chat_id: str) -> int:
        for idx, cid in enumerate(active.child_chat_ids, start=1):
            if cid == child_chat_id:
                return idx
        return 0

    async def _record_mfj_completion(
        self,
        *,
        app_id: str,
        parent_chat_id: str,
        trigger_id: str,
        mfj_cycle: int,
        child_count: int,
        succeeded_count: int,
        failed_count: int,
        child_chat_ids: Sequence[str],
        summary: Dict[str, Any],
    ) -> None:
        parent = str(parent_chat_id or "").strip()
        trigger = str(trigger_id or "").strip()
        if not parent or not trigger:
            return

        completed = self._completed_mfjs.setdefault(parent, set())
        completed.add(trigger)

        preview_keys = sorted(list(summary.keys()))[:20] if isinstance(summary, dict) else []
        merge_preview = {"keys": preview_keys}
        try:
            await self._completion_store.write_completion(
                app_id=app_id,
                parent_chat_id=parent,
                trigger_id=trigger,
                mfj_cycle=mfj_cycle,
                child_count=child_count,
                succeeded_count=succeeded_count,
                failed_count=failed_count,
                child_chat_ids=list(child_chat_ids),
                merge_summary_preview=merge_preview,
            )
        except Exception as exc:
            logger.debug("[PACK] completion persistence failed parent=%s trigger=%s: %s", parent, trigger, exc)

    def _cleanup_active(self, active: _ActivePackRun) -> None:
        if active._timeout_task and not active._timeout_task.done():
            active._timeout_task.cancel()

        self._active_by_parent.pop(active.parent_chat_id, None)
        for child_chat_id in list(active.child_chat_to_task.keys()):
            self._active_by_child.pop(child_chat_id, None)


__all__ = ["WorkflowPackCoordinator"]
