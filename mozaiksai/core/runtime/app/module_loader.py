from __future__ import annotations

"""ModuleLoader — discovers and loads module handlers from the platform bundle.

A module lives under platform/modules/{name}/ and has two parts:
  1. module.yaml  — declarative definition (name, version, actions, events)
  2. handler.py   — Python class implementing the action methods

The loader reads module.yaml for metadata/validation, then imports
handler.py and instantiates the handler class so it can be registered
with ModuleExecutor.

module.yaml schema:

    name: contacts                  # Unique module identifier
    version: "1.0"
    description: CRM contacts

    # Optional — for external API modules
    external: false

    actions:
      - name: list
        type: query
        description: List all contacts
      - name: create
        type: mutation
        description: Create a contact
        emits:
          - contacts.created

    # Optional — events this module can emit
    events:
      - contacts.created
      - contacts.updated
      - contacts.deleted

handler.py convention:

    class ContactsModule:
        async def list(self, ctx, *, limit=20, status=None): ...
        async def create(self, ctx, *, name, email): ...

    # ModuleLoader looks for: a class named after the module in PascalCase
    # with "Module" suffix, OR the first class that looks like a handler.
    # You can also set MODULE_CLASS = ContactsModule at module level.
"""

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, List, Optional

import yaml
from pydantic import BaseModel, Field

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("module_loader")


# ---------------------------------------------------------------------------
# module.yaml schema
# ---------------------------------------------------------------------------

class ActionDef(BaseModel):
    name: str
    type: str = "query"             # "query" | "mutation"
    description: Optional[str] = None
    emits: List[str] = Field(default_factory=list)


class ModuleDefinition(BaseModel):
    name: str
    version: str = "1.0"
    description: Optional[str] = None
    external: bool = False
    actions: List[ActionDef] = Field(default_factory=list)
    events: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LoadedModule — result of loading one module
# ---------------------------------------------------------------------------

class LoadedModule:
    """A module definition + instantiated handler, ready for registration."""

    def __init__(self, definition: ModuleDefinition, handler: Any, path: Path) -> None:
        self.definition = definition
        self.handler = handler
        self.path = path

    @property
    def name(self) -> str:
        return self.definition.name

    def __repr__(self) -> str:
        return f"<LoadedModule name={self.name!r} handler={type(self.handler).__name__}>"


# ---------------------------------------------------------------------------
# ModuleLoader
# ---------------------------------------------------------------------------

class ModuleLoadError(Exception):
    """Raised when a module cannot be loaded."""


class ModuleLoader:
    """Loads module handlers from a platform bundle directory.

    Usage:
        loader = ModuleLoader(base_path="/path/to/platform")
        modules = await loader.load_all()  # discovers modules/*/module.yaml
        for mod in modules:
            executor.register(mod.name, mod.handler)
    """

    YAML_FILENAME = "module.yaml"
    HANDLER_FILENAME = "handler.py"

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
        """Load a single module by name.

        Raises:
            ModuleLoadError: If module.yaml is missing, invalid, or handler
                             cannot be imported.
        """
        module_dir = self._base / "modules" / name

        if not module_dir.exists():
            raise ModuleLoadError(f"Module directory not found: {module_dir}")

        # --- Load definition -------------------------------------------------
        yaml_path = module_dir / self.YAML_FILENAME
        if not yaml_path.exists():
            raise ModuleLoadError(f"module.yaml not found in {module_dir}")

        try:
            with open(yaml_path) as f:
                raw = yaml.safe_load(f)
        except Exception as exc:
            raise ModuleLoadError(f"Failed to read module.yaml for {name!r}: {exc}") from exc

        try:
            definition = ModuleDefinition.model_validate(raw)
        except Exception as exc:
            raise ModuleLoadError(f"Invalid module.yaml for {name!r}: {exc}") from exc

        # --- Load handler ----------------------------------------------------
        handler_path = module_dir / self.HANDLER_FILENAME
        if not handler_path.exists():
            raise ModuleLoadError(f"handler.py not found in {module_dir}")

        handler = self._import_handler(name, handler_path, definition)

        logger.info(
            f"MODULE_LOADED: {name!r} v{definition.version} "
            f"handler={type(handler).__name__} actions={[a.name for a in definition.actions]}"
        )
        return LoadedModule(definition=definition, handler=handler, path=module_dir)

    def _import_handler(self, name: str, handler_path: Path, definition: ModuleDefinition) -> Any:
        """Import handler.py and instantiate the handler class.

        Resolution order:
          1. Module-level attribute MODULE_CLASS
          2. Class named <PascalCase(name)>Module  (e.g. ContactsModule)
          3. Class named <PascalCase(name)>         (e.g. Contacts)
          4. First class defined in the file that has at least one action method
        """
        module_key = f"mozaiks_module_{name.replace('.', '_').replace('-', '_')}"

        spec = importlib.util.spec_from_file_location(module_key, handler_path)
        if spec is None or spec.loader is None:
            raise ModuleLoadError(f"Cannot create import spec for {handler_path}")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_key] = mod

        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            raise ModuleLoadError(f"Error executing handler.py for {name!r}: {exc}") from exc

        # Resolution order
        cls = getattr(mod, "MODULE_CLASS", None)

        if cls is None:
            pascal = _to_pascal(name)
            cls = getattr(mod, f"{pascal}Module", None) or getattr(mod, pascal, None)

        if cls is None:
            action_names = {a.name for a in definition.actions}
            for attr_name in dir(mod):
                candidate = getattr(mod, attr_name, None)
                if inspect.isclass(candidate) and any(
                    hasattr(candidate, a) for a in action_names
                ):
                    cls = candidate
                    break

        if cls is None:
            raise ModuleLoadError(
                f"No handler class found in handler.py for module {name!r}. "
                f"Define MODULE_CLASS, or a class named {_to_pascal(name)}Module."
            )

        try:
            return cls()
        except Exception as exc:
            raise ModuleLoadError(
                f"Failed to instantiate handler class {cls.__name__} for module {name!r}: {exc}"
            ) from exc


def _to_pascal(name: str) -> str:
    """Convert 'some-module' or 'some.module' to 'SomeModule'."""
    return "".join(part.capitalize() for part in name.replace(".", "-").split("-"))
