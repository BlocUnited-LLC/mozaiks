"""Provider-neutral deployment contract helpers for generated app artifacts.

This module defines deterministic, testable deployment contracts for generated
apps without coupling the OSS generator to any cloud provider policy.
"""

from __future__ import annotations

import json
import re
from typing import Any

DEFAULT_RUNTIME_PORT = 8000
DEFAULT_HEALTH_PATH = "/api/health"
DEFAULT_PROFILE = "generic_container"

_TARGET_KINDS = {"container", "compose", "external_adapter"}
_TAG_STRATEGIES = {"commit_sha", "timestamp", "manual"}
_BUILD_STATUSES = {"pending", "running", "succeeded", "failed"}
_PROHIBITED_CONTENT_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)azure[_-]?subscription[_-]?id\s*[:=]\s*[0-9a-f-]{36}"),
    re.compile(r"(?im)(token|secret|password)[ \t]*[:=][ \t]*[^\s\"']{8,}"),
]
_FORBIDDEN_SECRET_KEY_FRAGMENTS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
)
_FORBIDDEN_SECRET_VALUE_MARKERS = (
    "-----BEGIN",
    "ghp_",
    "github_pat_",
    "sk-",
    "xoxb-",
    "xoxp-",
)
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_WORKFLOW_INPUT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_READINESS_CHECK_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_WORKFLOW_SECRET_REF_RE = re.compile(r"\$\{\{\s*secrets\.([A-Z][A-Z0-9_]*)\s*\}\}")
_WORKFLOW_INPUT_REF_RE = re.compile(r"\$\{\{\s*inputs\.([a-z][a-z0-9_]*)\s*\}\}")


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return default


def _int_or_default(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _list_of_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]


def _merge_env_names(base: list[str], extra: list[str] | None) -> list[str]:
    merged: list[str] = []
    for item in [*base, *list(extra or [])]:
        name = str(item or "").strip()
        if name and name not in merged:
            merged.append(name)
    return merged


def _looks_like_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or " " in text:
        return False
    return text.startswith("https://") or text.startswith("http://")


def _forbidden_secret_key(key: str) -> bool:
    normalized = str(key).strip().lower()
    return any(fragment in normalized for fragment in _FORBIDDEN_SECRET_KEY_FRAGMENTS)


def _forbidden_secret_value(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("env://") or text.startswith("vault://"):
        return False
    if "${" in text and "}" in text:
        return False
    return any(marker in text for marker in _FORBIDDEN_SECRET_VALUE_MARKERS)


def _first_forbidden_secret_path(payload: Any, *, path: str = "") -> str | None:
    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            key = str(raw_key)
            key_path = f"{path}.{key}" if path else key
            if _forbidden_secret_key(key):
                return key_path
            if _forbidden_secret_value(value):
                return key_path
            nested = _first_forbidden_secret_path(value, path=key_path)
            if nested:
                return nested
    elif isinstance(payload, list):
        for idx, value in enumerate(payload):
            next_path = f"{path}[{idx}]" if path else f"[{idx}]"
            if _forbidden_secret_value(value):
                return next_path
            nested = _first_forbidden_secret_path(value, path=next_path)
            if nested:
                return nested
    return None


def _empty_ci_secret_requirements() -> dict[str, list[dict[str, Any]]]:
    return {"required": [], "optional": [], "workflow_inputs": []}


def _default_ci_secret_requirements(*, include_workflow: bool) -> dict[str, list[dict[str, Any]]]:
    if not include_workflow:
        return _empty_ci_secret_requirements()

    return {
        "required": [
            {
                "name": "REGISTRY_PUSH_TOKEN",
                "purpose": "Allows the generated CI workflow to authenticate a publish-ready registry step.",
                "used_by": "build_workflow",
            }
        ],
        "optional": [
            {
                "name": "BUILD_CALLBACK_SECRET",
                "purpose": "Allows an external host adapter to verify an optional build callback.",
                "used_by": "build_workflow",
            }
        ],
        "workflow_inputs": [
            {
                "name": "deployment_id",
                "required": True,
                "purpose": "Deployment correlation identifier supplied by an external host or adapter.",
            },
            {
                "name": "build_result_id",
                "required": True,
                "purpose": "Build result correlation identifier supplied by an external host or adapter.",
            },
            {
                "name": "build_callback_url",
                "required": False,
                "purpose": "Optional callback URL for hosts that collect build completion events.",
            },
            {
                "name": "app_url",
                "required": False,
                "purpose": "Live application base URL used by the deploy job for the post-deploy health check.",
            },
        ],
    }


def _normalize_ci_secret_requirements(value: Any) -> dict[str, list[dict[str, Any]]]:
    normalized = _empty_ci_secret_requirements()
    if not isinstance(value, dict):
        return normalized

    for bucket in ("required", "optional"):
        items = value.get(bucket)
        if not isinstance(items, list):
            continue
        normalized[bucket] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized[bucket].append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "purpose": str(item.get("purpose") or "").strip() or None,
                    "used_by": str(item.get("used_by") or "").strip() or None,
                }
            )

    workflow_inputs = value.get("workflow_inputs")
    if isinstance(workflow_inputs, list):
        normalized["workflow_inputs"] = []
        for item in workflow_inputs:
            if not isinstance(item, dict):
                continue
            normalized["workflow_inputs"].append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "required": bool(item.get("required")),
                    "purpose": str(item.get("purpose") or "").strip() or None,
                }
            )
    return normalized


