"""Modular YAML loader for declarative workflow configurations.

Supports both *monolithic* (single ``notifications.yaml``) and *modular*
(``notifications/lifecycle.yaml``, ``notifications/custom.yaml``, …) layouts.

Layer: ``core.workflows`` (shared runtime).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ModularYAMLLoader:
    """Auto-detecting YAML loader for ``workflows/<workflow>/`` directories.

    For each config section (``notifications``, ``subscription``, ``settings``),
    the loader checks:

    1. A single ``<section>.yaml`` file   → monolithic mode.
    2. A ``<section>/`` subdirectory       → modular mode (merge all ``*.yaml``).
    """

    SECTIONS = ("notifications", "subscription", "settings")

    def __init__(self, workflow_dir: Path) -> None:
        self.workflow_dir = workflow_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, Any]:
        """Return merged config for all known sections.

        Returns a dict keyed by section name.
        """
        result: dict[str, Any] = {}
        for section in self.SECTIONS:
            data = self.load_section(section)
            if data:
                result[section] = data
        return result

    def load_section(self, section: str) -> dict[str, Any]:
        """Load a single *section* (e.g. ``"notifications"``)."""
        monolithic = self.workflow_dir / f"{section}.yaml"
        modular_dir = self.workflow_dir / section

        if monolithic.is_file():
            return self._load_file(monolithic)

        if modular_dir.is_dir():
            return self._merge_directory(modular_dir)

        return {}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_file(self, path: Path) -> dict[str, Any]:
        """Read and parse a single YAML file."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, dict):
                logger.warning("Expected dict at top level of %s, got %s", path, type(data))
                return {}
            return data
        except yaml.YAMLError as exc:
            logger.error("YAML parse error in %s: %s", path, exc)
            return {}
        except OSError as exc:
            logger.error("Cannot read %s: %s", path, exc)
            return {}

    def _merge_directory(self, directory: Path) -> dict[str, Any]:
        """Merge all ``*.yaml`` files in *directory* into a single dict."""
        merged: dict[str, Any] = {}
        for yaml_file in sorted(directory.glob("*.yaml")):
            data = self._load_file(yaml_file)
            merged = self._deep_merge(merged, data)
        return merged

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Recursively merge *override* into *base*."""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ModularYAMLLoader._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
