"""A host started without authentication says whom it will serve, before it starts.

With authentication off the runtime grants development access (the anonymous
user with ``AUTH_ANON_ROLES``, which a fresh scaffold sets to ``admin,user``,
and request-scoped personas) only per request, and ``AUTH_ANON_ACCESS``
decides to whom: ``local`` (default) serves this machine only, ``open`` every
client, ``public`` every client as an anonymous visitor without development
access. Commands that start a host refuse what the host would refuse (no auth
configuration at all) and what cannot work (``local`` on an address other
machines can reach), warn for ``open`` on such an address and announce
``public``. These tests run the real command functions. Only ``uvicorn.run``
(serve) and the process spawn (Studio launcher) are replaced, by recorders that
also note what had been printed when the host was about to start; nothing binds
a socket.
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
from mozaiks_cli.unauthenticated_bind import assess_unauthenticated_start, is_loopback
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.base import AuthError
from mozaiksai.core.auth.anonymous_access import (
    NOT_CONFIGURED_MESSAGE,
    OPEN_ACCESS_WARNING,
    STUDIO_PUBLIC_MESSAGE,
)
from mozaiksai.core.auth.config import clear_auth_config_cache
from tests.test_cli_serve_first_run import _VALID_CONTRACT, _workspace

_REFUSED = "Error: the server was not started"
_LOCAL_ONLY = "AUTH_ANON_ACCESS is local, so the server answers only requests from this machine"
_OPEN_WARNING = "WARNING: authentication is off with AUTH_ANON_ACCESS=open, and the server is about to listen on"
_ANY_USER = (
    "Every client that can reach it gets development access: it can act as any user with any role, "
    "including admin, credit wallets through billing fulfillment and, on Studio, create apps and "
    "start builds and previews that run code."
)
_PUBLIC_NOTICE = (
    "Authentication is off with AUTH_ANON_ACCESS=public: every client is served as an anonymous "
    "visitor without development access."
)
_KEYCLOAK = {
    "KEYCLOAK_URL": "https://idp.example.invalid",
    "KEYCLOAK_REALM": "apps",
    "KEYCLOAK_CLIENT_ID": "app-api",
}
_LOCAL_NO_AUTH = {"ENV": "development", "AUTH_ENABLED": "false", "AUTH_ANON_ROLES": "admin,user"}
_OPEN_NO_AUTH = {**_LOCAL_NO_AUTH, "AUTH_ANON_ACCESS": "open"}


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


def _serve(monkeypatch, capsys, workspace: Path, *, listen: str, host: str = "platform") -> dict:
    """Run ``mozaiks serve`` up to the point where the host would start.

    Returns what ``uvicorn.run`` received, or ``{"refused": stderr}`` when the
    command exited first.
    """
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
        try:
            serve(Namespace(workspace=str(workspace), host=host, port=8123, listen=listen, reload=False))
        except SystemExit as exc:
            assert exc.code == 1
            assert not started, "the host must not start after a refusal"
            return {"refused": capsys.readouterr().err}
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
def test_serve_on_loopback_starts_quietly(monkeypatch, tmp_path, capsys, listen) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH)

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen=listen)

    assert started["host"] == listen
    assert "authentication is off" not in started["stderr_before_start"] + capsys.readouterr().err


def test_serve_refuses_local_access_beyond_loopback(monkeypatch, tmp_path, capsys) -> None:
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH)

    refused = _serve(monkeypatch, capsys, workspace, listen="0.0.0.0")["refused"]

    assert f"{_REFUSED} on 0.0.0.0." in refused
    assert f"Authentication is off (AUTH_ENABLED=false) and {_LOCAL_ONLY}" in refused
    assert "--listen 0.0.0.0 accepts connections from other machines" in refused
    assert "--listen 127.0.0.1" in refused
    assert "AUTH_ANON_ACCESS=public" in refused
    assert "AUTH_ANON_ACCESS=open" in refused
    assert str(workspace / ".env") in refused


def test_serve_refusal_says_auth_enabled_false_overrides_a_supplied_provider(
    monkeypatch, tmp_path, capsys
) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH, **_KEYCLOAK, AUTH_ISSUER="https://idp.example.invalid/realms/apps")

    refused = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")["refused"]

    assert (
        "AUTH_ENABLED=false overrides the provider settings present in the environment "
        "(KEYCLOAK_URL, KEYCLOAK_REALM, AUTH_ISSUER): they are ignored."
    ) in refused


def test_serve_with_open_access_beyond_loopback_warns_before_starting(monkeypatch, tmp_path, capsys) -> None:
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    _auth_env(monkeypatch, **_OPEN_NO_AUTH)

    started = _serve(monkeypatch, capsys, workspace, listen="0.0.0.0")

    warning = started["stderr_before_start"]
    assert f"{_OPEN_WARNING} 0.0.0.0, which other machines can reach." in warning
    assert _ANY_USER in warning
    assert (
        "Requests that claim no identity are the anonymous user, with the roles in "
        "AUTH_ANON_ROLES: admin, user."
    ) in warning
    assert "To turn authentication on, set AUTH_ENABLED=true and configure an identity provider" in warning
    assert str(workspace / ".env") in warning
    assert "AUTH_ANON_ACCESS=public" in warning
    assert warning.count("WARNING:") == 1
    # A warning only: the host still starts, on the address that was asked for.
    assert started["host"] == "0.0.0.0"


def test_open_warning_states_the_same_consequences_as_the_host_startup_warning() -> None:
    # The CLI box and the host's startup WARNING describe one posture; they
    # must not drift apart in what they say development access allows.
    consequences = OPEN_ACCESS_WARNING.split("gets development access: ", 1)[1].split(" Use it only", 1)[0]
    assert consequences.startswith("it can act as any user with any role, including admin, ")
    assert _ANY_USER.endswith(f"gets development access: {consequences}")


@pytest.mark.parametrize("roles", [None, "", " , "], ids=["unset", "empty", "blank_entries"])
def test_open_warning_does_not_present_empty_anonymous_roles_as_a_limit(
    monkeypatch, tmp_path, capsys, roles
) -> None:
    auth = {"ENV": "development", "AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "open"}
    if roles is not None:
        auth["AUTH_ANON_ROLES"] = roles
    _auth_env(monkeypatch, **auth)

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")

    warning = started["stderr_before_start"]
    assert _ANY_USER in warning
    assert (
        "Requests that claim no identity are the anonymous user. AUTH_ANON_ROLES is empty, which "
        "does not limit access while development access is granted."
    ) in warning
    assert started["host"] == "0.0.0.0"


@pytest.mark.parametrize(
    ("switches", "expected"),
    [
        ({"AUTH_PROVIDER": "none"}, "AUTH_PROVIDER=none overrides"),
        ({"AUTH_ENABLED": "false", "AUTH_PROVIDER": "none"}, "AUTH_ENABLED=false and AUTH_PROVIDER=none override"),
    ],
    ids=["auth_provider_none", "both_switches"],
)
def test_open_warning_names_each_switch_that_overrides_a_supplied_provider(
    monkeypatch, tmp_path, capsys, switches, expected
) -> None:
    _auth_env(monkeypatch, ENV="development", AUTH_ANON_ACCESS="open", **switches, **_KEYCLOAK)

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")

    assert (
        f"{expected} the provider settings present in the environment (KEYCLOAK_URL, KEYCLOAK_REALM): "
        "they are ignored."
    ) in started["stderr_before_start"]


def test_serve_with_open_access_on_loopback_starts_quietly(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, **_OPEN_NO_AUTH)

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="127.0.0.1")

    assert "WARNING" not in started["stderr_before_start"]


@pytest.mark.parametrize(
    "auth",
    [_LOCAL_NO_AUTH, {"ENV": "development"}, _OPEN_NO_AUTH],
    ids=["local_beyond_loopback", "not_configured", "open_warning"],
)
def test_studio_messages_never_suggest_public_access(monkeypatch, tmp_path, capsys, auth) -> None:
    """Studio refuses AUTH_ANON_ACCESS=public, so its messages do not offer it."""
    _auth_env(monkeypatch, **auth)

    result = _serve(
        monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0", host="studio"
    )

    text = result.get("refused") or result.get("stderr_before_start")
    assert text
    assert "AUTH_ANON_ACCESS=public to serve" not in text
    assert "without development access: AUTH_ANON_ACCESS=public" not in text
    assert "set AUTH_ANON_ACCESS=public." not in text


@pytest.mark.parametrize("listen", ["127.0.0.1", "0.0.0.0"])
def test_serve_announces_public_access(monkeypatch, tmp_path, capsys, listen) -> None:
    _auth_env(monkeypatch, AUTH_ENABLED="false", AUTH_ANON_ACCESS="public")

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen=listen)

    assert started["host"] == listen
    assert _PUBLIC_NOTICE in started["stderr_before_start"]
    assert "WARNING" not in started["stderr_before_start"]


def test_serve_refuses_a_public_studio(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, AUTH_ENABLED="false", AUTH_ANON_ACCESS="public")

    refused = _serve(
        monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="127.0.0.1", host="studio"
    )["refused"]

    assert STUDIO_PUBLIC_MESSAGE in refused


def test_serve_takes_anonymous_access_alone_as_the_choice(monkeypatch, tmp_path, capsys) -> None:
    """What a generated public bundle's .env.example carries: AUTH_ANON_ACCESS only."""
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)

    _auth_env(monkeypatch, AUTH_ANON_ACCESS="public")
    started = _serve(monkeypatch, capsys, workspace, listen="0.0.0.0")
    assert _PUBLIC_NOTICE in started["stderr_before_start"]

    _auth_env(monkeypatch, AUTH_ANON_ACCESS="local")
    refused = _serve(monkeypatch, capsys, workspace, listen="0.0.0.0")["refused"]
    assert (
        "Authentication is off (AUTH_ANON_ACCESS=local), so the server answers only requests from "
        "this machine, but --listen 0.0.0.0 accepts connections from other machines."
    ) in refused


