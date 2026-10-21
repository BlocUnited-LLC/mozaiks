from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import pytest

from mozaiks_cli import mongo_preflight, studio_launcher


def test_mongo_preflight_requires_mongo_uri(tmp_path) -> None:
    with pytest.raises(RuntimeError) as exc:
        studio_launcher._assert_mongo_ready({}, workspace_root=tmp_path)

    message = str(exc.value)
    assert "MongoDB is required to start Mozaiks Studio" in message
    assert "Set MONGO_URI" in message
    assert f'python -m mozaiks studio --dir "{tmp_path}" --open' in message


def test_mongo_preflight_redacts_credentials(monkeypatch, tmp_path) -> None:
    def fail_ping(uri: str, *, timeout_ms: int) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", fail_ping)

    with pytest.raises(RuntimeError) as exc:
        studio_launcher._assert_mongo_ready(
            {"MONGO_URI": "mongodb://user:password@localhost:27017/mozaiks"},
            workspace_root=tmp_path,
        )

    message = str(exc.value)
    assert "mongodb://***@localhost:27017/mozaiks" in message
    assert "user:password" not in message
    assert "network down" in message


def test_mongo_preflight_redacts_a_uri_echoed_in_an_error(monkeypatch) -> None:
    uri = "mongodb://user:password@localhost:27017/mozaiks"

    def fail_ping(_uri: str, *, timeout_ms: int) -> None:
        raise RuntimeError(f"connection to {uri} failed")

    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", fail_ping)

    failure = mongo_preflight.mongo_unreachable(uri, timeout_ms=1000)

    assert failure is not None
    assert failure.shown_uri == "mongodb://***@localhost:27017/mozaiks"
    assert failure.reason == "RuntimeError: connection to mongodb://***@localhost:27017/mozaiks failed"


def test_mongo_preflight_accepts_alias(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, int]] = []

    def record_ping(uri: str, *, timeout_ms: int) -> None:
        calls.append((uri, timeout_ms))

    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", record_ping)

    studio_launcher._assert_mongo_ready(
        {
            "MONGODB_URI": "mongodb://localhost:27017/mozaiks",
            "MOZAIKS_MONGO_PREFLIGHT_TIMEOUT_MS": "1500",
        },
        workspace_root=tmp_path,
    )

    assert calls == [("mongodb://localhost:27017/mozaiks", 1500)]


