"""CLI entry point for ``python -m mozaiksai.cli``.

Usage:
    python -m mozaiksai.cli init                  # first-run bootstrap ritual
    python -m mozaiksai.cli up                    # one-command local startup
    python -m mozaiksai.cli doctor                # setup diagnostics
    python -m mozaiksai.cli generate              # all artifacts
    python -m mozaiksai.cli generate --realm       # only Keycloak realm
    python -m mozaiksai.cli generate --theme       # only Keycloak theme
    python -m mozaiksai.cli generate --check       # verify artifacts are current
    python -m mozaiksai.cli generate --dry         # preview without writing

Shorthand (when installed):
    mozaiks generate
    mozaiks generate --theme --dry
"""

from __future__ import annotations

import argparse
import sys

from mozaiksai.cli.paths import find_project_root


# ── Generator registry ───────────────────────────────────────────────────────
# Each entry: (label, module_path, supports_check)
GENERATORS = [
    ("Keycloak realm", "mozaiksai.cli.generators.realm", False),
    ("Keycloak theme", "mozaiksai.cli.generators.theme", True),
]


def _run_generate(args: argparse.Namespace) -> int:
    """Run the 'generate' sub-command."""
    from importlib import import_module

    root = find_project_root()

    # If user passed specific flags, only run those generators
    specific = args.realm or args.theme
    targets = []
    if not specific:
        targets = GENERATORS  # run all
    else:
        if args.realm:
            targets.append(GENERATORS[0])
        if args.theme:
            targets.append(GENERATORS[1])

    failed: list[str] = []

    for label, mod_path, supports_check in targets:
        print(f"\n{'-' * 60}")
        print(f"  {label}")
        print(f"{'-' * 60}")

        mod = import_module(mod_path)
        kwargs = {"root": root, "dry_run": args.dry}
        if supports_check and args.check:
            kwargs["check"] = True

        rc = mod.run(**kwargs)
        if rc != 0:
            failed.append(label)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    else:
        print("OK: All generators completed successfully")
        return 0


def _run_init(args: argparse.Namespace) -> int:
    from mozaiksai.cli.bootstrap import run as run_bootstrap

    auth_enabled = None
    if args.auth_enabled is not None:
        auth_enabled = args.auth_enabled.lower() == "true"

    return run_bootstrap(
        root=find_project_root(),
        non_interactive=args.non_interactive,
        app_name=args.app_name,
        app_id=args.app_id,
        api_url=args.api_url,
        ws_url=args.ws_url,
        openai_api_key=args.openai_api_key,
        auth_enabled=auth_enabled,
        skip_generate=args.skip_generate,
        llm_mode=args.llm,
        llm_model=args.llm_model,
    )


def _run_doctor(args: argparse.Namespace) -> int:
    from mozaiksai.cli.doctor import run as run_doctor

    return run_doctor(
        root=find_project_root(),
        strict=args.strict,
        timeout_seconds=args.timeout,
        skip_network=args.skip_network,
    )


def _run_up(args: argparse.Namespace) -> int:
    from mozaiksai.cli.up import run as run_up

    return run_up(
        root=find_project_root(),
        mode=args.mode,
        start_frontend=args.frontend,
        build=args.build,
        detach=not args.no_detach,
        skip_generate=args.skip_generate,
        skip_doctor=args.skip_doctor,
        strict_doctor=args.strict_doctor,
    )


def main(argv: list[str] | None = None) -> int:
    """Parse args and dispatch to the appropriate sub-command."""
    parser = argparse.ArgumentParser(
        prog="mozaiks",
        description="MozaiksAI platform CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # -- init -------------------------------------------------------------
    init_cmd = sub.add_parser(
        "init",
        help="Run first-run bootstrap ritual",
        description=(
            "Guided setup for app/app.json + .env, then generates realm/theme "
            "artifacts so first-time developers can start quickly."
        ),
    )
    init_cmd.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use defaults and provided flags without prompts",
    )
    init_cmd.add_argument("--app-name", help="App display name")
    init_cmd.add_argument("--app-id", help="App ID slug")
    init_cmd.add_argument("--api-url", help="Backend API URL")
    init_cmd.add_argument("--ws-url", help="Backend WebSocket URL")
    init_cmd.add_argument("--openai-api-key", help="OpenAI API key to write into .env")
    init_cmd.add_argument(
        "--auth-enabled",
        choices=["true", "false"],
        help="Whether AUTH_ENABLED should be true/false in .env",
    )
    init_cmd.add_argument(
        "--skip-generate",
        action="store_true",
        help="Skip realm/theme generation at the end of init",
    )
    init_cmd.add_argument(
        "--llm",
        action="store_true",
        help="Use an LLM-guided one-question-at-a-time ritual",
    )
    init_cmd.add_argument(
        "--llm-model",
        help="Model for --llm mode (default: MOZAIKS_BOOTSTRAP_MODEL or gpt-4o-mini)",
    )

    # -- up ---------------------------------------------------------------
    up_cmd = sub.add_parser(
        "up",
        help="Start the local stack with preflight checks",
    )
    up_cmd.add_argument(
        "--mode",
        choices=["docker", "local"],
        default="docker",
        help="docker: docker compose stack (default), local: start-dev.ps1 local mode",
    )
    up_cmd.add_argument(
        "--frontend",
        action="store_true",
        help="Also start the frontend dev server",
    )
    up_cmd.add_argument(
        "--build",
        action="store_true",
        help="Pass --build to docker compose up",
    )
    up_cmd.add_argument(
        "--no-detach",
        action="store_true",
        help="Run docker compose in attached mode",
    )
    up_cmd.add_argument(
        "--skip-generate",
        action="store_true",
        help="Skip generate preflight",
    )
    up_cmd.add_argument(
        "--skip-doctor",
        action="store_true",
        help="Skip doctor preflight/post-checks",
    )
    up_cmd.add_argument(
        "--strict-doctor",
        action="store_true",
        help="Fail startup when doctor warnings are present",
    )

    # -- doctor -----------------------------------------------------------
    doctor_cmd = sub.add_parser(
        "doctor",
        help="Run setup diagnostics with actionable fixes",
    )
    doctor_cmd.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures (exit code 1)",
    )
    doctor_cmd.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="HTTP timeout in seconds for runtime checks (default: 2.0)",
    )
    doctor_cmd.add_argument(
        "--skip-network",
        action="store_true",
        help="Skip localhost HTTP health checks",
    )

    # ── generate ─────────────────────────────────────────────────────────
    gen = sub.add_parser(
        "generate",
        help="Regenerate declarative artifacts from config files",
        description=(
            "Reads app.json and brand.json and regenerates all derived "
            "infrastructure artifacts (Keycloak realm, theme CSS, etc.)."
        ),
    )
    gen.add_argument(
        "--realm", action="store_true",
        help="Only regenerate Keycloak realm-export.json",
    )
    gen.add_argument(
        "--theme", action="store_true",
        help="Only regenerate Keycloak login theme",
    )
    gen.add_argument(
        "--dry", action="store_true",
        help="Preview output without writing files",
    )
    gen.add_argument(
        "--check", action="store_true",
        help="Verify generated files are up-to-date (CI mode)",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "generate":
        return _run_generate(args)
    if args.command == "init":
        return _run_init(args)
    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "up":
        return _run_up(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
