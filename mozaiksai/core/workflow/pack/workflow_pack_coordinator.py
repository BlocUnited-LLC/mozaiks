# === MOZAIKS-CORE-HEADER ===
# FILE: core/workflow/pack/workflow_pack_coordinator.py
# DESCRIPTION: Runtime-level coordinator for nested/multi-workflow packs.
#
# This is the fan-out / fan-in engine.  It sits at Layer 1.5 between
# single-GroupChat execution (orchestration_patterns.py) and the
# transport layer.  It reacts to events emitted by the AG2 adapter
# and uses transport primitives to pause/spawn/resume workflows.
#
# Key flow:
#   1. structured_output_ready → detect trigger → pause parent → spawn N children
#   2. run_complete (per child) → check if all done → collect results → merge → inject → resume parent
# ==============================================================================

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from logs.logging_config import get_core_logger
from mozaiksai.core.workflow.pack.merge import (
    ChildResult,
    MergeResult,
    MergeStrategy,
    get_merge_strategy,
)


logger = get_core_logger("workflow_pack_coordinator")


@dataclass
class _ActivePackRun:
    parent_chat_id: str
    parent_workflow_name: str
    app_id: str
    user_id: str
    ws_id: Optional[int]
    resume_agent: Optional[str]
    child_chat_ids: List[str]
    # --- enriched fields ---
    merge_strategy: MergeStrategy = field(default_factory=lambda: get_merge_strategy("collect_all"))
    inject_as: str = "mfj_child_outputs"       # context key for merged results
    trigger_id: Optional[str] = None
    timeout_seconds: int = 600                   # max wait for all children
    _timeout_task: Optional[asyncio.Task] = field(default=None, repr=False)


