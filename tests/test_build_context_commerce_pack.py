from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
COMMERCE = BUILD_CONTEXT / "commerce"
TEMPLATES = COMMERCE / "templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _walk_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _action_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {action["id"] for action in module_yaml.get("actions") or []}


def test_commerce_context_registers_active_appgenerator_pack() -> None:
    context = _read_yaml(COMMERCE / "context.yaml")

    assert context["context_id"] == "commerce"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "commerce"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "generated_module"
    assert {asset["kind"] for asset in context["assets"]} == {"contract", "templates"}

    capability_ids = {capability["capability_id"] for capability in context["capabilities"]}
    assert capability_ids == {
        "commerce.products.browse",
        "commerce.products.manage",
        "commerce.inventory.manage",
        "commerce.cart.manage",
        "commerce.checkout.start",
        "commerce.orders.manage",
    }


def test_commerce_contract_required_outputs_exist_under_templates() -> None:
    contract = _read_yaml(COMMERCE / "contract.yaml")

    assert contract["contract_id"] == "commerce"
    assert contract["contract_type"] == "build_pack_instructions"
    assert contract["facades"] == []
    payments = next(item for item in contract["cross_pack_integrations"] if item["pack"] == "mozaikspay")
    assert payments["optional"] is True

    missing = [
        output["path"]
        for output in contract["required_outputs"]
        if not (TEMPLATES / output["path"]).exists()
    ]
    assert missing == []

    forbidden = {entry["path_prefix"] for entry in contract["forbidden_outputs"]}
    assert {
        "modules/ecommerce/",
        "modules/shop/",
        "modules/marketplace/",
        "modules/mozaikspay/",
        "modules/payment_provider/",
        "services/adapters/payments/",
    } <= forbidden


def test_commerce_pack_pin_matches_across_git_line_endings(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        verify_pack_integrity,
    )

    repo = tmp_path / "source"
    pack_path = COMMERCE.relative_to(WORKSPACE)
    shutil.copytree(COMMERCE, repo / pack_path, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(WORKSPACE / ".gitattributes", repo / ".gitattributes")
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "--quiet"], check=True)
    subprocess.run([*git, "-c", "core.autocrlf=false", "add", "."], check=True)

    pins = []
    for autocrlf in ("false", "true"):
        output = tmp_path / autocrlf
        subprocess.run(
            [*git, "-c", f"core.autocrlf={autocrlf}", "checkout-index", "--all", f"--prefix={output.as_posix()}/"],
            check=True,
        )
        pins.append(verify_pack_integrity(output / pack_path, "commerce")["digest"])
    assert pins[0] == pins[1]


def test_commerce_module_declares_expected_actions_and_internal_checkout_callback() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "commerce" / "module.yaml")
    actions = {action["id"]: action for action in module_yaml["actions"]}

    assert _action_ids(module_yaml) == {
        "list_products",
        "get_product",
        "create_product",
        "update_product",
        "archive_product",
        "adjust_inventory",
        "get_cart",
        "add_cart_item",
        "update_cart_item",
        "remove_cart_item",
        "clear_cart",
        "start_checkout",
        "record_checkout_result",
        "list_orders",
        "get_order",
        "update_order_status",
    }
    assert actions["start_checkout"]["emits"] == ["domain.commerce.checkout.requested"]
    assert actions["record_checkout_result"]["api_surface"] == "internal"
    assert actions["record_checkout_result"]["permissions"] == []
    assert "domain.commerce.order.paid" in actions["record_checkout_result"]["emits"]

    for action_id in ("list_products", "list_orders"):
        output = actions[action_id]["output_schema"]["properties"]
        array_field = "products" if action_id == "list_products" else "orders"
        assert "items" in output[array_field]
        assert "properties" in output[array_field]["items"]


def test_commerce_module_capabilities_target_existing_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "commerce" / "module.yaml")
    actions = _action_ids(module_yaml)
    targets = {capability["target"] for capability in module_yaml["capabilities"]}
    capability_ids = {capability["capability_id"] for capability in module_yaml["capabilities"]}

    assert targets <= actions
    assert {
        "commerce.products.browse",
        "commerce.products.manage",
        "commerce.inventory.manage",
        "commerce.cart.manage",
        "commerce.checkout.start",
        "commerce.orders.manage",
    } <= capability_ids


