from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from mozaiks_cli.unauthenticated_bind import unauthenticated_bind_warning
from mozaiks_cli.workspace import load_workspace_dotenv, resolve_active_app_root
from mozaiksai.resources import (
    resolve_chat_ui_root,
    resolve_factory_app_root,
    resolve_factory_workflows_root,
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

    factory_workflows_root = resolve_factory_workflows_root()
    if host == "studio" and factory_workflows_root is not None:
        env.setdefault("MOZAIKS_WORKFLOWS_PATH", str(factory_workflows_root))

    web_shell_root = resolve_web_shell_root()
    if web_shell_root is not None:
        env.setdefault("MOZAIKS_WEB_SHELL_PATH", str(web_shell_root))

    chat_ui_root = resolve_chat_ui_root()
    if chat_ui_root is not None:
        env.setdefault("MOZAIKS_CHAT_UI_PATH", str(chat_ui_root))

    env_file = workspace_root / ".env"
    env_example = workspace_root / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy(env_example, env_file)
    if load_workspace_dotenv(workspace_root) is not None:
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


def _assert_mongo_ready(env: dict[str, str], *, workspace_root: Path) -> None:
    from mozaiks_cli import mongo_preflight

    uri = _mongo_uri_from_env(env)
    rerun_command = f'python -m mozaiks studio --dir "{workspace_root}" --open'
    env_path = workspace_root / ".env"

    if not uri:
        raise RuntimeError(
            "MongoDB is required to start Mozaiks Studio.\n"
            f"Set MONGO_URI in your shell or in {env_path}, then rerun:\n"
            f"  {rerun_command}\n"
            "For a local MongoDB server, use: mongodb://localhost:27017/mozaiks"
        )

    failure = mongo_preflight.mongo_unreachable(
        uri, timeout_ms=mongo_preflight.preflight_timeout_ms(env)
    )
    if failure is not None:
        raise RuntimeError(
            "MongoDB is required to start Mozaiks Studio.\n"
            f"Could not connect to MONGO_URI ({failure.shown_uri}).\n"
            "Start MongoDB locally, or set MONGO_URI to a reachable MongoDB Atlas/local URI "
            f"in {env_path}, then rerun:\n"
            f"  {rerun_command}\n"
            f"Underlying error: {failure.reason}"
        )


_LOG_TAIL_LINES = 40


def _process_log_path(workspace_root: Path, name: str) -> Path:
    """Return the workspace file that receives one launched server's output."""
    log_dir = workspace_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"studio-{name}.log"


def _log_tail(path: Path, *, lines: int = _LOG_TAIL_LINES) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _start_failure(detail: str, log_path: Path) -> RuntimeError:
    """Build the error for a server that did not start, quoting its own output."""
    message = f"{detail}\nFull log: {log_path}"
    tail = _log_tail(log_path)
    if tail:
        message += "\nLast lines:\n" + "\n".join(f"  {line}" for line in tail.splitlines())
    return RuntimeError(message)


def _spawn_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> subprocess.Popen[Any]:
    """Start a server that outlives this command, writing its output to ``log_path``.

    The CLI returns once the servers are up, so a separate console window or a
    pipe loses the output exactly when it matters: when a server dies during
    startup. The log file keeps it, and a failed start quotes its tail.
    """
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    with log_path.open("wb") as log_file:
        # The child keeps its own handle to the file after this one closes.
        return subprocess.Popen(command, stdout=log_file, **kwargs)


def _loopback_host(bind_host: str) -> str:
    """Return the host this machine uses to reach a server bound to ``bind_host``."""
    if bind_host in {"", "0.0.0.0", "localhost"}:
        return "127.0.0.1"
    if bind_host in {"::", "::1"}:
        return "[::1]"
    return bind_host


def _resolve_backend_app_module(preferred_host: str) -> str:
    if preferred_host in {"studio", "platform", "runtime", "mozaiks"}:
        return f"mozaiksai.hosts.{preferred_host}:app"
    if resolve_factory_app_root() is not None:
        return "mozaiksai.hosts.studio:app"
    return "mozaiksai.hosts.studio:app"


def launch_studio(
    *,
    workspace_root: Path,
    backend_port: int = 8000,
    frontend_port: int = 3000,
    bind_host: str = "127.0.0.1",
    open_browser: bool = True,
    preferred_host: str = "auto",
) -> dict[str, Any]:
    """Start (or reuse) the Studio backend and frontend for ``workspace_root``.

    Both servers listen on ``bind_host``: loopback by default, because local
    development runs with authentication off and an anonymous admin user. On
    any other address, a warning says so before the first server starts.
    """
    web_shell_root = resolve_web_shell_root()
    host_name = "studio" if preferred_host == "auto" else preferred_host
    app_module = _resolve_backend_app_module(host_name)
    env = _workspace_env(workspace_root, host=host_name)
    # The backend runs in a child process that receives exactly ``env``.
    exposure_warning = unauthenticated_bind_warning(
        bind_host, environ=env, env_file=workspace_root / ".env"
    )

    def warn_about_exposure_once() -> None:
        nonlocal exposure_warning
        if exposure_warning is not None:
            print(exposure_warning, file=sys.stderr, flush=True)
            exposure_warning = None

    local_host = _loopback_host(bind_host)
    backend_origin = f"http://{local_host}:{backend_port}"
    backend_url = f"{backend_origin}/api/health"
    frontend_url = f"http://{local_host}:{frontend_port}/"
    studio_url = f"{frontend_url}apps"

    backend_process = None
    backend_log: Path | None = None
    if not _http_ready(backend_url):
        _assert_mongo_ready(env, workspace_root=workspace_root)
        warn_about_exposure_once()
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
        backend_log = _process_log_path(workspace_root, "backend")
        backend_process = _spawn_process(
            backend_command,
            cwd=workspace_root,
            env={**env, "PYTHONUNBUFFERED": "1"},
            log_path=backend_log,
        )
        if not _wait_for_url(backend_url, timeout_seconds=40):
            exit_code = backend_process.poll()
            if exit_code is not None:
                raise _start_failure(f"Backend failed to start (exit code {exit_code}).", backend_log)
            raise _start_failure(
                f"Backend did not become healthy at {backend_url} within 40 seconds.", backend_log
            )
        print(f"Backend running (pid {backend_process.pid}); log: {backend_log}")

    frontend_process = None
    frontend_log: Path | None = None
    frontend_available = web_shell_root is not None and (web_shell_root / "package.json").exists()
    if frontend_available and not _http_ready(frontend_url):
        npm_cmd = shutil.which("npm")
        if not npm_cmd:
            raise RuntimeError("npm is required to launch the Studio frontend.")

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
        # The dev server proxies /api to the backend, so it exposes the same
        # anonymous user even when the backend was already running.
        warn_about_exposure_once()
        frontend_log = _process_log_path(workspace_root, "frontend")
        frontend_process = _spawn_process(
            frontend_command,
            cwd=web_shell_root,
            # The dev server proxies /api and /ws to MOZAIKS_BACKEND_URL; point
            # it at the backend this launch uses, not the 8000 default.
            env={**env, "MOZAIKS_BACKEND_URL": backend_origin},
            log_path=frontend_log,
        )
        if not _wait_for_url(frontend_url, timeout_seconds=50):
            exit_code = frontend_process.poll()
            if exit_code is not None:
                raise _start_failure(f"Frontend failed to start (exit code {exit_code}).", frontend_log)
            raise _start_failure(
                f"Frontend did not become ready at {frontend_url} within 50 seconds.", frontend_log
            )
        print(f"Frontend running (pid {frontend_process.pid}); log: {frontend_log}")

    if open_browser and frontend_available:
        webbrowser.open(studio_url)

    return {
        "backend_url": backend_origin,
        "frontend_url": frontend_url.rstrip("/") if frontend_available else None,
        "studio_url": studio_url if frontend_available else None,
        "backend_started": backend_process is not None,
        "frontend_started": frontend_process is not None,
        "backend_pid": backend_process.pid if backend_process is not None else None,
        "frontend_pid": frontend_process.pid if frontend_process is not None else None,
        "backend_log": str(backend_log) if backend_log is not None else None,
        "frontend_log": str(frontend_log) if frontend_log is not None else None,
        "frontend_available": frontend_available,
    }