def _default_readiness_requirements() -> dict[str, list[dict[str, Any]]]:
    return {
        "checks": [
            {
                "id": "runtime_environment",
                "category": "runtime",
                "label": "Runtime environment configured",
                "implemented_score": 7,
                "required_env": ["OPENAI_API_KEY", "MONGO_URI"],
                "required_evidence": [],
                "canonical_paths": ["env.example", "deployment.manifest.json"],
                "notes": "Host or operator must provide required runtime variables before deploy.",
            },
            {
                "id": "container_smoke",
                "category": "deployment",
                "label": "Container build and smoke check",
                "implemented_score": 7,
                "required_env": [],
                "required_evidence": ["APP_IMAGE_SMOKE_VERIFIED_AT"],
                "canonical_paths": ["Dockerfile", "deployment.manifest.json"],
                "notes": "Evidence stamp should reference a CI run, artifact, or change record.",
            },
            {
                "id": "healthcheck",
                "category": "deployment",
                "label": "Health endpoint check",
                "implemented_score": 7,
                "required_env": [],
                "required_evidence": ["APP_HEALTHCHECK_VERIFIED_AT"],
                "canonical_paths": ["deployment.manifest.json"],
                "notes": "Evidence stamp should be set only after the configured health path succeeds.",
            },
        ]
    }


def _normalize_readiness_requirements(value: Any) -> dict[str, list[dict[str, Any]]]:
    normalized: dict[str, list[dict[str, Any]]] = {"checks": []}
    if not isinstance(value, dict):
        return normalized
    checks = value.get("checks")
    if not isinstance(checks, list):
        return normalized
    for item in checks:
        if not isinstance(item, dict):
            continue
        normalized["checks"].append(
            {
                "id": str(item.get("id") or "").strip(),
                "category": str(item.get("category") or "").strip(),
                "label": str(item.get("label") or "").strip(),
                "implemented_score": _int_or_default(item.get("implemented_score"), 0),
                "required_env": _list_of_str(item.get("required_env")),
                "required_evidence": _list_of_str(item.get("required_evidence")),
                "canonical_paths": _list_of_str(item.get("canonical_paths")),
                "notes": str(item.get("notes") or "").strip() or None,
            }
        )
    return normalized


def validate_readiness_requirements(value: Any, *, path: str = "readiness_requirements") -> list[str]:
    errors: list[str] = []
    if value is None:
        return errors
    if not isinstance(value, dict):
        return [f"{path} must be an object when present"]

    unknown_root_keys = sorted(set(value) - {"checks"})
    if unknown_root_keys:
        errors.append(f"{path} has unsupported fields: {', '.join(unknown_root_keys)}")

    checks = value.get("checks", [])
    if checks is None:
        checks = []
    if not isinstance(checks, list):
        errors.append(f"{path}.checks must be a list")
        return errors

    seen_ids: set[str] = set()
    for idx, item in enumerate(checks):
        item_path = f"{path}.checks[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{item_path} must be an object")
            continue

        allowed_item_keys = {
            "id",
            "category",
            "label",
            "implemented_score",
            "required_env",
            "required_evidence",
            "canonical_paths",
            "notes",
        }
        unknown_item_keys = sorted(set(item) - allowed_item_keys)
        if unknown_item_keys:
            errors.append(f"{item_path} has unsupported fields: {', '.join(unknown_item_keys)}")

        check_id = str(item.get("id") or "").strip()
        if not check_id:
            errors.append(f"{item_path}.id is required")
        elif not _READINESS_CHECK_ID_RE.fullmatch(check_id):
            errors.append(f"{item_path}.id must be a lowercase identifier using letters, digits, or underscores")
        elif check_id in seen_ids:
            errors.append(f"{item_path}.id is duplicated")
        else:
            seen_ids.add(check_id)

        category = str(item.get("category") or "").strip()
        if not category:
            errors.append(f"{item_path}.category is required")
        elif not _READINESS_CHECK_ID_RE.fullmatch(category):
            errors.append(f"{item_path}.category must be a lowercase identifier using letters, digits, or underscores")

        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{item_path}.label is required")
        elif _forbidden_secret_value(label):
            errors.append(f"{item_path}.label must not contain raw secret material")

        implemented_score = item.get("implemented_score")
        if not isinstance(implemented_score, int) or not 0 <= implemented_score <= 10:
            errors.append(f"{item_path}.implemented_score must be an integer from 0 to 10")

        for bucket in ("required_env", "required_evidence"):
            values = item.get(bucket, [])
            if values is None:
                values = []
            if not isinstance(values, list):
                errors.append(f"{item_path}.{bucket} must be a list")
                continue
            seen_values: set[str] = set()
            for value_idx, raw_value in enumerate(values):
                value_path = f"{item_path}.{bucket}[{value_idx}]"
                name = str(raw_value or "").strip()
                if not name:
                    errors.append(f"{value_path} is required")
                elif not _SECRET_NAME_RE.fullmatch(name):
                    errors.append(f"{value_path} must be an uppercase identifier using letters, digits, or underscores")
                elif name in seen_values:
                    errors.append(f"{value_path} is duplicated")
                else:
                    seen_values.add(name)

        canonical_paths = item.get("canonical_paths", [])
        if canonical_paths is None:
            canonical_paths = []
        if not isinstance(canonical_paths, list):
            errors.append(f"{item_path}.canonical_paths must be a list")
        else:
            for path_idx, raw_value in enumerate(canonical_paths):
                if not isinstance(raw_value, str) or not raw_value.strip():
                    errors.append(f"{item_path}.canonical_paths[{path_idx}] must be a non-empty string")
                elif _forbidden_secret_value(raw_value):
                    errors.append(f"{item_path}.canonical_paths[{path_idx}] must not contain raw secret material")

        notes = item.get("notes")
        if notes is not None and not isinstance(notes, str):
            errors.append(f"{item_path}.notes must be a string when present")
        elif isinstance(notes, str) and _forbidden_secret_value(notes):
            errors.append(f"{item_path}.notes must not contain raw secret material")

    return errors


