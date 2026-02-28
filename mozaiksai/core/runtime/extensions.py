from __future__ import annotations

import importlib
import inspect
from typing import Any, Iterable, Optional

from fastapi import FastAPI
from fastapi.routing import APIRouter

from logs.logging_config import get_workflow_logger


logger = get_workflow_logger("runtime_extensions")


def _load_entrypoint(entrypoint: str) -> Any:
    """Load an entrypoint of form `module.path:attr`."""

    if not isinstance(entrypoint, str) or ":" not in entrypoint:
        raise ValueError(f"Invalid entrypoint (expected module:attr): {entrypoint!r}")

    module_path, attr = entrypoint.split(":", 1)
    module_path = module_path.strip()
    attr = attr.strip()
    if not module_path or not attr:
        raise ValueError(f"Invalid entrypoint (expected module:attr): {entrypoint!r}")

    module = importlib.import_module(module_path)
    obj = getattr(module, attr, None)
    if obj is None:
        raise ImportError(f"Entrypoint not found: {module_path}:{attr}")
    return obj


def _iter_declared_extensions() -> Iterable[dict[str, Any]]:
    """Yield extension dicts across all loaded workflows.

    Schema (preferred):
      runtime_extensions:
        - kind: api_router
          entrypoint: pkg.mod:get_router
          prefix: ""  # optional
        - kind: startup_service
          entrypoint: pkg.mod:ServiceClass

    Backward/alt schema supported:
        - id: api_router
          entrypoints: { router: pkg.mod:get_router }
          prefix: ""
    """

    try:
        from mozaiksai.core.workflow.workflow_manager import get_workflow_manager

        mgr = get_workflow_manager()
    except Exception as exc:  # pragma: no cover
        logger.debug(f"RUNTIME_EXTENSIONS_WORKFLOW_MANAGER_UNAVAILABLE: {exc}")
        return

    for wf_name in sorted(mgr.list_loaded_workflows()):
        cfg = mgr.get_config(wf_name) or {}
        ext_list = cfg.get("runtime_extensions")
        if not isinstance(ext_list, list):
            continue

        for ext in ext_list:
            if not isinstance(ext, dict):
                continue
            ext2 = dict(ext)
            ext2.setdefault("workflow", wf_name)
            yield ext2


def mount_declared_routers(app: FastAPI) -> int:
    """Mount all declared APIRouters onto the FastAPI app.

    Returns number of routers mounted.
    """

    mounted = 0

    for ext in _iter_declared_extensions():
        kind = (ext.get("kind") or ext.get("id") or "").strip().lower()
        if kind != "api_router":
            continue

        entrypoint = ext.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            eps = ext.get("entrypoints")
            if isinstance(eps, dict) and isinstance(eps.get("router"), str):
                entrypoint = eps.get("router")

        if not isinstance(entrypoint, str) or not entrypoint.strip():
            logger.warning(f"RUNTIME_EXTENSIONS_SKIP_ROUTER: missing entrypoint (workflow={ext.get('workflow')})")
            continue

        prefix = ext.get("prefix")
        if not isinstance(prefix, str):
            prefix = ""

        try:
            obj = _load_entrypoint(entrypoint.strip())
            router = obj() if callable(obj) and not isinstance(obj, APIRouter) else obj
            if not isinstance(router, APIRouter):
                logger.warning(
                    f"RUNTIME_EXTENSIONS_SKIP_ROUTER: entrypoint did not return APIRouter: {entrypoint}"
                )
                continue
            app.include_router(router, prefix=prefix)
            mounted += 1
            logger.info(f"RUNTIME_EXTENSIONS_ROUTER_MOUNTED: {entrypoint} (prefix='{prefix}')")
        except Exception as exc:
            logger.warning(f"RUNTIME_EXTENSIONS_ROUTER_FAILED: {entrypoint} error={exc}")

    return mounted


async def start_declared_services() -> list[Any]:
    """Instantiate + start declared services.

    Service contract:
      - entrypoint resolves to a class (instantiated with no args)
      - optional `start()` (sync or async)
      - optional `stop()` (sync or async)

    Returns list of service instances that were started.
    """

    started: list[Any] = []

    for ext in _iter_declared_extensions():
        kind = (ext.get("kind") or ext.get("id") or "").strip().lower()
        if kind != "startup_service":
            continue

        entrypoint = ext.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            eps = ext.get("entrypoints")
            if isinstance(eps, dict) and isinstance(eps.get("service"), str):
                entrypoint = eps.get("service")

        if not isinstance(entrypoint, str) or not entrypoint.strip():
            logger.warning(f"RUNTIME_EXTENSIONS_SKIP_SERVICE: missing entrypoint (workflow={ext.get('workflow')})")
            continue

        try:
            cls = _load_entrypoint(entrypoint.strip())
            svc = cls() if callable(cls) and not inspect.iscoroutinefunction(cls) else cls
            start_fn = getattr(svc, "start", None)
            if callable(start_fn):
                res = start_fn()
                if inspect.isawaitable(res):
                    await res
            started.append(svc)
            logger.info(f"RUNTIME_EXTENSIONS_SERVICE_STARTED: {entrypoint}")
        except Exception as exc:
            logger.debug(f"RUNTIME_EXTENSIONS_SERVICE_NOT_STARTED: {entrypoint} error={exc}")

    return started


