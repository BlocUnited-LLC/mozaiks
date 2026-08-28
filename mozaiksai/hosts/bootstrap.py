from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from mozaiksai.core.observability import configure_otel_from_env
from mozaiksai.core.workflow.paths import candidate_app_workflows_roots
from mozaiksai.resources import resolve_factory_app_root, resolve_factory_workflows_root

if TYPE_CHECKING:
    from fastapi import FastAPI

_BOOTSTRAP_STATE_ATTR = "mozaiks_repo_host_bootstrap_hosts"
_otel_configured = False


def _resolve_app_bundle_dir(path_value: str | os.PathLike[str]) -> Path:
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if (candidate / "app.json").exists():
        return candidate
    nested = candidate / "app"
    if (nested / "app.json").exists():
        return nested.resolve()
    factory_nested = candidate / "factory_app" / "app"
    if (factory_nested / "app.json").exists():
        return factory_nested.resolve()
    return candidate


def resolve_repo_host_defaults(
    host: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Compute the default app/workflow env entries for a Studio or platform host.

    Pure computation over ``environ`` (``os.environ`` when omitted): returns the
    entries a real host startup applies, without mutating anything. The active
    app workspace comes from PLATFORM_PATH or MOZAIKS_APP_WORKSPACE_PATH when
    provided; caller-provided values keep precedence and are only normalized to
    the resolved app bundle directory. In a repo-local dogfood checkout the
    fallback is the first-party factory_app/app bundle so Studio/platform hosts
    have a real app root for shell config, routes, and branding.

    Factory bundle discovery goes through ``mozaiksai.resources`` and therefore
    consults ``MOZAIKS_FACTORY_APP_PATH`` from the process environment.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    normalized_host = str(host or "").strip().lower()
    if normalized_host not in {"platform", "studio"}:
        return {}

    updates: dict[str, str] = {}

    external_workspace_root = str(env.get("MOZAIKS_APP_WORKSPACE_PATH") or "").strip()
    platform_path = str(env.get("PLATFORM_PATH") or "").strip()

    if platform_path:
        updates["PLATFORM_PATH"] = str(_resolve_app_bundle_dir(platform_path))
    elif external_workspace_root:
        updates["PLATFORM_PATH"] = str(_resolve_app_bundle_dir(external_workspace_root))
    else:
        factory_app_root = resolve_factory_app_root()
        if factory_app_root is not None:
            updates["PLATFORM_PATH"] = str(_resolve_app_bundle_dir(factory_app_root))

    single_root = str(env.get("MOZAIKS_WORKFLOWS_PATH") or "").strip()
    if single_root:
        return updates

    effective_platform_path = str(updates.get("PLATFORM_PATH") or "").strip()
    app_root = _resolve_app_bundle_dir(effective_platform_path) if effective_platform_path else None
    app_workflow_roots = candidate_app_workflows_roots(app_root) if app_root else ()
    app_workflows_root = next((root for root in app_workflow_roots if root.is_dir()), None)
    factory_workflows_root = resolve_factory_workflows_root()

    if normalized_host == "studio":
        selected_root: Path | None = factory_workflows_root or app_workflows_root
    else:
        selected_root = app_workflows_root if (app_workflows_root is not None and app_workflows_root.is_dir()) else factory_workflows_root
    if selected_root is not None:
        updates["MOZAIKS_WORKFLOWS_PATH"] = str(selected_root)
    return updates


def _configure_otel_once() -> None:
    """Configure OpenTelemetry at most once per process.

    ``configure_otel_from_env`` builds a fresh ``TracerProvider`` (and a
    ``BatchSpanProcessor`` worker thread) on every call, while
    ``trace.set_tracer_provider`` refuses to replace an installed provider. It
    used to run once, at host import; the guard keeps that once-per-process
    behavior now that bootstrap can run on each ASGI startup (``--reload``, or
    a test that starts the app more than once).

    The flag is set only after configuration returns, so a call that raises
    leaves observability un-configured and retryable on the next startup
    instead of permanently marking it done.
    """
    global _otel_configured
    if _otel_configured:
        return
    configure_otel_from_env()
    _otel_configured = True


def configure_repo_host_defaults(host: str) -> None:
    """Apply default app/workflow paths for a real Studio or platform startup.

    This is the explicit process-bootstrap step: it writes the resolved
    defaults into ``os.environ`` and configures OpenTelemetry from env. Call it
    from actual host startup (``register_repo_host_bootstrap``), the CLI, or a
    script entrypoint — never at module import time. Re-running it is
    idempotent: values already present keep precedence and resolved defaults
    are stable for an unchanged environment.
    """
    normalized_host = str(host or "").strip().lower()
    if normalized_host not in {"platform", "studio"}:
        return

    _configure_otel_once()
    os.environ.update(resolve_repo_host_defaults(normalized_host))


def align_workflow_catalog_with_host_config() -> None:
    """Rebind the global workflow catalog to the root host startup selected.

    ``mozaiksai.core.workflow.workflow_manager`` constructs its global manager
    at module import, binding whichever workflow root the environment named at
    that moment. Host startup — not import order — is the authority on that
    root, so when the two disagree the catalog is rebuilt against the resolved
    root. The rebuild preserves manager object identity, so modules that
    imported the manager by value keep working. When the roots already agree
    (the repo-local and correctly pre-configured cases) this is a no-op.

    Studio is the case that needs it: its defaults prefer the shared factory
    workflow root, while bare root resolution prefers an app workspace's own
    ``workflows/``. Without this, ``mozaiks serve <workspace> --host studio``
    would bind Studio to the workspace's (usually empty) workflow root and lose
    the entire factory build catalog.
    """
    from mozaiksai.core.workflow.paths import resolve_workflows_root
    from mozaiksai.core.workflow.workflow_manager import initialize_workflows, workflow_manager

    resolved_root = resolve_workflows_root()
    current_root = getattr(workflow_manager, "workflows_base_path", None)
    if current_root is not None and Path(str(current_root)) == resolved_root:
        return
    initialize_workflows(str(resolved_root))


@contextmanager
def _workflow_catalog_bound_to_host_config() -> Iterator[None]:
    """Bind the workflow catalog to the host's root for the life of the server.

    The binding is released on shutdown so it lasts exactly as long as the
    running server does. A process that starts a host, stops it, and starts
    another (``--reload``, or a test that drives more than one app) then gets
    the same catalog it would have had on a fresh start, instead of inheriting
    whichever root the previous server selected.

    Snapshotting and restoring the manager's own state is sufficient:
    ``initialize_workflows`` rebuilds the catalog into the existing manager
    object rather than replacing it, precisely so modules that imported the
    manager by value keep a live reference. The module globals therefore still
    point at this same object afterwards and need no rebinding.
    """
    from mozaiksai.core.workflow import workflow_manager as workflow_manager_module

    manager = workflow_manager_module.workflow_manager
    catalog_snapshot = dict(manager.__dict__)
    align_workflow_catalog_with_host_config()
    if manager.__dict__ == catalog_snapshot:
        yield
        return
    try:
        yield
    finally:
        manager.__dict__.clear()
        manager.__dict__.update(catalog_snapshot)


def register_repo_host_bootstrap(target_app: FastAPI, host: str) -> None:
    """Arrange for ``configure_repo_host_defaults(host)`` to run at server startup.

    Wraps the app's composed lifespan so the repo defaults are applied before
    any lower-layer startup (runtime, platform) runs, then rebinds the global
    workflow catalog to the root those defaults selected. Importing a host
    module stays free of environment mutation; only actually starting the
    server applies defaults. Registration is idempotent per (app, host) so
    module reloads do not stack duplicate bootstrap layers.
    """
    normalized_host = str(host or "").strip().lower()
    registered_hosts = getattr(target_app.state, _BOOTSTRAP_STATE_ATTR, None)
    if registered_hosts is None:
        registered_hosts = set()
        setattr(target_app.state, _BOOTSTRAP_STATE_ATTR, registered_hosts)
    if normalized_host in registered_hosts:
        return
    registered_hosts.add(normalized_host)

    existing_lifespan = target_app.router.lifespan_context

    @asynccontextmanager
    async def _bootstrap_lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        configure_repo_host_defaults(normalized_host)
        with _workflow_catalog_bound_to_host_config():
            async with existing_lifespan(app_instance):
                yield

    target_app.router.lifespan_context = _bootstrap_lifespan


__all__ = [
    "align_workflow_catalog_with_host_config",
    "configure_repo_host_defaults",
    "register_repo_host_bootstrap",
    "resolve_repo_host_defaults",
]
