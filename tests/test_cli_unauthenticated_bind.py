"""A host started without authentication beyond this machine warns first.

A fresh scaffold runs with authentication off and an anonymous user that has
the ``admin`` role. On a loopback address only this machine reaches it; with
``--listen 0.0.0.0`` everyone who can reach the server is that user. These
tests run the real command functions. Only ``uvicorn.run`` (serve) and the
process spawn (Studio launcher) are replaced, by recorders that also note what
had been printed when the host was about to start; nothing binds a socket.
"""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from dotenv import dotenv_values

from mozaiks_cli import mongo_preflight, studio_launcher
from mozaiks_cli.commands import init_command
from mozaiks_cli.commands.serve import run as serve
from mozaiks_cli.unauthenticated_bind import is_loopback
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.base import AuthError
from mozaiksai.core.auth.config import clear_auth_config_cache
from tests.test_cli_serve_first_run import _VALID_CONTRACT, _workspace

_WARNING = "WARNING: authentication is off and the server is about to listen on"
_KEYCLOAK = {
    "KEYCLOAK_URL": "https://idp.example.invalid",
    "KEYCLOAK_REALM": "apps",
    "KEYCLOAK_CLIENT_ID": "app-api",
}
_LOCAL_NO_AUTH = {"ENV": "development", "AUTH_ENABLED": "false", "AUTH_ANON_ROLES": "admin,user"}


@pytest.fixture(autouse=True)
def _auth_caches():
    yield
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()


def _auth_env(monkeypatch, **values: str) -> None:
    """Make ``values`` the whole auth configuration of this process."""
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _serve(monkeypatch, capsys, workspace: Path, *, listen: str) -> dict:
    """Run ``mozaiks serve`` up to the point where the host would start."""
    # serve() writes these into os.environ; record restore entries first.
    for name in ("PLATFORM_PATH", "MOZAIKS_APP_WORKSPACE_PATH", "MOZAIKS_HOST"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.delenv("MOZAIKS_SECRETS_CONFIG_PATH", raising=False)
    monkeypatch.setenv("MONGO_URI", "mongodb://127.0.0.1:27017/serve")
    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", lambda uri, *, timeout_ms: None)

    started: dict = {}
    fake_uvicorn = ModuleType("uvicorn")

    def record_run(app_module, **kwargs):
        started.update(kwargs, stderr_before_start=capsys.readouterr().err)

    fake_uvicorn.run = record_run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    with patch("mozaiks_cli.commands.sync_agent_guidance.auto_sync_agent_guidance"):
        serve(Namespace(workspace=str(workspace), host="platform", port=8123, listen=listen, reload=False))
    return started


@pytest.mark.parametrize(
    ("bind_host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.20", False),
        ("build-box.example.invalid", False),
        ("", False),
    ],
)
def test_only_addresses_this_machine_alone_can_reach_are_loopback(bind_host, expected) -> None:
    assert is_loopback(bind_host) is expected


# --- mozaiks serve -------------------------------------------------------------


@pytest.mark.parametrize("listen", ["127.0.0.1", "localhost", "::1"])
def test_serve_on_loopback_prints_no_warning(monkeypatch, tmp_path, capsys, listen) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH)

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen=listen)

    assert started["host"] == listen
    assert "authentication is off" not in started["stderr_before_start"] + capsys.readouterr().err


def test_serve_beyond_loopback_with_auth_off_warns_before_starting(monkeypatch, tmp_path, capsys) -> None:
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH)

    started = _serve(monkeypatch, capsys, workspace, listen="0.0.0.0")

    warning = started["stderr_before_start"]
    assert f"{_WARNING} 0.0.0.0, which other machines can reach." in warning
    assert "treated as the anonymous user with the roles in AUTH_ANON_ROLES: admin, user." in warning
    assert "To turn authentication on, set AUTH_ENABLED=true and configure an identity provider" in warning
    assert str(workspace / ".env") in warning
    assert "--listen 127.0.0.1" in warning
    assert "ignored" not in warning
    assert warning.count("WARNING:") == 1
    # A warning only: the host still starts, on the address that was asked for.
    assert started["host"] == "0.0.0.0"


