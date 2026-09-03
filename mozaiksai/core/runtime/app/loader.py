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
from mozaiksai.core.runtime.app.page_schema import (
    AppPageSchema,
    PageSchemaValidationError,
    build_page_action_index,
    build_page_action_index_from_module_contracts,
    discover_page_schema_paths,
    load_app_page_schemas,
)
from mozaiksai.core.runtime.app.provenance import (
    AppProvenance,
    AppProvenanceLoadError,
    load_app_provenance,
)
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
        provenance:           Parsed app provenance, or None when not declared
        page_schemas:         Validated declarative page schemas indexed by page name
        failed_module_names:  Names of modules that failed to load — empty on full success
    """
    definition: AppDefinition
    modules: list[LoadedModule] = field(default_factory=list)
    data_contract: dict[str, Any] | None = None
    data_entities_by_key: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    subscriptions_config: SubscriptionsConfig | None = None
    provenance: AppProvenance | None = None
    page_schemas: dict[str, AppPageSchema] = field(default_factory=dict)
    failed_module_names: list[str] = field(default_factory=list)


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

        try:
            provenance = load_app_provenance(base_path)
        except AppProvenanceLoadError as exc:
            raise AppLoadError(f"Invalid provenance.yaml: {exc}") from exc

        # Fail closed: an app that DECLARES a subscription contract must load
        # it or not load at all. Downgrading an invalid present config to
        # subscriptions_config=None would wire NoOpEntitlementAdapter and
        # silently grant every entitlement gate. Only an absent file means a
        # valid non-SaaS app. load_subscriptions_config remains the sole
        # schema authority (v1 and v2 both accepted, unchanged).
        subscriptions_config: SubscriptionsConfig | None = None
        try:
            subscriptions_config = load_subscriptions_config(base_path)
        except SubscriptionsLoadError as exc:
            raise AppLoadError(f"Invalid config/subscriptions.yaml: {exc}") from exc
        if subscriptions_config is not None:
            logger.info(
                "SUBSCRIPTIONS_LOADED: schema=%s root_plans=%s products=%s",
                subscriptions_config.schema_version,
                [p.plan_id for p in subscriptions_config.plans],
                [p.product_id for p in subscriptions_config.products])

        logger.info(
            "APP_LOADED: name=%s version=%s mode=%s workflows=%s modules=%s",
            app_def.name, app_def.version, app_def.execution_mode.value,
            len(app_def.workflows), len(app_def.modules))

        loaded_modules: list[LoadedModule] = []
        failed_module_names: list[str] = []
        if module_names:
            loaded_modules, failed_module_names = await module_loader.load_all(module_names)
            if failed_module_names:
                logger.warning(
                    "MODULE_LOAD_PARTIAL: %d/%d failed: %s — platform degraded for those modules",
                    len(failed_module_names),
                    len(module_names),
                    ", ".join(sorted(failed_module_names)),
                )
            else:
                logger.info(
                    "MODULES_LOADED: %d/%d (%s)",
                    len(loaded_modules),
                    len(module_names),
                    ", ".join(m.name for m in loaded_modules),
                )

        try:
            action_index = build_page_action_index_from_module_contracts(base_path)
            action_index.update(build_page_action_index(loaded_modules))
            page_schemas = load_app_page_schemas(
                base_path,
                action_index=action_index,
            )
        except PageSchemaValidationError as exc:
            formatted = "; ".join(
                f"{diagnostic.location}: {diagnostic.code}"
                for diagnostic in exc.diagnostics
            )
            raise AppLoadError(f"Invalid page schema: {formatted}") from exc

        return AppLoadResult(
            definition=app_def,
            modules=loaded_modules,
            data_contract=data_contract,
            data_entities_by_key=data_entities_by_key,
            subscriptions_config=subscriptions_config,
            provenance=provenance,
            page_schemas=page_schemas,
            failed_module_names=failed_module_names,
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
        return list(discover_page_schema_paths(base_path))

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
