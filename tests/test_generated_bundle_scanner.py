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
sections:
  - config:
      api_endpoint: /api/modules/billing_portal/get_subscription_status
  - config:
      api_endpoint: /api/modules/billing_portal/open_billing_portal
""",
        "ui/pages/usage.yaml": """
sections:
  - config:
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
