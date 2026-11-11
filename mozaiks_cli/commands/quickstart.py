"""mozaiks quickstart - Minimal first-run path for builders."""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path

import mozaiks_cli.commands.onboard as onboard_command
from mozaiks_cli.workspace import load_workspace_dotenv, resolve_workspace_root


def run(args) -> int:
    """Execute the quickstart command; return the process exit code."""
    workspace_root = resolve_workspace_root(getattr(args, "directory", None))
    # Warnings below must see the workspace .env that Studio will run with.
    load_workspace_dotenv(workspace_root)
    _print_intro(workspace_root)
    _print_environment_warnings(args)

    return onboard_command.run(
        Namespace(
            directory=str(workspace_root),
            preset=getattr(args, "preset", "chat"),
            name=getattr(args, "name", None),
            provider=getattr(args, "provider", None),
            model=getattr(args, "model", None),
            non_interactive=True,
            open_studio=True,
            backend_port=int(getattr(args, "backend_port", 8000)),
            frontend_port=int(getattr(args, "frontend_port", 3000)),
            no_browser=bool(getattr(args, "no_browser", False)),
        )
    )


def _print_intro(workspace_root: Path) -> None:
    print(f"Quickstart workspace: {workspace_root}")
    print(
        "Bootstrapping workspace and opening Studio. "
        "Use Studio to create your first app.\n"
    )


def _print_environment_warnings(args) -> None:
    warnings: list[str] = []
    # Onboarding flags describe the workspace. Runtime model selection is env-owned.
    provider = os.environ.get("LLM_PRIMARY_API_TYPE", "google").strip().lower() or "google"

    if not os.environ.get("MONGO_URI"):
        warnings.append(
            "MongoDB is required to start Studio. If MONGO_URI is not set, the generated workspace "
            "uses mongodb://localhost:27017/mozaiks; make sure local MongoDB is running or set MONGO_URI "
            "to a reachable MongoDB Atlas/local URI."
        )

    provider_keys = {
        "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
    }
    if provider in provider_keys and not any(
        os.environ.get(key, "").strip() for key in ("LLM_PRIMARY_API_KEY", *provider_keys[provider])
    ):
        warnings.append(f"{provider_keys[provider][0]} is not set for the runtime provider ({provider}).")
    elif provider not in {*provider_keys, "ollama"}:
        warnings.append(
            f"{provider.upper()} provider selected. Make sure the matching provider credentials are available."
        )

    if not warnings:
        return

    print("Environment warnings:")
    for warning in warnings:
        print(f"  - {warning}")
    print("")
