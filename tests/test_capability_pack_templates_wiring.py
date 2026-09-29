"""Every shipped capability pack's templates pass the assembly checks they will face.

Pack-owned outputs are never model work: assembly takes them only from the
templates and runs its page checks on the template bytes, with the same
function this test calls. A template that binds an undeclared response field,
breaks the page schema, or leaves a gated action unreachable would fail every
app that selects the pack, and no task could repair it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_templates_for_pack,
)
from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    reachable_page_action_keys,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index,
    pack_template_page_errors,
)

BUILD_CONTEXT = Path(__file__).resolve().parents[1] / "factory_app" / "build_context"
# Template variables a pack may render; values are fixtures, not policy.
RENDER_CONTEXT = {
    "build_timestamp": "2026-01-01T00:00:00Z",
    "readiness_profile": "saas_app",
    "evidence_mode": "local",
    "evidence_ledger_path": "docs/operations/evidence.md",
    "launch_check_command": "python scripts/check_operator_readiness_local.py",
    "monetization_check_command": "python scripts/check_operator_readiness_local.py --monetization",
}


def _packs() -> list[Path]:
    packs = []
    for pack_dir in sorted(BUILD_CONTEXT.iterdir()):
        context = pack_dir / "context.yaml"
        if not (pack_dir / "templates").is_dir() or not context.is_file():
            continue
        config = yaml.safe_load(context.read_text(encoding="utf-8")) or {}
        if isinstance(config.get("pack"), dict):
            packs.append(pack_dir)
    return packs


def _template_files(pack_dir: Path) -> dict[str, str]:
    return {
        file["filename"]: file["content"]
        for file in resolve_templates_for_pack(pack_dir, pack_dir.name, context_variables=dict(RENDER_CONTEXT))
    }


def _pages(files: dict[str, str]) -> dict[str, dict]:
    return {
        path: yaml.safe_load(content)
        for path, content in files.items()
        if path.startswith("ui/pages/") and path.endswith((".yaml", ".yml"))
    }


def test_the_pack_inventory_has_declarative_pages_to_check():
    assert {"commerce", "mozaikspay"} <= {pack.name for pack in _packs()}


@pytest.mark.parametrize("pack_dir", _packs(), ids=lambda pack: pack.name)
def test_pack_templates_pass_the_wiring_gate(pack_dir: Path):
    files = _template_files(pack_dir)
    result = asyncio.run(validate_wiring({"generated_files": files}))
    assert result["passed"], [(item.get("test"), item.get("error")) for item in result.get("failed_tests") or []]
    assert result["checks"][0]["details"]["unreachable_gated_actions"] == []


@pytest.mark.parametrize("pack_dir", _packs(), ids=lambda pack: pack.name)
def test_pack_template_pages_pass_the_assembly_checks_against_their_own_module_contracts(pack_dir: Path):
    """The same check assembly runs on template pages: schema, action closure, bindings, workflows."""
    files = _template_files(pack_dir)
    modules = module_action_index(files)
    pages = _pages(files)
    errors = [
        error for path in pages
        for error in pack_template_page_errors(files[path], path=path, modules=modules, workflow_names=set())
    ]
    assert errors == []
    gated = {
        f"{module}/{action_id}" for module, actions in modules.items()
        for action_id, action in actions.items() if action.get("entitlement_gate")
    }
    assert gated <= reachable_page_action_keys(list(pages.values()))


@pytest.mark.parametrize("pack_dir", _packs(), ids=lambda pack: pack.name)
def test_every_template_owned_output_ships_a_template(pack_dir: Path):
    """No task builds a pack-owned output, so a declared template output with no template is a hole."""
    contract = yaml.safe_load((pack_dir / "contract.yaml").read_text(encoding="utf-8")) or {}
    template_owned = {
        str(entry.get("path") if isinstance(entry, dict) else entry)
        for entry in contract.get("required_outputs") or []
        if not isinstance(entry, dict) or str(entry.get("owner") or "templates") == "templates"
    }
    assert template_owned <= set(_template_files(pack_dir)), sorted(template_owned - set(_template_files(pack_dir)))


def test_the_assembly_template_check_rejects_the_stale_billing_page_a_live_run_shipped():
    """Chat fdfa818e read mozaikspay from a stale checkout (1bdaf248): billing-status was a DataTable.

    Assembly failed there with "Billing/billing-status.data_key: 'None' must select a declared
    array"; the shared check names the same defect for any pack template that regresses to it.
    """
    files = _template_files(BUILD_CONTEXT / "mozaikspay")
    stale = yaml.safe_load(files["ui/pages/billing.yaml"])
    stale["sections"][0].update(primitive="DataTable", config={
        "api_endpoint": "/api/modules/billing_portal/get_subscription_status",
        "columns": [{"key": "plan_name", "label": "Plan"}, {"key": "status", "label": "Status"}],
    })
    errors = pack_template_page_errors(
        yaml.safe_dump(stale), path="ui/pages/billing.yaml", modules=module_action_index(files),
        workflow_names=set(), planned={"route": "/billing"},
    )
    assert len(errors) == 1
    assert "Billing/billing-status.data_key: 'None' must select a declared array" in errors[0]
    assert pack_template_page_errors(
        files["ui/pages/billing.yaml"], path="ui/pages/billing.yaml", modules=module_action_index(files),
        planned={"route": "/pricing"},
    ) == ["ui/pages/billing.yaml: route '/billing' does not match the approved route '/pricing'"]


@pytest.mark.parametrize("pack_dir", _packs(), ids=lambda pack: pack.name)
def test_every_template_page_is_a_declared_pack_output(pack_dir: Path):
    """Only declared outputs are pack-owned, so an undeclared template page would still be authored.

    The author's copy would be validated as model work and then overwritten by the template.
    """
    contract = yaml.safe_load((pack_dir / "contract.yaml").read_text(encoding="utf-8")) or {}
    declared = {
        str(entry.get("path") if isinstance(entry, dict) else entry)
        for entry in contract.get("required_outputs") or []
    }
    template_pages = set(_pages(_template_files(pack_dir)))
    assert template_pages <= declared, sorted(template_pages - declared)


def test_commerce_cart_and_product_creation_are_bound_to_declared_contracts():
    files = _template_files(BUILD_CONTEXT / "commerce")
    cart = yaml.safe_load(files["modules/commerce/module.yaml"])
    get_cart = next(action for action in cart["actions"] if action["id"] == "get_cart")
    items = get_cart["output_schema"]["properties"]["cart"]["properties"]["items"]["items"]["properties"]
    assert {"title", "quantity", "unit_amount", "line_total"} <= set(items)
    products = yaml.safe_load(files["ui/pages/products.yaml"])
    assert "commerce/create_product" in reachable_page_action_keys([products])
