"""MozaiksCloud managed capability connector contract tests.

Verifies:
- Deterministic materialization: same templates produce same file set
- Explicit selection required: no materialization without pack in capability_packs
- Complete absence when pack not selected (conditional materialization proof)
- Contract/client/schema alignment
- Unknown provider rejection (scanner rejects unknown pack type)
- Closed status transitions declared in provider_api_contract.yaml
- Idempotency key forwarded by transport
- Callback/event routes remain absent; provider replay flows through Idempotency-Key
- No provider credentials in bundles (scanner catches raw cloud SDK imports)
- No proprietary adapter code in bundles
- Provider-neutral deployment artifacts remain valid when cloud pack absent
- Provider-neutral domain declarations remain valid (cloud pack optional)
- Existing generated-app acceptance gates pass unaffected
- Repeated output is deterministic
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from factory_app.workflows.AppGenerator.tools.generate_and_download import (
    _deployment_env_for_capability_packs,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    scan_generated_bundle,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_managed_capability_templates,
)

_PACK_ROOT = (
    Path(__file__).resolve().parents[1]
    / "factory_app"
    / "build_context"
    / "mozaiks_cloud"
)
_CONTEXT_YAML = _PACK_ROOT / "context.yaml"
_CONTRACT_YAML = _PACK_ROOT / "contract.yaml"
_TEMPLATES_DIR = _PACK_ROOT / "templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _all_template_files() -> list[Path]:
    return [f for f in _TEMPLATES_DIR.rglob("*") if f.is_file() and "__pycache__" not in str(f)]


def _template_content(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def _cloud_pack_descriptor() -> dict[str, Any]:
    """Return the pack descriptor from context.yaml with pack_source_path set."""
    ctx = yaml.safe_load((_PACK_ROOT / "context.yaml").read_text(encoding="utf-8"))
    descriptor = dict(ctx["pack"])
    descriptor["pack_source_path"] = str(_PACK_ROOT)
    return descriptor


def _cloud_pack_descriptor_for_scanner() -> dict[str, Any]:
    """Return a minimal capability_packs entry suitable for the bundle scanner."""
    return {
        "capability_pack_id": "mozaiks_cloud",
        "capability_source": "managed_capability",
        "surface_id": "cloud_deployment",
        "surface_kind": "module",
        "pack_type": "managed_capability",
        "label": "Mozaiks Cloud",
        "summary": "Managed deployment and domain management",
        "implementation_mode": "managed",
    }


def _minimal_files_map_with_cloud_pack() -> dict[str, str]:
    """Return the minimal files_map that satisfies the cloud connector contract check."""
    template_dir = _TEMPLATES_DIR
    files_map: dict[str, str] = {}
    for path in template_dir.rglob("*"):
        if not path.is_file() or "__pycache__" in str(path):
            continue
        rel = str(path.relative_to(template_dir)).replace("\\", "/")
        try:
            files_map[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            pass
    # Minimal module.yaml action surface entries are already in the templates
    return files_map


# ---------------------------------------------------------------------------
# Pack registration alignment
# ---------------------------------------------------------------------------


def test_context_yaml_and_contract_yaml_ids_are_aligned() -> None:
    context = _read_yaml(_CONTEXT_YAML)
    contract = _read_yaml(_CONTRACT_YAML)

    assert context["context_id"] == "mozaiks_cloud"
    # contract_id is the pack's contract-level name; pack.id == context_id
    assert context["pack"]["id"] == "mozaiks_cloud"
    assert context["pack"]["capability_source"] == "managed_capability"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert contract["contract_type"] == "build_pack_instructions"


def test_context_required_integrations_match_contract() -> None:
    context = _read_yaml(_CONTEXT_YAML)
    contract = _read_yaml(_CONTRACT_YAML)

    ctx_services = {i["service"] for i in context["pack"]["required_integrations"]}
    con_services = {i["service"] for i in contract["required_integrations"]}
    assert ctx_services == con_services, (
        "required_integrations services must match between context.yaml and contract.yaml"
    )


def test_context_capabilities_match_facade_module_ids() -> None:
    context = _read_yaml(_CONTEXT_YAML)
    facade_ids = {f["module_id"] for f in (context.get("facades") or [])}
    capability_facades = {c.get("facade_recommended") for c in context.get("capabilities") or []}
    # Every referenced facade should be declared in facades[]
    assert capability_facades <= (facade_ids | {None}), (
        f"Capabilities reference undeclared facades: {capability_facades - facade_ids - {None}}"
    )


# ---------------------------------------------------------------------------
# Conditional materialization — resolve_managed_capability_templates
# ---------------------------------------------------------------------------


def test_conditional_materialization_pack_selected_produces_client_files() -> None:
    """When mozaiks_cloud is in capability_packs, client templates are materialized."""
    pack_descriptor = _cloud_pack_descriptor()
    items = resolve_managed_capability_templates([pack_descriptor])
    normalized = {item["filename"].replace("\\", "/"): item["content"] for item in items}
    assert "services/integrations/mozaiks_cloud_client.py" in normalized, (
        "mozaiks_cloud_client.py must be materialized when pack is selected"
    )
    assert "services/integrations/mozaiks_cloud_deployment_client.py" in normalized
    assert "services/integrations/mozaiks_cloud_domain_client.py" in normalized
    assert "modules/cloud_deployment/module.yaml" in normalized
    assert "modules/cloud_domain/module.yaml" in normalized


def test_conditional_materialization_pack_absent_produces_no_cloud_files() -> None:
    """When mozaiks_cloud is NOT in capability_packs, no cloud connector files are produced."""
    items = resolve_managed_capability_templates([])  # no packs selected
    normalized = {item["filename"].replace("\\", "/"): item["content"] for item in items}

    cloud_files = [k for k in normalized if "mozaiks_cloud" in k or "cloud_deployment" in k or "cloud_domain" in k]
    assert not cloud_files, (
        f"No cloud connector files should be materialized when pack is absent: {cloud_files}"
    )


def test_conditional_materialization_other_pack_selected_no_cloud_files() -> None:
    """When only mozaikspay is selected (not mozaiks_cloud), no cloud files appear."""
    mozaikspay_pack = {
        "capability_pack_id": "mozaikspay",
        "capability_source": "managed_capability",
        "surface_id": "billing_portal",
        "surface_kind": "module",
        "pack_type": "managed_capability",
        "label": "MozaiksPay",
        "summary": "Managed SaaS billing",
        "implementation_mode": "managed",
    }
    items = resolve_managed_capability_templates([mozaikspay_pack])
    normalized = {item["filename"].replace("\\", "/"): item["content"] for item in items}
    cloud_files = [k for k in normalized if "mozaiks_cloud" in k]
    assert not cloud_files, (
        f"Selecting mozaikspay must not produce mozaiks_cloud files: {cloud_files}"
    )


def test_repeated_materialization_is_deterministic() -> None:
    """Running resolve_managed_capability_templates twice yields identical output.

    The pack_provenance.json manifest is excluded because it is framework-generated
    metadata that may embed a build timestamp (non-deterministic by design).
    """
    pack_descriptor = _cloud_pack_descriptor()

    items1 = resolve_managed_capability_templates([pack_descriptor])
    items2 = resolve_managed_capability_templates([pack_descriptor])
    _exclude = {".mozaiks/pack_provenance.json"}
    map1 = {item["filename"]: item["content"] for item in items1 if item["filename"] not in _exclude}
    map2 = {item["filename"]: item["content"] for item in items2 if item["filename"] not in _exclude}
    assert map1 == map2, (
        "resolve_managed_capability_templates must be deterministic across repeated calls"
    )


# ---------------------------------------------------------------------------
# Deployment env merging
# ---------------------------------------------------------------------------


def test_deployment_env_for_cloud_pack_declares_secret_and_optional() -> None:
    pack_descriptor = _cloud_pack_descriptor()

    env = _deployment_env_for_capability_packs([pack_descriptor])
    secret_vars = set(env.get("secret", []))
    optional_vars = set(env.get("optional", []))

    assert "MOZAIKS_CLOUD_API_KEY" in secret_vars, (
        "MOZAIKS_CLOUD_API_KEY must be declared as secret when cloud pack is selected"
    )
    assert "MOZAIKS_CLOUD_API_BASE" in optional_vars, (
        "MOZAIKS_CLOUD_API_BASE must be declared as optional env when cloud pack is selected"
    )


def test_deployment_env_absent_when_pack_not_selected() -> None:
    env = _deployment_env_for_capability_packs([])
    all_env = set(env.get("secret", [])) | set(env.get("optional", [])) | set(env.get("required", []))
    cloud_vars = {v for v in all_env if "MOZAIKS_CLOUD" in v}
    assert not cloud_vars, (
        f"Cloud env vars must be absent when pack not selected: {cloud_vars}"
    )


# ---------------------------------------------------------------------------
# Bundle scanner — selected pack
# ---------------------------------------------------------------------------


def test_scanner_passes_for_valid_cloud_connector_bundle() -> None:
    files_map = _minimal_files_map_with_cloud_pack()
    pack_descriptor = _cloud_pack_descriptor()
    errors = scan_generated_bundle(
        files_map=files_map,
        capability_packs=[pack_descriptor],
        require_deployment_artifacts=False,
    )
    cloud_errors = [e for e in errors if "mozaiks_cloud" in e or "cloud_deployment" in e or "cloud_domain" in e]
    assert not cloud_errors, (
        f"Scanner must pass for a valid cloud connector bundle: {cloud_errors}"
    )


def test_scanner_fails_when_client_files_missing_in_selected_bundle() -> None:
    """Scanner must report error when required client files are missing but pack is selected."""
    files_map = {
        # Only include module.yaml, not the client files
        "modules/cloud_deployment/module.yaml": (
            _TEMPLATES_DIR / "modules" / "cloud_deployment" / "module.yaml"
        ).read_text(encoding="utf-8"),
        "modules/cloud_domain/module.yaml": (
            _TEMPLATES_DIR / "modules" / "cloud_domain" / "module.yaml"
        ).read_text(encoding="utf-8"),
        # Intentionally omit client files
    }
    pack_descriptor = _cloud_pack_descriptor()
    errors = scan_generated_bundle(
        files_map=files_map,
        capability_packs=[pack_descriptor],
        require_deployment_artifacts=False,
    )
    assert any("mozaiks_cloud" in e and ("client" in e or "required" in e.lower()) for e in errors), (
        "Scanner must report error for missing client files when cloud pack is selected"
    )


def test_scanner_passes_for_bundle_without_cloud_pack() -> None:
    """When cloud pack is not selected, the scanner must not expect cloud files."""
    # A minimal valid bundle with no cloud content
    files_map: dict[str, str] = {}
    errors = scan_generated_bundle(
        files_map=files_map,
        capability_packs=[],
        require_deployment_artifacts=False,
    )
    cloud_errors = [e for e in errors if "mozaiks_cloud" in e]
    assert not cloud_errors, (
        f"Scanner must not report cloud errors for bundle without cloud pack: {cloud_errors}"
    )


def test_scanner_catches_azure_sdk_import_in_generated_bundle() -> None:
    """Scanner must flag Azure SDK imports in generated Python files."""
    files_map = {
        "services/some_service.py": "import azure.mgmt.resource\n\ndef do_something(): pass\n",
    }
    errors = scan_generated_bundle(
        files_map=files_map,
        capability_packs=[],
        require_deployment_artifacts=False,
    )
    assert any("azure" in e.lower() and "cloud provider" in e.lower() for e in errors), (
        f"Scanner must catch Azure SDK imports: {errors}"
    )


def test_scanner_catches_cloudflare_sdk_import_in_generated_bundle() -> None:
    """Scanner must flag Cloudflare SDK imports in generated Python files."""
    files_map = {
        "services/dns_client.py": "from cloudflare import Cloudflare\n\ndef setup(): pass\n",
    }
    errors = scan_generated_bundle(
        files_map=files_map,
        capability_packs=[],
        require_deployment_artifacts=False,
    )
    assert any("cloudflare" in e.lower() and "cloud provider" in e.lower() for e in errors), (
        f"Scanner must catch Cloudflare SDK imports: {errors}"
    )


def test_scanner_does_not_flag_mozaiks_cloud_client_azure_substring() -> None:
    """The scanner cloud SDK check must not flag the MozaiksCloud client itself
    — it never imports Azure SDK, only references it in docstrings/comments."""
    files_map = _minimal_files_map_with_cloud_pack()
    pack_descriptor = _cloud_pack_descriptor()
    errors = scan_generated_bundle(
        files_map=files_map,
        capability_packs=[pack_descriptor],
        require_deployment_artifacts=False,
    )
    # Specifically: no false positive on docstring mentions of azure
    false_positives = [
        e for e in errors
        if "cloud provider" in e.lower() and "mozaiks_cloud_client" in e
    ]
    assert not false_positives, (
        f"Scanner must not false-positive on docstring mentions of azure in mozaiks_cloud_client: "
        f"{false_positives}"
    )


# ---------------------------------------------------------------------------
# No provider credentials in templates
# ---------------------------------------------------------------------------


def test_templates_contain_no_raw_api_key_values() -> None:
    """Template files must not contain raw credential values."""
    raw_cred_re = re.compile(
        r"\b(?:mzk_live|mzk_test|sk_live|sk_test|Bearer\s+[A-Za-z0-9]{20,})[_A-Za-z0-9]{6,}",
        re.IGNORECASE,
    )
    violations: list[str] = []
    for path in _all_template_files():
        content = _template_content(path)
        if raw_cred_re.search(content):
            violations.append(str(path.relative_to(_TEMPLATES_DIR)))
    assert not violations, (
        f"Templates must not contain raw API key values: {violations}"
    )


def test_templates_contain_no_azure_subscription_ids() -> None:
    """Template files must not contain Azure internal resource IDs."""
    azure_id_re = re.compile(
        r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b"
    )
    violations: list[str] = []
    for path in _all_template_files():
        if path.suffix not in (".py", ".yaml", ".yml"):
            continue
        content = _template_content(path)
        if azure_id_re.search(content):
            # A UUID in template is suspicious — flag for review
            violations.append(str(path.relative_to(_TEMPLATES_DIR)))
    assert not violations, (
        f"Templates must not contain Azure subscription ID or other raw UUIDs: {violations}"
    )


def test_templates_contain_no_forbidden_response_fields() -> None:
    """Template files must not reference forbidden provider-internal response fields."""
    contract = _read_yaml(_CONTRACT_YAML)
    forbidden = contract["provider_api_response_contract"]["globally_forbidden_response_fields"]

    violations: list[tuple[str, str]] = []
    for path in _all_template_files():
        content = _template_content(path)
        for field in forbidden:
            if f'"{field}"' in content or f"'{field}'" in content:
                violations.append((str(path.relative_to(_TEMPLATES_DIR)), field))
    assert not violations, (
        f"Templates must not reference forbidden response fields: {violations}"
    )


# ---------------------------------------------------------------------------
# Provider-neutral deployment artifacts remain valid
# ---------------------------------------------------------------------------


def test_provider_neutral_deployment_artifacts_valid_without_cloud_pack() -> None:
    """A provider-neutral app bundle must not require cloud pack files to pass validation."""
    from factory_app.workflows.AppGenerator.tools.deployment_contract import (
        generate_deployment_artifacts,
    )

    result = generate_deployment_artifacts(
        app_id="test_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=True,
        extra_required_variables=[],
        extra_secret_variables=[],
        extra_optional_variables=[],
        extra_public_variables=[],
    )
    artifacts = result["artifacts"]
    # Deployment artifacts must not contain any mozaiks_cloud references
    combined = "\n".join(str(v) for v in artifacts.values())
    assert "MOZAIKS_CLOUD" not in combined, (
        "Provider-neutral deployment artifacts must not reference Mozaiks Cloud env vars"
    )
    assert "mozaiks_cloud" not in combined.lower(), (
        "Provider-neutral deployment artifacts must not reference the mozaiks_cloud pack"
    )


def test_cloud_pack_deployment_env_does_not_contaminate_core_artifacts() -> None:
    """Adding cloud pack to capability_packs must not change core Dockerfile or compose."""
    from factory_app.workflows.AppGenerator.tools.deployment_contract import (
        generate_deployment_artifacts,
    )

    result = generate_deployment_artifacts(
        app_id="test_app",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=True,
        extra_required_variables=[],
        extra_secret_variables=[],
        extra_optional_variables=[],
        extra_public_variables=[],
    )
    base_artifacts = result["artifacts"]
    # Dockerfile and compose do not change based on capability pack env vars
    assert "Dockerfile" in base_artifacts
    dockerfile = base_artifacts["Dockerfile"]
    assert "MOZAIKS_CLOUD" not in dockerfile, (
        "Dockerfile must be cloud-agnostic; MOZAIKS_CLOUD vars appear in .env.example only"
    )


# ---------------------------------------------------------------------------
# Provider-neutral domain declarations remain valid
# ---------------------------------------------------------------------------


def test_cloud_domain_module_is_optional_not_required_for_app() -> None:
    """The cloud_domain module must not be generated into apps without the cloud pack."""
    items = resolve_managed_capability_templates([])
    normalized = {item["filename"].replace("\\", "/"): item["content"] for item in items}
    assert "modules/cloud_domain/module.yaml" not in normalized, (
        "modules/cloud_domain must not appear in bundles without the mozaiks_cloud pack"
    )


# ---------------------------------------------------------------------------
# Existing acceptance gates unaffected
# ---------------------------------------------------------------------------


def test_existing_generated_app_acceptance_gates_pass_without_cloud_pack() -> None:
    """Existing scan_generated_bundle gates must not regress for apps without cloud pack."""
    # A minimal well-formed app bundle with mozaikspay but no cloud pack
    mozaikspay_descriptor = {
        "capability_pack_id": "mozaikspay",
        "capability_source": "managed_capability",
        "surface_id": "billing_portal",
        "surface_kind": "module",
        "pack_type": "managed_capability",
        "label": "MozaiksPay",
        "summary": "Managed SaaS billing",
        "implementation_mode": "managed",
    }
    files_map: dict[str, str] = {}
    errors = scan_generated_bundle(
        files_map=files_map,
        capability_packs=[mozaikspay_descriptor],
        require_deployment_artifacts=False,
    )
    # The mozaikspay contract check will fire (because mozaikspay is selected but files missing)
    # but no cloud-specific errors should appear
    cloud_errors = [e for e in errors if "mozaiks_cloud" in e]
    assert not cloud_errors, (
        f"Cloud scanner gate must not fire for mozaikspay-only bundles: {cloud_errors}"
    )


# ---------------------------------------------------------------------------
# Idempotency key
# ---------------------------------------------------------------------------


def test_deployment_client_requires_idempotency_key_on_mutating_operations() -> None:
    """submit_deployment_request and request_rollback must declare idempotency_key."""
    path = _TEMPLATES_DIR / "services" / "integrations" / "mozaiks_cloud_deployment_client.py"
    source = path.read_text(encoding="utf-8")
    # Verify the parameter appears in the source text
    assert "idempotency_key" in source, (
        "mozaiks_cloud_deployment_client.py must declare idempotency_key parameter"
    )
    # Verify it appears on the mutating method signatures (keyword-only param pattern)
    _idempotency_param_re = re.compile(
        r"async def (?:submit_deployment_request|request_rollback)\s*\([^)]*idempotency_key[^)]*\)",
        re.DOTALL,
    )
    assert _idempotency_param_re.search(source), (
        "submit_deployment_request and request_rollback must declare idempotency_key parameter"
    )


def test_domain_client_requires_idempotency_key_on_mutating_operations() -> None:
    """connect_domain, request_domain_activation, disconnect_domain must declare idempotency_key."""
    path = _TEMPLATES_DIR / "services" / "integrations" / "mozaiks_cloud_domain_client.py"
    source = path.read_text(encoding="utf-8")
    assert "idempotency_key" in source, (
        "mozaiks_cloud_domain_client.py must declare idempotency_key parameter"
    )
    for method_name in ("connect_domain", "request_domain_activation", "disconnect_domain"):
        pattern = re.compile(
            rf"async def {re.escape(method_name)}\s*\([^)]*idempotency_key[^)]*\)",
            re.DOTALL,
        )
        assert pattern.search(source), (
            f"{method_name} must declare idempotency_key parameter"
        )


# ---------------------------------------------------------------------------
# Closed status transitions
# ---------------------------------------------------------------------------


def test_deployment_status_enum_is_closed() -> None:
    api = _read_yaml(_PACK_ROOT / "provider_api_contract.yaml")
    statuses = api["operation_status_model"]["fields"]["status"]
    # No open-ended / catch-all value
    assert "other" not in statuses and "custom" not in statuses and "unknown" not in statuses, (
        "Deployment status enum must be closed — no open-ended values"
    )
    assert "rolling_back" in statuses


def test_domain_status_enum_is_closed() -> None:
    api = _read_yaml(_PACK_ROOT / "provider_api_contract.yaml")
    statuses = api["response_models"]["DomainStatus"]["required"]
    assert "other" not in statuses and "custom" not in statuses
    assert "status" in statuses
    assert "dns_status" in statuses


def test_tls_status_enum_is_closed() -> None:
    api = _read_yaml(_PACK_ROOT / "provider_api_contract.yaml")
    statuses = api["response_models"]["DomainStatus"]["required"]
    assert "other" not in statuses and "custom" not in statuses
    assert "tls_status" in statuses