def test_commerce_data_contract_declares_collections_and_migration_ensures_them() -> None:
    contract = json.loads(_read_text(TEMPLATES / "data" / "contract.json"))
    assert contract["shared_collections"] == []
    [surface] = contract["surfaces"]
    assert (surface["surface_id"], surface["surface_kind"]) == ("commerce", "module")
    collections = {collection["name"]: collection for collection in surface["collections"]}
    assert set(collections) == {"products", "carts", "orders", "checkout_requests"}
    for collection in collections.values():
        assert (collection["tenancy"], collection["owner_field"]) == ("app_wide", None)
        assert collection["lifecycle"] == {"write_mode": "module_action"}

    index_names = {
        index["name"]
        for collection in collections.values()
        for index in collection.get("indexes", [])
    }
    assert {
        "products_by_id",
        "carts_by_actor_status",
        "orders_by_provider_reference",
        "checkout_requests_by_status_created",
    } <= index_names

    migration = json.loads(_read_text(TEMPLATES / "data" / "migrations" / "001_commerce_collections.json"))
    assert {"surfaces", "aliases", "shared_collections"}.isdisjoint(migration)
    operations = migration["operations"]
    assert {
        (operation["module_id"], operation["entity_name"])
        for operation in operations if operation["type"] == "ensure_collection"
    } == {("commerce", name) for name in collections}
    assert {
        (operation["entity_name"], operation["index"]["name"])
        for operation in operations if operation["type"] == "ensure_index"
    } == {
        (name, index["name"])
        for name, collection in collections.items()
        for index in collection.get("indexes", [])
    }


def test_commerce_backend_templates_compile_and_use_runtime_persistence() -> None:
    backend_root = TEMPLATES / "modules" / "commerce" / "backend"

    for path in backend_root.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    repo_text = _read_text(backend_root / "repo.py")
    assert 'persistence.collection("commerce", entity_name)' in repo_text
    assert "app_data_from_context" not in repo_text
    assert "get_mongo_client" not in repo_text
    assert "ctx.db" not in repo_text
    contract = json.loads(_read_text(TEMPLATES / "data" / "contract.json"))
    declared = {collection["name"] for collection in contract["surfaces"][0]["collections"]}
    assert set(re.findall(r'_collection\(ctx, "([a-z_]+)"\)', repo_text)) == declared

    service_text = _read_text(backend_root / "service.py")
    assert "domain.commerce.checkout.requested" in service_text
    assert "record_checkout_result" in service_text
    assert "/api/modules/mozaikspay" not in service_text
    assert "services.integrations.mozaikspay_client" not in service_text


def test_standalone_commerce_registers_reactions_without_a_payment_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factory_app.workflows.AppGenerator.tools.app_validation import _write_files_to_dir
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )
    from mozaiksai.core.account import account_data_registry
    from mozaiksai.core.runtime.app.module_loader import ModuleLoader
    from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter

    monkeypatch.setattr(account_data_registry, "_handlers", {})
    files = resolve_managed_capability_templates([{"id": "commerce", "capability_source": "operator_pack"}])
    _write_files_to_dir(tmp_path, {file["filename"]: file["content"] for file in files})
    commerce = ModuleLoader(str(tmp_path)).load("commerce")
    router = ModuleEventRouter([commerce])

    assert "domain.commerce.checkout.requested" not in router.event_types
    assert {"domain.commerce.order.paid", "domain.commerce.checkout.failed"} <= set(router.event_types)


def test_commerce_contracts_declare_events_admin_settings_and_reactions() -> None:
    contracts_root = TEMPLATES / "modules" / "commerce" / "contracts"

    events = _read_yaml(contracts_root / "events.yaml")
    event_types = {event["type"] for event in events["events"]}
    assert {
        "domain.commerce.product.created",
        "domain.commerce.cart.updated",
        "domain.commerce.checkout.requested",
        "domain.commerce.order.paid",
        "domain.commerce.checkout.failed",
    } <= event_types

    admin = _read_yaml(contracts_root / "admin.yaml")
    assert {panel["id"] for panel in admin["panels"]} == {
        "commerce_catalog_panel",
        "commerce_inventory_panel",
        "commerce_orders_panel",
    }

    settings = _read_yaml(contracts_root / "settings.yaml")
    assert "commerce.checkout.default_provider" in {item["id"] for item in settings["settings"]}

    reactions = _read_yaml(contracts_root / "reactions.yaml")
    assert {
        (row["event_type"], row["target"]["notification_id"])
        for row in reactions["reactions"] if row["target"]["kind"] == "notification"
    } == {
        ("domain.commerce.order.paid", "commerce_order_receipt"),
        ("domain.commerce.checkout.failed", "commerce_checkout_failed"),
    }


