from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def resolve_workspace_root(explicit_directory: str | None) -> Path:
    return Path(explicit_directory or ".").resolve()


def load_workspace_dotenv(workspace_root: Path) -> Path | None:
    """Load ``<workspace_root>/.env`` into the process environment.

    The CLI is a process entry point, so it is where a local ``.env`` is read:
    explicitly at CLI entry points, from the workspace they operate on, and never
    overriding a variable the process already has. Library imports never read a
    ``.env`` (issue #778), and nothing here searches parent directories.

    Returns the file that was loaded, or ``None`` when the workspace has none.
    """
    env_file = workspace_root.resolve() / ".env"
    if os.getenv("PYTHON_DOTENV_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
        return None
    if not env_file.is_file():
        return None
    load_dotenv(dotenv_path=env_file, override=False)
    return env_file


def is_framework_repo_root(path: Path) -> bool:
    """Return True when the path appears to be this framework repository root."""
    root = path.resolve()
    required_files = ["AGENTS.md", "CLAUDE.md", "ARCHITECTURE.md", "pyproject.toml"]
    required_dirs = ["mozaiksai", "factory_app", "mozaiks_cli"]

    for filename in required_files:
        if not (root / filename).is_file():
            return False

    for dirname in required_dirs:
        if not (root / dirname).is_dir():
            return False

    return True


def resolve_active_app_root(workspace_root: Path) -> Path:
    root = workspace_root.resolve()
    candidates = [
        root / "factory_app" / "app",
        root / "app",
        root,
    ]
    for candidate in candidates:
        if (candidate / "app.json").exists():
            return candidate.resolve()
    return (root / "app").resolve()


def resolve_theme_config_path(app_root: Path) -> Path:
    return app_root / "brand" / "theme_config.json"


def resolve_ui_route_manifest_path(app_root: Path) -> Path:
    return app_root / "ui" / "route_manifest.json"