def test_public_notice_names_provider_settings_it_ignores(monkeypatch, tmp_path, capsys) -> None:
    """A complete provider would turn authentication on; half of one is reported."""
    _auth_env(monkeypatch, AUTH_ANON_ACCESS="public", KEYCLOAK_URL=_KEYCLOAK["KEYCLOAK_URL"])

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="127.0.0.1")

    assert _PUBLIC_NOTICE in started["stderr_before_start"]
    assert (
        "The provider settings present in the environment (KEYCLOAK_URL) do not select a provider "
        "on their own: they are ignored."
    ) in started["stderr_before_start"]


@pytest.mark.parametrize("listen", ["127.0.0.1", "0.0.0.0"])
@pytest.mark.parametrize(
    "auth",
    [{}, {"ENV": "development"}, {"AUTH_ENABLED": ""}],
    ids=["nothing", "env_development_only", "empty_auth_enabled"],
)
def test_serve_refuses_when_authentication_is_not_configured(monkeypatch, tmp_path, capsys, auth, listen) -> None:
    """Implicit demo mode serves nobody, on any address: the operator chooses."""
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    _auth_env(monkeypatch, **auth)

    refused = _serve(monkeypatch, capsys, workspace, listen=listen)["refused"]

    assert _REFUSED in refused
    assert NOT_CONFIGURED_MESSAGE in refused
    assert str(workspace / ".env") in refused


