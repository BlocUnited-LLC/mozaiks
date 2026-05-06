"""mozaiks onboard - Guided setup from the local Dev/CLI layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mozaiks_cli.commands.init import create_scaffold
from mozaiks_cli.studio_launcher import launch_studio
from mozaiks_cli.workspace import (
    resolve_active_app_root,
    resolve_theme_config_path,
    resolve_ui_route_manifest_path,
    resolve_workspace_root,
)


THEME_PRESETS = {
    "cyan": {
        "main": "#06b6d4",
        "light": "#67e8f9",
        "dark": "#0e7490",
    },
    "teal": {
        "main": "#0f766e",
        "light": "#14b8a6",
        "dark": "#115e59",
    },
    "blue": {
        "main": "#1d4ed8",
        "light": "#60a5fa",
        "dark": "#1e40af",
    },
    "emerald": {
        "main": "#059669",
        "light": "#34d399",
        "dark": "#047857",
    },
    "slate": {
        "main": "#334155",
        "light": "#94a3b8",
        "dark": "#1e293b",
    },
    "amber": {
        "main": "#d97706",
        "light": "#fbbf24",
        "dark": "#b45309",
    },
    "rose": {
        "main": "#e11d48",
        "light": "#fb7185",
        "dark": "#be123c",
    },
}


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
    full_setup = bool(getattr(args, "full_setup", False))
    has_explicit_extended_inputs = any(
        getattr(args, name, None) is not None
        for name in (
            "journey",
            "goal",
            "tagline",
            "theme_primary",
            "admin_email",
            "existing_url",
            "host_owned_summary",
        )
    )
    collect_extended_setup = full_setup or has_explicit_extended_inputs
    missing_surfaces = _missing_scaffold_surfaces(workspace_root, app_root)
    if missing_surfaces:
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
    theme_json_path = resolve_theme_config_path(app_root)

    app_config = _read_json(app_json_path)
    ai_config = _read_json(ai_json_path)
    shell_config = _read_json(shell_json_path)
    theme_config = _read_json(theme_json_path)

    mode_label = "full" if collect_extended_setup else "minimal"
    print(f"Onboarding Mozaiks app bundle at: {workspace_root}")
    print(f"Setup mode: {mode_label}\n")

    default_name = app_config.get("appName") or workspace_root.name
    app_name = _prompt_text(
        label="App name",
        explicit=getattr(args, "name", None),
        default=default_name,
        should_prompt=should_prompt,
    )

    previous_onboarding = app_config.get("onboarding") or {}
    journey_default = previous_onboarding.get("journey") or "greenfield_app"
    if collect_extended_setup:
        journey = _prompt_choice(
            label="What are you doing first?",
            explicit=getattr(args, "journey", None),
            default=journey_default,
            options=["greenfield_app", "brownfield_app"],
            should_prompt=should_prompt,
        )
    else:
        journey = getattr(args, "journey", None) or journey_default

    default_goal = previous_onboarding.get("first_goal") or (
        "Define the first real product capability" if journey == "greenfield_app" else "Bridge the first useful host capability"
    )
    if collect_extended_setup:
        first_goal = _prompt_text(
            label="What should Mozaiks help with first?",
            explicit=getattr(args, "goal", None),
            default=default_goal,
            should_prompt=should_prompt,
        )
    else:
        first_goal = getattr(args, "goal", None) or default_goal

    existing_url = None
    host_owned_summary = None
    if journey == "brownfield_app":
        if collect_extended_setup:
            existing_url = _prompt_text(
                label="Existing app URL (optional)",
                explicit=getattr(args, "existing_url", None),
                default=previous_onboarding.get("existing_app_url") or "",
                should_prompt=should_prompt,
                allow_empty=True,
            )
            host_owned_summary = _prompt_text(
                label="What should stay host-owned? (optional)",
                explicit=getattr(args, "host_owned_summary", None),
                default=previous_onboarding.get("host_owned_summary") or "",
                should_prompt=should_prompt,
                allow_empty=True,
            )
        else:
            existing_url = getattr(args, "existing_url", None) or previous_onboarding.get("existing_app_url") or None
            host_owned_summary = getattr(args, "host_owned_summary", None) or previous_onboarding.get("host_owned_summary") or None

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

    identity = theme_config.get("identity") or {}
    if collect_extended_setup:
        tagline = _prompt_text(
            label="Brand tagline (optional)",
            explicit=getattr(args, "tagline", None),
            default=identity.get("tagline") or "",
            should_prompt=should_prompt,
            allow_empty=True,
        )
    else:
        tagline = getattr(args, "tagline", None)
        if tagline is None:
            tagline = identity.get("tagline") or ""

    theme_root = theme_config.get("theme") or {}
    theme_primary_default = theme_root.get("primary") if theme_root.get("primary") in THEME_PRESETS else "cyan"
    if collect_extended_setup:
        theme_primary = _prompt_choice(
            label="Primary brand color",
            explicit=getattr(args, "theme_primary", None),
            default=theme_primary_default,
            options=list(THEME_PRESETS.keys()),
            should_prompt=should_prompt,
        )
    else:
        theme_primary = getattr(args, "theme_primary", None) or theme_primary_default

    default_admin_email = ""
    admins = app_config.get("admins") or []
    if admins:
        default_admin_email = admins[0]

    if collect_extended_setup:
        admin_email = _prompt_text(
            label="Admin email (optional)",
            explicit=getattr(args, "admin_email", None),
            default=default_admin_email,
            should_prompt=should_prompt,
            allow_empty=True,
        )
    else:
        admin_email = getattr(args, "admin_email", None)
        if admin_email is None:
            admin_email = default_admin_email

    _apply_app_config(
        app_config=app_config,
        app_name=app_name,
        journey=journey,
        first_goal=first_goal,
        existing_url=existing_url,
        host_owned_summary=host_owned_summary,
        admin_email=admin_email,
    )
    _apply_ai_config(
        ai_config=ai_config,
        app_name=app_name,
        journey=journey,
        first_goal=first_goal,
        provider=provider,
        model=model,
    )
    _apply_shell_config(shell_config=shell_config, app_name=app_name)
    _apply_theme_config(
        theme_config=theme_config,
        app_name=app_name,
        tagline=tagline,
        theme_primary=theme_primary,
    )

    _write_json(app_json_path, app_config)
    print(f"Updated {app_json_path.relative_to(workspace_root)}")
    _write_json(ai_json_path, ai_config)
    print(f"Updated {ai_json_path.relative_to(workspace_root)}")
    _write_json(shell_json_path, shell_config)
    print(f"Updated {shell_json_path.relative_to(workspace_root)}")
    _write_json(theme_json_path, theme_config)
    print(f"Updated {theme_json_path.relative_to(workspace_root)}")

    print("\nOnboarding completed.")
    _show_next_steps(
        workspace_root=workspace_root,
        journey=journey,
        first_goal=first_goal,
        admin_email=admin_email,
        full_setup=collect_extended_setup,
    )
    should_open_studio = bool(getattr(args, "open_studio", False))
    if not should_open_studio and should_prompt:
        should_open_studio = _prompt_yes_no(
            label="Open Studio now?",
            default=True,
            should_prompt=should_prompt,
        )

    if should_open_studio:
        result = launch_studio(
            workspace_root=workspace_root,
            backend_port=int(getattr(args, "backend_port", 8000)),
            frontend_port=int(getattr(args, "frontend_port", 3000)),
            open_browser=not bool(getattr(args, "no_browser", False)),
        )
        print("\nStudio launched.")
        print(f"  Backend: {result['backend_url']}")
        if result["studio_url"]:
            print(f"  Studio:  {result['studio_url']}")
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


def _apply_app_config(
    *,
    app_config: dict,
    app_name: str,
    journey: str,
    first_goal: str,
    existing_url: str | None,
    host_owned_summary: str | None,
    admin_email: str,
) -> None:
    app_config["appName"] = app_name
    app_config.setdefault("startup", {}).setdefault("landing_spot", "/")
    onboarding = {
        "journey": journey,
        "first_goal": first_goal,
        "existing_app_url": existing_url or None,
        "host_owned_summary": host_owned_summary or None,
    }
    app_config["onboarding"] = onboarding

    admins = app_config.get("admins")
    if not isinstance(admins, list):
        admins = []
    normalized_admin = admin_email.strip().lower() if isinstance(admin_email, str) and admin_email.strip() else ""
    if normalized_admin and normalized_admin not in admins:
        admins.append(normalized_admin)
    app_config["admins"] = admins


def _apply_ai_config(
    *,
    ai_config: dict,
    app_name: str,
    journey: str,
    first_goal: str,
    provider: str,
    model: str,
) -> None:
    ask = ai_config.setdefault("ask", {})
    ask["ask_mode_prompt"] = _build_ask_prompt(
        app_name=app_name,
        journey=journey,
        first_goal=first_goal,
    )

    ai_config.setdefault("chat", {}).setdefault("chat_startup_mode", "ask")
    ai_config.setdefault("workflows", {}).setdefault("entry_point", None)
    ai_config.setdefault("workflows", {}).setdefault(
        "resume_policy", "last_active_then_oldest_then_entry_point"
    )
    ai_config["llm"] = {
        "provider": provider,
        "model": model,
    }
    ai_config["app_context"] = {
        "journey": journey,
        "first_goal": first_goal,
    }


def _build_ask_prompt(*, app_name: str, journey: str, first_goal: str) -> str:
    if journey == "brownfield_app":
        return (
            f"You are the assistant for {app_name}. Help the builder augment an existing app safely. "
            f"Prioritize bridging and scoped adoption first. Current first goal: {first_goal}."
        )
    return (
        f"You are the assistant for {app_name}. Help the builder define the first real product capability "
        f"before generating files. Current first goal: {first_goal}."
    )


def _apply_shell_config(*, shell_config: dict, app_name: str) -> None:
    header = shell_config.setdefault("header", {})
    logo = header.setdefault("logo", {})
    logo.setdefault("src", None)
    logo.setdefault("wordmark", None)
    logo["alt"] = f"{app_name} logo"
    logo.setdefault("href", "/")

    shell_config.setdefault("profile", {}).setdefault("show", False)
    shell_config.setdefault("notifications", {}).setdefault("show", False)
    shell_config.setdefault("footer", {}).setdefault("visible", True)


def _apply_theme_config(*, theme_config: dict, app_name: str, tagline: str, theme_primary: str) -> None:
    theme = theme_config.setdefault("theme", {})
    theme["primary"] = theme_primary
    branding = theme.setdefault("branding", {})
    branding["app_name"] = app_name

    identity = theme_config.setdefault("identity", {})
    identity["name"] = app_name
    identity["app_name"] = app_name
    identity["tagline"] = tagline or identity.get("tagline") or "Mozaiks app bundle"

    colors = theme_config.setdefault("colors", {})
    palette = THEME_PRESETS[theme_primary]
    colors["primary"] = {
        "main": palette["main"],
        "light": palette["light"],
        "dark": palette["dark"],
        "name": theme_primary,
    }
    border = colors.setdefault("border", {})
    border["accent"] = palette["main"]

    ui = theme_config.setdefault("ui", {})
    chat = ui.setdefault("chat", {})
    modes = chat.setdefault("modes", {})
    ask_mode = modes.setdefault("ask", {})
    ask_mode.setdefault("label", "Ask")
    ask_mode["tint"] = palette["main"]


def _show_next_steps(
    *,
    workspace_root: Path,
    journey: str,
    first_goal: str,
    admin_email: str,
    full_setup: bool,
) -> None:
    app_root = resolve_active_app_root(workspace_root)
    print("\nNext Steps:")
    print(f"  1. Review {app_root / 'app.json'} and confirm the onboarding summary")
    print(f"  2. Confirm your default AI provider and model in {app_root / 'config' / 'ai.json'}")
    print(f"  3. Open Studio with: mozaiks studio --dir \"{workspace_root}\" --open")
    if journey == "brownfield_app":
        print("  4. Use Studio to bridge the first host-owned surface before attempting broader generation")
    else:
        print("  4. Use the factory_app workflows in Studio to define the first real product surface")
    if first_goal:
        print("  5. Optional seed prompt for Studio:")
        print(f"     {first_goal}")
    if not full_setup:
        print("  6. Optional: run `mozaiks onboard --full` later for detailed brand/admin setup")
    if admin_email:
        print(f"  7. Verify admin access in {app_root / 'app.json'}")
