"""
MozaiksPay managed capability contract tests.

Verifies that the mozaikspay OSS build pack:
  - has a valid context.yaml and contract.yaml
  - all required_outputs have matching template files
  - no forbidden_output path prefixes appear in templates
  - the app-side client (mozaikspay_client.py) is provider-neutral
  - the billing_portal facade module is app-owned
  - generated pages call billing_portal actions only, not managed modules directly
  - no raw payment provider secrets or provider internals bleed into generated output
  - runtime boundary rules are documented in contract.yaml
"""
from __future__ import annotations

from pathlib import Path

import yaml

from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    generate_deployment_artifacts,
)
from factory_app.workflows.AppGenerator.tools.generate_and_download import (
    _deployment_env_for_capability_packs,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    scan_generated_bundle,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_managed_capability_templates,
)

# ---------------------------------------------------------------------------
# Pack paths
# ---------------------------------------------------------------------------

_PACK_ROOT = (
    Path(__file__).resolve().parents[1]
    / "factory_app"
    / "build_context"
    / "mozaikspay"
)
_CONTEXT_YAML = _PACK_ROOT / "context.yaml"
_CONTRACT_YAML = _PACK_ROOT / "contract.yaml"
_TEMPLATES_DIR = _PACK_ROOT / "templates"
_CLIENT_TEMPLATE = _TEMPLATES_DIR / "services" / "integrations" / "mozaikspay_client.py"
_MODULE_YAML = _TEMPLATES_DIR / "modules" / "billing_portal" / "module.yaml"
_BILLING_PAGE = _TEMPLATES_DIR / "ui" / "pages" / "billing.yaml"
_USAGE_PAGE = _TEMPLATES_DIR / "ui" / "pages" / "usage.yaml"


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_template_files() -> list[Path]:
    return [f for f in _TEMPLATES_DIR.rglob("*") if f.is_file() and "__pycache__" not in str(f)]


