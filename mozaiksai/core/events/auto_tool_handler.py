# ==============================================================================
# FILE: mozaiksai/core/events/auto_tool_handler.py
# DESCRIPTION: Handles structured output events by auto-invoking mapped UI tools with agent context.
# ==============================================================================



from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.events.event_serialization import serialize_event_content
from mozaiksai.core.workflow.agents.tools import load_agent_tool_functions
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    DETERMINISTIC_TOOL_WRITER,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import (
    StructuredOutputOverlay,
)
from mozaiksai.core.workflow.declarative import parse_tools_config
from mozaiksai.core.workflow.outputs.structured import get_structured_outputs_for_workflow
from mozaiksai.core.workflow.workflow_manager import workflow_manager

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport

logger = logging.getLogger("auto_tool_handler")


async def _get_simple_transport() -> SimpleTransport | None:
    """Resolve SimpleTransport lazily to avoid transport<->events import cycles."""
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    return await SimpleTransport.get_instance()


@dataclass(frozen=True)
class AutoToolBinding:
    """Represents the runtime contract for auto-invoked UI tools.

    This is the per-dispatch EXECUTION binding: its ``function`` is resolved
    fresh through the canonical workflow tool loader for each dispatch and is
    never cached across dispatches.
    """

    model_name: str
    agent_name: str
    tool_name: str
    function: Callable[..., Awaitable[Any] | Any]
    param_names: tuple[str, ...]
    accepts_context: bool
    ui_config: dict[str, Any]
    model_cls: Any


@dataclass(frozen=True)
class AutoToolBindingDescriptor:
    """Cached DECLARATIVE auto-tool binding metadata.

    Python callable authority is never cached: descriptors carry only the
    declarative facts (workflow tool declaration + exact model identity), and
    the executable function comes from the current canonical loader/runtime
    namespace at dispatch time.
    """

    model_name: str
    agent_name: str
    tool_name: str
    function_name: str
    ui_config: dict[str, Any]
    model_cls: Any


#: Per-binding execution checkpoint states. "PENDING" is the absence of a
#: record. Once a binding reaches TOOL_TERMINAL, that binding must never be
#: invoked again for the same turn; COMPLETE means post-processing finished.
_TOOL_TERMINAL = "tool_terminal"
_COMPLETE = "complete"


@dataclass
class _BindingExecutionRecord:
    """Process-local finite execution checkpoint for one binding of one turn.

    The record is captured synchronously after the tool returns a truthful
    terminal result — before the next cancellable await — and holds detached
    facts only (no live mutable aliases), so a retry resumes post-processing
    without re-running the tool.

    This guarantee is PROCESS-LOCAL runtime idempotency, not distributed
    exactly-once delivery: a process crash after an external non-idempotent
    tool side effect but before durable recording cannot be solved by this
    in-memory handler. Tools requiring cross-process exactly-once semantics
    must use their owning service's idempotency contract.
    """

    status: str
    result_payload: Any
    result_status: str
    context_snapshot: dict[str, Any] | None
    tool_call_emitted: bool = False
    writeback_done: bool = False
    persisted: bool = False
    result_emitted: bool = False


