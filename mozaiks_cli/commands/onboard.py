"""mozaiks onboard - Guided setup from the local Dev/CLI layer."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import yaml

from mozaiks_cli.commands.init import (
    build_default_ai_config,
    build_default_control_plane_config,
    build_default_shell_config,
    create_scaffold,
)
from mozaiks_cli.studio_launcher import launch_studio
from mozaiks_cli.workspace import (
    is_framework_repo_root,
    resolve_active_app_root,
    resolve_theme_config_path,
    resolve_ui_route_manifest_path,
    resolve_workspace_root,
)

PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4.1",
    "local": "qwen3:latest",
    "other": "set-me-explicitly",
}


def run(args) -> None:
    """Execute the onboard command."""
    workspace_root = resolve_workspace_root(getattr(args, "directory", None))
    app_root = resolve_active_app_root(workspace_root)
    should_prompt = not bool(getattr(args, "non_interactive", False)) and sys.stdin is not None and not sys.stdin.closed

    missing_surfaces = _missing_scaffold_surfaces(workspace_root, app_root)
    if missing_surfaces:
        if is_framework_repo_root(workspace_root):
            print(f"Error: refusing to scaffold inside framework repo root: {workspace_root}")
            print("Use --dir <workspace> to target an app workspace directory.")
            return

        preset = getattr(args, "preset", None) or "chat"
        default_name = getattr(args, "name", None) or workspace_root.name or "my-app"
        print(f"No valid Mozaiks scaffold found in {workspace_root}")
        print(f"Bootstrapping a fresh '{preset}' scaffold for {default_name}.\n")
        create_scaffold(
            target_dir=workspace_root,
            preset=preset,
            app_name=default_name,
            starter=False,
        )
        app_root = resolve_active_app_root(workspace_root)

    app_json_path = app_root / "app.json"
    ai_json_path = app_root / "config" / "ai.json"
    shell_json_path = app_root / "config" / "shell.json"
    control_plane_runtime_path = workspace_root / "control_plane" / "config" / "runtime.yaml"

    app_config = _read_json(app_json_path)
    ai_config = _read_json(ai_json_path)
    shell_config = _read_json(shell_json_path)

    default_name = app_config.get("appName") or workspace_root.name
    app_name = _prompt_text(
        label="App name",
        explicit=getattr(args, "name", None),
        default=default_name,
        should_prompt=should_prompt,
    )

    existing_llm = ai_config.get("llm") or {}
    provider = _prompt_choice(
        label="Which AI provider do you want to start with?",
        explicit=getattr(args, "provider", None),
        default=existing_llm.get("provider") or "anthropic",
        options=["anthropic", "openai", "local", "other"],
        should_prompt=should_prompt,
    )
    model = _prompt_text(
        label="Default model",
        explicit=getattr(args, "model", None),
        default=existing_llm.get("model") or PROVIDER_DEFAULT_MODELS[provider],
        should_prompt=should_prompt,
    )

    _apply_app_config(app_config=app_config, app_name=app_name)
    _apply_ai_config(ai_config=ai_config, app_name=app_name, provider=provider, model=model)
    control_plane_config = _apply_control_plane_config(control_plane_runtime_path)

    _write_json(app_json_path, app_config)
    print(f"Updated {app_json_path.relative_to(workspace_root)}")
    _write_json(ai_json_path, ai_config)
    print(f"Updated {ai_json_path.relative_to(workspace_root)}")
    _write_yaml(control_plane_runtime_path, control_plane_config)
    print(f"Updated {control_plane_runtime_path.relative_to(workspace_root)}")
    refreshed_shell_config = _maybe_refresh_shell_config(shell_config, app_name)
    if refreshed_shell_config is not None:
        _write_json(shell_json_path, refreshed_shell_config)
        print(f"Updated {shell_json_path.relative_to(workspace_root)}")

    from mozaiks_cli.commands.sync_agent_guidance import auto_sync_agent_guidance
    auto_sync_agent_guidance(workspace_root)

    print("\nSetup complete.")

    should_open_studio = bool(getattr(args, "open_studio", False))
    if not should_open_studio and should_prompt:
        should_open_studio = _prompt_yes_no(
            label="Open Studio now?",
            default=True,
            should_prompt=should_prompt,
        )

    if should_open_studio:
        print("Opening Studio...")
        result = launch_studio(
            workspace_root=workspace_root,
            backend_port=int(getattr(args, "backend_port", 8000)),
            frontend_port=int(getattr(args, "frontend_port", 3000)),
            open_browser=not bool(getattr(args, "no_browser", False)),
        )
        print("\nStudio launched.")
        print(f"  Backend: {result['backend_url']}")
        if result["studio_url"]:
            print(f"  Studio: {result['studio_url']}")
        elif result["frontend_available"]:
            print(f"  Frontend: {result['frontend_url']}")
        else:
            print("  Frontend shell is unavailable outside the framework repo checkout.")


def _missing_scaffold_surfaces(workspace_root: Path, app_root: Path) -> list[str]:
    theme_path = resolve_theme_config_path(app_root)
    ui_path = resolve_ui_route_manifest_path(app_root)
    required = {
        str((app_root / "app.json").relative_to(workspace_root)): app_root / "app.json",
        str((app_root / "config" / "ai.json").relative_to(workspace_root)): app_root / "config" / "ai.json",
        str((app_root / "config" / "shell.json").relative_to(workspace_root)): app_root / "config" / "shell.json",
        str(theme_path.relative_to(workspace_root)): theme_path,
        str(ui_path.relative_to(workspace_root)): ui_path,
    }
    return [rel_path for rel_path, path in required.items() if not path.exists()]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def _prompt_text(
    *,
    label: str,
    explicit: str | None,
    default: str,
    should_prompt: bool,
    allow_empty: bool = False,
) -> str:
    if explicit is not None:
        value = explicit.strip()
        if value or allow_empty:
            return value
        return default

    if not should_prompt:
        return default

    prompt_suffix = f" [{default}]" if default else ""
    while True:
        try:
            response = input(f"{label}{prompt_suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            response = ""

        if response:
            return response
        if allow_empty:
            return default if default else ""
        if default:
            return default
        print("Please enter a value.")


def _prompt_choice(
    *,
    label: str,
    explicit: str | None,
    default: str,
    options: list[str],
    should_prompt: bool,
) -> str:
    if explicit is not None:
        normalized = explicit.strip().lower()
        if normalized in options:
            return normalized
        raise ValueError(f"Invalid value '{explicit}' for {label}. Expected one of: {', '.join(options)}")

    if not should_prompt:
        return default

    print(label)
    for index, option in enumerate(options, start=1):
        marker = " (default)" if option == default else ""
        print(f"  {index}. {option}{marker}")

    while True:
        try:
            response = input("Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = ""

        if not response:
            return default
        if response in options:
            return response
        if response.isdigit():
            index = int(response) - 1
            if 0 <= index < len(options):
                return options[index]
        print(f"Enter one of: {', '.join(options)}")


def _prompt_yes_no(
    *,
    label: str,
    default: bool,
    should_prompt: bool,
) -> bool:
    if not should_prompt:
        return default

    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        try:
            response = input(f"{label}{suffix}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = ""

        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        print("Enter y or n.")


def _apply_app_config(*, app_config: dict, app_name: str) -> None:
    app_config.pop("onboarding", None)
    app_config["appName"] = app_name
    app_config.setdefault("startup", {}).setdefault("landing_spot", "/")


def _apply_ai_config(*, ai_config: dict, app_name: str, provider: str, model: str) -> None:
    defaults = build_default_ai_config(app_name, starter=False)
    ai_config.pop("app_context", None)
    ai_config["ask"] = deepcopy(defaults["ask"])
    ai_config["chat"] = deepcopy(defaults["chat"])
    ai_config["workflows"] = deepcopy(defaults["workflows"])
    ai_config.pop("control_plane", None)
    ai_config["llm"] = {
        "provider": provider,
        "model": model,
    }


def _apply_control_plane_config(path: Path) -> dict:
    existing: dict = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            loaded = None
        if isinstance(loaded, dict):
            existing = loaded

    merged = build_default_control_plane_config()
    merged.update(existing)
    return merged


def _maybe_refresh_shell_config(shell_config: dict, app_name: str) -> dict | None:
    if not _is_legacy_shell_placeholder(shell_config):
        return None
    return build_default_shell_config(app_name)


def _is_legacy_shell_placeholder(shell_config: dict) -> bool:
    if not isinstance(shell_config, dict):
        return False

    header = shell_config.get("header") if isinstance(shell_config.get("header"), dict) else {}
    logo = header.get("logo") if isinstance(header.get("logo"), dict) else {}
    shortcuts = shell_config.get("shortcuts") if isinstance(shell_config.get("shortcuts"), dict) else {}
    navigation = shell_config.get("navigation") if isinstance(shell_config.get("navigation"), dict) else {}
    navigation_policy = navigation.get("policy") if isinstance(navigation.get("policy"), dict) else {}
    chrome = shell_config.get("chrome") if isinstance(shell_config.get("chrome"), dict) else {}
    notifications = shell_config.get("notifications") if isinstance(shell_config.get("notifications"), dict) else {}
    footer = shell_config.get("footer") if isinstance(shell_config.get("footer"), dict) else {}
    profile = shell_config.get("profile") if isinstance(shell_config.get("profile"), dict) else {}

    return (
        header.get("pages") == []
        and header.get("actions") == []
        and logo.get("src") is None
        and logo.get("wordmark") is None
        and isinstance(logo.get("alt"), str)
        and logo.get("href") in {"/", None}
        and shortcuts.get("profile") == ["profile", "signout"]
        and shortcuts.get("mobile") == ["profile"]
        and shortcuts.get("footer") == []
        and shortcuts.get("footerHideOnMobile") is True
        and navigation_policy.get("desktop", {}).get("global") == "header"
        and navigation_policy.get("mobile", {}).get("global") == "bottomBar"
        and chrome.get("defaultMode") == "standard"
        and notifications.get("show") is False
        and notifications.get("path") == "/notifications"
        and notifications.get("emptyText") == "No notifications yet"
        and footer.get("links") == []
        and footer.get("visible") is True
        and profile.get("show") in {False, None}
        and profile.get("menu") in ([], None)
    )