def _template_content(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def _required_integration_fields(requirement: dict) -> set[str]:
    return {
        field.get("name")
        for field in requirement.get("required_fields", [])
        if isinstance(field, dict)
    }


def _golden_subscriptions_yaml() -> str:
    return """
schema_version: mozaiks.subscriptions.v1
label: Golden SaaS Plans
default_plan_id: free
assignment_store:
  data_alias: billing.subscriptions
  user_id_field: user_id
  active_statuses: [active, pending]
token_wallets:
  - wallet_id: ai_tokens
    label: AI tokens
    unit: tokens
    usage_meter_id: ai_tokens
    scope: user
    auto_debit_usage: true
    depleted_balance:
      recovery_action: top_up
      billing_route: /billing
      top_up_route: /billing
      upgrade_route: /pricing
top_up_products:
  - product_id: ai_tokens_10k
    label: 10K AI tokens
    wallet_id: ai_tokens
    token_amount: 10000
    price:
      amount_cents: 500
      currency: usd
plans:
  - plan_id: free
    label: Free
    capabilities: [billing_portal.read]
    usage_limits:
      - meter_id: ai_tokens
        unit: tokens
        monthly_limit: 1000
    token_allowances:
      - wallet_id: ai_tokens
        amount: 1000
        cadence: monthly
  - plan_id: pro
    label: Pro
    capabilities: [billing_portal.read, billing_portal.manage, reports.generate]
    usage_limits:
      - meter_id: ai_tokens
        unit: tokens
        monthly_limit: 100000
    token_allowances:
      - wallet_id: ai_tokens
        amount: 100000
        cadence: monthly
"""


def _golden_mozaikspay_saas_bundle() -> tuple[dict[str, str], list[dict]]:
    pack_descriptor = dict(_read_yaml(_CONTEXT_YAML)["pack"])
    pack_descriptor.setdefault("pack_source_path", str(_PACK_ROOT))
    capability_packs = [pack_descriptor]
    files = {
        item["filename"]: item["content"]
        for item in resolve_managed_capability_templates(capability_packs)
    }
    files["app.json"] = '{"name":"Golden MozaiksPay SaaS","version":"1.0.0"}\n'
    files["config/subscriptions.yaml"] = _golden_subscriptions_yaml()

    deployment_env = _deployment_env_for_capability_packs(capability_packs)
    files.update(
        generate_deployment_artifacts(
            app_id="golden-mozaikspay-saas",
            deployment_profile="generic_container",
            include_dockerfiles=True,
            include_workflow=False,
            include_compose=False,
            extra_required_variables=deployment_env["required"],
            extra_optional_variables=deployment_env["optional"],
            extra_secret_variables=deployment_env["secret"],
            extra_public_variables=deployment_env["public"],
        )["artifacts"]
    )
    return files, capability_packs


# ---------------------------------------------------------------------------
# 1. context.yaml contract
# ---------------------------------------------------------------------------

class TestContextYaml:
    def test_context_yaml_exists(self):
        assert _CONTEXT_YAML.exists(), "mozaikspay/context.yaml must exist"

    def test_context_id(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        assert ctx["context_id"] == "mozaikspay"

    def test_has_pack_descriptor(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        assert "pack" in ctx
        assert ctx["pack"]["id"] == "mozaikspay"

    def test_pack_status_is_active(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        assert ctx["pack"]["status"] == "active"

    def test_pack_declares_deployment_env_handles(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        deployment_env = ctx["pack"]["deployment_env"]

        assert "MOZAIKS_APP_URL" in deployment_env["optional"]
        assert "MOZAIKSPAY_API_BASE" in deployment_env["optional"]
        assert "MOZAIKSPAY_CLIENT_SECRET" in deployment_env["secret"]
        assert "MOZAIKSPAY_API_KEY" in deployment_env["secret"]

    def test_applies_to_app_generator(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        assert "AppGenerator" in ctx.get("applies_to_workflows", [])

    def test_assets_declares_contract_and_templates(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        asset_kinds = {a["kind"] for a in ctx.get("assets", [])}
        assert "contract" in asset_kinds
        assert "templates" in asset_kinds

    def test_facades_declare_billing_portal(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        facade_ids = {f["module_id"] for f in ctx.get("facades", [])}
        assert "billing_portal" in facade_ids

    def test_facade_provider_is_mozaikspay(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        portal = next(f for f in ctx["facades"] if f["module_id"] == "billing_portal")
        assert portal["provider_module"] == "mozaikspay"

    def test_context_declares_mozaikspay_connector_requirement(self):
        ctx = _read_yaml(_CONTEXT_YAML)
        requirements = ctx.get("required_integrations") or ctx["pack"].get("required_integrations") or []
        mozaikspay = next((item for item in requirements if item.get("service") == "mozaikspay"), None)
        assert mozaikspay is not None
        assert mozaikspay["kind"] == "api_key"
        assert mozaikspay["provider"] == "mozaiks_pay"
        assert _required_integration_fields(mozaikspay) == {"api_base", "api_key"}
        secret_field = next(field for field in mozaikspay["required_fields"] if field["name"] == "api_key")
        assert secret_field["frontend_safe"] is False


# ---------------------------------------------------------------------------
# 2. contract.yaml shape
# ---------------------------------------------------------------------------

class TestContractYaml:
    def test_contract_yaml_exists(self):
        assert _CONTRACT_YAML.exists(), "mozaikspay/contract.yaml must exist"

    def test_contract_id_and_type(self):
        c = _read_yaml(_CONTRACT_YAML)
        assert c["contract_id"] == "mozaikspay"
        assert c["contract_type"] == "build_pack_instructions"

    def test_contract_declares_canonical_provider_identity(self):
        c = _read_yaml(_CONTRACT_YAML)
        provider = c["canonical_provider"]

        assert provider["provider_id"] == "mozaiks_pay"
        assert provider["provider_pack_id"] == "mozaikspay"
        assert provider["api_contract_version"] == "mozaiks.provider_api_contract.v1"
        assert provider["fulfillment_boundary"] == "BillingFulfillmentCommand"
        assert provider["entitlement_boundary"] == "EntitlementPort"
        assert provider["activation_requires_explicit_selection"] is True

    def test_required_outputs_declared(self):
        c = _read_yaml(_CONTRACT_YAML)
        paths = {r["path"] for r in c.get("required_outputs", [])}
        assert "services/integrations/mozaikspay_client.py" in paths
        assert "modules/billing_portal/module.yaml" in paths

    def test_forbidden_outputs_exclude_managed_internals(self):
        c = _read_yaml(_CONTRACT_YAML)
        prefixes = {f["path_prefix"] for f in c.get("forbidden_outputs", [])}
        assert "modules/mozaikspay/" in prefixes or any(
            "mozaikspay" in p for p in prefixes
        ), "managed module path must be a forbidden output"
        assert any("wallet" in p for p in prefixes), "wallet module must be forbidden"

    def test_runtime_boundaries_documented(self):
        c = _read_yaml(_CONTRACT_YAML)
        boundaries = c.get("runtime_boundaries", [])
        assert len(boundaries) >= 3, "runtime boundaries must be documented"
        rule_ids = {b["id"] for b in boundaries}
        assert "managed_internals" in rule_ids

    def test_facades_declare_billing_portal(self):
        c = _read_yaml(_CONTRACT_YAML)
        facade_ids = {f["module_id"] for f in c.get("facades", [])}
        assert "billing_portal" in facade_ids

    def test_contract_declares_mozaikspay_connector_requirement(self):
        c = _read_yaml(_CONTRACT_YAML)
        requirements = c.get("required_integrations") or []
        mozaikspay = next((item for item in requirements if item.get("service") == "mozaikspay"), None)
        assert mozaikspay is not None
        assert mozaikspay["provider"] == "mozaiks_pay"
        assert _required_integration_fields(mozaikspay) == {"api_base", "api_key"}

    def test_runtime_connector_contract_names_selection_and_fulfillment_boundaries(self):
        c = _read_yaml(_CONTRACT_YAML)
        connector = c["runtime_connector_contract"]

        assert connector["canonical_provider_id"] == "mozaiks_pay"
        assert connector["selection_field"] == "AppBuildPlan.monetization_provider"
        assert connector["selection_value"] == "mozaiks_pay"
        assert connector["required_selected_pack_id"] == "mozaikspay"
        assert "entitlement_dispatch" in connector["mutually_exclusive_with"]
        assert connector["idempotency"]["replay_boundary"] == "BillingFulfillmentService"
        assert connector["callback_verification"]["fail_closed"] is True
        assert connector["entitlement_sync"]["normalized_command"] == "BillingFulfillmentCommand"

    def test_provider_api_contract_forbids_provider_neutral_internal_ids(self):
        c = _read_yaml(_CONTRACT_YAML)
        fields = set(c["provider_api_response_contract"]["globally_forbidden_response_fields"])

        assert "provider_customer_id" in fields
        assert "provider_subscription_id" in fields
        assert "payment_provider_customer_id" in fields
        assert "payment_provider_subscription_id" in fields
        assert "stripe_customer_id" not in fields
        assert "stripe_subscription_id" not in fields


# ---------------------------------------------------------------------------
# 3. Required output templates exist on disk
# ---------------------------------------------------------------------------

class TestRequiredOutputsExist:
    def test_all_required_outputs_have_template_files(self):
        c = _read_yaml(_CONTRACT_YAML)
        missing = []
        for req in c.get("required_outputs", []):
            template_path = _TEMPLATES_DIR / req["path"]
            if not template_path.exists():
                missing.append(req["path"])
        assert missing == [], f"Missing template files for required_outputs: {missing}"


# ---------------------------------------------------------------------------
# 4. Forbidden output patterns not in templates
# ---------------------------------------------------------------------------

class TestForbiddenOutputsAbsent:
    """Templates must not generate content that violates the forbidden_output rules."""

    def test_templates_do_not_import_payment_provider_directly(self):
        violations = []
        for f in _all_template_files():
            content = _template_content(f)
            if "import payment_provider" in content:
                violations.append(str(f.relative_to(_PACK_ROOT)))
        assert violations == [], f"Templates directly import payment_provider: {violations}"

    def test_templates_do_not_contain_payment_provider_secret_key(self):
        violations = []
        for f in _all_template_files():
            content = _template_content(f)
            if "PAYMENT_PROVIDER_SECRET_KEY" in content:
                violations.append(str(f.relative_to(_PACK_ROOT)))
        assert violations == [], f"Templates reference PAYMENT_PROVIDER_SECRET_KEY: {violations}"

    def test_templates_do_not_include_managed_entitlements_mutations(self):
        violations = []
        for f in _all_template_files():
            content = _template_content(f)
            if "managed_entitlements" in content.lower() and "mutation" in content.lower():
                violations.append(str(f.relative_to(_PACK_ROOT)))
        assert violations == [], f"Templates contain managed_entitlements mutations: {violations}"

    def test_templates_do_not_expose_mozaikspay_module_path(self):
        """Pages and service code must not bind directly to managed module routes."""
        violations = []
        for f in _all_template_files():
            if f.suffix not in (".py", ".yaml", ".yml"):
                continue
            content = _template_content(f)
            for forbidden in ("/api/modules/mozaikspay", "/api/modules/wallet", "/api/modules/managed_billing"):
                if forbidden in content:
                    violations.append(f"{f.relative_to(_PACK_ROOT)!s}: {forbidden}")
        assert violations == [], f"Templates expose forbidden managed module routes: {violations}"


# ---------------------------------------------------------------------------
# 5. mozaikspay_client.py — app-side adapter boundary
# ---------------------------------------------------------------------------

class TestMozaiksPayClientTemplate:
    def test_client_template_exists(self):
        assert _CLIENT_TEMPLATE.exists()

    def test_client_uses_connector_or_env_vars_not_hardcoded_url(self):
        content = _CLIENT_TEMPLATE.read_text(encoding="utf-8")
        assert "ConnectorStore" in content
        assert "get_connector_vault_backend" in content
        assert "MOZAIKSPAY_API_BASE" in content
        assert "MOZAIKSPAY_API_KEY" in content
        assert "MOZAIKSPAY_CLIENT_ID" in content
        assert "MOZAIKSPAY_CLIENT_SECRET" in content
        # Must not hardcode any production or staging URL
        assert "https://api.mozaiks" not in content

    def test_client_does_not_import_payment_provider(self):
        content = _CLIENT_TEMPLATE.read_text(encoding="utf-8")
        assert "import payment_provider" not in content

    def test_client_does_not_store_raw_secrets(self):
        content = _CLIENT_TEMPLATE.read_text(encoding="utf-8")
        # Should not persist or store a secret value
        assert "PAYMENT_PROVIDER_SECRET_KEY" not in content
        assert "provider_live_" not in content
        assert "provider_test_" not in content

    def test_client_calls_provider_api_path(self):
        content = _CLIENT_TEMPLATE.read_text(encoding="utf-8")
        assert "_CONNECTOR_SERVICE = \"mozaikspay\"" in content
        assert "_PROVIDER_API_PREFIX = \"/api/mozaikspay/v1\"" in content
        assert "_CONTRACT_VERSION = \"mozaiks.provider_api_contract.v1\"" in content
        assert "X-MozaiksPay-Contract-Version" in content
        assert "def readiness(" in content
        assert "_MAX_RETRIES = 2" in content
        assert "/subscription/status" in content
        assert "/health" in content
        assert "/billing-portal/session" in content
        assert "settings.api_key" in content
        assert "X-MozaiksPay-Client-Id" in content
        assert "ConnectorStore().get(" in content
        assert "get_connector_vault_backend" in content
        assert "/api/modules/managed_billing" not in content

    def test_runtime_usage_does_not_fall_back_to_provider_api_base(self):
        content = _CLIENT_TEMPLATE.read_text(encoding="utf-8")
        assert "runtime_base=runtime_base or api_base" not in content
        assert "settings.runtime_base or settings.api_base" not in content
        assert "MOZAIKSPAY_API_BASE is only the MozaiksPay provider host" in content

    def test_client_has_error_classes(self):
        content = _CLIENT_TEMPLATE.read_text(encoding="utf-8")
        assert "MozaiksPayError" in content
        assert "MozaiksPayConfigurationError" in content

    def test_client_uses_http_not_payment_provider_sdk(self):
        content = _CLIENT_TEMPLATE.read_text(encoding="utf-8")
        assert "httpx" in content


# ---------------------------------------------------------------------------
# 6. billing_portal module.yaml — app-owned facade
# ---------------------------------------------------------------------------

class TestBillingPortalModuleYaml:
    def test_module_yaml_exists(self):
        assert _MODULE_YAML.exists()

    def test_module_id_is_billing_portal(self):
        doc = _read_yaml(_MODULE_YAML)
        assert doc["module"]["id"] == "billing_portal"

    def test_owner_is_app(self):
        doc = _read_yaml(_MODULE_YAML)
        assert doc["module"]["owner"] == "app"

    def test_visibility_is_private(self):
        doc = _read_yaml(_MODULE_YAML)
        assert doc["module"]["visibility"] == "private"

    def test_facade_actions_declared(self):
        doc = _read_yaml(_MODULE_YAML)
        action_ids = {a["id"] for a in doc.get("actions", [])}
        assert {"get_subscription_status", "get_usage_status", "open_billing_portal"} <= action_ids

    def test_no_raw_provider_credentials_in_output_schema(self):
        content = _MODULE_YAML.read_text(encoding="utf-8")
        for forbidden in ("payment_provider_customer_id", "payment_provider_subscription_id", "PAYMENT_PROVIDER_"):
            assert forbidden not in content, (
                f"module.yaml must not expose provider-owned field: {forbidden}"
            )


# ---------------------------------------------------------------------------
# 7. Page YAMLs — bind through billing_portal facade, not managed modules
# ---------------------------------------------------------------------------

class TestPageYamls:
    def test_billing_page_exists(self):
        assert _BILLING_PAGE.exists()

    def test_usage_page_exists(self):
        assert _USAGE_PAGE.exists()

    def test_billing_page_calls_billing_portal_actions(self):
        content = _BILLING_PAGE.read_text(encoding="utf-8")
        assert "/api/modules/billing_portal/" in content, (
            "billing page must route through billing_portal facade module"
        )

    def test_billing_page_does_not_call_managed_billing_directly(self):
        content = _BILLING_PAGE.read_text(encoding="utf-8")
        assert "/api/modules/managed_billing/" not in content, (
            "billing page must not bind directly to managed_billing — use billing_portal facade"
        )

    def test_billing_page_does_not_call_managed_billing_module(self):
        content = _BILLING_PAGE.read_text(encoding="utf-8")
        assert "/api/modules/mozaikspay/" not in content

    def test_billing_page_schema_version(self):
        doc = _read_yaml(_BILLING_PAGE)
        assert doc.get("schema_version") == "mozaiks.page.v1"

    def test_usage_page_calls_billing_portal_actions(self):
        content = _USAGE_PAGE.read_text(encoding="utf-8")
        assert "/api/modules/billing_portal/" in content

    def test_usage_page_schema_version(self):
        doc = _read_yaml(_USAGE_PAGE)
        assert doc.get("schema_version") == "mozaiks.page.v1"


# ---------------------------------------------------------------------------
# 8. Golden generated bundle replay
# ---------------------------------------------------------------------------

class TestGoldenGeneratedMozaiksPaySaasBundle:
    def test_replayed_pack_templates_scan_as_exportable_generated_app(self):
        files, capability_packs = _golden_mozaikspay_saas_bundle()

        errors = scan_generated_bundle(
            files,
            capability_packs=capability_packs,
            require_deployment_artifacts=True,
        )

        assert errors == []
        assert {
            "services/integrations/mozaikspay_client.py",
            "modules/billing_portal/module.yaml",
            "modules/billing_portal/backend/handler.py",
            "modules/billing_portal/backend/service.py",
            "modules/billing_portal/backend/schemas.py",
            "config/subscriptions.yaml",
            "ui/pages/billing.yaml",
            "ui/pages/usage.yaml",
            ".env.example",
            "deployment.manifest.json",
        } <= set(files)

        env_example = files[".env.example"]
        for name in (
            "MOZAIKS_APP_URL",
            "MOZAIKSPAY_API_BASE",
            "MOZAIKSPAY_CLIENT_ID",
            "MOZAIKSPAY_CLIENT_SECRET",
            "MOZAIKSPAY_API_KEY",
        ):
            assert f"{name}=" in env_example

        rendered = "\n".join(files.values())
        assert "/api/modules/mozaikspay/" not in rendered
        assert "/api/modules/managed_billing/" not in rendered
        assert "import stripe" not in rendered


# ---------------------------------------------------------------------------
# 9. Pack-wide drift guard — no provider internals in any template
# ---------------------------------------------------------------------------

class TestPackDriftGuard:
    """Belt-and-suspenders: no template should contain managed-service internals."""

    DRIFT_PATTERNS = [
        ("app/modules/wallet/", "wallet module path — use billing_portal facade instead"),
        ("app/capability_packs/", "capability_packs internal path"),
        ("managed_entitlements mutation", "managed entitlements mutation logic"),
    ]

    def test_no_drift_patterns_in_templates(self):
        violations = []
        for template_file in _all_template_files():
            content = _template_content(template_file)
            for pattern, reason in self.DRIFT_PATTERNS:
                if pattern.lower() in content.lower():
                    violations.append(
                        f"{template_file.relative_to(_PACK_ROOT)!s}: "
                        f"contains '{pattern}' ({reason})"
                    )
        assert violations == [], "Drift guard violations:\n" + "\n".join(violations)
