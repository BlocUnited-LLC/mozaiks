from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from mozaiksai.core.observability import configure_otel_from_env
from mozaiksai.core.workflow.paths import candidate_app_workflows_roots
from mozaiksai.resources import resolve_factory_app_root, resolve_factory_workflows_root

if TYPE_CHECKING:
    from fastapi import FastAPI

_BOOTSTRAP_STATE_ATTR = "mozaiks_repo_host_bootstrap_hosts"


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

    configure_otel_from_env()
    os.environ.update(resolve_repo_host_defaults(normalized_host))


def register_repo_host_bootstrap(target_app: FastAPI, host: str) -> None:
    """Arrange for ``configure_repo_host_defaults(host)`` to run at server startup.

    Wraps the app's composed lifespan so the repo defaults are applied before
    any lower-layer startup (runtime, platform) resolves app or workflow roots.
    Importing a host module stays free of environment mutation; only actually
    starting the server applies defaults. Registration is idempotent per
    (app, host) so module reloads do not stack duplicate bootstrap layers.
    """
    registered_hosts = getattr(target_app.state, _BOOTSTRAP_STATE_ATTR, None)
    if registered_hosts is None:
        registered_hosts = set()
        setattr(target_app.state, _BOOTSTRAP_STATE_ATTR, registered_hosts)
    if host in registered_hosts:
        return
    registered_hosts.add(host)

    existing_lifespan = target_app.router.lifespan_context

    @asynccontextmanager
    async def _bootstrap_lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        configure_repo_host_defaults(host)
        async with existing_lifespan(app_instance):
            yield

    target_app.router.lifespan_context = _bootstrap_lifespan


__all__ = [
    "configure_repo_host_defaults",
    "register_repo_host_bootstrap",
    "resolve_repo_host_defaults",
]
