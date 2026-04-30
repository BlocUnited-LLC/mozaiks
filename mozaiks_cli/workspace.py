from __future__ import annotations

from pathlib import Path


def resolve_workspace_root(explicit_directory: str | None) -> Path:
    return Path(explicit_directory or ".").resolve()


def resolve_active_app_root(workspace_root: Path) -> Path:
    root = workspace_root.resolve()
    candidates = [
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
