from __future__ import annotations

from pathlib import Path


def resolve_workspace_root(explicit_directory: str | None) -> Path:
    return Path(explicit_directory or ".").resolve()


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
