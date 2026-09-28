"""Every shipped capability pack's templates pass the wiring gate they will face.

Pack templates replace authored pages at assembly, so acceptance validates the
templates, not the author. A template that binds an undeclared response field
or leaves a gated action unreachable would fail every app that selects the pack.
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
from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    reachable_page_action_keys,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    compile_page_data_sources,
    module_action_index,
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
def test_pack_template_pages_compile_against_their_own_module_contracts(pack_dir: Path):
    files = _template_files(pack_dir)
    modules = module_action_index(files)
    pages = _pages(files)
    for path, page in pages.items():
        validate_page_schema(page, expected_name=Path(path).stem)
        compile_page_data_sources(page, modules, reject_api_endpoints=False, workflow_names=set(), path=path)
    gated = {
        f"{module}/{action_id}" for module, actions in modules.items()
        for action_id, action in actions.items() if action.get("entitlement_gate")
    }
    assert gated <= reachable_page_action_keys(list(pages.values()))


@pytest.mark.parametrize("pack_dir", _packs(), ids=lambda pack: pack.name)
def test_every_template_page_is_a_declared_pack_output(pack_dir: Path):
    """Authoring defers a page's binding checks only for declared template outputs.

    A template page the contract does not declare would be checked against the
    author's placeholder contract at task time and then replaced at assembly.
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
