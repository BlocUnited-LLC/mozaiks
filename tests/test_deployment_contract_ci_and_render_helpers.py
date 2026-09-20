"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/deployment_contract.py

Covers the CI secret requirements helpers and render helpers:

  _empty_ci_secret_requirements:
    - returns dict with required, optional, workflow_inputs as empty lists

  _default_ci_secret_requirements:
    - include_workflow=False → empty structure
    - include_workflow=True → contains REGISTRY_PUSH_TOKEN and workflow inputs

  _normalize_ci_secret_requirements:
    - None → empty structure
    - non-dict → empty structure
    - valid required/optional items normalized
    - non-dict items in bucket skipped
    - name/purpose/used_by stripped
    - empty purpose → None
    - empty used_by → None
    - workflow_inputs normalized (name, required bool, purpose)
    - non-list workflow_inputs ignored
    - non-dict workflow input items skipped

  _render_env_example:
    - required variables appear in output
    - secret required variable has empty value
    - non-secret required variable has <required> value
    - optional variables section included when present
    - public variables section included when present
    - empty spec → minimal output

  _render_dockerfile:
    - default port (8000) in EXPOSE line
    - custom port from spec in EXPOSE line
    - static boilerplate lines present
    - no spec → default port

  _render_compose:
    - default port (8000) in ports line
    - custom port from spec in ports line
    - static boilerplate present (services, env_file, build context)
