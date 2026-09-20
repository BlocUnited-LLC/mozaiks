"""Only the canonical names-only secret manifest may cross task path admission."""

import pytest

from mozaiksai.core.runtime.app.paths import APP_SECURITY_SECRETS_PATH
from mozaiksai.core.workflow.path_ownership import normalize_owned_path, normalize_owned_paths


@pytest.mark.parametrize("path", ["security/secrets.yaml", "security\\secrets.yaml", "./security/secrets.yaml"])
def test_canonical_secret_contract_has_task_ownership(path):
    assert normalize_owned_path(path) == APP_SECURITY_SECRETS_PATH
    assert normalize_owned_paths([path]) == (APP_SECURITY_SECRETS_PATH,)


@pytest.mark.parametrize("path", [
    "Security/secrets.yaml", "security/Secrets.yaml", "security/secrets.YAML",
    "security/secrets.yaml.bak", "security/secrets.yaml/child", "security/secrets.yml",
    "app/security/secrets.yaml", ".env", ".env.local", "security/vault.yaml",
    "security/credentials.json", "keys/server.pem", "security/secret.txt",
    "../security/secrets.yaml", "security/../security/secrets.yaml",
    "/security/secrets.yaml", "C:\\app\\security\\secrets.yaml", "security/*.yaml",
])
def test_secret_contract_allowance_does_not_relax_other_owned_path_rejections(path):
    with pytest.raises(ValueError):
        normalize_owned_path(path)
