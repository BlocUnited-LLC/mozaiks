from __future__ import annotations

import os
from pathlib import Path

from mozaiksai.resources import resolve_factory_workflows_root


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
    return candidate


def configure_repo_host_defaults(host: str) -> None:
    """Apply default app/workflow paths for Studio and platform hosts.

    The active app workspace still comes from PLATFORM_PATH or
    MOZAIKS_APP_WORKSPACE_PATH. Shared factory workflows come from the packaged
    factory_app bundle when that package is installed.

    If callers set MOZAIKS_WORKFLOW_ROOTS or MOZAIKS_WORKFLOWS_PATH explicitly,
    those values remain authoritative.
    """
    normalized_host = str(host or "").strip().lower()
    if normalized_host not in {"platform", "studio", "mozaiks"}:
        return

    external_workspace_root = str(os.getenv("MOZAIKS_APP_WORKSPACE_PATH") or "").strip()

    if not str(os.getenv("PLATFORM_PATH") or "").strip():
        if external_workspace_root:
            os.environ["PLATFORM_PATH"] = str(_resolve_app_bundle_dir(external_workspace_root))

    if str(os.getenv("MOZAIKS_WORKFLOW_ROOTS") or "").strip():
        return
    if str(os.getenv("MOZAIKS_WORKFLOWS_PATH") or "").strip():
        return

    roots: list[str] = []

    platform_path = str(os.getenv("PLATFORM_PATH") or "").strip()
    if platform_path:
        app_root = _resolve_app_bundle_dir(platform_path)
        roots.append(str((app_root / "workflows").resolve()))

    factory_workflows_root = resolve_factory_workflows_root()
    if factory_workflows_root is not None:
        roots.append(str(factory_workflows_root))

    if roots:
        os.environ["MOZAIKS_WORKFLOW_ROOTS"] = os.pathsep.join(roots)


__all__ = ["configure_repo_host_defaults"]