def test_commerce_page_templates_use_canonical_primitives_and_app_owned_endpoints() -> None:
    # The products page opens a create-product Modal so the gated create_product
    # action has a page entry point; Modal is a shipped platform primitive.
    allowed_primitives = {"PageHeader", "ResourceTable", "SummaryStrip", "Form", "ActionButton", "Modal"}
    valid_page_types = set(
        _read_yaml(WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml")
        ["models"]["AppPageSchema"]["fields"]["page_type"]["values"]
    )

    for path in (TEMPLATES / "ui" / "pages").glob("*.yaml"):
        page = _read_yaml(path)
        assert page["schema_version"] == "mozaiks.app_page.v1"
        assert page["page_type"] in valid_page_types
        assert page["layout"] in {"grid", "sidebar", "full-width", "split"}

        primitives = {section["primitive"] for section in page["sections"]}
        assert primitives <= allowed_primitives

        for value in _walk_values(page):
            if not isinstance(value, str):
                continue
            if value.startswith("/api/modules/"):
                assert value.startswith("/api/modules/commerce/")
                assert "?" not in value and "#" not in value
                assert "/api/modules/mozaikspay/" not in value


def test_commerce_page_titles_follow_the_app_page_title_role() -> None:
    headers = [
        (path.name, section["config"])
        for path in sorted((TEMPLATES / "ui" / "pages").glob("*.yaml"))
        for section in _read_yaml(path)["sections"]
        if section["primitive"] == "PageHeader"
    ]
    assert {name for name, _ in headers} == {"cart.yaml", "orders.yaml", "product_detail.yaml", "products.yaml"}
    for name, config in headers:
        # Omitting title_font selects the app-owned ui.page.title font.
        assert "title_font" not in config, name


def test_appgenerator_selection_wiring_includes_commerce_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}
    commerce = by_id["commerce_pack"]
    assert commerce["capability_kind"] == "operator_pack"
    assert "commerce.products.browse" in commerce["capabilities_provided"]
    assert any("checkout" in signal.lower() for signal in commerce["intent_signals"])
    assert any("managed payment" in note.lower() for note in commerce["generator_notes"])

    routing = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_routing.yaml")
    packs = {
        pack["id"]: pack
        for pack in routing["layers"]["capability_pack"]["packs"]
    }
    assert packs["commerce"]["capability_kind"] == "operator_pack"
    assert "checkout" in packs["commerce"]["covers"]

    value_outputs = _read_yaml(WORKSPACE / "factory_app" / "workflows" / "ValueEngine" / "structured_outputs.yaml")
    app_outputs = _read_yaml(WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml")
    assert "commerce_pack" in value_outputs["models"]["CapabilityPackSpec"]["fields"]["pack_type"]["values"]
    assert "commerce_pack" in app_outputs["models"]["AppCapabilityPack"]["fields"]["pack_type"]["values"]


def test_commerce_module_declares_user_data_scope() -> None:
    """Commerce stores user-linked carts and orders — user_data_scope must be true."""
    module_yaml = _read_yaml(TEMPLATES / "modules" / "commerce" / "module.yaml")
    assert module_yaml["module"].get("user_data_scope") is True, (
        "commerce/module.yaml must declare user_data_scope: true — "
        "carts (actor_id) and orders (actor_id) are user-owned PII"
    )


def test_commerce_backend_ships_account_data_handler() -> None:
    """user_data_scope: true requires a matching account_data_handler.py."""
    handler = TEMPLATES / "modules" / "commerce" / "backend" / "account_data_handler.py"
    assert handler.exists(), (
        "commerce/backend/account_data_handler.py is missing — "
        "module declares user_data_scope: true but ships no GDPR handler"
    )
    src = handler.read_text(encoding="utf-8")
    assert "delete_user_data" in src
    assert "export_user_data" in src


def test_commerce_pack_does_not_generate_payment_provider_or_marketplace_modules() -> None:
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }

    assert not any(path.startswith("modules/mozaikspay/") for path in generated_paths)
    assert not any(path.startswith("modules/payment_provider/") for path in generated_paths)
    assert not any(path.startswith("modules/marketplace/") for path in generated_paths)
    assert not any(path.startswith("services/adapters/payments/") for path in generated_paths)
