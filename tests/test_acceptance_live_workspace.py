"""Exercise the existing acceptance owner's workspace and process boundaries offline."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.generated_ui_acceptance import bundle_digest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "acceptance_live_workspace", ROOT / "examples/canonical-apps/community/scripts/live_environment.py",
)
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


def args_for(workspace=None):
    return argparse.Namespace(
        workspace=workspace, mongo_port=27443, oidc_port=28443, api_port=18443,
        web_port=14443, native_callback=None, client_origin=None,
    )


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "candidate"
    (root / "app").mkdir(parents=True)
    (root / "app/app.json").write_text(json.dumps({"appId": "paired-projects"}), encoding="utf-8")
    return root


def test_workspace_environment_defaults_to_community_and_closes_custom_paths(workspace, tmp_path):
    for selected in (None, workspace):
        frontend, runtime = live.environment(args_for(selected), tmp_path)
        expected = live.WORKSPACE.resolve() if selected is None else workspace.resolve()
        for values in (frontend, runtime):
            assert values["PLATFORM_PATH"] == str(expected / "app")
            assert values["MOZAIKS_APP_WORKSPACE_PATH"] == str(expected)
        assert runtime["AUTH_ENABLED"] == "true"
        assert runtime["AUTH_PROVIDER"] == "jwt"
        assert runtime["AUTH_ISSUER"] == frontend["VITE_OIDC_AUTHORITY"]
        assert runtime["AUTH_JWKS_URL"].endswith("/protocol/openid-connect/certs")


def test_invalid_workspace_stops_before_service_operations(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "docker", lambda *_: pytest.fail("No Docker call is allowed"))
    with pytest.raises(RuntimeError, match="app/app.json"):
        live.run(args_for(tmp_path), tmp_path / "evidence")


def test_explicit_private_identity_fixture_uses_admitted_auth_scopes(workspace, tmp_path, monkeypatch):
    from tests.test_paired_experience_browser import _identity_realm_fixture

    original = (live.WORKSPACE / "tests/identity-realm.json").read_bytes()
    evidence = tmp_path / "fresh-runtime"
    auth = yaml.safe_load((ROOT / "examples/canonical-apps/project-hub/app/config/auth.yaml").read_text(encoding="utf-8"))
    auth["frontend"]["default_scopes"].extend(["messages.read", "messages.write"])
    fixture = _identity_realm_fixture(tmp_path / "identity-inputs/realm.json", {
        "config/auth.yaml": yaml.safe_dump(auth),
        "modules/example/module.yaml": json.dumps({"actions": [
            {"id": "read", "permissions": ["messages.read"]},
            {"id": "write", "api_surface": None, "permissions": ["messages.write"]},
            {"id": "private", "api_surface": "internal", "permissions": ["internal.only"]},
            {"id": "admin", "api_surface": "admin_internal", "permissions": ["admin.only"]},
            {"id": "unapproved", "permissions": ["unapproved.public"]},
        ]}),
    })
    assert not evidence.exists()
    private_input = fixture.read_bytes()
    args = args_for(workspace)
    args.identity_realm = fixture
    mock_owner(monkeypatch, evidence)

    def exercise(state, _process, child_env):
        expected = "openid basic profile email messages.read messages.write"
        assert state["frontend_env"]["VITE_OIDC_SCOPE"] == expected
        assert child_env["VITE_OIDC_SCOPE"] == expected
        assert child_env["AUTH_ENABLED"] == "true"
        realm = json.loads((evidence / "identity-realm.json").read_text(encoding="utf-8"))
        assert {scope["name"] for scope in realm["clientScopes"]} == {
            "basic", "profile", "email", "messages.read", "messages.write",
        }
        client = realm["clients"][0]
        assert client["redirectUris"] == [state["base_url"] + "/auth/callback"]
        mapper = next(item for item in client["protocolMappers"] if item["config"].get("claim.name") == "app_id")
        assert mapper["config"]["claim.value"] == "paired-projects"
        return "verified"

    assert live.run(args, evidence, exercise=exercise) == "verified"
    assert fixture.read_bytes() == private_input
    assert (live.WORKSPACE / "tests/identity-realm.json").read_bytes() == original


@pytest.mark.parametrize("auth", [None, {}, {"frontend": {"default_scopes": ["messages.read"]}}])
def test_private_identity_fixture_requires_valid_admitted_auth(tmp_path, auth):
    from pydantic import ValidationError

    from tests.test_paired_experience_browser import _identity_realm_fixture

    destination = tmp_path / "identity/realm.json"
    files = {} if auth is None else {"config/auth.yaml": yaml.safe_dump(auth)}
    with pytest.raises((KeyError, ValidationError)):
        _identity_realm_fixture(destination, files)
    assert not destination.exists()


@pytest.mark.parametrize("invalid", ["missing", "wrong_realm", "wrong_client"])
def test_invalid_explicit_identity_stops_before_services(workspace, tmp_path, monkeypatch, invalid):
    args = args_for(workspace)
    args.identity_realm = tmp_path / "realm.json"
    if invalid != "missing":
        args.identity_realm.write_text(json.dumps({
            "realm": "unexpected" if invalid == "wrong_realm" else "common-ground",
            "clients": [{"clientId": "unexpected" if invalid == "wrong_client" else "common-ground"}],
        }), encoding="utf-8")
    monkeypatch.setattr(live, "docker", lambda *_: pytest.fail("No Docker call is allowed"))
    with pytest.raises((FileNotFoundError, RuntimeError)):
        live.run(args, tmp_path / "fresh-runtime")


class Process:
    pid = 4321

    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout):
        self.returncode = 0
        return self.returncode


def mock_owner(monkeypatch, evidence, *, request_stop=False):
    process = Process()
    containers = {}
    commands = []

    def docker(*command):
        commands.append(command)
        if command[:2] == ("image", "inspect"):
            return json.dumps([{"Id": "sha256:local-test-image"}])
        if command[0] == "ps":
            return ""
        if command[0] == "create":
            name = command[command.index("--name") + 1]
            run_id = command[command.index("--label") + 1].split("=", 1)[1]
            containers[name] = run_id
            return name
        if command[0] == "inspect":
            return json.dumps([{"Config": {"Labels": {live.LABEL: containers[command[1]]}}}])
        return ""

    def ready(_url, *, process=None, **_kwargs):
        if request_stop and process is not None:
            state = json.loads((evidence / "environment.json").read_text(encoding="utf-8"))
            (evidence / "request.json").write_text(json.dumps({"action": "stop", "run_id": state["run_id"]}))
        return {"status": "ready"}

    monkeypatch.setattr(live, "docker", docker)
    monkeypatch.setattr(live, "free_port", lambda _port: None)
    monkeypatch.setattr(live, "wait_http", ready)
    monkeypatch.setattr(live, "version", lambda _: "test-version")
    monkeypatch.setattr(live.subprocess, "check_output", lambda command, **_: "a" * 40 if "rev-parse" in command else "")
    monkeypatch.setattr(live.subprocess, "Popen", lambda *_, **__: process)
    monkeypatch.setattr(live.subprocess, "run", lambda *_, **__: None)
    return process, commands


@pytest.mark.parametrize("exercise_fails", [False, True])
def test_owned_callback_uses_actual_process_and_always_tears_down(workspace, tmp_path, monkeypatch, exercise_fails):
    evidence = tmp_path / "evidence"
    process, commands = mock_owner(monkeypatch, evidence)
    monkeypatch.setenv("OPENAI_API_KEY", "excluded-test-value")
    monkeypatch.setenv("NODE_OPTIONS", "excluded-test-option")
    called = []

    def exercise(state, active, child_env):
        called.append(state["run_id"])
        assert state["status"] == "ready"
        assert state["app_id"] == "paired-projects"
        assert state["app_root"] == str(workspace / "app")
        assert state["bundle_sha256"] == bundle_digest(workspace / "app")
        assert active is process and active.poll() is None
        assert "OPENAI_API_KEY" not in child_env and "NODE_OPTIONS" not in child_env
        assert child_env["PLATFORM_PATH"] == state["app_root"]
        realm = json.loads((evidence / "identity-realm.json").read_text(encoding="utf-8"))
        client = realm["clients"][0]
        assert client["clientId"] == "common-ground"
        mapper = next(item for item in client["protocolMappers"] if item["config"].get("claim.name") == "app_id")
        assert mapper["config"]["claim.value"] == state["app_id"]
        if exercise_fails:
            raise ValueError("exercise failed")
        return "exercise-result"

    if exercise_fails:
        with pytest.raises(ValueError, match="exercise failed"):
            live.run(args_for(workspace), evidence, exercise=exercise)
    else:
        assert live.run(args_for(workspace), evidence, exercise=exercise) == "exercise-result"
    final = json.loads((evidence / "environment.json").read_text(encoding="utf-8"))
    assert len(called) == 1
    assert process.poll() == 0
    assert final["cleanup_status"] == "complete"
    assert final["status"] == ("failed" if exercise_fails else "stopped")
    assert len([command for command in commands if command[0] == "rm"]) == 2


def test_no_callback_keeps_existing_lifecycle_request_path(workspace, tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    process, _ = mock_owner(monkeypatch, evidence, request_stop=True)
    assert live.run(args_for(workspace), evidence) is None
    assert process.poll() == 0
    assert json.loads((evidence / "environment.json").read_text())["status"] == "stopped"


@pytest.fixture
def preview_setup(workspace, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "web_shell").mkdir(parents=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    frontend, runtime = live.environment(args_for(workspace), evidence)
    state = {"status": "ready", "app_root": str(workspace / "app"),
             "bundle_sha256": bundle_digest(workspace / "app"),
             "base_url": "http://127.0.0.1:14443", "frontend_env": frontend}
    monkeypatch.setattr(live, "REPO", repo)
    monkeypatch.setattr(live, "free_port", lambda _port: None)
    monkeypatch.setattr(live.shutil, "which", lambda command: command)
    return state, evidence, runtime, repo


@pytest.mark.parametrize("exercise_fails", [False, True])
def test_preview_builds_exact_workspace_and_owns_process_cleanup(preview_setup, monkeypatch, exercise_fails):
    state, evidence, runtime, repo = preview_setup
    process = Process()
    expected = bundle_digest(Path(state["app_root"]))
    runtime.update(OPENAI_API_KEY="excluded-test-value", NODE_OPTIONS="excluded-test-option")

    def build(command, **kwargs):
        assert command[:4] == ["npm", "run", "build", "--"]
        assert kwargs["cwd"] == repo / "web_shell"
        assert "OPENAI_API_KEY" not in kwargs["env"] and "NODE_OPTIONS" not in kwargs["env"]
        assert "MONGO_URI" not in kwargs["env"]
        assert kwargs["env"]["PLATFORM_PATH"] == state["app_root"]
        dist = Path(command[command.index("--outDir") + 1])
        dist.mkdir()
        (dist / "index.html").write_text("<main>actual build output</main>")

    def launch(command, **kwargs):
        assert command[:3] == ["node", "node_modules/vite/bin/vite.js", "preview"]
        assert command[command.index("--host") + 1] == "127.0.0.1"
        assert command[command.index("--port") + 1] == "14443"
        assert "--strictPort" in command
        assert Path(command[command.index("--outDir") + 1]) == evidence / "frontend-dist"
        return process

    def ready(url, **kwargs):
        assert url == state["base_url"]
        assert kwargs["process"] is process
        assert kwargs["parse_json"] is False
        return {"status": 200}

    monkeypatch.setattr(live.subprocess, "run", build)
    monkeypatch.setattr(live.subprocess, "Popen", launch)
    monkeypatch.setattr(live, "wait_http", ready)

    def exercise():
        with live.frontend_preview(state, evidence, runtime) as preview:
            assert preview["process"] is process and process.poll() is None
            assert preview["dist_path"] == evidence / "frontend-dist"
            assert preview["bundle_sha256"] == expected
            assert preview["env"]["PLATFORM_PATH"] == state["app_root"]
            if exercise_fails:
                raise ValueError("browser failed")

    if exercise_fails:
        with pytest.raises(ValueError, match="browser failed"):
            exercise()
    else:
        exercise()
    assert process.terminated


@pytest.mark.parametrize("failure", ["not_ready", "wrong_app", "remote_origin", "dotenv", "existing_dist", "changed_after_start"])
def test_preview_rejects_invalid_launch_inputs_before_build(preview_setup, monkeypatch, failure):
    state, evidence, runtime, repo = preview_setup
    if failure == "not_ready":
        state["status"] = "stopped"
    elif failure == "wrong_app":
        state["frontend_env"]["PLATFORM_PATH"] = str(evidence)
    elif failure == "remote_origin":
        state["base_url"] = "https://example.test"
    elif failure == "dotenv":
        (repo / ".env.production.local").touch()
    elif failure == "existing_dist":
        (evidence / "frontend-dist").mkdir()
    else:
        (Path(state["app_root"]) / "changed.txt").write_text("Changed after backend startup")
    monkeypatch.setattr(live.subprocess, "run", lambda *_, **__: pytest.fail("No build is allowed"))
    with pytest.raises(RuntimeError):
        with live.frontend_preview(state, evidence, runtime):
            pytest.fail("Invalid preview must never be yielded")


@pytest.mark.parametrize("failure", ["failed_build", "missing_output", "mutated_bundle"])
def test_preview_cannot_launch_failed_or_changed_build(preview_setup, monkeypatch, failure):
    state, evidence, runtime, _ = preview_setup

    def build(command, **kwargs):
        if failure == "failed_build":
            raise subprocess.CalledProcessError(1, command)
        if failure == "mutated_bundle":
            (evidence / "frontend-dist").mkdir()
            (evidence / "frontend-dist/index.html").write_text("<main>stale</main>")
            (Path(state["app_root"]) / "changed.txt").write_text("different input")

    monkeypatch.setattr(live.subprocess, "run", build)
    monkeypatch.setattr(live.subprocess, "Popen", lambda *_, **__: pytest.fail("No preview is allowed"))
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        with live.frontend_preview(state, evidence, runtime):
            pytest.fail("Failed build must never be yielded")
