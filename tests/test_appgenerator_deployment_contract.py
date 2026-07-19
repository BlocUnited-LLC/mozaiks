from __future__ import annotations

import asyncio
import json
from pathlib import Path

from factory_app.workflows.AppGenerator.tools.render_infra_scaffold import save_infra_scaffold
from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    _default_ci_secret_requirements,
    _render_workflow,
    build_deploy_target_spec,
    build_deployment_template_manifest,
    generate_deployment_artifacts,
    validate_ci_secret_requirements,
    validate_deploy_target_spec,
    validate_deployment_build_output,
    validate_deployment_template_manifest,
    validate_generated_deployment_bundle,
    validate_readiness_requirements,
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
    assert spec["readiness_requirements"]["checks"][0]["id"] == "runtime_environment"


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
    assert manifest["readiness_requirements"]["checks"]


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
    assert ".github/workflows/readiness.yml" in artifacts


def test_deployment_manifest_links_readiness_workflow_when_emitted() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    manifest = result["deployment_manifest"]

    assert manifest["ci_workflow"] == ".github/workflows/deploy.yml"
    assert manifest["readiness_workflow"] == ".github/workflows/readiness.yml"
    assert ".github/workflows/readiness.yml" in manifest["generated_files"]


def test_generated_readiness_workflow_is_environment_staging_gate() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
        auth_required=True,
    )
    workflow = result["artifacts"][".github/workflows/readiness.yml"]

    assert "environment: staging" in workflow
    assert "Artifact review staging happens inside Mozaiks/Studio" in workflow
    assert "Validate readiness contract" in workflow
    assert "docker build -t generated-app:readiness ." in workflow
    assert "APP_IMAGE_SMOKE_VERIFIED_AT.txt" in workflow
    assert "APP_HEALTHCHECK_VERIFIED_AT.txt" in workflow
    assert "APP_AUTH_SMOKE_VERIFIED_AT" in workflow
    assert "${{ secrets.OPENAI_API_KEY }}" in workflow
    assert "${{ secrets.MONGO_URI }}" in workflow
    assert "AZURE_SUBSCRIPTION_ID" not in workflow
    assert "MOZAIKSPAY" not in workflow


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
    assert "VITE_OIDC_AUTHORITY=" in env_example
    assert "VITE_OIDC_CLIENT_ID=" in env_example
    assert "VITE_OIDC_REDIRECT_URI=" in env_example
    assert "OPENAI_API_KEY=sk-" not in env_example
    assert "MONGO_URI=mongodb+srv://" not in env_example


def test_public_deployment_contract_does_not_require_runtime_auth() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
    )
    manifest = result["deployment_manifest"]

    assert manifest["auth"]["required"] is False
    assert "AUTH_PROVIDER" not in manifest["required_env"]
    assert "AUTH_PROVIDER" not in manifest["deploy_target_spec"]["environment"]["optional_variables"]
    assert "VITE_OIDC_DISCOVERY_URL" in manifest["public_env"]


def test_authenticated_deployment_contract_includes_oidc_runtime_env_and_readiness() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
        auth_required=True,
    )
    manifest = result["deployment_manifest"]
    env_example = result["artifacts"]["env.example"]
    checks = {item["id"]: item for item in manifest["readiness_requirements"]["checks"]}

    assert result["bundle_errors"] == []
    assert manifest["auth"]["required"] is True
    assert manifest["auth"]["provider"] == "jwt"
    assert "AUTH_ENABLED" in manifest["required_env"]
    assert "AUTH_PROVIDER" in manifest["required_env"]
    assert "VITE_OIDC_DISCOVERY_URL" in manifest["public_env"]
    assert "AUTH_ENABLED=" in env_example
    assert "AUTH_PROVIDER=" in env_example
    assert "AUTH_AUDIENCE=" in env_example
    assert "MOZAIKS_OIDC_DISCOVERY_URL=" in env_example
    assert "VITE_OIDC_DISCOVERY_URL=" in env_example
    assert "VITE_OIDC_SCOPE=" in env_example
    assert checks["auth_configuration"]["required_env"] == ["AUTH_ENABLED", "AUTH_PROVIDER"]
    assert "APP_AUTH_SMOKE_VERIFIED_AT" in checks["auth_configuration"]["required_evidence"]


