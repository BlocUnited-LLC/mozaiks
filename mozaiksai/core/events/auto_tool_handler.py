# ==============================================================================
# FILE: auto_tool_handler.py
# DESCRIPTION: 
# ==============================================================================

# === MOZAIKS-CORE-HEADER ===

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from pydantic import ValidationError

from mozaiksai.core.workflow.agents.tools import load_agent_tool_functions
from mozaiksai.core.workflow.outputs.structured import get_structured_outputs_for_workflow
from mozaiksai.core.events.event_serialization import serialize_event_content
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow.context.adapter import create_context_container

logger = logging.getLogger("auto_tool_handler")


@dataclass(frozen=True)
class AutoToolBinding:
    """Represents the runtime contract for auto-invoked UI tools."""

    model_name: str
    agent_name: str
    tool_name: str
    function: Callable[..., Awaitable[Any] | Any]
    param_names: tuple[str, ...]
    accepts_context: bool
    ui_config: Dict[str, Any]
    model_cls: Any


class AutoToolEventHandler:
    """Handle chat.structured_output_ready events by running the mapped UI tool."""

    _CACHE_LIMIT = 512

    def __init__(self) -> None:
        self._workflow_bindings: Dict[str, Dict[str, AutoToolBinding]] = {}
        self._processed_keys: set[str] = set()
        self._processed_order: asyncio.Queue[str] = asyncio.Queue()

    async def handle_structured_output_ready(self, event: Dict[str, Any]) -> None:
        """Process a structured-output-ready event and trigger the corresponding tool."""

        try:
            logger.info("[AUTO_TOOL] Received structured_output_ready event: agent=%s turn=%s", event.get('agent_name') or event.get('agent'), event.get('turn_idempotency_key'))
            auto_mode = bool(event.get("auto_tool_mode"))
            if not auto_mode:
                logger.debug("[AUTO_TOOL] Event marked auto_tool_mode=false; ignoring")
                return
            agent_name = str(event["agent_name"])
            model_name = str(event["model_name"])
            structured_data = event.get("structured_data")
            context = event.get("context") or {}
            workflow_name = str(context.get("workflow_name"))
            chat_id = context.get("chat_id")
            turn_key = str(event.get("turn_idempotency_key") or "")
            # Extract pattern context reference if available (for write-back)
            pattern_context_ref = event.get("_pattern_context_ref")
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("[AUTO_TOOL] Malformed structured_output_ready event: %s", exc)
            return

        if not workflow_name:
            logger.warning("[AUTO_TOOL] Missing workflow_name for agent %s", agent_name)
            return
        if not chat_id or not turn_key:
            logger.debug(
                "[AUTO_TOOL] Skipping auto-tool (missing chat_id/turn key) agent=%s", agent_name
            )
            return
        logger.info("[AUTO_TOOL] Processing auto tool turn=%s for agent=%s workflow=%s", turn_key, agent_name, workflow_name)
        if not isinstance(structured_data, dict):
            logger.warning(
                "[AUTO_TOOL] Structured data for agent %s is not a dict (type=%s)",
                agent_name,
                type(structured_data).__name__,
            )
            return

        cache_key = f"{chat_id}:{turn_key}"
        if cache_key in self._processed_keys:
            logger.debug("[AUTO_TOOL] Duplicate turn detected -> skipping (key=%s)", cache_key)
            return

        binding = await self._resolve_binding(workflow_name, model_name, agent_name)
        if binding:
            logger.info("[AUTO_TOOL] Binding resolved for agent=%s tool=%s model=%s", agent_name, binding.tool_name, binding.model_name)
        if not binding:
            logger.warning(
                "[AUTO_TOOL] No tool binding for workflow=%s model=%s agent=%s",
                workflow_name,
                model_name,
                agent_name,
            )
            await self._register_turn(cache_key)
            return

        try:
            validated = binding.model_cls.model_validate(structured_data)
            normalized = validated.model_dump(mode='json')  # type: ignore[attr-defined] - Force JSON serialization for enums
        except ValidationError as err:
            logger.error(
                "[AUTO_TOOL] Structured data failed validation for model=%s agent=%s errors=%s",
                model_name,
                agent_name,
                err.errors(),
            )
            await self._register_turn(cache_key)
            return
        except Exception:  # pragma: no cover - unexpected
            logger.exception(
                "[AUTO_TOOL] Unexpected failure validating structured data model=%s agent=%s",
                model_name,
                agent_name,
            )
            await self._register_turn(cache_key)
            return

        kwargs = self._build_tool_kwargs(binding, normalized, {
            **context,
            "turn_idempotency_key": turn_key,
            "agent_name": agent_name,
        }, pattern_context_ref)
        logger.info("[AUTO_TOOL] Prepared kwargs for %s: %s", binding.tool_name, {k: v for k, v in kwargs.items() if k != 'context_variables'})
        await self._emit_tool_call(binding, agent_name, chat_id, kwargs, turn_key)
        result_payload, status = await self._invoke_tool(binding, kwargs)
        
        # Write back context changes to pattern context if available
        container = kwargs.get("context_variables")
        if pattern_context_ref and container and hasattr(container, "data"):
            try:
                # Copy changes from tool's container back to the shared pattern context
                for key, value in getattr(container, "data").items():
                    try:
                        pattern_context_ref.set(key, value)
                    except Exception:
                        pass
                logger.debug("[AUTO_TOOL] Wrote back %d context keys to pattern context after %s execution", len(getattr(container, "data")), binding.tool_name)
            except Exception as wb_err:
                logger.debug("[AUTO_TOOL] Failed to write back context changes to pattern: %s", wb_err)
        
        await self._emit_tool_result(binding, agent_name, chat_id, result_payload, status, turn_key)
        await self._register_turn(cache_key)

    async def _resolve_binding(
        self, workflow_name: str, model_name: str, agent_name: str
    ) -> Optional[AutoToolBinding]:
        bindings = await self._load_bindings_for_workflow(workflow_name)
        binding = bindings.get(model_name)
        if not binding:
            logger.debug("[AUTO_TOOL] No cached binding for workflow=%s model=%s agent=%s", workflow_name, model_name, agent_name)
            return None
        if binding.agent_name != agent_name:
            logger.debug(
                "[AUTO_TOOL] Binding agent mismatch (expected=%s, actual=%s) for model=%s",
                binding.agent_name,
                agent_name,
                model_name,
            )
        return binding

    async def _load_bindings_for_workflow(self, workflow_name: str) -> Dict[str, AutoToolBinding]:
        cached = self._workflow_bindings.get(workflow_name)
        if cached is not None:
            logger.debug("[AUTO_TOOL] Returning cached bindings for workflow=%s (count=%d)", workflow_name, len(cached))
            return cached

        mapping: Dict[str, AutoToolBinding] = {}
        try:
            registry = get_structured_outputs_for_workflow(workflow_name)
            logger.debug("[AUTO_TOOL] Loaded structured outputs registry for workflow=%s: %s", workflow_name, list(registry.keys()))
        except Exception as err:
            logger.debug(
                "[AUTO_TOOL] Structured outputs unavailable for workflow %s: %s",
                workflow_name,
                err,
            )
            registry = {}

        if not registry:
            logger.warning("[AUTO_TOOL] Empty registry for workflow=%s - no bindings possible", workflow_name)
            self._workflow_bindings[workflow_name] = mapping
            return mapping

        tool_functions = load_agent_tool_functions(workflow_name)
        logger.debug("[AUTO_TOOL] Loaded tool functions for workflow=%s: agents=%s", workflow_name, list(tool_functions.keys()))
        agent_function_index: Dict[str, Dict[str, Callable[..., Any]]] = {}
        for agent, funcs in tool_functions.items():
            agent_function_index[agent] = {
                getattr(fn, "__name__", f"fn_{idx}"): fn for idx, fn in enumerate(funcs)
            }
            logger.debug("[AUTO_TOOL] Agent %s has functions: %s", agent, list(agent_function_index[agent].keys()))

        tools_path = Path("workflows") / workflow_name / "tools.json"
        try:
            tools_data = json.loads(tools_path.read_text(encoding="utf-8")) if tools_path.exists() else {}
        except Exception as err:
            logger.warning(
                "[AUTO_TOOL] Failed parsing tools.json for workflow %s: %s",
                workflow_name,
                err,
            )
            tools_data = {}
        entries = tools_data.get("tools") or []
        if not isinstance(entries, list):
            entries = []
        
        logger.debug("[AUTO_TOOL] Processing %d tool entries for workflow=%s", len(entries), workflow_name)

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            tool_type_raw = entry.get("tool_type") or entry.get("type")
            tool_type = str(tool_type_raw).upper() if tool_type_raw else ""
            auto_invoke_flag = entry.get("auto_invoke")
            if auto_invoke_flag is None:
                should_auto_invoke = tool_type == "UI_TOOL"
            else:
                try:
                    should_auto_invoke = bool(auto_invoke_flag)
                except Exception:
                    should_auto_invoke = False
            if not should_auto_invoke:
                logger.debug("[AUTO_TOOL] Skipping entry (auto_invoke=False): function=%s agent=%s", entry.get("function"), entry.get("agent"))
                continue
            function_name = entry.get("function")
            if not isinstance(function_name, str) or not function_name:
                continue
            agent_field = entry.get("agent") or entry.get("caller")
            if isinstance(agent_field, (list, tuple)):
                agents = [a for a in agent_field if isinstance(a, str)]
            elif isinstance(agent_field, str):
                agents = [agent_field]
            else:
                agents = []
            logger.debug("[AUTO_TOOL] Processing auto_invoke tool: function=%s agents=%s", function_name, agents)
            for agent_name in agents:
                model_cls = registry.get(agent_name)
                if model_cls is None:
                    logger.debug("[AUTO_TOOL] No structured output model for agent=%s (skipping binding)", agent_name)
                    continue
                model_name = getattr(model_cls, "__name__", str(model_cls))
                logger.debug("[AUTO_TOOL] Agent %s has model_name=%s", agent_name, model_name)
                fn_lookup = agent_function_index.get(agent_name, {})
                func = fn_lookup.get(function_name)
                if not func:
                    logger.debug(
                        "[AUTO_TOOL] Tool function '%s' not loaded for agent %s",
                        function_name,
                        agent_name,
                    )
                    continue
                logger.debug("[AUTO_TOOL] Found function %s for agent %s", function_name, agent_name)
                sig = inspect.signature(func)
                param_names = [
                    name
                    for name, param in sig.parameters.items()
                    if param.kind in (
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    )
                    and name not in {"self"}
                ]
                accepts_context = "context_variables" in sig.parameters
                ui_cfg = entry.get("ui") if isinstance(entry.get("ui"), dict) else {}
                binding = AutoToolBinding(
                    model_name=model_name,
                    agent_name=agent_name,
                    tool_name=entry.get("name") or function_name,
                    function=func,
                    param_names=tuple(param_names),
                    accepts_context=accepts_context,
                    ui_config=ui_cfg,
                    model_cls=model_cls,
                )
                mapping[model_name] = binding
                logger.info("[AUTO_TOOL] ✅ Created binding: model=%s agent=%s tool=%s", model_name, agent_name, binding.tool_name)

        logger.info("[AUTO_TOOL] Loaded %d total bindings for workflow=%s: %s", len(mapping), workflow_name, list(mapping.keys()))
        self._workflow_bindings[workflow_name] = mapping
        return mapping

    def _build_tool_kwargs(
        self,
        binding: AutoToolBinding,
        normalized_payload: Dict[str, Any],
        context: Dict[str, Any],
        pattern_context_ref: Any = None,
    ) -> Dict[str, Any]:
        def _normalize_key(raw: str | None) -> str:
            if not raw:
                return ""
            # Canonicalize keys so `PhaseAgents`, `phase_agents`, and `phaseAgents` resolve to the same parameter.
            return "".join(ch.lower() for ch in raw if ch.isalnum())

        param_lookup = {_normalize_key(name): name for name in binding.param_names}
        kwargs: Dict[str, Any] = {}
        for key, value in normalized_payload.items():
            matched = param_lookup.get(_normalize_key(str(key)))
            if matched:
                kwargs[matched] = value
        # Provide contextual metadata when the tool function explicitly accepts it.
        context_fallbacks = {
            "chat_id": context.get("chat_id"),
            "app_id": context.get("app_id"),
            "workflow_name": context.get("workflow_name"),
            "turn_idempotency_key": context.get("turn_idempotency_key"),
            "agent_name": context.get("agent_name"),
            "agent": context.get("agent_name") or context.get("agent"),
        }
        for key, value in context_fallbacks.items():
            if value is None:
                continue
            matched = param_lookup.get(_normalize_key(key))
            if matched and matched not in kwargs:
                kwargs[matched] = value
        if binding.accepts_context:
            # Prefer using the pattern's actual context reference if available
            if pattern_context_ref and hasattr(pattern_context_ref, "get") and hasattr(pattern_context_ref, "set"):
                kwargs["context_variables"] = pattern_context_ref
                logger.debug("[AUTO_TOOL] Using live pattern context reference for %s", binding.tool_name)
            else:
                # Fallback: create ephemeral container from snapshot
                snapshot = context.get("context_variables") if isinstance(context.get("context_variables"), dict) else None
                container = create_context_container(snapshot)
                for key in ("chat_id", "app_id", "workflow_name", "turn_idempotency_key", "agent_name"):
                    value = context.get(key)
                    if value is not None:
                        try:
                            container.set(key, value)
                        except Exception:
                            pass
                kwargs["context_variables"] = container
        return kwargs

    async def _invoke_tool(
        self, binding: AutoToolBinding, kwargs: Dict[str, Any]
    ) -> tuple[Any, str]:
        try:
            result = binding.function(**kwargs)
            if inspect.isawaitable(result):
                result = await result  # type: ignore[assignment]
            return result, "ok"
        except Exception as exc:
            logger.exception(
                "[AUTO_TOOL] Tool execution failed for agent=%s tool=%s",
                binding.agent_name,
                binding.tool_name,
            )
            return {"status": "error", "message": str(exc)}, "error"

    async def _emit_tool_call(
        self,
        binding: AutoToolBinding,
        agent_name: str,
        chat_id: Optional[str],
        kwargs: Dict[str, Any],
        turn_key: str,
    ) -> None:
        if not chat_id:
            return
        try:
            transport = await SimpleTransport.get_instance()
        except Exception as exc:  # pragma: no cover
            logger.debug("[AUTO_TOOL] Transport unavailable for tool_call: %s", exc)
            return
        if not transport:
            return
        arg_payload = {k: serialize_event_content(v) for k, v in kwargs.items() if k != "context_variables"}
        try:
            await transport.send_event_to_ui(
                {
                    "kind": "select_speaker",
                    "agent": agent_name,
                    "selected_speaker": agent_name,
                },
                chat_id,
            )
        except Exception:
            logger.debug("[AUTO_TOOL] Failed to emit select_speaker for agent=%s", agent_name)
        event_payload = {
            "kind": "tool_call",
            "agent": agent_name,
            "tool_name": binding.tool_name,
            "tool_call_id": turn_key,
            "corr": turn_key,
            "awaiting_response": False,
            "component_type": binding.ui_config.get("component"),
            "payload": {
                "tool_args": arg_payload,
                "agent_name": agent_name,
                "interaction_type": "auto_tool",
                "workflow_name": binding.ui_config.get("workflow_name"),
            },
        }
        agent_message = kwargs.get("agent_message")
        if isinstance(agent_message, str) and agent_message.strip():
            event_payload["payload"]["agent_message"] = agent_message.strip()
        try:
            logger.info("[AUTO_TOOL] Emitting chat.tool_call for agent=%s tool=%s turn=%s", agent_name, binding.tool_name, turn_key)
            await transport.send_event_to_ui(event_payload, chat_id)
        except Exception:
            logger.debug("[AUTO_TOOL] Failed to emit tool_call for agent=%s", agent_name)

    async def _emit_tool_result(
        self,
        binding: AutoToolBinding,
        agent_name: str,
        chat_id: Optional[str],
        result: Any,
        status: str,
        turn_key: str,
    ) -> None:
        if not chat_id:
            return
        try:
            transport = await SimpleTransport.get_instance()
        except Exception as exc:  # pragma: no cover
            logger.debug("[AUTO_TOOL] Transport unavailable for tool_result: %s", exc)
            return
        if not transport:
            return
        success = status == "ok" and not (isinstance(result, dict) and str(result.get('status', '')).lower() in {"error", "failed"})
        serialized_payload = serialize_event_content(result)
        message = None
        if isinstance(result, dict):
            message = result.get('message') or result.get('agent_message') or result.get('status')
        if isinstance(message, str) and message.strip():
            content_summary = message.strip()
        elif success:
            content_summary = f"Tool {binding.tool_name} completed successfully."
        else:
            content_summary = f"Tool {binding.tool_name} reported status {status}."

        payload = {
            "kind": "tool_response",
            "agent": agent_name,
            "tool_name": binding.tool_name,
            "call_id": turn_key,
            "corr": turn_key,
            "status": status,
            "success": success,
            "content": content_summary,
            "payload": serialized_payload,
            "interaction_type": "auto_tool",  # Mark as auto-tool for frontend filtering
        }
        try:
            logger.info("[AUTO_TOOL] Emitting chat.tool_response for agent=%s tool=%s status=%s turn=%s", agent_name, binding.tool_name, status, turn_key)
            await transport.send_event_to_ui(payload, chat_id)
        except Exception:
            logger.debug("[AUTO_TOOL] Failed to emit tool_result for agent=%s", agent_name)

    async def _register_turn(self, cache_key: str) -> None:
        if cache_key in self._processed_keys:
            return
        self._processed_keys.add(cache_key)
        try:
            self._processed_order.put_nowait(cache_key)
        except asyncio.QueueFull:  # pragma: no cover
            pass
        while len(self._processed_keys) > self._CACHE_LIMIT:
            try:
                oldest = self._processed_order.get_nowait()
                self._processed_keys.discard(oldest)
            except asyncio.QueueEmpty:
                break


__all__ = ["AutoToolEventHandler", "AutoToolBinding"]

