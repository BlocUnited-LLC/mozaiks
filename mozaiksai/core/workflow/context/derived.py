"""Derived context variable management (agent-centric schema)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Callable, Tuple

from autogen.events.agent_events import TextEvent

from logs.logging_config import get_workflow_logger
from ..workflow_manager import workflow_manager
from .schema import load_context_variables_config

logger = get_workflow_logger("derived_context")


def _resolve_nested_key(payload: Any, key: Optional[str]) -> Any:
    if key is None:
        return payload
    if not isinstance(key, str) or not key.strip():
        return None
    if not isinstance(payload, dict):
        return None
    if key in payload:
        return payload.get(key)
    # Support dotted lookup for nested response objects.
    parts = [p for p in key.split(".") if p]
    current: Any = payload
    for part in parts:
        if not isinstance(current, dict):
            return None
        if part not in current:
            return None
        current = current.get(part)
    return current

def _resolve_sender_name(event: TextEvent) -> Optional[str]:
    """Extract logical agent name for matching triggers."""

    def _from_value(value: Any, *, allow_string: bool = True) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            candidate = value.strip()
            return candidate if candidate and allow_string else None
        for attr in ('sender', 'agent', 'agent_name', 'name'):
            if hasattr(value, attr):
                candidate = getattr(value, attr)
                resolved = _from_value(candidate)
                if resolved:
                    return resolved
        if isinstance(value, dict):
            for key in ('agent', 'name', 'sender'):
                if key in value:
                    resolved = _from_value(value.get(key))
                    if resolved:
                        return resolved
            for nested in value.values():
                resolved = _from_value(nested, allow_string=False)
                if resolved:
                    return resolved
            return None
        if isinstance(value, (list, tuple, set)):
            for item in value:
                resolved = _from_value(item, allow_string=False)
                if resolved:
                    return resolved
        return None

    for attr in ('sender', 'agent', 'agent_name', 'name'):
        resolved = _from_value(getattr(event, attr, None))
        if resolved:
            return resolved
    metadata = getattr(event, 'metadata', None)
    if metadata:
        resolved = _from_value(metadata, allow_string=False)
        if resolved:
            return resolved
    raw = getattr(event, '__dict__', None) if hasattr(event, '__dict__') else None
    if isinstance(raw, dict):
        for key in ('agent', 'name', 'sender'):
            resolved = _from_value(raw.get(key))
            if resolved:
                return resolved
        if 'content' in raw:
            resolved = _from_value(raw.get('content'), allow_string=False)
            if resolved:
                return resolved
    resolved = _from_value(getattr(event, 'content', None), allow_string=False)
    if resolved:
        return resolved
    return None

@dataclass
class AgentTextTrigger:
    """Represents a derived trigger driven by agent text output."""

    agent: str
    equals: Optional[str] = None
    contains: Optional[str] = None
    regex: Optional[str] = None
    value: Any = True
    from_state: Optional[str] = None
    ui_hidden: bool = True
    _compiled: Optional[re.Pattern[str]] = None

    def __post_init__(self) -> None:
        if self.regex:
            try:
                self._compiled = re.compile(self.regex, re.IGNORECASE)
            except re.error:
                self._compiled = None

    def matches(self, event: TextEvent) -> bool:
        sender_name = _resolve_sender_name(event)
        if not sender_name or sender_name != self.agent:
            return False

        text = _extract_text(event)
        if not text:
            return False
        candidate = text.strip()
        if self.equals and candidate.lower() == self.equals.strip().lower():
            return True
        if self.contains and self.contains.lower() in candidate.lower():
            return True
        if self._compiled and self._compiled.search(candidate):
            return True
        return False


def _extract_text(event: TextEvent) -> str:
    raw = getattr(event, "content", None)

    def _dig(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if hasattr(value, "model_dump"):
            try:
                return _dig(value.model_dump())
            except Exception:  # pragma: no cover
                return None
        if hasattr(value, "dict"):
            try:
                return _dig(value.dict())
            except Exception:  # pragma: no cover
                return None
        if isinstance(value, dict):
            for key in ("content", "message", "text", "value"):
                if key in value:
                    found = _dig(value[key])
                    if found:
                        return found
            for item in value.values():
                found = _dig(item)
                if found:
                    return found
        if isinstance(value, (list, tuple)):
            for item in value:
                found = _dig(item)
                if found:
                    return found
        return None

    return _dig(raw) or ""


@dataclass
class DerivedVariableSpec:
    name: str
    default: Any
    triggers: List[AgentTextTrigger]

    def seed(self, providers: Iterable[Any]) -> None:
        for provider in providers:
            if hasattr(provider, "contains") and provider.contains(self.name):  # type: ignore[attr-defined]
                continue
            if hasattr(provider, "get") and provider.get(self.name, None) is not None:  # type: ignore[attr-defined]
                continue
            if hasattr(provider, "set"):
                try:
                    provider.set(self.name, self.default)  # type: ignore[attr-defined]
                except Exception as err:  # pragma: no cover
                    logger.debug(f"Derived variable seed failed: {err}")

    def apply(self, event: TextEvent, providers: Iterable[Any]) -> bool:
        for trigger in self.triggers:
            if trigger.matches(event):
                for provider in providers:
                    if hasattr(provider, "set"):
                        if trigger.from_state is not None:
                            current = None
                            if hasattr(provider, "get"):
                                try:
                                    current = provider.get(self.name)
                                except Exception:  # pragma: no cover
                                    current = None
                            if current != trigger.from_state:
                                continue
                        try:
                            provider.set(self.name, trigger.value)  # type: ignore[attr-defined]
                        except Exception as err:  # pragma: no cover
                            logger.debug(f"Derived variable update failed: {err}")
                return True
        return False


class DerivedContextManager:
    """Runtime helper that enforces declarative derived context variables."""

    def __init__(self, workflow_name: str, agents: Dict[str, Any], base_context: Any) -> None:
        self.workflow_name = workflow_name
        self.base_context = base_context
        self.providers: List[Any] = []
        self._listeners: List[Any] = []
        self._agent_hook_registry: set[int] = set()

        if base_context is not None:
            self.providers.append(base_context)
        self.providers.extend(
            [getattr(agent, "context_variables", None) for agent in agents.values() if getattr(agent, "context_variables", None)]
        )

        self.variables = self._load_variables()
        self.ui_response_bindings = self._load_ui_response_bindings()

        if self.variables:
            self.seed_defaults()
            logger.info(
                f"[DERIVED_CONTEXT] Loaded {len(self.variables)} agent_text state variables: {[v.name for v in self.variables]}"
            )
            self._register_agent_hooks(agents)
        if self.ui_response_bindings:
            logger.info(
                f"[DERIVED_CONTEXT] Loaded ui_response bindings: {len(self.ui_response_bindings)}"
            )
        if not self.variables and not self.ui_response_bindings:
            logger.debug("[DERIVED_CONTEXT] No triggers configured")


    @dataclass
    class UIResponseBinding:
        variable: str
        tool: str
        response_key: Optional[str] = None

    def _load_variables(self) -> List[DerivedVariableSpec]:
        definitions = getattr(self.base_context, "_mozaiks_context_definitions", None)
        if isinstance(definitions, dict) and definitions:
            return self._from_definitions(definitions)
        try:
            config = workflow_manager.get_config(self.workflow_name) or {}
            ctx_section = config.get("context_variables") or {}
            plan = load_context_variables_config(ctx_section)
            return self._from_definitions(plan.definitions)
        except Exception as err:  # pragma: no cover
            logger.debug(f"Derived context fallback load failed: {err}")
            return []

    def _from_definitions(self, definitions: Dict[str, Any]) -> List[DerivedVariableSpec]:
        results: List[DerivedVariableSpec] = []
        for name, definition in definitions.items():
            source = getattr(definition, "source", None)
            if not source:
                continue

            source_type = getattr(source, "type", None)
            if source_type != "state":
                continue

            triggers: List[AgentTextTrigger] = []

            # Direct triggers from source.triggers
            if source_type == "state" and getattr(source, "triggers", None):
                for trig_spec in getattr(source, "triggers", []) or []:
                    if not trig_spec or getattr(trig_spec, "type", None) != "agent_text":
                        continue
                    if not getattr(trig_spec, "agent", None):
                        continue
                    try:
                        triggers.append(
                            AgentTextTrigger(
                                agent=trig_spec.agent,
                                equals=trig_spec.match.equals if trig_spec.match else None,
                                contains=trig_spec.match.contains if trig_spec.match else None,
                                regex=trig_spec.match.regex if trig_spec.match else None,
                                value=True,
                                from_state=None,
                                ui_hidden=(getattr(trig_spec, "ui_hidden", None) is True),
                            )
                        )
                    except Exception as err:  # pragma: no cover
                        logger.debug(f"Skipping invalid direct trigger for {name}: {err}")

            if not triggers:
                continue

            results.append(
                DerivedVariableSpec(
                    name=name,
                    default=getattr(source, "default", False),
                    triggers=triggers,
                )
            )
        return results

    def _load_ui_response_bindings(self) -> List["DerivedContextManager.UIResponseBinding"]:
        definitions = getattr(self.base_context, "_mozaiks_context_definitions", None)
        if isinstance(definitions, dict) and definitions:
            return self._ui_bindings_from_definitions(definitions)
        try:
            config = workflow_manager.get_config(self.workflow_name) or {}
            ctx_section = config.get("context_variables") or {}
            plan = load_context_variables_config(ctx_section)
            return self._ui_bindings_from_definitions(plan.definitions)
        except Exception as err:  # pragma: no cover
            logger.debug(f"UI response bindings fallback load failed: {err}")
            return []

    def _ui_bindings_from_definitions(self, definitions: Dict[str, Any]) -> List["DerivedContextManager.UIResponseBinding"]:
        bindings: List[DerivedContextManager.UIResponseBinding] = []
        for name, definition in definitions.items():
            source = getattr(definition, "source", None)
            if not source:
                continue
            if getattr(source, "type", None) != "state":
                continue

            # Direct triggers from source.triggers
            for trig_spec in getattr(source, "triggers", []) or []:
                if not trig_spec or getattr(trig_spec, "type", None) != "ui_response":
                    continue
                tool = getattr(trig_spec, "tool", None)
                if not isinstance(tool, str) or not tool.strip():
                    continue
                response_key = getattr(trig_spec, "response_key", None)
                bindings.append(
                    DerivedContextManager.UIResponseBinding(
                        variable=name,
                        tool=tool.strip(),
                        response_key=response_key if isinstance(response_key, str) else None,
                    )
                )

        return bindings

    def has_variables(self) -> bool:
        # Back-compat method name: treat any trigger binding as active.
        return bool(self.variables or self.ui_response_bindings)

    def apply_ui_tool_response(self, *, tool_name: str, response_data: Dict[str, Any]) -> List[str]:
        """Apply declarative ui_response triggers based on a completed UI tool response.

        This updates AG2 ContextVariables providers (group manager, pattern context, etc.)
        so context-based handoffs can proceed immediately after the user interacts.
        """
        normalized_tool = (tool_name or "").strip()
        if not normalized_tool or not isinstance(response_data, dict):
            return []

        updated_vars: List[str] = []
        for binding in self.ui_response_bindings or []:
            if binding.tool != normalized_tool:
                continue
            value = _resolve_nested_key(response_data, binding.response_key)
            if value is None:
                continue
            updated = False
            for provider in self.providers:
                if hasattr(provider, "set"):
                    try:
                        provider.set(binding.variable, value)  # type: ignore[attr-defined]
                        updated = True
                    except Exception as err:  # pragma: no cover
                        logger.debug(f"[DERIVED_CONTEXT] ui_response update failed: {err}")
            if updated:
                updated_vars.append(binding.variable)
                logger.info(
                    f"[DERIVED_CONTEXT] {self.workflow_name}: {binding.variable} -> {value!r} (ui_response, tool={normalized_tool})"
                )
                for cb in list(self._listeners):
                    try:
                        cb({"variable": binding.variable, "value": value, "tool": normalized_tool})
                    except Exception:  # pragma: no cover
                        pass

        return updated_vars

    def _register_agent_hooks(self, agents: Dict[str, Any]) -> None:
        trigger_map: Dict[str, List[Tuple[DerivedVariableSpec, AgentTextTrigger]]] = {}
        for var in self.variables:
            for trigger in var.triggers:
                trigger_map.setdefault(trigger.agent, []).append((var, trigger))

        for agent_name, trigger_pairs in trigger_map.items():
            agent_obj = agents.get(agent_name)
            if not agent_obj or not hasattr(agent_obj, "register_hook"):
                continue
            agent_id = id(agent_obj)
            if agent_id in self._agent_hook_registry:
                continue
            try:
                agent_obj.register_hook(
                    "process_message_before_send",
                    self._make_pre_send_hook(agent_name, trigger_pairs),
                )
                self._agent_hook_registry.add(agent_id)
                logger.debug(f"[DERIVED_CONTEXT] Registered pre-send hook for {agent_name}")
            except Exception as err:  # pragma: no cover
                logger.debug(f"[DERIVED_CONTEXT] Failed to register pre-send hook for {agent_name}: {err}")

    def _make_pre_send_hook(
        self,
        agent_name: str,
        trigger_pairs: List[Tuple[DerivedVariableSpec, AgentTextTrigger]],
    ) -> Callable[[Any, Any, Any, bool], Any]:
        def _hook(sender=None, message=None, recipient=None, silent: bool = False):
            # Hooks must accept the AG2 signature, but we only care about the message payload.
            # Explicitly delete unused parameters so static analyzers know this is intentional.
            del sender, recipient, silent
            raw_message = message.get("content") if isinstance(message, dict) else message
            if not isinstance(raw_message, str):
                return message
            candidate = raw_message.strip()
            if not candidate:
                return message

            should_hide = False
            for var, trigger in trigger_pairs:
                if self._matches_trigger(trigger, candidate):
                    # Determine value to set (handle dynamic extraction)
                    value_to_set = trigger.value
                    if value_to_set == "$1" and trigger._compiled:
                        m = trigger._compiled.search(candidate)
                        if m and m.groups():
                            value_to_set = m.group(1)

                    updated = False
                    for provider in self.providers:
                        if hasattr(provider, "set"):
                            if trigger.from_state is not None:
                                current = None
                                if hasattr(provider, "get"):
                                    try:
                                        current = provider.get(var.name)
                                    except Exception:  # pragma: no cover
                                        current = None
                                if current != trigger.from_state:
                                    continue
                            try:
                                provider.set(var.name, value_to_set)  # type: ignore[attr-defined]
                                updated = True
                            except Exception as err:  # pragma: no cover
                                logger.debug(f"[DERIVED_CONTEXT] pre-send update failed: {err}")
                    if updated:
                        logger.info(
                            f"[DERIVED_CONTEXT] {self.workflow_name}: {var.name} -> {value_to_set!r} (pre-send, agent={agent_name})"
                        )
                    if trigger.ui_hidden:
                        should_hide = True
            if should_hide:
                if isinstance(message, dict):
                    updated_message = dict(message)
                    updated_message["_mozaiks_hide"] = True
                    return updated_message
                return {"content": raw_message, "_mozaiks_hide": True}
            return message

        return _hook

    @staticmethod
    def _matches_trigger(trigger: AgentTextTrigger, text: str) -> bool:
        candidate = text.strip()
        if not candidate:
            return False
        if trigger.equals and candidate.lower() == trigger.equals.strip().lower():
            return True
        if trigger.contains and trigger.contains.lower() in candidate.lower():
            return True
        if trigger._compiled and trigger._compiled.search(candidate):
            return True
        return False

    def register_additional_provider(self, provider: Any) -> None:
        if provider and provider not in self.providers:
            self.providers.append(provider)
            for var in self.variables:
                var.seed([provider])

    def seed_defaults(self) -> None:
        for var in self.variables:
            var.seed(self.providers)

    def add_listener(self, callback) -> None:
        if callable(callback):
            self._listeners.append(callback)

    def handle_event(self, event: Any) -> None:
        if not self.variables or not isinstance(event, TextEvent):
            return
        for var in self.variables:
            if var.apply(event, self.providers):
                logger.info(f"[DERIVED_CONTEXT] {self.workflow_name}: {var.name} -> True")
                try:
                    snapshot = (
                        self.base_context.to_dict()
                        if hasattr(self.base_context, "to_dict")
                        else getattr(self.base_context, "data", {})
                    )
                    logger.debug(f"[DERIVED_CONTEXT] base_context snapshot: {snapshot}")
                except Exception as ctx_err:  # pragma: no cover
                    logger.debug(f"[DERIVED_CONTEXT] base_context snapshot unavailable: {ctx_err}")
                if self._listeners:
                    payload = {"variable": var.name, "value": True}
                    for callback in list(self._listeners):
                        try:
                            callback(payload)
                        except Exception:  # pragma: no cover
                            continue


__all__ = ["DerivedContextManager"]
