"""Hook loading utilities for AG2 beta workflow hooks.

`hooks.yaml` is now a prompt-injection contract. The runtime loads
`update_agent_state` functions and wires them into beta agents through
`MozaiksHookPolicy` during agent construction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import contextlib
import importlib
import importlib.util
import logging
import sys

import yaml

from ..declarative import parse_hooks_config

logger = logging.getLogger("hooks_loader")


def _ensure_workflow_import_paths(workflow_path: Path) -> None:
    """Expose the active workflows package before importing hook modules."""

    desired_order = (
        workflow_path.parent.parent,
        workflow_path.parent,
        workflow_path,
        workflow_path / "tools",
    )
    normalized: List[str] = []
    for candidate in desired_order:
        try:
            value = str(candidate.resolve())
        except Exception:
            value = str(candidate)
        if value:
            normalized.append(value)
    for value in reversed(normalized):
        with contextlib.suppress(ValueError):
            sys.path.remove(value)
        if value:
            sys.path.insert(0, value)


def _resolve_import(
    workflow_name: str,
    file_value: Optional[str],
    function_value: str,
    workflow_path: Path,
) -> tuple[Optional[Callable], str]:
    """Resolve and import a hook function from a workflow bundle."""

    module_name: Optional[str] = None
    fn_name: Optional[str] = None

    if file_value:
        file_path = Path(file_value)
        if file_path.is_absolute():
            candidate_paths = [file_path]
        else:
            candidate_paths = [
                workflow_path / file_path,
                workflow_path / "tools" / file_path,
            ]
            if len(file_path.parts) == 1:
                candidate_paths.extend(
                    [
                        workflow_path / file_path.name,
                        workflow_path / "tools" / file_path.name,
                    ]
                )
        stem = file_path.stem
        fn_name = function_value
        resolved_file = next((candidate for candidate in candidate_paths if candidate.exists()), None)
        if resolved_file is None:
            logger.warning(
                "Hook import failed: workflow=%s filename=%s not found under %s",
                workflow_name,
                file_value,
                workflow_path,
            )
            return None, f"{workflow_name}.{stem}.{fn_name}"

        try:
            resolved_file = resolved_file.resolve()
            workflows_root = workflow_path.parent.resolve()
            if not resolved_file.is_relative_to(workflows_root):
                logger.warning(
                    "Hook import failed: workflow=%s filename=%s resolves outside workflows root %s",
                    workflow_name,
                    file_value,
                    workflows_root,
                )
                return None, f"{resolved_file}:{fn_name}"
            _ensure_workflow_import_paths(workflow_path)
            module_name = f"_mz_workflow_hook_{workflow_name}_{stem}_{abs(hash(str(resolved_file)))}"
            spec = importlib.util.spec_from_file_location(module_name, resolved_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {resolved_file}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Hook import failed: %s: %s", resolved_file, exc)
            return None, f"{resolved_file}:{fn_name}"

        try:
            fn = getattr(mod, fn_name)
        except AttributeError as exc:
            logger.warning("Hook attribute not found: %s:%s: %s", resolved_file, fn_name, exc)
            return None, f"{resolved_file}:{fn_name}"

        return fn, f"{resolved_file}:{fn_name}"

    if ":" in function_value:
        module_name, fn_name = function_value.split(":", 1)
    elif "." in function_value:
        parts = function_value.split(".")
        module_name = ".".join(parts[:-1])
        fn_name = parts[-1]
    else:
        module_name = function_value
        fn_name = function_value.split(".")[-1]

    try:
        _ensure_workflow_import_paths(workflow_path)
        mod = importlib.import_module(module_name)
    except Exception as exc:
        logger.warning("Hook import failed: %s: %s", module_name, exc)
        return None, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"

    try:
        fn = getattr(mod, fn_name)
    except AttributeError as exc:
        logger.warning("Hook attribute not found: %s.%s: %s", module_name, fn_name, exc)
        return None, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"

    return fn, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"


def _read_hook_config(workflow_path: Path) -> tuple[Dict[str, Any], str | None]:
    """Load and validate `hooks.yaml` from a workflow directory."""

    hooks_yaml = workflow_path / "hooks.yaml"
    if not hooks_yaml.exists():
        return {}, None

    try:
        payload = yaml.safe_load(hooks_yaml.read_text(encoding="utf-8")) or {}
        parsed = parse_hooks_config(payload)
        return parsed, str(hooks_yaml)
    except Exception as exc:
        logger.error("Failed reading hooks.yaml for workflow at %s: %s", workflow_path, exc)
        return {}, None


def load_hook_entries(workflow_name: str, *, base_path: str = "workflows") -> List[Dict[str, Any]]:
    """Load `update_agent_state` hook entries from `hooks.yaml`."""

    workflow_path = Path(base_path) / workflow_name
    data, source = _read_hook_config(workflow_path)
    if not source:
        return []
    entries = data.get("hooks") or []
    if not isinstance(entries, list):
        logger.warning("Hook config has invalid 'hooks' list in %s", source)
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


__all__ = [
    "_resolve_import",
    "load_hook_entries",
]
