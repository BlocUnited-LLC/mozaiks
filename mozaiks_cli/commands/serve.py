"""mozaiks serve - Start the Mozaiks runtime for an app workspace."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HOST_MODULES = {
    "runtime": "mozaiksai.hosts.runtime:app",
    "platform": "mozaiksai.hosts.platform:app",
    "studio": "mozaiksai.hosts.studio:app",
}


def run(args) -> None:
    workspace = Path(getattr(args, "workspace", ".")).resolve()
    host = getattr(args, "host", "platform")
    port = int(getattr(args, "port", 8000))
    listen = getattr(args, "listen", "0.0.0.0")
    reload = bool(getattr(args, "reload", False))

    app_root = _resolve_app_root(workspace)
    if app_root is None:
        print(f"Error: no app bundle found at {workspace}")
        print("Expected app/app.json or app.json in the workspace directory.")
        sys.exit(1)

    app_module = _HOST_MODULES.get(host)
    if not app_module:
        print(f"Error: unknown host '{host}'. Choices: {', '.join(_HOST_MODULES)}")
        sys.exit(1)

    os.environ["MOZAIKS_APP_WORKSPACE_PATH"] = str(app_root)
    os.environ["PLATFORM_PATH"] = str(app_root)
    os.environ.setdefault("MOZAIKS_HOST", host)

    # Load .env from the workspace directory so the app bundle owns its config,
    # regardless of which directory the CLI is invoked from.
    # If .env doesn't exist but .env.example does, create it automatically.
    try:
        import shutil

        from dotenv import load_dotenv
        env_file = workspace / ".env"
        env_example = workspace / ".env.example"
        if not env_file.exists() and env_example.exists():
            shutil.copy(env_example, env_file)
            print(f"Created {env_file} from .env.example — fill in your OPENAI_API_KEY and MONGO_URI before use.")
        if env_file.exists():
            load_dotenv(dotenv_path=env_file, override=False)
    except ImportError:
        pass

    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is required. It is included in the mozaiks package dependencies.")
        sys.exit(1)

    print(f"App root : {app_root}")
    print(f"Host     : {host}  ({listen}:{port})")
    if reload:
        print("Reload   : enabled")

    uvicorn.run(app_module, host=listen, port=port, reload=reload)


def _resolve_app_root(workspace: Path) -> Path | None:
    if (workspace / "factory_app" / "app" / "app.json").exists():
        return workspace / "factory_app" / "app"
    if (workspace / "app" / "app.json").exists():
        return workspace / "app"
    if (workspace / "app.json").exists():
        return workspace
    return None
