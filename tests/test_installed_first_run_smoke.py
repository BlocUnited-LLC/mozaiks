"""The release smoke must exercise Studio access and keep Mongo credentials private."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_release_audit, smoke_installed_first_run


def _studio_shell() -> dict:
    return {
        "pages": [
            {"path": "/apps", "component": "AppsPage", "meta": {"requiresRole": "admin"}}
        ]
    }


def test_smoke_checks_the_apps_page_and_backing_api(monkeypatch, tmp_path, capsys) -> None:
    requests: list[str] = []

    def get_json(url: str):
        requests.append(url)
        return 200, {"studio": {"route": "/apps"}, "apps": []}

    monkeypatch.setattr(smoke_installed_first_run, "_get_json", get_json)

    smoke_installed_first_run.check_studio_apps("http://127.0.0.1:8123", _studio_shell(), tmp_path / "studio.log")

    assert requests == ["http://127.0.0.1:8123/api/studio/apps"]
    assert "/api/studio/apps 200" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("shell", "status", "message"),
    [
        ({"pages": []}, 200, "must declare /apps"),
        (_studio_shell(), 403, "/api/studio/apps returned 403"),
    ],
)
def test_smoke_rejects_a_missing_apps_route_or_denied_api(
    monkeypatch, tmp_path, shell, status, message
) -> None:
    monkeypatch.setattr(
        smoke_installed_first_run,
        "_get_json",
        lambda url: (status, {"studio": {"route": "/apps"}, "apps": []}),
    )

    with pytest.raises(smoke_installed_first_run.SmokeFailure, match=message):
        smoke_installed_first_run.check_studio_apps("http://127.0.0.1:8123", shell, tmp_path / "studio.log")


def test_release_audit_never_prints_or_passes_the_mongo_uri_as_an_argument(
    monkeypatch, tmp_path, capsys
) -> None:
    mongo_uri = "mongodb://user:private-password@127.0.0.1:27017/first_run"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(args, *, cwd, env, check):
        calls.append((args, env))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_release_audit.subprocess, "run", fake_run)
    monkeypatch.setattr(run_release_audit.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(tmp_path / "checkout"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "checkout"))

    run_release_audit.step_first_run_smoke(Path(sys.executable), mongo_uri)

    printed = capsys.readouterr().out
    assert mongo_uri not in printed
    assert len(calls) == 1
    command, env = calls[0]
    assert mongo_uri not in " ".join(command)
    assert env[smoke_installed_first_run.MONGO_URI_ENV] == mongo_uri
    assert "MOZAIKS_FACTORY_APP_PATH" not in env
    assert "PYTHONPATH" not in env


def test_smoke_requires_an_empty_directory_outside_the_checkout(tmp_path, capsys) -> None:
    checkout_root = Path(smoke_installed_first_run.__file__).resolve().parents[1]
    assert smoke_installed_first_run.main([
        "--mongo-uri", "mongodb://localhost:27017/smoke",
        "--work-dir", str(checkout_root / "scratch"),
    ]) == 2

    (tmp_path / "marker").write_text("not empty", encoding="utf-8")
    assert smoke_installed_first_run.main([
        "--mongo-uri", "mongodb://localhost:27017/smoke",
        "--work-dir", str(tmp_path),
    ]) == 2
    errors = capsys.readouterr().err
    assert "must be outside the source checkout" in errors
    assert "must be empty" in errors
