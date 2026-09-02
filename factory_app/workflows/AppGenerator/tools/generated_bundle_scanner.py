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
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    ManagedCapabilityTemplateError,
    resolve_declared_pack_output_paths,
)
from mozaiksai.core.runtime.app.layout_registry import (
    ExtensionSlot,
    LayoutExtension,
    PathScope,
    build_app_layout_registry,
)
from mozaiksai.core.runtime.app.layout_validation import (
    filter_layout_scannable_file_map,
    layout_extensions_from_selected_packs,
    layout_validation_errors,
    validate_file_map_layout,
)
from mozaiksai.core.runtime.app.page_schema import (
    PageSchemaValidationError,
    validate_page_schema,
)
from mozaiksai.core.runtime.app.paths import (
    APP_AUTH_CONFIG_PATH,
    APP_DATA_CONTRACT_PATH,
    APP_SECURITY_SECRETS_PATH,
    disallowed_legacy_app_paths,
    noncanonical_app_config_paths,
    noncanonical_app_root_paths,
    unsafe_app_paths,
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
# Cloud provider SDK imports are forbidden in generated app bundles.
# Generated apps must route all cloud operations through the bounded MozaiksCloud
# sub-clients; they must not import Azure, Cloudflare, or GitHub SDKs directly.
_RAW_CLOUD_PROVIDER_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:import\s+(azure(?:\.[a-zA-Z0-9_.]+)?|cloudflare)\b|"
    r"from\s+(azure(?:\.[a-zA-Z0-9_.]+)?|cloudflare)\s+import\b)",
    re.IGNORECASE,
)

_MANAGED_SETUP_RAW_PROVIDER_ENV_RE = re.compile(
    r"\b(?:STRIPE|PADDLE|PAYPAL|BRAINTREE|SQUARE)_[A-Z0-9_]*"
    r"(?:SECRET|KEY|TOKEN|PRIVATE|WEBHOOK|CLIENT_ID|PUBLISHABLE)[A-Z0-9_]*\b"
)
_MANAGED_SETUP_PROVIDER_ROUTE_RE = re.compile(
    r"(?:^|[\"'\s:=])/?(?:api/)?(?:webhooks/(stripe|paddle|paypal|braintree|square)\b|"
    r"(stripe|paddle|paypal|braintree|square)/webhooks?\b|"
    r"(stripe|paddle|paypal|braintree|square)/(?:checkout|payment|billing)\b)",
    re.IGNORECASE,
)
_MANAGED_SETUP_PROVIDER_MECHANIC_RE = re.compile(
    r"\b(?:stripe|paddle|paypal|braintree|square)\s*\.\s*"
    r"(?:checkout|webhooks|PaymentIntent|Customer|Subscription|Refund|refunds)\b|"
    r"\bStripe-Signature\b|"
    r"\brequire\s*\(\s*[\"'](?:stripe|paddle|paypal|braintree|square)[\"']\s*\)",
    re.IGNORECASE,
)
_PAYMENT_PROVIDER_API_KEY_RE = re.compile(r"\bpayment_provider\s*\.\s*api_key\s*=")
_PAYMENT_PROVIDER_REFUND_CALL_RE = re.compile(
    r"\bpayment_provider\s*\.\s*(?:Refund\s*\.\s*create|refunds\s*\.\s*create)\s*\(",
    re.IGNORECASE,
)
_PAYMENT_PROVIDER_REFUNDS_ENDPOINT_RE = re.compile(r"['\"]?/refunds\b")
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
    ".yaml", ".yml", ".env.example", ".env.staging.example",
    ".env.production.example", ".env",
    # Pack-declared workspace scripts ship to customers and must never carry
    # raw credentials or provider leaks.
    ".ps1", ".sh",
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

_AUTH_MODES = frozenset(
    {
        "brokered_oidc",
        "public_self_signup",
        "private_workspace",
        "enterprise_sso",
        "multi_provider",
    }
)
_AUTH_LOGIN_METHOD_KINDS = frozenset(
    {
        "oidc_redirect",
        "create_account",
        "enterprise_sso",
    }
)

# Canonical action api_surface values — controls HTTP exposure posture.
# Must match the typed literal in structured_outputs.yaml ModuleAction.api_surface
# and the runtime ActionDef.api_surface field.
_CANONICAL_API_SURFACE_VALUES = frozenset({
    "public",
    "public_readonly",
    "internal",
    "admin_internal",
})

# Canonical reaction target kinds — must match file_contracts.yaml and the
# mozaiks.reactions.v1 schema.  service_adapter is intentionally present here
# and in the runtime loader but absent from structured_outputs.yaml: it is a
# pack-only extension (capability packs can generate service_adapter reactions
# via templates, but the AppGenerator LLM should not produce them directly).
_CANONICAL_REACTION_TARGET_KINDS = frozenset({
    "handler",
    "capability",
    "notification",
    "service_adapter",
})

# Platform-provided event namespaces — events in these namespaces are NOT
# declared in the bundle's events.yaml and must be skipped during closure checks.
_PLATFORM_EVENT_NAMESPACES = ("hosted.", "platform.", "mozaiks.")

# Shell-built-in component names registered in chat-ui/src/registry/coreComponents.js.
# These components are always available in the Mozaiks shell without a custom JSX file.
# Route manifest entries referencing these names do NOT require a ui/pages/custom/*.jsx file.
_SHELL_CORE_COMPONENTS = frozenset({
    "ChatPage",
    "SchemaPage",
    "LauncherScreen",
    "ConfirmScreen",
    "ProfilePage",
    "WorkflowCompletion",
    "TokenStatusTab",
    "AdminMyUsagePanel",
    "AdminAppUsagePanel",
})


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