def test_mongo_preflight_fails_fast_against_a_closed_port() -> None:
    """A real ping to a port nothing listens on returns a reason, not a hang."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    started = time.monotonic()
    failure = mongo_preflight.mongo_unreachable(f"mongodb://127.0.0.1:{port}/mozaiks", timeout_ms=1000)
    assert failure is not None
    assert failure.shown_uri == f"mongodb://127.0.0.1:{port}/mozaiks"
    assert time.monotonic() - started < 15


def test_studio_env_uses_factory_workflows_for_studio_host(monkeypatch, tmp_path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_text('{"appName": "Studio App"}\n', encoding="utf-8")
    (tmp_path / "workflows").mkdir()
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    env = studio_launcher._workspace_env(tmp_path, host="studio")

    assert env["MOZAIKS_APP_WORKSPACE_PATH"] == str(app_root.resolve())
    assert env["PLATFORM_PATH"] == str(app_root.resolve())
    assert env["MOZAIKS_WORKFLOWS_PATH"].endswith("factory_app\\workflows") or env[
        "MOZAIKS_WORKFLOWS_PATH"
    ].endswith("factory_app/workflows")


def test_spawned_server_output_lands_in_the_workspace_log(tmp_path) -> None:
    log_path = studio_launcher._process_log_path(tmp_path, "backend")
    process = studio_launcher._spawn_process(
        [sys.executable, "-c", "import sys; print('booting'); sys.exit('boom: bad config')"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        log_path=log_path,
    )
    assert process.wait(timeout=30) == 1

    assert log_path == tmp_path / "logs" / "studio-backend.log"
    error = studio_launcher._start_failure("Backend failed to start (exit code 1).", log_path)
    message = str(error)
    assert "Backend failed to start (exit code 1)." in message
    assert f"Full log: {log_path}" in message
    assert "booting" in message
    assert "boom: bad config" in message


class _ExitedProcess:
    pid = 4242

    def poll(self) -> int:
        return 3


def _workspace(tmp_path: Path) -> Path:
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_text('{"appName": "Studio App", "appId": "studio-app"}\n', encoding="utf-8")
    return tmp_path


def test_backend_crash_reports_its_log_tail(monkeypatch, tmp_path) -> None:
    workspace = _workspace(tmp_path)
    spawned: list[dict] = []

    def fake_spawn(command, *, cwd, env, log_path):
        log_path.write_text("Traceback ...\nSecretContractError: Invalid names-only secret contract\n", encoding="utf-8")
        spawned.append({"command": command, "env": env, "log_path": log_path})
        return _ExitedProcess()

    monkeypatch.setattr(studio_launcher, "_http_ready", lambda url: False)
    monkeypatch.setattr(studio_launcher, "_wait_for_url", lambda url, *, timeout_seconds: False)
    monkeypatch.setattr(studio_launcher, "_assert_mongo_ready", lambda env, *, workspace_root: None)
    monkeypatch.setattr(studio_launcher, "_spawn_process", fake_spawn)

    with pytest.raises(RuntimeError) as exc:
        studio_launcher.launch_studio(workspace_root=workspace, open_browser=False)

    message = str(exc.value)
    assert "Backend failed to start (exit code 3)." in message
    assert "SecretContractError: Invalid names-only secret contract" in message
    assert str(workspace / "logs" / "studio-backend.log") in message
    assert "backend terminal" not in message
    command = spawned[0]["command"]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert spawned[0]["env"]["PYTHONUNBUFFERED"] == "1"


def test_launch_binds_loopback_and_points_the_frontend_at_the_chosen_backend_port(
    monkeypatch, tmp_path
) -> None:
    workspace = _workspace(tmp_path)
    web_shell = tmp_path / "web_shell"
    (web_shell / "node_modules").mkdir(parents=True)
    (web_shell / "package.json").write_text("{}", encoding="utf-8")
    spawned: list[dict] = []
    probed: list[str] = []

    class _RunningProcess:
        pid = 1111

        def poll(self):
            return None

    def fake_spawn(command, *, cwd, env, log_path):
        spawned.append({"command": command, "env": env, "log_path": log_path})
        return _RunningProcess()

    def fake_wait(url, *, timeout_seconds):
        probed.append(url)
        return True

    monkeypatch.setattr(studio_launcher, "resolve_web_shell_root", lambda: web_shell)
    monkeypatch.setattr(studio_launcher.shutil, "which", lambda name: "npm")
    monkeypatch.setattr(studio_launcher, "_http_ready", lambda url: False)
    monkeypatch.setattr(studio_launcher, "_wait_for_url", fake_wait)
    monkeypatch.setattr(studio_launcher, "_assert_mongo_ready", lambda env, *, workspace_root: None)
    monkeypatch.setattr(studio_launcher, "_spawn_process", fake_spawn)

    result = studio_launcher.launch_studio(
        workspace_root=workspace, backend_port=8123, frontend_port=3123, open_browser=False
    )

    backend, frontend = spawned
    assert backend["command"][backend["command"].index("--host") + 1] == "127.0.0.1"
    assert frontend["command"][frontend["command"].index("--host") + 1] == "127.0.0.1"
    assert frontend["env"]["MOZAIKS_BACKEND_URL"] == "http://127.0.0.1:8123"
    assert probed == ["http://127.0.0.1:8123/api/health", "http://127.0.0.1:3123/"]
    assert result["backend_log"] == str(workspace / "logs" / "studio-backend.log")
    assert result["frontend_log"] == str(workspace / "logs" / "studio-frontend.log")


def test_launch_listens_on_all_interfaces_only_when_asked(monkeypatch, tmp_path) -> None:
    workspace = _workspace(tmp_path)
    spawned: list[list[str]] = []

    class _RunningProcess:
        pid = 2222

        def poll(self):
            return None

    def fake_spawn(command, *, cwd, env, log_path):
        spawned.append(command)
        return _RunningProcess()

    monkeypatch.setattr(studio_launcher, "resolve_web_shell_root", lambda: None)
    monkeypatch.setattr(studio_launcher, "_http_ready", lambda url: False)
    monkeypatch.setattr(studio_launcher, "_wait_for_url", lambda url, *, timeout_seconds: True)
    monkeypatch.setattr(studio_launcher, "_assert_mongo_ready", lambda env, *, workspace_root: None)
    monkeypatch.setattr(studio_launcher, "_spawn_process", fake_spawn)

    result = studio_launcher.launch_studio(workspace_root=workspace, bind_host="0.0.0.0", open_browser=False)

    assert spawned[0][spawned[0].index("--host") + 1] == "0.0.0.0"
    assert result["backend_url"] == "http://127.0.0.1:8000"


def test_launch_reports_urls_for_the_selected_bind_host(monkeypatch, tmp_path) -> None:
    workspace = _workspace(tmp_path)
    web_shell = tmp_path / "web_shell"
    web_shell.mkdir()
    (web_shell / "package.json").write_text("{}", encoding="utf-8")

    class _RunningProcess:
        pid = 2222

        def poll(self):
            return None

    monkeypatch.setattr(studio_launcher, "resolve_web_shell_root", lambda: web_shell)
    monkeypatch.setattr(studio_launcher, "_http_ready", lambda url: False)
    monkeypatch.setattr(studio_launcher, "_wait_for_url", lambda url, *, timeout_seconds: True)
    monkeypatch.setattr(studio_launcher, "_assert_mongo_ready", lambda env, *, workspace_root: None)
    monkeypatch.setattr(studio_launcher, "_spawn_process", lambda *args, **kwargs: _RunningProcess())
    monkeypatch.setattr(studio_launcher.shutil, "which", lambda name: "npm")
    (web_shell / "node_modules").mkdir()

    result = studio_launcher.launch_studio(
        workspace_root=workspace, bind_host="::1", backend_port=8123,
        frontend_port=3123, open_browser=False,
    )

    assert result["backend_url"] == "http://[::1]:8123"
    assert result["frontend_url"] == "http://[::1]:3123"
    assert result["studio_url"] == "http://[::1]:3123/apps"
