import io
import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from mozaiksai.core.runtime.app.auth_contract import AppAuthContractError
from mozaiksai.core.sandbox import preview_runtime as runtime


def _response(status, body=""):
    result = io.BytesIO(body.encode())
    result.status = status
    return result


@pytest.fixture(autouse=True)
def isolated_preview_environment(monkeypatch):
    monkeypatch.setattr(runtime.os, "environ", {})


@pytest.fixture
def app_root(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.json").write_text('{"appId":"preview-app","authRequired":true}', encoding="utf-8")
    (app / "config").mkdir()
    factory_auth = Path(__file__).resolve().parents[1] / "factory_app/app/config/auth.yaml"
    (app / "config/auth.yaml").write_text(factory_auth.read_text(encoding="utf-8"), encoding="utf-8")
    shell = tmp_path / "shell"
    vite = shell / "node_modules/vite/bin/vite.js"
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")
    monkeypatch.setattr(runtime, "resolve_web_shell_root", lambda: shell)
    monkeypatch.setattr(runtime, "resolve_chat_ui_root", lambda: tmp_path / "chat-ui")
    monkeypatch.setattr(runtime, "resolve_factory_app_root", lambda: tmp_path / "factory_app")
    return app


def test_authenticated_preview_never_silently_disables_auth(app_root, monkeypatch):
    with pytest.raises(ValueError, match="VITE_OIDC_AUTHORITY"):
        runtime.preview_environment(app_root, preview_url="http://localhost:3000")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    with pytest.raises(ValueError, match="cannot disable"):
        runtime.preview_environment(app_root, preview_url="http://localhost:3000")


def test_preview_uses_disposable_database_and_same_origin_api(app_root, monkeypatch):
    monkeypatch.setenv("AUTH_ISSUER", "http://local-idp")
    monkeypatch.setenv("AUTH_JWKS_URL", "http://local-idp/certs")
    monkeypatch.setenv("VITE_OIDC_AUTHORITY", "http://local-idp")
    monkeypatch.setenv("MONGO_URI", "must-not-use-external-database")
    for name in (
        "AUTH_AUDIENCE", "VITE_OIDC_CLIENT_ID", "VITE_OIDC_REDIRECT_URI", "CORS_ORIGINS",
        "MOZAIKS_HOST", "VITE_MOZAIKS_HOST", "PLATFORM_PATH", "MOZAIKS_APP_WORKSPACE_PATH",
        "MOZAIKS_WORKFLOWS_PATH", "MOZAIKS_APP_DATABASE_NAME", "MOZAIKS_APP_DATA_DATABASE_NAME",
        "VITE_API_URL", "VITE_CORE_URL", "VITE_WS_URL", "MOZAIKS_BACKEND_URL",
    ):
        monkeypatch.setenv(name, "must-not-use-host-setting")
    env = runtime.preview_environment(app_root, preview_url="http://localhost:12345")
    assert env["MONGO_URI"] == "mongodb://127.0.0.1:27017/mozaiks_preview"
    assert env["PLATFORM_PATH"] == str(app_root)
    assert env["MOZAIKS_APP_WORKSPACE_PATH"] == str(app_root.parent)
    assert env["MOZAIKS_WORKFLOWS_PATH"] == str(app_root.parent / "workflows")
    assert env["MOZAIKS_APP_DATABASE_NAME"] == "mozaiks_preview"
    assert env["MOZAIKS_APP_DATA_DATABASE_NAME"] == env["MOZAIKS_APP_DATABASE_NAME"]
    assert env["MOZAIKS_HOST"] == env["VITE_MOZAIKS_HOST"] == "platform"
    assert env["AUTH_AUDIENCE"] == env["VITE_OIDC_CLIENT_ID"] == "preview-app"
    assert env["AUTH_ENABLED"] == "true"
    assert env["VITE_OIDC_REDIRECT_URI"] == "http://localhost:12345/auth/callback"
    assert env["VITE_API_URL"] == env["VITE_CORE_URL"] == env["VITE_WS_URL"] == ""
    assert env["CORS_ORIGINS"] == "http://localhost:12345"
    assert env["MOZAIKS_BACKEND_URL"] == "http://127.0.0.1:8000"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["export", "delete"])
