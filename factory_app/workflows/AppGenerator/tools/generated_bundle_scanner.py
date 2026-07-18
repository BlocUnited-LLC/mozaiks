"""generated_bundle_scanner — scan a generated app bundle for forbidden patterns.

Checks that the generated app does not:

- Embed raw provider secret-looking literals.
  Credentials must be collected and resolved through the configured secret
  backend, not checked into generated app artifacts.

- Declare persistent module surfaces in data/contract.json without matching
  modules/{module_id}/module.yaml artifacts. Such bundles can pass page-level
  validation but fail promotion/runtime acceptance because there is no module
  endpoint to own the declared app business data.

- Emit removed data/security locations or unknown app-root folders. The
  generated app bundle has finite app planes; build-time context belongs at the
  workspace root under build_context/, not inside generated app bundles.

Called by generate_and_download.py after the full files_map is assembled.
Returns a list of human-readable error strings. An empty list means clean.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.runtime.app.paths import (
    APP_DATA_CONTRACT_PATH,
    APP_SECURITY_SECRETS_PATH,
    disallowed_legacy_app_paths,
    noncanonical_app_config_paths,
    noncanonical_app_root_paths,
)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Generic payment-provider secret placeholder used by generated fixtures. Exact
# hosted-processor key fingerprints belong in hosted/private validation packs.
_PAYMENT_PROVIDER_SECRET_LITERAL_RE = re.compile(r"\b(?:payment_provider|provider)_(?:live|test)_[A-Za-z0-9]{10,}")
_PAYMENT_PROVIDER_IMPORT_RE = re.compile(r"(?m)^\s*(?:import\s+payment_provider\b|from\s+payment_provider\s+import\b)")
_RAW_PAYMENT_PROVIDER_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:import\s+(stripe|paddle|paypal|braintree|square)\b|"
    r"from\s+(stripe|paddle|paypal|braintree|square)\s+import\b)",
    re.IGNORECASE,
)
_PAYMENT_PROVIDER_API_KEY_RE = re.compile(r"\bpayment_provider\s*\.\s*api_key\s*=")
_PAYMENT_PROVIDER_REFUND_CALL_RE = re.compile(
    r"\bpayment_provider\s*\.\s*(?:Refund\s*\.\s*create|refunds\s*\.\s*create)\s*\(",
    re.IGNORECASE,
)
_PAYMENT_PROVIDER_REFUNDS_ENDPOINT_RE = re.compile(r"['\"]?/refunds\b")
_HOSTED_INTERNAL_ENDPOINT_RE = re.compile(
    r"['\"]?(/api/modules/(?:mozaikspay|managed_billing|wallet)/[^'\"\s)]*)",
    re.IGNORECASE,
)
_APP_LOCAL_LEDGER_PATH_RE = re.compile(
    r"(?:^|/)(?:(?:token_)?wallet|(?:token_)?usage)_ledger\.(?:py|js|ts|tsx|jsx)$"
    r"|(?:^|/)ledgers?/(?:[^/]*)(?:wallet|usage)(?:[^/]*)\.(?:py|js|ts|tsx|jsx)$",
    re.IGNORECASE,
)
_APP_LOCAL_LEDGER_CODE_RE = re.compile(
    r"\bclass\s+(?:TokenWalletLedger|WalletLedger|RuntimeUsageLedger|UsageLedger|TokenUsageLedger)\b"
    r"|from\s+mozaiksai\.core\.(?:tokens\.wallet|usage\.ledger)\s+import\s+"
    r"(?:TokenWalletLedger|get_token_wallet_ledger|RuntimeUsageLedger|get_runtime_usage_ledger)"
    r"|RuntimeTokenWalletEntries|RuntimeTokenWalletBalances|RuntimeUsageEvents",
    re.IGNORECASE,
)

# File suffixes and compound endings that carry executable or config content.
# Checked via str.endswith so compound extensions like .env.example work.
_SCANNABLE_SUFFIXES = (
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".yaml", ".yml", ".env.example", ".env",
)

_RAW_SECRET_FIELD_KEYS = frozenset(
    {
        "api_key",
        "client_secret",
        "connection_string",
        "password",
        "private_key",
        "raw_value",
        "secret_value",
        "token",
        "value",
        "webhook_secret",
    }
)


def _is_scannable(path: str) -> bool:
    """Return True if this file path should be scanned for forbidden patterns."""
    lpath = path.lower()
    return any(lpath.endswith(s) for s in _SCANNABLE_SUFFIXES)


def _normalized_path(raw_path: str) -> str:
    return str(raw_path or "").replace("\\", "/").strip()


def _normalized_files_map(files_map: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_path, content in files_map.items():
        path = _normalized_path(raw_path)
        if path:
            normalized[path] = str(content)
    return normalized


def _app_manifest_auth_required(files_map: dict[str, str]) -> bool:
    normalized = _normalized_files_map(files_map)
    for path in ("app.json", "app/app.json"):
        raw = normalized.get(path)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("authRequired"), bool):
            return bool(payload["authRequired"])
    return False


def _iter_module_yaml_paths(files_map: dict[str, str]) -> dict[str, str]:
    modules: dict[str, str] = {}
    for raw_path in files_map:
        path = _normalized_path(raw_path)
        pure = PurePosixPath(path)
        if len(pure.parts) != 3 or pure.parts[0] != "modules" or pure.parts[2] != "module.yaml":
            continue
        module_id = str(pure.parts[1]).strip()
        if module_id:
            modules[module_id] = path
    return modules


def _declared_module_id_from_yaml(path: str, content: str) -> str | None:
    try:
        parsed = yaml.safe_load(content) or {}
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    module_block = parsed.get("module") if isinstance(parsed.get("module"), dict) else parsed
    if not isinstance(module_block, dict):
        return None
    module_id = str(module_block.get("id") or "").strip()
    return module_id or None


def _load_data_contract(files_map: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    raw = files_map.get(APP_DATA_CONTRACT_PATH)
    if raw is None:
        return None, None
    try:
        parsed = json.loads(str(raw))
    except Exception as exc:
        return None, f"{APP_DATA_CONTRACT_PATH}: data contract must be valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, f"{APP_DATA_CONTRACT_PATH}: data contract must be a JSON object."
    return parsed, None


def _module_surface_ids(data_contract: dict[str, Any]) -> set[str]:
    module_ids: set[str] = set()
    surfaces = data_contract.get("surfaces")
    if not isinstance(surfaces, list):
        return module_ids

    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        surface_kind = str(surface.get("surface_kind") or "").strip()
        surface_id = str(surface.get("surface_id") or "").strip()
        if surface_kind == "module" and surface_id:
            module_ids.add(surface_id)

        collections = surface.get("collections")
        if not isinstance(collections, list):
            continue
        for collection in collections:
            if not isinstance(collection, dict):
                continue
            ownership = collection.get("ownership") if isinstance(collection.get("ownership"), dict) else {}
            module_id = str(collection.get("module_id") or ownership.get("surface_id") or "").strip()  # type: ignore[union-attr]
            ownership_kind = str(ownership.get("surface_kind") or surface_kind).strip()  # type: ignore[union-attr]
            if ownership_kind == "module" and module_id:
                module_ids.add(module_id)
            elif surface_kind == "module" and surface_id:
                module_ids.add(surface_id)
    return module_ids


def _scan_data_contract_module_alignment(files_map: dict[str, str]) -> list[str]:
    errors: list[str] = []
    normalized_files = _normalized_files_map(files_map)
    data_contract, load_error = _load_data_contract(normalized_files)
    if load_error:
        return [load_error]
    if not data_contract:
        return errors

    module_surface_ids = _module_surface_ids(data_contract)
    if not module_surface_ids:
        return errors

    module_yaml_paths = _iter_module_yaml_paths(normalized_files)
    missing = sorted(module_surface_ids - set(module_yaml_paths))
    if missing:
        errors.append(
            f"{APP_DATA_CONTRACT_PATH} declares module surface(s) without matching module artifacts: "
            f"{missing}. Add modules/{{module_id}}/module.yaml plus the canonical backend files, "
            f"or remove the module surface from {APP_DATA_CONTRACT_PATH}. Page-only bundles must not "
            "declare app business data owned by missing modules."
        )

    for folder_module_id, path in sorted(module_yaml_paths.items()):
        if folder_module_id not in module_surface_ids:
            continue
        declared_module_id = _declared_module_id_from_yaml(path, normalized_files.get(path, ""))
        if declared_module_id and declared_module_id != folder_module_id:
            errors.append(
                f"{path}: module.id {declared_module_id!r} must match folder module id "
                f"{folder_module_id!r} declared by {APP_DATA_CONTRACT_PATH}."
            )
    return errors


def _scan_canonical_app_paths(files_map: dict[str, str]) -> list[str]:
    errors: list[str] = []
    normalized_paths = sorted(_normalized_files_map(files_map))
    legacy_paths = disallowed_legacy_app_paths(normalized_paths)
    if legacy_paths:
        errors.append(
            "Generated app bundle contains removed app paths that are no longer canonical: "
            f"{legacy_paths}. Use {APP_DATA_CONTRACT_PATH}, data/migrations/*.json, "
            f"and {APP_SECURITY_SECRETS_PATH}."
        )

    invalid_config_paths = noncanonical_app_config_paths(normalized_paths)
    if invalid_config_paths:
        errors.append(
            "Generated app bundle contains noncanonical app config files: "
            f"{invalid_config_paths}. Keep app config limited to config/ai.json, "
            "config/shell.json, config/asset_manifest.json, config/targets.json, "
            "and safe integration metadata under config/integrations/. Brownfield "
            "descriptors, credential metadata, and build-time prompting context do "
            "not belong under app/config/."
        )

    invalid_root_paths = noncanonical_app_root_paths(normalized_paths)
    if invalid_root_paths:
        errors.append(
            "Generated app bundle contains files outside the canonical app planes: "
            f"{invalid_root_paths}. Allowed app-root planes are admin, backend, brand, "
            "config, control_plane, data, modules, security, services, and ui, plus "
            "explicit deployment files."
        )
    return errors


def _find_raw_secret_fields(value: Any, path: tuple[str, ...] = ()) -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key or "").strip()
            normalized_key = key.lower().replace("-", "_")
            child_path = (*path, key or "<empty>")
            if normalized_key in _RAW_SECRET_FIELD_KEYS and str(child or "").strip():
                findings.append(".".join(child_path))
            findings.extend(_find_raw_secret_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_find_raw_secret_fields(child, (*path, str(index))))
    return findings


def _scan_security_secret_contract(files_map: dict[str, str]) -> list[str]:
    raw = _normalized_files_map(files_map).get(APP_SECURITY_SECRETS_PATH)
    if raw is None:
        return []
    try:
        parsed = yaml.safe_load(raw) or {}
    except Exception as exc:
        return [f"{APP_SECURITY_SECRETS_PATH}: secrets contract must be valid YAML: {exc}"]
    findings = _find_raw_secret_fields(parsed)
    if not findings:
        return []
    return [
        f"{APP_SECURITY_SECRETS_PATH}: generated secret contracts are names-only and "
        f"must not contain raw credential fields: {findings}. Store raw values only "
        "through the configured secret backend."
    ]


def _pack_id_from_descriptor(pack: Any) -> str:
    if not isinstance(pack, dict):
        return ""
    return str(pack.get("capability_pack_id") or pack.get("id") or pack.get("pack_id") or "").strip()


def _selected_managed_capability_ids(capability_packs: list[dict[str, Any]] | None) -> set[str]:
    ids: set[str] = set()
    for pack in capability_packs or []:
        if not isinstance(pack, dict):
            continue
        if str(pack.get("capability_source") or "").strip() != "managed_capability":
            continue
        pack_id = _pack_id_from_descriptor(pack)
        if pack_id:
            ids.add(pack_id)
    return ids


def _selected_pack_descriptor(
    capability_packs: list[dict[str, Any]] | None,
    pack_id: str,
) -> dict[str, Any] | None:
    for pack in capability_packs or []:
        if not isinstance(pack, dict):
            continue
        if _pack_id_from_descriptor(pack) == pack_id:
            return pack
    return None


def _iter_api_endpoint_literals(content: str) -> list[str]:
    return re.findall(r'["\'](/api/modules/[^"\']+)["\']', content)


def _scan_selected_managed_capability_boundaries(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None,
) -> list[str]:
    managed_capability_ids = _selected_managed_capability_ids(capability_packs)
    if not managed_capability_ids:
        return []

    normalized_files = _normalized_files_map(files_map)
    errors: list[str] = []
    for pack_id in sorted(managed_capability_ids):
        managed_capability_module_prefix = f"modules/{pack_id}/"
        managed_capability_module_paths = [
            path for path in normalized_files if path.startswith(managed_capability_module_prefix)
        ]
        if managed_capability_module_paths:
            errors.append(
                f"Selected managed capability '{pack_id}' must not generate provider internals: "
                f"{managed_capability_module_paths}. Generate app-owned facade modules instead."
            )

        adapter_path = f"services/integrations/{pack_id}_client.py"
        if adapter_path not in normalized_files:
            errors.append(
                f"Selected managed capability '{pack_id}' requires app-owned adapter "
                f"{adapter_path}."
            )

        direct_endpoint_prefix = f"/api/modules/{pack_id}/"
        for path, content in normalized_files.items():
            if not path.startswith("ui/pages/"):
                continue
            for endpoint in _iter_api_endpoint_literals(content):
                if endpoint.startswith(direct_endpoint_prefix):
                    errors.append(
                        f"{path}: page binds directly to managed capability endpoint "
                        f"{endpoint}. Bind pages to an app-owned facade module instead."
                    )
    return errors


def _load_yaml_mapping_from_file(
    files_map: dict[str, str],
    path: str,
) -> tuple[dict[str, Any] | None, str | None]:
    raw = files_map.get(path)
    if raw is None:
        return None, None
    try:
        parsed = yaml.safe_load(str(raw)) or {}
    except Exception as exc:
        return None, f"{path}: must be valid YAML: {exc}"
    if not isinstance(parsed, dict):
        return None, f"{path}: must be a YAML object."
    return parsed, None


def _module_actions_from_yaml(path: str, content: str) -> set[str]:
    parsed, error = _load_yaml_mapping_from_file({path: content}, path)
    if error or not isinstance(parsed, dict):
        return set()
    actions = parsed.get("actions")
    if not isinstance(actions, list):
        return set()
    return {
        str(action.get("id") or "").strip()
        for action in actions
        if isinstance(action, dict) and str(action.get("id") or "").strip()
    }


def _page_endpoint_literals(content: str) -> list[str]:
    return re.findall(r'["\']?(/api/modules/[^"\'\s]+)', content)


def _validate_subscriptions_contract(
    files_map: dict[str, str],
    *,
    path: str = "config/subscriptions.yaml",
) -> list[str]:
    raw, error = _load_yaml_mapping_from_file(files_map, path)
    if error:
        return [error]
    if not isinstance(raw, dict):
        return [f"{path}: generated SaaS apps must include config/subscriptions.yaml."]

    try:
        from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

        config = SubscriptionsConfig.model_validate(raw)
    except Exception as exc:
        return [f"{path}: invalid subscriptions contract: {exc}"]

    errors: list[str] = []
    plans_with_token_allowances = [
        plan.plan_id for plan in config.plans if plan.token_allowances
    ]
    plans_with_usage_limits = [
        plan.plan_id for plan in config.plans if plan.usage_limits
    ]
    if not config.token_wallets:
        if plans_with_token_allowances:
            errors.append(f"{path}: token_allowances require declared token_wallets.")
        if config.top_up_products:
            errors.append(f"{path}: top_up_products require declared token_wallets.")
        return errors

    if not (
        plans_with_token_allowances
        or plans_with_usage_limits
        or config.top_up_products
        or config.usage_charge_policies
    ):
        errors.append(
            f"{path}: token_wallets must be emitted only for generated apps that sell "
            "AI usage, credits, quotas, top-ups, or usage-charge estimates."
        )
    return errors


def _scan_mozaikspay_saas_contract(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None,
) -> list[str]:
    pack = _selected_pack_descriptor(capability_packs, "mozaikspay")
    if not pack or str(pack.get("capability_source") or "").strip() != "managed_capability":
        return []

    normalized_files = _normalized_files_map(files_map)
    errors: list[str] = []

    required_paths = {
        "config/subscriptions.yaml",
        "services/integrations/mozaikspay_client.py",
        "modules/billing_portal/module.yaml",
        "modules/billing_portal/backend/handler.py",
        "modules/billing_portal/backend/service.py",
        "modules/billing_portal/backend/schemas.py",
        "ui/pages/billing.yaml",
        "ui/pages/usage.yaml",
    }
    missing = sorted(path for path in required_paths if path not in normalized_files)
    if missing:
        errors.append(
            "Selected mozaikspay SaaS capability requires deterministic generated app files: "
            f"{missing}."
        )

    errors.extend(_validate_subscriptions_contract(normalized_files))

    client_content = normalized_files.get("services/integrations/mozaikspay_client.py", "")
    if client_content:
        required_markers = {
            "_CONNECTOR_SERVICE": "_CONNECTOR_SERVICE",
            "mozaikspay": "mozaikspay",
            "ConnectorStore": "ConnectorStore",
            "get_connector_vault_backend": "get_connector_vault_backend",
            "MOZAIKSPAY_API_BASE": "MOZAIKSPAY_API_BASE",
            "MOZAIKSPAY_API_KEY": "MOZAIKSPAY_API_KEY",
        }
        missing_markers = [
            label for label, marker in required_markers.items() if marker not in client_content
        ]
        if missing_markers:
            errors.append(
                "services/integrations/mozaikspay_client.py must resolve the app-scoped "
                f"mozaikspay connector and env fallback; missing markers: {missing_markers}."
            )

    env_example = normalized_files.get("env.example", "")
    if env_example:
        required_env_handles = {
            "MOZAIKS_APP_URL",
            "MOZAIKSPAY_API_BASE",
            "MOZAIKSPAY_CLIENT_ID",
            "MOZAIKSPAY_CLIENT_SECRET",
            "MOZAIKSPAY_API_KEY",
        }
        missing_env_handles = sorted(
            name for name in required_env_handles if f"{name}=" not in env_example
        )
        if missing_env_handles:
            errors.append(
                "env.example must document the MozaiksPay connector/env fallback handles "
                f"for generated SaaS apps: {missing_env_handles}."
            )

    module_content = normalized_files.get("modules/billing_portal/module.yaml", "")
    if module_content:
        actions = _module_actions_from_yaml("modules/billing_portal/module.yaml", module_content)
        required_actions = {"get_subscription_status", "get_usage_status", "open_billing_portal"}
        missing_actions = sorted(required_actions - actions)
        if missing_actions:
            errors.append(
                "modules/billing_portal/module.yaml must expose app-owned SaaS billing facade "
                f"actions: {missing_actions}."
            )
        declared_module_id = _declared_module_id_from_yaml(
            "modules/billing_portal/module.yaml",
            module_content,
        )
        if declared_module_id != "billing_portal":
            errors.append("modules/billing_portal/module.yaml must declare module.id 'billing_portal'.")

    service_content = normalized_files.get("modules/billing_portal/backend/service.py", "")
    if service_content:
        service_markers = {
            "MozaiksPayClient": "MozaiksPayClient",
            "get_subscription_status_for_scope": "get_subscription_status_for_scope",
            "get_runtime_ai_usage": "get_runtime_ai_usage",
            "create_billing_portal_session": "create_billing_portal_session",
        }
        missing_service_markers = [
            label for label, marker in service_markers.items() if marker not in service_content
        ]
        if missing_service_markers:
            errors.append(
                "modules/billing_portal/backend/service.py must delegate subscription, usage, "
                f"and portal operations through MozaiksPayClient; missing markers: {missing_service_markers}."
            )
        forbidden_terms = [
            "assign_plan",
            "cancel_subscription",
            "expire_subscription",
            "record_usage",
            "upsert_grants",
            "PaymentProviderBillingClient",
            "import payment_provider",
            "from payment_provider import",
        ]
        leaked_terms = sorted(term for term in forbidden_terms if term in service_content)
        if leaked_terms:
            errors.append(
                "modules/billing_portal/backend/service.py must not expose managed billing internals "
                f"or provider credentials: {leaked_terms}."
            )

    page_requirements = {
        "ui/pages/billing.yaml": {
            "/api/modules/billing_portal/get_subscription_status",
            "/api/modules/billing_portal/open_billing_portal",
        },
        "ui/pages/usage.yaml": {
            "/api/modules/billing_portal/get_usage_status",
        },
    }
    for page_path, required_endpoints in page_requirements.items():
        content = normalized_files.get(page_path, "")
        if not content:
            continue
        endpoints = set(_page_endpoint_literals(content))
        missing_endpoints = sorted(required_endpoints - endpoints)
        if missing_endpoints:
            errors.append(
                f"{page_path}: generated SaaS page must bind through billing_portal facade endpoints: "
                f"{missing_endpoints}."
            )
        direct_forbidden = [
            endpoint for endpoint in endpoints
            if endpoint.startswith((
                "/api/modules/mozaikspay/",
                "/api/modules/wallet/",
            ))
        ]
        if direct_forbidden:
            errors.append(
                f"{page_path}: generated SaaS page must not bind directly to managed/provider modules: "
                f"{sorted(direct_forbidden)}."
            )

    return errors


def _scan_deployment_artifacts_contract(files_map: dict[str, str]) -> list[str]:
    normalized_files = _normalized_files_map(files_map)
    deployment_artifacts: dict[str, str] = {
        path: normalized_files[path]
        for path in (
            "Dockerfile",
            "env.example",
            "deployment.manifest.json",
            ".github/workflows/deploy.yml",
        )
        if path in normalized_files
    }
    try:
        from .deployment_contract import validate_generated_deployment_bundle

        deployment_errors = validate_generated_deployment_bundle(
            deployment_artifacts,
            include_dockerfiles=True,
            include_workflow=".github/workflows/deploy.yml" in deployment_artifacts,
        )
    except Exception as exc:
        deployment_errors = [f"deployment artifact validation failed: {exc}"]
    if deployment_errors:
        return [
            "Generated app bundle must include valid provider-neutral deployment artifacts: "
            f"{deployment_errors}."
        ]
    return []


def _scan_auth_deployment_contract(files_map: dict[str, str]) -> list[str]:
    """Require deploy-time JWT/OIDC contract metadata for authenticated apps."""
    if not _app_manifest_auth_required(files_map):
        return []

    normalized_files = _normalized_files_map(files_map)
    errors: list[str] = []

    env_example = normalized_files.get("env.example", "")
    required_env_handles = {
        "AUTH_ENABLED",
        "AUTH_PROVIDER",
        "AUTH_AUDIENCE",
        "MOZAIKS_OIDC_DISCOVERY_URL",
        "MOZAIKS_OIDC_AUTHORITY",
        "AUTH_ISSUER",
        "AUTH_JWKS_URL",
        "VITE_OIDC_CLIENT_ID",
        "VITE_OIDC_REDIRECT_URI",
    }
    missing_env_handles = sorted(
        name for name in required_env_handles if f"{name}=" not in env_example
    )
    if missing_env_handles:
        errors.append(
            "Authenticated generated apps must document provider-neutral OIDC/JWT "
            "runtime and frontend env handles in env.example: "
            f"{missing_env_handles}."
        )

    manifest_text = normalized_files.get("deployment.manifest.json", "")
    if manifest_text:
        try:
            manifest = json.loads(manifest_text)
        except Exception as exc:
            errors.append(f"deployment.manifest.json must be valid JSON for auth contract scan: {exc}")
            return errors

        if not isinstance(manifest, dict):
            errors.append("deployment.manifest.json must be an object for auth contract scan.")
            return errors

        auth = manifest.get("auth")
        if not isinstance(auth, dict) or auth.get("required") is not True:
            errors.append(
                "Authenticated generated apps must carry auth.required=true in "
                "deployment.manifest.json."
            )

        required_env = {str(item) for item in manifest.get("required_env") or []}
        missing_required_env = sorted(
            name for name in ("AUTH_ENABLED", "AUTH_PROVIDER") if name not in required_env
        )
        if missing_required_env:
            errors.append(
                "Authenticated generated apps must include runtime auth required_env entries "
                f"in deployment.manifest.json: {missing_required_env}."
            )

    return errors


def scan_generated_bundle(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None = None,
    require_deployment_artifacts: bool = False,
) -> list[str]:
    """Scan files_map for forbidden patterns.

    Returns a list of human-readable error strings.
    An empty list means the bundle is clean and safe to deliver.

    Checks applied per file type:
    - All scannable files: raw provider secret key literals.
    """
    errors: list[str] = []
    errors.extend(_scan_canonical_app_paths(files_map))
    errors.extend(_scan_security_secret_contract(files_map))
    errors.extend(_scan_data_contract_module_alignment(files_map))
    errors.extend(
        _scan_selected_managed_capability_boundaries(
            files_map,
            capability_packs=capability_packs,
        )
    )
    errors.extend(
        _scan_mozaikspay_saas_contract(
            files_map,
            capability_packs=capability_packs,
        )
    )
    if require_deployment_artifacts:
        errors.extend(_scan_deployment_artifacts_contract(files_map))
        errors.extend(_scan_auth_deployment_contract(files_map))

    for path, content in files_map.items():
        if not isinstance(path, str) or not isinstance(content, str):
            continue

        if not _is_scannable(path):
            continue

        # ---- checks that apply to all scannable file types ----

        if _PAYMENT_PROVIDER_SECRET_LITERAL_RE.search(content):
            errors.append(
                f"{path}: contains a raw provider secret key literal "
                "in generated source. Generated apps must not embed "
                "raw credentials. Store credential values only through the "
                "configured secret backend."
            )

        if _PAYMENT_PROVIDER_API_KEY_RE.search(content):
            errors.append(
                f"{path}: assigns payment_provider.api_key directly. Generated apps must "
                "resolve provider credentials through the configured secret "
                "backend or managed capability adapter boundary."
            )

        if _PAYMENT_PROVIDER_REFUND_CALL_RE.search(content):
            errors.append(
                f"{path}: calls payment provider refunds APIs directly. Generated apps "
                "must route refund mutations through an app-owned facade or "
                "managed payment adapter."
            )

        if _PAYMENT_PROVIDER_REFUNDS_ENDPOINT_RE.search(content):
            errors.append(
                f"{path}: references a refunds endpoint (/refunds) directly. Generated apps must "
                "route refund mutations through an app-owned facade or managed "
                "payment adapter."
            )

        hosted_internal_refs = sorted(set(_HOSTED_INTERNAL_ENDPOINT_RE.findall(content)))
        if hosted_internal_refs:
            errors.append(
                f"{path}: calls hosted managed-capability internals directly: "
                f"{hosted_internal_refs}. Generated apps must bind through app-owned facade modules."
            )

        if _APP_LOCAL_LEDGER_PATH_RE.search(_normalized_path(path)) or _APP_LOCAL_LEDGER_CODE_RE.search(content):
            errors.append(
                f"{path}: contains app-local token wallet or usage ledger code. Generated apps must "
                "use OSS runtime token wallet and usage endpoints instead of duplicate ledgers."
            )

        if path.lower().endswith(".py") and _PAYMENT_PROVIDER_IMPORT_RE.search(content):
            errors.append(
                f"{path}: imports the payment provider SDK directly. Generated apps must "
                "use generated service boundaries or managed capability adapter clients "
                "instead of provider SDKs in app business code."
            )

        if path.lower().endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
            for raw_provider_import in _RAW_PAYMENT_PROVIDER_IMPORT_RE.finditer(content):
                provider_name = raw_provider_import.group(1) or raw_provider_import.group(2) or ""
                errors.append(
                    f"{path}: imports raw payment provider SDK {provider_name!r}. Generated apps must "
                    "use app-owned facade modules and managed/provider-neutral adapter clients."
                )

    return errors


__all__ = ["scan_generated_bundle"]

