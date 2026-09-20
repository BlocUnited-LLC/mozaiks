from __future__ import annotations

import pytest

from mozaiksai.core.environment import EnvironmentConfigError, resolve_environment


def test_prospective_configuration_uses_the_same_deployed_policy_without_mutation(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    settings = {"ENV": " ", "ENVIRONMENT": " staging-eu "}
    resolved = resolve_environment(environ=settings)
    assert resolved.name == "staging-eu"
    assert resolved.is_deployed is True
    assert resolved.permits_no_auth is False
    assert resolve_environment().name == "test"
    assert settings == {"ENV": " ", "ENVIRONMENT": " staging-eu "}


def test_explicit_empty_configuration_does_not_read_ambient_environment(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    assert resolve_environment(environ={}).classification == "absent"


def test_prospective_configuration_rejects_conflicting_authority():
    with pytest.raises(EnvironmentConfigError, match="Conflicting"):
        resolve_environment(environ={"ENV": "test", "ENVIRONMENT": "prod"})
