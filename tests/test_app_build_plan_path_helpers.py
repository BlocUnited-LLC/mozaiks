"""
AppGenerator app_build_plan.py path helper unit tests.

Covers:
  _raw_frontend_source_path:
    - owned_paths with .jsx → returned
    - owned_paths with .tsx → returned
    - owned_paths with .css/.scss/.less/.sass/.html → returned
    - .js in /frontend/ segment → returned
    - .ts in /src/components/ segment → returned
    - .js not in frontend segment → None
    - .py file → None
    - no owned_paths → None
    - empty owned_paths → None

  _normalized_owned_paths:
    - valid paths → normalized (backslash → forward slash, stripped)
    - empty paths → excluded
    - normalize_app_path result: whitespace paths → excluded
    - paths with backslashes → normalized

  _noncanonical_service_paths:
    - canonical services/config.py → not returned
    - canonical services/admin_config.py → not returned
    - services/integrations/* → not returned
    - services/adapters/* → not returned
    - services/routes/* → not returned
    - services/custom_thing.py → returned as noncanonical
    - non-services paths → not returned (no services/ prefix)
    - duplicates → deduplicated

  _invalid_service_foundation_paths:
    - services/config.py → allowed
    - security/secrets.yaml → allowed
    - services/integrations/* → allowed
    - services/adapters/* → allowed
    - services/routes/* → allowed
    - modules/tasks/backend/handler.py → invalid
    - services/random_helper.py → invalid
    - duplicates → deduplicated

  _deployment_contract_artifact_paths:
    - Dockerfile → returned
    - deployment.manifest.json → returned
    - .github/workflows/readiness.yml → returned
    - .github/workflows/deploy.yml → returned
    - app service files → ignored
"""
from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _deployment_contract_artifact_paths,
    _invalid_service_foundation_paths,
    _noncanonical_service_paths,
    _normalized_owned_paths,
    _raw_frontend_source_path,
    _validate_build_tasks,
)

# ---------------------------------------------------------------------------
# 1. _raw_frontend_source_path
# ---------------------------------------------------------------------------

class TestRawFrontendSourcePath:
    def _task(self, paths):
        return {"owned_paths": paths}

    def test_jsx_file_returned(self):
        result = _raw_frontend_source_path(self._task(["app/ui/components/Button.jsx"]))
        assert result == "app/ui/components/Button.jsx"

    def test_tsx_file_returned(self):
        result = _raw_frontend_source_path(self._task(["app/ui/pages/Home.tsx"]))
        assert result == "app/ui/pages/Home.tsx"

    def test_css_file_returned(self):
        result = _raw_frontend_source_path(self._task(["app/ui/styles/main.css"]))
        assert result == "app/ui/styles/main.css"

    def test_scss_file_returned(self):
        result = _raw_frontend_source_path(self._task(["app/ui/styles/main.scss"]))
        assert result == "app/ui/styles/main.scss"

    def test_less_file_returned(self):
        result = _raw_frontend_source_path(self._task(["app/ui/styles/main.less"]))
        assert result == "app/ui/styles/main.less"

    def test_sass_file_returned(self):
        result = _raw_frontend_source_path(self._task(["app/ui/styles/main.sass"]))
        assert result == "app/ui/styles/main.sass"

    def test_html_file_returned(self):
        result = _raw_frontend_source_path(self._task(["app/ui/templates/index.html"]))
        assert result == "app/ui/templates/index.html"

    def test_js_in_frontend_segment_returned(self):
        result = _raw_frontend_source_path(self._task(["app/frontend/script.js"]))
        assert result == "app/frontend/script.js"

    def test_ts_in_src_components_returned(self):
        result = _raw_frontend_source_path(self._task(["app/src/components/Button.ts"]))
        assert result == "app/src/components/Button.ts"

    def test_js_not_in_frontend_segment_returns_none(self):
        result = _raw_frontend_source_path(self._task(["services/config.js"]))
        assert result is None

    def test_py_file_returns_none(self):
        result = _raw_frontend_source_path(self._task(["modules/tasks/backend/handler.py"]))
        assert result is None

    def test_no_owned_paths_returns_none(self):
        result = _raw_frontend_source_path({})
        assert result is None

    def test_empty_owned_paths_returns_none(self):
        result = _raw_frontend_source_path(self._task([]))
        assert result is None

    def test_first_matching_path_returned(self):
        paths = ["modules/tasks/backend/handler.py", "app/ui/components/Form.tsx"]
        result = _raw_frontend_source_path(self._task(paths))
        assert result == "app/ui/components/Form.tsx"


# ---------------------------------------------------------------------------
# 2. _normalized_owned_paths
# ---------------------------------------------------------------------------

