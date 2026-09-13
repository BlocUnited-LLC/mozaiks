from __future__ import annotations

import json
import re
from typing import Any

import yaml

from factory_app.app.modules.security_readiness.backend.schemas import summarize_findings
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle

from .build_artifact import (
    context_get as _context_get,
)
from .build_artifact import (
    context_set as _context_set,
)
from .build_artifact import (
    resolve_security_artifact,
    source_exception,
    source_failure,
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
    re.compile(r"(?i)(?:sk_live|rk_live|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_./+=-]{12,}"),
)




def _finding(
    *,
    finding_id: str,
    severity: str,
    control_area: str,
    title: str,
    description: str,
    evidence_path: str | None = None,
    recommendation: str,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "finding_id": finding_id,
        "severity": severity,
        "status": "open",
        "source": "validation",
        "control_area": control_area,
        "title": title,
        "description": description,
        "recommendation": recommendation,
    }
    if evidence_path:
        finding["evidence"] = {"path": evidence_path}
    return finding


def _app_json(files: dict[str, str]) -> dict[str, Any]:
    text = files.get("app.json") or files.get("app/app.json")
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _yaml_file(files: dict[str, str], path: str) -> dict[str, Any] | None:
    text = files.get(path) or files.get(path.removeprefix("app/"))
    if not text:
        return None
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return {"__parse_error__": True}
    return parsed if isinstance(parsed, dict) else {}


def _declares_auth_required(files: dict[str, str]) -> bool:
    app = _app_json(files)
    for key in ("authRequired", "auth_required", "requiresAuth", "requires_auth"):
        if app.get(key) is True:
            return True
    return False


def _declares_deployment(files: dict[str, str]) -> bool:
    return any(
        path in files
        for path in ("Dockerfile", "deployment.manifest.json", ".github/workflows/deploy.yml")
    )


