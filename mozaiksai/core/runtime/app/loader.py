from __future__ import annotations

"""AppLoader — loads app.json and discovers bundle-owned runtime parts.

The app manifest owns product intent and startup metadata. Runtime composition
is discovered from owner manifests:
  - workflows/* directories
  - operations/*/operation.yaml
  - pages/*.yaml or pages/*/page.yaml

Workflow loading is handled by the existing WorkflowManager (unchanged).

Usage:
    result = await AppLoader.load("/path/to/platform")
    app_def    = result.definition          # AppDefinition
    operations = result.operations          # List[LoadedOperation]
    mode       = app_def.execution_mode     # ExecutionMode.AI_ONLY / FULL / etc.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from pydantic import ValidationError

from mozaiksai.core.runtime.app.definition import AppDefinition
from mozaiksai.core.runtime.app.module_loader import LoadedOperation, OperationLoader
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("app_loader")


class AppLoadError(Exception):
    """Raised when the app manifest cannot be loaded or is invalid."""


@dataclass
class AppLoadResult:
    """Result of AppLoader.load().

    Attributes:
        definition: Parsed AppDefinition from app.json and discovered owners
        operations: Loaded operation handlers
    """
    definition: AppDefinition
    operations: List[LoadedOperation] = field(default_factory=list)


class AppLoader:
    """Loads app-level metadata and discovers operations/pages/workflows.

    Also loads operation handlers when operations are present.
    Workflow loading remains handled by WorkflowManager.
    """

    APP_JSON_NAME = "app.json"

    @classmethod
    async def load(cls, path: str = ".") -> AppLoadResult:
        """Load app metadata and any discovered operations from a bundle directory.

        Args:
            path: Root directory of the platform bundle.

        Returns:
            AppLoadResult with parsed definition and loaded operations.

        Raises:
            AppLoadError: If app.json is missing, unparseable, or invalid.
        """
        base_path = Path(path)
        app_json_path = base_path / cls.APP_JSON_NAME

        if not app_json_path.exists():
            raise AppLoadError(f"app.json not found in {path}")

        try:
            raw = json.loads(app_json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise AppLoadError(f"Failed to read app.json: {exc}") from exc

        if not isinstance(raw, dict):
            raise AppLoadError("app.json must be a JSON object")

        raw = cls._resolve_env_vars(raw)
        operation_loader = OperationLoader(base_path=str(base_path))
        operation_names = operation_loader.discover_operation_names()
        workflow_names = cls._discover_workflow_names(base_path)
        page_names = cls._discover_page_names(base_path)

        app_def_raw: Dict[str, Any] = {
            "name": raw.get("appName") or raw.get("name") or base_path.name,
            "version": raw.get("version") or "1.0",
            "description": raw.get("description"),
            "workflows": [{"name": name} for name in workflow_names],
            "operations": [{"name": name} for name in operation_names],
            "pages": [{"name": name} for name in page_names],
            "config": raw,
        }

        try:
            app_def = AppDefinition.model_validate(app_def_raw)
        except ValidationError as exc:
            raise AppLoadError(f"Invalid app.json/discovered bundle: {exc}") from exc

        logger.info(
            f"APP_LOADED: name={app_def.name!r} version={app_def.version!r} "
            f"mode={app_def.execution_mode.value} "
            f"workflows={len(app_def.workflows)} operations={len(app_def.operations)}"
        )

        loaded_operations: List[LoadedOperation] = []
        if operation_names:
            loaded_operations = await operation_loader.load_all(operation_names)
            logger.info(
                f"OPERATIONS_LOADED: {len(loaded_operations)}/{len(operation_names)} "
                f"({[op.name for op in loaded_operations]})"
            )

        return AppLoadResult(definition=app_def, operations=loaded_operations)

    @classmethod
    def _discover_workflow_names(cls, base_path: Path) -> List[str]:
        workflows_dir = base_path / "workflows"
        if not workflows_dir.exists():
            return []
        names: List[str] = []
        for child in sorted(workflows_dir.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.name == "extended_orchestration":
                continue
            if any((child / filename).exists() for filename in ("agents.yaml", "orchestrator.yaml")):
                names.append(child.name)
        return names

    @classmethod
    def _discover_page_names(cls, base_path: Path) -> List[str]:
        pages_dir = base_path / "pages"
        if not pages_dir.exists():
            return []
        names: List[str] = []
        for child in sorted(pages_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.is_file() and child.suffix.lower() in {".yaml", ".yml"}:
                names.append(child.stem)
            elif child.is_dir() and (child / "page.yaml").exists():
                names.append(child.name)
        return names

    @classmethod
    def _resolve_env_vars(cls, content: Any) -> Any:
        """Resolve ${VAR_NAME} references to environment variable values."""
        if isinstance(content, str):
            pattern = r"\$\{([^}]+)\}"
            for match in re.findall(pattern, content):
                content = content.replace(f"${{{match}}}", os.environ.get(match, ""))
            return content
        if isinstance(content, dict):
            return {k: cls._resolve_env_vars(v) for k, v in content.items()}
        if isinstance(content, list):
            return [cls._resolve_env_vars(v) for v in content]
        return content
