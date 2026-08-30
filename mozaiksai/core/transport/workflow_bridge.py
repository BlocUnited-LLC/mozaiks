# ==============================================================================
# FILE: mozaiksai/core/transport/workflow_bridge.py
# DESCRIPTION: Workflow integration layer - orchestration execution bridge
# ==============================================================================
"""
Workflow bridge mixin for SimpleTransport.

This module handles workflow orchestration integration:
- API-driven workflow execution
- Background workflow running
- Workflow pausing/resuming
- Lifecycle event emission

Usage:
    class SimpleTransport(WorkflowBridgeMixin):
        ...
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mozaiksai.core.runtime.composition.extensions import get_workflow_lifecycle_hooks
from mozaiksai.core.transport.session_registry import session_registry

if TYPE_CHECKING:
    pass

logger = logging.getLogger("simple_transport.workflow")


def _persist_context_kwargs(
    *,
    chat_id: str,
    app_id: str,
    variables: dict[str, Any],
    workflow_name: str | None,
) -> dict[str, Any]:
    resolved_workflow_name = str(workflow_name or "").strip()
    if not resolved_workflow_name:
        raise ValueError("workflow_name is required to persist workflow context")
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "app_id": app_id,
        "workflow_name": resolved_workflow_name,
        "variables": variables,
    }
    return kwargs


class WorkflowBridgeMixin:
    """Mixin providing workflow integration functionality.

    Expects the following attributes on the class:
        - _input_request_registries: Dict[str, Dict[str, Any]]
        - _workflow_spawn_semaphore: asyncio.Semaphore
        - _background_tasks: Dict[str, asyncio.Task]
        - connections: Dict[str, Dict[str, Any]]
        - submit_user_input(request_id, input): method
        - process_incoming_user_message(...): method
        - send_error(message, code, chat_id): method
        - _build_resume_signal(chat_id, request_id): method
    """

    # ==================================================================================
    # API-DRIVEN WORKFLOW EXECUTION
    # ==================================================================================

    def _ui_run_complete_registry(self) -> dict[str, bool]:
        registry = getattr(self, "_ui_run_complete_sent", None)
        if not isinstance(registry, dict):
            registry = {}
            self._ui_run_complete_sent = registry
        return registry

    def _has_ui_run_complete_sent(self, chat_id: str) -> bool:
        registry = self._ui_run_complete_registry()
        if bool(registry.get(chat_id)):
            return True
        conn = getattr(self, "connections", {}).get(chat_id)
        return bool(isinstance(conn, dict) and conn.get("ui_run_complete_sent"))

    def _mark_ui_run_complete_sent(self, chat_id: str) -> None:
        self._ui_run_complete_registry()[chat_id] = True
        conn = getattr(self, "connections", {}).get(chat_id)
        if isinstance(conn, dict):
            conn["ui_run_complete_sent"] = True

    async def _emit_synthetic_run_complete_if_needed(
        self,
        *,
        chat_id: str,
        workflow_name: str,
        run_status_value: str,
    ) -> None:
        if str(run_status_value).strip().lower() != "completed":
            return
        if self._has_ui_run_complete_sent(chat_id):
            return
        pending_registry = getattr(self, "_input_request_registries", {}).get(chat_id)
        if isinstance(pending_registry, dict) and pending_registry:
            return
        persistence_manager = None
        if hasattr(self, "_get_or_create_persistence_manager"):
            try:
                persistence_manager = self._get_or_create_persistence_manager()
            except Exception:
                persistence_manager = None
        if persistence_manager is not None:
            conn = getattr(self, "connections", {}).get(chat_id) or {}
            app_id = conn.get("app_id")
            if app_id:
                try:
                    pending_input = await persistence_manager.get_pending_input_request(
                        chat_id=chat_id,
                        app_id=str(app_id),
                    )
                except Exception:
                    pending_input = None
                if isinstance(pending_input, dict) and pending_input:
                    return
        await self.send_event_to_ui(
            {
                "kind": "run_complete",
                "agent": workflow_name or "workflow",
                "chat_id": chat_id,
                "status": 1,
                "reason": "finished",
                "awaiting_user_input": False,
                "metadata": {"source": "workflow_bridge.synthetic_completion"},
            },
            chat_id,
        )

    async def _apply_user_text_context_updates(
        self,
        *,
        chat_id: str,
        workflow_name: str | None,
        app_id: str | None,
        user_input: str | None,
    ) -> dict[str, Any]:
        """Apply declarative user_text triggers before resuming a paused workflow."""

        candidate = str(user_input or "").strip()
        if not candidate or not workflow_name or not app_id:
            return {}

        persistence_manager = None
        if hasattr(self, "_get_or_create_persistence_manager"):
            try:
                persistence_manager = self._get_or_create_persistence_manager()
            except Exception:
                persistence_manager = None

        manager = getattr(self, "_derived_context_managers", {}).get(chat_id)
        if manager is None:
            persisted_context: dict[str, Any] = {}
            if persistence_manager is not None:
                persisted_context = await persistence_manager.fetch_chat_session_extra_context(
                    chat_id=chat_id,
                    app_id=str(app_id),
                    workflow_name=str(workflow_name),
                )
            try:
                from mozaiksai.core.workflow.context.adapter import create_context_container
                from mozaiksai.core.workflow.context.authority import build_context_authority_policy
                from mozaiksai.core.workflow.context.derived import DerivedContextManager
                from mozaiksai.core.workflow.execution.run_bootstrap import (
                    merge_persisted_extra_context,
                )
                from mozaiksai.core.workflow.workflow_manager import workflow_manager

                workflow_config = workflow_manager.get_config(str(workflow_name)) or {}
                authority_policy = build_context_authority_policy(
                    workflow_name=str(workflow_name),
                    definitions=(workflow_config.get("context_variables") or {}).get("definitions") or {},
                    transition_rules=(workflow_config.get("transition_graph") or {}).get("transition_rules") or [],
                )
                context_container = create_context_container(
                    initial={},
                    authority_policy=authority_policy,
                )
                if persisted_context:
                    merge_persisted_extra_context(context_container, persisted_context)
                manager = DerivedContextManager(
                    str(workflow_name),
                    {},
                    context_container,
                )
            except Exception as err:
                logger.debug("[SMART_ROUTING] Failed to build user_text manager for %s: %s", chat_id, err)
                manager = None

        if manager is None or not hasattr(manager, "apply_user_text"):
            return {}

        try:
            updated = manager.apply_user_text(candidate)
        except Exception as err:
            logger.debug("[SMART_ROUTING] user_text trigger apply failed for %s: %s", chat_id, err)
            return {}

        if updated and persistence_manager is not None:
            await persistence_manager.persist_context_variables(
                **_persist_context_kwargs(
                    chat_id=chat_id,
                    app_id=str(app_id),
                    workflow_name=str(workflow_name),
                    variables=updated,
                )
            )

        return updated

    async def handle_user_input_from_api(
        self,
        chat_id: str,
        user_id: str | None,
        workflow_name: str,
        message: str | None,
        app_id: str,
        initial_agent_name_override: str | None = None,
    ) -> dict[str, Any]:
        """
        Handle user input from the POST API endpoint with smart routing.

        Checks if there's an active workflow run waiting for input. If yes,
        passes message to the existing run. If no, starts a new workflow run.
        """
        try:
            starting_new_workflow = False
            is_resume_request = bool(
                isinstance(initial_agent_name_override, str)
                and initial_agent_name_override.strip()
                and not (isinstance(message, str) and message.strip())
            )

            # Load workflow-declared lifecycle hooks (modular, per-workflow)
            lifecycle = get_workflow_lifecycle_hooks(workflow_name)
            _emit_execution_started = lifecycle.get("on_start")
            _emit_execution_completed = lifecycle.get("on_complete")
            _emit_execution_failed = lifecycle.get("on_fail")

            # Check if there's an active AG2 session waiting for user input
            has_active_session = bool(self._input_request_registries.get(chat_id))

            # Also check if there are pending input callbacks for this chat
            active_callbacks = False
            if chat_id in self._input_request_registries:
                active_callbacks = bool(self._input_request_registries[chat_id])

            logger.debug("[SMART_ROUTING] chat=%s has_registry=%s has_callbacks=%s", chat_id, has_active_session, active_callbacks)

            live_run = None
            get_live_run = getattr(self, "get_live_ag2_workflow_run", None)
            if callable(get_live_run):
                live_run = get_live_run(chat_id)
            if live_run is not None and isinstance(message, str) and message.strip():
                return await self._continue_live_ag2_workflow_run(
                    live_run=live_run,
                    chat_id=chat_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    message=message,
                    app_id=app_id,
                )

            if has_active_session and active_callbacks:
                # Route to existing AG2 session via WebSocket callback mechanism
                logger.debug("[SMART_ROUTING] Continuing existing AG2 session for chat %s", chat_id)

                # Get any available request_id from the registry
                registry = self._input_request_registries.get(chat_id, {})
                if registry:
                    # Get the first available request_id
                    request_id = next(iter(registry.keys()))

                    normalized_message = message
                    resume_signal = False
                    if not normalized_message or (isinstance(normalized_message, str) and not normalized_message.strip()):
                        normalized_message = self._build_resume_signal(chat_id, request_id)
                        resume_signal = True

                    success = await self.submit_user_input(request_id, str(normalized_message))

                    if success:
                        route = "existing_session_resume" if resume_signal else "existing_session"
                        # Don't persist/echo resume signal messages - they're internal coordination only
                        if not resume_signal:
                            # Persist actual user messages to the canonical AG2 run stream.
                            try:
                                pm = self._get_or_create_persistence_manager()
                                append_user_message = getattr(pm, "append_run_user_message", None)
                                if append_user_message is not None:
                                    await append_user_message(
                                        chat_id=chat_id,
                                        app_id=app_id,
                                        content=str(message or ""),
                                        metadata={"source": "workflow_user", "user_id": user_id},
                                    )
                                await self.process_incoming_user_message(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    content=message,
                                    source='http'
                                )
                            except Exception as persist_err:
                                logger.debug("User message persistence failed (non-fatal): %s", persist_err)
                        return {"status": "success", "chat_id": chat_id, "message": "Input passed to existing AG2 session.", "route": route}
                    else:
                        logger.warning("[SMART_ROUTING] Failed to submit input to existing session, falling back to new workflow")

            # No active session or callback failed - start new workflow
            logger.debug("[SMART_ROUTING] Starting new workflow for chat %s", chat_id)
            starting_new_workflow = True

            from mozaiksai.core.adapters.ag2_orchestration import get_ag2_adapter
            from mozaiksai.core.ports.orchestration import ResumeRequest, RunRequest

            if message or is_resume_request:
                try:
                    pm = self._get_or_create_persistence_manager()
                    pending = await pm.get_pending_input_request(
                        chat_id=chat_id,
                        app_id=app_id,
                    )
                    if pending:
                        await pm.clear_pending_input_request(
                            chat_id=chat_id,
                            app_id=app_id,
                        )
                        logger.debug(
                            "[SMART_ROUTING] Cleared persisted pending input request %s before launching workflow for chat %s",
                            pending.get("request_id"),
                            chat_id,
                        )
                except Exception as clear_err:
                    logger.debug(
                        "[SMART_ROUTING] Failed clearing persisted pending input request for %s: %s", chat_id, clear_err)

            # Only persist and echo user message when starting NEW workflows
            # For existing sessions, the message goes directly to AG2 via callback
            if message:
                try:
                    await self._apply_user_text_context_updates(
                        chat_id=chat_id,
                        workflow_name=workflow_name,
                        app_id=app_id,
                        user_input=message,
                    )
                except Exception as trigger_err:
                    logger.debug("[SMART_ROUTING] user_text trigger update skipped for new run %s: %s", chat_id, trigger_err)
                try:
                    pm = self._get_or_create_persistence_manager()
                    append_user_message = getattr(pm, "append_run_user_message", None)
                    if append_user_message is not None:
                        await append_user_message(
                            chat_id=chat_id,
                            app_id=app_id,
                            content=str(message or ""),
                            metadata={"source": "workflow_user", "user_id": user_id},
                        )
                    await self.process_incoming_user_message(
                        chat_id=chat_id,
                        user_id=user_id,
                        content=message,
                        source='http'
                    )
                except Exception as persist_err:
                    logger.debug("Early persistence of user message failed (non-fatal): %s", persist_err)

            # Build lifecycle reporting (best-effort; non-blocking).
            if _emit_execution_started is not None:
                try:
                    _t = asyncio.create_task(
                        _emit_execution_started(
                            app_id=app_id,
                            execution_id=chat_id,
                            chat_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                        )
                    )
                    _t.add_done_callback(
                        lambda t: logger.debug("EXECUTION_STARTED_EMIT_FAILED chat=%s: %s", chat_id, t.exception())
                        if not t.cancelled() and t.exception() is not None
                        else None
                    )
                except Exception as _ev_exc:
                    logger.debug("EXECUTION_STARTED_EMIT_TASK_FAILED chat=%s: %s", chat_id, _ev_exc)

            # Launch orchestration via OrchestrationPort (engine-agnostic).
            # A missing message plus an explicit initial-agent override means the
            # caller is resuming an existing chat, not starting a fresh run.
            adapter = get_ag2_adapter()
            if is_resume_request:
                run_result = await adapter.resume(ResumeRequest(
                    workflow_name=workflow_name,
                    app_id=app_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    resume_agent=initial_agent_name_override,
                ))
            else:
                run_result = await adapter.run(RunRequest(
                    workflow_name=workflow_name,
                    app_id=app_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    initial_message=None,  # already persisted & sent upstream
                    initial_agent_name_override=initial_agent_name_override,
                ))

            run_status = getattr(run_result, "status", None)
            run_status_value = str(getattr(run_status, "value", run_status or "completed"))

            if _emit_execution_completed is not None and run_status_value == "completed":
                try:
                    _t = asyncio.create_task(
                        _emit_execution_completed(
                            app_id=app_id,
                            execution_id=chat_id,
                            chat_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                        )
                    )
                    _t.add_done_callback(
                        lambda t: logger.debug("EXECUTION_COMPLETED_EMIT_FAILED chat=%s: %s", chat_id, t.exception())
                        if not t.cancelled() and t.exception() is not None
                        else None
                    )
                except Exception as _ev_exc:
                    logger.debug("EXECUTION_COMPLETED_EMIT_TASK_FAILED chat=%s: %s", chat_id, _ev_exc)

            await self._emit_synthetic_run_complete_if_needed(
                chat_id=chat_id,
                workflow_name=workflow_name,
                run_status_value=run_status_value,
            )

            route = "workflow_resume" if is_resume_request else "new_workflow"
            message_text = "Workflow resumed successfully." if is_resume_request else "Workflow started successfully."
            return {
                "status": "success",
                "chat_id": chat_id,
                "message": message_text,
                "route": route,
                "run_status": run_status_value,
            }

        except Exception as e:
            # Surface token denial before the generic failure path so the UI
            # receives structured recovery metadata instead of WORKFLOW_EXECUTION_FAILED.
            if e.__class__.__name__ == "TokenUsageDenied":
                decision = getattr(e, "decision", None)
                extra_data = (
                    decision.to_error_metadata()
                    if hasattr(decision, "to_error_metadata")
                    else {"error_code": getattr(decision, "error_code", "INSUFFICIENT_TOKENS")}
                )
                logger.warning(
                    "Token usage denied during workflow execution chat=%s: %s", chat_id, e
                )
                await self.send_error(
                    error_message=str(e),
                    error_code=extra_data.get("error_code", "INSUFFICIENT_TOKENS"),
                    chat_id=chat_id,
                    extra_data=extra_data,
                )
                return {"status": "error", "chat_id": chat_id, "message": "Insufficient token balance"}

            logger.error("User input handling failed for chat %s: %s", chat_id, e, exc_info=True)
            if starting_new_workflow and _emit_execution_failed is not None:
                try:
                    _t = asyncio.create_task(
                        _emit_execution_failed(
                            app_id=app_id,
                            execution_id=chat_id,
                            chat_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                            message="workflow_execution_failed",
                            details=None,
                        )
                    )
                    _t.add_done_callback(
                        lambda t: logger.debug("EXECUTION_FAILED_EMIT_FAILED chat=%s: %s", chat_id, t.exception())
                        if not t.cancelled() and t.exception() is not None
                        else None
                    )
                except Exception as _ev_exc:
                    logger.debug("EXECUTION_FAILED_EMIT_TASK_FAILED chat=%s: %s", chat_id, _ev_exc)
            # Clear stuck REVISING state so the next refinement request can route correctly.
            if app_id and user_id:
                try:
                    from mozaiksai.core.session.router import get_session_router
                    _rev_task = asyncio.create_task(
                        get_session_router().fail_active_revision(
                            app_id=app_id,
                            user_id=user_id,
                            workflow_id=workflow_name,
                        )
                    )
                    _rev_task.add_done_callback(
                        lambda t: logger.debug("REVISION_FAIL_TASK_FAILED app=%s workflow=%s: %s", app_id, workflow_name, t.exception())
                        if not t.cancelled() and t.exception() is not None
                        else None
                    )
                except Exception as _rev_exc:
                    logger.debug("REVISION_FAIL_TASK_CREATE_FAILED app=%s workflow=%s: %s", app_id, workflow_name, _rev_exc)
            await self.send_error(
                error_message="An internal error occurred. Please try again.",
                error_code="WORKFLOW_EXECUTION_FAILED",
                chat_id=chat_id
            )
            return {"status": "error", "chat_id": chat_id, "message": "Workflow execution failed"}

    async def _continue_live_ag2_workflow_run(
        self,
        *,
        live_run: Any,
        chat_id: str,
        user_id: str | None,
        workflow_name: str,
        message: str,
        app_id: str,
    ) -> dict[str, Any]:
        """Continue a process-live AG2 Network workflow channel."""

        from mozaiksai.core.ports.orchestration import RunStatus
        from mozaiksai.core.workflow.orchestration_patterns import (
            _emit_validated_structured_outputs_from_runner_result,
            _last_agent_name_from_runner_result,
            _project_ag2_wal_to_mozaiks_transport,
        )

        context_updates = await self._apply_user_text_context_updates(
            chat_id=chat_id,
            workflow_name=workflow_name,
            app_id=app_id,
            user_input=message,
        )
        pm = self._get_or_create_persistence_manager()
        append_user_message = getattr(pm, "append_run_user_message", None)
        if append_user_message is not None:
            await append_user_message(
                chat_id=chat_id,
                app_id=app_id,
                content=str(message or ""),
                metadata={"source": "workflow_user", "user_id": user_id},
            )
        await self.process_incoming_user_message(
            chat_id=chat_id,
            user_id=user_id,
            content=message,
            source="http",
        )

        runner_result = await live_run.continue_with_user_message(
            message,
            context_updates=context_updates,
        )
        manager = getattr(self, "_derived_context_managers", {}).get(chat_id)
        try:
            from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

            _, structured_registry = load_workflow_structured_outputs(workflow_name)
        except Exception:
            structured_registry = {}
        ctx = dict(getattr(runner_result, "context_variables", {}) or {})
        await _emit_validated_structured_outputs_from_runner_result(
            runner_result=runner_result,
            workflow_name=workflow_name,
            chat_id=chat_id,
            app_id=app_id,
            user_id=user_id,
            turn_sequence_start=0,
            context_vars_dict=ctx,
            context_bridge=None,
            structured_registry=structured_registry,
            wf_logger=logger,
        )
        await _project_ag2_wal_to_mozaiks_transport(
            runner_result=runner_result,
            transport=self,
            persistence_manager=pm,
            chat_id=chat_id,
            app_id=app_id,
            agent_name_by_id=runner_result.agent_name_by_id,
            initial_sequence=0,
            derived_context_manager=manager,
            structured_registry=structured_registry,
        )

        if ctx:
            await pm.persist_context_variables(
                **_persist_context_kwargs(
                    chat_id=chat_id,
                    app_id=app_id,
                    workflow_name=workflow_name,
                    variables=ctx,
                )
            )

        run_failed = runner_result.status is RunStatus.FAILED
        awaiting_user_input = runner_result.status is RunStatus.PAUSED
        run_completed = runner_result.status is RunStatus.COMPLETED
        pause_agent = _last_agent_name_from_runner_result(runner_result) if awaiting_user_input else None

        if run_completed or run_failed:
            pop_live_run = getattr(self, "pop_live_ag2_workflow_run", None)
            if callable(pop_live_run):
                pop_live_run(chat_id)
        elif awaiting_user_input:
            register_live_run = getattr(self, "register_live_ag2_workflow_run", None)
            if callable(register_live_run):
                register_live_run(chat_id, live_run)

        if awaiting_user_input:
            await self.send_event_to_ui(
                {
                    "kind": "awaiting_reply",
                    "workflow": workflow_name,
                    "chat_id": chat_id,
                    "source_agent": pause_agent or workflow_name,
                    "reason": "awaiting_user_reply",
                    "display": "composer",
                    "interaction_type": "input_request",
                },
                chat_id,
            )

        await self.send_event_to_ui(
            {
                "kind": "run_complete",
                "workflow": workflow_name,
                "chat_id": chat_id,
                "run_completed": bool(run_completed and not run_failed),
                "awaiting_user_input": awaiting_user_input,
                "status": (
                    "failed"
                    if run_failed
                    else "paused"
                    if awaiting_user_input
                    else "completed"
                ),
                "reason": (
                    "failed"
                    if run_failed
                    else "awaiting_user_input"
                    if awaiting_user_input
                    else "finished"
                ),
                **({"agent": pause_agent} if pause_agent else {}),
                **({"error": runner_result.error} if runner_result.error else {}),
            },
            chat_id,
        )

        if run_completed and not run_failed:
            try:
                await pm.mark_chat_completed(chat_id, app_id=app_id)
            except Exception as complete_err:
                logger.debug("LIVE_AG2_MARK_COMPLETED_FAILED chat=%s: %s", chat_id, complete_err)

        return {
            "status": "success" if not run_failed else "error",
            "chat_id": chat_id,
            "message": (
                "Input passed to live AG2 workflow channel."
                if not run_failed
                else "Live AG2 workflow channel failed."
            ),
            "route": "live_ag2_network",
            "run_status": (
                "failed"
                if run_failed
                else "paused"
                if awaiting_user_input
                else "completed"
            ),
        }

    # ==================================================================================
    # BACKGROUND WORKFLOW EXECUTION
    # ==================================================================================

    async def _run_workflow_background(
        self,
        *,
        chat_id: str,
        workflow_name: str,
        app_id: str,
        user_id: str,
        ws_id: int | None,
        initial_message: str | None = None,
        initial_agent_name_override: str | None = None,
    ) -> None:
        """Run a workflow orchestration in the background.

        This enables parallel execution of multiple independent chats (each with its
        own chat_id) while preserving AG2-native semantics within each chat.
        """
        run_status_value = "failed"
        try:
            async with self._workflow_spawn_semaphore:
                try:
                    result = await self.handle_user_input_from_api(
                        chat_id=chat_id,
                        user_id=user_id,
                        workflow_name=workflow_name,
                        message=initial_message,
                        app_id=app_id,
                        initial_agent_name_override=initial_agent_name_override,
                    )
                    run_status = str(result.get("run_status") or "completed").strip().lower() or "completed"
                    run_status_value = run_status
                    # Emit run_complete success asynchronously to dispatcher
                    try:
                        from mozaiksai.core.events.unified_event_dispatcher import (
                            get_event_dispatcher,
                        )

                        dispatcher = get_event_dispatcher()
                        _t = asyncio.create_task(
                            dispatcher.emit(
                                "runtime.process_completed",
                                {
                                    "chat_id": chat_id,
                                    "workflow_name": workflow_name,
                                    "app_id": app_id,
                                    "user_id": user_id,
                                    "status": run_status,
                                },
                            )
                        )
                        _t.add_done_callback(
                            lambda t: logger.debug("PROCESS_COMPLETED_EMIT_FAILED chat=%s: %s", chat_id, t.exception())
                            if not t.cancelled() and t.exception() is not None
                            else None
                        )
                    except Exception as _ev_exc:
                        logger.debug("PROCESS_COMPLETED_EMIT_TASK_FAILED chat=%s: %s", chat_id, _ev_exc)
                    return result
                except Exception:
                    # Emit failed run_complete before re-raising so listeners can react
                    try:
                        from mozaiksai.core.events.unified_event_dispatcher import (
                            get_event_dispatcher,
                        )

                        dispatcher = get_event_dispatcher()
                        _t = asyncio.create_task(
                            dispatcher.emit(
                                "runtime.process_completed",
                                {
                                    "chat_id": chat_id,
                                    "workflow_name": workflow_name,
                                    "app_id": app_id,
                                    "user_id": user_id,
                                    "status": "failed",
                                },
                            )
                        )
                        _t.add_done_callback(
                            lambda t: logger.debug("PROCESS_FAILED_EMIT_FAILED chat=%s: %s", chat_id, t.exception())
                            if not t.cancelled() and t.exception() is not None
                            else None
                        )
                    except Exception as _ev_exc:
                        logger.debug("PROCESS_FAILED_EMIT_TASK_FAILED chat=%s: %s", chat_id, _ev_exc)
                    raise
        except asyncio.CancelledError:
            # Treat cancellation as an explicit pause request (adapter-driven).
            logger.info(
                "Background workflow cancelled (paused) workflow=%s chat=%s",
                workflow_name,
                chat_id,
            )
            raise
        except Exception as e:
            if e.__class__.__name__ == "TokenUsageDenied":
                decision = getattr(e, "decision", None)
                extra_data = (
                    decision.to_error_metadata()
                    if hasattr(decision, "to_error_metadata")
                    else {"error_code": getattr(decision, "error_code", "INSUFFICIENT_TOKENS")}
                )
                logger.warning(
                    "Token usage denied in background workflow=%s chat=%s: %s", workflow_name, chat_id, e
                )
                try:
                    await self.send_error(
                        error_message=str(e),
                        error_code=extra_data.get("error_code", "INSUFFICIENT_TOKENS"),
                        chat_id=chat_id,
                        extra_data=extra_data,
                    )
                except Exception as _send_exc:
                    logger.debug("WORKFLOW_BACKGROUND_TOKEN_DENIAL_SEND_FAILED chat=%s: %s", chat_id, _send_exc)
                return
            logger.error(
                "Background workflow run failed (workflow=%s chat=%s): %s", workflow_name, chat_id, e,
                exc_info=True,
            )
            try:
                await self.send_error(
                    error_message=f"Background workflow failed: {e}",
                    error_code="WORKFLOW_BACKGROUND_FAILED",
                    chat_id=chat_id,
                )
            except Exception as _send_exc:
                logger.debug("WORKFLOW_BACKGROUND_ERROR_SEND_FAILED chat=%s: %s", chat_id, _send_exc)
        finally:
            # Drop task handle
            try:
                self._background_tasks.pop(chat_id, None)
            except Exception:
                pass

            # Mark completed ONLY if we weren't cancelled.
            try:
                if ws_id:
                    task = asyncio.current_task()
                    was_cancelled = bool(task and task.cancelled())
                    if not was_cancelled and run_status_value == "completed":
                        session_registry.complete_workflow(ws_id, chat_id)
            except Exception as _reg_exc:
                logger.debug("SESSION_REGISTRY_COMPLETE_FAILED ws_id=%s chat=%s: %s", ws_id, chat_id, _reg_exc)

    # ==================================================================================
    # WORKFLOW PAUSE/RESUME
    # ==================================================================================

    async def pause_background_workflow(self, *, chat_id: str, reason: str = "paused") -> bool:
        """Cancel a running background workflow task so it can be resumed later.

        This is runtime-level orchestration only: AG2 state is persisted to Mongo,
        and resuming replays messages + continues from history.
        """
        task = self._background_tasks.get(chat_id)
        if not task:
            return False
        if task.done():
            return False

        # Best-effort: mark session as paused in the runtime registry.
        try:
            conn = self.connections.get(chat_id) or {}
            ws_id = conn.get("ws_id")
            if ws_id:
                # switch_workflow will mark the previous active chat paused; we also
                # want this chat paused if it was active.
                ctx = session_registry.get_workflow_by_chat_id(ws_id, chat_id)
                if ctx and getattr(ctx, "status", None) != "completed":
                    ctx.status = "paused"
        except Exception as _pause_exc:
            logger.debug("WORKFLOW_PAUSE_REGISTRY_FAILED chat=%s: %s", chat_id, _pause_exc)

        # Emit a lightweight runtime event for observability.
        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            if dispatcher:
                await dispatcher.emit(
                    "runtime.workflow_paused",
                    {"chat_id": chat_id, "reason": str(reason)},
                )
        except Exception as _emit_exc:
            logger.debug("WORKFLOW_PAUSED_EMIT_FAILED chat=%s: %s", chat_id, _emit_exc)

        task.cancel()
        return True

    # ==================================================================================
    # SIMPLIFIED CHAT MESSAGE API
    # ==================================================================================

    async def send_chat_message(
        self,
        message: str,
        agent_name: str | None = None,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """Send chat message to user interface."""
        # Create properly formatted event data with 'kind' field for envelope builder
        event_data = {
            "kind": "text",
            "agent": agent_name or "Agent",
            "content": str(message),
            "chat_id": chat_id,
            "timestamp": datetime.now(UTC).isoformat()
        }
        if metadata:
            event_data["metadata"] = metadata

        logger.debug("CHAT_MSG_SEND kind=%s agent=%s content_len=%s", event_data['kind'], agent_name, len(message))

        await self.send_event_to_ui(event_data, chat_id)