def validate_ci_secret_requirements(value: Any, *, path: str = "ci_secret_requirements") -> list[str]:
    errors: list[str] = []
    if value is None:
        return errors
    if not isinstance(value, dict):
        return [f"{path} must be an object when present"]

    allowed_root_keys = {"required", "optional", "workflow_inputs"}
    unknown_root_keys = sorted(set(value) - allowed_root_keys)
    if unknown_root_keys:
        errors.append(
            f"{path} has unsupported fields: {', '.join(unknown_root_keys)}"
        )

    seen_required: set[str] = set()
    seen_optional: set[str] = set()

    for bucket_name, seen in (("required", seen_required), ("optional", seen_optional)):
        bucket = value.get(bucket_name, [])
        if bucket is None:
            bucket = []
        if not isinstance(bucket, list):
            errors.append(f"{path}.{bucket_name} must be a list")
            continue

        for idx, item in enumerate(bucket):
            item_path = f"{path}.{bucket_name}[{idx}]"
            if not isinstance(item, dict):
                errors.append(f"{item_path} must be an object")
                continue

            allowed_item_keys = {"name", "purpose", "used_by"}
            unknown_item_keys = sorted(set(item) - allowed_item_keys)
            if unknown_item_keys:
                errors.append(
                    f"{item_path} has unsupported fields: {', '.join(unknown_item_keys)}"
                )

            name = str(item.get("name") or "").strip()
            if not name:
                errors.append(f"{item_path}.name is required")
            elif not _SECRET_NAME_RE.fullmatch(name):
                errors.append(
                    f"{item_path}.name must be an uppercase identifier using letters, digits, or underscores"
                )
            elif name in seen:
                errors.append(f"{item_path}.name is duplicated within {bucket_name}")
            else:
                seen.add(name)

            purpose = item.get("purpose")
            if purpose is not None and not isinstance(purpose, str):
                errors.append(f"{item_path}.purpose must be a string when present")
            elif purpose is not None and _forbidden_secret_value(purpose):
                errors.append(f"{item_path}.purpose must not contain raw secret material")

            used_by = item.get("used_by")
            if used_by is not None and not isinstance(used_by, str):
                errors.append(f"{item_path}.used_by must be a string when present")
            elif isinstance(used_by, str) and used_by.strip():
                if _forbidden_secret_value(used_by):
                    errors.append(f"{item_path}.used_by must not contain raw secret material")
                elif not _SAFE_IDENTIFIER_RE.fullmatch(used_by.strip()):
                    errors.append(f"{item_path}.used_by must be a safe identifier")

    overlap = sorted(seen_required & seen_optional)
    if overlap:
        errors.append(
            f"{path}.required and {path}.optional must not contain duplicate names: {', '.join(overlap)}"
        )

    workflow_inputs = value.get("workflow_inputs", [])
    if workflow_inputs is None:
        workflow_inputs = []
    if not isinstance(workflow_inputs, list):
        errors.append(f"{path}.workflow_inputs must be a list")
    else:
        seen_inputs: set[str] = set()
        for idx, item in enumerate(workflow_inputs):
            item_path = f"{path}.workflow_inputs[{idx}]"
            if not isinstance(item, dict):
                errors.append(f"{item_path} must be an object")
                continue

            allowed_item_keys = {"name", "required", "purpose"}
            unknown_item_keys = sorted(set(item) - allowed_item_keys)
            if unknown_item_keys:
                errors.append(
                    f"{item_path} has unsupported fields: {', '.join(unknown_item_keys)}"
                )

            name = str(item.get("name") or "").strip()
            if not name:
                errors.append(f"{item_path}.name is required")
            elif not _WORKFLOW_INPUT_NAME_RE.fullmatch(name):
                errors.append(
                    f"{item_path}.name must be a lowercase identifier using letters, digits, or underscores"
                )
            elif name in seen_inputs:
                errors.append(f"{item_path}.name is duplicated")
            else:
                seen_inputs.add(name)

            if not isinstance(item.get("required"), bool):
                errors.append(f"{item_path}.required must be boolean")

            purpose = item.get("purpose")
            if purpose is not None and not isinstance(purpose, str):
                errors.append(f"{item_path}.purpose must be a string when present")
            elif purpose is not None and _forbidden_secret_value(purpose):
                errors.append(f"{item_path}.purpose must not contain raw secret material")

    return errors


