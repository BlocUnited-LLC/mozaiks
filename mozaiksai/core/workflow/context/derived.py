"""Derived context variable management (agent-centric schema)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from autogen.events.agent_events import TextEvent

from logs.logging_config import get_workflow_logger

from .schema import load_context_variables_config

logger = get_workflow_logger("derived_context")


def _resolve_nested_key(payload: Any, key: str | None) -> Any:
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


def _compile_optional_regex(pattern: str | None) -> re.Pattern[str] | None:
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def _matches_text_conditions(
    *,
    text: str,
    equals: str | None,
    contains: str | None,
    compiled: re.Pattern[str] | None,
) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False
    if equals and candidate.lower() == equals.strip().lower():
        return True
    if contains and contains.lower() in candidate.lower():
        return True
    if compiled and compiled.search(candidate):
        return True
    return False

def _resolve_sender_name(event: TextEvent) -> str | None:
    """Extract logical agent name for matching triggers."""

    def _from_value(value: Any, *, allow_string: bool = True) -> str | None:
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
    equals: str | None = None
    contains: str | None = None
    regex: str | None = None
    value: Any = True
    from_state: str | None = None
    ui_hidden: bool = True
    _compiled: re.Pattern[str] | None = None

    def __post_init__(self) -> None:
        self._compiled = _compile_optional_regex(self.regex)

    def matches(self, event: TextEvent) -> bool:
        sender_name = _resolve_sender_name(event)
        if not sender_name or sender_name != self.agent:
            return False

        text = _extract_text(event)
        return _matches_text_conditions(
            text=text,
            equals=self.equals,
            contains=self.contains,
            compiled=self._compiled,
        )


@dataclass
class UserTextBinding:
    """Represents a derived trigger driven by a plain user reply."""

    variable: str
    equals: str | None = None
    contains: str | None = None
    regex: str | None = None
    value: Any = True
    _compiled: re.Pattern[str] | None = None

    def __post_init__(self) -> None:
        self._compiled = _compile_optional_regex(self.regex)

    def matches(self, text: str) -> bool:
        return _matches_text_conditions(
            text=text,
            equals=self.equals,
            contains=self.contains,
            compiled=self._compiled,
        )


def _extract_text(event: TextEvent) -> str:
    raw = getattr(event, "content", None)

    def _dig(value: Any) -> str | None:
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
    triggers: list[AgentTextTrigger]

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
                    logger.debug("Derived variable seed failed: %s", err)

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
                            logger.debug("Derived variable update failed: %s", err)
                return True
        return False


class DerivedContextManager:
    """Runtime helper that enforces declarative derived context variables."""

    def __init__(self, workflow_name: str, agents: dict[str, Any], base_context: Any) -> None:
        self.workflow_name = workflow_name
        self.base_context = base_context
        self.providers: list[Any] = []
        self._listeners: list[Any] = []

        if base_context is not None:
            self.providers.append(base_context)
        self.providers.extend(
            [getattr(agent, "context_variables", None) for agent in agents.values() if getattr(agent, "context_variables", None)]
        )

        self.definitions = self._load_definitions()
        self.state_defaults = self._load_state_defaults(self.definitions)
        self.variables = self._from_definitions(self.definitions)
        self.ui_response_bindings = self._ui_bindings_from_definitions(self.definitions)
        self.user_text_bindings = self._user_text_bindings_from_definitions(self.definitions)

        if self.variables or self.ui_response_bindings or self.user_text_bindings or self.state_defaults:
            self.seed_defaults()
        if self.variables:
            logger.debug(
                "[DERIVED_CONTEXT] Loaded %s agent_text state variables: %s", len(self.variables), [v.name for v in self.variables])
        if self.ui_response_bindings:
            logger.debug(
                "[DERIVED_CONTEXT] Loaded ui_response bindings: %s", len(self.ui_response_bindings))
        if self.user_text_bindings:
            logger.debug(
                "[DERIVED_CONTEXT] Loaded user_text bindings: %s", len(self.user_text_bindings))
        if not self.variables and not self.ui_response_bindings and not self.user_text_bindings:
            logger.debug("[DERIVED_CONTEXT] No triggers configured")


    @dataclass
    class UIResponseBinding:
        variable: str
        tool: str
        response_key: str | None = None

    def _load_definitions(self) -> dict[str, Any]:
        definitions = getattr(self.base_context, "_mozaiks_context_definitions", None)
        if isinstance(definitions, dict) and definitions:
            return definitions
        try:
            from ..workflow_manager import get_workflow_manager

            workflow_manager = get_workflow_manager()
            config = workflow_manager.get_config(self.workflow_name) or {}
            ctx_section = config.get("context_variables") or {}
            plan = load_context_variables_config(ctx_section)
            return plan.definitions
        except Exception as err:  # pragma: no cover
            logger.debug("Derived context fallback load failed: %s", err)
            return {}

    def _load_state_defaults(self, definitions: dict[str, Any]) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for name, definition in definitions.items():
            source = getattr(definition, "source", None)
            if not source or getattr(source, "type", None) != "state":
                continue
            defaults[name] = getattr(source, "default", False)
        return defaults

    def _from_definitions(self, definitions: dict[str, Any]) -> list[DerivedVariableSpec]:
        results: list[DerivedVariableSpec] = []
        for name, definition in definitions.items():
            source = getattr(definition, "source", None)
            if not source:
                continue

            source_type = getattr(source, "type", None)
            if source_type != "state":
                continue

            triggers: list[AgentTextTrigger] = []

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
                        logger.debug("Skipping invalid direct trigger for %s: %s", name, err)

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

    def _ui_bindings_from_definitions(self, definitions: dict[str, Any]) -> list[DerivedContextManager.UIResponseBinding]:
        bindings: list[DerivedContextManager.UIResponseBinding] = []
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

    def _user_text_bindings_from_definitions(self, definitions: dict[str, Any]) -> list[UserTextBinding]:
        bindings: list[UserTextBinding] = []
        for name, definition in definitions.items():
            source = getattr(definition, "source", None)
            if not source or getattr(source, "type", None) != "state":
                continue
            for trig_spec in getattr(source, "triggers", []) or []:
                if not trig_spec or getattr(trig_spec, "type", None) != "user_text":
                    continue
                match = getattr(trig_spec, "match", None)
                if not match:
                    continue
                bindings.append(
                    UserTextBinding(
                        variable=name,
                        equals=getattr(match, "equals", None),
                        contains=getattr(match, "contains", None),
                        regex=getattr(match, "regex", None),
                        value=True,
                    )
                )
        return bindings

    def has_variables(self) -> bool:
        # Back-compat method name: treat any trigger binding as active.
        return bool(self.variables or self.ui_response_bindings or self.user_text_bindings)

    def apply_tool_call_response(self, *, tool_name: str, response_data: dict[str, Any]) -> list[str]:
        """Apply declarative ui_response triggers based on a completed tool call response.

        This updates AG2 ContextVariables providers (group manager, pattern context, etc.)
        so context-based handoffs can proceed immediately after the user interacts.
        """
        normalized_tool = (tool_name or "").strip()
        if not normalized_tool or not isinstance(response_data, dict):
            return []

        updated_vars: list[str] = []
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
                        logger.debug("[DERIVED_CONTEXT] ui_response update failed: %s", err)
            if updated:
                updated_vars.append(binding.variable)
                logger.debug(
                    "[DERIVED_CONTEXT] %s: %s -> %s (ui_response, tool=%s)", self.workflow_name, binding.variable, value, normalized_tool)
                for cb in list(self._listeners):
                    try:
                        cb({"variable": binding.variable, "value": value, "tool": normalized_tool})
                    except Exception:  # pragma: no cover
                        pass

        return updated_vars

    def apply_agent_text(self, agent_name: str, text: str) -> dict[str, Any]:
        """Apply declarative agent_text triggers based on what an agent just said.

        Called after an AG2 agent packet is projected so context variables that
        depend on agent output (e.g. `intake_complete`) are updated before the
        next routing decision is made.
        """

        candidate = str(text or "").strip()
        if not candidate or not self.variables:
            return {}

        # Build a per-agent lookup once and check only variables that care about
        # this specific agent to keep the hot path O(matching triggers).
        updated_vars: dict[str, Any] = {}
        for var in self.variables:
            for trigger in var.triggers:
                if trigger.agent != agent_name:
                    continue
                if not self._matches_trigger(trigger, candidate):
                    continue
                value_to_set = trigger.value
                if value_to_set == "$1" and trigger._compiled:
                    m = trigger._compiled.search(candidate)
                    if m and m.groups():
                        value_to_set = m.group(1)
                for provider in self.providers:
                    if hasattr(provider, "set"):
                        try:
                            provider.set(var.name, value_to_set)  # type: ignore[attr-defined]
                            updated_vars[var.name] = value_to_set
                        except Exception as err:  # pragma: no cover
                            logger.debug("[DERIVED_CONTEXT] apply_agent_text update failed: %s", err)
                if var.name in updated_vars:
                    logger.debug(
                        "[DERIVED_CONTEXT] %s: %s -> %r (agent_text, agent=%s)",
                        self.workflow_name, var.name, updated_vars[var.name], agent_name,
                    )
                    for cb in list(self._listeners):
                        try:
                            cb({"variable": var.name, "value": updated_vars[var.name], "source": "agent_text"})
                        except Exception:  # pragma: no cover
                            pass
        return updated_vars

    def apply_user_text(self, text: str) -> dict[str, Any]:
        """Apply declarative user_text triggers based on a free-form composer reply."""

        candidate = str(text or "").strip()
        if not candidate:
            return {}

        updated_vars: dict[str, Any] = {}
        for binding in self.user_text_bindings or []:
            if not binding.matches(candidate):
                continue
            updated = False
            for provider in self.providers:
                if hasattr(provider, "set"):
                    try:
                        provider.set(binding.variable, binding.value)  # type: ignore[attr-defined]
                        updated = True
                    except Exception as err:  # pragma: no cover
                        logger.debug("[DERIVED_CONTEXT] user_text update failed: %s", err)
            if updated:
                updated_vars[binding.variable] = binding.value
                logger.debug(
                    "[DERIVED_CONTEXT] %s: %s -> %s (user_text)", self.workflow_name, binding.variable, binding.value)
                for cb in list(self._listeners):
                    try:
                        cb({"variable": binding.variable, "value": binding.value, "source": "user_text"})
                    except Exception:  # pragma: no cover
                        pass

        return updated_vars

    @staticmethod
    def _matches_trigger(trigger: AgentTextTrigger, text: str) -> bool:
        return _matches_text_conditions(
            text=text,
            equals=trigger.equals,
            contains=trigger.contains,
            compiled=trigger._compiled,
        )

    def register_additional_provider(self, provider: Any) -> None:
        if provider and provider not in self.providers:
            self.providers.append(provider)
            self.seed_defaults()

    def seed_defaults(self) -> None:
        for name, default in self.state_defaults.items():
            for provider in self.providers:
                if hasattr(provider, "contains") and provider.contains(name):  # type: ignore[attr-defined]
                    continue
                if hasattr(provider, "get") and provider.get(name, None) is not None:  # type: ignore[attr-defined]
                    continue
                if hasattr(provider, "set"):
                    try:
                        provider.set(name, default)  # type: ignore[attr-defined]
                    except Exception as err:  # pragma: no cover
                        logger.debug("Derived variable seed failed: %s", err)

    def add_listener(self, callback) -> None:
        if callable(callback):
            self._listeners.append(callback)

    def handle_event(self, event: Any) -> None:
        if not self.variables or not isinstance(event, TextEvent):
            return
        for var in self.variables:
            if var.apply(event, self.providers):
                logger.debug("[DERIVED_CONTEXT] %s: %s -> True", self.workflow_name, var.name)
                try:
                    snapshot = (
                        self.base_context.to_dict()
                        if hasattr(self.base_context, "to_dict")
                        else getattr(self.base_context, "data", {})
                    )
                    logger.debug("[DERIVED_CONTEXT] base_context snapshot: %s", snapshot)
                except Exception as ctx_err:  # pragma: no cover
                    logger.debug("[DERIVED_CONTEXT] base_context snapshot unavailable: %s", ctx_err)
                if self._listeners:
                    payload = {"variable": var.name, "value": True}
                    for callback in list(self._listeners):
                        try:
                            callback(payload)
                        except Exception:  # pragma: no cover
                            continue


__all__ = ["DerivedContextManager"]
