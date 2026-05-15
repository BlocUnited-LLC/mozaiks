from __future__ import annotations

"""ModuleLoader — discovers canonical Mozaiks module contracts.

A module lives under app/modules/{module_id}/ and must include:

  - module.yaml
  - events.yaml
  - subscriptions.yaml
  - notifications.yaml
  - settings.yaml
  - admin.yaml
  - backend/handler.py

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
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("module_loader")


class ModuleContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(values: Any) -> List[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("value must be a list")
    result: List[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _load_yaml_file(path: Path) -> Dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModuleLoadError(f"Failed to read {path.name}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ModuleLoadError(f"{path.name} must contain a YAML object")
    return raw


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
    display_name: Optional[str] = None
    version: str = "1.0.0"
    description: Optional[str] = None
    owner: Optional[str] = None
    visibility: Optional[str] = None
    handler: str

    @field_validator("id", "handler", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=f"module.{info.field_name}")

    @field_validator("display_name", "description", "owner", "visibility", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> Optional[str]:
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
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    permissions: List[str] = Field(default_factory=list)
    emits: List[str] = Field(default_factory=list)

    @field_validator("id", "description", "handler_method", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("permissions", "emits", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> List[str]:
        return _string_list(value)


class ModuleCapability(ModuleContractModel):
    capability_id: str
    kind: Literal["action", "workflow", "page", "transition", "hosted"]
    target: str
    title: str
    permissions: List[str] = Field(default_factory=list)
    input_schema: Optional[Dict[str, Any]] = None

    @field_validator("capability_id", "target", "title", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: Any) -> List[str]:
        return _string_list(value)


class ModuleDefinition(ModuleContractModel):
    schema_version: Literal["mozaiks.module.v1"]
    module: ModuleIdentity
    permissions: List[ModulePermission] = Field(default_factory=list)
    actions: List[ActionDef] = Field(default_factory=list)
    capabilities: List[ModuleCapability] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.module.id

    @property
    def version(self) -> str:
        return self.module.version

    @property
    def action_method_map(self) -> Dict[str, str]:
        return {action.id: action.handler_method for action in self.actions}

    @property
    def action_permissions_map(self) -> Dict[str, List[str]]:
        """Maps each action id to its declared required permission ids."""
        return {action.id: list(action.permissions) for action in self.actions}

    @property
    def action_schemas_map(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Maps each action id to its declared input/output JSON Schemas."""
        return {
            action.id: {"input": action.input_schema, "output": action.output_schema}
            for action in self.actions
        }

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> "ModuleDefinition":
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
        return self