async def test_preview_account_routes_reach_module_persistence(app_root, monkeypatch, action):
    from factory_app.app.modules.user_onboarding.backend.account_data_handler import (
        AccountDataHandler,
    )
    from mozaiksai.core.account.registry import AccountDataRegistry
    from mozaiksai.core.auth import UserPrincipal
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
    from mozaiksai.core.runtime.persistence import app_data, mongo
    from mozaiksai.core.runtime.persistence.naming import collection_name_for
    from mozaiksai.hosts.routers import account
    from tests.test_user_onboarding_account_data_handler import FakeCollection

    class Collection(FakeCollection):
        async def insert_one(self, document):
            self.docs.append(dict(document))
            return SimpleNamespace(inserted_id=len(self.docs))

    monkeypatch.setenv("MOZAIKS_OIDC_AUTHORITY", "http://local-idp")
    monkeypatch.setenv("VITE_OIDC_AUTHORITY", "http://local-idp")
    env = runtime.preview_environment(app_root, preview_url="http://localhost:3000")
    monkeypatch.setattr(runtime.os, "environ", env)
    client = defaultdict(lambda: defaultdict(lambda: Collection([])))
    monkeypatch.setattr(mongo, "get_mongo_client", lambda: client)
    monkeypatch.setattr(app_data, "get_mongo_client", lambda: client)
    registry = AccountDataRegistry()
    registry.register("user_onboarding", AccountDataHandler)
    monkeypatch.setattr(account, "account_data_registry", registry)
    monkeypatch.setattr(account, "get_platform_hooks", lambda: SimpleNamespace())

    # Use the executor's actual context factory and scoped writes, not a DB
    # injected directly into the account helper (which misses preview miswiring).
    contexts = []
    for app_id, user_id in (("preview-app", "owner-a"), ("preview-app", "owner-b"), ("other-app", "owner-a")):
        persistence = ModuleExecutor()._build_persistence_context(SimpleNamespace(
            app_id=app_id, user_id=user_id, tenant_id=None, workspace_id=None,
        ))
        assert persistence is not None
        assert persistence.collection_name("user_onboarding", "status") == collection_name_for(
            app_id=app_id, module_id="user_onboarding", entity_name="status",
        )
        await persistence.collection("user_onboarding", "status").insert_one({"seen_welcome": True})
        contexts.append(persistence)

    principal = UserPrincipal(
        app_id="preview-app", user_id="owner-a", email=None, name=None,
        roles=[], scopes=[], raw_claims={}, provider="test",
    )
    own = contexts[0]
    own_rows = client[own.database_name][own.collection_name("user_onboarding", "status")].docs
    foreign = contexts[2]
    foreign_rows = client[foreign.database_name][foreign.collection_name("user_onboarding", "status")].docs
    if action == "export":
        response = json.loads((await account.export_account_data(principal=principal)).body)
        assert response["user_onboarding_status"] == [
            {"app_id": "preview-app", "user_id": "owner-a", "seen_welcome": True},
        ]
        assert len(own_rows) == 2
    else:
        response = json.loads((await account.delete_account(principal=principal)).body)
        assert response["results"]["user_onboarding"] == {"deleted_count": 1}
        remaining = client[own.database_name][own.collection_name("user_onboarding", "status")].docs
        assert [row["user_id"] for row in remaining] == ["owner-b"]
        repeated = json.loads((await account.delete_account(principal=principal)).body)
        assert repeated["results"]["user_onboarding"] == {"deleted_count": 0}
    assert foreign_rows == [{"app_id": "other-app", "user_id": "owner-a", "seen_welcome": True}]
    assert app_data.app_data_from_context(None, contract={}).db is client[own.database_name]


