from __future__ import annotations

import pytest

from mozaiks_cli import studio_launcher


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

    monkeypatch.setattr(studio_launcher, "_ping_mongo_uri", fail_ping)

    with pytest.raises(RuntimeError) as exc:
        studio_launcher._assert_mongo_ready(
            {"MONGO_URI": "mongodb://user:password@localhost:27017/mozaiks"},
            workspace_root=tmp_path,
        )

    message = str(exc.value)
    assert "mongodb://***@localhost:27017/mozaiks" in message
    assert "user:password" not in message
    assert "network down" in message


def test_mongo_preflight_accepts_alias(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, int]] = []

    def record_ping(uri: str, *, timeout_ms: int) -> None:
        calls.append((uri, timeout_ms))

    monkeypatch.setattr(studio_launcher, "_ping_mongo_uri", record_ping)

    studio_launcher._assert_mongo_ready(
        {
            "MONGODB_URI": "mongodb://localhost:27017/mozaiks",
            "MOZAIKS_MONGO_PREFLIGHT_TIMEOUT_MS": "1500",
        },
        workspace_root=tmp_path,
    )

    assert calls == [("mongodb://localhost:27017/mozaiks", 1500)]


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