class ModuleEvent(ModuleContractModel):
    type: str
    version: int
    description: Optional[str] = None
    producer: str
    payload_schema: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("type", "producer", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("type")
    @classmethod
    def _canonical_event_namespace(cls, value: str) -> str:
        allowed = ("domain.", "platform.", "hosted.")
        if not value.startswith(allowed):
            raise ValueError("module-published events must use domain.*, platform.*, or hosted.*")
        return value


class ModuleEventsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.events.v1"] = "mozaiks.events.v1"
    events: List[ModuleEvent] = Field(default_factory=list)

    @property
    def event_types(self) -> set[str]:
        return {event.type for event in self.events}

    @model_validator(mode="after")
    def _validate_unique_events(self) -> "ModuleEventsManifest":
        event_types = [event.type for event in self.events]
        if len(event_types) != len(set(event_types)):
            raise ValueError("events.yaml events must have unique type values")
        return self


class ModuleSubscriptionsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.subscriptions.v1"] = "mozaiks.subscriptions.v1"
    subscriptions: List[Dict[str, Any]] = Field(default_factory=list)


class ModuleNotificationsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.notifications.v1"] = "mozaiks.notifications.v1"
    notifications: List[Dict[str, Any]] = Field(default_factory=list)


class ModuleSettingsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.settings.v1"] = "mozaiks.settings.v1"
    settings: List[Dict[str, Any]] = Field(default_factory=list)
    features: List[Dict[str, Any]] = Field(default_factory=list)


class ModuleAdminPanel(ModuleContractModel):
    id: str
    label: str
    description: Optional[str] = None
    page: str  # references a page id declared in app/admin/admin_registry.yaml
    order: int = 0
    renderer: Literal["schema", "custom_component"] = "schema"
    layout: Optional[Literal["grid", "sidebar", "full-width", "split"]] = None
    sections: List[Dict[str, Any]] = Field(default_factory=list)
    component: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)

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
    def _optional(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: Any) -> List[str]:
        return _string_list(value)

    @field_validator("sections", mode="before")
    @classmethod
    def _sections(cls, value: Any) -> List[Dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("sections must be a list")
        result: List[Dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("sections entries must be objects")
            result.append(item)
        return result

    @model_validator(mode="after")
    def _validate_renderer_contract(self) -> "ModuleAdminPanel":
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
    panels: List[ModuleAdminPanel] = Field(default_factory=list)
    hooks: List[str] = Field(default_factory=list)

    @field_validator("hooks", mode="before")
    @classmethod
    def _hooks(cls, value: Any) -> List[str]:
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
    def _unique_panel_ids(self) -> "ModuleAdminManifest":
        panel_ids = [panel.id for panel in self.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("admin.yaml panels must have unique id values")
        return self


class ModuleRuntimeExtension(ModuleContractModel):
    kind: Literal["api_router", "startup_service"]
    entrypoint: str
    prefix: Optional[str] = None

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
    def _validate_extension_contract(self) -> "ModuleRuntimeExtension":
        if self.kind == "startup_service" and self.prefix:
            raise ValueError("startup_service runtime extensions must not declare prefix")
        if self.kind == "api_router" and self.prefix is not None and not self.prefix.startswith("/"):
            raise ValueError("api_router runtime extension prefix must start with /")
        return self


class ModuleRuntimeExtensionsManifest(ModuleContractModel):
    schema_version: Literal["mozaiks.runtime_extensions.v1"]
    extensions: List[ModuleRuntimeExtension] = Field(default_factory=list)


class ModuleCompanionManifests(ModuleContractModel):
    events: Optional[ModuleEventsManifest] = None
    subscriptions: Optional[ModuleSubscriptionsManifest] = None
    notifications: Optional[ModuleNotificationsManifest] = None
    settings: Optional[ModuleSettingsManifest] = None
    admin: Optional[ModuleAdminManifest] = None
    runtime_extensions: Optional[ModuleRuntimeExtensionsManifest] = None


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
    def action_method_map(self) -> Dict[str, str]:
        return self.definition.action_method_map

    @property
    def action_permissions_map(self) -> Dict[str, List[str]]:
        return self.definition.action_permissions_map

    @property
    def action_schemas_map(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        return self.definition.action_schemas_map

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
        "subscriptions": ("subscriptions.yaml", ModuleSubscriptionsManifest),
        "notifications": ("notifications.yaml", ModuleNotificationsManifest),
        "settings": ("settings.yaml", ModuleSettingsManifest),
        "admin": ("admin.yaml", ModuleAdminManifest),
    }

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)

    def discover_module_names(self) -> List[str]:
        """Return module names for every modules/*/module.yaml in the bundle."""
        modules_dir = self._base / "modules"
        if not modules_dir.exists():
            return []
        names: List[str] = []
        for child in sorted(modules_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir() and (child / self.YAML_FILENAME).exists():
                names.append(child.name)
        return names

    async def load_all(self, module_names: Optional[List[str]] = None) -> List[LoadedModule]:
        """Load named modules, or discover all when no list is provided."""
        loaded = []
        names = module_names if module_names is not None else self.discover_module_names()
        for name in names:
            try:
                mod = self.load(name)
                loaded.append(mod)
            except ModuleLoadError as exc:
                logger.error(f"MODULE_LOAD_FAILED: {name} — {exc}")
        return loaded

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

        handler = self._import_handler(name, module_dir, handler_path, class_name, definition)

        logger.info(
            f"MODULE_LOADED: {name!r} v{definition.version} "
            f"handler={type(handler).__name__} actions={[a.id for a in definition.actions]}"
        )
        return LoadedModule(
            definition=definition,
            handler=handler,
            path=module_dir,
            manifests=manifests,
        )

    def _load_companion_manifests(
        self,
        module_dir: Path,
        definition: ModuleDefinition,
    ) -> ModuleCompanionManifests:
        parsed: Dict[str, Any] = {}
        contracts_dir = module_dir / self.CONTRACTS_DIRNAME
        for key, (filename, model) in self.OPTIONAL_MANIFESTS.items():
            path = contracts_dir / filename
            if not path.exists():
                continue
            try:
                parsed[key] = model.model_validate(_load_yaml_file(path))
            except Exception as exc:
                raise ModuleLoadError(f"Invalid {filename} for {definition.name!r}: {exc}") from exc

        ext_yaml = module_dir / "runtime_extensions.yaml"
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

        if manifests.subscriptions is not None:
            for subscription in manifests.subscriptions.subscriptions:
                if not isinstance(subscription, dict):
                    raise ModuleLoadError("subscriptions.yaml entries must be objects")
                event_type = str(subscription.get("event_type") or "").strip()
                if event_type and not self._is_known_or_canonical_event(event_type, declared_events):
                    raise ModuleLoadError(
                        f"subscriptions.yaml references non-canonical event {event_type!r}"
                    )

        if manifests.notifications is not None:
            for notification in manifests.notifications.notifications:
                if not isinstance(notification, dict):
                    raise ModuleLoadError("notifications.yaml entries must be objects")
                event_type = str(notification.get("event_type") or "").strip()
                if event_type and not self._is_known_or_canonical_event(event_type, declared_events):
                    raise ModuleLoadError(
                        f"notifications.yaml references non-canonical event {event_type!r}"
                    )

    @staticmethod
    def _is_known_or_canonical_event(event_type: str, declared_events: set[str]) -> bool:
        if event_type in declared_events:
            return True
        return event_type.startswith(("domain.", "platform.", "hosted."))

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
