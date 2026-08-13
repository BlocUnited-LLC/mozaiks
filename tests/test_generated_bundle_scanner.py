from __future__ import annotations

import json
from pathlib import Path

from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    generate_deployment_artifacts,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle

_WORKSPACE = Path(__file__).resolve().parents[1]
_MOZAIKSPAY_PACK_ROOT = _WORKSPACE / "factory_app" / "build_context" / "mozaikspay"


def _data_contract(*, module_id: str = "tickets") -> str:
    return json.dumps(
        {
            "version": "1",
            "app_id": "support-operations-smoke",
            "surfaces": [
                {
                    "surface_id": module_id,
                    "surface_kind": "module",
                    "collections": [
                        {
                            "name": module_id,
                            "ownership": {
                                "surface_id": module_id,
                                "surface_kind": "module",
                            },
                        }
                    ],
                }
            ],
            "shared_collections": [],
        }
    )


def _module_yaml(*, module_id: str = "tickets") -> str:
    return f"""
schema_version: mozaiks.module.v1
module:
  id: {module_id}
  handler: backend.handler:TicketsModule
actions: []
"""


def _auth_contract_yaml() -> str:
    return """
schema_version: mozaiks.auth.v1
auth_required: true
strategy: oidc
mode: brokered_oidc
signup_enabled: false
routes:
  login: /login
  callback: /auth/callback
  logout: /login
  post_login_default: /dashboard
frontend:
  adapter: oidc_pkce
  client_id_env: VITE_OIDC_CLIENT_ID
  authority_env: VITE_OIDC_AUTHORITY
  discovery_url_env: VITE_OIDC_DISCOVERY_URL
  redirect_uri_env: VITE_OIDC_REDIRECT_URI
  scope_env: VITE_OIDC_SCOPE
  default_scopes: [openid, profile, email]
runtime:
  provider_env: AUTH_PROVIDER
  enabled_env: AUTH_ENABLED
  authority_env: MOZAIKS_OIDC_AUTHORITY
  discovery_url_env: MOZAIKS_OIDC_DISCOVERY_URL
  issuer_env: AUTH_ISSUER
  jwks_url_env: AUTH_JWKS_URL
identity_providers:
  - id: google
    label: Google
    provider_role: upstream_oidc_provider
login_methods:
  - id: broker
    kind: oidc_redirect
    label: Sign in
    primary: true
customization:
  login_theme_source: brand/theme_config.json
  upstream_provider_setup: host_or_operator
"""


def _auth_adapter_js() -> str:
    return """
const TRANSACTION_KEY = 'demo_oidc_transactions';
function writeAuthTransaction(transaction) {
  return transaction.state;
}
function readAuthTransaction(state) {
  return state;
}
function clearStoredUserSession() {
  sessionStorage.removeItem('demo_access_token');
}
async function handleCallback() {
  const state = new URLSearchParams(window.location.search).get('state');
  const transaction = readAuthTransaction(state);
  return { returnPath: transaction?.returnPath || '/dashboard' };
}
"""


def _mozaikspay_pack() -> list[dict]:
    # Include pack_source_path so _packs_providing() can load provides_capabilities
    # from contract.yaml and correctly skip the entitlement_dispatch requirement.
    return [{"id": "mozaikspay", "capability_source": "managed_capability", "pack_source_path": str(_MOZAIKSPAY_PACK_ROOT)}]


