"""An app workspace serves exactly the workflows it declares.

A host binds one workflow root. Studio binds the packaged Factory root; any
other host binds the active app's own ``workflows/`` root, and when the app
declares none it serves none. The server checks boot the real platform host in
its own process on 127.0.0.1, with JWT sign-in verified against a local JWKS
and a real MongoDB (MONGO_URI); nothing in the host is patched.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.workflow.paths import (
    discover_workflow_paths,
    resolve_workflow_path,
    resolve_workflows_root,
)
from mozaiksai.hosts.bootstrap import resolve_repo_host_defaults
from mozaiksai.resources import resolve_factory_workflows_root

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
AUDIENCE = "workflow-root-api"
APP_ID = "tasktracker"


def _write_app(app_root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        target = app_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return app_root


def _recorded_bundle(app_root: Path) -> Path:
    bundle = json.loads((FIXTURES / "runtime_smoke_good_bundle_fdfa818e.json").read_text(encoding="utf-8"))
    return _write_app(app_root, bundle["files"])


def _bind_app(monkeypatch: pytest.MonkeyPatch, app_root: Path) -> None:
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(app_root))
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)


# --------------------------------------------------------------------------- root resolution


def test_app_without_workflows_binds_its_own_absent_root(monkeypatch, tmp_path) -> None:
    app_root = _recorded_bundle(tmp_path / "app")
    _bind_app(monkeypatch, app_root)

    root = resolve_workflows_root()

    assert root == (tmp_path / "workflows").resolve()
    assert not root.exists()
    assert discover_workflow_paths() == {}
    assert resolve_workflow_path("DesignDocs") is None


def test_platform_host_defaults_select_no_factory_root_for_an_app_without_workflows(tmp_path) -> None:
    app_root = _recorded_bundle(tmp_path / "app")

    updates = resolve_repo_host_defaults("platform", {"PLATFORM_PATH": str(app_root)})

    assert updates["PLATFORM_PATH"] == str(app_root.resolve())
    assert "MOZAIKS_WORKFLOWS_PATH" not in updates


def test_studio_host_defaults_still_select_the_factory_root(tmp_path) -> None:
    app_root = _recorded_bundle(tmp_path / "app")

    updates = resolve_repo_host_defaults("studio", {"PLATFORM_PATH": str(app_root)})

    factory_root = resolve_factory_workflows_root()
    assert factory_root is not None
    assert updates["MOZAIKS_WORKFLOWS_PATH"] == str(factory_root)


def test_app_registry_extending_the_default_registry_still_resolves_factory_workflows(
    monkeypatch, tmp_path,
) -> None:
    app_root = _recorded_bundle(tmp_path / "app")
    workflows_root = tmp_path / "workflows"
    registry = workflows_root / "extended_orchestration" / "extension_registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"extends": "mozaiks.default_workflow_registry"}), encoding="utf-8")
    own = workflows_root / "SupportTriage"
    own.mkdir()
    (own / "orchestrator.yaml").write_text("workflow_name: SupportTriage\n", encoding="utf-8")
    _bind_app(monkeypatch, app_root)

    factory_root = resolve_factory_workflows_root()
    assert factory_root is not None
    assert resolve_workflows_root() == workflows_root.resolve()
    discovered = discover_workflow_paths()
    assert discovered["SupportTriage"] == own.resolve()
    assert discovered["DesignDocs"] == (factory_root / "DesignDocs").resolve()
    assert resolve_workflow_path("DesignDocs") == (factory_root / "DesignDocs").resolve()


# --------------------------------------------------------------------------- live platform host


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str, *, method: str = "GET", token: str | None = None, body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, raw.decode("utf-8", errors="replace")


@pytest.fixture
def mongo_uri() -> str:
    uri = os.environ.get("MONGO_URI", "").strip()
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    from pymongo import MongoClient

    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    except Exception:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    finally:
        client.close()
    return uri


@pytest.fixture
def identity_provider() -> Iterator[dict[str, Any]]:
    """A local JWKS endpoint and a minter for ordinary signed-in user tokens."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {
        **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())),
        "kid": "workflow-root-key", "alg": "RS256", "use": "sig",
    }
    payload = json.dumps({"keys": [jwk]}).encode("utf-8")

    class _JWKS(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), _JWKS)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    issuer = f"http://127.0.0.1:{server.server_address[1]}"

    def token(*, app_id: str | None = APP_ID) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": issuer, "sub": "ordinary-user", "aud": AUDIENCE,
            "iat": now, "nbf": now - 1, "exp": now + 600,
        }
        if app_id:
            claims["app_id"] = app_id
        return jwt.encode(
            claims, signing_key, algorithm="RS256", headers={"kid": "workflow-root-key", "typ": "at+jwt"},
        )

    try:
        yield {"issuer": issuer, "token": token}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@contextmanager