def test_serve_warning_says_the_anonymous_user_has_no_roles_when_none_are_set(
    monkeypatch, tmp_path, capsys
) -> None:
    _auth_env(monkeypatch, ENV="development", AUTH_ENABLED="false")

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")

    assert "the anonymous user with no roles (AUTH_ANON_ROLES is empty)." in started["stderr_before_start"]


def test_serve_beyond_loopback_with_auth_on_prints_no_warning(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, ENV="development", AUTH_ENABLED="true", **_KEYCLOAK)

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")

    assert started["host"] == "0.0.0.0"
    assert "authentication is off" not in started["stderr_before_start"] + capsys.readouterr().err


def test_serve_warning_says_auth_enabled_false_overrides_a_supplied_provider(
    monkeypatch, tmp_path, capsys
) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH, **_KEYCLOAK, AUTH_ISSUER="https://idp.example.invalid/realms/apps")

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")

    warning = started["stderr_before_start"]
    assert f"{_WARNING} 0.0.0.0" in warning
    assert (
        "AUTH_ENABLED=false overrides the provider settings present in the environment "
        "(KEYCLOAK_URL, KEYCLOAK_REALM, AUTH_ISSUER): they are ignored."
    ) in warning
    assert started["host"] == "0.0.0.0"


def test_serve_leaves_the_production_refusal_to_the_host(monkeypatch, tmp_path, capsys) -> None:
    """ENV=production without authentication: the host refuses, as before."""
    _auth_env(monkeypatch, ENV="production", AUTH_ENABLED="false", AUTH_ANON_ROLES="admin,user")

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")

    # The command adds no warning and no refusal of its own ...
    assert started["host"] == "0.0.0.0"
    assert "authentication is off" not in started["stderr_before_start"] + capsys.readouterr().err
    # ... because the host's startup validation still refuses this environment.
    with pytest.raises(AuthError, match="not permitted in the 'production' environment"):
        auth_registry.validate_auth_provider_configuration()


def test_fresh_scaffold_served_beyond_loopback_warns_about_the_anonymous_admin(
    monkeypatch, tmp_path, capsys
) -> None:
    """The reported case: a provider in the container environment, and the
    ``.env`` that serve creates from the scaffold switching authentication off."""
    workspace = tmp_path / "fresh"
    init_command.create_scaffold(target_dir=workspace, preset="chat", app_name="Fresh")
    _auth_env(monkeypatch, **_KEYCLOAK)
    # serve() loads the workspace .env into os.environ; record restore entries.
    for name in dotenv_values(workspace / ".env.example"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)

    started = _serve(monkeypatch, capsys, workspace, listen="0.0.0.0")

    assert (workspace / ".env").is_file()
    warning = started["stderr_before_start"]
    assert f"{_WARNING} 0.0.0.0" in warning
    assert "the roles in AUTH_ANON_ROLES: admin, user." in warning
    assert (
        "AUTH_ENABLED=false overrides the provider settings present in the environment "
        "(KEYCLOAK_URL, KEYCLOAK_REALM): they are ignored."
    ) in warning
    assert started["host"] == "0.0.0.0"


# --- Studio launcher -----------------------------------------------------------


class _RunningProcess:
    pid = 4321

    def poll(self):
        return None


