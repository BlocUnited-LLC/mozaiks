from __future__ import annotations

import json

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle


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
            "build_context/HostedPayments/context.yaml": "context_id: hosted_context\n",
            "app.json": '{"name":"Demo"}',
        }
    )

    assert any("outside the canonical app planes" in error for error in errors)
    assert any("build_context/HostedPayments/context.yaml" in error for error in errors)


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
            "services/integrations/payments_client.py": "import stripe\nstripe.api_key = 'sk_test_1234567890'\n",
        }
    )

    assert any("raw provider secret key literal" in error for error in errors)
    assert any("imports the Stripe SDK directly" in error for error in errors)
    assert any("assigns stripe.api_key directly" in error for error in errors)


def test_scan_generated_bundle_rejects_direct_provider_refund_calls() -> None:
    errors = scan_generated_bundle(
        {
            "modules/payments/backend/service.py": (
                "result = stripe.Refund.create(payment_intent=payment_intent_id)\n"
            ),
            "services/integrations/payments_client.py": 'url = "/v1/refunds"\n',
        }
    )

    assert any("refunds APIs directly" in error for error in errors)
    assert any("/v1/refunds" in error for error in errors)


def test_scan_generated_bundle_allows_hosted_refund_adapter_call() -> None:
    errors = scan_generated_bundle(
        {
            "modules/billing_portal/backend/service.py": (
                "from services.integrations.mozaikspay_client import MozaiksPayClient\n"
                "result = await client.request_refund(payment_id=payment_id, amount=amount)\n"
            )
        }
    )

    assert errors == []


def test_scan_generated_bundle_enforces_selected_hosted_pack_adapter() -> None:
    errors = scan_generated_bundle(
        {
            "app.json": "{}",
            "ui/pages/billing.yaml": "sections: []\n",
        },
        capability_packs=[{"id": "mozaikspay", "capability_source": "hosted_pack"}],
    )

    assert any("services/integrations/mozaikspay_client.py" in error for error in errors)


def test_scan_generated_bundle_rejects_selected_hosted_pack_internals() -> None:
    errors = scan_generated_bundle(
        {
            "services/integrations/mozaikspay_client.py": "class MozaiksPayClient: pass\n",
            "modules/mozaikspay/module.yaml": "module:\n  id: mozaikspay\n",
        },
        capability_packs=[{"id": "mozaikspay", "capability_source": "hosted_pack"}],
    )

    assert any("must not generate hosted internals" in error for error in errors)


def test_scan_generated_bundle_rejects_page_binding_to_selected_hosted_pack() -> None:
    errors = scan_generated_bundle(
        {
            "services/integrations/mozaikspay_client.py": "class MozaiksPayClient: pass\n",
            "ui/pages/billing.yaml": 'api_endpoint: "/api/modules/mozaikspay/create_checkout_session"\n',
        },
        capability_packs=[{"id": "mozaikspay", "capability_source": "hosted_pack"}],
    )

    assert any("page binds directly to hosted pack endpoint" in error for error in errors)