def _workflow_secret_refs(workflow_text: str) -> set[str]:
    return set(_WORKFLOW_SECRET_REF_RE.findall(str(workflow_text or "")))


def _workflow_input_refs(workflow_text: str) -> set[str]:
    return set(_WORKFLOW_INPUT_REF_RE.findall(str(workflow_text or "")))


def validate_deployment_build_output(build_output: dict[str, Any]) -> list[str]:
    """Return validation errors for a provider-neutral DeploymentBuildOutput payload."""
    errors: list[str] = []
    if not isinstance(build_output, dict):
        return ["DeploymentBuildOutput must be an object"]

    build_status = str(build_output.get("build_status") or "").strip()
    if build_status not in _BUILD_STATUSES:
        errors.append("build_status must be one of: pending, running, succeeded, failed")

    image_ref = str(build_output.get("image_ref") or "").strip()
    if build_status == "succeeded" and not image_ref:
        errors.append("image_ref is required when build_status is succeeded")

    artifact_digest = str(build_output.get("artifact_digest") or "").strip()
    if artifact_digest and not artifact_digest.startswith("sha256:"):
        errors.append("artifact_digest must use sha256: prefix when present")

    repo_url = build_output.get("repo_url")
    if repo_url is not None and str(repo_url).strip() and not _looks_like_url(repo_url):
        errors.append("repo_url must be URL-shaped when present")

    workflow_run_url = build_output.get("workflow_run_url")
    if workflow_run_url is not None and str(workflow_run_url).strip() and not _looks_like_url(workflow_run_url):
        errors.append("workflow_run_url must be URL-shaped when present")

    logs_url = build_output.get("logs_url")
    if logs_url is not None and str(logs_url).strip() and not _looks_like_url(logs_url):
        errors.append("logs_url must be URL-shaped when present")

    safe_details = build_output.get("safe_details")
    if safe_details is not None:
        if not isinstance(safe_details, dict):
            errors.append("safe_details must be an object when present")
        else:
            forbidden_path = _first_forbidden_secret_path(safe_details)
            if forbidden_path:
                errors.append(
                    f"safe_details must not contain secret-shaped keys or values: {forbidden_path}"
                )

    for top_level_key in (
        "repo_url",
        "commit_sha",
        "image_ref",
        "artifact_digest",
        "workflow_run_url",
        "logs_url",
        "error_code",
    ):
        if _forbidden_secret_value(build_output.get(top_level_key)):
            errors.append(f"{top_level_key} must not contain raw secret material")

    return errors


def build_deploy_target_spec(
    *,
    app_id: str,
    deployment_profile: str = DEFAULT_PROFILE,
    target_id: str | None = None,
    target_kind: str = "container",
    include_dockerfiles: bool = True,
    include_workflow: bool = True,
    include_compose: bool = False,
    extra_required_variables: list[str] | None = None,
    extra_optional_variables: list[str] | None = None,
    extra_secret_variables: list[str] | None = None,
    extra_public_variables: list[str] | None = None,
) -> dict[str, Any]:
    """Build a provider-neutral DeployTargetSpec dictionary."""
    resolved_target_id = str(target_id or deployment_profile or DEFAULT_PROFILE).strip() or DEFAULT_PROFILE
    resolved_kind = str(target_kind or "container").strip() or "container"
    if resolved_kind not in _TARGET_KINDS:
        resolved_kind = "container"

    outputs = ["env.example", "deployment.manifest.json"]
    if _bool(include_dockerfiles, True):
        outputs.append("Dockerfile")
    if _bool(include_compose, False):
        outputs.append("docker-compose.yml")
    if _bool(include_workflow, True):
        outputs.append(".github/workflows/deploy.yml")

    return {
        "target_id": resolved_target_id,
        "target_kind": resolved_kind,
        "runtime": {
            "container_port": DEFAULT_RUNTIME_PORT,
            "health_path": DEFAULT_HEALTH_PATH,
            "start_command": None,
        },
        "artifact_outputs": outputs,
        "environment": {
            "required_variables": _merge_env_names(["OPENAI_API_KEY", "MONGO_URI"], extra_required_variables),
            "optional_variables": _merge_env_names(
                ["MOZAIKS_APP_ID", "MOZAIKS_BACKEND_URL", "INTERNAL_API_KEY"],
                extra_optional_variables,
            ),
            "secret_variables": _merge_env_names(
                ["OPENAI_API_KEY", "MONGO_URI", "INTERNAL_API_KEY"],
                extra_secret_variables,
            ),
            "public_variables": _merge_env_names(
                [
                    "VITE_APP_ID",
                    "VITE_API_URL",
                    "VITE_WS_URL",
                    "VITE_CORE_URL",
                    "VITE_AGENT_WEBSOCKET_URL",
                    "VITE_AGENT_API_URL",
                    "VITE_OIDC_AUTHORITY",
                    "VITE_OIDC_CLIENT_ID",
                    "VITE_OIDC_REDIRECT_URI",
                ],
                extra_public_variables,
            ),
        },
        "image": {
            "image_name": f"{str(app_id).strip() or 'generated-app'}-runtime",
            "tag_strategy": "commit_sha",
        },
        "checks": {
            "build": True,
            "smoke": True,
            "health": True,
        },
        "provider_profile": {
            "name": "generic",
            "description": "Provider-neutral defaults for container deployment targets.",
            "metadata": {},
        },
        "deployment_profile": str(deployment_profile or DEFAULT_PROFILE),
        "deploy_environments": {
            "staging": "staging",
            "production": "production",
        },
        "ci_secret_requirements": _default_ci_secret_requirements(
            include_workflow=_bool(include_workflow, True)
        ),
        "readiness_requirements": _default_readiness_requirements(),
    }


