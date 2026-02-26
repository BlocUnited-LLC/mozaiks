"""Settings dispatcher — YAML-driven user settings management.

Reads ``settings.yaml`` (or ``settings/*.yaml``) from a workflow directory,
validates input, stores values via the :class:`SettingsBackend` port, and emits
``settings.updated`` business events.

Layer: ``core.events`` (shared runtime).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mozaiks.contracts.ports.business import SettingsBackend, UpdateResult
from mozaiks.core.events.dispatcher import BusinessEventDispatcher
from mozaiks.core.workflows.yaml_loader import ModularYAMLLoader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config data classes
# ---------------------------------------------------------------------------

@dataclass
class FieldConfig:
    """A single user-configurable field."""

    name: str
    type: str
    label: str
    description: str = ""
    default: Any = None
    required: bool = False
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    max_length: int | None = None
    pattern: str | None = None
    options: list[dict[str, Any]] = field(default_factory=list)
    requires_feature: str | None = None
    depends_on: dict[str, Any] | None = None
    sensitive: bool = False


@dataclass
class GroupConfig:
    """A settings group (e.g. "preferences", "ai_behavior")."""

    id: str
    label: str
    description: str = ""
    requires_feature: str | None = None
    fields: dict[str, FieldConfig] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class SettingsDispatcher:
    """Manages user settings based on YAML declarations."""

    def __init__(
        self,
        workflow_name: str,
        backend: SettingsBackend,
        *,
        workflow_dir: Path | None = None,
        business_dispatcher: BusinessEventDispatcher | None = None,
        feature_checker: Any | None = None,
    ) -> None:
        self.workflow_name = workflow_name
        self._backend = backend
        self._business = business_dispatcher or BusinessEventDispatcher.get_instance()
        self._feature_checker = feature_checker  # callable(plan, feature) -> bool
        self._groups: dict[str, GroupConfig] = {}

        if workflow_dir is not None:
            self._load(workflow_dir)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, workflow_dir: Path) -> None:
        loader = ModularYAMLLoader(workflow_dir)
        raw = loader.load_section("settings")
        entries = raw.get("settings", raw)

        for group_id, group_data in entries.items():
            if not isinstance(group_data, dict):
                continue

            fields: dict[str, FieldConfig] = {}
            for field_id, field_data in group_data.get("fields", {}).items():
                if not isinstance(field_data, dict):
                    continue
                fields[field_id] = FieldConfig(
                    name=field_id,
                    type=field_data.get("type", "string"),
                    label=field_data.get("label", field_id),
                    description=field_data.get("description", ""),
                    default=field_data.get("default"),
                    required=field_data.get("required", False),
                    min_value=field_data.get("min_value"),
                    max_value=field_data.get("max_value"),
                    step=field_data.get("step"),
                    max_length=field_data.get("max_length"),
                    pattern=field_data.get("pattern"),
                    options=field_data.get("options", []),
                    requires_feature=field_data.get("requires_feature"),
                    depends_on=field_data.get("depends_on"),
                    sensitive=field_data.get("sensitive", False),
                )

            self._groups[group_id] = GroupConfig(
                id=group_id,
                label=group_data.get("label", group_id),
                description=group_data.get("description", ""),
                requires_feature=group_data.get("requires_feature"),
                fields=fields,
            )

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    async def get_settings_schema(
        self,
        user_id: str,
        user_plan: str,
    ) -> dict[str, Any]:
        """Return settings schema filtered by *user_plan* features."""
        schema: dict[str, Any] = {"groups": []}

        for group in self._groups.values():
            if group.requires_feature and not self._has_feature(user_plan, group.requires_feature):
                continue

            group_schema: dict[str, Any] = {
                "id": group.id,
                "label": group.label,
                "description": group.description,
                "fields": [],
            }

            for field_cfg in group.fields.values():
                if field_cfg.requires_feature and not self._has_feature(
                    user_plan, field_cfg.requires_feature
                ):
                    continue

                group_schema["fields"].append({
                    "id": field_cfg.name,
                    "type": field_cfg.type,
                    "label": field_cfg.label,
                    "description": field_cfg.description,
                    "default": field_cfg.default,
                    "required": field_cfg.required,
                    "min_value": field_cfg.min_value,
                    "max_value": field_cfg.max_value,
                    "step": field_cfg.step,
                    "max_length": field_cfg.max_length,
                    "pattern": field_cfg.pattern,
                    "options": field_cfg.options,
                    "sensitive": field_cfg.sensitive,
                })

            if group_schema["fields"]:
                schema["groups"].append(group_schema)

        return schema

    # ------------------------------------------------------------------
    # Get / Set
    # ------------------------------------------------------------------

    async def get_settings_values(self, user_id: str) -> dict[str, Any]:
        """Return current settings, applying defaults for missing values."""
        stored = await self._backend.get_settings(user_id, self.workflow_name)

        values: dict[str, Any] = {}
        for group in self._groups.values():
            for field_id, field_cfg in group.fields.items():
                if field_id in stored:
                    values[field_id] = stored[field_id]
                else:
                    values[field_id] = field_cfg.default

        return values

    async def update_settings(
        self,
        user_id: str,
        updates: dict[str, Any],
    ) -> UpdateResult:
        """Validate and persist *updates* for *user_id*."""
        errors: dict[str, str] = {}

        for field_id, value in updates.items():
            field_cfg = self._get_field(field_id)
            if not field_cfg:
                errors[field_id] = "Unknown field"
                continue
            error = self._validate_field(field_cfg, value)
            if error:
                errors[field_id] = error

        if errors:
            return UpdateResult(success=False, errors=errors)

        await self._backend.update_settings(user_id, self.workflow_name, updates)

        await self._business.emit(
            "settings.updated",
            {
                "user_id": user_id,
                "workflow_name": self.workflow_name,
                "fields": list(updates.keys()),
            },
        )

        return UpdateResult(success=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_field(self, field_id: str) -> FieldConfig | None:
        for group in self._groups.values():
            if field_id in group.fields:
                return group.fields[field_id]
        return None

    def _has_feature(self, plan: str, feature: str) -> bool:
        if self._feature_checker is not None:
            return self._feature_checker(plan, feature)
        return True  # permissive default when no checker wired

    @staticmethod
    def _validate_field(cfg: FieldConfig, value: Any) -> str | None:
        """Return an error string if *value* is invalid, else ``None``."""
        if cfg.type == "boolean":
            if not isinstance(value, bool):
                return "Expected boolean"

        elif cfg.type in ("number", "slider"):
            try:
                num = float(value)
            except (ValueError, TypeError):
                return "Expected number"
            if cfg.min_value is not None and num < cfg.min_value:
                return f"Must be >= {cfg.min_value}"
            if cfg.max_value is not None and num > cfg.max_value:
                return f"Must be <= {cfg.max_value}"

        elif cfg.type == "string":
            if not isinstance(value, str):
                return "Expected string"
            if cfg.max_length is not None and len(value) > cfg.max_length:
                return f"Max length is {cfg.max_length}"
            if cfg.pattern:
                if not re.match(cfg.pattern, value):
                    return f"Must match pattern {cfg.pattern}"

        elif cfg.type == "select":
            valid_values = [o.get("value") for o in cfg.options]
            if value not in valid_values:
                return f"Must be one of {valid_values}"

        elif cfg.type == "secret":
            if not isinstance(value, str):
                return "Expected string"

        return None

    @property
    def groups(self) -> dict[str, GroupConfig]:
        """Expose parsed groups for introspection / testing."""
        return self._groups
