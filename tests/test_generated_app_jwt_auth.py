"""Generated-app JWT acceptance at the real platform HTTP boundary.

The platform has process-global state, so this check runs in a fresh interpreter.
JWKS and MongoDB are local stubs; the JWT adapter and HTTP auth dependencies
remain unmodified.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_probe() -> None:
    import asyncio
    import json
    import time

    import jwt
    import pytest
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient

    import mozaiksai.core.auth.adapters.jwt_adapter as jwt_adapter_module
    from factory_app.workflows.AppGenerator.tools.deployment_contract import (
        generate_deployment_artifacts,
    )
    from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import (
        materialize_auth_scaffold,
    )
    from mozaiksai.core.auth.adapters.base import AuthError
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
    from mozaiksai.core.runtime.app.loader import AppLoader
    from tests.test_generated_app_functional_acceptance import (
        _basic_crud_files,
        _FakeMongoClient,
        _materialize_bundle,
        _prepare_platform_test_runtime,
    )

    app_root = Path(os.environ["JWT_ACCEPTANCE_APP_ROOT"])
    files = _basic_crud_files()
    manifest = json.loads(files["app.json"])
    manifest["authRequired"] = True
    files["app.json"] = json.dumps(manifest)
    scaffold = materialize_auth_scaffold(
        files, data_contract=json.loads(files["data/contract.json"]),
    )
    assert {"config/auth.yaml", "ui/auth/authAdapter.js", "ui/route_manifest.json"} <= scaffold.keys()
    files.update(scaffold)

    deployment = generate_deployment_artifacts(
        app_id="support-operations", deployment_profile="production_container",
        auth_required=True, auth_provider="jwt",
    )
    assert deployment["deploy_target_spec_errors"] == []
    assert deployment["bundle_errors"] == []
    assert "AUTH_AUDIENCE" in deployment["deployment_manifest"]["required_env"]
    assert "AUTH_AUDIENCE" in deployment["deployment_manifest"]["auth"]["runtime_required_variables"]
    assert "AUTH_AUDIENCE=<required>" in deployment["artifacts"][".env.production.example"]
    files.update(deployment["artifacts"])
    _materialize_bundle(app_root, files)

    loaded = asyncio.run(AppLoader.load(str(app_root)))
    assert loaded.auth_contract is not None and loaded.auth_contract.auth_required is True
    assert any(module.name == "orders" for module in loaded.modules)
    assert "orders" in loaded.page_schemas

    issuer = "https://issuer.example.invalid"
    audience = "support-operations-api"
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = {
        **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())),
        "kid": "generated-app-key", "alg": "RS256", "use": "sig",
    }

    class StaticJWKSClient:
        def __init__(self, *, jwks_url: str, **_kwargs: object) -> None:
            assert jwks_url == f"{issuer}/jwks"

        async def get_signing_key(self, kid: str) -> dict[str, object] | None:
            return public_jwk if kid == "generated-app-key" else None

    def token(*, aud: str | None = audience, typ: str = "at+jwt") -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": issuer, "sub": "alice", "app_id": "support-operations",
            "iat": now, "nbf": now - 1, "exp": now + 300,
        }
        if aud is not None:
            claims["aud"] = aud
        return jwt.encode(
            claims, signing_key, algorithm="RS256",
            headers={"kid": "generated-app-key", "typ": typ},
        )

    fake_mongo = _FakeMongoClient()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(jwt_adapter_module, "JWKSClient", StaticJWKSClient)
        monkeypatch.setattr("mozaiksai.hosts.runtime.get_mongo_client", lambda: fake_mongo)
        monkeypatch.setattr("mozaiksai.core.startup.validation.get_mongo_client", lambda: fake_mongo)

        from mozaiksai.hosts import platform

        _prepare_platform_test_runtime(platform, monkeypatch)
        monkeypatch.delenv("AUTH_AUDIENCE", raising=False)
        reset_auth_adapter()
        with pytest.raises(AuthError, match="AUTH_AUDIENCE"):
            asyncio.run(platform._platform_startup())

        monkeypatch.setenv("AUTH_AUDIENCE", audience)
        reset_auth_adapter()
        with TestClient(platform.app, raise_server_exceptions=False) as client:
            health = client.get("/health")
            assert health.status_code == 200, health.text

            route = "/api/modules/orders/create_order"
            body = {"params": {"customer_name": "Ada"}}
            missing = client.post(route, json=body)
            assert missing.status_code == 401, missing.text
            assert missing.json()["detail"] == "Missing authorization token"

            accepted = client.post(route, json=body, headers={"Authorization": f"Bearer {token()}"})
            assert accepted.status_code == 200, accepted.text
            assert accepted.json() == {"order": {"customer_name": "Ada"}}

            for rejected_token, reason in (
                (token(aud="some-other-api"), "Invalid token audience"),
                (token(aud=None), "Token missing required claim: aud"),
                (token(typ="JWT"), "Token is not an access token"),
            ):
                rejected = client.post(
                    route, json=body, headers={"Authorization": f"Bearer {rejected_token}"},
                )
                assert rejected.status_code == 401, rejected.text
                assert rejected.json()["detail"] == reason

            cross_user = client.post(
                f"{route}?user_id=bob", json=body,
                headers={"Authorization": f"Bearer {token()}"},
            )
            assert cross_user.status_code == 403, cross_user.text
            assert cross_user.json()["detail"] == "Token user_id does not match request user_id"


def test_generated_app_jwt_auth_enforces_audience_and_access_token_type(tmp_path: Path) -> None:
    env = {
        name: value for name, value in os.environ.items()
        if name not in {"REDIS_URL", "DATABASE_URL", "MOZAIKS_WORKFLOWS_PATH"}
        and not name.startswith(("AUTH_", "KEYCLOAK_", "SUPABASE_", "MOZAIKS_OIDC_", "MONGO_", "MONGODB_"))
    }
    env.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(ROOT),
        "JWT_ACCEPTANCE_APP_ROOT": str(tmp_path / "app"),
        "PLATFORM_PATH": str(tmp_path / "app"),
        "ENV": "test",
        "AUTH_ENABLED": "true",
        "AUTH_PROVIDER": "jwt",
        "AUTH_ISSUER": "https://issuer.example.invalid",
        "AUTH_JWKS_URL": "https://issuer.example.invalid/jwks",
        "RATE_LIMIT_ENABLED": "false",
        "MOZAIKS_DATABASE_STARTUP_POLICY": "best_effort",
    })
    completed = subprocess.run(
        [sys.executable, "-B", "-c", "from tests.test_generated_app_jwt_auth import _run_probe; _run_probe()"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180, check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