def test_explicitly_public_app_does_not_inherit_an_enabled_provider(app_root, monkeypatch):
    (app_root / "app.json").write_text('{"appId":"preview-app","authRequired":false}', encoding="utf-8")
    (app_root / "config/auth.yaml").unlink()
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("VITE_OIDC_REDIRECT_URI", "http://host.invalid/callback")
    env = runtime.preview_environment(app_root, preview_url="http://localhost:3000")
    assert env["AUTH_PROVIDER"] == "none" and env["AUTH_ENABLED"] == "false"
    assert env["VITE_OIDC_REDIRECT_URI"] == ""


@pytest.mark.parametrize("required", [True, False])
def test_internal_api_key_is_private_fresh_and_never_inherited(app_root, monkeypatch, required):
    if required:
        monkeypatch.setenv("MOZAIKS_OIDC_AUTHORITY", "http://local-idp")
        monkeypatch.setenv("VITE_OIDC_AUTHORITY", "http://local-idp")
    else:
        (app_root / "app.json").write_text('{"appId":"preview-app","authRequired":false}', encoding="utf-8")
        (app_root / "config/auth.yaml").unlink()
    monkeypatch.setenv("INTERNAL_API_KEY", "host-key-test-sentinel")
    first = runtime.preview_environment(app_root, preview_url="http://localhost:3000")
    second = runtime.preview_environment(app_root, preview_url="http://localhost:3000")

    assert len(first["INTERNAL_API_KEY"]) >= 43
    assert first["INTERNAL_API_KEY"] != second["INTERNAL_API_KEY"]
    assert first["INTERNAL_API_KEY"] != "host-key-test-sentinel"
    assert runtime.os.environ["INTERNAL_API_KEY"] == "host-key-test-sentinel"
    assert all(value != first["INTERNAL_API_KEY"] for name, value in first.items() if name.startswith("VITE_"))
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert len(runtime.preview_environment(app_root, preview_url="http://localhost:3000")["INTERNAL_API_KEY"]) >= 43


@pytest.mark.parametrize("callback", ["/auth/callback", "/session/oidc/return"])
@pytest.mark.parametrize("preview_url", ["http://localhost:12345", "http://localhost:12345/"])
def test_preview_redirect_uses_canonical_auth_callback(app_root, monkeypatch, callback, preview_url):
    path = app_root / "config/auth.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    contract["routes"]["callback"] = callback
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    monkeypatch.setenv("MOZAIKS_OIDC_AUTHORITY", "http://local-idp")
    monkeypatch.setenv("VITE_OIDC_AUTHORITY", "http://local-idp")
    monkeypatch.setenv("VITE_OIDC_REDIRECT_URI", "http://host.invalid/auth/callback")

    env = runtime.preview_environment(app_root, preview_url=preview_url)

    assert env["VITE_OIDC_REDIRECT_URI"] == "http://localhost:12345" + callback


@pytest.mark.parametrize("fault", ["missing", "invalid_yaml", "alias", "unsafe_callback", "public_conflict", "non_boolean"])
def test_preview_rejects_invalid_auth_contracts_through_canonical_loader(app_root, fault):
    path = app_root / "config/auth.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    if fault == "missing":
        path.unlink()
    elif fault == "invalid_yaml":
        path.write_text("routes: [", encoding="utf-8")
    elif fault == "public_conflict":
        (app_root / "app.json").write_text('{"appId":"preview-app","authRequired":false}', encoding="utf-8")
    elif fault == "non_boolean":
        (app_root / "app.json").write_text('{"appId":"preview-app","authRequired":"false"}', encoding="utf-8")
    else:
        if fault == "alias":
            contract["callback_path"] = contract["routes"].pop("callback")
        else:
            contract["routes"]["callback"] = "//foreign.invalid/callback"
        path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    with pytest.raises(AppAuthContractError):
        runtime.preview_environment(app_root, preview_url="http://localhost:3000")


@pytest.mark.parametrize("settings", [
    {"VITE_OIDC_AUTHORITY": "http://local-idp"},
    {"VITE_OIDC_AUTHORITY": "http://local-idp", "AUTH_ISSUER": "http://local-idp"},
    {"VITE_OIDC_AUTHORITY": "http://local-idp", "AUTH_JWKS_URL": "http://local-idp/certs"},
])
def test_authenticated_preview_requires_runtime_oidc_configuration(app_root, monkeypatch, settings):
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="runtime OIDC"):
        runtime.preview_environment(app_root, preview_url="http://localhost:3000")


