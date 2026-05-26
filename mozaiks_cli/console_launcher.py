from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from mozaiks_cli.workspace import resolve_active_app_root
from mozaiksai.resources import (
    resolve_chat_ui_root,
    resolve_factory_app_root,
    resolve_web_shell_root,
)


def _workspace_env(workspace_root: Path, *, host: str) -> dict[str, str]:
    env = os.environ.copy()
    active_app_root = resolve_active_app_root(workspace_root)
    env["MOZAIKS_APP_WORKSPACE_PATH"] = str(active_app_root)
    env["PLATFORM_PATH"] = str(active_app_root)
    env["MOZAIKS_HOST"] = host
    env.setdefault("MOZAIKS_GENERATED_ARTIFACTS_PATH", str((workspace_root / "generated").resolve()))

    factory_app_root = resolve_factory_app_root()
    if factory_app_root is not None:
        env.setdefault("MOZAIKS_FACTORY_APP_PATH", str(factory_app_root))

    web_shell_root = resolve_web_shell_root()
    if web_shell_root is not None:
        env.setdefault("MOZAIKS_WEB_SHELL_PATH", str(web_shell_root))

    chat_ui_root = resolve_chat_ui_root()
    if chat_ui_root is not None:
        env.setdefault("MOZAIKS_CHAT_UI_PATH", str(chat_ui_root))

    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("python-dotenv is required to launch the Console from the CLI.") from exc

    env_file = workspace_root / ".env"
    env_example = workspace_root / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy(env_example, env_file)
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=False)
        for line in env_file.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip("\"'"))

    return env


def _http_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=1.0) as response:  # nosec B310 - local dev health check
            return 200 <= getattr(response, "status", 200) < 500
    except URLError:
        return False
    except Exception:
        return False


def _wait_for_url(url: str, *, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _http_ready(url):
            return True
        time.sleep(0.5)
    return False


def _mongo_uri_from_env(env: dict[str, str]) -> str:
    for key in ("MONGO_URI", "MONGODB_URI", "MONGO_URL"):
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return ""


def _redact_mongo_uri(uri: str) -> str:
    return re.sub(r"^(mongodb(?:\+srv)?://)([^/@]+@)", r"\1***@", uri)


def _short_error_message(exc: Exception) -> str:
    message = str(exc).replace("\n", " ")
    message = re.sub(r"\s*\(configured timeouts:[^)]+\)", "", message)
    for marker in (", Timeout:", " Timeout:", ", Topology Description:", " Topology Description:"):
        index = message.find(marker)
        if index != -1:
            message = message[:index]
            break
    return f"{type(exc).__name__}: {message.strip()}"


def _mongo_timeout_ms(env: dict[str, str]) -> int:
    raw_value = str(env.get("MOZAIKS_MONGO_PREFLIGHT_TIMEOUT_MS") or "").strip()
    if not raw_value:
        return 5000
    try:
        return max(1000, int(raw_value))
    except ValueError:
        return 5000


def _ping_mongo_uri(uri: str, *, timeout_ms: int) -> None:
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
    try:
        client.admin.command("ping")
    finally:
        client.close()


def _assert_mongo_ready(env: dict[str, str], *, workspace_root: Path) -> None:
    uri = _mongo_uri_from_env(env)
    rerun_command = f'mozaiks console --dir "{workspace_root}" --open'
    env_path = workspace_root / ".env"

    if not uri:
        raise RuntimeError(
            "MongoDB is required to start the Mozaiks Console.\n"
            f"Set MONGO_URI in your shell or in {env_path}, then rerun:\n"
            f"  {rerun_command}\n"
            "For a local MongoDB server, use: mongodb://localhost:27017/mozaiks"
        )

    try:
        _ping_mongo_uri(uri, timeout_ms=_mongo_timeout_ms(env))
    except Exception as exc:
        safe_uri = _redact_mongo_uri(uri)
        raise RuntimeError(
            "MongoDB is required to start the Mozaiks Console.\n"
            f"Could not connect to MONGO_URI ({safe_uri}).\n"
            "Start MongoDB locally, or set MONGO_URI to a reachable MongoDB Atlas/local URI "
            f"in {env_path}, then rerun:\n"
            f"  {rerun_command}\n"
            f"Underlying error: {_short_error_message(exc)}"
        ) from exc


def _spawn_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.Popen[Any]:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
    }
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return subprocess.Popen(command, **kwargs)


