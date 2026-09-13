"""Supervise a canonical app inside an already isolated preview sandbox.

This is not an app host or agent executor. The existing platform host and web
shell run unchanged; Mongo is private to this disposable provider session.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mozaiksai.core.runtime.app.auth_contract import load_app_auth_contract
from mozaiksai.resources import (
    resolve_chat_ui_root,
    resolve_factory_app_root,
    resolve_web_shell_root,
)

_STATE_ROOT = Path("/tmp/mozaiks-preview")
_PID_PATH = _STATE_ROOT / "supervisor.pid"
_LOG_PATH = _STATE_ROOT / "runtime.log"


def preview_environment(app_root: Path, *, preview_url: str) -> dict[str, str]:
    manifest = json.loads((app_root / "app.json").read_text(encoding="utf-8"))
    app_id = manifest.get("appId")
    if not isinstance(app_id, str) or not app_id:
        raise ValueError("Preview requires app.json with appId")
    auth_contract = load_app_auth_contract(app_root, auth_required=manifest.get("authRequired", True))
    web_shell = resolve_web_shell_root()
    chat_ui = resolve_chat_ui_root()
    factory = resolve_factory_app_root()
    if web_shell is None or chat_ui is None or factory is None:
        raise ValueError("Preview image is missing packaged Mozaiks frontend resources")
    if not (web_shell / "node_modules/vite/bin/vite.js").is_file():
        raise ValueError("Preview image is missing installed web shell dependencies")

    env = os.environ.copy()
    env.update({
        "MOZAIKS_HOST": "platform",
        "INTERNAL_API_KEY": secrets.token_urlsafe(32),
        "PYTHON_DOTENV_DISABLED": "1",
        "VITE_MOZAIKS_HOST": "platform",
        "PLATFORM_PATH": str(app_root),
        "MOZAIKS_APP_WORKSPACE_PATH": str(app_root.parent),
        "MOZAIKS_WEB_SHELL_PATH": str(web_shell),
        "MOZAIKS_CHAT_UI_PATH": str(chat_ui),
        "MOZAIKS_FACTORY_APP_PATH": str(factory),
        "MOZAIKS_WORKFLOWS_PATH": str(app_root.parent / "workflows"),
        "MONGO_URI": "mongodb://127.0.0.1:27017/mozaiks_preview",
        # Module persistence and account routes must reach the same app records.
        "MOZAIKS_APP_DATABASE_NAME": "mozaiks_preview",
        "MOZAIKS_APP_DATA_DATABASE_NAME": "mozaiks_preview",
        "VITE_API_URL": "",
        "VITE_CORE_URL": "",
        "VITE_WS_URL": "",
        "MOZAIKS_BACKEND_URL": "http://127.0.0.1:8000",
        "CORS_ORIGINS": preview_url,
        "VITE_MOCK_MODE": "false",
        "VITE_USAGE_DEMO_MODE": "false",
        "AUTH_AUDIENCE": app_id,
        "VITE_OIDC_CLIENT_ID": app_id,
        "VITE_OIDC_REDIRECT_URI": preview_url.rstrip("/") + auth_contract.routes.callback if auth_contract else "",
        "LOGS_BASE_DIR": str(_STATE_ROOT / "logs"),
    })
    if auth_contract is None:
        env["AUTH_ENABLED"] = "false"
        env["AUTH_PROVIDER"] = "none"
    else:
        if env.get("AUTH_ENABLED", "true").lower() not in {"true", "1", "yes", "on"}:
            raise ValueError("Authenticated app previews cannot disable authentication")
        env["AUTH_ENABLED"] = "true"
        if not env.get("VITE_OIDC_AUTHORITY"):
            raise ValueError("Configure preview VITE_OIDC_AUTHORITY for this authenticated app")
        if not (env.get("MOZAIKS_OIDC_AUTHORITY") or (env.get("AUTH_ISSUER") and env.get("AUTH_JWKS_URL"))):
            raise ValueError("Configure preview runtime OIDC authority or issuer/JWKS settings")
    return env


def check_ready(*, port: int = 3000, app_root: Path = Path("/workspace/app")) -> bool:
    for url in ("http://127.0.0.1:8000/api/health", f"http://127.0.0.1:{port}/"):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status != 200:
                    return False
        except (OSError, urllib.error.URLError):
            return False
    try:
        expected_app_id = json.loads((app_root / "app.json").read_text(encoding="utf-8"))["appId"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/shell-config", timeout=2) as response:
            shell_config = json.load(response)
            return response.status == 200 and isinstance(shell_config, dict) and shell_config.get("appId") == expected_app_id
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        return False


def stop_runtime() -> None:
    if not _PID_PATH.exists():
        return
    pid = int(_PID_PATH.read_text(encoding="ascii"))
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _PID_PATH.unlink(missing_ok=True)
        return
    deadline = time.monotonic() + 15
    while _PID_PATH.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if _PID_PATH.exists():
        raise RuntimeError("Previous preview runtime did not stop")


def run_runtime(*, app_root: Path, preview_url: str) -> int:
    if os.name != "posix":
        raise RuntimeError("Preview supervision runs inside a Linux sandbox")
    env = preview_environment(app_root, preview_url=preview_url)
    _STATE_ROOT.mkdir(parents=True, exist_ok=True)
    if _PID_PATH.exists():
        raise RuntimeError("Stop the previous preview runtime before starting another")
    _PID_PATH.write_text(str(os.getpid()), encoding="ascii")
    database = _STATE_ROOT / "mongo"
    database.mkdir(exist_ok=True)
    (app_root.parent / "workflows").mkdir(exist_ok=True)
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    children: list[subprocess.Popen] = []
    try:
        with _LOG_PATH.open("w", encoding="utf-8") as log:
            children.append(subprocess.Popen([
                "mongod", "--bind_ip", "127.0.0.1", "--port", "27017",
                "--dbpath", str(database), "--wiredTigerCacheSizeGB", "0.25",
            ], env=env, stdout=log, stderr=log))
            from pymongo import MongoClient
            from pymongo.errors import PyMongoError

            deadline = time.monotonic() + 30
            client: MongoClient[dict[str, Any]]
            with MongoClient(env["MONGO_URI"], serverSelectionTimeoutMS=1000) as client:
                while not stopping:
                    try:
                        client.admin.command("ping")
                        break
                    except PyMongoError:
                        if children[0].poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("Disposable preview database did not become ready") from None
                        time.sleep(0.2)
            if stopping:
                return 0
            children.append(subprocess.Popen([
                sys.executable, "-m", "uvicorn", "mozaiksai.hosts.platform:app",
                "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning",
            ], cwd=app_root.parent, env=env, stdout=log, stderr=log))
            children.append(subprocess.Popen([
                "node", "node_modules/vite/bin/vite.js", "--host", "0.0.0.0",
                "--port", "3000", "--strictPort",
            ], cwd=env["MOZAIKS_WEB_SHELL_PATH"], env=env, stdout=log, stderr=log))
            while not stopping:
                if any(child.poll() is not None for child in children):
                    return 1
                time.sleep(0.25)
            return 0
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        _PID_PATH.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "check"))
    parser.add_argument("--app-root", type=Path, default=Path("/workspace/app"))
    parser.add_argument("--preview-url", default="http://localhost:3000")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    if args.action == "check":
        return 0 if check_ready(port=args.port, app_root=args.app_root) else 1
    if args.action == "stop":
        stop_runtime()
        return 0
    return run_runtime(app_root=args.app_root.resolve(), preview_url=args.preview_url.rstrip("/"))


if __name__ == "__main__":
    raise SystemExit(main())
