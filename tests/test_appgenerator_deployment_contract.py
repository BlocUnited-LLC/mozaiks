from __future__ import annotations

import json
from pathlib import Path

from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    build_deploy_target_spec,
    build_deployment_template_manifest,
    generate_deployment_artifacts,
    validate_ci_secret_requirements,
    validate_deployment_build_output,
    validate_deploy_target_spec,
    validate_deployment_template_manifest,
    validate_generated_deployment_bundle,
)


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_deploy_target_spec_validates_generic_container() -> None:
    spec = build_deploy_target_spec(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )

    errors = validate_deploy_target_spec(spec)
    assert errors == []
    assert spec["target_kind"] == "container"
    assert spec["provider_profile"]["name"] == "generic"
    assert spec["ci_secret_requirements"]["required"][0]["name"] == "REGISTRY_PUSH_TOKEN"


def test_deployment_template_manifest_validates_required_fields() -> None:
    spec = build_deploy_target_spec(
        app_id="demo_app",
        deployment_profile="managed_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=True,
    )
    manifest = build_deployment_template_manifest(
        app_id="demo_app",
        deployment_profile="managed_container",
        deploy_target_spec=spec,
        generated_files={
            "Dockerfile": "FROM python:3.13-slim\n",
            "docker-compose.yml": "version: '3.9'\n",
            ".github/workflows/deploy.yml": "name: deploy\n",
            "env.example": "OPENAI_API_KEY=\n",
            "deployment.manifest.json": "{}\n",
        },
    )

    errors = validate_deployment_template_manifest(manifest)
    assert errors == []
    assert manifest["schema_version"] == "mozaiks.deployment.manifest.v1"
    assert manifest["validation_status"] in {"valid", "invalid"}


def test_generate_artifacts_include_dockerfile_when_requested() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
    )
    artifacts = result["artifacts"]

    assert "Dockerfile" in artifacts
    assert "deployment.manifest.json" in artifacts


def test_generate_artifacts_include_workflow_when_requested() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=False,
        include_workflow=True,
        include_compose=False,
    )
    artifacts = result["artifacts"]

    assert ".github/workflows/deploy.yml" in artifacts


def test_env_example_contains_placeholders_not_secrets() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    env_example = result["artifacts"]["env.example"]

    assert "OPENAI_API_KEY=" in env_example
    assert "MONGO_URI=" in env_example
    assert "OPENAI_API_KEY=sk-" not in env_example
    assert "MONGO_URI=mongodb+srv://" not in env_example


def test_secret_variables_are_listed_but_not_assigned_values() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="local_compose",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=True,
    )
    manifest = result["deployment_manifest"]
    env_example = result["artifacts"]["env.example"]

    assert "OPENAI_API_KEY" in manifest["secret_env"]
    assert "MONGO_URI" in manifest["secret_env"]
    assert "OPENAI_API_KEY=\n" in env_example
    assert "MONGO_URI=\n" in env_example


def test_manifest_accepts_empty_ci_secret_requirements() -> None:
    spec = build_deploy_target_spec(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
    )
    manifest = build_deployment_template_manifest(
        app_id="demo_app",
        deployment_profile="generic_container",
        deploy_target_spec=spec,
        generated_files={
            "Dockerfile": "FROM python:3.13-slim\n",
            "env.example": "OPENAI_API_KEY=\n",
            "deployment.manifest.json": "{}\n",
        },
    )
    manifest["ci_secret_requirements"] = {"required": [], "optional": [], "workflow_inputs": []}

    assert validate_deployment_template_manifest(manifest) == []