def _valid_mozaikspay_saas_bundle(*, include_deployment: bool = True) -> dict[str, str]:
    files = {
        "app.json": '{"name":"SaaS Smoke"}',
        "config/subscriptions.yaml": """
schema_version: mozaiks.subscriptions.v1
label: SaaS Smoke Plans
default_plan_id: free
assignment_store:
  data_alias: subscriptions.assignments
  user_id_field: user_id
  active_statuses: [active, pending]
token_wallets:
  - wallet_id: ai_tokens
    label: AI tokens
    unit: tokens
    usage_meter_id: ai_tokens
    scope: user
    auto_debit_usage: true
plans:
  - plan_id: free
    label: Free
    capabilities: []
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
    capabilities: [billing_portal.read]
    usage_limits:
      - meter_id: ai_tokens
        unit: tokens
        monthly_limit: 100000
    token_allowances:
      - wallet_id: ai_tokens
        amount: 100000
        cadence: monthly
""",
        "services/integrations/mozaikspay_client.py": """
from mozaiksai.core.data.persistence.connector_store import ConnectorStore
from mozaiksai.core.secrets import get_connector_vault_backend

_CONNECTOR_SERVICE = "mozaikspay"
MOZAIKSPAY_API_BASE = "MOZAIKSPAY_API_BASE"
MOZAIKSPAY_API_KEY = "MOZAIKSPAY_API_KEY"
MOZAIKSPAY_CLIENT_ID = "MOZAIKSPAY_CLIENT_ID"
MOZAIKSPAY_CLIENT_SECRET = "MOZAIKSPAY_CLIENT_SECRET"

class MozaiksPayClient:
    async def get_subscription_status_for_scope(self, **scope):
        return {"success": True, "scope": scope}

    async def get_runtime_ai_usage(self, limit=500):
        return {"success": True, "limit": limit}

    async def create_billing_portal_session(self, **payload):
        return {"success": True, "portal_url": "https://billing.example.test"}
""",
        "modules/billing_portal/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: billing_portal
  handler: backend.handler:BillingPortalHandler
actions:
  - id: get_subscription_status
    handler_method: get_subscription_status
  - id: get_usage_status
    handler_method: get_usage_status
  - id: open_billing_portal
    handler_method: open_billing_portal
""",
        "modules/billing_portal/backend/handler.py": """
class BillingPortalHandler:
    pass
""",
        "modules/billing_portal/backend/service.py": """
from services.integrations.mozaikspay_client import MozaiksPayClient

class BillingPortalService:
    async def get_subscription_status(self, ctx):
        return await MozaiksPayClient().get_subscription_status_for_scope(user_id=ctx.user_id)

    async def get_usage_status(self, ctx, limit=500):
        return await MozaiksPayClient().get_runtime_ai_usage(limit=limit)

    async def open_billing_portal(self, ctx, return_url):
        return await MozaiksPayClient().create_billing_portal_session(return_url=return_url)
""",
        "modules/billing_portal/backend/schemas.py": """
class SubscriptionStatus:
    pass
""",
        "ui/pages/billing.yaml": """
name: billing
route: /billing
title: Billing
sections:
  - id: subscription-status
    primitive: DataTable
    config:
      api_endpoint: /api/modules/billing_portal/get_subscription_status
  - id: billing-portal
    primitive: DataTable
    config:
      api_endpoint: /api/modules/billing_portal/open_billing_portal
""",
        "ui/pages/usage.yaml": """
name: usage
route: /usage
title: Usage
sections:
  - id: usage-status
    primitive: DataTable
    config:
      api_endpoint: /api/modules/billing_portal/get_usage_status
""",
    }
    if include_deployment:
        files.update(
            generate_deployment_artifacts(
                app_id="saas-smoke",
                deployment_profile="generic_container",
                include_dockerfiles=True,
                include_workflow=False,
                include_compose=False,
                extra_optional_variables=[
                    "MOZAIKS_APP_URL",
                    "MOZAIKSPAY_API_BASE",
                    "MOZAIKSPAY_CLIENT_ID",
                    "MOZAIKSPAY_CLIENT_SECRET",
                    "MOZAIKSPAY_API_KEY",
                ],
                extra_secret_variables=[
                    "MOZAIKSPAY_CLIENT_SECRET",
                    "MOZAIKSPAY_API_KEY",
                ],
            )["artifacts"]
        )
    return files


def test_scan_generated_bundle_rejects_data_contract_module_without_module_artifact() -> None:
    errors = scan_generated_bundle(
        {
            "app.json": '{"name":"Support Operations"}',
            "data/contract.json": _data_contract(module_id="tickets"),
            "ui/pages/Tickets.yaml": "name: Tickets\n",
        }
    )

    assert any("data/contract.json declares module surface" in error for error in errors)
    assert any("tickets" in error for error in errors)


def test_scan_generated_bundle_accepts_matching_data_contract_module_artifact() -> None:
    errors = scan_generated_bundle(
        {
            "data/contract.json": _data_contract(module_id="tickets"),
            "modules/tickets/module.yaml": _module_yaml(module_id="tickets"),
        }
    )

    assert errors == []


def test_scan_generated_bundle_normalizes_windows_paths_for_module_alignment() -> None:
    errors = scan_generated_bundle(
        {
            "data\\contract.json": _data_contract(module_id="tickets"),
            "modules\\tickets\\module.yaml": _module_yaml(module_id="tickets"),
        }
    )

    assert errors == []


def test_scan_generated_bundle_rejects_legacy_data_contract_path() -> None:
    errors = scan_generated_bundle(
        {
            "config\\data.json": _data_contract(module_id="tickets"),
            "modules\\tickets\\module.yaml": _module_yaml(module_id="tickets"),
        }
    )

    assert any("removed app paths" in error for error in errors)
    assert any("config/data.json" in error for error in errors)


def test_scan_generated_bundle_rejects_noncanonical_config_descriptor() -> None:
    errors = scan_generated_bundle(
        {
            "config/app_zero_brownfield.json": '{"kind":"descriptor"}',
            "config/shell.json": "{}",
        }
    )

    assert any("noncanonical app config files" in error for error in errors)
    assert any("config/app_zero_brownfield.json" in error for error in errors)


def test_scan_generated_bundle_accepts_runtime_project_manifests() -> None:
    errors = scan_generated_bundle(
        {
            "app.json": '{"name":"Demo"}',
            "provenance.yaml": "schema_version: mozaiks.provenance.v1\ncreated_with:\n  mode: factory\n",
            "dashboard/dashboard.yaml": "schema_version: mozaiks.dashboard.v1\n",
            "package.json": '{"scripts":{"build":"vite"}}',
            "requirements.txt": "fastapi\n",
            "vite.config.js": "export default {};\n",
        }
    )

    assert errors == []


def test_scan_generated_bundle_rejects_build_time_prompting_root() -> None:
    errors = scan_generated_bundle(
        {
            "build_context/ManagedPayments/context.yaml": "context_id: managed_context\n",
            "app.json": '{"name":"Demo"}',
        }
    )

    assert any("outside the canonical app planes" in error for error in errors)
    assert any("build_context/ManagedPayments/context.yaml" in error for error in errors)


def test_scan_generated_bundle_rejects_raw_secret_fields() -> None:
    errors = scan_generated_bundle(
        {
            "security/secrets.yaml": "secrets:\n  - name: OPENAI_API_KEY\n    value: sk-test-raw\n",
        }
    )

    assert any("names-only" in error for error in errors)
    assert any("secrets.0.value" in error for error in errors)


def test_scan_generated_bundle_rejects_data_contract_module_id_mismatch() -> None:
    errors = scan_generated_bundle(
        {
            "data/contract.json": _data_contract(module_id="tickets"),
            "modules/tickets/module.yaml": _module_yaml(module_id="queues"),
        }
    )

    assert any("module.id 'queues' must match folder module id 'tickets'" in error for error in errors)


def test_scan_generated_bundle_rejects_raw_provider_secret_literals() -> None:
    errors = scan_generated_bundle(
        {
            "services/integrations/payments_client.py": "import payment_provider\npayment_provider.api_key = 'provider_test_1234567890'\n",
        }
    )

    assert any("raw provider secret key literal" in error for error in errors)
    assert any("imports the payment provider SDK directly" in error for error in errors)
    assert any("assigns payment_provider.api_key directly" in error for error in errors)


def test_scan_generated_bundle_rejects_direct_provider_refund_calls() -> None:
    errors = scan_generated_bundle(
        {
            "modules/payments/backend/service.py": (
                "result = payment_provider.Refund.create(payment_intent=payment_intent_id)\n"
            ),
            "services/integrations/payments_client.py": 'url = "/refunds"\n',
        }
    )

    assert any("refunds APIs directly" in error for error in errors)
    assert any("/refunds" in error for error in errors)


def test_scan_generated_bundle_rejects_raw_payment_provider_imports() -> None:
    errors = scan_generated_bundle(
        {
            "modules/billing/backend/service.py": "import stripe\nfrom paddle import Client\n",
        }
    )

    assert any("raw payment provider SDK" in error and "stripe" in error for error in errors)
    assert any("raw payment provider SDK" in error and "paddle" in error for error in errors)


def test_scan_generated_bundle_rejects_app_local_wallet_and_usage_ledgers() -> None:
    errors = scan_generated_bundle(
        {
            "modules/billing/backend/token_wallet_ledger.py": "class TokenWalletLedger:\n    pass\n",
            "services/ledgers/usage.py": (
                "from mozaiksai.core.usage.ledger import RuntimeUsageLedger\n"
            ),
        }
    )

    assert sum("app-local token wallet or usage ledger" in error for error in errors) == 2


def test_scan_generated_bundle_rejects_direct_managed_capability_endpoint_calls() -> None:
    """Backend service files are checked for direct calls to selected managed capability endpoints."""
    errors = scan_generated_bundle(
        {
            "services/integrations/custompay_client.py": "class CustomPayClient: pass\n",
            "modules/billing_portal/backend/service.py": (
                'endpoint = "/api/modules/custompay/create_checkout_session"\n'
                'other = "/api/modules/custompay/assign_plan"\n'
            ),
        },
        capability_packs=[{"id": "custompay", "capability_source": "managed_capability"}],
    )

    assert any("/api/modules/custompay/create_checkout_session" in error for error in errors)
    assert any("/api/modules/custompay/assign_plan" in error for error in errors)


def test_scan_generated_bundle_allows_managed_refund_adapter_call() -> None:
    errors = scan_generated_bundle(
        {
            "modules/billing_portal/backend/service.py": (
                "from services.integrations.mozaikspay_client import MozaiksPayClient\n"
                "result = await client.request_refund(payment_id=payment_id, amount=amount)\n"
            )
        }
    )

    assert errors == []


def test_scan_generated_bundle_enforces_selected_managed_capability_adapter() -> None:
    errors = scan_generated_bundle(
        {
            "app.json": "{}",
            "ui/pages/billing.yaml": "sections: []\n",
        },
        capability_packs=[{"id": "mozaikspay", "capability_source": "managed_capability"}],
    )

    assert any("services/integrations/mozaikspay_client.py" in error for error in errors)


def test_scan_generated_bundle_rejects_selected_managed_capability_internals() -> None:
    errors = scan_generated_bundle(
        {
            "services/integrations/mozaikspay_client.py": "class MozaiksPayClient: pass\n",
            "modules/mozaikspay/module.yaml": "module:\n  id: mozaikspay\n",
        },
        capability_packs=[{"id": "mozaikspay", "capability_source": "managed_capability"}],
    )

    assert any("must not generate provider internals" in error for error in errors)


def test_scan_generated_bundle_rejects_inline_pack_forbidden_output_prefixes() -> None:
    errors = scan_generated_bundle(
        {
            "services/integrations/testpay_client.py": "class TestPayClient: pass\n",
            "modules/provider_ledger/module.yaml": "module:\n  id: provider_ledger\n",
        },
        capability_packs=[
            {
                "id": "testpay",
                "capability_source": "managed_capability",
                "forbidden_outputs": [{"path_prefix": "modules/provider_ledger/"}],
            }
        ],
    )

    assert any("forbidden output prefixes" in error for error in errors)
    assert any("modules/provider_ledger/module.yaml" in error for error in errors)


def test_scan_generated_bundle_rejects_mozaikspay_contract_forbidden_output_prefixes() -> None:
    errors = scan_generated_bundle(
        {
            "services/integrations/mozaikspay_client.py": "class MozaiksPayClient: pass\n",
            "modules/wallet/module.yaml": "module:\n  id: wallet\n",
        },
        capability_packs=[
            {
                "id": "mozaikspay",
                "capability_source": "managed_capability",
                "pack_source_path": str(_MOZAIKSPAY_PACK_ROOT),
            }
        ],
    )

    assert any("forbidden output prefixes" in error for error in errors)
    assert any("modules/wallet/module.yaml" in error for error in errors)


def test_scan_generated_bundle_rejects_page_binding_to_selected_managed_capability() -> None:
    errors = scan_generated_bundle(
        {
            "services/integrations/mozaikspay_client.py": "class MozaiksPayClient: pass\n",
            "ui/pages/billing.yaml": 'api_endpoint: "/api/modules/mozaikspay/create_checkout_session"\n',
        },
        capability_packs=[{"id": "mozaikspay", "capability_source": "managed_capability"}],
    )

    assert any("calls managed capability endpoint" in error for error in errors)


def test_scan_generated_bundle_rejects_managed_setup_raw_provider_env_handles() -> None:
    errors = scan_generated_bundle(
        {
            "config/integrations.yaml": """
schema_version: mozaiks.integrations.v1
requirements:
  - integration_id: payments
    service: mozaikspay
    preferred_setup_lane: managed
    allowed_setup_lanes: [managed, bring_your_own_key]
""",
            ".env.example": "STRIPE_SECRET_KEY=\nSTRIPE_WEBHOOK_SECRET=\n",
        }
    )

    assert any("raw payment-provider environment handles" in error for error in errors)


def test_scan_generated_bundle_rejects_managed_setup_provider_webhook_routes() -> None:
    errors = scan_generated_bundle(
        {
            "config/integrations.yaml": """
schema_version: mozaiks.integrations.v1
requirements:
  - integration_id: payments
    service: mozaikspay
    preferred_setup_lane: managed
    allowed_setup_lanes: [managed, bring_your_own_key]
""",
            "services/routes/webhooks.py": '@router.post("/webhooks/stripe")\nasync def webhook(): pass\n',
        }
    )

    assert any("raw payment-provider webhook or checkout routes" in error for error in errors)


def test_scan_generated_bundle_rejects_managed_setup_provider_sdk_mechanics() -> None:
    errors = scan_generated_bundle(
        {
            "config/integrations.yaml": """
schema_version: mozaiks.integrations.v1
requirements:
  - integration_id: payments
    service: mozaikspay
    preferred_setup_lane: managed
    allowed_setup_lanes: [managed, bring_your_own_key]
""",
            "modules/billing/backend/service.py": "session = stripe.checkout.Session.create()\n",
        }
    )

    assert any("raw payment-provider SDK mechanics" in error for error in errors)


def test_scan_generated_bundle_allows_provider_env_handles_when_no_managed_setup_lane() -> None:
    errors = scan_generated_bundle(
        {
            "config/integrations.yaml": """
schema_version: mozaiks.integrations.v1
requirements:
  - integration_id: direct_payments
    service: direct_payments
    preferred_setup_lane: bring_your_own_key
    allowed_setup_lanes: [bring_your_own_key]
""",
            ".env.example": "STRIPE_SECRET_KEY=\n",
        }
    )

    assert not any("managed setup lane" in error for error in errors)


def test_scan_generated_bundle_accepts_mozaikspay_saas_contract_with_deployment_artifacts() -> None:
    errors = scan_generated_bundle(
        _valid_mozaikspay_saas_bundle(include_deployment=True),
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=True,
    )

    assert errors == []


def test_scan_generated_bundle_rejects_authenticated_app_without_auth_deploy_contract() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=False,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml()
    files["ui/auth/authAdapter.js"] = _auth_adapter_js()

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert any("Authenticated generated apps must document" in error for error in errors)
    assert any("auth.required=true" in error for error in errors)
    assert any("AUTH_PROVIDER" in error for error in errors)


def test_scan_generated_bundle_accepts_authenticated_app_deploy_contract() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml()
    files["ui/auth/authAdapter.js"] = _auth_adapter_js()

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert errors == []


def test_scan_generated_bundle_rejects_authenticated_app_without_auth_yaml() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["ui/auth/authAdapter.js"] = _auth_adapter_js()

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert any("config/auth.yaml" in error for error in errors)
    assert any("mozaiks.auth.v1" in error for error in errors)


def test_scan_generated_bundle_rejects_auth_yaml_with_provider_url_literal() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml() + "\nprovider_url: https://accounts.google.com\n"
    files["ui/auth/authAdapter.js"] = _auth_adapter_js()

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert any("provider URLs must be supplied by env handles" in error for error in errors)


def test_scan_generated_bundle_rejects_auth_yaml_with_unknown_mode() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml().replace(
        "mode: brokered_oidc",
        "mode: direct_google_oauth",
    )
    files["ui/auth/authAdapter.js"] = _auth_adapter_js()

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert any("mode must be one of" in error for error in errors)


def test_scan_generated_bundle_rejects_auth_yaml_with_direct_login_method_url() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml() + """
login_methods:
  - id: google
    kind: direct_google
    label: Sign in with Google
    url: https://accounts.google.com
"""
    files["ui/auth/authAdapter.js"] = _auth_adapter_js()

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert any("login_methods[0] has unsupported fields" in error for error in errors)
    assert any("login_methods[0].kind" in error for error in errors)
    assert any("provider URLs must be supplied by env handles" in error for error in errors)


def test_scan_generated_bundle_accepts_public_signup_auth_mode() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml().replace(
        "mode: brokered_oidc\nsignup_enabled: false",
        "mode: public_self_signup\nsignup_enabled: true",
    ).replace(
        "  - id: broker\n    kind: oidc_redirect\n    label: Sign in\n    primary: true",
        "  - id: sign-in\n    kind: oidc_redirect\n    label: Sign in\n    primary: true\n"
        "  - id: create-account\n    kind: create_account\n    label: Create account\n    primary: false",
    )
    files["ui/auth/authAdapter.js"] = _auth_adapter_js()

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert errors == []


def test_scan_generated_bundle_rejects_auth_adapter_without_stateful_pkce() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml()
    files["ui/auth/authAdapter.js"] = "async function handleCallback() {}\n"

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert any("state-bound PKCE" in error for error in errors)


def test_scan_generated_bundle_rejects_auth_adapter_local_storage() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml()
    files["ui/auth/authAdapter.js"] = _auth_adapter_js() + "\nlocalStorage.setItem('token', token);\n"

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert any("must use sessionStorage, not localStorage" in error for error in errors)


def test_scan_generated_bundle_rejects_auth_adapter_provider_specific_code() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="generic_container",
        include_dockerfiles=True,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml()
    files["ui/auth/authAdapter.js"] = _auth_adapter_js() + "\nconst provider = 'accounts.google.com';\n"

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert any("must stay provider-neutral OIDC" in error for error in errors)


def test_scan_generated_bundle_accepts_production_profile_readiness_only_contract() -> None:
    files = generate_deployment_artifacts(
        app_id="private-smoke",
        deployment_profile="production_container",
        include_dockerfiles=False,
        include_workflow=False,
        include_compose=False,
        auth_required=True,
    )["artifacts"]
    files["app.json"] = '{"name":"Private Smoke","authRequired":true}'
    files["config/auth.yaml"] = _auth_contract_yaml()
    files["ui/auth/authAdapter.js"] = _auth_adapter_js()

    errors = scan_generated_bundle(files, require_deployment_artifacts=True)

    assert errors == []
    assert ".github/workflows/readiness.yml" in files
    assert ".github/workflows/deploy.yml" not in files


def test_scan_generated_bundle_rejects_mozaikspay_saas_without_env_handles() -> None:
    files = _valid_mozaikspay_saas_bundle(include_deployment=True)
    files[".env.example"] = "OPENAI_API_KEY=\nMONGO_URI=\n"

    errors = scan_generated_bundle(
        files,
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=True,
    )

    assert any(".env.example must document" in error for error in errors)
    assert any("MOZAIKSPAY_API_BASE" in error for error in errors)


def test_scan_generated_bundle_accepts_subscription_only_mozaikspay_saas_without_token_wallets() -> None:
    files = _valid_mozaikspay_saas_bundle()
    files["config/subscriptions.yaml"] = """
schema_version: mozaiks.subscriptions.v1
label: SaaS Smoke Plans
default_plan_id: free
assignment_store:
  data_alias: subscriptions.assignments
  user_id_field: user_id
  active_statuses: [active, pending]
plans:
  - plan_id: free
    label: Free
    capabilities: []
  - plan_id: pro
    label: Pro
    capabilities: [billing_portal.read]
"""

    errors = scan_generated_bundle(
        files,
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=True,
    )

    assert errors == []


def test_scan_generated_bundle_rejects_token_wallets_without_usage_credit_or_quota_contract() -> None:
    files = _valid_mozaikspay_saas_bundle()
    files["config/subscriptions.yaml"] = """
schema_version: mozaiks.subscriptions.v1
label: SaaS Smoke Plans
default_plan_id: free
assignment_store:
  data_alias: subscriptions.assignments
  user_id_field: user_id
  active_statuses: [active, pending]
token_wallets:
  - wallet_id: ai_tokens
    label: AI tokens
    unit: tokens
    usage_meter_id: ai_tokens
    scope: user
    auto_debit_usage: true
plans:
  - plan_id: free
    label: Free
    capabilities: []
  - plan_id: pro
    label: Pro
    capabilities: [billing_portal.read]
"""

    errors = scan_generated_bundle(
        files,
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=True,
    )

    assert any("token_wallets must be emitted only" in error for error in errors)


def test_scan_generated_bundle_rejects_mozaikspay_saas_without_subscriptions() -> None:
    files = _valid_mozaikspay_saas_bundle()
    files.pop("config/subscriptions.yaml")

    errors = scan_generated_bundle(
        files,
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=True,
    )

    assert any("config/subscriptions.yaml" in error for error in errors)


def test_scan_generated_bundle_rejects_mozaikspay_saas_without_billing_facade() -> None:
    files = _valid_mozaikspay_saas_bundle()
    files.pop("modules/billing_portal/module.yaml")

    errors = scan_generated_bundle(
        files,
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=True,
    )

    assert any("billing_portal/module.yaml" in error for error in errors)


def test_scan_generated_bundle_rejects_mozaikspay_saas_without_runtime_usage_delegate() -> None:
    files = _valid_mozaikspay_saas_bundle()
    files["modules/billing_portal/backend/service.py"] = files[
        "modules/billing_portal/backend/service.py"
    ].replace("get_runtime_ai_usage", "get_usage_status")

    errors = scan_generated_bundle(
        files,
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=True,
    )

    assert any("get_runtime_ai_usage" in error for error in errors)


def test_scan_generated_bundle_rejects_managed_mozaikspay_saas_without_deployment_artifacts() -> None:
    errors = scan_generated_bundle(
        _valid_mozaikspay_saas_bundle(include_deployment=False),
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=True,
    )

    assert any("deployment artifacts" in error for error in errors)
    assert any("Dockerfile" in error for error in errors)


def test_scan_generated_bundle_allows_mozaikspay_saas_without_deployment_artifacts_when_not_required() -> None:
    errors = scan_generated_bundle(
        _valid_mozaikspay_saas_bundle(include_deployment=False),
        capability_packs=_mozaikspay_pack(),
        require_deployment_artifacts=False,
    )

    assert errors == []


# ---------------------------------------------------------------------------
# Pack-capability provision mechanism
# ---------------------------------------------------------------------------

_ASSIGNMENT_STORE_SUBS_YAML = """
schema_version: mozaiks.subscriptions.v1
label: Plans
default_plan_id: free
assignment_store:
  data_alias: billing.assignments
  user_id_field: user_id
  active_statuses: [active]
plans:
  - plan_id: free
    label: Free
    capabilities: []
"""


def test_scan_generated_bundle_skips_entitlement_dispatch_for_any_pack_providing_subscription_write_path_inline() -> None:
    """Any managed pack with in-memory provides_capabilities: [subscription_write_path] exempts entitlement_dispatch."""
    errors = scan_generated_bundle(
        {"config/subscriptions.yaml": _ASSIGNMENT_STORE_SUBS_YAML},
        capability_packs=[{
            "id": "custombilling",
            "capability_source": "managed_capability",
            "provides_capabilities": ["subscription_write_path"],
        }],
    )
    assert not any("entitlement_dispatch" in error for error in errors)


def test_scan_generated_bundle_skips_entitlement_dispatch_for_mozaikspay_via_contract_file() -> None:
    """mozaikspay loaded with pack_source_path provides subscription_write_path via contract.yaml."""
    errors = scan_generated_bundle(
        {"config/subscriptions.yaml": _ASSIGNMENT_STORE_SUBS_YAML},
        capability_packs=_mozaikspay_pack(),
    )
    assert not any("entitlement_dispatch" in error for error in errors)


def test_scan_generated_bundle_requires_entitlement_dispatch_when_no_managed_subscription_writer() -> None:
    """assignment_store without any subscription_write_path provider requires entitlement_dispatch."""
    errors = scan_generated_bundle(
        {"config/subscriptions.yaml": _ASSIGNMENT_STORE_SUBS_YAML},
        capability_packs=[],
    )
    assert any("entitlement_dispatch" in error for error in errors)
    assert any("assignment_store" in error for error in errors)


def test_scan_generated_bundle_does_not_skip_entitlement_dispatch_for_non_managed_subscription_writer() -> None:
    """Only selected managed_capability packs can own the subscription write path."""
    errors = scan_generated_bundle(
        {"config/subscriptions.yaml": _ASSIGNMENT_STORE_SUBS_YAML},
        capability_packs=[{
            "id": "custombilling",
            "capability_source": "operator_pack",
            "provides_capabilities": ["subscription_write_path"],
        }],
    )

    assert any("entitlement_dispatch" in error for error in errors)
    assert any("assignment_store" in error for error in errors)


# ---------------------------------------------------------------------------
# Schema-native page structure validation (_scan_page_schema_structure)
# ---------------------------------------------------------------------------


def test_scan_page_schema_structure_rejects_unknown_primitive() -> None:
    errors = scan_generated_bundle(
        {
            "ui/pages/orders.yaml": """
name: Orders
route: /orders
sections:
  - id: orders-table
    primitive: LegacyGridWidget
""",
        }
    )

    assert any("primitive 'LegacyGridWidget' is not a canonical section primitive" in e for e in errors)


def test_scan_page_schema_structure_accepts_canonical_primitives() -> None:
    # Spot-check a representative subset — DataTable, Form, PageHeader, SummaryStrip.
    errors = scan_generated_bundle(
        {
            "ui/pages/dashboard.yaml": """
name: Dashboard
route: /dashboard
sections:
  - id: header
    primitive: PageHeader
  - id: summary
    primitive: SummaryStrip
  - id: table
    primitive: DataTable
  - id: form
    primitive: Form
""",
        }
    )

    assert not any("canonical section primitive" in e for e in errors)


def test_scan_page_schema_structure_rejects_missing_required_fields() -> None:
    errors = scan_generated_bundle(
        {
            "ui/pages/broken.yaml": """
title: Missing name and route
sections: []
""",
        }
    )

    assert any("missing required field 'name'" in e for e in errors)
    assert any("missing required field 'route'" in e for e in errors)


def test_scan_page_schema_structure_skips_custom_pages() -> None:
    # Custom pages under ui/pages/custom/ are React escape-hatch files — not schema-native.
    errors = scan_generated_bundle(
        {
            "ui/pages/custom/SpecialDashboard.yaml": """
primitive: NotCanonicalAtAll
""",
        }
    )

    # Must NOT raise an error for the custom page.
    assert not any("SpecialDashboard" in e for e in errors)
    assert not any("canonical section primitive" in e for e in errors)


# ---------------------------------------------------------------------------
# API endpoint → module/action reference closure (_scan_page_api_endpoint_alignment)
# ---------------------------------------------------------------------------


def _simple_module_yaml(module_id: str, *action_ids: str) -> str:
    actions_yaml = "\n".join(
        f"  - id: {action_id}\n    handler_method: {action_id}" for action_id in action_ids
    )
    return f"""schema_version: mozaiks.module.v1
module:
  id: {module_id}
  handler: backend.handler:Handler
actions:
{actions_yaml if actions_yaml else "  []"}
"""


def test_scan_page_api_endpoint_alignment_skips_when_no_modules() -> None:
    # Bundle with no module.yaml files — api_endpoint closure check must not fire.
    errors = scan_generated_bundle(
        {
            "ui/pages/landing.yaml": """
name: Landing
route: /landing
sections:
  - id: cta
    primitive: Button
    api_endpoint: /api/modules/some_module/some_action
""",
        }
    )

    # The missing-module check fires on a different path; this check must stay silent.
    assert not any("not declared in this bundle" in e for e in errors)


def test_scan_page_api_endpoint_alignment_accepts_valid_endpoint() -> None:
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _simple_module_yaml("orders", "list_orders", "create_order"),
            "modules/orders/backend/handler.py": "class Handler: pass\n",
            "ui/pages/orders.yaml": """
name: Orders
route: /orders
sections:
  - id: orders-table
    primitive: DataTable
    config:
      api_endpoint: /api/modules/orders/list_orders
""",
        }
    )

    assert not any("api_endpoint" in e and "not declared" in e for e in errors)


def test_scan_page_api_endpoint_alignment_rejects_undeclared_module() -> None:
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _simple_module_yaml("orders", "list_orders"),
            "modules/orders/backend/handler.py": "class Handler: pass\n",
            "ui/pages/invoices.yaml": """
name: Invoices
route: /invoices
sections:
  - id: invoices-table
    primitive: DataTable
    config:
      api_endpoint: /api/modules/invoices/list_invoices
""",
        }
    )

    assert any(
        "module 'invoices' which is not declared in this bundle" in e for e in errors
    )


def test_scan_page_api_endpoint_alignment_rejects_undeclared_action() -> None:
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _simple_module_yaml("orders", "list_orders"),
            "modules/orders/backend/handler.py": "class Handler: pass\n",
            "ui/pages/orders.yaml": """
name: Orders
route: /orders
sections:
  - id: orders-table
    primitive: DataTable
    api_endpoint: /api/modules/orders/delete_order
""",
        }
    )

    assert any(
        "action 'delete_order' which is not declared in modules/orders/module.yaml" in e
        for e in errors
    )


def test_scan_page_api_endpoint_alignment_checks_both_section_and_config_endpoints() -> None:
    # The scanner checks both section.api_endpoint and section.config.api_endpoint.
    errors = scan_generated_bundle(
        {
            "modules/users/module.yaml": _simple_module_yaml("users", "list_users"),
            "modules/users/backend/handler.py": "class Handler: pass\n",
            "ui/pages/admin.yaml": """
name: Admin
route: /admin
sections:
  - id: direct-ep
    primitive: DataTable
    api_endpoint: /api/modules/users/ghost_action
  - id: config-ep
    primitive: Form
    config:
      api_endpoint: /api/modules/users/another_ghost
""",
        }
    )

    assert any("ghost_action" in e for e in errors)
    assert any("another_ghost" in e for e in errors)


# ---------------------------------------------------------------------------
# Route manifest → custom page file closure (_scan_route_manifest_component_files)
# ---------------------------------------------------------------------------


def test_scan_route_manifest_component_files_passes_when_no_manifest() -> None:
    errors = scan_generated_bundle(
        {
            "ui/pages/dashboard.yaml": """
name: Dashboard
route: /dashboard
sections:
  - id: header
    primitive: PageHeader
""",
        }
    )

    assert not any("route_manifest" in e and "404" in e for e in errors)


def test_scan_route_manifest_component_files_accepts_present_jsx() -> None:
    errors = scan_generated_bundle(
        {
            "ui/route_manifest.json": '{"pages": [{"path": "/custom", "component": "CustomDashboard"}]}',
            "ui/pages/custom/CustomDashboard.jsx": "export default function CustomDashboard() { return null; }\n",
        }
    )

    assert not any("CustomDashboard" in e and "404" in e for e in errors)


def test_scan_route_manifest_component_files_rejects_missing_jsx() -> None:
    errors = scan_generated_bundle(
        {
            "ui/route_manifest.json": '{"pages": [{"path": "/dashboard", "component": "MissingPage"}]}',
        }
    )

    assert any("MissingPage" in e and "404 at runtime" in e for e in errors)
    assert any("ui/pages/custom/MissingPage.jsx is missing" in e for e in errors)


def test_scan_route_manifest_component_files_rejects_multiple_missing_jsx() -> None:
    errors = scan_generated_bundle(
        {
            "ui/route_manifest.json": """
{
  "pages": [
    {"path": "/alpha", "component": "AlphaPage"},
    {"path": "/beta", "component": "BetaPage"}
  ]
}
""",
            "ui/pages/custom/AlphaPage.jsx": "export default function AlphaPage() { return null; }\n",
        }
    )

    # AlphaPage present → no error for it.
    assert not any("AlphaPage" in e and "404" in e for e in errors)
    # BetaPage missing → error.
    assert any("BetaPage" in e and "404 at runtime" in e for e in errors)


# ---------------------------------------------------------------------------
# Page_type taxonomy (_scan_page_schema_structure — page_type branch)
# ---------------------------------------------------------------------------


def test_scan_page_schema_structure_rejects_unknown_page_type() -> None:
    errors = scan_generated_bundle(
        {
            "ui/pages/dashboard.yaml": """
name: Dashboard
route: /dashboard
page_type: magic_dashboard
sections:
  - id: header
    primitive: PageHeader
""",
        }
    )

    assert any("page_type 'magic_dashboard' is not a canonical page type" in e for e in errors)


def test_scan_page_schema_structure_accepts_canonical_page_types() -> None:
    # Spot-check several canonical page_type values.
    for page_type in ("record_list", "analytics_dashboard", "activity_feed", "settings"):
        errors = scan_generated_bundle(
            {
                f"ui/pages/{page_type}.yaml": f"""
name: Page
route: /{page_type}
page_type: {page_type}
sections:
  - id: s1
    primitive: DataTable
""",
            }
        )
        assert not any("page_type" in e and "canonical" in e for e in errors), (
            f"page_type='{page_type}' should be accepted but got: {errors}"
        )


def test_scan_page_schema_structure_accepts_missing_page_type() -> None:
    # page_type is optional — absence must not be an error.
    errors = scan_generated_bundle(
        {
            "ui/pages/orders.yaml": """
name: Orders
route: /orders
sections:
  - id: table
    primitive: DataTable
""",
        }
    )

    assert not any("page_type" in e for e in errors)


# ---------------------------------------------------------------------------
# Action api_surface taxonomy (_scan_action_api_surface)
# ---------------------------------------------------------------------------


def _module_yaml_with_api_surface(module_id: str, action_id: str, api_surface: str | None) -> str:
    surface_line = f"\n    api_surface: {api_surface}" if api_surface is not None else ""
    return f"""schema_version: mozaiks.module.v1
module:
  id: {module_id}
  handler: backend.handler:Handler
actions:
  - id: {action_id}
    handler_method: {action_id}{surface_line}
"""


def test_scan_action_api_surface_rejects_unknown_value() -> None:
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _module_yaml_with_api_surface(
                "orders", "list_orders", "external"
            ),
            "modules/orders/backend/handler.py": "class Handler: pass\n",
        }
    )

    assert any("api_surface 'external' is not a canonical value" in e for e in errors)


def test_scan_action_api_surface_accepts_all_canonical_values() -> None:
    for surface in ("public", "public_readonly", "internal", "admin_internal"):
        errors = scan_generated_bundle(
            {
                "modules/orders/module.yaml": _module_yaml_with_api_surface(
                    "orders", "list_orders", surface
                ),
                "modules/orders/backend/handler.py": "class Handler: pass\n",
            }
        )
        assert not any("api_surface" in e and "canonical" in e for e in errors), (
            f"api_surface='{surface}' should be accepted but got: {errors}"
        )


def test_scan_action_api_surface_accepts_null_and_absent() -> None:
    # Absent api_surface → no error.
    errors_absent = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _module_yaml_with_api_surface("orders", "op", None),
            "modules/orders/backend/handler.py": "class Handler: pass\n",
        }
    )
    assert not any("api_surface" in e for e in errors_absent)


# ---------------------------------------------------------------------------
# Event/reaction closure (_scan_event_reaction_closure)
# ---------------------------------------------------------------------------


def _events_yaml(module_id: str, *event_types: str) -> str:
    events_block = "\n".join(
        f"  - type: {et}\n    version: 1\n    description: test\n    producer: {module_id}"
        for et in event_types
    )
    return f"schema_version: mozaiks.events.v1\nevents:\n{events_block}\n"


def _reactions_yaml(reactions: list[dict]) -> str:
    lines = ["schema_version: mozaiks.reactions.v1", "reactions:"]
    for r in reactions:
        lines.append(f"  - id: {r['id']}")
        lines.append(f"    event_type: {r['event_type']}")
        lines.append("    target:")
        lines.append(f"      kind: {r['target']['kind']}")
        for k, v in r["target"].items():
            if k != "kind":
                lines.append(f"      {k}: {v}")
    return "\n".join(lines) + "\n"


def test_scan_event_reaction_closure_accepts_valid_bundle() -> None:
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _simple_module_yaml("orders", "create_order"),
            "modules/orders/contracts/events.yaml": _events_yaml(
                "orders", "domain.orders.order_created"
            ),
            "modules/orders/contracts/reactions.yaml": _reactions_yaml(
                [
                    {
                        "id": "on_order_created",
                        "event_type": "domain.orders.order_created",
                        "target": {"kind": "handler", "handler_method": "handle_order_created"},
                    }
                ]
            ),
        }
    )

    assert not any("reaction" in e.lower() for e in errors)


def test_scan_event_reaction_closure_rejects_undeclared_event_type() -> None:
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _simple_module_yaml("orders", "create_order"),
            "modules/orders/contracts/events.yaml": _events_yaml(
                "orders", "domain.orders.order_created"
            ),
            "modules/orders/contracts/reactions.yaml": _reactions_yaml(
                [
                    {
                        "id": "on_ghost_event",
                        "event_type": "domain.orders.ghost_event",
                        "target": {"kind": "handler", "handler_method": "handle_ghost"},
                    }
                ]
            ),
        }
    )

    assert any("ghost_event" in e and "not declared" in e for e in errors)


def test_scan_event_reaction_closure_passes_platform_namespace_events() -> None:
    # hosted.* events are platform-provided — not in bundle events.yaml.
    errors = scan_generated_bundle(
        {
            "modules/billing/module.yaml": _simple_module_yaml("billing", "refresh_status"),
            "modules/billing/contracts/reactions.yaml": _reactions_yaml(
                [
                    {
                        "id": "on_subscription_activated",
                        "event_type": "hosted.billing.subscription.activated",
                        "target": {"kind": "handler", "handler_method": "on_activated"},
                    }
                ]
            ),
        }
    )

    assert not any("not declared" in e for e in errors)


def test_scan_event_reaction_closure_rejects_unknown_target_kind() -> None:
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _simple_module_yaml("orders", "create_order"),
            "modules/orders/contracts/events.yaml": _events_yaml(
                "orders", "domain.orders.order_created"
            ),
            "modules/orders/contracts/reactions.yaml": _reactions_yaml(
                [
                    {
                        "id": "on_order_created",
                        "event_type": "domain.orders.order_created",
                        "target": {"kind": "webhook"},
                    }
                ]
            ),
        }
    )

    assert any("target.kind 'webhook' is not canonical" in e for e in errors)


def test_scan_event_reaction_closure_rejects_handler_without_method() -> None:
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _simple_module_yaml("orders", "create_order"),
            "modules/orders/contracts/events.yaml": _events_yaml(
                "orders", "domain.orders.order_created"
            ),
        }
    )
    # Build a reactions.yaml with handler kind but no handler_method via raw YAML.
    bundle = {
        "modules/orders/module.yaml": _simple_module_yaml("orders", "create_order"),
        "modules/orders/contracts/events.yaml": _events_yaml(
            "orders", "domain.orders.order_created"
        ),
        "modules/orders/contracts/reactions.yaml": (
            "schema_version: mozaiks.reactions.v1\n"
            "reactions:\n"
            "  - id: missing_method\n"
            "    event_type: domain.orders.order_created\n"
            "    target:\n"
            "      kind: handler\n"
        ),
    }
    errors = scan_generated_bundle(bundle)

    assert any("handler_method" in e for e in errors)


def test_scan_event_reaction_closure_accepts_capability_reaction() -> None:
    errors = scan_generated_bundle(
        {
            "modules/commerce/module.yaml": _simple_module_yaml("commerce", "request_checkout"),
            "modules/commerce/contracts/events.yaml": _events_yaml(
                "commerce", "domain.commerce.checkout.requested"
            ),
            "modules/commerce/contracts/reactions.yaml": _reactions_yaml(
                [
                    {
                        "id": "checkout_payment_bridge",
                        "event_type": "domain.commerce.checkout.requested",
                        "target": {
                            "kind": "capability",
                            "capability_id": "mozaikspay.checkout.create_session",
                        },
                    }
                ]
            ),
        }
    )

    assert not any("reaction" in e.lower() for e in errors)


def test_scan_event_reaction_closure_accepts_notification_reaction() -> None:
    errors = scan_generated_bundle(
        {
            "modules/commerce/module.yaml": _simple_module_yaml("commerce", "pay_order"),
            "modules/commerce/contracts/events.yaml": _events_yaml(
                "commerce", "domain.commerce.order.paid"
            ),
            "modules/commerce/contracts/reactions.yaml": _reactions_yaml(
                [
                    {
                        "id": "order_receipt",
                        "event_type": "domain.commerce.order.paid",
                        "target": {
                            "kind": "notification",
                            "notification_id": "commerce_order_receipt",
                        },
                    }
                ]
            ),
        }
    )

    assert not any("reaction" in e.lower() for e in errors)


def test_scan_event_reaction_closure_passes_when_no_reactions_present() -> None:
    # Bundle with no reactions.yaml at all — check must not fire.
    errors = scan_generated_bundle(
        {
            "modules/orders/module.yaml": _simple_module_yaml("orders", "list_orders"),
            "modules/orders/contracts/events.yaml": _events_yaml(
                "orders", "domain.orders.order_created"
            ),
        }
    )

    assert not any("reaction" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Mutation tests — each breaks one reference and verifies early detection
# ---------------------------------------------------------------------------


def _full_crud_bundle() -> dict[str, str]:
    """A representative CRUD bundle with module, events, and reactions wired."""
    return {
        "app.json": '{"name":"Orders"}',
        "modules/orders/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: orders
  handler: backend.handler:OrdersHandler
actions:
  - id: list_orders
    handler_method: list_orders
    api_surface: public_readonly
  - id: create_order
    handler_method: create_order
    emits:
      - domain.orders.order_created
""",
        "modules/orders/backend/handler.py": "class OrdersHandler: pass\n",
        "modules/orders/contracts/events.yaml": """
schema_version: mozaiks.events.v1
events:
  - type: domain.orders.order_created
    version: 1
    description: Emitted after an order is created.
    producer: orders
    payload_schema:
      type: object
      properties:
        order_id: {type: string}
""",
        "modules/orders/contracts/reactions.yaml": """
schema_version: mozaiks.reactions.v1
reactions:
  - id: on_order_created_notify
    event_type: domain.orders.order_created
    target:
      kind: notification
      notification_id: order_receipt
""",
        "ui/pages/orders.yaml": """
name: Orders
route: /orders
page_type: record_list
sections:
  - id: orders-table
    primitive: DataTable
    config:
      api_endpoint: /api/modules/orders/list_orders
""",
    }


