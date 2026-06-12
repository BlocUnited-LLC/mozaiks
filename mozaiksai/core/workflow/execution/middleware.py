"""AG2 beta middleware support for Mozaiks workflow prompt injection.

Mozaiks uses ``middleware.yaml`` as the declarative authoring file for
workflow-local prompt middleware. Entries are compiled into agent-level AG2 beta
middleware that mutates ``Context.prompt`` before the model call.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml
from autogen.beta import Context
from autogen.beta.events import BaseEvent, ModelResponse
from autogen.beta.middleware import BaseMiddleware, LLMCall, Middleware

from ..declarative import parse_middleware_config

logger = logging.getLogger("middleware_loader")


class MozaiksPromptMiddleware(BaseMiddleware):
    """Run Mozaiks prompt middleware declarations via AG2 beta middleware."""

    def __init__(
        self,
        event: BaseEvent,
        context: Context,
        *,
        middleware_functions: Sequence[Callable],
        agent_name: str,
        base_system_message: str,
        context_bridge: Any,
    ) -> None:
        super().__init__(event, context)
        self._middleware_functions = list(middleware_functions)
        self._agent_name = agent_name
        self._base_system_message = base_system_message
        self._context_bridge = context_bridge

    async def on_llm_call(
        self,
        call_next: LLMCall,
        events: Sequence[BaseEvent],
        context: Context,
    ) -> ModelResponse:
        if not self._middleware_functions:
            return await call_next(events, context)

        captured_prompt = await self._run_prompt_middleware(context)
        if captured_prompt and captured_prompt != self._base_system_message:
            context.prompt[:] = [captured_prompt]

        return await call_next(events, context)

    async def _run_prompt_middleware(self, context: Context) -> str | None:
        class _PromptCapture:
            def __init__(self, name: str, ctx: Any, base_message: str) -> None:
                self.name = name
                self.context_variables = ctx
                self.system_message = base_message
                self._system_message = base_message
                self._captured: str | None = None

            def update_system_message(self, message: str) -> None:
                self.system_message = message
                self._system_message = message
                self._captured = message

        history = context.variables.get("_mozaiks_history", [])
        capture = _PromptCapture(
            self._agent_name,
            self._context_bridge,
            self._base_system_message,
        )

        for middleware_fn in self._middleware_functions:
            try:
                result = middleware_fn(capture, history)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                logger.debug(
                    "[MIDDLEWARE] Prompt middleware failed for %s: %s",
                    self._agent_name,
                    exc,
                )

        return capture._captured


def _ensure_workflow_import_paths(workflow_path: Path) -> None:
    """Expose the active workflows package before importing middleware modules."""

    desired_order = (
        workflow_path.parent.parent,
        workflow_path.parent,
        workflow_path,
        workflow_path / "tools",
    )
    normalized: list[str] = []
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
    file_value: str | None,
    function_value: str,
    workflow_path: Path,
) -> tuple[Callable | None, str]:
    """Resolve and import a prompt middleware function from a workflow bundle."""

    module_name: str | None = None
    fn_name: str | None = None

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
                "Middleware import failed: workflow=%s filename=%s not found under %s",
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
                    "Middleware import failed: workflow=%s filename=%s resolves outside workflows root %s",
                    workflow_name,
                    file_value,
                    workflows_root,
                )
                return None, f"{resolved_file}:{fn_name}"
            _ensure_workflow_import_paths(workflow_path)
            module_name = f"_mz_workflow_middleware_{workflow_name}_{stem}_{abs(hash(str(resolved_file)))}"
            spec = importlib.util.spec_from_file_location(module_name, resolved_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {resolved_file}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Middleware import failed: %s: %s", resolved_file, exc)
            return None, f"{resolved_file}:{fn_name}"

        try:
            fn = getattr(mod, fn_name)
        except AttributeError as exc:
            logger.warning("Middleware attribute not found: %s:%s: %s", resolved_file, fn_name, exc)
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
        logger.warning("Middleware import failed: %s: %s", module_name, exc)
        return None, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"

    try:
        fn = getattr(mod, fn_name)
    except AttributeError as exc:
        logger.warning("Middleware attribute not found: %s.%s: %s", module_name, fn_name, exc)
        return None, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"

    return fn, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"


def _read_middleware_config(workflow_path: Path) -> tuple[dict[str, Any], str | None]:
    """Load and validate `middleware.yaml` from a workflow directory."""

    middleware_yaml = workflow_path / "middleware.yaml"
    if not middleware_yaml.exists():
        return {}, None

    try:
        payload = yaml.safe_load(middleware_yaml.read_text(encoding="utf-8")) or {}
        parsed = parse_middleware_config(payload)
        return parsed, str(middleware_yaml)
    except Exception as exc:
        logger.error("Failed reading middleware.yaml for workflow at %s: %s", workflow_path, exc)
        return {}, None


def load_prompt_middleware_entries(workflow_name: str, *, base_path: str = "workflows") -> list[dict[str, Any]]:
    """Load prompt middleware entries from `middleware.yaml`."""

    workflow_path = Path(base_path) / workflow_name
    data, source = _read_middleware_config(workflow_path)
    if not source:
        return []
    entries = data.get("prompt_middleware") or []
    if not isinstance(entries, list):
        logger.warning("Middleware config has invalid 'prompt_middleware' list in %s", source)
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def build_prompt_middleware(
    *,
    middleware_functions: Sequence[Callable],
    agent_name: str,
    base_system_message: str,
    context_bridge: Any,
) -> Middleware:
    """Build the AG2 beta middleware factory for Mozaiks prompt middleware."""

    return Middleware(
        MozaiksPromptMiddleware,
        middleware_functions=list(middleware_functions),
        agent_name=agent_name,
        base_system_message=base_system_message,
        context_bridge=context_bridge,
    )


__all__ = [
    "MozaiksPromptMiddleware",
    "build_prompt_middleware",
    "load_prompt_middleware_entries",
    "_resolve_import",
]