def validate_deploy_target_spec(spec: dict[str, Any]) -> list[str]:
    """Return validation errors for a DeployTargetSpec payload."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["DeployTargetSpec must be an object"]

    if not str(spec.get("target_id") or "").strip():
        errors.append("target_id is required")

    target_kind = str(spec.get("target_kind") or "").strip()
    if target_kind not in _TARGET_KINDS:
        errors.append("target_kind must be one of: container, compose, external_adapter")

    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
    container_port = runtime.get("container_port")  # type: ignore[union-attr]
    if not isinstance(container_port, int) or container_port <= 0:
        errors.append("runtime.container_port must be a positive integer")
    if not str(runtime.get("health_path") or "").startswith("/"):  # type: ignore[union-attr]
        errors.append("runtime.health_path must start with '/'")

    outputs = _list_of_str(spec.get("artifact_outputs"))
    if "env.example" not in outputs:
        errors.append("artifact_outputs must include env.example")
    if "deployment.manifest.json" not in outputs:
        errors.append("artifact_outputs must include deployment.manifest.json")

    env = spec.get("environment") if isinstance(spec.get("environment"), dict) else {}
    required_env = _list_of_str(env.get("required_variables"))  # type: ignore[union-attr]
    secret_env = _list_of_str(env.get("secret_variables"))  # type: ignore[union-attr]
    public_env = _list_of_str(env.get("public_variables"))  # type: ignore[union-attr]
    if not required_env:
        errors.append("environment.required_variables must not be empty")
    if set(secret_env) & set(public_env):
        errors.append("environment.secret_variables and environment.public_variables must not overlap")

    image = spec.get("image") if isinstance(spec.get("image"), dict) else {}
    if not str(image.get("image_name") or "").strip():  # type: ignore[union-attr]
        errors.append("image.image_name is required")
    if str(image.get("tag_strategy") or "") not in _TAG_STRATEGIES:  # type: ignore[union-attr]
        errors.append("image.tag_strategy must be one of: commit_sha, timestamp, manual")

    checks = spec.get("checks") if isinstance(spec.get("checks"), dict) else {}
    for key in ("build", "smoke", "health"):
        if not isinstance(checks.get(key), bool):  # type: ignore[union-attr]
            errors.append(f"checks.{key} must be boolean")

    provider_profile = spec.get("provider_profile")
    if not isinstance(provider_profile, dict):
        errors.append("provider_profile must be an object")
    elif not str(provider_profile.get("name") or "").strip():
        errors.append("provider_profile.name is required")

    deploy_environments = spec.get("deploy_environments")
    if deploy_environments is not None:
        if not isinstance(deploy_environments, dict):
            errors.append("deploy_environments must be an object when present")
        else:
            for env_key in ("staging", "production"):
                val = str(deploy_environments.get(env_key) or "").strip()
                if val and _forbidden_secret_value(val):
                    errors.append(f"deploy_environments.{env_key} must not contain raw secret material")

    errors.extend(
        validate_ci_secret_requirements(spec.get("ci_secret_requirements"), path="ci_secret_requirements")
    )
    errors.extend(
        validate_readiness_requirements(spec.get("readiness_requirements"), path="readiness_requirements")
    )

    return errors


def build_deployment_template_manifest(
    *,
    app_id: str,
    deployment_profile: str,
    deploy_target_spec: dict[str, Any],
    generated_files: dict[str, str],
) -> dict[str, Any]:
    """Build a deterministic DeploymentTemplateManifest dictionary."""
    file_paths = sorted([p for p in generated_files.keys() if isinstance(p, str)])
    runtime = deploy_target_spec.get("runtime") if isinstance(deploy_target_spec.get("runtime"), dict) else {}
    env = deploy_target_spec.get("environment") if isinstance(deploy_target_spec.get("environment"), dict) else {}
    has_ci_workflow = ".github/workflows/deploy.yml" in file_paths

    manifest = {
        "schema_version": "mozaiks.deployment.manifest.v1",
        "app_id": str(app_id),
        "deployment_profile": str(deployment_profile or DEFAULT_PROFILE),
        "generated_files": file_paths,
        "required_env": _list_of_str(env.get("required_variables")),  # type: ignore[union-attr]
        "secret_env": _list_of_str(env.get("secret_variables")),  # type: ignore[union-attr]
        "exposed_ports": [int(runtime.get("container_port") or DEFAULT_RUNTIME_PORT)],  # type: ignore[union-attr]
        "healthcheck": {
            "path": str(runtime.get("health_path") or DEFAULT_HEALTH_PATH),  # type: ignore[union-attr]
            "port": int(runtime.get("container_port") or DEFAULT_RUNTIME_PORT),  # type: ignore[union-attr]
        },
        "ci_workflow": ".github/workflows/deploy.yml" if has_ci_workflow else None,
        "ci_secret_requirements": (
            _normalize_ci_secret_requirements(deploy_target_spec.get("ci_secret_requirements"))
            if has_ci_workflow
            else _empty_ci_secret_requirements()
        ),
        "readiness_requirements": _normalize_readiness_requirements(
            deploy_target_spec.get("readiness_requirements")
        ),
        "dockerfile": "Dockerfile" if "Dockerfile" in file_paths else None,
        "compose": "docker-compose.yml" if "docker-compose.yml" in file_paths else None,
        "validation_status": "pending",
        "deploy_target_spec": deploy_target_spec,
        "build_output_contract": None,
    }

    errors = validate_deployment_template_manifest(manifest)
    manifest["validation_status"] = "valid" if not errors else "invalid"
    if errors:
        manifest["validation_errors"] = errors
    return manifest


def validate_deployment_template_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return validation errors for a DeploymentTemplateManifest payload."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["DeploymentTemplateManifest must be an object"]

    if str(manifest.get("schema_version") or "") != "mozaiks.deployment.manifest.v1":
        errors.append("schema_version must be mozaiks.deployment.manifest.v1")
    if not str(manifest.get("app_id") or "").strip():
        errors.append("app_id is required")
    if not str(manifest.get("deployment_profile") or "").strip():
        errors.append("deployment_profile is required")
    if not isinstance(manifest.get("generated_files"), list):
        errors.append("generated_files must be a list")
    if not isinstance(manifest.get("required_env"), list):
        errors.append("required_env must be a list")
    if not isinstance(manifest.get("secret_env"), list):
        errors.append("secret_env must be a list")
    if not isinstance(manifest.get("exposed_ports"), list):
        errors.append("exposed_ports must be a list")

    ci_secret_requirements = manifest.get("ci_secret_requirements")
    errors.extend(
        validate_ci_secret_requirements(ci_secret_requirements, path="ci_secret_requirements")
    )
    errors.extend(
        validate_readiness_requirements(manifest.get("readiness_requirements"), path="readiness_requirements")
    )

    health = manifest.get("healthcheck") if isinstance(manifest.get("healthcheck"), dict) else {}
    if not str(health.get("path") or "").startswith("/"):  # type: ignore[union-attr]
        errors.append("healthcheck.path must start with '/'")
    if not isinstance(health.get("port"), int):  # type: ignore[union-attr]
        errors.append("healthcheck.port must be an integer")

    status = str(manifest.get("validation_status") or "")
    if status not in {"pending", "valid", "invalid"}:
        errors.append("validation_status must be pending, valid, or invalid")

    if not isinstance(manifest.get("deploy_target_spec"), dict):
        errors.append("deploy_target_spec must be present")

    normalized_ci_requirements = _normalize_ci_secret_requirements(ci_secret_requirements)
    if manifest.get("ci_workflow") is None:
        if (
            normalized_ci_requirements["required"]
            or normalized_ci_requirements["optional"]
            or normalized_ci_requirements["workflow_inputs"]
        ):
            errors.append("ci_secret_requirements must be empty when ci_workflow is not present")

    build_output_contract = manifest.get("build_output_contract")
    if build_output_contract is not None:
        if not isinstance(build_output_contract, dict):
            errors.append("build_output_contract must be an object when present")
        else:
            build_output_errors = validate_deployment_build_output(build_output_contract)
            errors.extend([f"build_output_contract.{err}" for err in build_output_errors])
    return errors


def _render_env_example(spec: dict[str, Any]) -> str:
    env = spec.get("environment") if isinstance(spec.get("environment"), dict) else {}
    required_env = _list_of_str(env.get("required_variables"))  # type: ignore[union-attr]
    optional_env = _list_of_str(env.get("optional_variables"))  # type: ignore[union-attr]
    secret_env = set(_list_of_str(env.get("secret_variables")))  # type: ignore[union-attr]
    public_env = _list_of_str(env.get("public_variables"))  # type: ignore[union-attr]

    lines: list[str] = [
        "# Generated by Mozaiks deployment contract (provider-neutral)",
        "# Placeholder values only. Do not commit real secrets.",
        "",
        "# Required variables",
    ]
    for key in required_env:
        if key in secret_env:
            lines.append(f"{key}=")
        else:
            lines.append(f"{key}=<required>")

    if optional_env:
        lines.extend(["", "# Optional variables"])
        for key in optional_env:
            if key in secret_env:
                lines.append(f"{key}=")
            else:
                lines.append(f"{key}=")

    if public_env:
        lines.extend(["", "# Public client variables"])
        for key in public_env:
            lines.append(f"{key}=")

    return "\n".join(lines).rstrip() + "\n"


def _render_dockerfile(spec: dict[str, Any]) -> str:
    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
    port = int(runtime.get("container_port") or DEFAULT_RUNTIME_PORT)  # type: ignore[union-attr]
    return "\n".join(
        [
            "FROM python:3.13-slim",
            "WORKDIR /app",
            "COPY requirements.txt ./",
            "RUN pip install --no-cache-dir -r requirements.txt",
            "COPY . .",
            f"EXPOSE {port}",
            "CMD [\"python\", \"run_server.py\"]",
            "",
        ]
    )


def _render_compose(spec: dict[str, Any]) -> str:
    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
    port = int(runtime.get("container_port") or DEFAULT_RUNTIME_PORT)  # type: ignore[union-attr]
    return "\n".join(
        [
            "version: '3.9'",
            "services:",
            "  app:",
            "    build:",
            "      context: .",
            "      dockerfile: Dockerfile",
            "    env_file:",
            "      - .env",
            "    ports:",
            f"      - \"{port}:{port}\"",
            "",
        ]
    )


def _render_workflow(spec: dict[str, Any]) -> str:
    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
    port = int(runtime.get("container_port") or DEFAULT_RUNTIME_PORT)  # type: ignore[union-attr]
    health_path = str(runtime.get("health_path") or DEFAULT_HEALTH_PATH).strip() or DEFAULT_HEALTH_PATH  # type: ignore[union-attr]
    ci_secret_requirements = _normalize_ci_secret_requirements(spec.get("ci_secret_requirements"))
    required_secrets = ci_secret_requirements["required"]
    optional_secrets = ci_secret_requirements["optional"]
    workflow_inputs = ci_secret_requirements["workflow_inputs"]

    deploy_envs = spec.get("deploy_environments") if isinstance(spec.get("deploy_environments"), dict) else {}
    staging_env = str(deploy_envs.get("staging") or "staging").strip() or "staging"  # type: ignore[union-attr]
    production_env = str(deploy_envs.get("production") or "production").strip() or "production"  # type: ignore[union-attr]

    lines = [
        "name: deploy",
        "on:",
        "  push:",
        "    branches:",
        "      - main",
        "  workflow_dispatch:",
        "    inputs:",
    ]

    for item in workflow_inputs:
        input_name = str(item.get("name") or "").strip()
        if not input_name:
            continue
        description = str(item.get("purpose") or f"Workflow input for {input_name}.").strip()
        lines.extend(
            [
                f"      {input_name}:",
                f"        description: {json.dumps(description)}",
                f"        required: {'true' if bool(item.get('required')) else 'false'}",
                "        type: string",
            ]
        )

    # ── build-and-smoke job (gates on staging environment) ──────────────────
    lines.extend(
        [
            "jobs:",
            "  build-and-smoke:",
            "    runs-on: ubuntu-latest",
            f"    environment: {staging_env}",
        ]
    )

    if required_secrets or optional_secrets or workflow_inputs:
        lines.append("    env:")
        for item in [*required_secrets, *optional_secrets]:
            secret_name = str(item.get("name") or "").strip()
            if secret_name:
                lines.append(f"      {secret_name}: ${{{{ secrets.{secret_name} }}}}")
        for item in workflow_inputs:
            input_name = str(item.get("name") or "").strip()
            if input_name:
                lines.append(f"      {input_name.upper()}: ${{{{ inputs.{input_name} }}}}")

    lines.extend(
        [
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - uses: actions/setup-python@v5",
            "        with:",
            "          python-version: '3.13'",
        ]
    )

    if required_secrets:
        lines.extend(
            [
                "      - name: Validate CI secret contract",
                "        shell: bash",
            ]
        )
        lines.append("        env:")
        for item in required_secrets:
            secret_name = str(item.get("name") or "").strip()
            if secret_name:
                lines.append(f"          {secret_name}: ${{{{ secrets.{secret_name} }}}}")
        lines.extend(["        run: |"])
        for item in required_secrets:
            secret_name = str(item.get("name") or "").strip()
            if not secret_name:
                continue
            lines.extend(
                [
                    f"          if [ -z \"${secret_name}\" ]; then",
                    f"            echo \"Missing required CI requirement: {secret_name}\" >&2",
                    "            exit 1",
                    "          fi",
                ]
            )

    if workflow_inputs:
        lines.extend(
            [
                "      - name: Show workflow contract inputs",
                "        shell: bash",
                "        run: |",
            ]
        )
        for item in workflow_inputs:
            input_name = str(item.get("name") or "").strip()
            if input_name:
                lines.append(f"          echo \"{input_name}=${{{input_name.upper()}:-}}\"")

    lines.extend(
        [
            "      - name: Build container",
            "        run: docker build -t generated-app:ci .",
            "      - name: Smoke check",
            f"        run: docker run --rm -e PORT={port} generated-app:ci python -c \"print('smoke ok')\"",
        ]
    )

    # ── deploy job (gates on production environment, runs after build-and-smoke) ─
    lines.extend(
        [
            "",
            "  deploy:",
            "    runs-on: ubuntu-latest",
            "    needs: build-and-smoke",
            f"    environment: {production_env}",
            "    env:",
            "      APP_URL: ${{ inputs.app_url }}",
            f"      HEALTH_PATH: {health_path}",
            "    steps:",
            "      - name: Post-deploy health check",
            "        shell: bash",
            "        run: |",
            "          if [ -z \"${APP_URL}\" ]; then",
            "            echo \"APP_URL not set — skipping live health check.\"",
            "            exit 0",
            "          fi",
            "          echo \"Checking live instance at ${APP_URL}${HEALTH_PATH} ...\"",
            "          for i in $(seq 1 10); do",
            "            status=$(curl -s -o /dev/null -w \"%{http_code}\" \"${APP_URL}${HEALTH_PATH}\")",
            "            if [ \"$status\" = \"200\" ]; then",
            "              echo \"Health check passed (HTTP $status).\"",
            "              exit 0",
            "            fi",
            "            echo \"Attempt $i: HTTP $status — retrying in 10s ...\"",
            "            sleep 10",
            "          done",
            "          echo \"Health check failed after 10 attempts.\" >&2",
            "          exit 1",
            "",
        ]
    )
    return "\n".join(lines)


def generate_deployment_artifacts(
    *,
    app_id: str,
    deployment_profile: str = DEFAULT_PROFILE,
    include_dockerfiles: bool = True,
    include_workflow: bool = True,
    include_compose: bool = False,
    extra_required_variables: list[str] | None = None,
    extra_optional_variables: list[str] | None = None,
    extra_secret_variables: list[str] | None = None,
    extra_public_variables: list[str] | None = None,
) -> dict[str, Any]:
    """Generate deterministic deployment artifacts and validated manifests."""
    spec = build_deploy_target_spec(
        app_id=app_id,
        deployment_profile=deployment_profile,
        target_id=deployment_profile,
        target_kind="compose" if _bool(include_compose, False) else "container",
        include_dockerfiles=include_dockerfiles,
        include_workflow=include_workflow,
        include_compose=include_compose,
        extra_required_variables=extra_required_variables,
        extra_optional_variables=extra_optional_variables,
        extra_secret_variables=extra_secret_variables,
        extra_public_variables=extra_public_variables,
    )
    spec_errors = validate_deploy_target_spec(spec)

    files: dict[str, str] = {
        "env.example": _render_env_example(spec),
    }
    if _bool(include_dockerfiles, True):
        files["Dockerfile"] = _render_dockerfile(spec)
    if _bool(include_compose, False):
        files["docker-compose.yml"] = _render_compose(spec)
    if _bool(include_workflow, True):
        files[".github/workflows/deploy.yml"] = _render_workflow(spec)

    manifest = build_deployment_template_manifest(
        app_id=app_id,
        deployment_profile=deployment_profile,
        deploy_target_spec=spec,
        generated_files=files,
    )
    files["deployment.manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    bundle_errors = validate_generated_deployment_bundle(
        files,
        include_dockerfiles=_bool(include_dockerfiles, True),
        include_workflow=_bool(include_workflow, True),
    )

    return {
        "deploy_target_spec": spec,
        "deploy_target_spec_errors": spec_errors,
        "deployment_manifest": manifest,
        "artifacts": files,
        "bundle_errors": bundle_errors,
    }


def validate_generated_deployment_bundle(
    artifacts: dict[str, str],
    *,
    include_dockerfiles: bool,
    include_workflow: bool,
) -> list[str]:
    """Validate artifact presence and verify forbidden secret/provider markers are absent."""
    errors: list[str] = []
    if not isinstance(artifacts, dict):
        return ["artifacts must be a dictionary"]

    required_files = {"env.example", "deployment.manifest.json"}
    if include_dockerfiles:
        required_files.add("Dockerfile")
    if include_workflow:
        required_files.add(".github/workflows/deploy.yml")

    missing = sorted(path for path in required_files if path not in artifacts)
    if missing:
        errors.append("missing required artifacts: " + ", ".join(missing))

    env_example = str(artifacts.get("env.example") or "")
    for line in env_example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key.endswith("_KEY") or key.endswith("_SECRET") or key in {"MONGO_URI", "INTERNAL_API_KEY"}:
            if value and not value.startswith("<"):
                errors.append(f"secret variable {key} must not be assigned a real value")

    for path, content in artifacts.items():
        body = str(content or "")
        for pattern in _PROHIBITED_CONTENT_PATTERNS:
            if pattern.search(body):
                errors.append(f"artifact {path} contains forbidden secret/provider marker")
                break

    manifest_text = str(artifacts.get("deployment.manifest.json") or "")
    manifest_payload: dict[str, Any] | None = None
    if manifest_text:
        try:
            parsed_manifest = json.loads(manifest_text)
            if isinstance(parsed_manifest, dict):
                manifest_payload = parsed_manifest
        except Exception:
            errors.append("deployment.manifest.json must contain valid JSON")

    if include_workflow and manifest_payload is not None:
        workflow_text = str(artifacts.get(".github/workflows/deploy.yml") or "")
        ci_secret_requirements = _normalize_ci_secret_requirements(
            manifest_payload.get("ci_secret_requirements")
        )
        manifest_secret_names = {
            str(item.get("name") or "").strip()
            for item in [
                *ci_secret_requirements["required"],
                *ci_secret_requirements["optional"],
            ]
            if str(item.get("name") or "").strip()
        }
        workflow_secret_names = _workflow_secret_refs(workflow_text)
        if workflow_secret_names != manifest_secret_names:
            errors.append(
                "workflow secret references must match deployment manifest ci_secret_requirements names"
            )

        manifest_input_names = {
            str(item.get("name") or "").strip()
            for item in ci_secret_requirements["workflow_inputs"]
            if str(item.get("name") or "").strip()
        }
        workflow_input_names = _workflow_input_refs(workflow_text)
        if workflow_input_names != manifest_input_names:
            errors.append(
                "workflow input references must match deployment manifest ci_secret_requirements.workflow_inputs names"
            )

    return errors


__all__ = [
    "build_deploy_target_spec",
    "build_deployment_template_manifest",
    "generate_deployment_artifacts",
    "validate_ci_secret_requirements",
    "validate_deployment_build_output",
    "validate_deploy_target_spec",
    "validate_deployment_template_manifest",
    "validate_generated_deployment_bundle",
    "validate_readiness_requirements",
]

