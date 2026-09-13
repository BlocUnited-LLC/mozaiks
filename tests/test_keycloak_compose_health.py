from pathlib import Path

import pytest
import yaml


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
