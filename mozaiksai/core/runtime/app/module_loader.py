from __future__ import annotations

"""OperationLoader — discovers and loads operation handlers from the platform bundle.

An operation lives under platform/operations/{name}/ and has two parts:
  1. operation.yaml  — declarative definition (name, version, actions, events)
  2. handler.py      — Python class implementing the action methods

The loader reads operation.yaml for metadata/validation, then imports
handler.py and instantiates the handler class so it can be registered
with OperationExecutor.

operation.yaml schema:

    name: contacts                  # Unique operation identifier
    version: "1.0"
    description: CRM contacts

    # Optional — for external API operations
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

    # Optional — events this operation can emit
    events:
      - contacts.created
      - contacts.updated
      - contacts.deleted

handler.py convention:

    class ContactsOperation:
        async def list(self, ctx, *, limit=20, status=None): ...
        async def create(self, ctx, *, name, email): ...

    # OperationLoader looks for: a class named after the operation in PascalCase
    # with "Operation" suffix, OR the first class that looks like a handler.
    # You can also set OPERATION_CLASS = ContactsOperation at module level.
"""

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, List, Optional

import yaml
from pydantic import BaseModel, Field

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("operation_loader")


# ---------------------------------------------------------------------------
# operation.yaml schema
# ---------------------------------------------------------------------------

class ActionDef(BaseModel):
    name: str
    type: str = "query"             # "query" | "mutation"
    description: Optional[str] = None
    emits: List[str] = Field(default_factory=list)


class OperationDefinition(BaseModel):
    name: str
    version: str = "1.0"
    description: Optional[str] = None
    external: bool = False
    actions: List[ActionDef] = Field(default_factory=list)
    events: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LoadedOperation — result of loading one operation
# ---------------------------------------------------------------------------

class LoadedOperation:
    """An operation definition + instantiated handler, ready for registration."""

    def __init__(self, definition: OperationDefinition, handler: Any, path: Path) -> None:
        self.definition = definition
        self.handler = handler
        self.path = path

    @property
    def name(self) -> str:
        return self.definition.name

    def __repr__(self) -> str:
        return f"<LoadedOperation name={self.name!r} handler={type(self.handler).__name__}>"


# ---------------------------------------------------------------------------
# OperationLoader
# ---------------------------------------------------------------------------

class OperationLoadError(Exception):
    """Raised when an operation cannot be loaded."""


class OperationLoader:
    """Loads operation handlers from a platform bundle directory.

    Usage:
        loader = OperationLoader(base_path="/path/to/platform")
        ops = await loader.load_all()  # discovers operations/*/operation.yaml
        for op in ops:
            executor.register(op.name, op.handler)
    """

    YAML_FILENAME = "operation.yaml"
    HANDLER_FILENAME = "handler.py"

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)

    def discover_operation_names(self) -> List[str]:
        """Return operation names for every operations/*/operation.yaml in the bundle."""
        operations_dir = self._base / "operations"
        if not operations_dir.exists():
            return []
        names: List[str] = []
        for child in sorted(operations_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir() and (child / self.YAML_FILENAME).exists():
                names.append(child.name)
        return names

    async def load_all(self, operation_names: Optional[List[str]] = None) -> List[LoadedOperation]:
        """Load named operations, or discover all when no list is provided."""
        loaded = []
        names = operation_names if operation_names is not None else self.discover_operation_names()
        for name in names:
            try:
                op = self.load(name)
                loaded.append(op)
            except OperationLoadError as exc:
                logger.error(f"OPERATION_LOAD_FAILED: {name} — {exc}")
        return loaded

    def load(self, name: str) -> LoadedOperation:
        """Load a single operation by name.

        Raises:
            OperationLoadError: If operation.yaml is missing, invalid, or handler
                                cannot be imported.
        """
        operation_dir = self._base / "operations" / name

        if not operation_dir.exists():
            raise OperationLoadError(f"Operation directory not found: {operation_dir}")

        # --- Load definition -------------------------------------------------
        yaml_path = operation_dir / self.YAML_FILENAME
        if not yaml_path.exists():
            raise OperationLoadError(f"operation.yaml not found in {operation_dir}")

        try:
            with open(yaml_path) as f:
                raw = yaml.safe_load(f)
        except Exception as exc:
            raise OperationLoadError(f"Failed to read operation.yaml for {name!r}: {exc}") from exc

        try:
            definition = OperationDefinition.model_validate(raw)
        except Exception as exc:
            raise OperationLoadError(f"Invalid operation.yaml for {name!r}: {exc}") from exc

        # --- Load handler ----------------------------------------------------
        handler_path = operation_dir / self.HANDLER_FILENAME
        if not handler_path.exists():
            raise OperationLoadError(f"handler.py not found in {operation_dir}")

        handler = self._import_handler(name, handler_path, definition)

        logger.info(
            f"OPERATION_LOADED: {name!r} v{definition.version} "
            f"handler={type(handler).__name__} actions={[a.name for a in definition.actions]}"
        )
        return LoadedOperation(definition=definition, handler=handler, path=operation_dir)

    def _import_handler(self, name: str, handler_path: Path, definition: OperationDefinition) -> Any:
        """Import handler.py and instantiate the handler class.

        Resolution order:
          1. Module-level attribute OPERATION_CLASS
          2. Class named <PascalCase(name)>Operation  (e.g. ContactsOperation)
          3. Class named <PascalCase(name)>            (e.g. Contacts)
          4. First class defined in the file that has at least one action method
        """
        module_key = f"mozaiks_operation_{name.replace('.', '_').replace('-', '_')}"

        spec = importlib.util.spec_from_file_location(module_key, handler_path)
        if spec is None or spec.loader is None:
            raise OperationLoadError(f"Cannot create import spec for {handler_path}")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_key] = mod

        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            raise OperationLoadError(f"Error executing handler.py for {name!r}: {exc}") from exc

        # Resolution order
        cls = getattr(mod, "OPERATION_CLASS", None)

        if cls is None:
            pascal = _to_pascal(name)
            cls = getattr(mod, f"{pascal}Operation", None) or getattr(mod, pascal, None)

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
            raise OperationLoadError(
                f"No handler class found in handler.py for operation {name!r}. "
                f"Define OPERATION_CLASS, or a class named {_to_pascal(name)}Operation."
            )

        try:
            return cls()
        except Exception as exc:
            raise OperationLoadError(
                f"Failed to instantiate handler class {cls.__name__} for operation {name!r}: {exc}"
            ) from exc


def _to_pascal(name: str) -> str:
    """Convert 'some-operation' or 'some.operation' to 'SomeOperation'."""
    return "".join(part.capitalize() for part in name.replace(".", "-").split("-"))