def _platform_host(app_root: Path, *, mongo_uri: str, issuer: str, log_path: Path) -> Iterator[str]:
    """Serve ``app_root`` on the platform host as ``mozaiks serve`` configures it."""
    env = {
        name: value for name, value in os.environ.items()
        if name not in {"ENV", "PLATFORM_PATH", "REDIS_URL", "DATABASE_URL"}
        and not name.startswith(("MOZAIKS_", "AUTH_", "KEYCLOAK_", "SUPABASE_", "MONGO", "OPENAI_"))
    }
    env.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(ROOT),
        "PLATFORM_PATH": str(app_root),
        "MOZAIKS_APP_WORKSPACE_PATH": str(app_root),
        "MOZAIKS_HOST": "platform",
        "MONGO_URI": mongo_uri,
        "AUTH_ENABLED": "true",
        "AUTH_PROVIDER": "jwt",
        "AUTH_ISSUER": issuer,
        "AUTH_JWKS_URL": f"{issuer}/jwks",
        "AUTH_AUDIENCE": AUDIENCE,
        "RATE_LIMIT_ENABLED": "false",
    })
    port = _free_port()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-P", "-m", "uvicorn", "mozaiksai.hosts.platform:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=app_root, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 150
            while True:
                if process.poll() is not None:
                    pytest.fail(f"platform host exited {process.returncode}:\n{log_path.read_text(encoding='utf-8')[-4000:]}")
                if time.monotonic() > deadline:
                    pytest.fail(f"platform host never became healthy:\n{log_path.read_text(encoding='utf-8')[-4000:]}")
                try:
                    if _request(f"{base}/api/health")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.5)
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=30)


def test_recorded_bundle_on_the_platform_host_serves_no_factory_workflows(
    tmp_path, mongo_uri, identity_provider,
) -> None:
    # The bundle root is named ``app``, as in the generated image's WORKDIR.
    app_root = _recorded_bundle(tmp_path / "app")
    factory_root = resolve_factory_workflows_root()
    assert factory_root is not None
    token = identity_provider["token"]()

    with _platform_host(
        app_root, mongo_uri=mongo_uri, issuer=identity_provider["issuer"], log_path=tmp_path / "host.log",
    ) as base:
        status, health = _request(f"{base}/api/health")
        listed_anonymous = _request(f"{base}/api/workflows")
        listed = _request(f"{base}/api/workflows", token=token)
        started = _request(
            f"{base}/api/chats/{APP_ID}/DesignDocs/start", method="POST", token=token,
            body={"user_id": "ordinary-user"},
        )

    assert status == 200
    assert health["workflows"] == {"total_workflows": 0, "loaded_workflows": 0, "error_workflows": 0}
    rendered = json.dumps(health)
    for path in (tmp_path, factory_root):
        for spelling in {str(path), path.as_posix(), str(path.resolve()), path.resolve().as_posix()}:
            assert json.dumps(spelling)[1:-1] not in rendered
    assert listed_anonymous[0] == 401
    assert listed == (200, {"workflows": []})
    assert started[0] == 404, started


def _fresh_scaffold(target_dir: Path, *, starter: bool) -> Path:
    from mozaiks_cli.commands.init import create_scaffold

    return create_scaffold(
        target_dir=target_dir, preset="chat", app_name="Atlas", admin_email="admin@example.com", starter=starter,
    )


@pytest.mark.parametrize(("starter", "expected"), [(False, set()), (True, {"HelloWorkflow"})])
def test_fresh_init_scaffold_binds_its_own_workflow_root(monkeypatch, tmp_path, starter, expected) -> None:
    workspace = _fresh_scaffold(tmp_path / "atlas", starter=starter)
    _bind_app(monkeypatch, workspace / "app")

    own_root = (workspace / "workflows").resolve()
    assert resolve_workflows_root() == own_root
    assert set(discover_workflow_paths()) == expected
    defaults = resolve_repo_host_defaults("platform", {"PLATFORM_PATH": str(workspace / "app")})
    assert defaults["MOZAIKS_WORKFLOWS_PATH"] == str(own_root)


def test_fresh_init_scaffold_on_the_platform_host_serves_its_own_workflows(
    tmp_path, mongo_uri, identity_provider,
) -> None:
    from mozaiksai.core.secrets.app_secrets import load_secret_contract
    from mozaiksai.core.secrets.contract import SecretContractError

    workspace = _fresh_scaffold(tmp_path / "atlas", starter=True)
    try:
        load_secret_contract(app_root=workspace / "app")
    except SecretContractError as exc:
        pytest.skip(f"a fresh scaffold's app/security/secrets.yaml does not load yet (#797): {exc}")
    token = identity_provider["token"](app_id=None)

    with _platform_host(
        workspace / "app", mongo_uri=mongo_uri, issuer=identity_provider["issuer"], log_path=tmp_path / "host.log",
    ) as base:
        status, health = _request(f"{base}/api/health")
        listed = _request(f"{base}/api/workflows", token=token)

    assert status == 200
    assert health["workflows"]["total_workflows"] == 1
    assert listed[0] == 200
    assert [workflow["name"] for workflow in listed[1]["workflows"]] == ["HelloWorkflow"]
