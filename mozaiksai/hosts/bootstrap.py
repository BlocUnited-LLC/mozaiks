from __future__ import annotations

import os
from pathlib import Path


def _find_repo_root() -> Path | None:
    """Locate the Mozaiks repo root if running from a development checkout.

    Walks CWD upward looking for the characteristic ``factory_app/app`` and
    ``mozaiksai`` directories that only exist in the framework repo.  Returns
    ``None`` when running from an installed package without a repo alongside,
    in which case all paths must be supplied via environment variables.
    """
    cwd = Path.cwd()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "factory_app" / "app").is_dir() and (candidate / "mozaiksai").is_dir():
            return candidate
    return None


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
    """Apply defaults when running directly from the Mozaiks repo checkout.

    Does nothing when the canonical repo layout (``factory_app/app``) is not
    found in or above the current working directory.  In that case all paths
    must be configured explicitly via environment variables:

    - ``PLATFORM_PATH`` — path to the active app bundle directory
    - ``MOZAIKS_WORKFLOW_ROOTS`` — OS-path-separated list of workflow roots
    """
    normalized_host = str(host or "").strip().lower()
    if normalized_host not in {"platform", "studio", "mozaiks"}:
        return

    repo_root = _find_repo_root()
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

    if repo_root is not None:
        factory_workflows_root = (repo_root / "factory_app" / "workflows").resolve()
        if factory_workflows_root.is_dir():
            roots.append(str(factory_workflows_root))

    if roots:
        os.environ["MOZAIKS_WORKFLOW_ROOTS"] = os.pathsep.join(roots)


__all__ = ["configure_repo_host_defaults"]
