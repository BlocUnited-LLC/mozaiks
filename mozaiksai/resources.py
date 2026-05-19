from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path


def _resolve_repo_dir(relative_path: str) -> Path | None:
    repo_root = Path(__file__).resolve().parents[1]
    candidate = (repo_root / relative_path).resolve()
    return candidate if candidate.exists() and candidate.is_dir() else None


def _resolve_env_dir(env_name: str) -> Path | None:
    raw = str(os.getenv(env_name) or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate if candidate.exists() and candidate.is_dir() else None


def _resolve_package_dir(package_name: str) -> Path | None:
    try:
        spec = find_spec(package_name)
    except ValueError:
        # Some tests and dynamic import paths leave a module entry behind with
        # __spec__ unset. Fall back to repo/env resolution instead of failing.
        spec = None
    if spec is None:
        return None
    if spec.submodule_search_locations:
        for location in spec.submodule_search_locations:
            candidate = Path(location).resolve()
            if candidate.exists() and candidate.is_dir():
                return candidate
    origin = getattr(spec, "origin", None)
    if origin:
        candidate = Path(origin).resolve().parent
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def resolve_factory_app_root() -> Path | None:
    return (
        _resolve_env_dir("MOZAIKS_FACTORY_APP_PATH")
        or _resolve_repo_dir("factory_app")
        or _resolve_package_dir("factory_app")
    )


def resolve_factory_workflows_root() -> Path | None:
    root = resolve_factory_app_root()
    if root is None:
        return None
    candidate = (root / "workflows").resolve()
    return candidate if candidate.exists() and candidate.is_dir() else None


def resolve_factory_brand_root() -> Path | None:
    root = resolve_factory_app_root()
    if root is None:
        return None
    candidate = (root / "app" / "brand").resolve()
    return candidate if candidate.exists() and candidate.is_dir() else None


def resolve_web_shell_root() -> Path | None:
    return (
        _resolve_env_dir("MOZAIKS_WEB_SHELL_PATH")
        or _resolve_repo_dir("web_shell")
        or _resolve_package_dir("web_shell")
    )


def resolve_chat_ui_root() -> Path | None:
    return (
        _resolve_env_dir("MOZAIKS_CHAT_UI_PATH")
        or _resolve_repo_dir("chat-ui")
        or _resolve_package_dir("mozaiks_chat_ui")
    )


def resolve_chat_ui_src_root() -> Path | None:
    root = resolve_chat_ui_root()
    if root is None:
        return None
    candidate = (root / "src").resolve()
    return candidate if candidate.exists() and candidate.is_dir() else None


__all__ = [
    "resolve_chat_ui_root",
    "resolve_chat_ui_src_root",
    "resolve_factory_app_root",
    "resolve_factory_brand_root",
    "resolve_factory_workflows_root",
    "resolve_web_shell_root",
]
