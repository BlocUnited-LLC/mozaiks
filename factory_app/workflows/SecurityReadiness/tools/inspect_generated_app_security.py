from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import yaml

from factory_app.app.modules.security_readiness.backend.schemas import summarize_findings
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from mozaiksai.core.runtime.app.auth_contract import (
    AppAuthContractError,
    validate_app_auth_contract,
)
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

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
from .module_data_reach import APP_WIDE, NONE, OWNED, BundleDataReach, valid_data_contract

_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
    re.compile(r"(?i)(?:sk_live|rk_live|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_./+=-]{12,}"),
)

# The surfaces the module router admits anonymous callers to, exactly as the loader accepts them.
_PUBLIC_SURFACES = ("public", "public_readonly")

# What a non-public action without permissions exposes, keyed by the declared
# contract that fails to bound its caller. See
# docs/architecture/app/generated-action-protection.md for the decision table.
_PERMISSION_GAPS: dict[str, tuple[str, str, str, str]] = {
    "private": (
        "high",
        "Private module action has no permission gate",
        "Action {action} in {module} is not public but declares no permissions.",
        "Declare explicit module permissions for private or mutating actions.",
    ),
    "no_sign_in": (
        "high",
        "Non-public module action in an app without sign-in",
        "Action {action} in {module} is not public and declares no permissions, and the app declares "
        "no sign-in, so no declared contract identifies or limits its caller.",
        "Declare sign-in with app.json authRequired and config/auth.yaml, declare a module permission, "
        "or declare the action public or public_readonly if anyone may call it.",
    ),
    "shared_data": (
        "high",
        "Signed-in action reaches shared records without a permission",
        "Any signed-in user can call action {action} in {module}: it declares no permissions and no "
        "entitlement gate that the default plan withholds, and the collections its module reaches "
        "include app_wide collections that every user shares.",
        "Declare a module permission, an entitlement gate the default plan does not grant, or "
        "per_user/per_workspace ownership for the collection in data/contract.json.",
    ),
    "plan_only": (
        "medium",
        "Signed-in action reaches shared records restricted by plan only",
        "Any signed-in user whose plan includes the entitlement gate of action {action} in {module} can "
        "call it: it declares no permissions, and the collections its module reaches include app_wide "
        "collections. The gate restricts callers by plan; it does not decide whose records they change.",
        "Declare a module permission, or per_user/per_workspace ownership for the collection in "
        "data/contract.json, if plan holders must not change each other's records.",
    ),
    "unscoped_data": (
        "high",
        "Signed-in action reaches records no declared ownership scopes",
        "Any signed-in user can call action {action} in {module}: it declares no permissions, and its "
        "module reaches records that no declared ownership scopes. The app has no valid data/contract.json, "
        "a collection the module reaches declares neither per_user/per_workspace ownership nor app_wide "
        "tenancy, or the module's code uses persistence that cannot be resolved to declared collections.",
        "Declare a module permission, or declare every collection the module reaches in data/contract.json "
        "and address it with constant names through ctx.persistence.collection or "
        "app_data_from_context(ctx).collection.",
    ),
    "undeclared_scope": (
        "medium",
        "Signed-in action has no declared permission or data scope",
        "Any signed-in user can call action {action} in {module}: it declares no permissions and no "
        "entitlement gate that the default plan withholds, and its module reaches no declared collection "
        "that bounds what it does.",
        "Declare a module permission or an entitlement gate, or declare the collections it uses with "
        "per_user/per_workspace ownership in data/contract.json.",
    ),
}


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


def _app_root(files: dict[str, str]) -> str:
    """The prefix of the app root the runtime binds: the bundle root when it holds app.json, else app/."""
    if "app.json" not in files and "app/app.json" in files:
        return "app/"
    return ""


def _app_files(files: dict[str, str]) -> dict[str, str]:
    """The files of the bound app root, keyed relative to it. Files outside it are never loaded."""
    root = _app_root(files)
    return {path.removeprefix(root): text for path, text in files.items() if path.startswith(root)}


def _app_json(files: dict[str, str]) -> dict[str, Any]:
    text = files.get("app.json")
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _yaml_file(files: dict[str, str], path: str) -> dict[str, Any] | None:
    text = files.get(path)
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


def _declares_sign_in(files: dict[str, str]) -> bool:
    """True only for the app.json and config/auth.yaml pair the runtime loads as sign-in."""
    if _app_json(files).get("authRequired") is not True:
        return False
    contract = _yaml_file(files, "config/auth.yaml")
    if not contract or contract.get("__parse_error__"):
        return False
    try:
        validate_app_auth_contract(contract)
    except AppAuthContractError:
        return False
    return True