@pytest.mark.parametrize("app_id", [None, "", 123])
def test_preview_requires_manifest_app_identity(app_root, app_id):
    (app_root / "app.json").write_text(json.dumps({"appId": app_id, "authRequired": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="appId"):
        runtime.preview_environment(app_root, preview_url="http://localhost:3000")


def test_missing_frontend_dependencies_fail_before_start(app_root):
    (app_root.parent / "shell/node_modules/vite/bin/vite.js").unlink()
    with pytest.raises(ValueError, match="dependencies"):
        runtime.preview_environment(app_root, preview_url="http://localhost:3000")


@pytest.mark.parametrize("backend,frontend,shell_app,ready", [(200, 200, "preview-app", True), (503, 200, "preview-app", False), (200, 500, "preview-app", False), (200, 200, "wrong-app", False)])
def test_readiness_checks_backend_frontend_proxy_and_identity(app_root, monkeypatch, backend, frontend, shell_app, ready):
    get = Mock(side_effect=[_response(backend), _response(frontend), _response(200, json.dumps({"appId": shell_app}))])
    monkeypatch.setattr(runtime.urllib.request, "urlopen", get)
    assert runtime.check_ready(app_root=app_root) is ready
    expected = ["http://127.0.0.1:8000/api/health"]
    if backend == 200:
        expected.append("http://127.0.0.1:3000/")
        if frontend == 200:
            expected.append("http://127.0.0.1:3000/api/shell-config")
    assert [call.args[0] for call in get.call_args_list] == expected
    assert all(call.kwargs == {"timeout": 2} for call in get.call_args_list)


@pytest.mark.parametrize("status,body", [
    (503, '{"appId":"preview-app"}'), (200, "not-json"),
    (200, "null"), (200, "[]"), (200, "{}"), (200, '{"appId":"other-app"}'),
])
def test_readiness_rejects_bad_proxied_shell_response(app_root, monkeypatch, status, body):
    get = Mock(side_effect=[_response(200), _response(200), _response(status, body)])
    monkeypatch.setattr(runtime.urllib.request, "urlopen", get)

    assert runtime.check_ready(app_root=app_root) is False
    assert get.call_args_list[-1].args[0] == "http://127.0.0.1:3000/api/shell-config"


def test_readiness_uses_selected_app_root_and_proxy_port(app_root, monkeypatch):
    (app_root / "app.json").write_text('{"appId":"second-preview"}', encoding="utf-8")
    get = Mock(side_effect=[_response(200), _response(200), _response(200, '{"appId":"second-preview"}')])
    monkeypatch.setattr(runtime.urllib.request, "urlopen", get)

    assert runtime.check_ready(app_root=app_root, port=4321) is True
    assert [call.args[0] for call in get.call_args_list] == [
        "http://127.0.0.1:8000/api/health", "http://127.0.0.1:4321/", "http://127.0.0.1:4321/api/shell-config",
    ]


@pytest.mark.parametrize("failed_step", [0, 1, 2])
def test_readiness_connection_error_is_not_success(app_root, monkeypatch, failed_step):
    responses = [_response(200) for _ in range(failed_step)] + [OSError("connection refused")]
    monkeypatch.setattr(runtime.urllib.request, "urlopen", Mock(side_effect=responses))
    assert not runtime.check_ready(app_root=app_root)


def test_preview_image_has_runtime_frontend_database_and_no_host_mounts():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "infra/docker/Dockerfile.preview").read_text()
    assert "USER sandbox" in dockerfile
    assert "pip install --no-cache-dir ." in dockerfile
    assert "npm ci --prefix web_shell" in dockerfile
    assert "COPY --from=mongo" in dockerfile
    assert "COPY .env" not in dockerfile and "docker.sock" not in dockerfile
    ignore = (root / ".dockerignore").read_text()
    assert ".local/" in ignore and ".codex-worktrees/" in ignore