"""

from __future__ import annotations

import json
from pathlib import Path

from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    DEFAULT_RUNTIME_PORT,
    _default_ci_secret_requirements,
    _empty_ci_secret_requirements,
    _normalize_ci_secret_requirements,
    _render_compose,
    _render_dockerfile,
    _render_env_example,
)


# ---------------------------------------------------------------------------
# 1. _empty_ci_secret_requirements
# ---------------------------------------------------------------------------
class TestEmptyCiSecretRequirements:
    def test_returns_dict(self):
        result = _empty_ci_secret_requirements()
        assert isinstance(result, dict)

    def test_required_is_empty_list(self):
        assert _empty_ci_secret_requirements()["required"] == []

    def test_optional_is_empty_list(self):
        assert _empty_ci_secret_requirements()["optional"] == []

    def test_workflow_inputs_is_empty_list(self):
        assert _empty_ci_secret_requirements()["workflow_inputs"] == []

    def test_has_exactly_three_keys(self):
        result = _empty_ci_secret_requirements()
        assert set(result.keys()) == {"required", "optional", "workflow_inputs"}


# ---------------------------------------------------------------------------
# 2. _default_ci_secret_requirements
# ---------------------------------------------------------------------------
class TestDefaultCiSecretRequirements:
    def test_include_workflow_false_returns_empty(self):
        result = _default_ci_secret_requirements(include_workflow=False)
        assert result == {"required": [], "optional": [], "workflow_inputs": []}

    def test_include_workflow_true_has_required_token(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        names = [item["name"] for item in result["required"]]
        assert "REGISTRY_PUSH_TOKEN" in names

    def test_include_workflow_true_has_optional_callback_secret(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        names = [item["name"] for item in result["optional"]]
        assert "BUILD_CALLBACK_SECRET" in names

    def test_include_workflow_true_has_deployment_id_input(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        input_names = [item["name"] for item in result["workflow_inputs"]]
        assert "deployment_id" in input_names

    def test_include_workflow_true_has_build_result_id_input(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        input_names = [item["name"] for item in result["workflow_inputs"]]
        assert "build_result_id" in input_names

    def test_include_workflow_true_has_build_callback_url_input(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        input_names = [item["name"] for item in result["workflow_inputs"]]
        assert "build_callback_url" in input_names

    def test_include_workflow_true_deployment_id_is_required(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        deployment_id = next(
            item for item in result["workflow_inputs"] if item["name"] == "deployment_id"
        )
        assert deployment_id["required"] is True

    def test_include_workflow_true_callback_url_not_required(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        callback = next(
            item for item in result["workflow_inputs"] if item["name"] == "build_callback_url"
        )
        assert callback["required"] is False

    def test_include_workflow_true_has_app_url_input(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        input_names = [item["name"] for item in result["workflow_inputs"]]
        assert "app_url" in input_names

    def test_include_workflow_true_app_url_not_required(self):
        result = _default_ci_secret_requirements(include_workflow=True)
        app_url = next(item for item in result["workflow_inputs"] if item["name"] == "app_url")
        assert app_url["required"] is False


# ---------------------------------------------------------------------------
# 3. _normalize_ci_secret_requirements
# ---------------------------------------------------------------------------
class TestNormalizeCiSecretRequirements:
    def test_none_returns_empty(self):
        result = _normalize_ci_secret_requirements(None)
        assert result == {"required": [], "optional": [], "workflow_inputs": []}

    def test_non_dict_returns_empty(self):
        result = _normalize_ci_secret_requirements("not-a-dict")
        assert result == {"required": [], "optional": [], "workflow_inputs": []}

    def test_empty_dict_returns_empty(self):
        result = _normalize_ci_secret_requirements({})
        assert result == {"required": [], "optional": [], "workflow_inputs": []}

    def test_required_item_normalized(self):
        value = {
            "required": [{"name": "MY_SECRET", "purpose": "Used for auth", "used_by": "deploy"}]
        }
        result = _normalize_ci_secret_requirements(value)
        assert len(result["required"]) == 1
        item = result["required"][0]
        assert item["name"] == "MY_SECRET"
        assert item["purpose"] == "Used for auth"
        assert item["used_by"] == "deploy"

    def test_optional_item_normalized(self):
        value = {"optional": [{"name": "EXTRA_TOKEN"}]}
        result = _normalize_ci_secret_requirements(value)
        assert len(result["optional"]) == 1
        assert result["optional"][0]["name"] == "EXTRA_TOKEN"

    def test_name_stripped(self):
        value = {"required": [{"name": "  MY_SECRET  "}]}
        result = _normalize_ci_secret_requirements(value)
        assert result["required"][0]["name"] == "MY_SECRET"

    def test_empty_purpose_becomes_none(self):
        value = {"required": [{"name": "MY_SECRET", "purpose": "  "}]}
        result = _normalize_ci_secret_requirements(value)
        assert result["required"][0]["purpose"] is None

    def test_empty_used_by_becomes_none(self):
        value = {"required": [{"name": "MY_SECRET", "used_by": ""}]}
        result = _normalize_ci_secret_requirements(value)
        assert result["required"][0]["used_by"] is None

    def test_non_dict_item_in_bucket_skipped(self):
        value = {"required": ["not-a-dict", {"name": "VALID"}]}
        result = _normalize_ci_secret_requirements(value)
        assert len(result["required"]) == 1
        assert result["required"][0]["name"] == "VALID"

    def test_non_list_bucket_keeps_empty(self):
        # If required is not a list, it should stay as []
        value = {"required": "not-a-list"}
        result = _normalize_ci_secret_requirements(value)
        assert result["required"] == []

    def test_workflow_inputs_normalized(self):
        value = {
            "workflow_inputs": [
                {"name": "deployment_id", "required": True, "purpose": "Correlation ID"}
            ]
        }
        result = _normalize_ci_secret_requirements(value)
        assert len(result["workflow_inputs"]) == 1
        item = result["workflow_inputs"][0]
        assert item["name"] == "deployment_id"
        assert item["required"] is True
        assert item["purpose"] == "Correlation ID"

    def test_workflow_input_required_falsy_becomes_false(self):
        value = {"workflow_inputs": [{"name": "callback_url", "required": False}]}
        result = _normalize_ci_secret_requirements(value)
        assert result["workflow_inputs"][0]["required"] is False

    def test_workflow_input_required_missing_becomes_false(self):
        value = {"workflow_inputs": [{"name": "optional_input"}]}
        result = _normalize_ci_secret_requirements(value)
        assert result["workflow_inputs"][0]["required"] is False

    def test_workflow_input_non_list_ignored(self):
        value = {"workflow_inputs": "not-a-list"}
        result = _normalize_ci_secret_requirements(value)
        # workflow_inputs stays as [] when the value is not a list
        assert result["workflow_inputs"] == []

    def test_workflow_input_non_dict_item_skipped(self):
        value = {"workflow_inputs": ["bad", {"name": "good"}]}
        result = _normalize_ci_secret_requirements(value)
        assert len(result["workflow_inputs"]) == 1
        assert result["workflow_inputs"][0]["name"] == "good"

    def test_multiple_required_and_optional_items(self):
        value = {
            "required": [{"name": "A"}, {"name": "B"}],
            "optional": [{"name": "C"}],
        }
        result = _normalize_ci_secret_requirements(value)
        assert len(result["required"]) == 2
        assert len(result["optional"]) == 1


# ---------------------------------------------------------------------------
# 4. _render_env_example
# ---------------------------------------------------------------------------
class TestRenderEnvExample:
    def test_empty_spec_produces_header(self):
        result = _render_env_example({})
        assert "# Generated by Mozaiks" in result

    def test_required_non_secret_has_placeholder(self):
        spec = {"environment": {"required_variables": ["APP_HOST"]}}
        result = _render_env_example(spec)
        assert "APP_HOST=<required>" in result

    def test_required_secret_variable_has_empty_value(self):
        spec = {
            "environment": {
                "required_variables": ["DB_PASSWORD"],
                "secret_variables": ["DB_PASSWORD"],
            }
        }
        result = _render_env_example(spec)
        # Secret required variable → key= (no placeholder value)
        assert "DB_PASSWORD=" in result
        assert "DB_PASSWORD=<required>" not in result

    def test_optional_section_present_when_optional_vars(self):
        spec = {"environment": {"optional_variables": ["DEBUG"]}}
        result = _render_env_example(spec)
        assert "# Optional variables" in result
        assert "DEBUG=" in result

    def test_no_optional_section_when_empty(self):
        spec = {"environment": {"optional_variables": []}}
        result = _render_env_example(spec)
        assert "# Optional variables" not in result

    def test_public_section_present(self):
        spec = {"environment": {"public_variables": ["VITE_APP_NAME"]}}
        result = _render_env_example(spec)
        assert "# Public client variables" in result
        assert "VITE_APP_NAME=" in result

    def test_result_ends_with_newline(self):
        result = _render_env_example({})
        assert result.endswith("\n")

    def test_no_real_secret_comment(self):
        result = _render_env_example({})
        assert "Do not commit real secrets" in result


# ---------------------------------------------------------------------------
# 5. _render_dockerfile
# ---------------------------------------------------------------------------
class TestRenderDockerfile:
    def test_default_port_in_expose(self):
        result = _render_dockerfile({})
        assert f"EXPOSE {DEFAULT_RUNTIME_PORT}" in result

    def test_custom_port_in_expose(self):
        spec = {"runtime": {"container_port": 9000}}
        result = _render_dockerfile(spec)
        assert "EXPOSE 9000" in result

    def test_from_python_line(self):
        result = _render_dockerfile({})
        assert "FROM python:" in result

    def test_workdir_line(self):
        result = _render_dockerfile({})
        assert "WORKDIR /app" in result

    def test_copy_requirements_line(self):
        result = _render_dockerfile({})
        assert "COPY requirements.txt" in result

    def test_pip_install_line(self):
        result = _render_dockerfile({})
        assert "pip install" in result

    def test_cmd_line(self):
        result = _render_dockerfile({})
        command = json.loads(
            next(
                line.removeprefix("CMD ") for line in result.splitlines() if line.startswith("CMD ")
            )
        )
        assert command == [
            "mozaiks",
            "serve",
            ".",
            "--host",
            "platform",
            "--listen",
            "0.0.0.0",
            "--port",
            str(DEFAULT_RUNTIME_PORT),
        ]

        pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        assert 'mozaiks = "mozaiks_cli.main:main"' in pyproject

    def test_custom_port_in_start_command(self):
        result = _render_dockerfile({"runtime": {"container_port": 9000}})
        command = json.loads(
            next(
                line.removeprefix("CMD ") for line in result.splitlines() if line.startswith("CMD ")
            )
        )
        assert command[-2:] == ["--port", "9000"]

    def test_runtime_none_uses_default_port(self):
        spec = {"runtime": None}
        result = _render_dockerfile(spec)
        assert f"EXPOSE {DEFAULT_RUNTIME_PORT}" in result

    def test_non_dict_runtime_uses_default_port(self):
        spec = {"runtime": "invalid"}
        result = _render_dockerfile(spec)
        assert f"EXPOSE {DEFAULT_RUNTIME_PORT}" in result


# ---------------------------------------------------------------------------
# 6. _render_compose
# ---------------------------------------------------------------------------
class TestRenderCompose:
    def test_default_port_in_ports_line(self):
        result = _render_compose({})
        assert f'"{DEFAULT_RUNTIME_PORT}:{DEFAULT_RUNTIME_PORT}"' in result

    def test_custom_port_in_ports_line(self):
        spec = {"runtime": {"container_port": 9000}}
        result = _render_compose(spec)
        assert '"9000:9000"' in result

    def test_services_key_present(self):
        result = _render_compose({})
        assert "services:" in result

    def test_env_file_present(self):
        result = _render_compose({})
        assert "env_file:" in result

    def test_build_context_present(self):
        result = _render_compose({})
        assert "context: ." in result

    def test_dockerfile_reference_present(self):
        result = _render_compose({})
        assert "dockerfile: Dockerfile" in result

    def test_runtime_none_uses_default_port(self):
        spec = {"runtime": None}
        result = _render_compose(spec)
        assert f'"{DEFAULT_RUNTIME_PORT}:{DEFAULT_RUNTIME_PORT}"' in result

    def test_result_ends_with_newline(self):
        result = _render_compose({})
        assert result.endswith("\n")