class AutoToolEventHandler:
    """Handle runtime.agent_output_validated events by running the mapped UI tool."""

    _CACHE_LIMIT = 512

    def __init__(self) -> None:
        # Self-validating DECLARATIVE binding cache: each entry pairs built
        # binding descriptors with the declarative authority fingerprint they
        # were built from (exact model identity + tools.yaml declaration). A
        # cached entry is reusable only while those inputs still match; the
        # executable callable is never part of the cache.
        self._workflow_binding_descriptors: dict[
            str, tuple[tuple[Any, ...], dict[str, list[AutoToolBindingDescriptor]]]
        ] = {}
        self._processed_keys: set[str] = set()
        self._processed_order: asyncio.Queue[str] = asyncio.Queue()
        # Turns claimed atomically before the first await: a duplicate
        # delivery observing an IN_FLIGHT (or COMPLETED) turn never executes.
        self._in_flight_keys: set[str] = set()
        # Per-turn, per-binding execution checkpoints (process-local). Popped
        # when a turn completes; bounded by _CACHE_LIMIT with active in-flight
        # state never evicted.
        self._turn_checkpoints: dict[str, dict[str, _BindingExecutionRecord]] = {}

    async def handle_tool_dispatch(self, event: dict[str, Any]) -> None:
        """Process an agent_output_validated event and trigger the corresponding tool."""

        try:
            logger.debug("[AUTO_TOOL] Received agent_output_validated event: agent=%s turn=%s", event.get('agent_name') or event.get('agent'), event.get('turn_idempotency_key'))
            auto_tool_call_enabled = bool(event.get("auto_tool_call"))
            if not auto_tool_call_enabled:
                logger.debug("[AUTO_TOOL] Event marked auto_tool_call=false; ignoring")
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
            logger.warning("[AUTO_TOOL] Malformed agent_output_validated event: %s", exc)
            return

        if not workflow_name:
            logger.warning("[AUTO_TOOL] Missing workflow_name for agent %s", agent_name)
            return
        if not chat_id or not turn_key:
            logger.debug(
                "[AUTO_TOOL] Skipping auto-tool (missing chat_id/turn key) agent=%s", agent_name
            )
            return
        logger.debug("[AUTO_TOOL] Processing auto tool turn=%s for agent=%s workflow=%s", turn_key, agent_name, workflow_name)
        if not isinstance(structured_data, dict):
            logger.warning(
                "[AUTO_TOOL] Structured data for agent %s is not a dict (type=%s)",
                agent_name,
                type(structured_data).__name__,
            )
            return

        cache_key = f"{chat_id}:{turn_key}"
        # Atomic in-flight claim: check-and-add with no await in between is
        # atomic within one event loop, and all handler access is loop-local.
        # A concurrent duplicate delivery of the same turn observes IN_FLIGHT
        # (or COMPLETED) and never executes the tool.
        if cache_key in self._processed_keys or cache_key in self._in_flight_keys:
            logger.debug("[AUTO_TOOL] Duplicate turn detected -> skipping (key=%s)", cache_key)
            return
        self._in_flight_keys.add(cache_key)
        try:
            await self._dispatch_claimed_turn(
                cache_key=cache_key,
                workflow_name=workflow_name,
                model_name=model_name,
                agent_name=agent_name,
                structured_data=structured_data,
                context=context,
                chat_id=chat_id,
                turn_key=turn_key,
                pattern_context_ref=pattern_context_ref,
            )
        finally:
            # An unexpected interruption (cancellation, unforeseen exception)
            # before a truthful terminal result releases the claim so a
            # legitimate retry can execute. Terminal outcomes have already
            # registered COMPLETED via _register_turn by this point.
            self._in_flight_keys.discard(cache_key)

    async def _dispatch_claimed_turn(
        self,
        *,
        cache_key: str,
        workflow_name: str,
        model_name: str,
        agent_name: str,
        structured_data: dict[str, Any],
        context: dict[str, Any],
        chat_id: Any,
        turn_key: str,
        pattern_context_ref: Any,
    ) -> None:
        bindings = await self._resolve_bindings(workflow_name, model_name, agent_name)
        if not bindings:
            logger.warning(
                "[AUTO_TOOL] No tool binding for workflow=%s model=%s agent=%s",
                workflow_name,
                model_name,
                agent_name,
            )
            await self._register_turn(cache_key)
            return

        try:
            validated = bindings[0].model_cls.model_validate(structured_data)
            # ONE untouched canonical detached payload represents the accepted
            # event result. It is never handed to a tool by mutable reference.
            canonical_payload = detach(validated.model_dump(mode='json'))  # type: ignore[attr-defined] - Force JSON serialization for enums
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

        # Per-turn execution checkpoints: a retry after cancellation resumes
        # at the first unfinished binding/stage; a binding whose tool already
        # returned a terminal result is NEVER invoked again for this turn.
        self._trim_turn_checkpoints()
        checkpoints = self._turn_checkpoints.setdefault(cache_key, {})

        for index, binding in enumerate(bindings):
            binding_key = f"{index}:{binding.agent_name}:{binding.tool_name}"
            record = checkpoints.get(binding_key)
            if record is not None and record.status == _COMPLETE:
                continue

            if record is None:
                # PENDING: execute the tool. Pre-execution interruption keeps
                # the binding PENDING, so a retry may execute it.
                logger.debug("[AUTO_TOOL] Binding resolved for agent=%s tool=%s model=%s", agent_name, binding.tool_name, binding.model_name)
                # Each binding gets its own detached copy: a tool mutating an
                # explicit nested argument can never contaminate the canonical
                # payload, another binding's arguments, or another binding's
                # structured_output overlay.
                binding_payload = detach(canonical_payload)
                kwargs = self._build_tool_kwargs(binding, binding_payload, {
                    **context,
                    "turn_idempotency_key": turn_key,
                    "agent_name": agent_name,
                }, pattern_context_ref)
                logger.debug("[AUTO_TOOL] Prepared kwargs for %s: %s", binding.tool_name, {k: v for k, v in kwargs.items() if k != 'context_variables'})
                await self._emit_tool_call(binding, agent_name, chat_id, kwargs, turn_key)
                result_payload, status = await self._invoke_tool(binding, kwargs)
                # TOOL_TERMINAL: recorded SYNCHRONOUSLY before the next
                # cancellable await, with detached facts only. A truthful tool
                # failure is also a terminal execution result for this turn.
                record = _BindingExecutionRecord(
                    status=_TOOL_TERMINAL,
                    result_payload=self._detached_or_original(result_payload),
                    result_status=status,
                    context_snapshot=self._container_snapshot(kwargs.get("context_variables")),
                    tool_call_emitted=True,
                )
                checkpoints[binding_key] = record

            # TOOL_TERMINAL: finish post-processing stages exactly once each.
            if not record.writeback_done:
                # Synchronous write-back from the detached terminal snapshot.
                if pattern_context_ref and record.context_snapshot:
                    self._write_back_context(
                        pattern_context_ref, record.context_snapshot, binding.tool_name
                    )
                record.writeback_done = True

            if not record.persisted:
                await self._persist_context_variables(
                    chat_id=chat_id,
                    app_id=context.get("app_id"),
                    workflow_name=workflow_name,
                    context_variables=record.context_snapshot,
                )
                record.persisted = True

            if not record.result_emitted:
                await self._emit_tool_result(
                    binding, agent_name, chat_id, record.result_payload, record.result_status, turn_key
                )
                record.result_emitted = True

            record.status = _COMPLETE
        await self._register_turn(cache_key)

    @staticmethod
    def _detached_or_original(value: Any) -> Any:
        try:
            return detach(value)
        except Exception:  # pragma: no cover - non-copyable tool result
            logger.debug("[AUTO_TOOL] Tool result could not be detached; storing original")
            return value

    @staticmethod
    def _container_snapshot(container: Any) -> dict[str, Any] | None:
        """Detached context facts captured at the terminal-record boundary."""
        if container is None:
            return None
        try:
            for method_name in ("snapshot", "to_dict"):
                method = getattr(container, method_name, None)
                if callable(method):
                    data = method()
                    if isinstance(data, dict):
                        return data
            if isinstance(container, dict):
                return dict(container)
        except Exception as snap_err:  # pragma: no cover - defensive
            logger.debug("[AUTO_TOOL] Context snapshot capture failed: %s", snap_err)
        return None

    @staticmethod
    def _write_back_context(
        pattern_context_ref: Any, snapshot: dict[str, Any], tool_name: str
    ) -> None:
        try:
            for key, value in snapshot.items():
                try:
                    pattern_context_ref.set(key, value)
                except Exception as _set_err:
                    logger.debug("[AUTO_TOOL] Context write-back failed key=%s: %s", key, _set_err)
            logger.debug(
                "[AUTO_TOOL] Wrote back %d context keys to pattern context after %s execution",
                len(snapshot),
                tool_name,
            )
        except Exception as wb_err:
            logger.debug("[AUTO_TOOL] Failed to write back context changes to pattern: %s", wb_err)

    def _trim_turn_checkpoints(self) -> None:
        """Bounded checkpoint bookkeeping: never evict active in-flight state."""
        if len(self._turn_checkpoints) <= self._CACHE_LIMIT:
            return
        for key in list(self._turn_checkpoints):
            if len(self._turn_checkpoints) <= self._CACHE_LIMIT:
                break
            if key in self._in_flight_keys:
                continue
            self._turn_checkpoints.pop(key, None)

    async def _resolve_bindings(
        self, workflow_name: str, model_name: str, agent_name: str
    ) -> list[AutoToolBinding]:
        descriptors = await self._load_binding_descriptors(workflow_name)
        candidates = [
            descriptor
            for descriptor in descriptors.get(model_name) or []
            if descriptor.agent_name == agent_name
        ]
        if not candidates:
            logger.debug("[AUTO_TOOL] No cached binding for workflow=%s model=%s agent=%s", workflow_name, model_name, agent_name)
            return []
        # Python callable authority is never cached across dispatches: the
        # executable function is resolved fresh through the canonical
        # workflow tool loader so workflow-owned source/helper changes are
        # observed without a process restart. (External installed packages
        # remain a restart boundary.)
        tool_functions = load_agent_tool_functions(workflow_name, include_auto_only=True)
        function_index: dict[str, dict[str, Callable[..., Any]]] = {}
        for agent, funcs in tool_functions.items():
            function_index[agent] = {
                getattr(fn, "__name__", f"fn_{idx}"): fn for idx, fn in enumerate(funcs)
            }
        bindings: list[AutoToolBinding] = []
        for descriptor in candidates:
            func = function_index.get(descriptor.agent_name, {}).get(descriptor.function_name)
            if func is None:
                logger.warning(
                    "[AUTO_TOOL] Tool function '%s' not currently loadable for agent %s",
                    descriptor.function_name,
                    descriptor.agent_name,
                )
                continue
            sig = inspect.signature(func)
            param_names = tuple(
                name
                for name, param in sig.parameters.items()
                if param.kind in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
                and name not in {"self"}
            )
            bindings.append(
                AutoToolBinding(
                    model_name=descriptor.model_name,
                    agent_name=descriptor.agent_name,
                    tool_name=descriptor.tool_name,
                    function=func,
                    param_names=param_names,
                    accepts_context="context_variables" in sig.parameters,
                    ui_config=descriptor.ui_config,
                    model_cls=descriptor.model_cls,
                )
            )
        return bindings

    def _resolve_registry(self, workflow_name: str) -> tuple[dict[str, Any], bool]:
        """Resolve the current structured-output registry authority.

        Returns ``(registry, resolved)``. ``resolved=False`` means the current
        configuration could not be loaded (unloaded workflow, failed reload):
        a cached binding must never be reused on top of that failure, and the
        empty result must not be cached as authority either.
        """
        try:
            registry = get_structured_outputs_for_workflow(workflow_name)
            logger.debug(
                "[AUTO_TOOL] Loaded structured outputs registry for workflow=%s: %s",
                workflow_name,
                list(registry.keys()),
            )
            return dict(registry), True
        except Exception as err:
            logger.debug(
                "[AUTO_TOOL] Structured outputs unavailable for workflow %s: %s",
                workflow_name,
                err,
            )
            return {}, False

    @staticmethod
    def _file_digest(path: Any) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _binding_authority_fingerprint(
        self, workflow_name: str, registry: dict[str, Any]
    ) -> tuple[Any, ...]:
        """Declarative authority inputs that decide descriptor-cache reuse.

        Covers the exact structured-output model class identity per agent and
        the workflow's tools.yaml declaration bytes. Callable behavior is
        deliberately NOT fingerprinted here — file bytes cannot prove full
        callable behavior (imported helpers change behavior without touching
        the tool file), so the executable function is resolved fresh through
        the canonical loader at every dispatch instead of being cached.
        """
        registry_identity = tuple(
            sorted(
                (agent, getattr(model_cls, "__name__", str(model_cls)), id(model_cls))
                for agent, model_cls in registry.items()
            )
        )
        workflow_path = workflow_manager.resolve_workflow_path(workflow_name)
        tools_digest: str | None = None
        if workflow_path is not None:
            tools_digest = self._file_digest(workflow_path / "tools.yaml")
        return (
            str(workflow_path or ""),
            registry_identity,
            tools_digest,
        )

    async def _load_binding_descriptors(
        self, workflow_name: str
    ) -> dict[str, list[AutoToolBindingDescriptor]]:
        # Self-validating DECLARATIVE cache: resolve the current authority
        # BEFORE any cached descriptors are reused. Cached descriptors are
        # reusable only while the declarative fingerprint (exact model
        # identity + tools.yaml declaration) still matches; the executable
        # callable is never cached here.
        registry, registry_resolved = self._resolve_registry(workflow_name)
        fingerprint = self._binding_authority_fingerprint(workflow_name, registry)
        cached = self._workflow_binding_descriptors.get(workflow_name)
        if cached is not None:
            cached_fingerprint, cached_mapping = cached
            if registry_resolved and cached_fingerprint == fingerprint:
                logger.debug(
                    "[AUTO_TOOL] Returning cached binding descriptors for workflow=%s (count=%d)",
                    workflow_name,
                    len(cached_mapping),
                )
                return cached_mapping
            logger.debug(
                "[AUTO_TOOL] Binding authority changed for workflow=%s -> rebuilding",
                workflow_name,
            )
            self._workflow_binding_descriptors.pop(workflow_name, None)

        mapping: dict[str, list[AutoToolBindingDescriptor]] = {}
        if not registry:
            logger.warning("[AUTO_TOOL] Empty registry for workflow=%s - no bindings possible", workflow_name)
            if registry_resolved:
                self._workflow_binding_descriptors[workflow_name] = (fingerprint, mapping)
            return mapping

        workflow_path = workflow_manager.resolve_workflow_path(workflow_name)
        if workflow_path is None:
            logger.warning("[AUTO_TOOL] Workflow path not found for workflow=%s", workflow_name)
            self._workflow_binding_descriptors[workflow_name] = (fingerprint, mapping)
            return mapping
        tools_yaml_path = workflow_path / "tools.yaml"
        tools_data: dict[str, Any] = {}

        if tools_yaml_path.exists():
            try:
                raw = yaml.safe_load(tools_yaml_path.read_text(encoding="utf-8")) or {}
                tools_data = parse_tools_config(raw)
            except Exception as err:
                logger.warning(
                    "[AUTO_TOOL] Failed parsing tools.yaml for workflow %s: %s",
                    workflow_name,
                    err,
                )
        entries = tools_data.get("tools") or []
        if not isinstance(entries, list):
            entries = []
        
        logger.debug("[AUTO_TOOL] Processing %d tool entries for workflow=%s", len(entries), workflow_name)

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            tool_type_raw = entry.get("tool_type") or entry.get("type")
            tool_type = str(tool_type_raw).upper() if tool_type_raw else ""
            auto_tool_call_flag = entry.get("auto_tool_call")
            if auto_tool_call_flag is None:
                should_auto_tool_call = tool_type in {"UI_TOOL", "UI_SURFACE"}
            else:
                try:
                    should_auto_tool_call = bool(auto_tool_call_flag)
                except Exception:
                    should_auto_tool_call = False
            if not should_auto_tool_call:
                logger.debug("[AUTO_TOOL] Skipping entry (auto_tool_call=False): function=%s agent=%s", entry.get("function"), entry.get("agent"))
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
            logger.debug("[AUTO_TOOL] Processing auto_tool_call tool: function=%s agents=%s", function_name, agents)
            for agent_name in agents:
                model_cls = registry.get(agent_name)
                if model_cls is None:
                    logger.debug("[AUTO_TOOL] No structured output model for agent=%s (skipping binding)", agent_name)
                    continue
                model_name = getattr(model_cls, "__name__", str(model_cls))
                logger.debug("[AUTO_TOOL] Agent %s has model_name=%s", agent_name, model_name)
                ui_cfg = entry.get("ui") if isinstance(entry.get("ui"), dict) else {}
                descriptor = AutoToolBindingDescriptor(
                    model_name=model_name,
                    agent_name=agent_name,
                    tool_name=entry.get("name") or function_name,
                    function_name=function_name,
                    ui_config=ui_cfg,
                    model_cls=model_cls,
                )
                mapping.setdefault(model_name, []).append(descriptor)
                logger.debug("AUTO_TOOL_BINDING_CREATED model=%s agent=%s tool=%s", model_name, agent_name, descriptor.tool_name)

        logger.debug("[AUTO_TOOL] Loaded %d total binding descriptors for workflow=%s: %s", len(mapping), workflow_name, list(mapping.keys()))
        self._workflow_binding_descriptors[workflow_name] = (fingerprint, mapping)
        return mapping

    def _build_tool_kwargs(
        self,
        binding: AutoToolBinding,
        normalized_payload: dict[str, Any],
        context: dict[str, Any],
        pattern_context_ref: Any = None,
    ) -> dict[str, Any]:
        def _normalize_key(raw: str | None) -> str:
            if not raw:
                return ""
            # Canonicalize keys so `PhaseAgents`, `phase_agents`, and `phaseAgents` resolve to the same parameter.
            return "".join(ch.lower() for ch in raw if ch.isalnum())

        param_lookup = {_normalize_key(name): name for name in binding.param_names}
        kwargs: dict[str, Any] = {}
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
            # The documented auto-tool contract is
            # context_variables.get("structured_output") -> the exact validated
            # structured_data for THIS turn. The runtime satisfies it with a
            # transient read-only overlay over the live context: no
            # application-declared variable is required, the key is never
            # written into pattern/workflow state, and snapshots/persistence
            # never include it. All other keys keep ordinary context behavior.
            # Prefer using the pattern's actual context reference if available
            if pattern_context_ref and hasattr(pattern_context_ref, "get") and hasattr(pattern_context_ref, "set"):
                kwargs["context_variables"] = StructuredOutputOverlay(
                    pattern_context_ref, normalized_payload
                )
                logger.debug("[AUTO_TOOL] Using live pattern context reference for %s", binding.tool_name)
            else:
                # Fallback: create ephemeral container from snapshot
                snapshot = context.get("context_variables") if isinstance(context.get("context_variables"), dict) else None
                authority_policy = None
                workflow_name = context.get("workflow_name")
                if workflow_name:
                    try:
                        workflow_config = workflow_manager.get_config(str(workflow_name)) or {}
                        authority_policy = build_context_authority_policy(
                            workflow_name=str(workflow_name),
                            definitions=(workflow_config.get("context_variables") or {}).get("definitions") or {},
                            transition_rules=(workflow_config.get("transition_graph") or {}).get("transition_rules") or [],
                        )
                    except Exception as policy_err:
                        logger.debug("[AUTO_TOOL] Context authority policy unavailable for %s: %s", workflow_name, policy_err)
                container = create_context_container(
                    snapshot,
                    chat_id=context.get("chat_id"),
                    app_id=context.get("app_id"),
                    authority_policy=authority_policy,
                    writer_id=DETERMINISTIC_TOOL_WRITER,
                )
                for key in ("chat_id", "app_id", "workflow_name", "turn_idempotency_key", "agent_name"):
                    value = context.get(key)
                    if value is not None:
                        try:
                            container.set(key, value)
                        except Exception as _cs_err:
                            logger.debug("[AUTO_TOOL] Container seed failed key=%s: %s", key, _cs_err)
                kwargs["context_variables"] = StructuredOutputOverlay(
                    container, normalized_payload
                )
        return kwargs

    async def _invoke_tool(
        self, binding: AutoToolBinding, kwargs: dict[str, Any]
    ) -> tuple[Any, str]:
        try:
            result = binding.function(**kwargs)
            if inspect.isawaitable(result):
                result = await result  # type: ignore[assignment]
            return result, "ok"
        except Exception:
            logger.exception(
                "[AUTO_TOOL] Tool execution failed for agent=%s tool=%s",
                binding.agent_name,
                binding.tool_name,
            )
            return {"status": "error", "message": "tool_execution_failed"}, "error"

    async def _persist_context_variables(
        self,
        *,
        chat_id: str | None,
        app_id: str | None,
        workflow_name: str | None,
        context_variables: Any,
    ) -> None:
        if not chat_id or not app_id or context_variables is None:
            return

        snapshot: dict[str, Any] | None = None
        if isinstance(context_variables, dict):
            snapshot = context_variables
        elif hasattr(context_variables, "snapshot") and callable(getattr(context_variables, "snapshot", None)):
            data = context_variables.snapshot()
            if isinstance(data, dict):
                snapshot = data
        elif hasattr(context_variables, "to_dict") and callable(getattr(context_variables, "to_dict", None)):
            data = context_variables.to_dict()
            if isinstance(data, dict):
                snapshot = data
        else:
            snapshot = None

        if not isinstance(snapshot, dict) or not snapshot:
            return

        try:
            pm = AG2PersistenceManager()
            await pm.persist_context_variables(
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name,
                variables=snapshot,
            )
        except Exception as exc:
            logger.debug("[AUTO_TOOL] Failed to persist context variables for chat=%s: %s", chat_id, exc)

    async def _emit_tool_call(
        self,
        binding: AutoToolBinding,
        agent_name: str,
        chat_id: str | None,
        kwargs: dict[str, Any],
        turn_key: str,
    ) -> None:
        if not chat_id:
            return
        try:
            transport = await _get_simple_transport()
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
        display_mode = binding.ui_config.get("mode") or "inline"
        workflow_name = binding.ui_config.get("workflow_name")
        event_payload = {
            "kind": "tool_call",
            "agent": agent_name,
            "tool_name": binding.tool_name,
            "tool_call_id": turn_key,
            "corr": turn_key,
            "awaiting_response": False,
            "component_type": binding.ui_config.get("component"),
            "workflow_name": workflow_name,
            "interaction_type": "auto_tool",
            "display": display_mode,
            "display_type": display_mode,
            "payload": {
                "tool_args": arg_payload,
                "agent_name": agent_name,
                "interaction_type": "auto_tool",
                "workflow_name": workflow_name,
                "display": display_mode,
                "mode": display_mode,
            },
        }
        agent_message = kwargs.get("agent_message")
        if isinstance(agent_message, str) and agent_message.strip():
            event_payload["payload"]["agent_message"] = agent_message.strip()
        try:
            logger.debug("[AUTO_TOOL] Emitting chat.tool_call for agent=%s tool=%s turn=%s", agent_name, binding.tool_name, turn_key)
            await transport.send_event_to_ui(event_payload, chat_id)
        except Exception:
            logger.debug("[AUTO_TOOL] Failed to emit tool_call for agent=%s", agent_name)

    async def _emit_tool_result(
        self,
        binding: AutoToolBinding,
        agent_name: str,
        chat_id: str | None,
        result: Any,
        status: str,
        turn_key: str,
    ) -> None:
        if not chat_id:
            return
        try:
            transport = await _get_simple_transport()
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
            logger.debug("[AUTO_TOOL] Emitting chat.tool_response for agent=%s tool=%s status=%s turn=%s", agent_name, binding.tool_name, status, turn_key)
            await transport.send_event_to_ui(payload, chat_id)
        except Exception:
            logger.debug("[AUTO_TOOL] Failed to emit tool_result for agent=%s", agent_name)

    async def _register_turn(self, cache_key: str) -> None:
        # A completed (or terminally consumed) turn keeps only its bounded
        # membership in the processed set; per-binding checkpoints are dropped.
        self._turn_checkpoints.pop(cache_key, None)
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


__all__ = ["AutoToolEventHandler", "AutoToolBinding", "AutoToolBindingDescriptor"]