def _resolve_backend_app_module(preferred_host: str) -> str:
    if preferred_host in {"studio", "platform", "runtime", "mozaiks"}:
        return f"mozaiksai.hosts.{preferred_host}:app"
    if resolve_factory_app_root() is not None:
        return "mozaiksai.hosts.studio:app"
    return "mozaiksai.hosts.studio:app"


def launch_console(
    *,
    workspace_root: Path,
    backend_port: int = 8000,
    frontend_port: int = 3000,
    bind_host: str = "0.0.0.0",
    open_browser: bool = True,
    preferred_host: str = "auto",
) -> dict[str, Any]:
    web_shell_root = resolve_web_shell_root()
    host_name = "studio" if preferred_host == "auto" else preferred_host
    app_module = _resolve_backend_app_module(host_name)
    env = _workspace_env(workspace_root, host=host_name)

    backend_url = f"http://localhost:{backend_port}/api/health"
    frontend_url = f"http://localhost:{frontend_port}/"
    console_url = f"http://localhost:{frontend_port}/apps"

    backend_process = None
    if not _http_ready(backend_url):
        _assert_mongo_ready(env, workspace_root=workspace_root)
        backend_command = [
            sys.executable,
            "-m",
            "uvicorn",
            app_module,
            "--host",
            bind_host,
            "--port",
            str(backend_port),
        ]
        backend_process = _spawn_process(backend_command, cwd=workspace_root, env=env)
        if not _wait_for_url(backend_url, timeout_seconds=40):
            if backend_process.poll() is not None:
                raise RuntimeError("Backend failed to start. Check the backend terminal for details.")
            raise RuntimeError("Backend did not become healthy in time.")

    frontend_process = None
    frontend_available = web_shell_root is not None and (web_shell_root / "package.json").exists()
    if frontend_available and not _http_ready(frontend_url):
        npm_cmd = shutil.which("npm")
        if not npm_cmd:
            raise RuntimeError("npm is required to launch the Console frontend.")

        assert web_shell_root is not None
        node_modules_dir = web_shell_root / "node_modules"
        if not node_modules_dir.exists():
            subprocess.run(
                [npm_cmd, "--prefix", str(web_shell_root), "install"],
                cwd=str(web_shell_root),
                env=env,
                check=True,
            )

        frontend_command = [
            npm_cmd,
            "--prefix",
            str(web_shell_root),
            "run",
            "dev",
            "--",
            "--host",
            bind_host,
            "--port",
            str(frontend_port),
            "--strictPort",
        ]
        frontend_process = _spawn_process(frontend_command, cwd=web_shell_root, env=env)
        if not _wait_for_url(frontend_url, timeout_seconds=50):
            if frontend_process.poll() is not None:
                raise RuntimeError("Frontend failed to start. Check the frontend terminal for details.")
            raise RuntimeError("Frontend did not become ready in time.")

    if open_browser and frontend_available:
        webbrowser.open(console_url)

    return {
        "backend_url": f"http://localhost:{backend_port}",
        "frontend_url": f"http://localhost:{frontend_port}" if frontend_available else None,
        "console_url": console_url if frontend_available else None,
        "backend_started": backend_process is not None,
        "frontend_started": frontend_process is not None,
        "backend_pid": backend_process.pid if backend_process is not None else None,
        "frontend_pid": frontend_process.pid if frontend_process is not None else None,
        "frontend_available": frontend_available,
    }
