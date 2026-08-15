"""Contract tests for the mozaiks_cloud build context pack.

Verifies that:
- context.yaml registers the pack correctly for AppGenerator as managed_capability
- contract.yaml required_outputs all exist under templates; forbidden paths absent
- provider_api_contract.yaml ships with required response fields and no forbidden fields
- cloud_deployment module declares expected actions with correct permissions
- cloud_domain module declares expected actions with correct permissions
- Capabilities declared in context.yaml match facade modules
- Callback/reaction contracts remain absent; generated apps poll operation status
- Client files compile without errors
- Client reads credentials from env/connector; no hardcoded secrets
- No Azure, Cloudflare, or GitHub SDK imports in any generated template
- AppGenerator capability_directory wires mozaiks_cloud as operator_pack
- No monolithic mozaiks_cloud module; no forbidden adapter paths generated
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
PACK_ROOT = BUILD_CONTEXT / "mozaiks_cloud"
TEMPLATES = PACK_ROOT / "templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _action_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {action["id"] for action in module_yaml.get("actions") or []}


def _permission_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {p["id"] for p in module_yaml.get("permissions") or []}


# ---------------------------------------------------------------------------
# Pack registration
# ---------------------------------------------------------------------------


def test_mozaiks_cloud_context_registers_active_appgenerator_managed_pack() -> None:
    context = _read_yaml(PACK_ROOT / "context.yaml")

    assert context["context_id"] == "mozaiks_cloud"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "mozaiks_cloud"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "managed_capability"

    asset_kinds = {asset["kind"] for asset in context["assets"]}
    assert "contract" in asset_kinds
    assert "templates" in asset_kinds


def test_mozaiks_cloud_context_declares_capabilities() -> None:
    context = _read_yaml(PACK_ROOT / "context.yaml")
    capability_ids = {cap["capability_id"] for cap in context["capabilities"]}

    assert capability_ids == {
        "cloud.deployment.submit",
        "cloud.deployment.status",
        "cloud.deployment.health",
        "cloud.deployment.rollback",
        "cloud.environment.endpoints",
        "cloud.domain.connect",
        "cloud.domain.status",
        "cloud.domain.disconnect",
    }


def test_mozaiks_cloud_context_declares_two_bounded_facades() -> None:
    context = _read_yaml(PACK_ROOT / "context.yaml")
    facade_ids = {f["module_id"] for f in (context.get("facades") or [])}
    assert "cloud_deployment" in facade_ids
    assert "cloud_domain" in facade_ids


def test_mozaiks_cloud_context_declares_deployment_env_secret() -> None:
    context = _read_yaml(PACK_ROOT / "context.yaml")
    deployment_env = context["pack"].get("deployment_env", {})
    secret_vars = set(deployment_env.get("secret", []))
    assert "MOZAIKS_CLOUD_API_KEY" in secret_vars


def test_mozaiks_cloud_context_deployment_env_optional_vars() -> None:
    context = _read_yaml(PACK_ROOT / "context.yaml")
    deployment_env = context["pack"].get("deployment_env", {})
    optional_vars = set(deployment_env.get("optional", []))
    assert "MOZAIKS_CLOUD_API_BASE" in optional_vars
    assert "MOZAIKS_CLOUD_APP_ID" in optional_vars


# ---------------------------------------------------------------------------
# Required / forbidden outputs
# ---------------------------------------------------------------------------


def test_mozaiks_cloud_contract_required_outputs_exist_under_templates() -> None:
    contract = _read_yaml(PACK_ROOT / "contract.yaml")

    assert contract["contract_id"] == "mozaiks_cloud_connector"
    assert contract["contract_type"] == "build_pack_instructions"

    missing = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "templates" and not (TEMPLATES / output["path"]).exists()
    ]
    assert missing == [], f"Missing required template outputs: {missing}"


def test_mozaiks_cloud_pack_forbidden_outputs_are_absent() -> None:
    contract = _read_yaml(PACK_ROOT / "contract.yaml")

    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }

    for forbidden in contract.get("forbidden_outputs", []):
        if "path_prefix" in forbidden:
            assert not any(p.startswith(forbidden["path_prefix"]) for p in generated_paths), (
                f"Forbidden prefix found in templates: {forbidden['path_prefix']}"
            )
        elif "path" in forbidden:
            assert forbidden["path"] not in generated_paths, (
                f"Forbidden path found in templates: {forbidden['path']}"
            )


def test_mozaiks_cloud_provider_api_contract_ships() -> None:
    """provider_api_contract.yaml must exist and declare the versioned API spec."""
    api_contract = PACK_ROOT / "provider_api_contract.yaml"
    assert api_contract.exists(), "mozaiks_cloud pack must ship provider_api_contract.yaml"

    content = _read_yaml(api_contract)
    assert content.get("schema_version") == "mozaiks.provider_api_contract.v1"
    assert content.get("contract_id") == "mozaiks_cloud_provider_api"
    assert content.get("base_path") == "/api/mozaiks-cloud/v1"
    assert content.get("api_version", {}).get("header") == "X-MozaiksCloud-Api-Version"
    assert content.get("api_version", {}).get("current") == "mozaiks.cloud.v1"


def test_mozaiks_cloud_provider_api_contract_is_provider_compatible() -> None:
    """The machine-readable contract must allow a compatible provider to satisfy it."""
    api_contract = PACK_ROOT / "provider_api_contract.yaml"
    text = api_contract.read_text(encoding="utf-8")

    assert "mozaiks_cloud_compatible_provider" in (PACK_ROOT / "contract.yaml").read_text(encoding="utf-8")
    assert "managed_service_bearer" in text


def test_mozaiks_cloud_provider_api_contract_declares_deployment_endpoints() -> None:
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")
    endpoint_ids = {ep["id"] for ep in (api.get("operations") or [])}

    assert "submit_deployment" in endpoint_ids
    assert "get_deployment_operation_status" in endpoint_ids
    assert "get_environment_endpoints" in endpoint_ids
    assert "request_rollback" in endpoint_ids
    assert "get_deployment_health" in endpoint_ids


def test_mozaiks_cloud_provider_api_contract_declares_domain_endpoints() -> None:
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")
    endpoint_ids = {ep["id"] for ep in (api.get("operations") or [])}

    assert "connect_domain" in endpoint_ids
    assert "get_domain_verification" in endpoint_ids
    assert "get_dns_instructions" in endpoint_ids
    assert "check_domain_verification" in endpoint_ids
    assert "request_domain_activation" in endpoint_ids
    assert "get_domain_status" in endpoint_ids
    assert "disconnect_domain" in endpoint_ids


def test_mozaiks_cloud_provider_api_contract_has_exact_operation_set() -> None:
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")
    endpoint_ids = {ep["id"] for ep in (api.get("operations") or [])}

    assert endpoint_ids == {
        "submit_deployment",
        "get_deployment_operation_status",
        "get_environment_endpoints",
        "get_deployment_health",
        "request_rollback",
        "connect_domain",
        "get_domain_verification",
        "get_dns_instructions",
        "check_domain_verification",
        "request_domain_activation",
        "get_domain_status",
        "disconnect_domain",
    }


def test_mozaiks_cloud_provider_api_contract_model_references_are_closed() -> None:
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")
    request_models = set(api["request_models"])
    response_models = set(api["response_models"]) | {"OperationStatus"}

    missing_requests = {
        op["request_model"]
        for op in api["operations"]
        if op["request_model"] not in request_models
    }
    missing_responses = {
        op["response_model"]
        for op in api["operations"]
        if op["response_model"] not in response_models
    }

    assert missing_requests == set()
    assert missing_responses == set()


def test_mozaiks_cloud_provider_api_contract_auth_version_and_errors_are_canonical() -> None:
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")

    assert api["provider_id"] == "mozaiks_cloud"
    assert api["base_path"] == "/api/mozaiks-cloud/v1"
    assert api["api_version"] == {
        "header": "X-MozaiksCloud-Api-Version",
        "current": "mozaiks.cloud.v1",
    }
    assert api["auth"]["kind"] == "managed_service_bearer"
    assert api["auth"]["service"] == "mozaiks_cloud"
    assert api["auth"]["credential_reference"] == "MOZAIKS_CLOUD_API_KEY"
    assert api["error_envelope"]["success_shape"] == {"success": True}
    assert api["error_envelope"]["error_shape"]["success"] is False


def test_mozaiks_cloud_provider_api_contract_idempotency_rules_are_complete() -> None:
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")

    assert api["idempotency"]["header"] == "Idempotency-Key"
    assert set(api["idempotency"]["required_for_methods"]) == {"POST", "DELETE"}
    mutating = [op for op in api["operations"] if op["method"] in {"POST", "DELETE"}]
    assert mutating
    assert all(op["idempotency"] == "required" for op in mutating)
    assert all(op["idempotency"] == "not_allowed" for op in api["operations"] if op["method"] == "GET")


def test_mozaiks_cloud_provider_api_contract_declares_globally_forbidden_fields() -> None:
    contract = _read_yaml(PACK_ROOT / "contract.yaml")
    forbidden = set(contract["provider_api_response_contract"]["globally_forbidden_response_fields"])

    assert "azure_resource_id" in forbidden
    assert "azure_subscription_id" in forbidden
    assert "cloudflare_account_id" in forbidden
    assert "cloudflare_zone_id" in forbidden
    assert "github_installation_id" in forbidden
    assert "ssl_private_key" in forbidden
    assert "tls_private_key" in forbidden
    assert "provider_credentials" in forbidden


def test_mozaiks_cloud_provider_api_contract_required_fields_covered() -> None:
    contract = _read_yaml(PACK_ROOT / "contract.yaml")
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")

    operation_fields = set(api["operation_status_model"]["fields"])
    missing = set(contract["provider_api_response_contract"]["operation_required_fields"]) - operation_fields
    assert not missing, f"operation_status_model missing required fields: {missing}"
    assert "DomainStatus" in api["response_models"]
    assert "DeploymentHealth" in api["response_models"]


def test_mozaiks_cloud_provider_api_contract_declares_closed_status_enums() -> None:
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")
    status_type = api["operation_status_model"]["fields"]["status"]
    for value in ("pending", "running", "succeeded", "failed", "cancelled", "rolling_back", "activating"):
        assert value in status_type
    assert "unknown" not in status_type


def test_mozaiks_cloud_provider_api_contract_declares_error_taxonomy() -> None:
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")
    error_kind_type = api["error_envelope"]["error_shape"]["error"]["kind"]

    required_kinds = {
        "auth_error", "rate_limited", "validation_error", "not_found",
        "conflict", "provider_error", "timeout", "unknown",
    }
    assert required_kinds <= set(error_kind_type.split(" | "))


# ---------------------------------------------------------------------------
# Module: cloud_deployment
# ---------------------------------------------------------------------------


def test_cloud_deployment_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_deployment" / "module.yaml"
    )
    assert _action_ids(module_yaml) == {
        "submit_deployment",
        "get_deployment_status",
        "get_environment_endpoints",
        "get_deployment_health",
        "request_rollback",
    }


def test_cloud_deployment_module_declares_expected_permissions() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_deployment" / "module.yaml"
    )
    perm_ids = _permission_ids(module_yaml)
    assert "cloud_deployment.read" in perm_ids
    assert "cloud_deployment.write" in perm_ids


def test_cloud_deployment_module_id_is_canonical() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_deployment" / "module.yaml"
    )
    assert module_yaml.get("module", {}).get("id") == "cloud_deployment"


def test_cloud_deployment_read_actions_require_read_permission() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_deployment" / "module.yaml"
    )
    read_actions = {"get_deployment_status", "get_environment_endpoints"}
    for action in (module_yaml.get("actions") or []):
        if action["id"] in read_actions:
            assert "cloud_deployment.read" in action.get("permissions", []), (
                f"{action['id']} must require cloud_deployment.read"
            )


def test_cloud_deployment_write_actions_require_write_permission() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_deployment" / "module.yaml"
    )
    write_actions = {"submit_deployment", "request_rollback"}
    for action in (module_yaml.get("actions") or []):
        if action["id"] in write_actions:
            assert "cloud_deployment.write" in action.get("permissions", []), (
                f"{action['id']} must require cloud_deployment.write"
            )


def test_cloud_deployment_module_has_no_user_data_scope() -> None:
    """cloud_deployment is a facade with no user PII — must not declare user_data_scope."""
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_deployment" / "module.yaml"
    )
    assert not module_yaml.get("module", {}).get("user_data_scope"), (
        "cloud_deployment is a facade and must not declare user_data_scope"
    )


# ---------------------------------------------------------------------------
# Module: cloud_domain
# ---------------------------------------------------------------------------


def test_cloud_domain_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_domain" / "module.yaml"
    )
    assert _action_ids(module_yaml) == {
        "connect_domain",
        "get_domain_verification",
        "get_dns_instructions",
        "request_domain_activation",
        "get_domain_status",
        "disconnect_domain",
    }


def test_cloud_domain_module_declares_expected_permissions() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_domain" / "module.yaml"
    )
    perm_ids = _permission_ids(module_yaml)
    assert "cloud_domain.read" in perm_ids
    assert "cloud_domain.manage" in perm_ids


def test_cloud_domain_module_id_is_canonical() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_domain" / "module.yaml"
    )
    assert module_yaml.get("module", {}).get("id") == "cloud_domain"


def test_cloud_domain_read_actions_require_read_permission() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_domain" / "module.yaml"
    )
    read_actions = {"get_domain_verification", "get_dns_instructions", "get_domain_status"}
    for action in (module_yaml.get("actions") or []):
        if action["id"] in read_actions:
            assert "cloud_domain.read" in action.get("permissions", []), (
                f"{action['id']} must require cloud_domain.read"
            )


def test_cloud_domain_manage_actions_require_manage_permission() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_domain" / "module.yaml"
    )
    manage_actions = {"connect_domain", "request_domain_activation", "disconnect_domain"}
    for action in (module_yaml.get("actions") or []):
        if action["id"] in manage_actions:
            assert "cloud_domain.manage" in action.get("permissions", []), (
                f"{action['id']} must require cloud_domain.manage"
            )


def test_cloud_domain_module_has_no_user_data_scope() -> None:
    """cloud_domain is a facade with no user PII — must not declare user_data_scope."""
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "cloud_domain" / "module.yaml"
    )
    assert not module_yaml.get("module", {}).get("user_data_scope"), (
        "cloud_domain is a facade and must not declare user_data_scope"
    )


# ---------------------------------------------------------------------------
# Callback/reaction boundary
# ---------------------------------------------------------------------------


def test_mozaiks_cloud_pack_does_not_generate_callback_or_reaction_routes() -> None:
    assert not (TEMPLATES / "modules" / "cloud_deployment" / "contracts" / "reactions.yaml").exists()
    assert not (TEMPLATES / "modules" / "cloud_domain" / "contracts" / "reactions.yaml").exists()
    api = _read_yaml(PACK_ROOT / "provider_api_contract.yaml")
    assert api["callbacks"]["generated_app_callback_routes"] == "unsupported"
    assert api["callbacks"]["polling_contract"] == "use_operations_endpoint"


# ---------------------------------------------------------------------------
# Event boundary
# ---------------------------------------------------------------------------


def test_mozaiks_cloud_pack_does_not_generate_event_manifests_without_emitters() -> None:
    assert not (TEMPLATES / "modules" / "cloud_deployment" / "contracts" / "events.yaml").exists()
    assert not (TEMPLATES / "modules" / "cloud_domain" / "contracts" / "events.yaml").exists()


# ---------------------------------------------------------------------------
# Client template compilation and safety
# ---------------------------------------------------------------------------


def test_mozaiks_cloud_client_templates_compile() -> None:
    for name in (
        "mozaiks_cloud_client.py",
        "mozaiks_cloud_deployment_client.py",
        "mozaiks_cloud_domain_client.py",
    ):
        path = TEMPLATES / "services" / "integrations" / name
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_mozaiks_cloud_templates_contain_no_placeholder_or_fake_success_code() -> None:
    forbidden_patterns = {
        "NotImplementedError": "not implemented placeholder",
        "TODO": "todo placeholder",
        "placeholder": "placeholder",
        "return {\"success\": True}": "silent fake success",
        "return {'success': True}": "silent fake success",
    }
    violations: list[tuple[str, str]] = []
    for path in TEMPLATES.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml"}:
            continue
        content = path.read_text(encoding="utf-8")
        for needle, reason in forbidden_patterns.items():
            if needle in content:
                violations.append((str(path.relative_to(TEMPLATES)), reason))
        if path.suffix == ".py" and "\n    pass\n" in content:
            violations.append((str(path.relative_to(TEMPLATES)), "empty pass body"))

    assert violations == []


def test_mozaiks_cloud_facade_actions_have_handler_methods() -> None:
    for module_id in ("cloud_deployment", "cloud_domain"):
        module_yaml = _read_yaml(TEMPLATES / "modules" / module_id / "module.yaml")
        handler_source = _read_text(TEMPLATES / "modules" / module_id / "backend" / "handler.py")
        missing = [
            action["handler_method"]
            for action in module_yaml["actions"]
            if f"async def {action['handler_method']}(" not in handler_source
        ]
        assert missing == []


def test_mozaiks_cloud_transport_client_reads_credentials_from_env_or_connector() -> None:
    client_text = _read_text(
        TEMPLATES / "services" / "integrations" / "mozaiks_cloud_client.py"
    )
    assert "MOZAIKS_CLOUD_API_KEY" in client_text, (
        "mozaiks_cloud_client.py must read the API key from MOZAIKS_CLOUD_API_KEY"
    )
    assert "MOZAIKS_CLOUD_API_BASE" in client_text, (
        "mozaiks_cloud_client.py must read the base URL from MOZAIKS_CLOUD_API_BASE"
    )
    assert "ConnectorStore" in client_text, (
        "mozaiks_cloud_client.py must use ConnectorStore for app-scoped credential resolution"
    )
    assert "get_connector_vault_backend" in client_text, (
        "mozaiks_cloud_client.py must use the connector vault for secret resolution"
    )


def test_mozaiks_cloud_client_no_hardcoded_credentials() -> None:
    for name in (
        "mozaiks_cloud_client.py",
        "mozaiks_cloud_deployment_client.py",
        "mozaiks_cloud_domain_client.py",
    ):
        text = _read_text(TEMPLATES / "services" / "integrations" / name)
        assert "mzk_live_" not in text and "mzk_test_" not in text, (
            f"{name} must not hardcode any API key values"
        )
        # No raw Azure/Cloudflare/GitHub SDK calls
        assert "azure." not in text.lower().split("import")[-1] or "import azure" not in text.lower(), (
            f"{name} must not import Azure SDK"
        )


def test_mozaiks_cloud_templates_contain_no_azure_sdk_imports() -> None:
    """No generated template file may import azure or cloudflare SDKs."""
    import re
    raw_sdk_re = re.compile(
        r"(?m)^\s*(?:import\s+(?:azure|cloudflare)\b|from\s+(?:azure|cloudflare)\s+import\b)",
        re.IGNORECASE,
    )
    violations = []
    for path in TEMPLATES.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if raw_sdk_re.search(content):
            violations.append(str(path.relative_to(TEMPLATES)))
    assert not violations, (
        f"Templates must not import Azure/Cloudflare SDKs: {violations}"
    )


def test_mozaiks_cloud_deployment_client_has_bounded_operations() -> None:
    text = _read_text(
        TEMPLATES / "services" / "integrations" / "mozaiks_cloud_deployment_client.py"
    )
    assert "submit_deployment_request" in text
    assert "get_operation_status" in text
    assert "get_environment_endpoints" in text
    assert "request_rollback" in text
    assert "get_deployment_health" in text
    # Must not contain domain operations
    assert "connect_domain" not in text
    assert "get_dns_instructions" not in text


def test_mozaiks_cloud_domain_client_has_bounded_operations() -> None:
    text = _read_text(
        TEMPLATES / "services" / "integrations" / "mozaiks_cloud_domain_client.py"
    )
    assert "connect_domain" in text
    assert "get_domain_verification" in text
    assert "get_dns_instructions" in text
    assert "request_domain_activation" in text
    assert "get_domain_status" in text
    assert "disconnect_domain" in text
    # Must not contain deployment operations
    assert "submit_deployment" not in text
    assert "request_rollback" not in text


def test_environment_endpoint_operation_stays_in_deployment_subclient() -> None:
    text = _read_text(
        TEMPLATES / "services" / "integrations" / "mozaiks_cloud_deployment_client.py"
    )
    assert "get_environment_endpoints" in text
    assert not (TEMPLATES / "services" / "integrations" / "mozaiks_cloud_environment_client.py").exists()


def test_mozaiks_cloud_transport_has_bounded_retries() -> None:
    text = _read_text(
        TEMPLATES / "services" / "integrations" / "mozaiks_cloud_client.py"
    )
    assert "_DEFAULT_MAX_RETRIES" in text, (
        "Transport must declare bounded max retries"
    )
    assert "max_retries" in text, (
        "Transport must enforce a max_retries limit"
    )


def test_mozaiks_cloud_transport_uses_idempotency_key() -> None:
    text = _read_text(
        TEMPLATES / "services" / "integrations" / "mozaiks_cloud_client.py"
    )
    assert "Idempotency-Key" in text, (
        "Transport must forward idempotency_key in request headers"
    )
    assert "idempotency_key" in text, (
        "Transport request() method must accept idempotency_key parameter"
    )


def test_mozaiks_cloud_transport_normalizes_error_envelope() -> None:
    text = _read_text(
        TEMPLATES / "services" / "integrations" / "mozaiks_cloud_client.py"
    )
    assert "_error_from_response" in text
    assert "error_envelope" not in text
    assert "auth_error" in text
    assert "rate_limited" in text
    assert "not_found" in text
    assert "provider_error" in text


# ---------------------------------------------------------------------------
# Contract avoids hosted implementation topology
# ---------------------------------------------------------------------------


def test_mozaiks_cloud_contract_avoids_hosted_implementation_details() -> None:
    # Provider-internal field names are allowed only inside globally_forbidden_response_fields
    # (where they serve as a blocklist). Check that they don't appear as env var names,
    # required_fields references, runtime_boundaries rules, or credential config.
    contract = _read_yaml(PACK_ROOT / "contract.yaml")

    # These must not appear as env var names in required_integrations or runtime_boundaries
    env_var_forbidden = (
        "AZURE_CLIENT_SECRET",
        "CLOUDFLARE_GLOBAL_API_KEY",
        "GITHUB_PAT",
    )
    contract_text_outside_forbidden_list = (PACK_ROOT / "contract.yaml").read_text(encoding="utf-8")
    # Remove the globally_forbidden_response_fields block for this check
    lines_outside = [
        line for line in contract_text_outside_forbidden_list.splitlines()
        if "azure_resource_id" not in line and "cloudflare_api_token" not in line
        and "github_installation_id" not in line and "ssl_private_key" not in line
        and "globally_forbidden" not in line
    ]
    text_outside = "\n".join(lines_outside)

    for detail in env_var_forbidden:
        assert detail not in text_outside, (
            f"contract.yaml must not reference provider env var credential: {detail}"
        )

    # stripe must never appear anywhere (it's a payment provider, not a cloud provider)
    assert "stripe" not in contract_text_outside_forbidden_list.lower(), (
        "contract.yaml must not reference stripe"
    )

    boundary = contract.get("provider_lifecycle_boundary") or []
    assert boundary, "contract.yaml must declare provider_lifecycle_boundary"
    provider_roles = {b.get("provider_role") for b in boundary}
    assert "mozaiks_cloud_compatible_provider" in provider_roles


def test_mozaiks_cloud_contract_selection_requires_explicit_confirmation() -> None:
    contract = _read_yaml(PACK_ROOT / "contract.yaml")
    rules = contract.get("selection_rules") or []
    actions = {r.get("action") for r in rules}
    # All selection rules must use require_explicit_user_confirmation;
    # the pack must never auto-select
    assert "select_pack" not in actions, (
        "mozaiks_cloud must never auto-select — all rules must require explicit confirmation"
    )
    assert "require_explicit_user_confirmation" in actions


def test_mozaiks_cloud_contract_inactive_surfaces_exclude_domain_purchasing() -> None:
    contract = _read_yaml(PACK_ROOT / "contract.yaml")
    inactive = set(contract.get("inactive_surfaces") or [])
    assert "domain_purchasing" in inactive
    assert "registrar_transfer" in inactive


# ---------------------------------------------------------------------------
# AppGenerator wiring
# ---------------------------------------------------------------------------


def test_appgenerator_capability_directory_wires_mozaiks_cloud_as_operator_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "mozaiks_cloud" in by_id, "capability_directory must have 'mozaiks_cloud' entry"
    entry = by_id["mozaiks_cloud"]
    assert entry["capability_kind"] == "operator_pack"
    domains = entry.get("domains", [])
    assert "deployments" in domains or "hosting" in domains


def test_appgenerator_capability_directory_mozaiks_cloud_declares_facades() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}
    entry = by_id["mozaiks_cloud"]
    adapter = entry.get("adapter_contract", {})
    facades = set(adapter.get("app_facing_facades", []))
    assert "cloud_deployment" in facades
    assert "cloud_domain" in facades


def test_appgenerator_capability_directory_mozaiks_cloud_requires_explicit_selection() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}
    entry = by_id["mozaiks_cloud"]
    assert entry.get("selection_rule") == "require_explicit_user_confirmation", (
        "mozaiks_cloud must declare require_explicit_user_confirmation selection_rule"
    )


# ---------------------------------------------------------------------------
# No provider internals in templates
# ---------------------------------------------------------------------------


def test_mozaiks_cloud_pack_does_not_generate_monolithic_cloud_module() -> None:
    """Must not generate a god modules/mozaiks_cloud/ module."""
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }
    assert not any(p.startswith("modules/mozaiks_cloud/") for p in generated_paths), (
        "Must not generate a monolithic modules/mozaiks_cloud/ module"
    )


def test_mozaiks_cloud_pack_does_not_generate_provider_adapter_paths() -> None:
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }
    forbidden_prefixes = [
        "services/adapters/azure/",
        "services/adapters/cloudflare/",
        "services/adapters/github/",
        "services/adapters/dns/",
        "infra/azure/",
    ]
    for prefix in forbidden_prefixes:
        assert not any(p.startswith(prefix) for p in generated_paths), (
            f"Must not generate provider adapter path: {prefix}"
        )
