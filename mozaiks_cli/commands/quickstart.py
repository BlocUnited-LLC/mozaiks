"""mozaiks quickstart - Minimal first-run path for builders."""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path

import mozaiks_cli.commands.onboard as onboard_command
from mozaiks_cli.workspace import resolve_workspace_root


def run(args) -> None:
    """Execute the quickstart command."""
    workspace_root = resolve_workspace_root(getattr(args, "directory", None))
    _print_intro(workspace_root)
    _print_environment_warnings(args)

    onboard_command.run(
        Namespace(
            directory=str(workspace_root),
            preset=getattr(args, "preset", "chat"),
            name=getattr(args, "name", None),
            journey=getattr(args, "journey", None),
            goal=getattr(args, "goal", None),
            provider=getattr(args, "provider", None),
            model=getattr(args, "model", None),
            tagline=None,
            theme_primary=None,
            admin_email=None,
            existing_url=None,
            host_owned_summary=None,
            non_interactive=True,
            full_setup=False,
            open_studio=True,
            backend_port=int(getattr(args, "backend_port", 8000)),
            frontend_port=int(getattr(args, "frontend_port", 3000)),
            no_browser=bool(getattr(args, "no_browser", False)),
        )
    )


def _print_intro(workspace_root: Path) -> None:
    print(f"Quickstart workspace: {workspace_root}")
    print("Mode: builder")
    print("This path bootstraps the workspace with minimal defaults and opens Studio.\n")


def _print_environment_warnings(args) -> None:
    warnings: list[str] = []
    provider = getattr(args, "provider", None)

    if not os.environ.get("MONGO_URI"):
        warnings.append(
            "MONGO_URI is not set. Studio can still launch, but builds will fail until Mongo is configured."
        )

    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        warnings.append("OPENAI_API_KEY is not set for the selected provider.")
    elif provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        warnings.append("ANTHROPIC_API_KEY is not set for the selected provider.")
    elif provider not in {"local", None} and provider not in {"openai", "anthropic"}:
        warnings.append(
            f"{provider.upper()} provider selected. Make sure the matching provider credentials are available."
        )
    elif provider is None and not any(os.environ.get(key) for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")):
        warnings.append(
            "No provider API key detected. Set OPENAI_API_KEY or ANTHROPIC_API_KEY before starting a build."
        )

    if not warnings:
        return

    print("Environment warnings:")
    for warning in warnings:
        print(f"  - {warning}")
    print("")