def test_manifest_accepts_required_and_optional_ci_secret_names() -> None:
    spec = build_deploy_target_spec(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    manifest = build_deployment_template_manifest(
        app_id="demo_app",
        deployment_profile="generic_container",
        deploy_target_spec=spec,
        generated_files={
            "Dockerfile": "FROM python:3.13-slim\n",
            ".github/workflows/deploy.yml": "name: deploy\n",
            "env.example": "OPENAI_API_KEY=\n",
            "deployment.manifest.json": "{}\n",
        },
    )
    manifest["ci_secret_requirements"] = {
        "required": [{"name": "REGISTRY_PUSH_TOKEN", "purpose": "Registry auth", "used_by": "build_workflow"}],
        "optional": [{"name": "BUILD_CALLBACK_SECRET", "purpose": "Optional callback auth", "used_by": "build_workflow"}],
        "workflow_inputs": [{"name": "deployment_id", "required": True, "purpose": "Deployment correlation id"}],
    }

    assert validate_deployment_template_manifest(manifest) == []


def test_manifest_rejects_secret_looking_ci_secret_values() -> None:
    errors = validate_ci_secret_requirements(
        {
            "required": [
                {
                    "name": "REGISTRY_PUSH_TOKEN",
                    "purpose": "github_pat_abcdefghijklmnopqrstuvwxyz123456",
                    "used_by": "build_workflow",
                }
            ],
            "optional": [],
            "workflow_inputs": [],
        }
    )

    assert any("must not contain raw secret material" in err for err in errors)


def test_manifest_rejects_duplicate_ci_secret_names() -> None:
    errors = validate_ci_secret_requirements(
        {
            "required": [{"name": "REGISTRY_PUSH_TOKEN", "purpose": "A", "used_by": "build_workflow"}],
            "optional": [{"name": "REGISTRY_PUSH_TOKEN", "purpose": "B", "used_by": "build_workflow"}],
            "workflow_inputs": [],
        }
    )

    assert any("must not contain duplicate names" in err for err in errors)


def test_manifest_rejects_invalid_ci_secret_name_format() -> None:
    errors = validate_ci_secret_requirements(
        {
            "required": [{"name": "registry-push-token", "purpose": "Registry auth", "used_by": "build_workflow"}],
            "optional": [],
            "workflow_inputs": [],
        }
    )

    assert any("uppercase identifier" in err for err in errors)


def test_deployment_manifest_has_no_provider_specific_secrets() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="managed_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=True,
    )
    manifest_text = result["artifacts"]["deployment.manifest.json"]

    assert "AZURE_SUBSCRIPTION_ID" not in manifest_text
    assert "GITHUB_TOKEN" not in manifest_text
    assert "ghp_" not in manifest_text


def test_generic_contract_does_not_require_azure_fields() -> None:
    spec = build_deploy_target_spec(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    # Azure-specific fields are intentionally absent from the generic contract.
    assert "azure_subscription_id" not in spec
    assert "azure" not in json.dumps(spec).lower()
    assert validate_deploy_target_spec(spec) == []


def test_generated_artifacts_do_not_contain_github_tokens() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    errors = validate_generated_deployment_bundle(
        result["artifacts"],
        include_dockerfiles=True,
        include_workflow=True,
    )

    assert errors == []


def test_generated_workflow_references_ci_secret_names_without_values() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]

    assert "${{ secrets.REGISTRY_PUSH_TOKEN }}" in workflow
    assert "${{ secrets.BUILD_CALLBACK_SECRET }}" in workflow
    assert "ghp_" not in workflow
    assert "github_pat_" not in workflow
    assert "PRIVATE KEY" not in workflow


def test_generated_workflow_and_manifest_ci_secret_names_are_consistent() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    manifest = result["deployment_manifest"]
    required_names = {entry["name"] for entry in manifest["ci_secret_requirements"]["required"]}
    optional_names = {entry["name"] for entry in manifest["ci_secret_requirements"]["optional"]}

    assert required_names == {"REGISTRY_PUSH_TOKEN"}
    assert optional_names == {"BUILD_CALLBACK_SECRET"}
    for name in required_names | optional_names:
        assert f"${{{{ secrets.{name} }}}}" in workflow


def test_generated_workflow_and_manifest_inputs_are_consistent() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    manifest = result["deployment_manifest"]
    input_names = {entry["name"] for entry in manifest["ci_secret_requirements"]["workflow_inputs"]}

    assert input_names == {"deployment_id", "build_result_id", "build_callback_url"}
    for name in input_names:
        assert f"${{{{ inputs.{name} }}}}" in workflow


def test_e2b_is_documented_as_predeploy_validation_only() -> None:
    source = _read("mozaiksai/core/workflow/generator_support/app_validation_strategy.py")
    assert "pre-deploy build validation" in source
    assert "Not a production hosting runtime" in source


def test_appgenerator_guidance_mentions_provider_neutral_target_profiles() -> None:
    source = _read("factory_app/workflows/AppGenerator/agents.yaml")
    assert "provider-neutral outputs from the deployment contract" in source
    assert "deployment_profile" in source
    assert "ci_secret_requirements" in source


def test_self_host_docker_compose_path_is_documented() -> None:
    source = _read("docs/architecture/deployment/generated-app-deployment-contract.md")
    assert "self-hosting" in source.lower()
    assert "local Docker" in source
    assert "local Compose" in source
    assert "ci_secret_requirements" in source


