"""mozaiks serve - Start the Mozaiks runtime for an app workspace."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from mozaiks_cli.unauthenticated_bind import warn_if_unauthenticated_bind
from mozaiks_cli.workspace import load_workspace_dotenv

_HOST_MODULES = {
    "runtime": "mozaiksai.hosts.runtime:app",
    "platform": "mozaiksai.hosts.platform:app",
    "studio": "mozaiksai.hosts.studio:app",
}


def run(args) -> None:
    workspace = Path(getattr(args, "workspace", ".")).resolve()
    host = getattr(args, "host", "platform")
    port = int(getattr(args, "port", 8000))
    listen = getattr(args, "listen", "127.0.0.1")
    reload = bool(getattr(args, "reload", False))

    app_root = _resolve_app_root(workspace)
    if app_root is None:
        print(f"Error: no app bundle found at {workspace}")
        print("Expected app/app.json or app.json in the workspace directory.")
        sys.exit(1)

    from mozaiks_cli.commands.sync_agent_guidance import auto_sync_agent_guidance
    auto_sync_agent_guidance(workspace)

    app_module = _HOST_MODULES.get(host)
    if not app_module:
        print(f"Error: unknown host '{host}'. Choices: {', '.join(_HOST_MODULES)}")
        sys.exit(1)

    os.environ["MOZAIKS_APP_WORKSPACE_PATH"] = str(app_root)
    os.environ["PLATFORM_PATH"] = str(app_root)
    os.environ.setdefault("MOZAIKS_HOST", host)

    # Load .env from the workspace directory so the app bundle owns its config,
    # regardless of which directory the CLI is invoked from. This is the only
    # .env the served host sees: library imports never read one (issue #778).
    # If .env doesn't exist but .env.example does, create it automatically.
    env_file = workspace / ".env"
    env_example = workspace / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy(env_example, env_file)
        print(f"Created {env_file} from .env.example — fill in your OPENAI_API_KEY and MONGO_URI before use.")
    load_workspace_dotenv(workspace)

    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is required. It is included in the mozaiks package dependencies.")
        sys.exit(1)

    # uvicorn requires a lowercase log level string.  Normalize here so that
    # LOG_LEVEL=INFO (the conventional uppercase value used in .env files,
    # Helm values, and Dockerfile ENV declarations) does not produce a
    # KeyError inside uvicorn's LOG_LEVELS dict lookup.
    raw_log_level = os.environ.get("LOG_LEVEL", "info")
    log_level = raw_log_level.strip().lower() if raw_log_level.strip() else "info"

    _require_reachable_mongo(app_root, workspace=workspace, host=host)

    print(f"App root : {app_root}")
    print(f"Host     : {host}  ({listen}:{port})")
    if reload:
        print("Reload   : enabled")

    # The host runs with this process's environment.
    warn_if_unauthenticated_bind(listen, environ=os.environ, env_file=env_file)

    uvicorn.run(app_module, host=listen, port=port, reload=reload, log_level=log_level)


def _require_reachable_mongo(app_root: Path, *, workspace: Path, host: str) -> None:
    """Exit with an actionable message unless the app's MONGO_URI answers a ping.

    Resolves MONGO_URI exactly as the host will (the app's secret contract,
    then the environment), so an invalid ``app/security/secrets.yaml`` is
    reported here too instead of as a startup traceback.
    """
    from mozaiks_cli import mongo_preflight
    from mozaiksai.core.secrets import SecretContractError, SecretResolutionError, resolve_secret

    env_file = workspace / ".env"
    rerun = f'mozaiks serve "{workspace}" --host {host}'
    try:
        uri = resolve_secret("MONGO_URI", app_root=app_root)
    except SecretContractError as exc:
        print(f"Error: {app_root / 'security' / 'secrets.yaml'} is not a valid secret contract ({exc}).")
        print("A minimal valid contract is:")
        print("  version: 1")
        print("  kind: app_secret_contract")
        print("  provider:")
        print("    type: env")
        print("  secrets: []")
        sys.exit(1)
    except SecretResolutionError as exc:
        print(f"Error: MongoDB is required to serve this app. {exc}")
        print(f"Set MONGO_URI in your shell or in {env_file}, then rerun:")
        print(f"  {rerun}")
        sys.exit(1)

    failure = mongo_preflight.mongo_unreachable(
        uri, timeout_ms=mongo_preflight.preflight_timeout_ms(os.environ)
    )
    if failure is not None:
        print(f"Error: MongoDB is not reachable at MONGO_URI ({failure.shown_uri}).")
        print(
            "Start MongoDB, or set MONGO_URI in your shell or in "
            f"{env_file} to a reachable server, then rerun:"
        )
        print(f"  {rerun}")
        print(f"Underlying error: {failure.reason}")
        sys.exit(1)


def _resolve_app_root(workspace: Path) -> Path | None:
    if (workspace / "factory_app" / "app" / "app.json").exists():
        return workspace / "factory_app" / "app"
    if (workspace / "app" / "app.json").exists():
        return workspace / "app"
    if (workspace / "app.json").exists():
        return workspace
    return None
