"""Contract tests for the mozaikspay build context pack.

Verifies that:
- context.yaml registers the pack correctly for AppGenerator as managed_capability
- contract.yaml required_outputs all exist under templates; forbidden paths absent
- provider_api_contract.yaml ships with all required response fields and no forbidden fields
- The billing_portal module declares expected actions with correct permissions
- billing_portal capabilities are declared in context.yaml
- Reactions contract covers all 3 hosted subscription lifecycle events
- Profile tab declared for token wallet status
- Admin panels declared (my-usage, app-usage) on usage page
- Backend files compile (service.py, schemas.py, base_handler.py)
- Service routes through MozaiksPayClient, not direct provider calls
- Service never exposes provider-internal or forbidden response fields
- mozaikspay_client.py reads credentials from env/connector, no hardcoded secrets
- AppGenerator capability_directory wires mozaikspay as operator_pack
- AppGenerator capability_routing monetization layer names mozaikspay for subscriptions
- No modules/mozaikspay/, modules/wallet/, or services/managed_billing/ generated
- No checkout, webhook, invoice, or provider-credential logic in generated templates
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
MOZAIKSPAY = BUILD_CONTEXT / "mozaikspay"
TEMPLATES = MOZAIKSPAY / "templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _action_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {action["id"] for action in module_yaml.get("actions") or []}


def _capability_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {cap["capability_id"] for cap in module_yaml.get("capabilities") or []}


# ---------------------------------------------------------------------------
# Pack registration
# ---------------------------------------------------------------------------


def test_mozaikspay_context_registers_active_appgenerator_managed_pack() -> None:
    context = _read_yaml(MOZAIKSPAY / "context.yaml")

    assert context["context_id"] == "mozaikspay"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "mozaikspay"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "managed_capability"

    asset_kinds = {asset["kind"] for asset in context["assets"]}
    assert "contract" in asset_kinds
    assert "templates" in asset_kinds


def test_mozaikspay_context_declares_six_capabilities() -> None:
    context = _read_yaml(MOZAIKSPAY / "context.yaml")
    capability_ids = {cap["capability_id"] for cap in context["capabilities"]}

    assert capability_ids == {
        "mozaikspay.billing_portal",
        "mozaikspay.subscription_status",
        "mozaikspay.subscription_checkout",
        "mozaikspay.usage_status",
        "mozaikspay.token_status",
        "mozaikspay.token_top_up",
    }


def test_mozaikspay_context_declares_billing_portal_facade() -> None:
    context = _read_yaml(MOZAIKSPAY / "context.yaml")
    facade_ids = {f["module_id"] for f in (context.get("facades") or [])}
    assert "billing_portal" in facade_ids


# ---------------------------------------------------------------------------
# Required / forbidden outputs
# ---------------------------------------------------------------------------


def test_mozaikspay_contract_required_outputs_exist_under_templates() -> None:
    contract = _read_yaml(MOZAIKSPAY / "contract.yaml")

    assert contract["contract_id"] == "mozaikspay"
    assert contract["contract_type"] == "build_pack_instructions"

    missing = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "templates" and not (TEMPLATES / output["path"]).exists()
    ]
    assert missing == [], f"Missing required template outputs: {missing}"


def test_mozaikspay_pack_forbidden_outputs_are_absent() -> None:
    contract = _read_yaml(MOZAIKSPAY / "contract.yaml")

    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }

    for forbidden in contract.get("forbidden_outputs", []):
        if "path_prefix" in forbidden:
            assert not any(p.startswith(forbidden["path_prefix"]) for p in generated_paths), (
                f"Forbidden prefix found: {forbidden['path_prefix']}"
            )
        elif "path" in forbidden:
            assert forbidden["path"] not in generated_paths, (
                f"Forbidden path found: {forbidden['path']}"
            )


def test_mozaikspay_provider_api_contract_ships() -> None:
    """provider_api_contract.yaml must exist — it is the machine-readable API interface spec."""
    api_contract = MOZAIKSPAY / "provider_api_contract.yaml"
    assert api_contract.exists(), "mozaikspay pack must ship provider_api_contract.yaml"

    content = _read_yaml(api_contract)
    assert content.get("schema_version") == "mozaiks.provider_api_contract.v1"
    assert content.get("contract_id") == "mozaikspay_provider_api"


def test_mozaikspay_public_provider_contract_doc_ships() -> None:
    """The public docs must explain how a compatible provider replaces MozaiksPay."""
    doc = WORKSPACE / "docs" / "architecture" / "modules-systems" / "mozaikspay-provider-contract.md"
    assert doc.exists(), "MozaiksPay provider replacement contract doc must ship"

    text = doc.read_text(encoding="utf-8")
    assert "MozaiksPay is the default managed monetization provider" in text
    assert "compatible provider can replace the hosted" in text
    assert "provider_api_contract.yaml" in text
    assert "GET /api/mozaikspay/v1/subscription/status" in text
    assert "POST /api/mozaikspay/v1/billing-portal/session" in text
    assert "POST /api/mozaikspay/v1/subscription/checkout-session" in text
    assert "POST /api/mozaikspay/v1/tokens/top-up-session" in text
    assert "wallet, payout, settlement" in text


def test_mozaikspay_pack_contract_avoids_hosted_implementation_topology() -> None:
    """The OSS pack can describe provider lifecycle needs without naming App Zero internals."""
    text = (MOZAIKSPAY / "contract.yaml").read_text(encoding="utf-8")

    forbidden_hosted_details = (
        "hosted.hosting.app.deployed",
        "hosted_billing",
        "provision_app_billing_plans",
        "Stripe products",
        "Stripe prices",
        "price_ids",
        "hosted.billing.app_billing_plans",
    )
    for detail in forbidden_hosted_details:
        assert detail not in text

    contract = _read_yaml(MOZAIKSPAY / "contract.yaml")
    boundary = contract.get("provider_lifecycle_boundary") or []
    assert boundary
    assert boundary[0]["provider_role"] == "mozaikspay_compatible_provider"


def test_mozaikspay_provider_api_contract_declares_required_response_fields() -> None:
    contract = _read_yaml(MOZAIKSPAY / "contract.yaml")
    api = _read_yaml(MOZAIKSPAY / "provider_api_contract.yaml")

    # Contract declares required fields for each operation type
    sub_required = set(contract["provider_api_response_contract"]["subscription_status_required_fields"])

    # provider_api_contract.yaml must have endpoints that cover the subscription_status shape
    endpoint_ids = {ep["id"] for ep in (api.get("endpoints") or [])}
    assert "subscription_status" in endpoint_ids
    assert "billing_portal_session" in endpoint_ids

    # Required fields per contract must appear in the spec's success_shape keys
    sub_endpoint = next(ep for ep in api["endpoints"] if ep["id"] == "subscription_status")
    spec_fields = set((sub_endpoint.get("response") or {}).get("success_shape", {}).keys())
    missing = sub_required - spec_fields
    assert not missing, f"subscription_status response missing required fields: {missing}"


def test_mozaikspay_provider_api_contract_declares_globally_forbidden_fields() -> None:
    """The API contract must list provider-internal field names that must never appear in responses."""
    contract = _read_yaml(MOZAIKSPAY / "contract.yaml")
    forbidden = set(contract["provider_api_response_contract"]["globally_forbidden_response_fields"])

    # Spot-check the most critical forbidden fields
    assert "provider_customer_id" in forbidden
    assert "provider_subscription_id" in forbidden
    assert "client_secret" in forbidden


# ---------------------------------------------------------------------------
# Module: billing_portal
# ---------------------------------------------------------------------------


def test_billing_portal_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "billing_portal" / "module.yaml")

    assert _action_ids(module_yaml) == {
        "get_subscription_status",
        "get_usage_status",
        "get_token_status",
        "list_plans",
        "start_subscription_checkout",
        "start_token_top_up",
        "open_billing_portal",
    }


def test_billing_portal_module_has_no_user_data_scope() -> None:
    """billing_portal is a facade — it stores no user PII and must not declare user_data_scope."""
    module_yaml = _read_yaml(TEMPLATES / "modules" / "billing_portal" / "module.yaml")
    assert not module_yaml.get("module", {}).get("user_data_scope"), (
        "billing_portal is a read-through facade and must not declare user_data_scope"
    )


def test_billing_portal_module_permissions_are_declared() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "billing_portal" / "module.yaml")
    permission_ids = {p["id"] for p in (module_yaml.get("permissions") or [])}
    assert "billing_portal.read" in permission_ids
    assert "billing_portal.manage" in permission_ids


def test_billing_portal_read_actions_require_read_permission() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "billing_portal" / "module.yaml")
    read_actions = {"get_subscription_status", "get_usage_status", "get_token_status", "list_plans"}
    for action in (module_yaml.get("actions") or []):
        if action["id"] in read_actions:
            assert "billing_portal.read" in action.get("permissions", []), (
                f"{action['id']} must require billing_portal.read"
            )


def test_billing_portal_manage_actions_require_manage_permission() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "billing_portal" / "module.yaml")
    manage_actions = {"start_subscription_checkout", "start_token_top_up", "open_billing_portal"}
    for action in (module_yaml.get("actions") or []):
        if action["id"] in manage_actions:
            assert "billing_portal.manage" in action.get("permissions", []), (
                f"{action['id']} must require billing_portal.manage"
            )


# ---------------------------------------------------------------------------
# Reactions contract
# ---------------------------------------------------------------------------


def test_billing_portal_reactions_cover_subscription_lifecycle() -> None:
    reactions = _read_yaml(
        TEMPLATES / "modules" / "billing_portal" / "contracts" / "reactions.yaml"
    )
    assert reactions.get("schema_version") == "mozaiks.reactions.v1"

    event_types = {r["event_type"] for r in (reactions.get("reactions") or [])}
    assert "hosted.billing.subscription.activated" in event_types
    assert "hosted.billing.subscription.updated" in event_types
    assert "hosted.billing.subscription.cancelled" in event_types


def test_billing_portal_reactions_are_read_only() -> None:
    """Subscription reactions must not mutate billing/entitlement state — they are display cache only."""
    reactions_text = _read_text(
        TEMPLATES / "modules" / "billing_portal" / "contracts" / "reactions.yaml"
    )
    # The contract must document that reactions are read-only
    assert "Read-only" in reactions_text or "read-only" in reactions_text or "never mutates" in reactions_text, (
        "reactions.yaml must document read-only constraint for hosted billing reactions"
    )


# ---------------------------------------------------------------------------
# Profile and admin contracts
# ---------------------------------------------------------------------------


def test_billing_portal_profile_tab_declared_for_token_status() -> None:
    profile = _read_yaml(
        TEMPLATES / "modules" / "billing_portal" / "contracts" / "profile.yaml"
    )
    assert profile.get("schema_version") == "mozaiks.profile.v1"
    tab_ids = {tab["id"] for tab in (profile.get("tabs") or [])}
    assert "tokens" in tab_ids


def test_billing_portal_admin_panels_declared_for_usage_page() -> None:
    admin = _read_yaml(
        TEMPLATES / "modules" / "billing_portal" / "contracts" / "admin.yaml"
    )
    assert admin.get("schema_version") == "mozaiks.admin.v2"
    panel_ids = {panel["id"] for panel in (admin.get("panels") or [])}
    assert "my-usage" in panel_ids
    assert "app-usage" in panel_ids


# ---------------------------------------------------------------------------
# UI contracts
# ---------------------------------------------------------------------------


def test_mozaikspay_billing_page_template_ships() -> None:
    billing = TEMPLATES / "ui" / "pages" / "billing.yaml"
    assert billing.exists()
    content = _read_yaml(billing)
    assert content.get("route") == "/billing"
    assert content.get("schema_version") == "mozaiks.page.v1"


def test_mozaikspay_usage_page_template_ships() -> None:
    usage = TEMPLATES / "ui" / "pages" / "usage.yaml"
    assert usage.exists()
    content = _read_yaml(usage)
    assert content.get("route") == "/usage"
    assert content.get("schema_version") == "mozaiks.page.v1"


def test_mozaikspay_pages_bind_through_billing_portal_facade() -> None:
    """Pages must call billing_portal actions, never the hosted MozaiksPay API directly."""
    for page_file in (TEMPLATES / "ui" / "pages").glob("*.yaml"):
        content = page_file.read_text(encoding="utf-8")
        assert "mozaikspay/v1" not in content, (
            f"{page_file.name} must not call hosted MozaiksPay API directly — "
            "bind through billing_portal facade actions"
        )


# ---------------------------------------------------------------------------
# Backend template contracts
# ---------------------------------------------------------------------------


def test_mozaikspay_backend_templates_compile() -> None:
    backend_root = TEMPLATES / "modules" / "billing_portal" / "backend"
    for path in backend_root.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_mozaikspay_client_compiles() -> None:
    client_path = TEMPLATES / "services" / "integrations" / "mozaikspay_client.py"
    compile(client_path.read_text(encoding="utf-8"), str(client_path), "exec")


def test_mozaikspay_service_routes_through_client() -> None:
    """Service must use MozaiksPayClient, not direct HTTP calls or provider SDKs."""
    service_text = _read_text(TEMPLATES / "modules" / "billing_portal" / "backend" / "service.py")

    assert "MozaiksPayClient" in service_text, (
        "billing_portal/service.py must use MozaiksPayClient for all hosted API calls"
    )


def test_mozaikspay_service_does_not_contain_provider_internals() -> None:
    """Generated service must not contain Stripe calls, webhook logic, or invoice operations."""
    service_text = _read_text(TEMPLATES / "modules" / "billing_portal" / "backend" / "service.py")

    forbidden_patterns = [
        "stripe",
        "webhook",
        "invoice",
        "checkout.Session",
        "customer.create",
        "subscription.create",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in service_text.lower(), (
            f"billing_portal/service.py must not contain provider internal '{pattern}'"
        )


def test_mozaikspay_service_does_not_expose_forbidden_response_fields() -> None:
    """Service must strip provider-internal fields before returning to callers."""
    contract = _read_yaml(MOZAIKSPAY / "contract.yaml")
    forbidden = contract["provider_api_response_contract"]["globally_forbidden_response_fields"]
    service_text = _read_text(TEMPLATES / "modules" / "billing_portal" / "backend" / "service.py")
    schemas_text = _read_text(TEMPLATES / "modules" / "billing_portal" / "backend" / "schemas.py")

    for field in forbidden:
        # Neither service output dicts nor schema models should key on forbidden field names
        assert f'"{field}"' not in service_text, (
            f"service.py must not expose forbidden field '{field}'"
        )
        assert f'"{field}"' not in schemas_text, (
            f"schemas.py must not expose forbidden field '{field}'"
        )


def test_mozaikspay_client_reads_credentials_from_env_or_connector() -> None:
    """Client must not hardcode credentials — reads from env vars or connector config."""
    client_text = _read_text(TEMPLATES / "services" / "integrations" / "mozaikspay_client.py")

    assert "MOZAIKSPAY_API_KEY" in client_text or "MOZAIKSPAY_CLIENT_SECRET" in client_text, (
        "mozaikspay_client.py must read credentials from MOZAIKSPAY_* env vars or connector config"
    )
    assert "mzk_live_" not in client_text and "mzk_test_" not in client_text, (
        "mozaikspay_client.py must not hardcode any API key values"
    )


# ---------------------------------------------------------------------------
# AppGenerator wiring
# ---------------------------------------------------------------------------


def test_appgenerator_capability_directory_wires_mozaikspay_as_operator_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "mozaikspay" in by_id, "capability_directory must have 'mozaikspay' entry"
    entry = by_id["mozaikspay"]
    assert entry["capability_kind"] == "operator_pack"
    assert "billing" in entry.get("domains", []) or "subscriptions" in entry.get("domains", [])


def test_appgenerator_capability_routing_monetization_layer_names_mozaikspay() -> None:
    routing = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_routing.yaml")
    monetization = routing["layers"]["monetization"]

    entries = monetization.get("entries") or []
    subscription_entry = next(
        (e for e in entries if e.get("revenue_model") == "subscriptions"),
        None,
    )
    assert subscription_entry is not None, (
        "capability_routing monetization layer must have a subscriptions revenue_model entry"
    )
    assert subscription_entry.get("capability_pack") == "mozaikspay", (
        "subscriptions revenue_model entry must name mozaikspay as the capability_pack"
    )


# ---------------------------------------------------------------------------
# No provider internals, no forbidden modules
# ---------------------------------------------------------------------------


def test_mozaikspay_pack_does_not_generate_forbidden_module_paths() -> None:
    """No wallet module, no monolithic mozaikspay module, no managed_billing service."""
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }
    assert not any(p.startswith("modules/mozaikspay/") for p in generated_paths)
    assert not any(p.startswith("modules/wallet/") for p in generated_paths)
    assert not any(p.startswith("services/managed_billing/") for p in generated_paths)
    assert not any(p.startswith("services/wallet/") for p in generated_paths)
    assert not any(p.startswith("app/capability_packs/") for p in generated_paths)