def test_generate_artifacts_accept_extra_capability_env_handles() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        extra_optional_variables=[
            "MOZAIKS_APP_URL",
            "MOZAIKSPAY_API_BASE",
            "MOZAIKSPAY_CLIENT_ID",
            "MOZAIKSPAY_CLIENT_SECRET",
            "MOZAIKSPAY_API_KEY",
        ],
        extra_secret_variables=["MOZAIKSPAY_CLIENT_SECRET", "MOZAIKSPAY_API_KEY"],
    )
    manifest = result["deployment_manifest"]
    env_example = result["artifacts"]["env.example"]

    assert "MOZAIKSPAY_API_BASE=" in env_example
    assert "MOZAIKSPAY_CLIENT_ID=" in env_example
    assert "MOZAIKSPAY_CLIENT_SECRET=\n" in env_example
    assert "MOZAIKSPAY_API_KEY=\n" in env_example
    assert "MOZAIKS_APP_URL=" in env_example
    assert "MOZAIKSPAY_CLIENT_SECRET" in manifest["secret_env"]
    assert "MOZAIKSPAY_API_KEY" in manifest["secret_env"]
    assert "MOZAIKSPAY_CLIENT_SECRET=secret" not in env_example
    assert result["bundle_errors"] == []


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
    assert "MOZAIKSPAY" not in manifest_text


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

    assert input_names == {"deployment_id", "build_result_id", "build_callback_url", "app_url"}
    for name in input_names:
        assert f"${{{{ inputs.{name} }}}}" in workflow


def test_deployment_manifest_carries_provider_neutral_readiness_requirements() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
        include_compose=False,
    )
    manifest = result["deployment_manifest"]
    requirements = manifest["readiness_requirements"]

    assert validate_readiness_requirements(requirements) == []
    checks = {item["id"]: item for item in requirements["checks"]}
    assert checks["runtime_environment"]["required_env"] == ["OPENAI_API_KEY", "MONGO_URI"]
    assert "APP_IMAGE_SMOKE_VERIFIED_AT" in checks["container_smoke"]["required_evidence"]
    assert "APP_HEALTHCHECK_VERIFIED_AT" in checks["healthcheck"]["required_evidence"]
    assert "azure" not in json.dumps(requirements).lower()
    assert "mozaikspay" not in json.dumps(requirements).lower()


def test_readiness_requirements_reject_secret_values_and_bad_names() -> None:
    errors = validate_readiness_requirements(
        {
            "checks": [
                {
                    "id": "Runtime",
                    "category": "deployment",
                    "label": "github_pat_abcdefghijklmnopqrstuvwxyz123456",
                    "implemented_score": 11,
                    "required_env": ["openai-api-key"],
                    "required_evidence": ["APP_IMAGE_SMOKE_VERIFIED_AT", "APP_IMAGE_SMOKE_VERIFIED_AT"],
                    "canonical_paths": ["deployment.manifest.json"],
                }
            ]
        }
    )

    assert any(".id must be a lowercase identifier" in err for err in errors)
    assert any(".label must not contain raw secret material" in err for err in errors)
    assert any(".implemented_score must be an integer from 0 to 10" in err for err in errors)
    assert any(".required_env[0] must be an uppercase identifier" in err for err in errors)
    assert any(".required_evidence[1] is duplicated" in err for err in errors)


def test_e2b_is_documented_as_predeploy_validation_only() -> None:
    source = _read("mozaiksai/core/workflow/generator_support/app_validation_strategy.py")
    assert "pre-deploy build validation" in source
    assert "Not a production hosting runtime" in source


def test_appgenerator_guidance_mentions_provider_neutral_target_profiles() -> None:
    source = _read("factory_app/workflows/AppGenerator/agents.yaml")
    assert "provider-neutral outputs from the deployment contract" in source
    assert "how the generated app runs" in source
    assert "provider-owned adapters" in source
    assert "deployment_profile" in source
    assert "ci_secret_requirements" in source
    assert ".github/workflows/readiness.yml" in source
    assert "ArtifactVersion promotion" in source
    assert "environment staging" in source


def test_self_host_docker_compose_path_is_documented() -> None:
    source = _read("docs/architecture/deployment/generated-app-deployment-contract.md")
    assert "self-hosting" in source.lower()
    assert "local Docker" in source
    assert "local Compose" in source
    assert "ci_secret_requirements" in source
    assert ".github/workflows/readiness.yml" in source
    assert "Artifact review staging" in source
    assert "Environment staging" in source
    assert "app.json.authRequired" in source
    assert "auth.required=true" in source
    assert "APP_AUTH_SMOKE_VERIFIED_AT" in source
    assert "how does this generated app run" in source
    assert "must not include hosted product provider adapters" in source
    assert "App Zero dogfooding follows the same handoff" in source