def test_serve_refusal_says_an_incomplete_provider_signal_selects_nothing(monkeypatch, tmp_path, capsys) -> None:
    """Half of Keycloak's pair, nothing else: no provider, so no auth configuration."""
    _auth_env(monkeypatch, ENV="development", KEYCLOAK_URL=_KEYCLOAK["KEYCLOAK_URL"])

    refused = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="127.0.0.1")["refused"]

    assert NOT_CONFIGURED_MESSAGE in refused
    assert (
        "The provider settings present in the environment (KEYCLOAK_URL) do not select a provider "
        "on their own: they are ignored."
    ) in refused


def test_serve_beyond_loopback_with_auth_on_prints_nothing(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, ENV="development", AUTH_ENABLED="true", **_KEYCLOAK)

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")

    assert started["host"] == "0.0.0.0"
    assert "authentication is off" not in started["stderr_before_start"].lower()


def test_serve_leaves_the_production_refusal_to_the_host(monkeypatch, tmp_path, capsys) -> None:
    """ENV=production without authentication: the host refuses, as before."""
    _auth_env(monkeypatch, ENV="production", AUTH_ENABLED="false", AUTH_ANON_ROLES="admin,user")

    started = _serve(monkeypatch, capsys, _workspace(tmp_path, secrets=_VALID_CONTRACT), listen="0.0.0.0")

    # The command adds no message and no refusal of its own ...
    assert started["host"] == "0.0.0.0"
    assert "authentication is off" not in started["stderr_before_start"].lower()
    # ... because the host's startup validation still refuses this environment.
    with pytest.raises(AuthError, match="not permitted in the 'production' environment"):
        auth_registry.validate_auth_provider_configuration()