async def stop_services(services: list[Any]) -> None:
    for svc in services or []:
        try:
            stop_fn = getattr(svc, "stop", None)
            if callable(stop_fn):
                res = stop_fn()
                if inspect.isawaitable(res):
                    await res
        except Exception:
            pass


# =============================================================================
# LIFECYCLE HOOKS - Workflow-declared build/workflow lifecycle notifications
# =============================================================================

def get_workflow_lifecycle_hooks(workflow_name: str) -> dict[str, Any]:
    """Load lifecycle hooks declared by a specific workflow.

    Workflows can declare lifecycle hooks in their orchestrator.yaml via
    runtime_extensions with kind: lifecycle_hooks. This allows workflows
    to notify external systems (e.g., a platform control plane) when the
    workflow starts, completes, or fails.

    Example orchestrator.yaml:
        runtime_extensions:
          - kind: lifecycle_hooks
            entrypoint: workflows.MyWorkflow.tools.lifecycle:get_hooks

    The entrypoint should return a dict with optional callables:
        {
            "is_build_workflow": callable(workflow_name) -> bool,
            "on_start": async callable(app_id, user_id, chat_id, workflow_name),
            "on_complete": async callable(app_id, user_id, chat_id, workflow_name, result),
            "on_fail": async callable(app_id, user_id, chat_id, workflow_name, error),
        }

    Returns dict with None values if no hooks are declared (safe to call without checking).
    """
    empty_hooks = {
        "is_build_workflow": None,
        "on_start": None,
        "on_complete": None,
        "on_fail": None,
    }

    if not workflow_name:
        return empty_hooks

    try:
        from mozaiksai.core.workflow.workflow_manager import get_workflow_manager
        mgr = get_workflow_manager()
        cfg = mgr.get_config(workflow_name) or {}
    except Exception as exc:
        logger.debug(f"LIFECYCLE_HOOKS_WORKFLOW_MANAGER_UNAVAILABLE: {exc}")
        return empty_hooks

    ext_list = cfg.get("runtime_extensions")
    if not isinstance(ext_list, list):
        return empty_hooks

    for ext in ext_list:
        if not isinstance(ext, dict):
            continue

        kind = (ext.get("kind") or "").strip().lower()
        if kind != "lifecycle_hooks":
            continue

        entrypoint = ext.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            logger.warning(f"LIFECYCLE_HOOKS_SKIP: missing entrypoint (workflow={workflow_name})")
            continue

        try:
            factory = _load_entrypoint(entrypoint.strip())
            hooks = factory() if callable(factory) else factory

            if not isinstance(hooks, dict):
                logger.warning(f"LIFECYCLE_HOOKS_SKIP: entrypoint did not return dict: {entrypoint}")
                continue

            # Merge with empty_hooks to ensure all keys exist
            result = dict(empty_hooks)
            for key in empty_hooks:
                if key in hooks and hooks[key] is not None:
                    result[key] = hooks[key]

            logger.info(f"LIFECYCLE_HOOKS_LOADED: {entrypoint} (workflow={workflow_name})")
            return result

        except Exception as exc:
            logger.warning(f"LIFECYCLE_HOOKS_FAILED: {entrypoint} error={exc}")
            continue

    return empty_hooks


# =============================================================================
# API ROUTER - Workflow-declared additional FastAPI routes
# =============================================================================

def get_workflow_api_router(workflow_name: str) -> Any:
    """Load an optional FastAPI APIRouter declared by a specific workflow.

    Workflows can declare an API router in their orchestrator.yaml via
    runtime_extensions with kind: api_router. The entrypoint callable should
    return a FastAPI APIRouter instance.

    Example orchestrator.yaml::

        runtime_extensions:
          - kind: api_router
            entrypoint: workflows.MyWorkflow.tools.api:get_router

    Returns None if no router is declared (safe to call without checking).
    """
    if not workflow_name:
        return None

    try:
        from mozaiksai.core.workflow.workflow_manager import get_workflow_manager
        mgr = get_workflow_manager()
        cfg = mgr.get_config(workflow_name) or {}
    except Exception as exc:
        logger.debug(f"API_ROUTER_WORKFLOW_MANAGER_UNAVAILABLE: {exc}")
        return None

    ext_list = cfg.get("runtime_extensions")
    if not isinstance(ext_list, list):
        return None

    for ext in ext_list:
        if not isinstance(ext, dict):
            continue
        kind = (ext.get("kind") or "").strip().lower()
        if kind != "api_router":
            continue
        entrypoint = ext.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            logger.warning(f"API_ROUTER_SKIP: missing entrypoint (workflow={workflow_name})")
            continue
        try:
            factory = _load_entrypoint(entrypoint.strip())
            router = factory() if callable(factory) else factory
            logger.info(f"API_ROUTER_LOADED: {entrypoint} (workflow={workflow_name})")
            return router
        except Exception as exc:
            logger.warning(f"API_ROUTER_FAILED: {entrypoint} error={exc}")
            continue

    return None
