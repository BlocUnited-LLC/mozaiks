from __future__ import annotations

"""ModuleLoader — discovers canonical Mozaiks module contracts.

A module lives under app/modules/{module_id}/ and must include:

    - module.yaml
    - backend/handler.py

Companion manifests live under contracts/ and are optional:

    - events.yaml
    - reactions.yaml
    - notifications.yaml
    - settings.yaml
    - admin.yaml
    - policy_hooks.yaml

`module.yaml` declares the public module surface. `backend/handler.py`
implements the deterministic methods referenced by `actions[].handler_method`.
The loader validates the contract before the module is registered for runtime
execution.
"""

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("module_loader")

CANONICAL_EVENT_PREFIXES = ("domain.", "platform.", "hosted.", "mozaikspay.")
CANONICAL_EVENT_PREFIX_LABEL = "domain.*, platform.*, hosted.*, or mozaikspay.*"


def _default_notification_channels() -> list[Literal["in_app", "email", "push", "sms"]]:
    return ["in_app"]


class ModuleContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("value must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModuleLoadError(f"Failed to read {path.name}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ModuleLoadError(f"{path.name} must contain a YAML object")
    return raw


def _normalize_notifications_yaml(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize YAML 1.1 boolean keys such as unquoted `on:` before validation."""
    notifications = raw.get("notifications")
    if not isinstance(notifications, list):
        return raw
    normalized: list[Any] = []
    changed = False
    for item in notifications:
        if isinstance(item, dict) and True in item and "on" not in item:
            item = dict(item)
            item["on"] = item.pop(True)
            changed = True
        normalized.append(item)
    if not changed:
        return raw
    return {**raw, "notifications": normalized}


def _validate_entrypoint(entrypoint: str) -> tuple[str, str]:
    text = _required_text(entrypoint, field_name="module.handler")
    if ":" not in text:
        raise ValueError("module.handler must use module.path:ClassName syntax")

    module_path, class_name = text.split(":", 1)
    module_path = module_path.strip()
    class_name = class_name.strip()
    if not module_path or not class_name:
        raise ValueError("module.handler must include both module path and class name")
    if "_shared" in module_path or "\\" in module_path:
        raise ValueError("module.handler must be module-local")

    rel = PurePosixPath(module_path.replace(".", "/") + ".py")
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError("module.handler must be a safe module-local path")
    if not class_name.replace("_", "").isalnum():
        raise ValueError("module.handler class name must be a valid identifier-like value")
    return str(rel), class_name


class ModuleIdentity(ModuleContractModel):
    id: str
    display_name: str | None = None
    version: str = "1.0.0"
    type: Literal[
        "standard",
        "messaging",
        "workflow",
        "event_pipeline",
        "transactional",
        "billing",
        "membership",
        "entitlement_dispatch",
    ] = "standard"
    description: str | None = None
    owner: str | None = None
    visibility: str | None = None
    handler: str
    # When true the module owns user-scoped data and must provide a
    # backend/account_data_handler.py that implements AccountDataHandler.
    # The loader auto-registers the handler with account_data_registry at startup.
    user_data_scope: bool = False

    @field_validator("id", "handler", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=f"module.{info.field_name}")

    @field_validator("display_name", "description", "owner", "visibility", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> str:
        return _required_text(value or "1.0.0", field_name="module.version")

    @field_validator("handler")
    @classmethod
    def _handler_entrypoint(cls, value: str) -> str:
        _validate_entrypoint(value)
        return value


class ModulePermission(ModuleContractModel):
    id: str
    description: str

    @field_validator("id", "description", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)


class ActionDef(ModuleContractModel):
    id: str
    description: str
    handler_method: str
    api_surface: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    emits: list[str] = Field(default_factory=list)
    # Optional entitlement gate: capability_id to check before dispatch.
    # When set, ModuleExecutor calls EntitlementPort.check(entitlement_gate, ...)
    # and returns ENTITLEMENT_REQUIRED if the grant is not active.
    # When null/absent, no entitlement check is performed.
    entitlement_gate: str | None = None

    @field_validator("id", "description", "handler_method", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("permissions", "emits", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        return _string_list(value)

    @field_validator("entitlement_gate", mode="before")
    @classmethod
    def _entitlement_gate(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("api_surface", mode="before")
    @classmethod
    def _api_surface(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("api_surface must be a string")
        text = value.strip()
        if not text:
            raise ValueError("api_surface must be non-empty")
        return text


class ModuleCapability(ModuleContractModel):
    capability_id: str
    kind: Literal["action", "workflow", "page", "transition", "hosted"]
    target: str
    title: str
    permissions: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] | None = None

    @field_validator("capability_id", "target", "title", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: Any) -> list[str]:
        return _string_list(value)


class ModuleDefinition(ModuleContractModel):
    schema_version: Literal["mozaiks.module.v1"]
    module: ModuleIdentity
    permissions: list[ModulePermission] = Field(default_factory=list)
    actions: list[ActionDef] = Field(default_factory=list)
    capabilities: list[ModuleCapability] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.module.id

    @property
    def version(self) -> str:
        return self.module.version

    @property
    def action_method_map(self) -> dict[str, str]:
        return {action.id: action.handler_method for action in self.actions}

    @property
    def action_permissions_map(self) -> dict[str, list[str]]:
        """Maps each action id to its declared required permission ids."""
        return {action.id: list(action.permissions) for action in self.actions}

    @property
    def action_api_surface_map(self) -> dict[str, str | None]:
        """Maps each action id to its declared external API surface metadata."""
        return {action.id: action.api_surface for action in self.actions}

    @property
    def action_schemas_map(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Maps each action id to its declared input/output JSON Schemas."""
        return {
            action.id: {"input": action.input_schema, "output": action.output_schema}
            for action in self.actions
        }

    @property
    def action_entitlement_map(self) -> dict[str, str | None]:
        """Maps each action id to its entitlement_gate capability_id (or None)."""
        return {action.id: action.entitlement_gate for action in self.actions}

    @property
    def action_emits_map(self) -> dict[str, list[str]]:
        """Maps each action id to event types declared in module.yaml actions[].emits."""
        return {action.id: list(action.emits) for action in self.actions}

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> ModuleDefinition:
        action_ids = [action.id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("module actions must have unique ids")

        permission_ids = [permission.id for permission in self.permissions]
        if len(permission_ids) != len(set(permission_ids)):
            raise ValueError("module permissions must have unique ids")

        capability_ids = [capability.capability_id for capability in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("module capabilities must have unique capability_id values")

        known_actions = set(action_ids)
        for capability in self.capabilities:
            if capability.kind == "action" and capability.target not in known_actions:
                raise ValueError(
                    f"action capability {capability.capability_id!r} targets unknown action {capability.target!r}"
                )

        # Check 3: every permission ID referenced in action.permissions[] must be
        # declared in the module-level permissions block.
        declared_permission_ids = set(permission_ids)
        for action in self.actions:
            for perm_id in action.permissions:
                if perm_id not in declared_permission_ids:
                    raise ValueError(
                        f"action {action.id!r} references undeclared permission {perm_id!r}; "
                        "add it to the module-level permissions block"
                    )
        return self


class ModuleEvent(ModuleContractModel):
    type: str
    version: int
    description: str | None = None
    producer: str
    payload_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type", "producer", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("type")
    @classmethod
    def _canonical_event_namespace(cls, value: str) -> str:
        if not value.startswith(CANONICAL_EVENT_PREFIXES):
            raise ValueError(f"module-published events must use {CANONICAL_EVENT_PREFIX_LABEL}")
        return value


class ModuleEventsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.events.v1"] = "mozaiks.events.v1"
    events: list[ModuleEvent] = Field(default_factory=list)

    @property
    def event_types(self) -> set[str]:
        return {event.type for event in self.events}

    @model_validator(mode="after")
    def _validate_unique_events(self) -> ModuleEventsManifest:
        event_types = [event.type for event in self.events]
        if len(event_types) != len(set(event_types)):
            raise ValueError("events.yaml events must have unique type values")
        return self


class ModuleReactionTarget(ModuleContractModel):
    kind: Literal["handler", "capability", "notification", "service_adapter"]
    handler_method: str | None = None
    capability_id: str | None = None
    notification_id: str | None = None
    adapter: str | None = None
    adapter_method: str | None = None

    @field_validator("handler_method", "capability_id", "notification_id", "adapter", "adapter_method", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_target_contract(self) -> ModuleReactionTarget:
        if self.kind == "handler":
            if not self.handler_method:
                raise ValueError("handler reactions must declare target.handler_method")
            if self.capability_id or self.notification_id or self.adapter or self.adapter_method:
                raise ValueError(
                    "handler reactions must not declare capability_id, notification_id, adapter, or adapter_method"
                )
        elif self.kind == "capability":
            if not self.capability_id:
                raise ValueError("capability reactions must declare target.capability_id")
            if self.handler_method or self.notification_id or self.adapter or self.adapter_method:
                raise ValueError(
                    "capability reactions must not declare handler_method, notification_id, adapter, or adapter_method"
                )
        elif self.kind == "notification":
            if not self.notification_id:
                raise ValueError("notification reactions must declare target.notification_id")
            if self.handler_method or self.capability_id or self.adapter or self.adapter_method:
                raise ValueError(
                    "notification reactions must not declare handler_method, capability_id, adapter, or adapter_method"
                )
        elif self.kind == "service_adapter":
            if not self.adapter or not self.adapter_method:
                raise ValueError("service_adapter reactions must declare target.adapter and target.adapter_method")
            if self.handler_method or self.capability_id or self.notification_id:
                raise ValueError(
                    "service_adapter reactions must not declare handler_method, capability_id, or notification_id"
                )
        return self


class ModuleReaction(ModuleContractModel):
    id: str
    event_type: str
    target: ModuleReactionTarget
    description: str | None = None
    filters: dict[str, Any] | None = None
    idempotency_key: str | None = None
    permissions: list[str] = Field(default_factory=list)
    # Lease and retry configuration — only meaningful when idempotency_key is set.
    # lease_seconds: how long a claim is held before it becomes reclaimable (crash recovery).
    # max_attempts: total attempts allowed; None = unlimited retries.
    # retry_delay_seconds: minimum wait after a failure before the slot is reclaimable.
    lease_seconds: int = 300
    max_attempts: int | None = None
    retry_delay_seconds: int = 0

    @field_validator("id", "event_type", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("description", "idempotency_key", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: Any) -> list[str]:
        return _string_list(value)

    @field_validator("event_type")
    @classmethod
    def _canonical_event_namespace(cls, value: str) -> str:
        if not value.startswith(CANONICAL_EVENT_PREFIXES):
            raise ValueError(
                f"module reactions must use {CANONICAL_EVENT_PREFIX_LABEL} event_type values"
            )
        return value

    @field_validator("lease_seconds", "max_attempts", "retry_delay_seconds", mode="before")
    @classmethod
    def _int_not_bool(cls, value: Any, info) -> Any:
        """Reject booleans and non-integers; accept None only for max_attempts."""
        if value is None:
            return value
        if isinstance(value, bool):
            raise ValueError(f"{info.field_name} must be an integer, not a boolean")
        if not isinstance(value, int):
            raise ValueError(f"{info.field_name} must be an integer")
        return value

    @field_validator("lease_seconds")
    @classmethod
    def _lease_seconds_bounds(cls, value: int) -> int:
        if value < 10 or value > 3600:
            raise ValueError("lease_seconds must be between 10 and 3600")
        return value

    @field_validator("max_attempts")
    @classmethod
    def _max_attempts_positive(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("max_attempts must be at least 1")
        return value

    @field_validator("retry_delay_seconds")
    @classmethod
    def _retry_delay_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retry_delay_seconds must be >= 0")
        return value


class ModuleReactionsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.reactions.v1"] = "mozaiks.reactions.v1"
    reactions: list[ModuleReaction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_reactions(self) -> ModuleReactionsManifest:
        reaction_ids = [reaction.id for reaction in self.reactions]
        if len(reaction_ids) != len(set(reaction_ids)):
            raise ValueError("reactions.yaml reactions must have unique id values")
        return self


class ModuleNotificationAudience(ModuleContractModel):
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    user_id_field: str | None = None
    resolver: str | None = None

    @field_validator("roles", "permissions", mode="before")
    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        return _string_list(value)

    @field_validator("user_id_field", "resolver", mode="before")
    @classmethod
    def _optional_resolver(cls, value: Any) -> str | None:
        return _optional_text(value)


class ModuleNotificationTemplate(ModuleContractModel):
    title: str | None = None
    body: str | None = None

    @field_validator("title", "body", mode="before")
    @classmethod
    def _optional_template_text(cls, value: Any) -> str | None:
        return _optional_text(value)


class ModuleNotification(ModuleContractModel):
    id: str
    event_type: str | None = None
    # Alternate field accepted by notification contracts.
    on: str | None = None
    description: str | None = None
    condition: dict[str, Any] | None = None
    channels: list[Literal["in_app", "email", "push", "sms"]] = Field(default_factory=_default_notification_channels)
    audience: ModuleNotificationAudience = Field(default_factory=ModuleNotificationAudience)
    template: ModuleNotificationTemplate | None = None
    # AppGenerator's structured output also supports top-level title/body.
    title: str | None = None
    body: str | None = None
    context_fields: list[str] = Field(default_factory=list)
    preference_key: str | None = None
    default_enabled: bool = True
    renderer: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _required_id(cls, value: Any) -> str:
        return _required_text(value, field_name="notifications[].id")

    @field_validator("event_type", "on", "description", "preference_key", "renderer", mode="before")
    @classmethod
    def _optional_fields(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("title", "body", mode="before")
    @classmethod
    def _optional_message_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("context_fields", mode="before")
    @classmethod
    def _context_fields(cls, value: Any) -> list[str]:
        return _string_list(value)

    @field_validator("channels", mode="before")
    @classmethod
    def _channels(cls, value: Any) -> list[str]:
        channels = _string_list(value)
        return channels or ["in_app"]

    @model_validator(mode="after")
    def _normalize(self) -> ModuleNotification:
        event_type = self.event_type or self.on
        if not event_type:
            raise ValueError("notifications.yaml entries must declare event_type")
        self.event_type = event_type
        if self.template is None and (self.title or self.body):
            self.template = ModuleNotificationTemplate(title=self.title, body=self.body)
        return self

    def as_rule(self) -> dict[str, Any]:
        data = self.model_dump(mode="python", exclude_none=True)
        data.pop("on", None)
        data.pop("title", None)
        data.pop("body", None)
        return data


class ModuleNotificationsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.notifications.v1"] = "mozaiks.notifications.v1"
    notifications: list[ModuleNotification] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_yaml_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        notifications = data.get("notifications")
        if not isinstance(notifications, list):
            return data
        normalized: list[Any] = []
        for item in notifications:
            if isinstance(item, dict) and True in item and "on" not in item:
                item = dict(item)
                item["on"] = item.pop(True)
            normalized.append(item)
        return {**data, "notifications": normalized}

    @model_validator(mode="after")
    def _validate_unique_notifications(self) -> ModuleNotificationsManifest:
        notification_ids = [notification.id for notification in self.notifications]
        if len(notification_ids) != len(set(notification_ids)):
            raise ValueError("notifications.yaml notifications must have unique id values")
        return self


class SettingDef(ModuleContractModel):
    """A single setting declared in contracts/settings.yaml."""

    id: str
    type: Literal["string", "boolean", "integer", "enum"] = "string"
    default: Any = None
    scope: Literal["app", "user"] = "app"
    label: str = ""
    description: str | None = None
    enum_values: list[str] | None = None


class ModuleSettingsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.settings.v1"] = "mozaiks.settings.v1"
    settings: list[SettingDef] = Field(default_factory=list)
    features: list[dict[str, Any]] = Field(default_factory=list)

    def defaults(self) -> dict[str, Any]:
        """Return a resolved dict of {setting_id: default_value} for all declared settings."""
        return {s.id: s.default for s in self.settings}


class ModuleAdminPanel(ModuleContractModel):
    id: str
    label: str
    description: str | None = None
    page: str  # references a page id declared in app/admin/admin_registry.yaml
    order: int = 0
    renderer: Literal["schema", "custom_component"] = "schema"
    layout: Literal["grid", "sidebar", "full-width", "split"] | None = None
    sections: list[dict[str, Any]] = Field(default_factory=list)
    component: str | None = None
    permissions: list[str] = Field(default_factory=list)

    @field_validator("id", "label", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("page", mode="before")
    @classmethod
    def _page(cls, value: Any) -> str:
        text = str(value or "").strip().lower().replace("_", "-")
        if not text:
            raise ValueError("admin panel 'page' must reference a non-empty page id")
        return text

    @field_validator("description", "component", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: Any) -> list[str]:
        return _string_list(value)

    @field_validator("sections", mode="before")
    @classmethod
    def _sections(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("sections must be a list")
        result: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("sections entries must be objects")
            result.append(item)
        return result

    @model_validator(mode="after")
    def _validate_renderer_contract(self) -> ModuleAdminPanel:
        if self.renderer == "schema":
            if self.component is not None:
                raise ValueError("schema admin panels must not declare component")
            if not self.sections:
                raise ValueError("schema admin panels must declare sections")
            if self.layout is None:
                self.layout = "full-width"
        else:
            if not self.component:
                raise ValueError("custom_component admin panels must declare component")
            if self.sections:
                raise ValueError("custom_component admin panels must not declare sections")
            self.layout = None
        return self


class ModuleAdminManifest(ModuleContractModel):
    """Validated admin.yaml contract.

    `hooks` declares optional Python callables that supply panel-specific data.
    Format: "module.path:function_name", e.g. "backend.admin:resolve_metrics".
    The runtime validates and stores these at load time; panel data endpoints
    resolve them on demand when a panel requests dynamic data.
    """

    schema_version: Literal["mozaiks.admin.v2"] = "mozaiks.admin.v2"
    panels: list[ModuleAdminPanel] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)

    @field_validator("hooks", mode="before")
    @classmethod
    def _hooks(cls, value: Any) -> list[str]:
        entries = _string_list(value)
        for entry in entries:
            if ":" not in entry:
                raise ValueError(
                    f"admin.yaml hook must be in 'module.path:function_name' format, got {entry!r}"
                )
            module_path, _, fn_name = entry.partition(":")
            if not module_path.strip() or not fn_name.strip():
                raise ValueError(
                    f"admin.yaml hook has empty module path or function name: {entry!r}"
                )
        return entries

    @model_validator(mode="after")
    def _unique_panel_ids(self) -> ModuleAdminManifest:
        panel_ids = [panel.id for panel in self.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("admin.yaml panels must have unique id values")
        return self


# ---------------------------------------------------------------------------
# Profile panel contract — modules/{module}/contracts/profile.yaml
# ---------------------------------------------------------------------------

_PROFILE_FIELD_TYPES = {"string", "number", "currency", "date", "boolean", "status"}
_PROFILE_PANEL_KINDS = {"metrics", "list", "component"}
# "form" is reserved for a future implementation and intentionally excluded from
# _PROFILE_PANEL_KINDS so that profile.yaml validation rejects it at load time
# rather than silently producing an unsupported-kind render in the UI.


class ModuleProfileField(ModuleContractModel):
    id: str
    label: str
    type: str = "string"

    @field_validator("id", "label", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=f"profile field {info.field_name}")

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, value: Any) -> str:
        text = str(value or "string").strip().lower()
        if text not in _PROFILE_FIELD_TYPES:
            raise ValueError(
                f"profile field type must be one of {sorted(_PROFILE_FIELD_TYPES)}, got {text!r}"
            )
        return text


class ModuleProfilePanel(ModuleContractModel):
    id: str
    title: str
    description: str | None = None
    order: int = 100
    kind: str = "metrics"
    action: str | None = None          # module action called to hydrate panel data
    component: str | None = None       # registered React component (kind=component)
    fields: list[ModuleProfileField] = Field(default_factory=list)

    @field_validator("id", "title", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=f"profile panel {info.field_name}")

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value: Any) -> str:
        text = str(value or "metrics").strip().lower()
        if text not in _PROFILE_PANEL_KINDS:
            raise ValueError(
                f"profile panel kind must be one of {sorted(_PROFILE_PANEL_KINDS)}, got {text!r}"
            )
        return text

    @field_validator("description", "action", "component", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_panel_contract(self) -> ModuleProfilePanel:
        if self.kind == "component":
            if not self.component:
                raise ValueError("component profile panels must declare 'component'")
            if self.fields:
                raise ValueError("component profile panels must not declare 'fields'")
        elif self.kind in {"metrics", "list"}:
            if not self.fields:
                raise ValueError(f"{self.kind} profile panels must declare 'fields'")
        return self


class ModuleProfileTab(ModuleContractModel):
    """A module-declared tab contribution to the social profile page."""

    id: str
    label: str
    order: int = 100
    action: str | None = None       # module action called to hydrate tab data
    component: str | None = None    # registered React component for tab body

    @field_validator("id", "label", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=f"profile tab {info.field_name}")

    @field_validator("action", "component", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_tab_contract(self) -> ModuleProfileTab:
        if not self.component and not self.action:
            raise ValueError("profile tabs must declare at least a 'component' or an 'action'")
        return self


class ModuleProfilePage(ModuleContractModel):
    """A module-declared page contribution to the profile surface (v2)."""

    id: str
    label: str
    route: str                          # relative to /me, e.g. "messages" → /me/messages
    section: Literal["overview", "platform", "social", "settings"] = "overview"
    order: int = 100
    renderer: Literal["custom_component"] = "custom_component"
    component: str
    visibility: Literal["owner_only", "public"] = "public"
    action: str | None = None           # module action called to hydrate page data
    icon: str | None = None
    description: str | None = None

    @field_validator("id", "label", "route", "component", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=f"profile page {info.field_name}")

    @field_validator("action", "icon", "description", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("order", mode="before")
    @classmethod
    def _order(cls, value: Any) -> int:
        if value in (None, ""):
            return 100
        return int(value)


class ModuleProfileManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.profile.v1", "mozaiks.profile.v2"] = "mozaiks.profile.v1"
    panels: list[ModuleProfilePanel] = Field(default_factory=list)
    tabs: list[ModuleProfileTab] = Field(default_factory=list)
    pages: list[ModuleProfilePage] = Field(default_factory=list)

    @field_validator("pages", mode="after")
    @classmethod
    def _unique_page_ids(cls, pages: list[ModuleProfilePage]) -> list[ModuleProfilePage]:
        page_ids = [p.id for p in pages]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("profile.yaml pages must have unique id values")
        return pages

    @model_validator(mode="after")
    def _unique_ids(self) -> ModuleProfileManifest:
        panel_ids = [p.id for p in self.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("profile.yaml panels must have unique id values")
        tab_ids = [t.id for t in self.tabs]
        if len(tab_ids) != len(set(tab_ids)):
            raise ValueError("profile.yaml tabs must have unique id values")
        if self.schema_version == "mozaiks.profile.v2" and not self.pages:
            logger.warning(
                "[profile] v2 manifest has no pages declared — consider adding at least one page"
            )
        return self


# ---------------------------------------------------------------------------
# User relationship contract — modules/{module}/contracts/relationships.yaml
# ---------------------------------------------------------------------------


class ModuleRelationshipProvider(ModuleContractModel):
    id: str
    label: str
    description: str | None = None
    order: int = 100
    action: str
    resource_types: list[str] = Field(default_factory=list)
    relationship_types: list[str] = Field(default_factory=list)

    @field_validator("id", "label", "action", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=f"relationship provider {info.field_name}")

    @field_validator("description", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("order", mode="before")
    @classmethod
    def _order(cls, value: Any) -> int:
        if value in (None, ""):
            return 100
        return int(value)

    @field_validator("resource_types", "relationship_types", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        return _string_list(value)

    @model_validator(mode="after")
    def _validate_provider_contract(self) -> ModuleRelationshipProvider:
        if not self.resource_types:
            raise ValueError("relationship providers must declare at least one resource_type")
        return self


class ModuleRelationshipsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.relationships.v1"] = "mozaiks.relationships.v1"
    providers: list[ModuleRelationshipProvider] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_provider_ids(self) -> ModuleRelationshipsManifest:
        provider_ids = [provider.id for provider in self.providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("relationships.yaml providers must have unique id values")
        return self


# ---------------------------------------------------------------------------
# Policy hook contract — modules/{module}/contracts/policy_hooks.yaml
# ---------------------------------------------------------------------------


class ModulePolicyHookProvider(ModuleContractModel):
    id: str
    label: str
    description: str | None = None
    order: int = 100
    hook_type: Literal["access", "classification", "decision_input", "custom"] = "custom"
    action: str
    resource_types: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    deterministic: bool = True

    @field_validator("id", "label", "action", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=f"policy hook {info.field_name}")

    @field_validator("description", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("order", mode="before")
    @classmethod
    def _order(cls, value: Any) -> int:
        if value in (None, ""):
            return 100
        return int(value)

    @field_validator("resource_types", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        return _string_list(value)

    @model_validator(mode="after")
    def _validate_hook_contract(self) -> ModulePolicyHookProvider:
        if not self.resource_types:
            raise ValueError("policy hook providers must declare at least one resource_type")
        return self


class ModulePolicyHooksManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.policy_hooks.v1"] = "mozaiks.policy_hooks.v1"
    hooks: list[ModulePolicyHookProvider] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_hook_ids(self) -> ModulePolicyHooksManifest:
        hook_ids = [hook.id for hook in self.hooks]
        if len(hook_ids) != len(set(hook_ids)):
            raise ValueError("policy_hooks.yaml hooks must have unique id values")
        return self


class ModuleRuntimeExtension(ModuleContractModel):
    kind: Literal["api_router", "startup_service"]
    entrypoint: str
    prefix: str | None = None

    @field_validator("entrypoint", mode="before")
    @classmethod
    def _entrypoint(cls, value: Any) -> str:
        text = _required_text(value, field_name="runtime_extensions.entrypoint")
        if ":" not in text:
            raise ValueError("runtime_extensions.entrypoint must use module.path:attribute syntax")
        module_path, _, attr = text.partition(":")
        module_path = module_path.strip()
        attr = attr.strip()
        if not module_path or not attr:
            raise ValueError("runtime_extensions.entrypoint must include both module path and attribute")
        if module_path.startswith(("modules.", "app.modules.", "mozaiksai.")):
            raise ValueError(
                "runtime_extensions.entrypoint must be module-local, e.g. "
                "backend.router:get_router or backend.worker:ServiceClass"
            )
        if not module_path.startswith("backend."):
            raise ValueError("runtime_extensions.entrypoint must point at a module-local backend.* file")
        rel = PurePosixPath(module_path.replace(".", "/") + ".py")
        if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
            raise ValueError("runtime_extensions.entrypoint must be a safe module-local path")
        if not attr.replace("_", "").isalnum():
            raise ValueError("runtime_extensions.entrypoint attribute must be identifier-like")
        return f"{module_path}:{attr}"

    @model_validator(mode="after")
    def _validate_extension_contract(self) -> ModuleRuntimeExtension:
        if self.kind == "startup_service" and self.prefix:
            raise ValueError("startup_service runtime extensions must not declare prefix")
        if self.kind == "api_router" and self.prefix is not None and not self.prefix.startswith("/"):
            raise ValueError("api_router runtime extension prefix must start with /")
        return self


class ModuleRuntimeExtensionsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.runtime_extensions.v1"]
    extensions: list[ModuleRuntimeExtension] = Field(default_factory=list)


class ModuleCompanionManifests(ModuleContractModel):
    events: ModuleEventsManifest | None = None
    reactions: ModuleReactionsManifest | None = None
    notifications: ModuleNotificationsManifest | None = None
    settings: ModuleSettingsManifest | None = None
    admin: ModuleAdminManifest | None = None
    profile: ModuleProfileManifest | None = None
    relationships: ModuleRelationshipsManifest | None = None
    policy_hooks: ModulePolicyHooksManifest | None = None
    runtime_extensions: ModuleRuntimeExtensionsManifest | None = None


class LoadedModule:
    """A module definition + instantiated handler, ready for registration."""

    def __init__(
        self,
        definition: ModuleDefinition,
        handler: Any,
        path: Path,
        manifests: ModuleCompanionManifests,
    ) -> None:
        self.definition = definition
        self.handler = handler
        self.path = path
        self.manifests = manifests

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def action_method_map(self) -> dict[str, str]:
        return self.definition.action_method_map

    @property
    def action_permissions_map(self) -> dict[str, list[str]]:
        return self.definition.action_permissions_map

    @property
    def action_api_surface_map(self) -> dict[str, str | None]:
        return self.definition.action_api_surface_map

    @property
    def action_schemas_map(self) -> dict[str, dict[str, dict[str, Any]]]:
        return self.definition.action_schemas_map

    @property
    def action_entitlement_map(self) -> dict[str, str | None]:
        return self.definition.action_entitlement_map

    @property
    def action_emits_map(self) -> dict[str, list[str]]:
        return self.definition.action_emits_map

    @property
    def event_payload_schemas_map(self) -> dict[str, dict[str, Any]]:
        if self.manifests.events is None:
            return {}
        return {
            event.type: dict(event.payload_schema)
            for event in self.manifests.events.events
            if event.payload_schema
        }

    def __repr__(self) -> str:
        return f"<LoadedModule name={self.name!r} handler={type(self.handler).__name__}>"


class ModuleLoadError(Exception):
    """Raised when a module cannot be loaded."""


class ModuleLoader:
    """Loads canonical module contracts from a platform bundle directory."""

    YAML_FILENAME = "module.yaml"
    CONTRACTS_DIRNAME = "contracts"
    OPTIONAL_MANIFESTS = {
        "events": ("events.yaml", ModuleEventsManifest),
        "reactions": ("reactions.yaml", ModuleReactionsManifest),
        "notifications": ("notifications.yaml", ModuleNotificationsManifest),
        "settings": ("settings.yaml", ModuleSettingsManifest),
        "admin": ("admin.yaml", ModuleAdminManifest),
        "profile": ("profile.yaml", ModuleProfileManifest),
        "relationships": ("relationships.yaml", ModuleRelationshipsManifest),
        "policy_hooks": ("policy_hooks.yaml", ModulePolicyHooksManifest),
    }

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)
        import_roots = [self._base.parent, self._base] if self._base.name == "app" else [self._base]
        for root in import_roots:
            root_text = str(root.resolve())
            if root_text not in sys.path:
                sys.path.insert(0, root_text)
        self._clear_registered_package("services")

    def discover_module_names(self) -> list[str]:
        """Return module names for every modules/*/module.yaml in the bundle."""
        modules_dir = self._base / "modules"
        if not modules_dir.exists():
            return []
        names: list[str] = []
        for child in sorted(modules_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir() and (child / self.YAML_FILENAME).exists():
                names.append(child.name)
        return names

    async def load_all(
        self,
        module_names: list[str] | None = None,
    ) -> tuple[list[LoadedModule], list[str]]:
        """Load named modules, or discover all when no list is provided.

        Returns:
            Tuple of (loaded_modules, failed_module_names).  Both lists are
            non-empty only when some but not all modules succeeded.  Callers
            that only need the module list can unpack via ``loaded, _ = ...``.
        """
        loaded: list[LoadedModule] = []
        failed: list[str] = []
        names = module_names if module_names is not None else self.discover_module_names()
        for name in names:
            try:
                mod = self.load(name)
                loaded.append(mod)
            except ModuleLoadError as exc:
                logger.error("MODULE_LOAD_FAILED: %s — %s", name, exc, exc_info=True)
                failed.append(name)
        return loaded, failed

    def load(self, name: str) -> LoadedModule:
        """Load a single canonical module by folder name."""
        module_dir = self._base / "modules" / name
        if not module_dir.exists():
            raise ModuleLoadError(f"Module directory not found: {module_dir}")

        yaml_path = module_dir / self.YAML_FILENAME
        if not yaml_path.exists():
            raise ModuleLoadError(f"module.yaml not found in {module_dir}")

        try:
            definition = ModuleDefinition.model_validate(_load_yaml_file(yaml_path))
        except Exception as exc:
            raise ModuleLoadError(f"Invalid module.yaml for {name!r}: {exc}") from exc

        if definition.name != name:
            raise ModuleLoadError(
                f"module.yaml module.id {definition.name!r} must match folder name {name!r}"
            )

        manifests = self._load_companion_manifests(module_dir, definition)
        handler_path_rel, class_name = _validate_entrypoint(definition.module.handler)
        handler_path = module_dir / handler_path_rel
        if not handler_path.exists():
            raise ModuleLoadError(f"handler entrypoint file not found: {handler_path_rel}")

        # Defense-in-depth: verify the resolved path stays within module_dir.
        # _validate_entrypoint rejects ".." segments, but this explicit check
        # also catches symlink-based escapes that static analysis cannot prevent.
        try:
            handler_path.resolve().relative_to(module_dir.resolve())
        except ValueError as exc:
            raise ModuleLoadError(
                f"handler path escapes module directory: {handler_path_rel}"
            ) from exc

        handler = self._import_handler(name, module_dir, handler_path, class_name, definition)

        if definition.module.user_data_scope:
            self._register_account_data_handler(name, module_dir)

        logger.info(
            "MODULE_LOADED: %s v%s handler=%s actions=%s user_data_scope=%s",
            name, definition.version, type(handler).__name__,
            [a.id for a in definition.actions], definition.module.user_data_scope)
        return LoadedModule(
            definition=definition,
            handler=handler,
            path=module_dir,
            manifests=manifests,
        )

    def _register_account_data_handler(self, module_id: str, module_dir: Path) -> None:
        """Import and register backend/account_data_handler.py with account_data_registry.

        Called automatically when module.yaml declares ``user_data_scope: true``.
        The handler file must export a class named ``AccountDataHandler`` (or any
        name — the loader looks for a class implementing the AccountDataHandler
        Protocol).  It is registered under ``module_id`` with the process-global
        ``account_data_registry``.

        A missing backend/account_data_handler.py raises ModuleLoadError — the
        module will not load. Generated modules must declare this file whenever
        user_data_scope=true is set.
        """
        handler_file = module_dir / "backend" / "account_data_handler.py"
        if not handler_file.exists():
            raise ModuleLoadError(
                f"module {module_id!r} declares user_data_scope=true but "
                "backend/account_data_handler.py was not found. "
                "Generate backend/account_data_handler.py implementing the "
                "AccountDataHandler protocol (delete_user_data + export_user_data), "
                "or remove user_data_scope=true from module.yaml."
            )

        try:
            import importlib.util as _ilu
            spec = _ilu.spec_from_file_location(
                f"app.modules.{module_id}.backend.account_data_handler",
                handler_file,
            )
            if spec is None or spec.loader is None:
                raise ImportError("spec_from_file_location returned None")
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]

            # Find the handler class — prefer one explicitly named AccountDataHandler,
            # fall back to any class that satisfies the Protocol.
            from mozaiksai.core.account import AccountDataHandler as _Protocol
            handler_cls = getattr(mod, "AccountDataHandler", None)
            if handler_cls is None:
                # Search for any class that implements the protocol
                import inspect as _inspect
                candidates = [
                    obj for _, obj in _inspect.getmembers(mod, _inspect.isclass)
                    if obj.__module__ == mod.__name__ and isinstance(obj, type)
                ]
                for candidate in candidates:
                    try:
                        if isinstance(candidate(db=None), _Protocol):  # type: ignore[call-arg]
                            handler_cls = candidate
                            break
                    except Exception:
                        pass

            if handler_cls is None:
                logger.warning(
                    "ACCOUNT_HANDLER_NO_CLASS: module %r backend/account_data_handler.py "
                    "found but no AccountDataHandler class discovered. "
                    "Export a class named AccountDataHandler.",
                    module_id,
                )
                return

            from mozaiksai.core.account import AccountDataHandler, account_data_registry
            account_data_registry.register(module_id, cast(type[AccountDataHandler], handler_cls))
            logger.info("ACCOUNT_HANDLER_REGISTERED: module=%s class=%s", module_id, handler_cls.__name__)

        except Exception as exc:
            logger.warning(
                "ACCOUNT_HANDLER_LOAD_ERROR: module %r backend/account_data_handler.py "
                "could not be loaded: %s",
                module_id,
                exc,
            )

    def _load_companion_manifests(
        self,
        module_dir: Path,
        definition: ModuleDefinition,
    ) -> ModuleCompanionManifests:
        parsed: dict[str, Any] = {}
        contracts_dir = module_dir / self.CONTRACTS_DIRNAME

        reactions_path = contracts_dir / "reactions.yaml"
        subscriptions_path = contracts_dir / "subscriptions.yaml"
        if subscriptions_path.exists():
            raise ModuleLoadError(
                f"contracts/subscriptions.yaml is not supported for {definition.name!r}; "
                "use contracts/reactions.yaml"
            )

        if reactions_path.exists():
            try:
                parsed["reactions"] = ModuleReactionsManifest.model_validate(_load_yaml_file(reactions_path))
            except Exception as exc:
                raise ModuleLoadError(f"Invalid reactions.yaml for {definition.name!r}: {exc}") from exc

        for key, (filename, model) in self.OPTIONAL_MANIFESTS.items():
            if key == "reactions":
                continue
            path = contracts_dir / filename
            if not path.exists():
                continue
            try:
                raw_manifest = _load_yaml_file(path)
                if key == "notifications":
                    raw_manifest = _normalize_notifications_yaml(raw_manifest)
                parsed[key] = model.model_validate(raw_manifest)  # type: ignore[attr-defined]
            except Exception as exc:
                raise ModuleLoadError(f"Invalid {filename} for {definition.name!r}: {exc}") from exc

        ext_yaml = module_dir / "runtime_extensions.yaml"
        # Warn loudly when runtime_extensions.yaml is accidentally placed inside
        # contracts/ (matching the pattern of other companion manifests but wrong
        # for runtime_extensions). It must live at the module root.
        misplaced_ext_yaml = module_dir / "contracts" / "runtime_extensions.yaml"
        if misplaced_ext_yaml.exists() and not ext_yaml.exists():
            import warnings as _warnings

            _warnings.warn(
                f"Module {definition.name!r}: runtime_extensions.yaml found at "
                f"contracts/runtime_extensions.yaml but it must be at the module "
                f"root (not inside contracts/). Extensions will NOT be loaded until "
                f"the file is moved.",
                stacklevel=2,
            )
        if ext_yaml.exists():
            try:
                parsed["runtime_extensions"] = ModuleRuntimeExtensionsManifest.model_validate(
                    _load_yaml_file(ext_yaml)
                )
            except Exception as exc:
                raise ModuleLoadError(
                    f"Invalid runtime_extensions.yaml for {definition.name!r}: {exc}"
                ) from exc

        manifests = ModuleCompanionManifests.model_validate(parsed)
        declared_events: set[str] = manifests.events.event_types if manifests.events is not None else set()
        if manifests.events is not None:
            for event in manifests.events.events:
                if event.producer != definition.name:
                    raise ModuleLoadError(
                        f"events.yaml event {event.type!r} producer {event.producer!r} "
                        f"must match module id {definition.name!r}"
                    )

        for action in definition.actions:
            for event_type in action.emits:
                if event_type not in declared_events:
                    raise ModuleLoadError(
                        f"module.yaml action {action.id!r} emits undeclared event {event_type!r}"
                    )

        self._validate_manifest_event_references(definition, manifests)
        return manifests

    def _validate_manifest_event_references(
        self,
        definition: ModuleDefinition,
        manifests: ModuleCompanionManifests,
    ) -> None:
        declared_events: set[str] = manifests.events.event_types if manifests.events is not None else set()

        if manifests.reactions is not None:
            declared_permission_ids = {permission.id for permission in definition.permissions}
            for reaction in manifests.reactions.reactions:
                event_type = str(reaction.event_type or "").strip()
                if event_type and not self._is_known_or_canonical_event(event_type, declared_events):
                    raise ModuleLoadError(
                        f"reactions.yaml references non-canonical event {event_type!r}"
                    )
                for permission_id in reaction.permissions:
                    if permission_id not in declared_permission_ids:
                        raise ModuleLoadError(
                            f"reactions.yaml reaction {reaction.id!r} references undeclared "
                            f"permission {permission_id!r}; add it to the module-level permissions block"
                        )

        if manifests.notifications is not None:
            for notification in manifests.notifications.notifications:
                event_type = str(getattr(notification, "event_type", "") or "").strip()
                if event_type and not self._is_known_or_canonical_event(event_type, declared_events):
                    raise ModuleLoadError(
                        f"notifications.yaml references non-canonical event {event_type!r}"
                    )

        # Check 6: notification reactions must reference a notification_id
        # declared in contracts/notifications.yaml.
        if manifests.reactions is not None:
            notification_reactions = [
                r for r in manifests.reactions.reactions if r.target.kind == "notification"
            ]
            if notification_reactions:
                if manifests.notifications is None:
                    raise ModuleLoadError(
                        f"reactions.yaml reaction {notification_reactions[0].id!r} has "
                        "target.kind=notification but contracts/notifications.yaml does not exist"
                    )
                declared_notification_ids = {n.id for n in manifests.notifications.notifications}
                for reaction in notification_reactions:
                    nid = reaction.target.notification_id
                    if nid and nid not in declared_notification_ids:
                        raise ModuleLoadError(
                            f"reactions.yaml reaction {reaction.id!r} target.notification_id "
                            f"{nid!r} is not declared in contracts/notifications.yaml"
                        )

    @staticmethod
    def _is_known_or_canonical_event(event_type: str, declared_events: set[str]) -> bool:
        if event_type in declared_events:
            return True
        return event_type.startswith(CANONICAL_EVENT_PREFIXES)

    def _import_handler(
        self,
        name: str,
        module_dir: Path,
        handler_path: Path,
        class_name: str,
        definition: ModuleDefinition,
    ) -> Any:
        """Import backend/handler.py and instantiate the declared handler class."""
        package_root = f"mozaiks_runtime_module_{name.replace('.', '_').replace('-', '_')}"
        relative_module = handler_path.relative_to(module_dir).with_suffix("")
        module_key = ".".join((package_root, *relative_module.parts))

        self._clear_registered_module_package(package_root)
        self._register_module_package(package_root, module_dir)

        package_name = package_root
        package_path = module_dir
        for part in relative_module.parts[:-1]:
            package_name = f"{package_name}.{part}"
            package_path = package_path / part
            self._register_module_package(package_name, package_path)

        importlib.invalidate_caches()

        spec = importlib.util.spec_from_file_location(module_key, handler_path)
        if spec is None or spec.loader is None:
            raise ModuleLoadError(f"Cannot create import spec for {handler_path}")

        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = module_key.rpartition(".")[0]
        sys.modules[module_key] = mod

        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            raise ModuleLoadError(f"Error executing handler for {name!r}: {exc}") from exc

        cls = getattr(mod, class_name, None)
        if cls is None or not inspect.isclass(cls):
            raise ModuleLoadError(
                f"Handler class {class_name!r} not found in {self._display_path(handler_path)}"
            )

        missing = [
            action.handler_method
            for action in definition.actions
            if not hasattr(cls, action.handler_method)
        ]
        if missing:
            raise ModuleLoadError(
                f"Handler class {class_name!r} is missing action method(s): {', '.join(sorted(missing))}"
            )

        try:
            return cls()
        except Exception as exc:
            raise ModuleLoadError(
                f"Failed to instantiate handler class {class_name!r} for module {name!r}: {exc}"
            ) from exc

    @staticmethod
    def _clear_registered_module_package(package_root: str) -> None:
        ModuleLoader._clear_registered_package(package_root)

    @staticmethod
    def _clear_registered_package(package_root: str) -> None:
        prefix = f"{package_root}."
        for module_name in list(sys.modules):
            if module_name == package_root or module_name.startswith(prefix):
                sys.modules.pop(module_name, None)

    @staticmethod
    def _register_module_package(package_name: str, package_path: Path) -> None:
        package = ModuleType(package_name)
        package.__file__ = str(package_path / "__init__.py")
        package.__package__ = package_name
        package.__path__ = [str(package_path)]
        sys.modules[package_name] = package

    @staticmethod
    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(path.parents[2]))
        except Exception:
            return str(path)