def _scan_raw_secret_values(files: dict[str, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path, text in sorted(files.items()):
        if path.endswith("package-lock.json"):
            continue
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(text):
                findings.append(
                    _finding(
                        finding_id=f"raw_secret_value:{path}",
                        severity="critical",
                        control_area="secrets",
                        title="Possible raw secret value in generated file",
                        description="A generated file matched a raw credential pattern. The finding records only the file path, not the matched value.",
                        evidence_path=path,
                        recommendation="Move credential values into the configured secret backend and keep only names/env handles in app/security/secrets.yaml or env.example.",
                    )
                )
                break
    return findings


def _scan_secret_contract(files: dict[str, str]) -> list[dict[str, Any]]:
    contract = _yaml_file(files, "app/security/secrets.yaml")
    if contract is None:
        return []
    if contract.get("__parse_error__"):
        return [
            _finding(
                finding_id="secret_contract:parse_error",
                severity="high",
                control_area="secrets",
                title="Secret contract is not valid YAML",
                description="app/security/secrets.yaml could not be parsed as YAML.",
                evidence_path="app/security/secrets.yaml",
                recommendation="Regenerate app/security/secrets.yaml as the canonical names-only secret contract.",
            )
        ]
    findings: list[dict[str, Any]] = []
    secrets = contract.get("secrets")
    if secrets is not None and not isinstance(secrets, list):
        findings.append(
            _finding(
                finding_id="secret_contract:secrets_not_list",
                severity="high",
                control_area="secrets",
                title="Secret contract secrets field is not a list",
                description="app/security/secrets.yaml must declare secrets as a list of names-only entries.",
                evidence_path="app/security/secrets.yaml",
                recommendation="Use secrets entries with id, env, purpose, required, and optional provider metadata.",
            )
        )
    forbidden_keys = {
        "value",
        "secret",
        "token",
        "password",
        "api_key",
        "connection_string",
        "private_key",
    }
    for index, item in enumerate(secrets or []):
        if not isinstance(item, dict):
            continue
        raw_keys = forbidden_keys.intersection(item)
        if raw_keys:
            findings.append(
                _finding(
                    finding_id=f"secret_contract:raw_value_field:{index}",
                    severity="critical",
                    control_area="secrets",
                    title="Secret contract contains a raw-value field",
                    description=f"Secret entry {index} declares forbidden value-bearing fields: {sorted(raw_keys)}.",
                    evidence_path="app/security/secrets.yaml",
                    recommendation="Replace value-bearing fields with provider-neutral secret names and env handles only.",
                )
            )
    return findings


def _scan_module_contracts(files: dict[str, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    module_paths = sorted(
        path for path in files if path.startswith(("app/modules/", "modules/")) and path.endswith("/module.yaml")
    )
    for path in module_paths:
        try:
            module = yaml.safe_load(files[path])
        except yaml.YAMLError:
            findings.append(
                _finding(
                    finding_id=f"module_contract:parse_error:{path}",
                    severity="high",
                    control_area="permissions",
                    title="Module contract is not valid YAML",
                    description="A module.yaml file could not be parsed.",
                    evidence_path=path,
                    recommendation="Regenerate the module contract using the canonical mozaiks.module.v1 shape.",
                )
            )
            continue
        if not isinstance(module, dict):
            continue
        declared_permissions = {
            str(item.get("id") or "").strip()
            for item in module.get("permissions") or []
            if isinstance(item, dict)
        }
        module_id = str((module.get("module") or {}).get("id") or path).strip()
        for action in module.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip()
            api_surface = str(action.get("api_surface") or "").strip()
            permissions = [
                str(item).strip() for item in action.get("permissions") or [] if str(item).strip()
            ]
            if api_surface in {"public", "public_mutation", "public_readonly"}:
                continue
            if not permissions:
                findings.append(
                    _finding(
                        finding_id=f"module_permissions:missing:{module_id}:{action_id}",
                        severity="high",
                        control_area="permissions",
                        title="Private module action has no permission gate",
                        description=f"Action {action_id or '<unknown>'} in {module_id} is not public but declares no permissions.",
                        evidence_path=path,
                        recommendation="Declare explicit module permissions for private or mutating actions.",
                    )
                )
            unknown = sorted(set(permissions) - declared_permissions)
            if unknown:
                findings.append(
                    _finding(
                        finding_id=f"module_permissions:undeclared:{module_id}:{action_id}",
                        severity="high",
                        control_area="permissions",
                        title="Module action references undeclared permissions",
                        description=f"Action {action_id or '<unknown>'} references permissions not declared in module.yaml: {unknown}.",
                        evidence_path=path,
                        recommendation="Add the permission declarations or correct the action permission ids.",
                    )
                )
        module_root = path.rsplit("/", 1)[0]
        repo_path = f"{module_root}/backend/repo.py"
        policy_path = f"{module_root}/backend/policy.py"
        if repo_path in files and policy_path not in files:
            findings.append(
                _finding(
                    finding_id=f"tenant_scope:missing_policy:{module_id}",
                    severity="medium",
                    control_area="tenant_isolation",
                    title="Persistent module has no policy.py",
                    description=f"Module {module_id} has backend/repo.py but no backend/policy.py scoping helper.",
                    evidence_path=module_root,
                    recommendation="Add module-local policy helpers for owner, tenant, or workspace scoping when persistence is user or tenant scoped.",
                )
            )
    return findings


async def inspect_generated_app_security(context_variables: Any | None = None) -> dict[str, Any]:
    """Inspect the generated app bundle for baseline advisory security readiness."""

    _context_set(context_variables, "security_readiness_recorded", False)
    try:
        binding, artifact = await resolve_security_artifact(context_variables)
        files, diagnostics = await read_artifact_bundle(artifact)
    except Exception as exc:
        return source_exception(context_variables, exc)
    if any(item["blocking"] for item in diagnostics):
        return source_failure(context_variables, "security_source_incomplete", diagnostics)
    if not files:
        return source_failure(context_variables, "security_source_empty", diagnostics)

    findings: list[dict[str, Any]] = []
    findings.extend(_scan_raw_secret_values(files))
    findings.extend(_scan_secret_contract(files))
    if _declares_auth_required(files) and not {"app/config/auth.yaml", "config/auth.yaml"}.intersection(files):
        findings.append(
            _finding(
                finding_id="auth_contract:missing_auth_yaml",
                severity="high",
                control_area="auth",
                title="Auth-required app has no auth contract",
                description="The app appears to require authentication but app/config/auth.yaml is absent.",
                evidence_path="app/config/auth.yaml",
                recommendation="Generate app/config/auth.yaml for provider-neutral authentication behavior.",
            )
        )
    if (
        _declares_deployment(files)
        and not {"app/security/production_operations.yaml", "security/production_operations.yaml"}.intersection(files)
    ):
        findings.append(
            _finding(
                finding_id="production_operations:missing",
                severity="medium",
                control_area="deployment",
                title="Deployable app has no production operations contract",
                description="The app declares deployment artifacts or profile data, but no provider-neutral production operations contract was found.",
                evidence_path="app/security/production_operations.yaml",
                recommendation="Generate app/security/production_operations.yaml for production operation authority and readiness expectations.",
            )
        )
    findings.extend(_scan_module_contracts(files))

    summary = summarize_findings(findings)
    result = {
        "success": True,
        "status": "not_assessed" if not files else "passed" if summary["open"] == 0 else "attention_required",
        "mode": str(
            _context_get(context_variables, "security_readiness_mode", "advisory") or "advisory"
        ),
        "checked_file_count": len(files),
        "findings": findings,
        "summary": summary,
        "artifact_version_id": artifact.id,
        **binding.model_dump(),
        "source_diagnostics": diagnostics,
    }
    _context_set(context_variables, "security_readiness_findings", findings)
    _context_set(context_variables, "security_readiness_summary", result)
    return result