def _fresh_scaffold(monkeypatch, tmp_path) -> Path:
    workspace = tmp_path / "fresh"
    init_command.create_scaffold(target_dir=workspace, preset="chat", app_name="Fresh")
    # serve() loads the workspace .env into os.environ; record restore entries.
    for name in dotenv_values(workspace / ".env.example"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    return workspace


def test_fresh_scaffold_served_on_loopback_starts_with_no_extra_step(monkeypatch, tmp_path, capsys) -> None:
    """`mozaiks init` then `mozaiks serve` on a laptop keeps working as it is."""
    _auth_env(monkeypatch)
    workspace = _fresh_scaffold(monkeypatch, tmp_path)

    started = _serve(monkeypatch, capsys, workspace, listen="127.0.0.1")

    assert (workspace / ".env").is_file()
    assert started["host"] == "127.0.0.1"
    assert started["stderr_before_start"] == ""


def test_fresh_scaffold_served_beyond_loopback_is_refused(monkeypatch, tmp_path, capsys) -> None:
    """The reported case: a provider in the container environment, and the
    ``.env`` that serve creates from the scaffold switching authentication off."""
    _auth_env(monkeypatch, **_KEYCLOAK)
    workspace = _fresh_scaffold(monkeypatch, tmp_path)

    refused = _serve(monkeypatch, capsys, workspace, listen="0.0.0.0")["refused"]

    assert (workspace / ".env").is_file()
    assert _LOCAL_ONLY in refused
    assert (
        "AUTH_ENABLED=false overrides the provider settings present in the environment "
        "(KEYCLOAK_URL, KEYCLOAK_REALM): they are ignored."
    ) in refused


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


def test_studio_launch_on_loopback_prints_nothing(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH)

    spawned = _launch(monkeypatch, capsys, tmp_path, bind_host="127.0.0.1", frontend=True)

    assert len(spawned) == 2
    printed = "".join(spawn["stderr_before_start"] for spawn in spawned) + capsys.readouterr().err
    assert "authentication is off" not in printed.lower()


def test_studio_launch_refuses_local_access_beyond_loopback_before_any_server(
    monkeypatch, tmp_path, capsys
) -> None:
    _auth_env(monkeypatch, **_LOCAL_NO_AUTH, **_KEYCLOAK)
    spawned: list = []
    monkeypatch.setattr(studio_launcher, "_spawn_process", lambda *a, **k: spawned.append(a))

    with pytest.raises(RuntimeError) as refusal:
        _launch(monkeypatch, capsys, tmp_path, bind_host="0.0.0.0", frontend=True)

    assert _LOCAL_ONLY in str(refusal.value)
    assert "AUTH_ENABLED=false overrides the provider settings present in the environment" in str(refusal.value)
    assert spawned == []


def test_studio_launch_refuses_when_authentication_is_not_configured(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, ENV="development")

    with pytest.raises(RuntimeError, match="Authentication is not configured"):
        _launch(monkeypatch, capsys, tmp_path, bind_host="127.0.0.1")


def test_studio_launch_with_open_access_beyond_loopback_warns_once_before_the_backend(
    monkeypatch, tmp_path, capsys
) -> None:
    _auth_env(monkeypatch, **_OPEN_NO_AUTH, **_KEYCLOAK)

    backend, frontend = _launch(monkeypatch, capsys, tmp_path, bind_host="0.0.0.0", frontend=True)

    warning = backend["stderr_before_start"]
    assert backend["command"][backend["command"].index("--host") + 1] == "0.0.0.0"
    assert f"{_OPEN_WARNING} 0.0.0.0, which other machines can reach." in warning
    assert "the roles in AUTH_ANON_ROLES: admin, user." in warning
    assert "AUTH_ENABLED=false overrides the provider settings present in the environment" in warning
    assert str(tmp_path / "ws" / ".env") in warning
    assert "WARNING:" not in frontend["stderr_before_start"] + capsys.readouterr().err


def test_studio_launch_warns_before_a_frontend_that_proxies_a_running_backend(
    monkeypatch, tmp_path, capsys
) -> None:
    _auth_env(monkeypatch, **_OPEN_NO_AUTH)

    (frontend,) = _launch(
        monkeypatch, capsys, tmp_path, bind_host="0.0.0.0", frontend=True, running=("http://127.0.0.1:8123/",)
    )

    assert frontend["command"][0] == "npm"
    assert f"{_OPEN_WARNING} 0.0.0.0" in frontend["stderr_before_start"]


def test_studio_launch_refuses_public_access(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, AUTH_ENABLED="false", AUTH_ANON_ACCESS="public")

    with pytest.raises(RuntimeError, match="Studio cannot run with AUTH_ANON_ACCESS=public"):
        _launch(monkeypatch, capsys, tmp_path, bind_host="127.0.0.1")


def test_studio_launch_beyond_loopback_with_auth_on_prints_nothing(monkeypatch, tmp_path, capsys) -> None:
    _auth_env(monkeypatch, ENV="development", AUTH_ENABLED="true", **_KEYCLOAK)

    (backend,) = _launch(monkeypatch, capsys, tmp_path, bind_host="0.0.0.0")

    assert "authentication is off" not in (backend["stderr_before_start"] + capsys.readouterr().err).lower()


def test_studio_launch_resolves_the_environment_the_backend_receives(monkeypatch, tmp_path, capsys) -> None:
    """The backend is a child process: its environment decides, not this one's."""
    _auth_env(monkeypatch, ENV="development", AUTH_ENABLED="true", **_KEYCLOAK)
    monkeypatch.setattr(
        studio_launcher, "_workspace_env", lambda workspace_root, *, host: {**_OPEN_NO_AUTH, "AUTH_ANON_ROLES": "admin"}
    )

    (backend,) = _launch(monkeypatch, capsys, tmp_path, bind_host="0.0.0.0")

    assert f"{_OPEN_WARNING} 0.0.0.0" in backend["stderr_before_start"]
    assert "the roles in AUTH_ANON_ROLES: admin." in backend["stderr_before_start"]


# --- Every outcome names the provider settings it ignores ------------------------


_HALF_KEYCLOAK = {"KEYCLOAK_URL": _KEYCLOAK["KEYCLOAK_URL"]}
_SELECTS_NOTHING = (
    "The provider settings present in the environment (KEYCLOAK_URL) do not select a provider "
    "on their own: they are ignored."
)
_OVERRIDDEN = (
    "AUTH_ENABLED=false overrides the provider settings present in the environment (KEYCLOAK_URL): "
    "they are ignored."
)


@pytest.mark.parametrize(
    ("environ", "listen", "host", "outcome", "ignored"),
    [
        (_HALF_KEYCLOAK, "127.0.0.1", "platform", "refusal", _SELECTS_NOTHING),
        (_HALF_KEYCLOAK, "127.0.0.1", "studio", "refusal", _SELECTS_NOTHING),
        ({"AUTH_ANON_ACCESS": "public", **_HALF_KEYCLOAK}, "127.0.0.1", "studio", "refusal", _SELECTS_NOTHING),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public", **_HALF_KEYCLOAK}, "127.0.0.1", "studio", "refusal", _OVERRIDDEN),
        ({"AUTH_ANON_ACCESS": "public", **_HALF_KEYCLOAK}, "0.0.0.0", "platform", "notice", _SELECTS_NOTHING),
        ({"AUTH_ENABLED": "false", **_HALF_KEYCLOAK}, "0.0.0.0", "platform", "refusal", _OVERRIDDEN),
        ({"AUTH_ENABLED": "false", **_HALF_KEYCLOAK}, "0.0.0.0", "studio", "refusal", _OVERRIDDEN),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "open", **_HALF_KEYCLOAK}, "0.0.0.0", "platform", "notice", _OVERRIDDEN),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "open", **_HALF_KEYCLOAK}, "0.0.0.0", "studio", "notice", _OVERRIDDEN),
    ],
    ids=[
        "not_configured", "not_configured_studio", "public_studio", "public_studio_hard_off",
        "public", "local_beyond_loopback", "local_beyond_loopback_studio", "open_beyond_loopback",
        "open_beyond_loopback_studio",
    ],
)
def test_every_outcome_names_the_provider_settings_it_ignores(tmp_path, environ, listen, host, outcome, ignored) -> None:
    start = assess_unauthenticated_start(listen, environ=environ, env_file=tmp_path / ".env", host=host)

    text = getattr(start, outcome)
    assert text is not None and ignored in text
    assert getattr(start, "notice" if outcome == "refusal" else "refusal") is None


def test_a_boxed_refusal_is_printed_without_a_second_error_prefix(monkeypatch, tmp_path, capsys) -> None:
    """The Studio launcher raises the box; the CLI prints it as it is."""
    from mozaiks_cli import main as cli_main

    refusal = assess_unauthenticated_start(
        "127.0.0.1", environ={"AUTH_ANON_ACCESS": "public"}, env_file=tmp_path / ".env", host="studio"
    ).refusal
    assert refusal is not None and refusal.startswith("=")

    def fail_with(error: Exception):
        def run(args):
            raise error

        return run

    monkeypatch.setattr(sys, "argv", ["mozaiks", "info"])
    monkeypatch.setattr(cli_main.info_command, "run", fail_with(RuntimeError(refusal)))
    with pytest.raises(SystemExit) as exited:
        cli_main.main()
    assert exited.value.code == 1
    assert capsys.readouterr().err == f"{refusal}\n"
    assert refusal.splitlines()[1] == "Error: the server was not started."

    monkeypatch.setattr(cli_main.info_command, "run", fail_with(RuntimeError("no workspace here")))
    with pytest.raises(SystemExit):
        cli_main.main()
    assert capsys.readouterr().err == "Error: no workspace here\n"
