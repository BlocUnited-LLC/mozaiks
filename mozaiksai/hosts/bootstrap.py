from __future__ import annotations

import os
from pathlib import Path

from mozaiksai.core.workflow.paths import candidate_app_workflows_roots
from mozaiksai.resources import resolve_factory_app_root, resolve_factory_workflows_root


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


def configure_repo_host_defaults(host: str) -> None:
    """Apply default app/workflow paths for Studio and platform hosts.

    The active app workspace comes from PLATFORM_PATH or
    MOZAIKS_APP_WORKSPACE_PATH when provided. In a repo-local dogfood checkout,
    fall back to the first-party factory_app/app bundle so Studio/platform hosts
    have a real app root for shell config, routes, and branding. Shared factory
    workflows come from the packaged factory_app bundle when that package is
    installed.

    A running host should resolve its workflow root through MOZAIKS_WORKFLOWS_PATH
    or through the host-specific app/factory defaults below.
    """
    normalized_host = str(host or "").strip().lower()
    if normalized_host not in {"platform", "studio", "mozaiks"}:
        return

    external_workspace_root = str(os.getenv("MOZAIKS_APP_WORKSPACE_PATH") or "").strip()
    platform_path = str(os.getenv("PLATFORM_PATH") or "").strip()

    if platform_path:
        os.environ["PLATFORM_PATH"] = str(_resolve_app_bundle_dir(platform_path))
    elif external_workspace_root:
        os.environ["PLATFORM_PATH"] = str(_resolve_app_bundle_dir(external_workspace_root))
    else:
        factory_app_root = resolve_factory_app_root()
        if factory_app_root is not None:
            os.environ["PLATFORM_PATH"] = str(_resolve_app_bundle_dir(factory_app_root))

    single_root = str(os.getenv("MOZAIKS_WORKFLOWS_PATH") or "").strip()
    if single_root:
        return

    platform_path = str(os.getenv("PLATFORM_PATH") or "").strip()
    app_root = _resolve_app_bundle_dir(platform_path) if platform_path else None
    app_workflow_roots = candidate_app_workflows_roots(app_root) if app_root else ()
    app_workflows_root = next((root for root in app_workflow_roots if root.is_dir()), None)
    factory_workflows_root = resolve_factory_workflows_root()

    if normalized_host == "studio":
        selected_root: Path | None = factory_workflows_root or app_workflows_root
        if selected_root is not None:
            os.environ["MOZAIKS_WORKFLOWS_PATH"] = str(selected_root)
    elif normalized_host == "mozaiks":
        selected_root = app_workflows_root if (app_workflows_root is not None and app_workflows_root.is_dir()) else factory_workflows_root
        if selected_root is not None:
            os.environ["MOZAIKS_WORKFLOWS_PATH"] = str(selected_root)
    else:
        selected_root = app_workflows_root if (app_workflows_root is not None and app_workflows_root.is_dir()) else factory_workflows_root
        if selected_root is not None:
            os.environ["MOZAIKS_WORKFLOWS_PATH"] = str(selected_root)


__all__ = ["configure_repo_host_defaults"]