def test_canonical_app_structure_documents_deploy_artifact_boundary() -> None:
    source = _read("docs/architecture/app/canonical-app-structure.md")
    assert "Dockerfile" in source
    assert "deployment.manifest.json" in source
    assert "Root deployment artifacts declare how the app runs" in source
    assert "app.json.authRequired" in source
    assert "provider-neutral JWT/OIDC auth contract" in source
    assert "AppGenerator build tasks do not own these files" in source
    assert "must not generate hosted platform provider adapters into customer app bundles" in source


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


# ---------------------------------------------------------------------------
# GitHub Environments + post-deploy health check
# ---------------------------------------------------------------------------

def test_default_ci_secret_requirements_has_app_url_input() -> None:
    result = _default_ci_secret_requirements(include_workflow=True)
    input_names = [item["name"] for item in result["workflow_inputs"]]
    assert "app_url" in input_names


def test_default_app_url_input_is_not_required() -> None:
    result = _default_ci_secret_requirements(include_workflow=True)
    app_url = next(item for item in result["workflow_inputs"] if item["name"] == "app_url")
    assert app_url["required"] is False


def test_generated_workflow_has_staging_environment_on_build_job() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_workflow=True,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    assert "environment: staging" in workflow


def test_infra_scaffold_emits_readiness_workflow_from_documented_template() -> None:
    result = asyncio.run(
        save_infra_scaffold(
            emit_infra=True,
            emit_auth_adapter=False,
            context_variables={"app_slug": "Demo App"},
        )
    )
    files = {item["filename"]: item["content"] for item in result["code_files"]}

    assert ".github/workflows/readiness.yml" in files
    assert ".github/workflows/deploy.yml" in files
    assert "environment staging" in files[".github/workflows/readiness.yml"].lower()
    assert "artifact review staging" in files[".github/workflows/readiness.yml"].lower()


def test_generated_workflow_has_production_environment_on_deploy_job() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_workflow=True,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    assert "environment: production" in workflow


def test_generated_workflow_has_separate_deploy_job_with_needs() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_workflow=True,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    assert "  deploy:" in workflow
    assert "needs: build-and-smoke" in workflow


def test_generated_workflow_deploy_job_has_post_deploy_health_check() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_workflow=True,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    assert "Post-deploy health check" in workflow
    assert "APP_URL" in workflow
    assert "HEALTH_PATH" in workflow


def test_generated_workflow_health_check_uses_default_health_path() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_workflow=True,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    assert "HEALTH_PATH: /api/health" in workflow


def test_generated_workflow_health_check_skips_when_app_url_unset() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_workflow=True,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    assert "APP_URL not set" in workflow


def test_generated_workflow_health_check_retries_ten_times() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_workflow=True,
    )
    workflow = result["artifacts"][".github/workflows/deploy.yml"]
    assert "seq 1 10" in workflow


def test_render_workflow_uses_custom_environment_names() -> None:
    spec = build_deploy_target_spec(app_id="demo_app")
    spec["deploy_environments"] = {"staging": "ci-preview", "production": "prod-us"}
    workflow = _render_workflow(spec)
    assert "environment: ci-preview" in workflow
    assert "environment: prod-us" in workflow


def test_render_workflow_uses_custom_health_path() -> None:
    spec = build_deploy_target_spec(app_id="demo_app")
    spec["runtime"]["health_path"] = "/healthz"
    workflow = _render_workflow(spec)
    assert "HEALTH_PATH: /healthz" in workflow


def test_deploy_target_spec_includes_deploy_environments() -> None:
    spec = build_deploy_target_spec(app_id="demo_app")
    assert "deploy_environments" in spec
    assert spec["deploy_environments"]["staging"] == "staging"
    assert spec["deploy_environments"]["production"] == "production"


def test_validate_deploy_target_spec_rejects_secret_environment_name() -> None:
    spec = build_deploy_target_spec(app_id="demo_app")
    spec["deploy_environments"]["production"] = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    errors = validate_deploy_target_spec(spec)
    assert any("deploy_environments.production" in err for err in errors)


def test_validate_deploy_target_spec_rejects_non_dict_deploy_environments() -> None:
    spec = build_deploy_target_spec(app_id="demo_app")
    spec["deploy_environments"] = "not-a-dict"
    errors = validate_deploy_target_spec(spec)
    assert any("deploy_environments must be an object" in err for err in errors)


def test_validate_deploy_target_spec_accepts_absent_deploy_environments() -> None:
    spec = build_deploy_target_spec(app_id="demo_app")
    del spec["deploy_environments"]
    errors = validate_deploy_target_spec(spec)
    assert errors == []


def test_generated_workflow_passes_bundle_validation() -> None:
    result = generate_deployment_artifacts(
        app_id="demo_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=True,
    )
    assert result["bundle_errors"] == []
    assert result["deploy_target_spec_errors"] == []