def _launch(monkeypatch, capsys, tmp_path, *, bind_host: str, frontend: bool = False, running=()) -> list[dict]:
    """Run ``launch_studio`` with recorded spawns; ``running`` URLs already answer."""
    workspace = tmp_path / "ws"
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "app.json").write_text('{"appName": "Studio", "appId": "studio"}\n', encoding="utf-8")
    web_shell = None
    if frontend:
        web_shell = tmp_path / "web_shell"
        (web_shell / "node_modules").mkdir(parents=True)
        (web_shell / "package.json").write_text("{}", encoding="utf-8")
    spawned: list[dict] = []

    def record_spawn(command, *, cwd, env, log_path):
        spawned.append({"command": command, "stderr_before_start": capsys.readouterr().err})
        return _RunningProcess()

    monkeypatch.setattr(studio_launcher, "resolve_web_shell_root", lambda: web_shell)
    monkeypatch.setattr(studio_launcher.shutil, "which", lambda name: "npm")
    monkeypatch.setattr(studio_launcher, "_http_ready", lambda url: any(url.startswith(prefix) for prefix in running))
    monkeypatch.setattr(studio_launcher, "_wait_for_url", lambda url, *, timeout_seconds: True)
    monkeypatch.setattr(studio_launcher, "_assert_mongo_ready", lambda env, *, workspace_root: None)
    monkeypatch.setattr(studio_launcher, "_spawn_process", record_spawn)

    studio_launcher.launch_studio(
        workspace_root=workspace, bind_host=bind_host, backend_port=8123, frontend_port=3123, open_browser=False
    )
    return spawned


def test_studio_launch_on_loopback_prints_no_warning(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH)

    spawned = _launch(monkeypatch, capsys, tmp_path, bind_host="127.0.0.1", frontend=True)

    assert len(spawned) == 2
    printed = "".join(spawn["stderr_before_start"] for spawn in spawned) + capsys.readouterr().err
    assert "authentication is off" not in printed


def test_studio_launch_beyond_loopback_with_auth_off_warns_once_before_the_backend(
    monkeypatch, tmp_path, capsys
) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH, **_KEYCLOAK)

    backend, frontend = _launch(monkeypatch, capsys, tmp_path, bind_host="0.0.0.0", frontend=True)

    warning = backend["stderr_before_start"]
    assert backend["command"][backend["command"].index("--host") + 1] == "0.0.0.0"
    assert f"{_WARNING} 0.0.0.0, which other machines can reach." in warning
    assert "the roles in AUTH_ANON_ROLES: admin, user." in warning
    assert "AUTH_ENABLED=false overrides the provider settings present in the environment" in warning
    assert str(tmp_path / "ws" / ".env") in warning
    assert "WARNING:" not in frontend["stderr_before_start"] + capsys.readouterr().err


def test_studio_launch_warns_before_a_frontend_that_proxies_a_running_backend(
    monkeypatch, tmp_path, capsys
) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH)

    (frontend,) = _launch(
        monkeypatch, capsys, tmp_path, bind_host="0.0.0.0", frontend=True, running=("http://127.0.0.1:8123/",)
    )

    assert frontend["command"][0] == "npm"
    assert f"{_WARNING} 0.0.0.0" in frontend["stderr_before_start"]


def test_studio_launch_beyond_loopback_with_auth_on_prints_no_warning(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, ENV="development", AUTH_ENABLED="true", **_KEYCLOAK)

    (backend,) = _launch(monkeypatch, capsys, tmp_path, bind_host="0.0.0.0")

    assert "authentication is off" not in backend["stderr_before_start"] + capsys.readouterr().err


def test_studio_launch_resolves_the_environment_the_backend_receives(monkeypatch, tmp_path, capsys) -> None:
    """The backend is a child process: its environment decides, not this one's."""
    _auth_env(monkeypatch, ENV="development", AUTH_ENABLED="true", **_KEYCLOAK)
    monkeypatch.setattr(
        studio_launcher, "_workspace_env", lambda workspace_root, *, host: {**_LOCAL_NO_AUTH, "AUTH_ANON_ROLES": "admin"}
    )

    (backend,) = _launch(monkeypatch, capsys, tmp_path, bind_host="0.0.0.0")

    assert f"{_WARNING} 0.0.0.0" in backend["stderr_before_start"]
    assert "the roles in AUTH_ANON_ROLES: admin." in backend["stderr_before_start"]