def _scan_canonical_app_paths(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None = None,
) -> list[str]:
    errors: list[str] = []
    unsafe_paths = unsafe_app_paths(files_map)
    if unsafe_paths:
        errors.append(
            "Generated app bundle contains absolute or traversal paths outside the app root: "
            f"{unsafe_paths}. Every generated path must be app-root-relative."
        )
    normalized_paths = sorted(_normalized_files_map(files_map))
    try:
        declared_pack_paths = resolve_declared_pack_output_paths(capability_packs)
    except ManagedCapabilityTemplateError as exc:
        return [f"Selected CapabilityPack output contract is invalid: {exc}"]
    legacy_paths = disallowed_legacy_app_paths(normalized_paths)
    if legacy_paths:
        errors.append(
            "Generated app bundle contains removed app paths that are no longer canonical: "
            f"{legacy_paths}. Use {APP_DATA_CONTRACT_PATH}, data/migrations/*.json, "
            f"and {APP_SECURITY_SECRETS_PATH}."
        )

    invalid_config_paths = sorted(
        set(noncanonical_app_config_paths(normalized_paths)) - declared_pack_paths
    )
    if invalid_config_paths:
        errors.append(
            "Generated app bundle contains noncanonical app config files: "
            f"{invalid_config_paths}. Keep app config limited to config/ai.json, "
            "config/shell.json, config/asset_manifest.json, config/targets.json, "
            "and safe integration metadata under config/integrations/. Brownfield "
            "descriptors, credential metadata, and build-time prompting context do "
            "not belong under app/config/."
        )

    invalid_root_paths = sorted(
        set(noncanonical_app_root_paths(normalized_paths)) - declared_pack_paths
    )
    if invalid_root_paths:
        errors.append(
            "Generated app bundle contains files outside the canonical app planes: "
            f"{invalid_root_paths}. Allowed app-root planes are admin, backend, brand, "
            "config, dashboard, data, modules, refinement_harness, security, services, "
            "and ui, plus provenance.yaml and explicit deployment files."
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


def _packs_providing(
    capability_packs: list[dict[str, Any]] | None,
    capability: str,
) -> list[dict[str, Any]]:
    """Return pack descriptors that declare they provide the given capability.

    Checks the in-memory ``provides_capabilities`` list first; if absent,
    falls back to loading ``provides_capabilities`` from
    ``pack_source_path/contract.yaml``.  This lets operator packs declare
    their capabilities without the OSS scanner hardcoding any pack name.
    """
    result: list[dict[str, Any]] = []
    for pack in capability_packs or []:
        if not isinstance(pack, dict):
            continue
        inline = pack.get("provides_capabilities")
        if isinstance(inline, list) and capability in inline:
            result.append(pack)
            continue
        raw_source_path = str(pack.get("pack_source_path") or "").strip()
        if not raw_source_path:
            continue
        contract_path = Path(raw_source_path) / "contract.yaml"
        try:
            contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if isinstance(contract, dict):
            from_contract = contract.get("provides_capabilities") or []
            if isinstance(from_contract, list) and capability in from_contract:
                result.append(pack)
    return result


def _normalized_forbidden_output_prefix(value: Any) -> str:
    raw = value
    if isinstance(value, dict):
        raw = value.get("path_prefix") or value.get("path") or ""
    prefix = _normalized_path(str(raw or "")).lstrip("/")
    if prefix.startswith("app/"):
        prefix = prefix.removeprefix("app/")
    return prefix.rstrip("/")


def _forbidden_output_prefixes_from_pack(pack: dict[str, Any]) -> list[str]:
    prefixes: list[str] = []

    def add_many(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            prefix = _normalized_forbidden_output_prefix(item)
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)

    add_many(pack.get("forbidden_outputs"))

    raw_source_path = str(pack.get("pack_source_path") or "").strip()
    if raw_source_path:
        contract_path = Path(raw_source_path) / "contract.yaml"
        try:
            contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
        except Exception:
            contract = {}
        if isinstance(contract, dict):
            add_many(contract.get("forbidden_outputs"))

    return sorted(prefixes)


def _path_matches_prefix(path: str, prefix: str) -> bool:
    normalized_path = _normalized_path(path).rstrip("/")
    normalized_prefix = _normalized_path(prefix).rstrip("/")
    return normalized_path == normalized_prefix or normalized_path.startswith(f"{normalized_prefix}/")


def _iter_api_endpoint_literals(content: str) -> list[str]:
    return re.findall(r'(?:["\']|:\s*)(/api/modules/[^"\'\s]+)', content)


def _load_integration_requirements(
    files_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized_files = _normalized_files_map(files_map)
    requirements: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in (
        "config/integrations.yaml",
        "app/config/integrations.yaml",
    ):
        raw = normalized_files.get(path)
        if raw is None:
            continue
        try:
            parsed = yaml.safe_load(raw) or {}
        except Exception as exc:
            errors.append(f"{path}: integration contract must be valid YAML: {exc}")
            continue
        if not isinstance(parsed, dict):
            errors.append(f"{path}: integration contract must be an object.")
            continue
        raw_requirements = parsed.get("requirements")
        if raw_requirements is None:
            raw_requirements = parsed.get("integrations")
        if raw_requirements is None:
            continue
        if not isinstance(raw_requirements, list):
            errors.append(f"{path}: integration requirements must be a list.")
            continue
        requirements.extend(item for item in raw_requirements if isinstance(item, dict))
    return requirements, errors


def _requirement_uses_managed_lane(requirement: dict[str, Any]) -> bool:
    allowed = requirement.get("allowed_setup_lanes")
    lanes: set[str] = set()
    if isinstance(allowed, list):
        lanes.update(str(item or "").strip() for item in allowed)
    preferred = str(requirement.get("preferred_setup_lane") or "").strip()
    if preferred:
        lanes.add(preferred)
    return "managed" in lanes


def _managed_requirement_labels(requirements: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for requirement in requirements:
        if not _requirement_uses_managed_lane(requirement):
            continue
        label = str(
            requirement.get("integration_id")
            or requirement.get("service")
            or requirement.get("provider")
            or "<unknown>"
        ).strip()
        if label and label not in labels:
            labels.append(label)
    return sorted(labels)


def _scan_managed_setup_provider_leaks(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None,
) -> list[str]:
    requirements, errors = _load_integration_requirements(files_map)
    managed_labels = _managed_requirement_labels(requirements)
    selected_managed = sorted(_selected_managed_capability_ids(capability_packs))
    if not managed_labels and not selected_managed:
        return errors

    context = sorted(set(managed_labels + selected_managed))
    context_label = ", ".join(context) if context else "managed setup"
    normalized_files = _normalized_files_map(files_map)
    for path, content in sorted(normalized_files.items()):
        if not isinstance(content, str):
            continue
        if _MANAGED_SETUP_RAW_PROVIDER_ENV_RE.search(content):
            errors.append(
                f"{path}: managed setup lane ({context_label}) must not expose raw "
                "payment-provider environment handles. Use the managed capability "
                "client contract and names-only Mozaiks-managed handles instead."
            )
        if _MANAGED_SETUP_PROVIDER_ROUTE_RE.search(content):
            errors.append(
                f"{path}: managed setup lane ({context_label}) must not generate raw "
                "payment-provider webhook or checkout routes. Provider callbacks "
                "belong in the managed/hosted product boundary."
            )
        if _MANAGED_SETUP_PROVIDER_MECHANIC_RE.search(content):
            errors.append(
                f"{path}: managed setup lane ({context_label}) must not contain raw "
                "payment-provider SDK mechanics. Generated apps should call an "
                "app-owned facade or managed capability client."
            )
    return errors


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
        pack = _selected_pack_descriptor(capability_packs, pack_id) or {"id": pack_id}
        forbidden_prefixes = _forbidden_output_prefixes_from_pack(pack)
        forbidden_output_paths = {
            path
            for path in normalized_files
            for prefix in forbidden_prefixes
            if _path_matches_prefix(path, prefix)
        }
        if forbidden_output_paths:
            errors.append(
                f"Selected managed capability '{pack_id}' declares forbidden output prefixes "
                f"{forbidden_prefixes}; generated bundle contains forbidden paths: "
                f"{sorted(forbidden_output_paths)}."
            )

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
            if not _is_scannable(path):
                continue
            for endpoint in _iter_api_endpoint_literals(content):
                if endpoint.startswith(direct_endpoint_prefix):
                    errors.append(
                        f"{path}: calls managed capability endpoint "
                        f"{endpoint} directly. Use an app-owned facade module instead."
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
    return re.findall(r'(?:["\']|:\s*)(/api/modules/[^"\'\s]+)', content)


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


def _scan_token_wallets_require_mozaikspay(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None,
) -> list[str]:
    """Generated apps that declare top_up_products must select the mozaikspay pack.

    top_up_products requires a hosted payment processor to create checkout sessions.
    The OSS runtime ledger can track balances without MozaiksPay, but token top-ups
    need a billing provider. This gate enforces the selection so the billing_portal
    module is generated and the checkout/top-up surfaces are wired correctly.

    Note: token_wallets alone without top_up_products is valid for self-hosted OSS
    apps that use entitlement_dispatch for plan enforcement and do not sell top-ups.
    """
    normalized_files = _normalized_files_map(files_map)
    subs_path = "config/subscriptions.yaml"
    raw, _ = _load_yaml_mapping_from_file(normalized_files, subs_path)
    if not isinstance(raw, dict):
        return []

    top_up_products = raw.get("top_up_products")
    if not top_up_products or not isinstance(top_up_products, list) or len(top_up_products) == 0:
        return []

    # top_up_products declared — mozaikspay must be selected as a managed_capability
    pack = _selected_pack_descriptor(capability_packs, "mozaikspay")
    if pack and str(pack.get("capability_source") or "").strip() == "managed_capability":
        return []

    return [
        f"{subs_path}: declares top_up_products but the mozaikspay managed capability pack "
        "is not selected. Token top-up products require a hosted billing provider to create "
        "checkout sessions. Add mozaikspay to AppBuildPlan.capability_packs with "
        "capability_source: managed_capability."
    ]


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
        "ui/pages/pricing.yaml",
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

    env_example = normalized_files.get(".env.example", "")
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
                ".env.example must document the MozaiksPay connector/env fallback handles "
                f"for generated SaaS apps: {missing_env_handles}."
            )

    module_content = normalized_files.get("modules/billing_portal/module.yaml", "")
    if module_content:
        actions = _module_actions_from_yaml("modules/billing_portal/module.yaml", module_content)
        required_actions = {
            "get_subscription_status",
            "get_usage_status",
            "list_plans",
            "open_billing_portal",
        }
        missing_actions = sorted(required_actions - actions)
        if missing_actions:
            errors.append(
                "modules/billing_portal/module.yaml must expose app-owned SaaS billing facade "
                f"actions: {missing_actions}."
            )
        parsed_module, module_parse_error = _load_yaml_mapping_from_file(
            {"modules/billing_portal/module.yaml": module_content},
            "modules/billing_portal/module.yaml",
        )
        if not module_parse_error and isinstance(parsed_module, dict):
            for action in parsed_module.get("actions") or []:
                if not isinstance(action, dict) or action.get("id") != "list_plans":
                    continue
                surface = str(action.get("api_surface") or "").strip()
                if surface not in {"public", "public_readonly"} or action.get("permissions"):
                    errors.append(
                        "modules/billing_portal/module.yaml: 'list_plans' is the canonical "
                        "anonymous data source for the public /pricing page and must declare "
                        "api_surface public_readonly with no permissions."
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
        "ui/pages/pricing.yaml": {
            "/api/modules/billing_portal/list_plans",
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


def _scan_mozaiks_cloud_connector_contract(
    files_map: dict[str, Any],
    *,
    capability_packs: list[dict[str, Any]] | None,
) -> list[str]:
    """Validate Mozaiks Cloud connector contract when the pack is selected.

    When mozaiks_cloud is selected as managed_capability:
    - All four client files must be present.
    - The transport client must resolve credentials from ConnectorStore/env.
    - Both facade module YAMLs must declare expected actions.
    - No Azure SDK or Cloudflare SDK imports may appear anywhere in the bundle.
    When the pack is NOT selected, this check is a no-op — absence is proven
    by the fact that the templates were never materialized.
    """
    pack = _selected_pack_descriptor(capability_packs, "mozaiks_cloud")
    if not pack or str(pack.get("capability_source") or "").strip() != "managed_capability":
        return []

    normalized_files = _normalized_files_map(files_map)
    errors: list[str] = []

    required_paths = {
        "services/integrations/mozaiks_cloud_client.py",
        "services/integrations/mozaiks_cloud_deployment_client.py",
        "services/integrations/mozaiks_cloud_domain_client.py",
        "modules/cloud_deployment/module.yaml",
        "modules/cloud_domain/module.yaml",
    }
    missing = sorted(path for path in required_paths if path not in normalized_files)
    if missing:
        errors.append(
            "Selected mozaiks_cloud connector capability requires deterministic generated app "
            f"files: {missing}."
        )

    client_content = normalized_files.get("services/integrations/mozaiks_cloud_client.py", "")
    if client_content:
        required_markers = {
            "_CONNECTOR_SERVICE": "_CONNECTOR_SERVICE",
            "mozaiks_cloud": "mozaiks_cloud",
            "ConnectorStore": "ConnectorStore",
            "get_connector_vault_backend": "get_connector_vault_backend",
            "MOZAIKS_CLOUD_API_BASE": "MOZAIKS_CLOUD_API_BASE",
            "MOZAIKS_CLOUD_API_KEY": "MOZAIKS_CLOUD_API_KEY",
        }
        missing_markers = [
            label for label, marker in required_markers.items() if marker not in client_content
        ]
        if missing_markers:
            errors.append(
                "services/integrations/mozaiks_cloud_client.py must resolve the app-scoped "
                f"mozaiks_cloud connector and env fallback; missing markers: {missing_markers}."
            )

    deployment_module = normalized_files.get("modules/cloud_deployment/module.yaml", "")
    if deployment_module:
        actions = _module_actions_from_yaml("modules/cloud_deployment/module.yaml", deployment_module)
        required_actions = {
            "submit_deployment",
            "get_deployment_status",
            "get_environment_endpoints",
            "request_rollback",
        }
        missing_actions = sorted(required_actions - actions)
        if missing_actions:
            errors.append(
                "modules/cloud_deployment/module.yaml must expose cloud_deployment facade "
                f"actions: {missing_actions}."
            )
        declared_id = _declared_module_id_from_yaml(
            "modules/cloud_deployment/module.yaml", deployment_module
        )
        if declared_id != "cloud_deployment":
            errors.append(
                "modules/cloud_deployment/module.yaml must declare module.id 'cloud_deployment'."
            )

    domain_module = normalized_files.get("modules/cloud_domain/module.yaml", "")
    if domain_module:
        actions = _module_actions_from_yaml("modules/cloud_domain/module.yaml", domain_module)
        required_actions = {
            "connect_domain",
            "get_domain_verification",
            "get_dns_instructions",
            "request_domain_activation",
            "get_domain_status",
            "disconnect_domain",
        }
        missing_actions = sorted(required_actions - actions)
        if missing_actions:
            errors.append(
                "modules/cloud_domain/module.yaml must expose cloud_domain facade "
                f"actions: {missing_actions}."
            )
        declared_id = _declared_module_id_from_yaml(
            "modules/cloud_domain/module.yaml", domain_module
        )
        if declared_id != "cloud_domain":
            errors.append(
                "modules/cloud_domain/module.yaml must declare module.id 'cloud_domain'."
            )

    return errors


def _entitlement_gates_from_module_yaml(path: str, content: str) -> set[str]:
    parsed, error = _load_yaml_mapping_from_file({path: content}, path)
    if error or not isinstance(parsed, dict):
        return set()
    gates: set[str] = set()
    for action in (parsed.get("actions") or []):
        if not isinstance(action, dict):
            continue
        gate = str(action.get("entitlement_gate") or "").strip()
        if gate:
            gates.add(gate)
    return gates


def _entitlement_gate_map_from_module_yaml(path: str, content: str) -> dict[str, str]:
    """Return {action_id: entitlement_gate} for every gated action in module.yaml."""
    parsed, error = _load_yaml_mapping_from_file({path: content}, path)
    if error or not isinstance(parsed, dict):
        return {}
    result: dict[str, str] = {}
    for action in (parsed.get("actions") or []):
        if not isinstance(action, dict):
            continue
        gate = str(action.get("entitlement_gate") or "").strip()
        action_id = str(action.get("id") or "").strip()
        if gate and action_id:
            result[action_id] = gate
    return result


def _subscriptions_config_from_yaml(path: str, content: str) -> tuple[Any | None, str | None]:
    raw, error = _load_yaml_mapping_from_file({path: content}, path)
    if error:
        return None, error
    try:
        from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

        return SubscriptionsConfig.model_validate(raw), None
    except Exception as exc:
        return None, f"{path}: invalid subscriptions contract: {exc}"


def _capability_ids_from_subscriptions_config(config: Any) -> set[str]:
    """Extract capability_ids using the canonical subscriptions loader output."""
    capability_ids: set[str] = set()
    for plan in (getattr(config, "plans", None) or []):
        capability_ids.update(str(cap).strip() for cap in (plan.capabilities or []) if str(cap).strip())
    for product in (getattr(config, "products", None) or []):
        for plan in (getattr(product, "plans", None) or []):
            capability_ids.update(str(cap).strip() for cap in (plan.capabilities or []) if str(cap).strip())
    return capability_ids


def _capability_ids_from_subscriptions_yaml(content: str) -> set[str]:
    """Extract plan-granted capability_ids from canonical subscriptions parsing."""
    config, error = _subscriptions_config_from_yaml("config/subscriptions.yaml", content)
    if error or config is None:
        return set()
    return _capability_ids_from_subscriptions_config(config)


def _scan_self_hosted_entitlement_dispatch_contract(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None,
) -> list[str]:
    """Validate the self-hosted entitlement_dispatch module contract.

    Fires when config/subscriptions.yaml declares an assignment_store and
    no selected managed-capability pack declares provides_capabilities:
    [subscription_write_path]. In that case the generated app owns the
    subscription assignment write path, and an entitlement_dispatch module is
    required.
    """
    # Any managed capability pack that declares provides_capabilities:
    # [subscription_write_path] owns the subscription assignment write path.
    # entitlement_dispatch is only needed when no such pack is selected.
    managed_writers = [
        p for p in _packs_providing(capability_packs, "subscription_write_path")
        if str(p.get("capability_source") or "").strip() == "managed_capability"
    ]
    if managed_writers:
        return []

    normalized_files = _normalized_files_map(files_map)
    subs_content = normalized_files.get("config/subscriptions.yaml")
    if not subs_content:
        return []

    try:
        subs_config = yaml.safe_load(subs_content) or {}
    except Exception:
        return []

    if not isinstance(subs_config, dict) or not subs_config.get("assignment_store"):
        return []

    errors: list[str] = []
    dispatch_path = "modules/entitlement_dispatch/module.yaml"
    dispatch_content = normalized_files.get(dispatch_path)
    if not dispatch_content:
        errors.append(
            "config/subscriptions.yaml declares assignment_store but "
            f"{dispatch_path} is missing. Self-hosted apps require an "
            "entitlement_dispatch module to write subscription assignment records."
        )
        return errors

    declared_id = _declared_module_id_from_yaml(dispatch_path, dispatch_content)
    if declared_id != "entitlement_dispatch":
        errors.append(
            f"{dispatch_path}: module.id must be 'entitlement_dispatch', got {declared_id!r}."
        )

    actions = _module_actions_from_yaml(dispatch_path, dispatch_content)
    required_actions = {"activate_subscription", "deactivate_subscription"}
    missing_actions = sorted(required_actions - actions)
    if missing_actions:
        errors.append(
            f"{dispatch_path}: entitlement_dispatch module must declare actions "
            f"{sorted(required_actions)}; missing: {missing_actions}."
        )

    return errors


def _scan_entitlement_gate_capability_alignment(files_map: dict[str, str]) -> list[str]:
    """Validate entitlement_gate ↔ subscriptions.yaml compile-time closure.

    For every module action that declares an entitlement_gate capability_id,
    that capability_id must appear in at least one plan's capabilities list in
    config/subscriptions.yaml. An unresolvable gate causes permanent runtime
    denial for all callers regardless of their subscription tier.

    Skips apps without config/subscriptions.yaml (ungated apps, NoOp adapter).
    Apps with config/subscriptions.yaml use ConfiguredEntitlementAdapter at
    platform startup; assignment_store controls persisted assignment lookup, not
    adapter selection.

    Diagnostics include:
    - module path and action id of the gated action
    - the unresolvable capability_id
    - typo near-matches found in the declared plan capabilities
    - confirmation location (config/subscriptions.yaml plans[].capabilities[])
    - whether no plan grants any capabilities at all

    Errors are returned in deterministic sorted order.
    """
    import difflib

    normalized_files = _normalized_files_map(files_map)
    subs_content = normalized_files.get("config/subscriptions.yaml")
    if not subs_content:
        # No subscriptions.yaml -> ungated app, so ModuleExecutor uses NoOp.
        return []

    subscriptions_config, subscriptions_error = _subscriptions_config_from_yaml(
        "config/subscriptions.yaml",
        subs_content,
    )
    if subscriptions_error:
        return [subscriptions_error]
    plan_capabilities = _capability_ids_from_subscriptions_config(subscriptions_config)

    # Collect (module_path, action_id, gate) for every gated action.
    gate_contexts: list[tuple[str, str, str]] = []
    for path, content in sorted(normalized_files.items()):
        if not path.startswith("modules/") or not path.endswith("/module.yaml"):
            continue
        gate_map = _entitlement_gate_map_from_module_yaml(path, content)
        for action_id, gate in sorted(gate_map.items()):
            gate_contexts.append((path, action_id, gate))

    if not gate_contexts:
        module_paths = sorted(
            path
            for path in normalized_files
            if path.startswith("modules/") and path.endswith("/module.yaml")
        )
        if plan_capabilities and module_paths:
            # The bundle sells plan capabilities but gates nothing, so every
            # declared capability is unenforceable at dispatch time and the
            # subscription contract is decorative.
            return [
                "config/subscriptions.yaml grants plan capabilities "
                f"{sorted(plan_capabilities)} but no module action declares an "
                "entitlement_gate. A SaaS bundle that sells capabilities must "
                "enforce at least one of them: set actions[].entitlement_gate "
                "to a granted capability_id on each plan-gated action in "
                f"{module_paths}."
            ]
        return []

    errors: list[str] = []
    for module_path, action_id, gate in gate_contexts:
        if gate in plan_capabilities:
            continue

        msg = (
            f"{module_path}: action '{action_id}' declares "
            f"entitlement_gate '{gate}' which is not granted by any plan in "
            f"config/subscriptions.yaml. "
            f"Actions with an unresolvable gate permanently deny all callers "
            f"regardless of subscription tier. "
            f"Add '{gate}' to at least one plan's capabilities[], or correct "
            f"the capability_id. "
            f"Expected location: config/subscriptions.yaml → "
            f"plans[].capabilities[] (v1) or "
            f"products[].plans[].capabilities[] (v2)."
        )
        if plan_capabilities:
            close = difflib.get_close_matches(gate, sorted(plan_capabilities), n=3, cutoff=0.7)
            if close:
                msg += f" Near-matches in declared plan capabilities: {close}."
        else:
            msg += " No plan currently grants any capabilities."
        errors.append(msg)

    return sorted(errors)


def _scan_deployment_artifacts_contract(files_map: dict[str, str]) -> list[str]:
    normalized_files = _normalized_files_map(files_map)
    deployment_artifacts: dict[str, str] = {
        path: normalized_files[path]
        for path in (
            "Dockerfile",
            ".env.example",
            ".env.staging.example",
            ".env.production.example",
            "deployment.manifest.json",
            ".github/workflows/readiness.yml",
            ".github/workflows/deploy.yml",
        )
        if path in normalized_files
    }
    try:
        from .deployment_contract import validate_generated_deployment_bundle

        deployment_errors = validate_generated_deployment_bundle(
            deployment_artifacts,
            include_dockerfiles=True,
            include_readiness_workflow=".github/workflows/readiness.yml" in deployment_artifacts,
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

    env_example = normalized_files.get(".env.example", "")
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
            "runtime and frontend env handles in .env.example: "
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


def _is_local_route(value: Any) -> bool:
    route = str(value or "").strip()
    return bool(route) and route.startswith("/") and not route.startswith("//")


def _scan_auth_app_contract(files_map: dict[str, str]) -> list[str]:
    """Validate the provider-neutral generated app auth contract."""
    if not _app_manifest_auth_required(files_map):
        return []

    normalized_files = _normalized_files_map(files_map)
    errors: list[str] = []
    raw_contract = normalized_files.get(APP_AUTH_CONFIG_PATH)
    if raw_contract is None:
        return [
            f"Authenticated generated apps must include {APP_AUTH_CONFIG_PATH} "
            "with schema_version mozaiks.auth.v1."
        ]

    try:
        contract = yaml.safe_load(raw_contract) or {}
    except Exception as exc:
        return [f"{APP_AUTH_CONFIG_PATH}: auth contract must be valid YAML: {exc}"]

    if not isinstance(contract, dict):
        return [f"{APP_AUTH_CONFIG_PATH}: auth contract must be a YAML object."]

    allowed_root_keys = {
        "schema_version",
        "auth_required",
        "strategy",
        "mode",
        "signup_enabled",
        "routes",
        "frontend",
        "runtime",
        "identity_providers",
        "login_methods",
        "customization",
    }
    unknown_root_keys = sorted(set(contract) - allowed_root_keys)
    if unknown_root_keys:
        errors.append(f"{APP_AUTH_CONFIG_PATH}: unsupported fields: {unknown_root_keys}.")

    if contract.get("schema_version") != "mozaiks.auth.v1":
        errors.append(f"{APP_AUTH_CONFIG_PATH}: schema_version must be mozaiks.auth.v1.")
    if contract.get("auth_required") is not True:
        errors.append(f"{APP_AUTH_CONFIG_PATH}: auth_required must be true when app.json.authRequired=true.")
    if contract.get("strategy") != "oidc":
        errors.append(f"{APP_AUTH_CONFIG_PATH}: strategy must be oidc for authenticated generated apps.")

    mode = contract.get("mode", "brokered_oidc")
    if mode not in _AUTH_MODES:
        errors.append(
            f"{APP_AUTH_CONFIG_PATH}: mode must be one of {sorted(_AUTH_MODES)} when present."
        )
    signup_enabled = contract.get("signup_enabled", False)
    if not isinstance(signup_enabled, bool):
        errors.append(f"{APP_AUTH_CONFIG_PATH}: signup_enabled must be a boolean when present.")

    routes = contract.get("routes") if isinstance(contract.get("routes"), dict) else {}
    if not isinstance(routes, dict) or not routes:
        errors.append(f"{APP_AUTH_CONFIG_PATH}: routes must declare login, callback, logout, and post_login_default.")
    else:
        route_fields = ("login", "callback", "logout", "post_login_default")
        missing_routes = [field for field in route_fields if field not in routes]
        if missing_routes:
            errors.append(f"{APP_AUTH_CONFIG_PATH}: routes missing fields: {missing_routes}.")
        for field in route_fields:
            if field in routes and not _is_local_route(routes.get(field)):
                errors.append(f"{APP_AUTH_CONFIG_PATH}: routes.{field} must be an app-local route.")

    frontend = contract.get("frontend") if isinstance(contract.get("frontend"), dict) else {}
    required_frontend = {
        "adapter": "oidc_pkce",
        "client_id_env": "VITE_OIDC_CLIENT_ID",
        "authority_env": "VITE_OIDC_AUTHORITY",
        "discovery_url_env": "VITE_OIDC_DISCOVERY_URL",
        "redirect_uri_env": "VITE_OIDC_REDIRECT_URI",
        "scope_env": "VITE_OIDC_SCOPE",
    }
    if not isinstance(frontend, dict) or not frontend:
        errors.append(f"{APP_AUTH_CONFIG_PATH}: frontend must declare OIDC PKCE env handles.")
    else:
        for field, expected in required_frontend.items():
            if frontend.get(field) != expected:
                errors.append(f"{APP_AUTH_CONFIG_PATH}: frontend.{field} must be {expected}.")
        scopes = frontend.get("default_scopes")
        if not isinstance(scopes, list) or not {"openid", "profile", "email"}.issubset(set(scopes)):
            errors.append(f"{APP_AUTH_CONFIG_PATH}: frontend.default_scopes must include openid, profile, and email.")

    runtime = contract.get("runtime") if isinstance(contract.get("runtime"), dict) else {}
    required_runtime = {
        "provider_env": "AUTH_PROVIDER",
        "enabled_env": "AUTH_ENABLED",
        "authority_env": "MOZAIKS_OIDC_AUTHORITY",
        "discovery_url_env": "MOZAIKS_OIDC_DISCOVERY_URL",
        "issuer_env": "AUTH_ISSUER",
        "jwks_url_env": "AUTH_JWKS_URL",
    }
    if not isinstance(runtime, dict) or not runtime:
        errors.append(f"{APP_AUTH_CONFIG_PATH}: runtime must declare provider-neutral backend env handles.")
    else:
        for field, expected in required_runtime.items():
            if runtime.get(field) != expected:
                errors.append(f"{APP_AUTH_CONFIG_PATH}: runtime.{field} must be {expected}.")

    identity_providers = contract.get("identity_providers", [])
    if identity_providers is None:
        identity_providers = []
    if not isinstance(identity_providers, list):
        errors.append(f"{APP_AUTH_CONFIG_PATH}: identity_providers must be a list when present.")
    else:
        allowed_idp_fields = {"id", "label", "provider_role"}
        for index, item in enumerate(identity_providers):
            if not isinstance(item, dict):
                errors.append(f"{APP_AUTH_CONFIG_PATH}: identity_providers[{index}] must be an object.")
                continue
            unknown = sorted(set(item) - allowed_idp_fields)
            if unknown:
                errors.append(
                    f"{APP_AUTH_CONFIG_PATH}: identity_providers[{index}] has unsupported fields: {unknown}."
                )
            if item.get("provider_role") not in {None, "upstream_oidc_provider"}:
                errors.append(
                    f"{APP_AUTH_CONFIG_PATH}: identity_providers[{index}].provider_role "
                    "must be upstream_oidc_provider when present."
                )

    login_methods = contract.get("login_methods", [])
    if login_methods is None:
        login_methods = []
    if not isinstance(login_methods, list):
        errors.append(f"{APP_AUTH_CONFIG_PATH}: login_methods must be a list when present.")
    else:
        allowed_login_fields = {"id", "kind", "label", "primary", "provider_id"}
        for index, item in enumerate(login_methods):
            if not isinstance(item, dict):
                errors.append(f"{APP_AUTH_CONFIG_PATH}: login_methods[{index}] must be an object.")
                continue
            unknown = sorted(set(item) - allowed_login_fields)
            if unknown:
                errors.append(
                    f"{APP_AUTH_CONFIG_PATH}: login_methods[{index}] has unsupported fields: {unknown}."
                )
            if not str(item.get("id") or "").strip():
                errors.append(f"{APP_AUTH_CONFIG_PATH}: login_methods[{index}].id is required.")
            kind = item.get("kind")
            if kind not in _AUTH_LOGIN_METHOD_KINDS:
                errors.append(
                    f"{APP_AUTH_CONFIG_PATH}: login_methods[{index}].kind "
                    f"must be one of {sorted(_AUTH_LOGIN_METHOD_KINDS)}."
                )
            if not str(item.get("label") or "").strip():
                errors.append(f"{APP_AUTH_CONFIG_PATH}: login_methods[{index}].label is required.")
            if "primary" in item and not isinstance(item.get("primary"), bool):
                errors.append(f"{APP_AUTH_CONFIG_PATH}: login_methods[{index}].primary must be boolean.")
            if "provider_id" in item and not str(item.get("provider_id") or "").strip():
                errors.append(f"{APP_AUTH_CONFIG_PATH}: login_methods[{index}].provider_id must be non-empty.")

    if "http://" in raw_contract or "https://" in raw_contract:
        errors.append(
            f"{APP_AUTH_CONFIG_PATH}: provider URLs must be supplied by env handles, "
            "not committed as literal URLs."
        )

    adapter = normalized_files.get("ui/auth/authAdapter.js", "")
    if not adapter:
        errors.append("Authenticated generated apps must include ui/auth/authAdapter.js.")
    else:
        required_markers = {
            "TRANSACTION_KEY": "TRANSACTION_KEY",
            "state": "state",
            "clearStoredUserSession": "clearStoredUserSession",
            "writeAuthTransaction": "writeAuthTransaction",
            "readAuthTransaction": "readAuthTransaction",
            "returnPath": "returnPath",
        }
        missing_markers = [label for label, marker in required_markers.items() if marker not in adapter]
        if missing_markers:
            errors.append(
                "ui/auth/authAdapter.js must implement state-bound PKCE transaction handling; "
                f"missing markers: {missing_markers}."
            )
        if "localStorage" in adapter:
            errors.append("ui/auth/authAdapter.js must use sessionStorage, not localStorage, for auth state.")
        forbidden_provider_markers = [
            "accounts.google.com",
            "GoogleAuthProvider",
            "gapi.",
            "keycloak-js",
            "client_secret",
        ]
        found_provider_markers = [marker for marker in forbidden_provider_markers if marker in adapter]
        if found_provider_markers:
            errors.append(
                "ui/auth/authAdapter.js must stay provider-neutral OIDC; "
                f"found provider-specific markers: {found_provider_markers}."
            )

    return errors


def _scan_route_manifest_consistency(files_map: dict[str, str]) -> list[str]:
    """Validate ui/route_manifest.json structure when present.

    Checks that each page entry has a path starting with '/' and a non-empty
    component name. These are the minimum fields required for the runtime to
    resolve a page route.
    """
    errors: list[str] = []
    normalized = _normalized_files_map(files_map)
    manifest_raw = normalized.get("ui/route_manifest.json")
    if not manifest_raw:
        return errors

    try:
        manifest = json.loads(manifest_raw)
    except Exception:
        errors.append("ui/route_manifest.json: invalid JSON — cannot parse route manifest")
        return errors

    pages = manifest.get("pages") if isinstance(manifest, dict) else None
    if not isinstance(pages, list):
        errors.append("ui/route_manifest.json: 'pages' must be a list")
        return errors

    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            errors.append(f"ui/route_manifest.json: pages[{i}] must be a dict")
            continue
        path = page.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            errors.append(
                f"ui/route_manifest.json: pages[{i}] 'path' must be a string starting with '/'"
            )
        component = page.get("component")
        if not isinstance(component, str) or not component.strip():
            errors.append(
                f"ui/route_manifest.json: pages[{i}] 'component' must be a non-empty string"
            )

    return errors


def _scan_page_schema_structure(files_map: dict[str, str]) -> list[str]:
    """Validate declarative page YAML with the runtime page-schema contract."""
    errors: list[str] = []
    normalized = _normalized_files_map(files_map)

    for path, content in normalized.items():
        if not path.startswith("ui/pages/"):
            continue
        if not path.endswith(".yaml"):
            continue
        if "/custom/" in path:
            # Custom React escape-hatch pages — skip schema validation.
            continue

        try:
            schema = yaml.safe_load(content)
        except Exception:
            errors.append(f"{path}: invalid YAML in schema-native page")
            continue

        if not isinstance(schema, dict):
            errors.append(f"{path}: page schema must be a YAML mapping")
            continue

        try:
            validate_page_schema(schema)
        except PageSchemaValidationError as exc:
            errors.extend(
                f"{path}: {diagnostic.location}: {diagnostic.code}"
                for diagnostic in exc.diagnostics
            )

    return errors


_PACK_PROVENANCE_PATH = ".mozaiks/pack_provenance.json"
_PACK_PROVENANCE_SCHEMA_VERSION = "mozaiks.pack_provenance.v1"

_PROVENANCE_REQUIRED_KEYS = frozenset({"schema_version", "framework_version", "generated_at", "packs"})
_PROVENANCE_PACK_REQUIRED_KEYS = frozenset({
    "pack_id",
    "version",
    "source",
    "digest",
    "materialized_owned_files",
})


def _scan_pack_provenance_manifest(files_map: dict[str, str]) -> list[str]:
    """Validate .mozaiks/pack_provenance.json schema when present.

    The file is optional — emitted only when packs were selected.  When present
    it must conform to the ``mozaiks.pack_provenance.v1`` schema so future tooling
    can rely on the structure for upgrade/diff decisions.
    """
    raw = files_map.get(_PACK_PROVENANCE_PATH)
    if raw is None:
        return []

    errors: list[str] = []
    try:
        manifest = json.loads(raw)
    except Exception as exc:
        return [f"{_PACK_PROVENANCE_PATH}: invalid JSON — {exc}"]

    if not isinstance(manifest, dict):
        return [f"{_PACK_PROVENANCE_PATH}: pack_provenance.json must be a JSON object"]

    # Check required top-level keys
    missing_keys = _PROVENANCE_REQUIRED_KEYS - set(manifest.keys())
    for key in sorted(missing_keys):
        errors.append(f"{_PACK_PROVENANCE_PATH}: missing required field '{key}'")
    unknown_keys = sorted(set(manifest.keys()) - _PROVENANCE_REQUIRED_KEYS)
    for key in unknown_keys:
        errors.append(f"{_PACK_PROVENANCE_PATH}: unsupported field '{key}'")

    # Check schema_version value
    sv = manifest.get("schema_version")
    if sv and sv != _PACK_PROVENANCE_SCHEMA_VERSION:
        errors.append(
            f"{_PACK_PROVENANCE_PATH}: schema_version must be "
            f"'{_PACK_PROVENANCE_SCHEMA_VERSION}', got '{sv}'"
        )

    # Validate packs entries
    packs = manifest.get("packs")
    if packs is not None:
        if not isinstance(packs, list):
            errors.append(f"{_PACK_PROVENANCE_PATH}: 'packs' must be a JSON array")
        else:
            for i, entry in enumerate(packs):
                if not isinstance(entry, dict):
                    errors.append(f"{_PACK_PROVENANCE_PATH}: packs[{i}] must be a JSON object")
                    continue
                missing_pack_keys = _PROVENANCE_PACK_REQUIRED_KEYS - set(entry.keys())
                for key in sorted(missing_pack_keys):
                    errors.append(f"{_PACK_PROVENANCE_PATH}: packs[{i}] missing required field '{key}'")
                unknown_pack_keys = sorted(set(entry.keys()) - _PROVENANCE_PACK_REQUIRED_KEYS)
                for key in unknown_pack_keys:
                    errors.append(f"{_PACK_PROVENANCE_PATH}: packs[{i}] unsupported field '{key}'")
                digest = str(entry.get("digest") or "")
                if digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    errors.append(f"{_PACK_PROVENANCE_PATH}: packs[{i}].digest must be a canonical sha256 digest")
                files = entry.get("materialized_owned_files")
                if files is not None and not isinstance(files, list):
                    errors.append(
                        f"{_PACK_PROVENANCE_PATH}: packs[{i}].materialized_owned_files must be a JSON array"
                    )

    return errors


def _scan_page_api_endpoint_alignment(files_map: dict[str, str]) -> list[str]:
    """Validate that api_endpoint values in schema-native page sections reference
    modules and actions declared in the same bundle.

    Only fires when module.yaml files are present — bundles without modules are
    either page-only bundles or capability-only bundles, both of which may
    legitimately call external modules.

    Endpoints that do not match /api/modules/{module_id}/{action_id} exactly are
    already flagged by the ui_quality gate; this check only verifies that
    well-formed endpoints resolve to declared bundle artifacts.
    """
    normalized_files = _normalized_files_map(files_map)

    # Build module_id → set[action_id] from all module.yaml files in the bundle.
    module_actions: dict[str, set[str]] = {}
    for path, content in normalized_files.items():
        if not (path.startswith("modules/") and path.endswith("/module.yaml")):
            continue
        parts = PurePosixPath(path).parts
        if len(parts) != 3:
            continue
        module_id = parts[1]
        actions = _module_actions_from_yaml(path, content)
        module_actions[module_id] = actions

    if not module_actions:
        return []  # No modules in bundle — skip reference closure.

    errors: list[str] = []
    for path, content in sorted(normalized_files.items()):
        if not path.startswith("ui/pages/") or not path.endswith(".yaml") or "/custom/" in path:
            continue
        try:
            schema = yaml.safe_load(content) or {}
        except Exception:
            continue
        if not isinstance(schema, dict):
            continue
        sections = schema.get("sections")
        if not isinstance(sections, list):
            continue
        for i, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            for ep in (
                section.get("api_endpoint"),
                (section.get("config") or {}).get("api_endpoint"),
            ):
                if not isinstance(ep, str):
                    continue
                ep = ep.strip()
                if not ep.startswith("/api/modules/"):
                    continue
                ep_parts = ep.split("/")
                # Expected: ['', 'api', 'modules', module_id, action_id]
                if len(ep_parts) != 5:
                    continue
                ref_module, ref_action = ep_parts[3], ep_parts[4]
                if not ref_module or not ref_action:
                    continue
                if ref_module not in module_actions:
                    errors.append(
                        f"{path}: sections[{i}] api_endpoint '{ep}' references "
                        f"module '{ref_module}' which is not declared in this bundle."
                    )
                elif ref_action not in module_actions[ref_module]:
                    errors.append(
                        f"{path}: sections[{i}] api_endpoint '{ep}' references "
                        f"action '{ref_action}' which is not declared in "
                        f"modules/{ref_module}/module.yaml."
                    )
    return errors


def _scan_route_manifest_component_files(files_map: dict[str, str]) -> list[str]:
    """Validate that every component declared in ui/route_manifest.json has a
    matching custom page file under ui/pages/custom/.

    This catches the common generation failure where route_manifest.json is
    written correctly but the corresponding JSX file is missing, which produces
    a runtime 404 for every missing component.
    """
    errors: list[str] = []
    normalized = _normalized_files_map(files_map)
    manifest_raw = normalized.get("ui/route_manifest.json")
    if not manifest_raw:
        return errors

    try:
        manifest = json.loads(manifest_raw)
    except Exception:
        return errors  # Already caught by _scan_route_manifest_consistency.

    pages = manifest.get("pages") if isinstance(manifest, dict) else None
    if not isinstance(pages, list):
        return errors

    # Collect custom page file stems that are actually present.
    custom_page_stems: set[str] = set()
    for path in normalized:
        if path.startswith("ui/pages/custom/") and path.endswith(".jsx"):
            stem = PurePosixPath(path).stem
            if stem:
                custom_page_stems.add(stem)

    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        component = str(page.get("component") or "").strip()
        if not component:
            continue
        # Shell-built-in components are always available — no custom JSX file required.
        if component in _SHELL_CORE_COMPONENTS:
            continue
        if component not in custom_page_stems:
            route_path = str(page.get("path") or "<unknown>").strip()
            errors.append(
                f"ui/route_manifest.json: pages[{i}] route '{route_path}' declares "
                f"component '{component}' but ui/pages/custom/{component}.jsx is missing. "
                f"The route will 404 at runtime."
            )
    return errors


def _scan_action_api_surface(files_map: dict[str, str]) -> list[str]:
    """Validate that action api_surface values in module.yaml use the canonical vocabulary.

    api_surface controls HTTP exposure posture.  Unknown values are silently ignored
    by the runtime and could produce unintended public exposure.  Only the four
    declared values are canonical; any other string is a generation error.
    """
    errors: list[str] = []
    normalized = _normalized_files_map(files_map)

    for path, content in normalized.items():
        if not (path.startswith("modules/") and path.endswith("/module.yaml")):
            continue
        parts = PurePosixPath(path).parts
        if len(parts) != 3:
            continue
        module_id = parts[1]

        try:
            parsed = yaml.safe_load(content) or {}
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue

        actions = parsed.get("actions")
        if not isinstance(actions, list):
            continue

        for action in actions:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip()
            api_surface = action.get("api_surface")
            if api_surface is None:
                continue
            api_surface_str = str(api_surface).strip()
            if not api_surface_str or api_surface_str == "null":
                continue
            if api_surface_str not in _CANONICAL_API_SURFACE_VALUES:
                errors.append(
                    f"modules/{module_id}/module.yaml: action '{action_id}' "
                    f"api_surface '{api_surface_str}' is not a canonical value. "
                    f"Must be one of: {', '.join(sorted(_CANONICAL_API_SURFACE_VALUES))}."
                )

    return errors


def _scan_event_reaction_closure(files_map: dict[str, str]) -> list[str]:
    """Validate event/reaction reference closure within a generated bundle.

    Checks:
    1. Every reaction's event_type resolves to a declared event in the bundle's
       events.yaml files (platform-namespace events are exempt and always pass).
    2. Every reaction target.kind is a canonical value.
    3. Target-kind-specific required fields are present and non-empty.

    Only fires when at least one reactions.yaml is present in the bundle.
    Platform events (hosted.*, platform.*, mozaiks.*) are not bundle-declared
    and are always treated as resolvable.
    """
    errors: list[str] = []
    normalized = _normalized_files_map(files_map)

    # Collect all declared event_types from modules/*/contracts/events.yaml.
    declared_event_types: set[str] = set()
    for path, content in normalized.items():
        if not re.match(r"modules/[^/]+/contracts/events\.yaml", path):
            continue
        try:
            parsed = yaml.safe_load(content) or {}
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        events = parsed.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if isinstance(event, dict):
                event_type = str(event.get("type") or "").strip()
                if event_type:
                    declared_event_types.add(event_type)

    # Validate each reaction in modules/*/contracts/reactions.yaml.
    for path, content in sorted(normalized.items()):
        if not re.match(r"modules/[^/]+/contracts/reactions\.yaml", path):
            continue
        try:
            parsed = yaml.safe_load(content) or {}
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue

        reactions = parsed.get("reactions")
        if not isinstance(reactions, list):
            continue

        for i, reaction in enumerate(reactions):
            if not isinstance(reaction, dict):
                continue
            reaction_id = str(reaction.get("id") or f"[{i}]").strip()
            label = f"{path}: reaction '{reaction_id}'"

            # 1. event_type closure.
            event_type = str(reaction.get("event_type") or "").strip()
            if not event_type:
                errors.append(f"{label} missing required 'event_type'.")
            elif not any(event_type.startswith(ns) for ns in _PLATFORM_EVENT_NAMESPACES):
                # Only validate domain events that must be bundle-declared.
                if declared_event_types and event_type not in declared_event_types:
                    errors.append(
                        f"{label} event_type '{event_type}' is not declared in any "
                        f"modules/*/contracts/events.yaml in this bundle."
                    )

            # 2. target.kind validation.
            target = reaction.get("target")
            if not isinstance(target, dict):
                errors.append(f"{label} missing required 'target' mapping.")
                continue

            kind = str(target.get("kind") or "").strip()
            if not kind:
                errors.append(f"{label} target missing required 'kind'.")
            elif kind not in _CANONICAL_REACTION_TARGET_KINDS:
                errors.append(
                    f"{label} target.kind '{kind}' is not canonical. "
                    f"Must be one of: {', '.join(sorted(_CANONICAL_REACTION_TARGET_KINDS))}."
                )
            else:
                # 3. Target-kind-specific required fields.
                if kind == "handler":
                    method = str(target.get("handler_method") or "").strip()
                    if not method:
                        errors.append(
                            f"{label} target.kind='handler' requires non-empty 'handler_method'."
                        )
                elif kind == "capability":
                    cap_id = str(target.get("capability_id") or "").strip()
                    if not cap_id:
                        errors.append(
                            f"{label} target.kind='capability' requires non-empty 'capability_id'."
                        )
                elif kind == "notification":
                    notif_id = str(target.get("notification_id") or "").strip()
                    if not notif_id:
                        errors.append(
                            f"{label} target.kind='notification' requires non-empty 'notification_id'."
                        )
                elif kind == "service_adapter":
                    adapter = str(target.get("adapter") or "").strip()
                    if not adapter:
                        errors.append(
                            f"{label} target.kind='service_adapter' requires non-empty 'adapter'."
                        )

    return errors


def _layout_extensions_for_selected_packs(
    capability_packs: list[dict[str, Any]] | None,
) -> tuple[LayoutExtension, ...]:
    extensions = list(layout_extensions_from_selected_packs(capability_packs))
    claimed_paths: dict[str, str] = {}
    for pack in capability_packs or []:
        pack_id = _pack_id_from_descriptor(pack)
        if not pack_id:
            continue
        for path in sorted(resolve_declared_pack_output_paths([pack])):
            prior_owner = claimed_paths.get(path)
            if prior_owner is not None and prior_owner != pack_id:
                raise ManagedCapabilityTemplateError(
                    f"Selected packs '{prior_owner}' and '{pack_id}' both declare "
                    f"output path {path!r}; duplicate pack output claims fail closed"
                )
            claimed_paths[path] = pack_id
            if _path_matches_core_layout(path):
                continue
            try:
                extensions.append(
                    LayoutExtension(
                        slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
                        pack_id=pack_id,
                        path=path,
                    )
                )
            except (ValueError, PydanticValidationError) as exc:
                raise ManagedCapabilityTemplateError(
                    f"Pack '{pack_id}' declares output {path!r} outside the "
                    f"permitted pack output lanes: {exc}"
                ) from exc
    return tuple(sorted(extensions, key=lambda item: (item.slot.value, item.pack_id, item.path or "")))


def _path_matches_core_layout(path: str) -> bool:
    """True when the core registry (no extensions) already classifies the path."""
    registry = build_app_layout_registry(())
    scopes = (
        PathScope.APP_BUNDLE_ROOT,
        PathScope.DEPLOYMENT_DERIVED,
        PathScope.WORKSPACE_ROOT,
        PathScope.GENERATED_STAGING,
    )
    for scope in scopes:
        try:
            registry.match_path(path, scope)
        except ValueError:
            continue
        return True
    return False


def _scan_declared_pack_repo_support_outputs(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None = None,
) -> list[str]:
    try:
        declared_paths = (
            resolve_declared_pack_output_paths(capability_packs)
            if capability_packs
            else frozenset()
        )
    except ManagedCapabilityTemplateError as exc:
        return [f"Selected CapabilityPack output contract is invalid: {exc}"]

    repo_support = (
        ".claude/",
        ".github/",
        "docs/",
        "scripts/",
        "tests/",
    )
    invalid = sorted(
        path
        for path in _normalized_files_map(files_map)
        if path.startswith(repo_support)
        and path not in {".github/workflows/deploy.yml", ".github/workflows/readiness.yml"}
        and path not in declared_paths
    )
    if not invalid:
        return []
    return [
        "Generated app bundle contains undeclared repository-support pack outputs: "
        f"{invalid}. Selected capability packs must declare generated docs, scripts, "
        "and repository-support files explicitly."
    ]


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
    - .mozaiks/pack_provenance.json: schema validation when present.
    - ui/route_manifest.json: required path/component fields; component file existence.
    - ui/pages/*.yaml (schema-native): required fields, canonical page_type, canonical primitives,
      api_endpoint → module/action closure.
    - modules/*/module.yaml: action api_surface canonical vocabulary.
    - modules/*/contracts/reactions.yaml: event_type closure, target.kind taxonomy,
      target-kind-specific required fields.
    """
    try:
        selected_layout_extensions = _layout_extensions_for_selected_packs(capability_packs)
    except ManagedCapabilityTemplateError as exc:
        return [f"Selected CapabilityPack output contract is invalid: {exc}"]
    layout_report = validate_file_map_layout(
        files_map,
        selected_extensions=selected_layout_extensions,
    )
    errors: list[str] = layout_validation_errors(layout_report)
    errors.extend(
        _scan_declared_pack_repo_support_outputs(
            files_map,
            capability_packs=capability_packs,
        )
    )
    scannable_files_map = filter_layout_scannable_file_map(files_map, layout_report)
    errors.extend(
        _scan_canonical_app_paths(
            scannable_files_map,
            capability_packs=capability_packs,
        )
    )
    errors.extend(_scan_security_secret_contract(scannable_files_map))
    errors.extend(_scan_pack_provenance_manifest(scannable_files_map))
    errors.extend(_scan_route_manifest_consistency(scannable_files_map))
    errors.extend(_scan_route_manifest_component_files(scannable_files_map))
    errors.extend(_scan_page_schema_structure(scannable_files_map))
    errors.extend(_scan_page_api_endpoint_alignment(scannable_files_map))
    errors.extend(_scan_action_api_surface(scannable_files_map))
    errors.extend(_scan_event_reaction_closure(scannable_files_map))
    errors.extend(_scan_data_contract_module_alignment(scannable_files_map))
    errors.extend(
        _scan_selected_managed_capability_boundaries(
            scannable_files_map,
            capability_packs=capability_packs,
        )
    )
    errors.extend(
        _scan_managed_setup_provider_leaks(
            scannable_files_map,
            capability_packs=capability_packs,
        )
    )
    errors.extend(
        _scan_token_wallets_require_mozaikspay(
            scannable_files_map,
            capability_packs=capability_packs,
        )
    )
    errors.extend(
        _scan_mozaikspay_saas_contract(
            scannable_files_map,
            capability_packs=capability_packs,
        )
    )
    errors.extend(
        _scan_mozaiks_cloud_connector_contract(
            scannable_files_map,
            capability_packs=capability_packs,
        )
    )
    errors.extend(
        _scan_self_hosted_entitlement_dispatch_contract(
            scannable_files_map,
            capability_packs=capability_packs,
        )
    )
    errors.extend(_scan_entitlement_gate_capability_alignment(scannable_files_map))
    errors.extend(_scan_auth_app_contract(scannable_files_map))
    if require_deployment_artifacts:
        errors.extend(_scan_deployment_artifacts_contract(scannable_files_map))
        errors.extend(_scan_auth_deployment_contract(scannable_files_map))

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

            for raw_cloud_import in _RAW_CLOUD_PROVIDER_IMPORT_RE.finditer(content):
                provider_name = raw_cloud_import.group(1) or raw_cloud_import.group(2) or ""
                errors.append(
                    f"{path}: imports raw cloud provider SDK {provider_name!r}. Generated apps must "
                    "route cloud operations through the bounded MozaiksCloud sub-clients, not "
                    "provider SDKs (azure, cloudflare, etc.) directly."
                )

    return errors


__all__ = ["scan_generated_bundle"]