def test_full_crud_bundle_passes_all_checks() -> None:
    errors = scan_generated_bundle(_full_crud_bundle())
    assert errors == [], f"Clean CRUD bundle should pass all checks: {errors}"


def test_mutation_unknown_api_surface_caught_early() -> None:
    bundle = _full_crud_bundle()
    bundle["modules/orders/module.yaml"] = bundle["modules/orders/module.yaml"].replace(
        "api_surface: public_readonly", "api_surface: semi_public"
    )
    errors = scan_generated_bundle(bundle)
    assert any("api_surface 'semi_public' is not a canonical value" in e for e in errors)


def test_mutation_unknown_page_type_caught_early() -> None:
    bundle = _full_crud_bundle()
    bundle["ui/pages/orders.yaml"] = bundle["ui/pages/orders.yaml"].replace(
        "page_type: record_list", "page_type: unknown_layout"
    )
    errors = scan_generated_bundle(bundle)
    assert any("page_type 'unknown_layout' is not a canonical page type" in e for e in errors)


def test_mutation_orphaned_reaction_event_caught_early() -> None:
    bundle = _full_crud_bundle()
    bundle["modules/orders/contracts/reactions.yaml"] = bundle[
        "modules/orders/contracts/reactions.yaml"
    ].replace("domain.orders.order_created", "domain.orders.ghost_event")
    errors = scan_generated_bundle(bundle)
    assert any("ghost_event" in e and "not declared" in e for e in errors)


def test_mutation_bad_reaction_target_kind_caught_early() -> None:
    bundle = _full_crud_bundle()
    bundle["modules/orders/contracts/reactions.yaml"] = bundle[
        "modules/orders/contracts/reactions.yaml"
    ].replace("kind: notification", "kind: webhook")
    errors = scan_generated_bundle(bundle)
    assert any("target.kind 'webhook' is not canonical" in e for e in errors)


def test_mutation_missing_api_endpoint_module_caught_early() -> None:
    bundle = _full_crud_bundle()
    bundle["ui/pages/orders.yaml"] = bundle["ui/pages/orders.yaml"].replace(
        "/api/modules/orders/list_orders", "/api/modules/ghost_module/list_orders"
    )
    errors = scan_generated_bundle(bundle)
    assert any("module 'ghost_module' which is not declared" in e for e in errors)
