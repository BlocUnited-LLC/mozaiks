from __future__ import annotations

import json
from pathlib import Path

import pytest

from mozaiksai.core.validation import validate_app_workspace
from mozaiksai.resources import resolve_factory_app_root


def test_factory_app_uses_shared_workspace_contracts():
    factory = resolve_factory_app_root()
    assert factory is not None
    assert validate_app_workspace(factory / "app") == []


def test_independent_workspace_checks_registered_composition_and_action_closure(tmp_path):
    app = tmp_path / "app"
    (app / "ui").mkdir(parents=True)
    (app / "app.json").write_text('{"appName": "Independent"}')
    (app / "ui" / "route_manifest.json").write_text(json.dumps({
        "pages": [{"path": "/", "component": "SharedPage"}],
    }))
    (app / "ui" / "index.js").write_text('moduleAction("absent", "read");')
    inherited = tmp_path / "shared-ui"
    inherited.mkdir()
    (inherited / "index.js").write_text("registerComponent('SharedPage', SharedPage);")
    issues = validate_app_workspace(app)
    assert {issue.code for issue in issues} == {"MISSING_ROUTE_COMPONENT", "MISSING_MODULE_ACTION"}
    issues = validate_app_workspace(app, inherited_ui_roots=(inherited,))
    assert {issue.code for issue in issues} == {"MISSING_MODULE_ACTION"}
    (app / "ui" / "index.js").write_text("")
    assert validate_app_workspace(app, inherited_ui_roots=(inherited,)) == []


def test_independent_workspace_secret_policy_is_validated(tmp_path: Path):
    (tmp_path / "app.json").write_text('{"appName": "Independent"}')
    security = tmp_path / "security"
    security.mkdir()
    (security / "secrets.yaml").write_text("version: 1\nsecrets:\n  - env: SERVICE_KEY\n")
    assert validate_app_workspace(tmp_path) == []
    (security / "secrets.yaml").write_text("version: 1\nsecrets: [SERVICE_KEY]\n")
    assert any(issue.code == "APP_CONTRACT_INVALID" for issue in validate_app_workspace(tmp_path))


@pytest.mark.parametrize("app_name", [123, True, {"invalid": "name"}, ["name"], "   "])
def test_workspace_rejects_malformed_app_name(tmp_path, app_name):
    (tmp_path / "app.json").write_text(json.dumps({"appName": app_name}))
    assert any(issue.code == "INVALID_APP_MANIFEST" for issue in validate_app_workspace(tmp_path))


@pytest.mark.parametrize("relative_path", [
    "config/secrets.yaml", "config/integrations/api_keys.yaml",
    "services/security/resolver.py", "services/data/repository.py",
    "security/resolver.py", "data/repository.py",
])
def test_workspace_rejects_retired_and_misplaced_data_or_secret_files(tmp_path, relative_path):
    (tmp_path / "app.json").write_text('{"appName": "Independent"}')
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_text("sensitive_test_value")
    issues = validate_app_workspace(tmp_path)
    assert any(issue.code == "APP_CONTRACT_INVALID" and relative_path in issue.message for issue in issues)
    assert "sensitive_test_value" not in str(issues)


def test_workspace_allows_authored_host_and_declarative_policy_paths(tmp_path):
    (tmp_path / "app.json").write_text('{"appName": "Independent"}')
    (tmp_path / "host.py").write_text("APP_HOST = True\n")
    (tmp_path / "data/migrations").mkdir(parents=True)
    (tmp_path / "data/migrations/additive.json").write_text("{}")
    (tmp_path / "security").mkdir()
    (tmp_path / "security/operations.yaml").write_text("schema_version: example.operations.v1\noperations: []\n")
    assert validate_app_workspace(tmp_path) == []
