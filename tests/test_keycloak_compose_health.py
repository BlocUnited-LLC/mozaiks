import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_keycloak_health_matches_enabled_management_interface(name):
    path = Path(__file__).resolve().parents[1] / "infra" / "compose" / name
    service = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]["keycloak"]
    assert service["environment"]["KC_HEALTH_ENABLED"] == "true"
    assert service["environment"]["KC_HTTP_MANAGEMENT_PORT"] == "9000"
    assert "--optimized" not in service["command"]
    probe = service["healthcheck"]["test"][1]
    assert "/dev/tcp/127.0.0.1/9000" in probe
    assert "/health/ready" in probe
    assert " 200 " in probe
    assert all("9000" not in str(port) for port in service.get("ports", []))


def test_production_compose_requires_separate_operator_realm():
    compose_dir = REPO_ROOT / "infra" / "compose"
    prod = yaml.safe_load((compose_dir / "docker-compose.prod.yml").read_text(encoding="utf-8"))[
        "services"
    ]
    local = yaml.safe_load((compose_dir / "docker-compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]
    assert any(
        "factory_app/app/brand/realm-export.json" in mount for mount in local["keycloak"]["volumes"]
    )

    def realm_mount(service):
        return next(
            mount
            for mount in service["volumes"]
            if isinstance(mount, dict) and mount["target"].endswith("realm-export.json")
        )

    import_mount = realm_mount(prod["keycloak"])
    check_mount = realm_mount(prod["keycloak-realm-check"])
    assert import_mount["source"] == check_mount["source"]
    assert import_mount["source"].startswith("${MOZAIKS_PROD_REALM_IMPORT_PATH:?")
    assert prod["keycloak-realm-check"]["network_mode"] == "none"
    assert prod["keycloak-realm-check"]["read_only"] is True
    for mount in (import_mount, check_mount):
        assert mount["read_only"] is True
        assert mount["bind"]["create_host_path"] is False
    assert (
        prod["keycloak"]["depends_on"]["keycloak-realm-check"]["condition"]
        == "service_completed_successfully"
    )


def _run_realm_check(tmp_path, realm, *, hostname="login.example.org"):
    path = tmp_path / "realm-export.json"
    path.write_text(json.dumps(realm), encoding="utf-8")
    environment = os.environ.copy()
    environment["KC_HOSTNAME"] = hostname
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_prod_realm_import.py"), str(path)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def production_realm():
    realm = json.loads(
        (REPO_ROOT / "factory_app" / "app" / "brand" / "realm-export.json").read_text(
            encoding="utf-8"
        )
    )
    browser = next(client for client in realm["clients"] if client["clientId"] == "mozaiks-studio")
    browser["redirectUris"] = ["https://studio.example.org/auth/callback"]
    browser["webOrigins"] = ["https://studio.example.org"]
    return realm


def test_prod_realm_check_accepts_exact_https_browser_client(tmp_path, production_realm):
    result = _run_realm_check(tmp_path, production_realm)
    assert result.returncode == 0, result.stderr


def test_prod_realm_check_rejects_packaged_local_browser_client(tmp_path):
    realm = json.loads(
        (REPO_ROOT / "factory_app" / "app" / "brand" / "realm-export.json").read_text(
            encoding="utf-8"
        )
    )
    result = _run_realm_check(tmp_path, realm)
    assert result.returncode == 1
    assert "redirectUris" in result.stderr


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("redirectUris", ["http://studio.example.org/auth/callback"]),
        ("redirectUris", ["https://studio.example.org/*"]),
        ("redirectUris", ["https://127.0.0.1/auth/callback"]),
        ("webOrigins", ["http://localhost:3000"]),
        ("webOrigins", ["https://other.example.org"]),
    ],
)
def test_prod_realm_check_rejects_unsafe_browser_urls(tmp_path, production_realm, field, value):
    production_realm["clients"][0][field] = value
    result = _run_realm_check(tmp_path, production_realm)
    assert result.returncode == 1


def test_prod_realm_check_rejects_local_keycloak_hostname(tmp_path, production_realm):
    result = _run_realm_check(tmp_path, production_realm, hostname="localhost")
    assert result.returncode == 1
    assert "KC_HOSTNAME" in result.stderr


def test_prod_realm_check_requires_enabled_browser_client(tmp_path, production_realm):
    production_realm["clients"][0]["enabled"] = False
    result = _run_realm_check(tmp_path, production_realm)
    assert result.returncode == 1
    assert "browser client" in result.stderr


def test_prod_realm_check_requires_external_tls(tmp_path, production_realm):
    production_realm["sslRequired"] = "none"
    result = _run_realm_check(tmp_path, production_realm)
    assert result.returncode == 1
    assert "TLS" in result.stderr


def test_prod_realm_check_rejects_invalid_json(tmp_path):
    path = tmp_path / "realm-export.json"
    path.write_text("{", encoding="utf-8")
    environment = os.environ.copy()
    environment["KC_HOSTNAME"] = "login.example.org"
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_prod_realm_import.py"), str(path)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "UTF-8 JSON" in result.stderr
