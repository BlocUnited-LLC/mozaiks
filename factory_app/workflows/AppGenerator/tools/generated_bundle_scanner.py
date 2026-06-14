"""generated_bundle_scanner — scan a generated app bundle for forbidden patterns.

Checks that the generated app does not:

- Embed raw provider secret key literals such as Stripe sk_live_* / sk_test_*.
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

# Stripe secret key literal in any scannable file. This remains a generic secret
# hygiene check; provider-specific SDK usage is governed by the selected pack.
# Matches sk_live_* and sk_test_* with at least 10 trailing alphanum chars.
_STRIPE_SECRET_LITERAL_RE = re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{10,}")
_STRIPE_IMPORT_RE = re.compile(r"(?m)^\s*(?:import\s+stripe\b|from\s+stripe\s+import\b)")
_STRIPE_API_KEY_RE = re.compile(r"\bstripe\s*\.\s*api_key\s*=")
_STRIPE_REFUND_CALL_RE = re.compile(
    r"\bstripe\s*\.\s*(?:Refund\s*\.\s*create|refunds\s*\.\s*create)\s*\(",
    re.IGNORECASE,
)
_STRIPE_REFUNDS_ENDPOINT_RE = re.compile(r"['\"]?/v1/refunds\b")

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


def _selected_hosted_pack_ids(capability_packs: list[dict[str, Any]] | None) -> set[str]:
    ids: set[str] = set()
    for pack in capability_packs or []:
        if not isinstance(pack, dict):
            continue
        if str(pack.get("capability_source") or "").strip() != "hosted_pack":
            continue
        pack_id = _pack_id_from_descriptor(pack)
        if pack_id:
            ids.add(pack_id)
    return ids


def _iter_api_endpoint_literals(content: str) -> list[str]:
    return re.findall(r'["\'](/api/modules/[^"\']+)["\']', content)


def _scan_selected_hosted_pack_boundaries(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None,
) -> list[str]:
    hosted_pack_ids = _selected_hosted_pack_ids(capability_packs)
    if not hosted_pack_ids:
        return []

    normalized_files = _normalized_files_map(files_map)
    errors: list[str] = []
    for pack_id in sorted(hosted_pack_ids):
        hosted_module_prefix = f"modules/{pack_id}/"
        hosted_module_paths = [
            path for path in normalized_files if path.startswith(hosted_module_prefix)
        ]
        if hosted_module_paths:
            errors.append(
                f"Selected hosted pack '{pack_id}' must not generate hosted internals: "
                f"{hosted_module_paths}. Generate app-owned facade modules instead."
            )

        adapter_path = f"services/integrations/{pack_id}_client.py"
        if adapter_path not in normalized_files:
            errors.append(
                f"Selected hosted pack '{pack_id}' requires app-owned adapter "
                f"{adapter_path}."
            )

        direct_endpoint_prefix = f"/api/modules/{pack_id}/"
        for path, content in normalized_files.items():
            if not path.startswith("ui/pages/"):
                continue
            for endpoint in _iter_api_endpoint_literals(content):
                if endpoint.startswith(direct_endpoint_prefix):
                    errors.append(
                        f"{path}: page binds directly to hosted pack endpoint "
                        f"{endpoint}. Bind pages to an app-owned facade module instead."
                    )
    return errors


def scan_generated_bundle(
    files_map: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None = None,
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
        _scan_selected_hosted_pack_boundaries(
            files_map,
            capability_packs=capability_packs,
        )
    )

    for path, content in files_map.items():
        if not isinstance(path, str) or not isinstance(content, str):
            continue

        if not _is_scannable(path):
            continue

        # ---- checks that apply to all scannable file types ----

        if _STRIPE_SECRET_LITERAL_RE.search(content):
            errors.append(
                f"{path}: contains a raw provider secret key literal "
                "(sk_live_* or sk_test_*). Generated apps must not embed "
                "raw credentials. Store credential values only through the "
                "configured secret backend."
            )

        if _STRIPE_API_KEY_RE.search(content):
            errors.append(
                f"{path}: assigns stripe.api_key directly. Generated apps must "
                "resolve provider credentials through the configured secret "
                "backend or hosted adapter boundary."
            )

        if _STRIPE_REFUND_CALL_RE.search(content):
            errors.append(
                f"{path}: calls Stripe refunds APIs directly. Generated apps "
                "must route refund mutations through an app-owned facade or "
                "hosted payment adapter."
            )

        if _STRIPE_REFUNDS_ENDPOINT_RE.search(content):
            errors.append(
                f"{path}: references /v1/refunds directly. Generated apps must "
                "route refund mutations through an app-owned facade or hosted "
                "payment adapter."
            )

        if path.lower().endswith(".py") and _STRIPE_IMPORT_RE.search(content):
            errors.append(
                f"{path}: imports the Stripe SDK directly. Generated apps must "
                "use generated service boundaries or hosted adapter clients "
                "instead of provider SDKs in app business code."
            )

    return errors


__all__ = ["scan_generated_bundle"]