class TestNormalizedOwnedPaths:
    def _task(self, paths):
        return {"owned_paths": paths}

    def test_valid_path_returned(self):
        result = _normalized_owned_paths(self._task(["modules/tasks/backend/handler.py"]))
        assert "modules/tasks/backend/handler.py" in result

    def test_backslash_normalized(self):
        result = _normalized_owned_paths(self._task(["modules\\tasks\\backend\\handler.py"]))
        assert any("modules/tasks/backend/handler.py" in p for p in result)

    def test_empty_paths_excluded(self):
        result = _normalized_owned_paths(self._task(["", "  ", "modules/tasks/backend/handler.py"]))
        empty_count = sum(1 for p in result if not p.strip())
        assert empty_count == 0

    def test_no_owned_paths_key_returns_empty(self):
        result = _normalized_owned_paths({})
        assert result == []

    def test_multiple_paths_all_returned(self):
        paths = ["modules/tasks/backend/handler.py", "services/config.py"]
        result = _normalized_owned_paths(self._task(paths))
        assert len(result) == 2

    def test_whitespace_only_paths_excluded(self):
        result = _normalized_owned_paths(self._task(["   "]))
        assert result == []


# ---------------------------------------------------------------------------
# 3. _noncanonical_service_paths
# ---------------------------------------------------------------------------

class TestNoncanonicalServicePaths:
    def test_canonical_config_py_allowed(self):
        result = _noncanonical_service_paths(["services/config.py"])
        assert "services/config.py" not in result

    def test_canonical_admin_config_py_allowed(self):
        result = _noncanonical_service_paths(["services/admin_config.py"])
        assert "services/admin_config.py" not in result

    def test_integrations_prefix_allowed(self):
        result = _noncanonical_service_paths(["services/integrations/payment_provider_client.py"])
        assert result == []

    def test_adapters_prefix_allowed(self):
        result = _noncanonical_service_paths(["services/adapters/auth/keycloak.py"])
        assert result == []

    def test_routes_prefix_allowed(self):
        result = _noncanonical_service_paths(["services/routes/admin.py"])
        assert result == []

    def test_noncanonical_service_file_returned(self):
        result = _noncanonical_service_paths(["services/custom_helper.py"])
        assert "services/custom_helper.py" in result

    def test_non_services_path_not_returned(self):
        result = _noncanonical_service_paths(["modules/tasks/backend/handler.py"])
        assert result == []

    def test_duplicates_deduplicated(self):
        paths = ["services/custom.py", "services/custom.py"]
        result = _noncanonical_service_paths(paths)
        assert result.count("services/custom.py") == 1

    def test_returned_list_is_sorted(self):
        paths = ["services/z_custom.py", "services/a_custom.py"]
        result = _noncanonical_service_paths(paths)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# 4. _invalid_service_foundation_paths
# ---------------------------------------------------------------------------

class TestInvalidServiceFoundationPaths:
    def test_services_config_py_allowed(self):
        result = _invalid_service_foundation_paths(["services/config.py"])
        assert result == []

    def test_security_secrets_yaml_allowed(self):
        result = _invalid_service_foundation_paths(["security/secrets.yaml"])
        assert result == []

    def test_integrations_prefix_allowed(self):
        result = _invalid_service_foundation_paths(["services/integrations/payment_provider_client.py"])
        assert result == []

    def test_adapters_prefix_allowed(self):
        result = _invalid_service_foundation_paths(["services/adapters/auth/keycloak.py"])
        assert result == []

    def test_routes_prefix_allowed(self):
        result = _invalid_service_foundation_paths(["services/routes/admin.py"])
        assert result == []

    def test_module_path_is_invalid(self):
        result = _invalid_service_foundation_paths(["modules/tasks/backend/handler.py"])
        assert "modules/tasks/backend/handler.py" in result

    def test_random_services_file_is_invalid(self):
        result = _invalid_service_foundation_paths(["services/random_helper.py"])
        assert "services/random_helper.py" in result

    def test_duplicates_deduplicated(self):
        paths = ["modules/tasks/handler.py", "modules/tasks/handler.py"]
        result = _invalid_service_foundation_paths(paths)
        assert result.count("modules/tasks/handler.py") == 1

    def test_returned_list_is_sorted(self):
        paths = ["z_invalid.py", "a_invalid.py"]
        result = _invalid_service_foundation_paths(paths)
        assert result == sorted(result)

    def test_empty_list_returns_empty(self):
        assert _invalid_service_foundation_paths([]) == []


# ---------------------------------------------------------------------------
# 5. _deployment_contract_artifact_paths
# ---------------------------------------------------------------------------

class TestDeploymentContractArtifactPaths:
    def test_deployment_root_files_returned(self):
        result = _deployment_contract_artifact_paths([
            "Dockerfile",
            "deployment.manifest.json",
            "docker-compose.yml",
            "env.example",
        ])
        assert result == [
            "Dockerfile",
            "deployment.manifest.json",
            "docker-compose.yml",
            "env.example",
        ]

    def test_github_workflow_returned(self):
        result = _deployment_contract_artifact_paths(
            [".github/workflows/readiness.yml", ".github/workflows/deploy.yml"]
        )
        assert result == [".github/workflows/deploy.yml", ".github/workflows/readiness.yml"]

    def test_app_service_file_ignored(self):
        result = _deployment_contract_artifact_paths(["services/adapters/dns/provider.py"])
        assert result == []

    def test_build_tasks_cannot_own_deployment_artifacts(self):
        task = {
            "task_id": "deploy_scaffold",
            "task_type": "service_foundation",
            "initial_agent": "ConfigMiddlewareAgent",
            "capability_pack_id": None,
            "owned_paths": ["Dockerfile", "deployment.manifest.json"],
        }

        with pytest.raises(ValueError, match="deployment contract artifact"):
            _validate_build_tasks([task])