class WorkflowPackCoordinator:
    """Runtime-level coordinator for nested/multi-workflow packs.

    This coordinator is intentionally lightweight:
    - It does NOT implement business logic.
    - It reacts to agent-produced structured outputs (via dispatcher events) and
      uses transport primitives to pause/spawn/resume workflows.

    Contract:
    - Parent workflow may ship a declarative config at `workflows/<name>/_pack/workflow_graph.json`.
    - A trigger agent emits a structured output containing a list of child workflows.
    - Children run as independent AG2 GroupChats (separate `chat_id`s).
    - When children complete, parent is resumed at a chosen initiating agent.

    Safety:
    - Enforces app_id boundary.
        - Default behavior (spawn_mode=workflow): skips spawning workflows that do not exist on disk.
        - Generator behavior (spawn_mode=generator_subrun): spawns generator subruns for workflows that
            may not exist on disk yet (intended for mid-workflow generation).
    """

    def __init__(self) -> None:
        self._active_by_parent: Dict[str, _ActivePackRun] = {}
        self._active_by_child: Dict[str, str] = {}
        self._completed_mfjs: Dict[str, List[str]] = {}  # parent_chat_id → [trigger_id, ...]

    async def handle_structured_output_ready(self, event: Dict[str, Any]) -> None:
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

        triggers = pack_cfg.get("nested_chats")
        if not isinstance(triggers, list) or not triggers:
            return

        # For now, only one nested chat can be active per parent chat.
        if parent_chat_id in self._active_by_parent:
            return

        # Find a matching trigger entry.
        trigger_entry = None
        for entry in triggers:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("trigger_agent") or "") == agent_name:
                trigger_entry = entry
                break
        if not trigger_entry:
            return

        # Extract child workflows from structured output.
        plan = self._extract_pack_plan(structured_data)
        if not plan:
            return

        is_multi = bool(plan.get("is_multi_workflow"))
        workflows = plan.get("workflows")
        if not is_multi:
            return
        if not isinstance(workflows, list) or not workflows:
            return

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

        # Pre-compute spawnable children so we don't pause/restart the parent when nothing is runnable.
        spawnable: List[Dict[str, Any]] = []
        for wf in workflows:
            if not isinstance(wf, dict):
                continue
            child_workflow_name = wf.get("name")
            if not isinstance(child_workflow_name, str) or not child_workflow_name.strip():
                continue
            child_workflow_name = child_workflow_name.strip()

            if spawn_mode == "workflow":
                if not (Path("workflows") / child_workflow_name).exists():
                    logger.info("[PACK] Skipping spawn for missing workflow=%s", child_workflow_name)
                    continue

            spawnable.append(wf)

        if not spawnable:
            return

        # Resume agent can come from output (preferred) or workflow_graph.json.
        resume_agent = None
        raw_resume = plan.get("resume_agent")
        if isinstance(raw_resume, str) and raw_resume.strip():
            resume_agent = raw_resume.strip()
        else:
            cfg_resume = trigger_entry.get("resume_agent")
            if isinstance(cfg_resume, str) and cfg_resume.strip():
                resume_agent = cfg_resume.strip()

        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        # MONOLITH ASSUMPTION: transport + orchestration are process-local.
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

        # Pause parent before starting children (so parent doesn't keep running).
        try:
            await transport.pause_background_workflow(chat_id=parent_chat_id, reason="spawn_children")
        except Exception as exc:
            logger.debug("[PACK] Failed pausing parent chat=%s: %s", parent_chat_id, exc)

        child_chat_ids: List[str] = []
        started_payloads: List[Dict[str, Any]] = []

        pm = transport._get_or_create_persistence_manager()  # runtime-owned

        for wf in spawnable:
            if not isinstance(wf, dict):
                continue
            target_workflow_name = wf.get("name")
            if not isinstance(target_workflow_name, str) or not target_workflow_name.strip():
                continue
            target_workflow_name = target_workflow_name.strip()

            child_workflow_name = target_workflow_name if spawn_mode == "workflow" else str(generator_workflow)

            initial_agent_override = wf.get("initial_agent")
            if not isinstance(initial_agent_override, str) or not initial_agent_override.strip():
                cfg_initial_agent = trigger_entry.get("child_initial_agent")
                if spawn_mode == "generator_subrun" and isinstance(cfg_initial_agent, str) and cfg_initial_agent.strip():
                    initial_agent_override = cfg_initial_agent.strip()
                else:
                    initial_agent_override = None
            else:
                initial_agent_override = initial_agent_override.strip()

            initial_message = wf.get("initial_message")
            if not isinstance(initial_message, str) or not initial_message.strip():
                if spawn_mode == "generator_subrun":
                    wf_desc = wf.get("description")
                    if isinstance(wf_desc, str) and wf_desc.strip():
                        initial_message = f"Generate a new workflow named '{target_workflow_name}'. Description: {wf_desc.strip()}"
                    else:
                        initial_message = f"Generate a new workflow named '{target_workflow_name}'."
                else:
                    initial_message = None
            else:
                initial_message = initial_message.strip()

            import uuid

            if spawn_mode == "generator_subrun":
                new_chat_id = f"chat_gen_{target_workflow_name}_{uuid.uuid4().hex[:8]}"
            else:
                new_chat_id = f"chat_{child_workflow_name}_{uuid.uuid4().hex[:8]}"

            extra_fields: Dict[str, Any] = {
                "parent_chat_id": parent_chat_id,
                "parent_workflow_name": parent_workflow,
                "spawn_mode": spawn_mode,
                "spawn_trigger_agent": agent_name,
                "spawn_trigger_id": trigger_entry.get("id"),
            }

            if spawn_mode == "generator_subrun":
                # Seed minimal context so the generator workflow can start mid-run
                # while still having PatternSelection + index available.
                wf_item: Dict[str, Any] = dict(wf)
                wf_item["name"] = target_workflow_name
                pattern_selection = {
                    "is_multi_workflow": False,
                    "resume_agent": None,
                    "decomposition_reason": None,
                    "pack_name": target_workflow_name,
                    "workflows": [wf_item],
                }
                extra_fields.update(
                    {
                        "is_child_workflow": True,
                        "generated_workflow_name": target_workflow_name,
                        "generated_workflow_description": wf.get("description"),
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

            # Add to session registry if we have a ws_id.
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
            started_payloads.append(
                {
                    "chat_id": new_chat_id,
                    "workflow_name": str(child_workflow_name),
                    "app_id": str(app_id),
                    "user_id": str(user_id),
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
            # MONOLITH ASSUMPTION: spawning a child == create an in-process asyncio task.

            self._active_by_child[new_chat_id] = parent_chat_id

        if not child_chat_ids:
            # Nothing spawned; resume parent immediately.
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

        # Resolve merge strategy from trigger config.
        strategy_name = str(trigger_entry.get("aggregation_strategy") or "collect_all").strip().lower()
        merge_strategy = get_merge_strategy(strategy_name)
        inject_as = str(trigger_entry.get("inject_as") or "mfj_child_outputs").strip()
        timeout_seconds = int(trigger_entry.get("timeout_seconds") or 600)
        trigger_id = str(trigger_entry.get("id") or f"trigger_{agent_name}").strip()

        # Check multi-MFJ sequencing (requires field).
        requires = trigger_entry.get("requires")
        if isinstance(requires, list) and requires:
            completed = self._completed_mfjs.get(parent_chat_id, [])
            for req in requires:
                req_id = str(req).strip()
                if req_id and req_id not in completed:
                    logger.info(
                        "[PACK] MFJ %s blocked — requires %s (completed: %s)",
                        trigger_id, req_id, completed,
                    )
                    return

        active_run = _ActivePackRun(
            parent_chat_id=parent_chat_id,
            parent_workflow_name=parent_workflow,
            app_id=str(app_id),
            user_id=str(user_id),
            ws_id=int(ws_id) if isinstance(ws_id, int) else None,
            resume_agent=resume_agent,
            child_chat_ids=child_chat_ids,
            merge_strategy=merge_strategy,
            inject_as=inject_as,
            trigger_id=trigger_id,
            timeout_seconds=timeout_seconds,
        )
        self._active_by_parent[parent_chat_id] = active_run

        # Start timeout watchdog.
        if timeout_seconds > 0:
            active_run._timeout_task = asyncio.create_task(
                self._timeout_watchdog(parent_chat_id, timeout_seconds)
            )

        # Notify UI on parent channel so it can connect to child chat_ids.
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
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                parent_chat_id,
            )
        except Exception:
            # Best-effort only; runtime should still work without UI notification.
            pass

        logger.info(
            "[PACK] Spawned %d child workflows for parent chat=%s via agent=%s model=%s",
            len(child_chat_ids),
            parent_chat_id,
            agent_name,
            model_name,
        )

    async def handle_run_complete(self, payload: Dict[str, Any]) -> None:
        """Called when transport emits a run_complete envelope for any chat."""
        try:
            chat_id = str(payload.get("chat_id") or "")
        except Exception:
            return
        if not chat_id:
            return

        parent_chat_id = self._active_by_child.get(chat_id)
        if not parent_chat_id:
            return

        active = self._active_by_parent.get(parent_chat_id)
        if not active:
            return

        # If all children are done, resume parent and cancel any stragglers.
        all_done = True
        for child_id in list(active.child_chat_ids):
            # If there's still an active background task, it isn't done.
            # We treat missing task handle as done (task cleaned up).
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            if not transport:
                return
            t = transport._background_tasks.get(child_id)
            if t and not t.done():
                all_done = False
                break

        # MONOLITH ASSUMPTION: completion + resume decisions use in-process task state.

        if not all_done:
            return

        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        if not transport:
            return

        # Cancel timeout watchdog.
        if active._timeout_task and not active._timeout_task.done():
            active._timeout_task.cancel()

        # Cleanup child indexes.
        for child_id in list(active.child_chat_ids):
            self._active_by_child.pop(child_id, None)

        self._active_by_parent.pop(parent_chat_id, None)

        # --- FAN-IN: collect + merge + inject ---
        child_results = await self._collect_child_results(active, transport)
        merge_result = active.merge_strategy.merge(child_results)

        logger.info(
            "[PACK] Fan-in complete for parent=%s: %d children, %d failed, strategy=%s",
            parent_chat_id,
            merge_result.child_count,
            merge_result.failed_count,
            merge_result.strategy_used,
        )

        # Inject merged results into parent's persisted context.
        await self._inject_merged_context(
            transport=transport,
            parent_chat_id=parent_chat_id,
            app_id=active.app_id,
            inject_as=active.inject_as,
            merged=merge_result.merged,
        )

        # Record MFJ completion for sequencing.
        if active.trigger_id:
            self._completed_mfjs.setdefault(parent_chat_id, []).append(active.trigger_id)

        await self._resume_parent(
            transport=transport,
            parent_chat_id=active.parent_chat_id,
            parent_workflow=active.parent_workflow_name,
            app_id=active.app_id,
            user_id=active.user_id,
            ws_id=active.ws_id,
            resume_agent=active.resume_agent,
        )

    # ----------------------------
    # Internals
    # ----------------------------

    def _load_pack_graph(self, workflow_name: str) -> Optional[Dict[str, Any]]:
        try:
            path = Path("workflows") / workflow_name / "_pack" / "workflow_graph.json"
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("[PACK] Failed reading pack graph for %s: %s", workflow_name, exc)
            return None

    def _extract_pack_plan(self, structured_data: Any) -> Optional[Dict[str, Any]]:
        """Extract a normalized plan from structured outputs.

        Supports PatternSelectionOutput shape:
          {"PatternSelection": {"is_multi_workflow": bool, "workflows": [...] , "resume_agent": ...}}
        """
        if not isinstance(structured_data, dict):
            return None

        ps = structured_data.get("PatternSelection")
        if isinstance(ps, dict):
            return ps

        # Some agents may nest differently; try common alternatives.
        ps2 = structured_data.get("pattern_selection")
        if isinstance(ps2, dict):
            return ps2

        return None

    # ----------------------------
    # Fan-in helpers
    # ----------------------------

    async def _collect_child_results(
        self, active: _ActivePackRun, transport: Any
    ) -> List[ChildResult]:
        """Read each child's final context from MongoDB."""
        results: List[ChildResult] = []
        try:
            pm = transport._get_or_create_persistence_manager()
            coll = await pm._coll()
        except Exception as exc:
            logger.warning("[PACK] Cannot access persistence for fan-in: %s", exc)
            return results

        from mozaiksai.core.multitenant import build_app_scope_filter

        for child_id in active.child_chat_ids:
            try:
                doc = await coll.find_one(
                    {"_id": child_id, **build_app_scope_filter(active.app_id)},
                    projection={
                        "_id": 1,
                        "workflow_name": 1,
                        "status": 1,
                        "extra_context": 1,
                        "context_variables": 1,
                    },
                )
                if not doc:
                    results.append(ChildResult(
                        child_chat_id=child_id,
                        workflow_name="unknown",
                        success=False,
                        error="session not found",
                    ))
                    continue

                wf_name = str(doc.get("workflow_name") or "unknown")
                # Child outputs may be in extra_context or context_variables.
                ctx = doc.get("extra_context") or doc.get("context_variables") or {}
                if not isinstance(ctx, dict):
                    ctx = {}

                # Check whether the child succeeded.
                status_val = doc.get("status")
                success = True
                if isinstance(status_val, int) and status_val < 0:
                    success = False
                if isinstance(status_val, str) and status_val.lower() in ("failed", "error"):
                    success = False

                results.append(ChildResult(
                    child_chat_id=child_id,
                    workflow_name=wf_name,
                    context=ctx,
                    success=success,
                ))
            except Exception as exc:
                logger.warning("[PACK] Failed reading child result %s: %s", child_id, exc)
                results.append(ChildResult(
                    child_chat_id=child_id,
                    workflow_name="unknown",
                    success=False,
                    error=str(exc),
                ))

        return results

    async def _inject_merged_context(
        self,
        *,
        transport: Any,
        parent_chat_id: str,
        app_id: str,
        inject_as: str,
        merged: Dict[str, Any],
    ) -> None:
        """Persist merged child outputs into the parent's session so the resume
        agent can read them from context_variables."""
        if not merged:
            return
        try:
            pm = transport._get_or_create_persistence_manager()
            coll = await pm._coll()
            from mozaiksai.core.multitenant import build_app_scope_filter

            await coll.update_one(
                {"_id": parent_chat_id, **build_app_scope_filter(app_id)},
                {"$set": {f"extra_context.{inject_as}": merged}},
            )
            logger.debug(
                "[PACK] Injected merged context key=%s into parent=%s (%d keys)",
                inject_as,
                parent_chat_id,
                len(merged),
            )
        except Exception as exc:
            logger.warning("[PACK] Failed injecting merged context into parent=%s: %s", parent_chat_id, exc)

    async def _timeout_watchdog(self, parent_chat_id: str, timeout_seconds: int) -> None:
        """Cancel in-flight children if they exceed the configured timeout."""
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return  # Normal — all children finished before timeout.

        active = self._active_by_parent.get(parent_chat_id)
        if not active:
            return

        logger.warning(
            "[PACK] Timeout (%ds) reached for parent=%s — forcing fan-in with available results",
            timeout_seconds,
            parent_chat_id,
        )

        # Cancel remaining child tasks.
        from mozaiksai.core.transport.simple_transport import SimpleTransport
        transport = await SimpleTransport.get_instance()
        if not transport:
            return

        for child_id in list(active.child_chat_ids):
            t = transport._background_tasks.get(child_id)
            if t and not t.done():
                t.cancel()

        # Trigger fan-in with whatever we have.
        # Cleanup child indexes.
        for child_id in list(active.child_chat_ids):
            self._active_by_child.pop(child_id, None)
        self._active_by_parent.pop(parent_chat_id, None)

        child_results = await self._collect_child_results(active, transport)
        merge_result = active.merge_strategy.merge(child_results)

        await self._inject_merged_context(
            transport=transport,
            parent_chat_id=parent_chat_id,
            app_id=active.app_id,
            inject_as=active.inject_as,
            merged=merge_result.merged,
        )

        if active.trigger_id:
            self._completed_mfjs.setdefault(parent_chat_id, []).append(active.trigger_id)

        await self._resume_parent(
            transport=transport,
            parent_chat_id=parent_chat_id,
            parent_workflow=active.parent_workflow_name,
            app_id=active.app_id,
            user_id=active.user_id,
            ws_id=active.ws_id,
            resume_agent=active.resume_agent,
        )

    # ----------------------------
    # Resume
    # ----------------------------

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
    ) -> None:
        # Cancel any running parent task first (idempotent)
        try:
            await transport.pause_background_workflow(chat_id=parent_chat_id, reason="resume_restart")
        except Exception:
            pass

        # Start orchestration again; it will resume from persisted history.
        # Merged child outputs are already in extra_context (injected above).
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


__all__ = ["WorkflowPackCoordinator"]
