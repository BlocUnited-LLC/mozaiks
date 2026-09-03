from __future__ import annotations

import importlib
import importlib.util
import inspect
from collections.abc import Iterable
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.routing import APIRouter

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("runtime_extensions")

_RUN_LEVEL_TRIGGERS = {"on_start", "on_complete", "on_fail"}


# ---------------------------------------------------------------------------
# Module-local entrypoint resolution
# ---------------------------------------------------------------------------

def _module_package_root(module_name: str) -> str:
    """Return the sys.modules package root that ModuleLoader registers for a module.

    ModuleLoader registers loaded module backend code under a synthetic package
    named ``mozaiks_runtime_module_{safe_name}`` so that module-local imports are
    isolated.  This function returns that root so extension entrypoints declared as
    ``backend.router:get_router`` can be resolved as
    ``mozaiks_runtime_module_task_manager.backend.router:get_router``.
    """
    safe = module_name.replace(".", "_").replace("-", "_")
    return f"mozaiks_runtime_module_{safe}"


def _qualify_module_entrypoint(module_name: str, entrypoint: str) -> str:
    """Qualify a module-local entrypoint with its registered package root.

    ``backend.router:get_router`` →
    ``mozaiks_runtime_module_task_manager.backend.router:get_router``
    """
    if ":" not in entrypoint:
        raise ValueError(f"Invalid entrypoint (expected module.path:attr): {entrypoint!r}")
    module_path, _, attr = entrypoint.partition(":")
    return f"{_module_package_root(module_name)}.{module_path.strip()}:{attr.strip()}"


def mount_module_routers(app: FastAPI, loaded_modules: Iterable[Any]) -> int:
    """Mount ``api_router`` extensions declared in already-loaded module manifests.

    Uses the module's registered ``sys.modules`` package root to resolve
    module-local entrypoints (e.g. ``backend.router:get_router``).

    This must be called *after* ``ModuleLoader.load_all()`` so that the module
    packages are registered in ``sys.modules``.

    Returns the number of routers successfully mounted.
    """
    mounted = 0
    for mod in loaded_modules:
        manifests = getattr(mod, "manifests", None)
        rt_ext = (
            getattr(manifests, "runtime_extensions", None)
            if manifests is not None
            else None
        )
        if rt_ext is None:
            continue
        module_name: str = getattr(mod, "name", "")
        for ext in rt_ext.extensions:
            if ext.kind != "api_router":
                continue
            try:
                qualified = _qualify_module_entrypoint(module_name, ext.entrypoint)
                obj = _load_entrypoint(qualified)
                router = obj() if callable(obj) and not isinstance(obj, APIRouter) else obj
                if not isinstance(router, APIRouter):
                    logger.warning(
                        "MODULE_EXTENSIONS_SKIP_ROUTER: entrypoint did not return APIRouter: "
                        "%s (module=%s)",
                        ext.entrypoint,
                        module_name,
                    )
                    continue
                app.include_router(router, prefix=ext.prefix or "")
                mounted += 1
                logger.info(
                    "MODULE_EXTENSIONS_ROUTER_MOUNTED: %s (module=%s prefix=%r)",
                    ext.entrypoint,
                    module_name,
                    ext.prefix or "",
                )
            except Exception as exc:
                logger.warning(
                    "MODULE_EXTENSIONS_ROUTER_FAILED: %s (module=%s) error=%s",
                    ext.entrypoint,
                    module_name,
                    exc,
                )
    return mounted


