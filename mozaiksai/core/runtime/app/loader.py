from __future__ import annotations

"""AppLoader — loads app.json and discovers bundle-owned runtime parts.

The app manifest owns product intent and startup metadata. Runtime composition
is discovered from owner manifests:
  - workflows/* directories
  - modules/*/module.yaml
  - ui/pages/*.yaml or ui/pages/*/page.yaml

Workflow loading is handled by the existing WorkflowManager (unchanged).

Usage:
    result = await AppLoader.load("/path/to/platform")
    app_def = result.definition          # AppDefinition
    modules  = result.modules            # List[LoadedModule]
    mode     = app_def.execution_mode    # ExecutionMode.AI_ONLY / FULL / etc.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from logs.logging_config import get_workflow_logger
from mozaiksai.core.runtime.app.definition import AppDefinition
from mozaiksai.core.runtime.app.module_loader import LoadedModule, ModuleLoader
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsConfig,
    SubscriptionsLoadError,
    load_subscriptions_config,
)
from mozaiksai.core.runtime.persistence.intent_loader import (
    DataContractLoadError,
    index_data_contract_by_entity,
    load_data_contract,
)
from mozaiksai.core.workflow.paths import candidate_app_workflows_roots

logger = get_workflow_logger("app_loader")


class AppLoadError(Exception):
    """Raised when the app manifest cannot be loaded or is invalid."""


@dataclass
class AppLoadResult:
    """Result of AppLoader.load().

    Attributes:
        definition:           Parsed AppDefinition from app.json and discovered owners
        modules:              Loaded module handlers
        data_contract:        Parsed data contract, or None
        data_entities_by_key: Data entities indexed by (module_id, entity_name)
        subscriptions_config: Parsed subscriptions config, or None for non-SaaS apps
    """
    definition: AppDefinition
    modules: list[LoadedModule] = field(default_factory=list)
    data_contract: dict[str, Any] | None = None
    data_entities_by_key: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    subscriptions_config: SubscriptionsConfig | None = None


class AppLoader:
    """Loads app-level metadata and discovers modules/pages/workflows.

    Also loads module handlers when modules are present.
    Workflow loading remains handled by WorkflowManager.
    """

    APP_JSON_NAME = "app.json"

    @classmethod
    async def load(cls, path: str = ".") -> AppLoadResult:
        """Load app metadata and any discovered modules from a bundle directory.

        Args:
            path: Root directory of the platform bundle.

        Returns:
            AppLoadResult with parsed definition and loaded modules.

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
        module_loader = ModuleLoader(base_path=str(base_path))
        module_names = module_loader.discover_module_names()
        workflow_names = cls._discover_workflow_names(base_path)
        page_names = cls._discover_page_names(base_path)

        app_def_raw: dict[str, Any] = {
            "name": raw.get("appName") or raw.get("name") or base_path.name,
            "version": raw.get("version") or "1.0",
            "description": raw.get("description"),
            "workflows": [{"name": name} for name in workflow_names],
            "modules": [{"name": name} for name in module_names],
            "pages": [{"name": name} for name in page_names],
            "config": raw,
        }

        try:
            app_def = AppDefinition.model_validate(app_def_raw)
        except ValidationError as exc:
            raise AppLoadError(f"Invalid app.json/discovered bundle: {exc}") from exc

        try:
            data_contract = load_data_contract(base_path)
            data_entities_by_key = index_data_contract_by_entity(data_contract)
        except DataContractLoadError as exc:
            raise AppLoadError(f"Invalid data/contract.json: {exc}") from exc

        subscriptions_config: SubscriptionsConfig | None = None
        try:
            subscriptions_config = load_subscriptions_config(base_path)
            if subscriptions_config is not None:
                logger.info(
                    f"SUBSCRIPTIONS_LOADED: {len(subscriptions_config.plans)} plans "
                    f"({[p.plan_id for p in subscriptions_config.plans]})"
                )
        except SubscriptionsLoadError as exc:
            logger.warning(
                f"SUBSCRIPTIONS_CONFIG_INVALID: {exc} — entitlement enforcement disabled"
            )

        logger.info(
            f"APP_LOADED: name={app_def.name!r} version={app_def.version!r} "
            f"mode={app_def.execution_mode.value} "
            f"workflows={len(app_def.workflows)} modules={len(app_def.modules)}"
        )

        loaded_modules: list[LoadedModule] = []
        if module_names:
            loaded_modules = await module_loader.load_all(module_names)
            logger.info(
                f"MODULES_LOADED: {len(loaded_modules)}/{len(module_names)} "
                f"({[m.name for m in loaded_modules]})"
            )

        return AppLoadResult(
            definition=app_def,
            modules=loaded_modules,
            data_contract=data_contract,
            data_entities_by_key=data_entities_by_key,
            subscriptions_config=subscriptions_config,
        )

    @classmethod
    def _discover_workflow_names(cls, base_path: Path) -> list[str]:
        workflows_dir = next(
            (root for root in candidate_app_workflows_roots(base_path) if root.exists()),
            candidate_app_workflows_roots(base_path)[0],
        )
        if not workflows_dir.exists():
            return []
        names: list[str] = []
        for child in sorted(workflows_dir.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.name == "extended_orchestration":
                continue
            if any((child / filename).exists() for filename in ("agents.yaml", "orchestrator.yaml")):
                names.append(child.name)
        return names

    @classmethod
    def _discover_page_names(cls, base_path: Path) -> list[str]:
        pages_dir = base_path / "ui" / "pages"
        if not pages_dir.exists():
            return []
        names: list[str] = []
        for child in sorted(pages_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.is_file() and child.suffix.lower() in {".yaml", ".yml"}:
                names.append(child.stem)
            elif child.is_dir() and ((child / "page.yaml").exists() or (child / "page.yml").exists()):
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