def test_build_output_accepts_pending_without_image_ref() -> None:
    payload = {
        "repo_url": "https://repo.example.invalid/demo-app",
        "commit_sha": "abc123",
        "build_status": "pending",
        "image_ref": None,
        "artifact_digest": None,
    }
    assert validate_deployment_build_output(payload) == []


def test_build_output_accepts_running_without_image_ref() -> None:
    payload = {
        "repo_url": "https://repo.example.invalid/demo-app",
        "commit_sha": "abc123",
        "build_status": "running",
        "image_ref": None,
        "artifact_digest": None,
    }
    assert validate_deployment_build_output(payload) == []


def test_build_output_rejects_succeeded_without_image_ref() -> None:
    payload = {
        "repo_url": "https://repo.example.invalid/demo-app",
        "commit_sha": "abc123",
        "build_status": "succeeded",
        "image_ref": None,
        "artifact_digest": "sha256:" + "a" * 64,
    }
    errors = validate_deployment_build_output(payload)
    assert "image_ref is required when build_status is succeeded" in errors


def test_build_output_accepts_succeeded_with_image_ref() -> None:
    payload = {
        "repo_url": "https://repo.example.invalid/demo-app",
        "commit_sha": "abc123",
        "build_status": "succeeded",
        "image_ref": "registry.example.invalid/demo-app:abc123",
        "artifact_digest": "sha256:" + "a" * 64,
    }
    assert validate_deployment_build_output(payload) == []


def test_build_output_validates_sha256_artifact_digest() -> None:
    payload = {
        "repo_url": "https://repo.example.invalid/demo-app",
        "commit_sha": "abc123",
        "build_status": "failed",
        "image_ref": None,
        "artifact_digest": "md5:bad",
    }
    errors = validate_deployment_build_output(payload)
    assert "artifact_digest must use sha256: prefix when present" in errors


def test_build_output_rejects_secret_shaped_safe_details_keys() -> None:
    payload = {
        "repo_url": "https://repo.example.invalid/demo-app",
        "commit_sha": "abc123",
        "build_status": "failed",
        "image_ref": None,
        "artifact_digest": None,
        "safe_details": {"access_token": "env://TOKEN_REF"},
    }
    errors = validate_deployment_build_output(payload)
    assert any("safe_details must not contain secret-shaped keys or values" in err for err in errors)


def test_build_output_rejects_secret_looking_values_in_safe_details() -> None:
    payload = {
        "repo_url": "https://repo.example.invalid/demo-app",
        "commit_sha": "abc123",
        "build_status": "failed",
        "image_ref": None,
        "artifact_digest": None,
        "safe_details": {"note": "ghp_abcdefghijklmnopqrstuvwxyz123456"},
    }
    errors = validate_deployment_build_output(payload)
    assert any("safe_details must not contain secret-shaped keys or values" in err for err in errors)


def test_build_output_does_not_require_provider_specific_fields() -> None:
    payload = {
        "build_status": "pending",
        "repo_url": None,
        "commit_sha": None,
        "image_ref": None,
        "artifact_digest": None,
        "workflow_run_url": None,
        "logs_url": None,
        "safe_details": None,
        "error_code": None,
    }
    assert validate_deployment_build_output(payload) == []


def test_manifest_can_reference_optional_build_output_contract() -> None:
    manifest = build_deployment_template_manifest(
        app_id="demo_app",
        deployment_profile="generic_container",
        deploy_target_spec=build_deploy_target_spec(app_id="demo_app"),
        generated_files={
            "Dockerfile": "FROM python:3.13-slim\n",
            "env.example": "OPENAI_API_KEY=\nMONGO_URI=\n",
            "deployment.manifest.json": "{}\n",
        },
    )
    manifest["build_output_contract"] = {
        "repo_url": "https://repo.example.invalid/demo-app",
        "commit_sha": "abc123",
        "build_status": "succeeded",
        "image_ref": "registry.example.invalid/demo-app:abc123",
        "artifact_digest": "sha256:" + "a" * 64,
        "workflow_run_url": "https://repo.example.invalid/demo-app/actions/runs/123",
        "logs_url": "https://repo.example.invalid/demo-app/builds/123/logs",
        "safe_details": {"run_id": "123"},
        "error_code": None,
    }

    errors = validate_deployment_template_manifest(manifest)
    assert errors == []

