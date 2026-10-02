"""`mozaiks serve` on a first run: loopback by default, and a fast, named stop
when MongoDB or the app's secret contract would otherwise fail inside startup."""

from __future__ import annotations

import sys
from argparse import Namespace
from types import ModuleType
from unittest.mock import patch

import pytest

from mozaiks_cli import mongo_preflight
from mozaiks_cli.commands import init_command
from mozaiks_cli.main import create_parser

_VALID_CONTRACT = init_command._secrets_yaml_placeholder()
# The shape `mozaiks init` wrote before the contract gained `version`/`kind`.
_PRE_VERSION_CONTRACT = "provider: env\nsecrets: []\n"


def _workspace(tmp_path, *, secrets: str):
    app_root = tmp_path / "app"
    (app_root / "security").mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "Serve", "appId": "serve"}', encoding="utf-8")
    (app_root / "security" / "secrets.yaml").write_text(secrets, encoding="utf-8")
    return tmp_path


def _serve(monkeypatch, workspace, **overrides) -> dict:
    # serve.run() writes these into os.environ; record restore entries first.
    for name in ("PLATFORM_PATH", "MOZAIKS_APP_WORKSPACE_PATH", "MOZAIKS_HOST"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.delenv("MOZAIKS_SECRETS_CONFIG_PATH", raising=False)

    captured: dict = {}
    fake_uvicorn = ModuleType("uvicorn")

    def _fake_run(app_module, **kwargs):
        captured["app_module"] = app_module
        captured.update(kwargs)

    fake_uvicorn.run = _fake_run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    args = {"workspace": str(workspace), "host": "platform", "port": 8123, "listen": "127.0.0.1", "reload": False}
    args.update(overrides)
    with patch("mozaiks_cli.commands.sync_agent_guidance.auto_sync_agent_guidance"):
        from mozaiks_cli.commands.serve import run

        run(Namespace(**args))
    return captured


def test_serve_and_studio_listen_on_loopback_unless_told_otherwise() -> None:
    parser = create_parser()

    assert parser.parse_args(["serve"]).listen == "127.0.0.1"
    assert parser.parse_args(["serve", "--listen", "0.0.0.0"]).listen == "0.0.0.0"
    assert parser.parse_args(["studio", "--open"]).listen == "127.0.0.1"
    assert parser.parse_args(["studio", "--open", "--listen", "0.0.0.0"]).listen == "0.0.0.0"


def test_serve_starts_the_host_on_loopback_when_mongo_answers(monkeypatch, tmp_path) -> None:
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    pings: list[str] = []
    monkeypatch.setenv("MONGO_URI", "mongodb://127.0.0.1:27017/serve")
    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", lambda uri, *, timeout_ms: pings.append(uri))

    captured = _serve(monkeypatch, workspace)

    assert pings == ["mongodb://127.0.0.1:27017/serve"]
    assert captured["app_module"] == "mozaiksai.hosts.platform:app"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123


def test_serve_stops_fast_and_names_mongo_uri_when_mongo_is_unreachable(monkeypatch, tmp_path, capsys) -> None:
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    monkeypatch.setenv("MONGO_URI", "mongodb://user:secret@127.0.0.1:27099/serve")

    def refuse(uri: str, *, timeout_ms: int) -> None:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", refuse)

    with pytest.raises(SystemExit) as exc:
        _serve(monkeypatch, workspace)

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "MongoDB is not reachable at MONGO_URI (mongodb://***@127.0.0.1:27099/serve)" in out
    assert "user:secret" not in out
    assert str(workspace / ".env") in out
    assert "ConnectionRefusedError: connection refused" in out


def test_serve_names_mongo_uri_when_it_is_not_configured(monkeypatch, tmp_path, capsys) -> None:
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    monkeypatch.delenv("MONGO_URI", raising=False)

    with pytest.raises(SystemExit) as exc:
        _serve(monkeypatch, workspace)

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "MONGO_URI" in out
    assert f"Set MONGO_URI in your shell or in {workspace / '.env'}" in out


def test_serve_reports_an_invalid_secret_contract_before_startup(monkeypatch, tmp_path, capsys) -> None:
    workspace = _workspace(tmp_path, secrets=_PRE_VERSION_CONTRACT)
    monkeypatch.setenv("MONGO_URI", "mongodb://127.0.0.1:27017/serve")

    with pytest.raises(SystemExit) as exc:
        _serve(monkeypatch, workspace)

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "secrets.yaml is not a valid secret contract" in out
    assert "version: 1" in out
    assert "kind: app_secret_contract" in out