def _default_plan_capabilities(files: dict[str, str]) -> frozenset[str] | None:
    """Capabilities a signed-in user holds without a subscription assignment.

    None without a valid subscriptions contract: the runtime then wires no
    entitlement adapter and every entitlement gate passes.
    """
    contract = _yaml_file(files, "config/subscriptions.yaml")
    if not contract or contract.get("__parse_error__"):
        return None
    try:
        config = SubscriptionsConfig.model_validate(contract)
    except (TypeError, ValueError):
        return None
    if config.products:
        return frozenset().union(
            *(product.capabilities_for_plan(product.default_plan_id) for product in config.products)
        )
    return config.capabilities_for_plan(config.default_plan_id or "")


def _authenticated_action_gap(
    action: dict[str, Any], *, sign_in: bool, reach: str, default_capabilities: frozenset[str] | None,
) -> str | None:
    """The unprotected exposure of an authenticated-surface action without permissions.

    An entitlement gate restricts callers by plan. It never stands in for
    ownership: it only lowers app_wide reach to medium and covers an action
    that reaches no collection.
    """
    if not sign_in:
        return "no_sign_in"
    if reach == OWNED:
        return None
    gate = action.get("entitlement_gate")
    restricting = (
        isinstance(gate, str) and bool(gate.strip())
        and default_capabilities is not None and gate.strip() not in default_capabilities
    )
    if reach == NONE:
        return None if restricting else "undeclared_scope"
    if reach == APP_WIDE:
        return "plan_only" if restricting else "shared_data"
    return "unscoped_data"


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
    contract = _yaml_file(files, "security/secrets.yaml")
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


def _iterable(value: Any) -> Iterable[Any]:
    """A contract value to iterate; a scalar where a list belongs holds nothing."""
    return value if isinstance(value, Iterable) else ()


def _scan_module_contracts(files: dict[str, str], root: str) -> list[dict[str, Any]]:
    """Module findings for the app root's files; evidence paths keep the bundle's root prefix."""
    findings: list[dict[str, Any]] = []
    module_paths = sorted(path for path in files if path.startswith("modules/") and path.endswith("/module.yaml"))
    sign_in = _declares_sign_in(files)
    default_capabilities = _default_plan_capabilities(files)
    data_reach = BundleDataReach(files, valid_data_contract(files.get("data/contract.json")))
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
                    evidence_path=root + path,
                    recommendation="Regenerate the module contract using the canonical mozaiks.module.v1 shape.",
                )
            )
            continue
        if not isinstance(module, dict):
            continue
        declared_permissions = {
            str(item.get("id") or "").strip()
            for item in _iterable(module.get("permissions"))
            if isinstance(item, dict)
        }
        header = module.get("module")
        module_id = str((header.get("id") if isinstance(header, dict) else None) or root + path).strip()
        module_root = path.rsplit("/", 1)[0]
        reach = data_reach.for_module(module_id, module_root)
        for action in _iterable(module.get("actions")):
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip()
            permissions = [
                str(item).strip() for item in _iterable(action.get("permissions")) if str(item).strip()
            ]
            if action.get("api_surface") in _PUBLIC_SURFACES:
                continue
            # Reaction-only internal actions are authorized by the event bus.
            if action.get("api_surface") == "internal" and action.get("permissions") == []:
                continue
            # An absent or null surface is authenticated HTTP. Sign-in with owned
            # reach can protect it; every other surface still needs a declared
            # permission.
            gap: str | None = None
            if not permissions:
                gap = "private"
                raw_permissions = action.get("permissions")
                loadable = raw_permissions is None or isinstance(raw_permissions, list)
                if action.get("api_surface") is None and loadable:
                    gap = _authenticated_action_gap(
                        action, sign_in=sign_in, reach=reach, default_capabilities=default_capabilities,
                    )
            if gap is not None:
                severity, title, description, recommendation = _PERMISSION_GAPS[gap]
                findings.append(
                    _finding(
                        finding_id=f"module_permissions:missing:{module_id}:{action_id}",
                        severity=severity,
                        control_area="permissions",
                        title=title,
                        description=description.format(action=action_id or "<unknown>", module=module_id),
                        evidence_path=root + path,
                        recommendation=recommendation,
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
                        evidence_path=root + path,
                        recommendation="Add the permission declarations or correct the action permission ids.",
                    )
                )
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
                    evidence_path=root + module_root,
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

    # The runtime binds one app root and loads contracts only from it.
    app_files = _app_files(files)
    findings: list[dict[str, Any]] = []
    findings.extend(_scan_raw_secret_values(files))
    findings.extend(_scan_secret_contract(app_files))
    if _declares_auth_required(app_files) and "config/auth.yaml" not in app_files:
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
        and "security/production_operations.yaml" not in app_files
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
    findings.extend(_scan_module_contracts(app_files, _app_root(files)))

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
    _context_set(context_variables, "artifact_version_id", artifact.id)
    _context_set(context_variables, "security_readiness_findings", findings)
    _context_set(context_variables, "security_readiness_summary", result)
    return result