async def start_module_services(loaded_modules: Iterable[Any]) -> list[Any]:
    """Start ``startup_service`` extensions declared in already-loaded module manifests.

    Uses the module's registered ``sys.modules`` package root to resolve
    module-local entrypoints (e.g. ``backend.worker:MyService``).

    This must be called *after* ``ModuleLoader.load_all()`` so that the module
    packages are registered in ``sys.modules``.

    Returns list of service instances that were started (suitable for passing
    to ``stop_services`` on shutdown).
    """
    started: list[Any] = []
    for mod in loaded_modules:
        manifests = getattr(mod, "manifests", None)
        rt_ext = (
            getattr(manifests, "runtime_extensions", None)
            if manifests is not None
            else None
        )
        if rt_ext is None:
            continue
        module_name: str = getattr(mod, "name", "")
        for ext in rt_ext.extensions:
            if ext.kind != "startup_service":
                continue
            try:
                qualified = _qualify_module_entrypoint(module_name, ext.entrypoint)
                cls = _load_entrypoint(qualified)
                svc = cls() if callable(cls) and not inspect.iscoroutinefunction(cls) else cls
                start_fn = getattr(svc, "start", None)
                if callable(start_fn):
                    res = start_fn()
                    if inspect.isawaitable(res):
                        await res
                started.append(svc)
                logger.info(
                    "MODULE_EXTENSIONS_SERVICE_STARTED: %s (module=%s)",
                    ext.entrypoint,
                    module_name,
                )
            except Exception as exc:
                logger.warning(
                    "MODULE_EXTENSIONS_SERVICE_FAILED: %s (module=%s) error=%s",
                    ext.entrypoint,
                    module_name,
                    exc,
                )
    return started


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
    """Yield extension dicts from modules/{name}/runtime_extensions.yaml in the active app root.

    Schema for runtime_extensions.yaml:
        schema_version: mozaiks.runtime_extensions.v1
        extensions:
          - kind: api_router
            entrypoint: backend.router:get_router
            prefix: /webhooks        # optional
          - kind: startup_service
            entrypoint: backend.worker:MyService
    """
    try:
        from mozaiksai.core.workflow.paths import resolve_active_app_root
        app_root = resolve_active_app_root()
    except Exception as exc:
        logger.debug("RUNTIME_EXTENSIONS_APP_ROOT_UNAVAILABLE: %s", exc)
        return

    modules_dir = app_root / "modules"
    if not modules_dir.is_dir():
        return

    for module_dir in sorted(modules_dir.iterdir(), key=lambda d: d.name.lower()):
        if not module_dir.is_dir():
            continue

        ext_yaml = module_dir / "runtime_extensions.yaml"
        if not ext_yaml.exists():
            continue

        try:
            with open(ext_yaml, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        except Exception as exc:
            logger.warning("RUNTIME_EXTENSIONS_PARSE_FAILED: %s error=%s", ext_yaml, exc)
            continue

        ext_list = raw.get("extensions")
        if not isinstance(ext_list, list):
            continue

        for ext in ext_list:
            if not isinstance(ext, dict):
                continue
            ext2 = dict(ext)
            ext2.setdefault("module", module_dir.name)
            yield ext2


def mount_declared_routers(app: FastAPI) -> int:
    """Mount all declared APIRouters onto the FastAPI app.

    Returns number of routers mounted.
    """

    mounted = 0

    for ext in _iter_declared_extensions():
        kind = (ext.get("kind") or "").strip().lower()
        if kind != "api_router":
            continue

        entrypoint = ext.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            logger.warning("RUNTIME_EXTENSIONS_SKIP_ROUTER: missing entrypoint (module=%s)", ext.get('module'))
            continue

        prefix = ext.get("prefix")
        if not isinstance(prefix, str):
            prefix = ""

        try:
            obj = _load_entrypoint(entrypoint.strip())
            router = obj() if callable(obj) and not isinstance(obj, APIRouter) else obj
            if not isinstance(router, APIRouter):
                logger.warning(
                    "RUNTIME_EXTENSIONS_SKIP_ROUTER: entrypoint did not return APIRouter: %s", entrypoint)
                continue
            app.include_router(router, prefix=prefix)
            mounted += 1
            logger.info("RUNTIME_EXTENSIONS_ROUTER_MOUNTED: %s (prefix='%s')", entrypoint, prefix)
        except Exception as exc:
            logger.warning("RUNTIME_EXTENSIONS_ROUTER_FAILED: %s error=%s", entrypoint, exc)

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
        kind = (ext.get("kind") or "").strip().lower()
        if kind != "startup_service":
            continue

        entrypoint = ext.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            logger.warning("RUNTIME_EXTENSIONS_SKIP_SERVICE: missing entrypoint (module=%s)", ext.get('module'))
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
            logger.info("RUNTIME_EXTENSIONS_SERVICE_STARTED: %s", entrypoint)
        except Exception as exc:
            logger.debug("RUNTIME_EXTENSIONS_SERVICE_NOT_STARTED: %s error=%s", entrypoint, exc)

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
# LIFECYCLE HOOKS — Workflow-declared run-level lifecycle notifications
# =============================================================================

def get_workflow_lifecycle_hooks(workflow_name: str) -> dict[str, Any]:
    """Load run-level lifecycle hooks declared by a workflow in tools.yaml.

    Workflows declare on_start, on_complete, and on_fail notifications via
    lifecycle_tools entries in tools.yaml using run-level trigger values:

        lifecycle_tools:
          - trigger: on_start
            agent: null
            file: tools/platform/build_lifecycle.py
            function: emit_build_started
          - trigger: on_complete
            agent: null
            file: tools/platform/build_lifecycle.py
            function: emit_build_completed
          - trigger: on_fail
            agent: null
            file: tools/platform/build_lifecycle.py
            function: emit_build_failed

    Each hook callable signature:
        on_start/on_complete:
            async fn(app_id, execution_id, chat_id, user_id, workflow_name)
        on_fail:
            async fn(app_id, execution_id, chat_id, user_id, workflow_name, message, details=None)

    Returns dict with None values when no hooks are declared (safe to call unconditionally).
    """
    empty_hooks: dict[str, Any] = {
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
        logger.debug("LIFECYCLE_HOOKS_WORKFLOW_MANAGER_UNAVAILABLE: %s", exc)
        return empty_hooks

    lifecycle_tools = cfg.get("lifecycle_tools")
    if not isinstance(lifecycle_tools, list):
        return empty_hooks

    loaded: dict[str, list[tuple[Any, str]]] = {trigger: [] for trigger in empty_hooks}
    seen_declarations: set[tuple[str, str, str]] = set()

    for entry in lifecycle_tools:
        if not isinstance(entry, dict):
            continue

        trigger = (entry.get("trigger") or "").strip().lower()
        if trigger not in _RUN_LEVEL_TRIGGERS:
            continue

        # Build entrypoint from workflow-relative file + function fields
        wf_file = (entry.get("file") or "").strip()
        function = (entry.get("function") or "").strip()
        if not wf_file or not function:
            logger.warning(
                "LIFECYCLE_HOOKS_SKIP: missing file or function for trigger=%s (workflow=%s)",
                trigger, workflow_name)
            continue

        # Duplicate declarations are deduplicated explicitly, never run twice.
        declaration_key = (trigger, wf_file, function)
        if declaration_key in seen_declarations:
            logger.warning(
                "LIFECYCLE_HOOKS_DUPLICATE: %s:%s trigger=%s (workflow=%s) — "
                "duplicate declaration ignored",
                wf_file, function, trigger, workflow_name,
            )
            continue
        seen_declarations.add(declaration_key)

        # Closed declarative failure policy. `required` hook failures
        # propagate and prevent lifecycle claims; `best_effort` failures are
        # recorded without suppressing canonical state. The default is
        # `required` so no hook silently becomes optional.
        policy = str(entry.get("policy") or "required").strip().lower()
        if policy not in _LIFECYCLE_HOOK_POLICIES:
            logger.warning(
                "LIFECYCLE_HOOKS_INVALID_POLICY: %s:%s trigger=%s policy=%r "
                "(workflow=%s) — entry skipped",
                wf_file, function, trigger, policy, workflow_name,
            )
            continue

        try:
            callable_fn = _load_workflow_local_entrypoint(
                workflow_name=workflow_name,
                file_path=wf_file,
                function=function,
            )
            loaded[trigger].append((callable_fn, policy))
            logger.info(
                "LIFECYCLE_HOOKS_LOADED: %s:%s trigger=%s policy=%s (workflow=%s)",
                wf_file,
                function,
                trigger,
                policy,
                workflow_name,
            )
        except Exception as exc:
            logger.warning(
                "LIFECYCLE_HOOKS_FAILED: %s:%s trigger=%s error=%s",
                wf_file,
                function,
                trigger,
                exc,
            )

    result = dict(empty_hooks)
    for trigger, hooks in loaded.items():
        if hooks:
            result[trigger] = _compose_lifecycle_hooks(hooks, trigger, workflow_name)

    return result


_LIFECYCLE_HOOK_POLICIES = frozenset({"required", "best_effort"})


def _isolated_hook_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Shallow-copy hook kwargs so sibling hooks cannot mutate each other's input."""
    isolated = dict(kwargs)
    context = isolated.get("context_variables")
    if isinstance(context, dict):
        isolated["context_variables"] = dict(context)
    return isolated


def _compose_lifecycle_hooks(
    hooks: list[tuple[Any, str]], trigger: str, workflow_name: str
) -> Any:
    """Invoke every declared hook for a trigger in declaration order.

    A workflow may declare several lifecycle_tools for the same run-level
    trigger; each runs in declaration order with isolated kwargs. A
    ``required`` hook failure propagates immediately — remaining hooks do not
    run and no downstream state may treat the trigger as satisfied. A
    ``best_effort`` hook failure is logged and never suppresses canonical
    state. Returns the first non-None hook result.
    """

    async def _composed(*args: Any, **kwargs: Any) -> Any:
        first_result: Any = None
        for hook, policy in hooks:
            hook_name = getattr(hook, "__name__", repr(hook))
            try:
                hook_result = hook(*args, **_isolated_hook_kwargs(kwargs))
                if inspect.isawaitable(hook_result):
                    hook_result = await hook_result
                if first_result is None:
                    first_result = hook_result
            except Exception as exc:
                if policy == "required":
                    logger.error(
                        "LIFECYCLE_HOOK_REQUIRED_FAILED: trigger=%s workflow=%s hook=%s error=%s",
                        trigger, workflow_name, hook_name, exc,
                    )
                    raise
                logger.warning(
                    "LIFECYCLE_HOOK_BEST_EFFORT_FAILED: trigger=%s workflow=%s hook=%s error=%s",
                    trigger, workflow_name, hook_name, exc,
                )
        return first_result

    return _composed


def _load_workflow_local_entrypoint(
    *,
    workflow_name: str,
    file_path: str,
    function: str,
) -> Any:
    """Load a lifecycle hook from a workflow-local Python file.

    Workflow folders are runtime data under the selected workflows root, not a
    Python package named ``workflows``. Loading by resolved file path keeps the
    lifecycle contract aligned with ``tools.yaml`` and with generated workflow
    bundles.
    """
    from mozaiksai.core.workflow.paths import resolve_workflow_path

    workflow_path = resolve_workflow_path(workflow_name)
    if workflow_path is None:
        raise FileNotFoundError(f"workflow path not found for {workflow_name!r}")

    clean_file = str(file_path or "").strip()
    if not clean_file:
        raise ValueError("file_path is required")
    clean_function = str(function or "").strip()
    if not clean_function:
        raise ValueError("function is required")

    candidates = [
        (workflow_path / clean_file).resolve(),
        (workflow_path / "tools" / clean_file).resolve(),
    ]
    resolved_file = next((candidate for candidate in candidates if candidate.is_file()), None)
    if resolved_file is None:
        raise FileNotFoundError(f"lifecycle hook file not found: {clean_file}")

    module_name = (
        "mozaiks_lifecycle_run_"
        f"{workflow_name}_{resolved_file.stem}_{abs(hash(str(resolved_file)))}"
    )
    spec = importlib.util.spec_from_file_location(module_name, resolved_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load lifecycle hook module from {resolved_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    callable_fn = getattr(module, clean_function, None)
    if not callable(callable_fn):
        raise AttributeError(f"{clean_function!r} is not callable in {resolved_file}")
    return callable_fn
