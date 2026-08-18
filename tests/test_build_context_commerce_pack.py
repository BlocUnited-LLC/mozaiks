from __future__ import annotations

from pathlib import Path
from typing import Any

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
    assert "mozaikspay" in {item["pack"] for item in contract["cross_pack_integrations"]}

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


def test_commerce_data_migration_declares_stable_aliases_and_indexes() -> None:
    migration = _read_yaml(TEMPLATES / "data" / "migrations" / "001_commerce_collections.json")
    collections = migration["surfaces"][0]["collections"]

    aliases = {collection["data_alias"] for collection in collections}
    assert aliases == {
        "commerce.products",
        "commerce.carts",
        "commerce.orders",
        "commerce.checkout_requests",
    }

    index_names = {
        index["name"]
        for collection in collections
        for index in collection.get("indexes", [])
    }
    assert {
        "products_by_id",
        "carts_by_actor_status",
        "orders_by_provider_reference",
        "checkout_requests_by_status_created",
    } <= index_names


def test_commerce_backend_templates_compile_and_use_runtime_persistence() -> None:
    backend_root = TEMPLATES / "modules" / "commerce" / "backend"

    for path in backend_root.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    repo_text = _read_text(backend_root / "repo.py")
    assert "app_data_from_context" in repo_text
    assert "get_mongo_client" not in repo_text
    assert "ctx.db" not in repo_text

    service_text = _read_text(backend_root / "service.py")
    assert "domain.commerce.checkout.requested" in service_text
    assert "record_checkout_result" in service_text
    assert "/api/modules/mozaikspay" not in service_text
    assert "services.integrations.mozaikspay_client" not in service_text


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
    bridge = reactions["reactions"][0]
    assert bridge["event_type"] == "domain.commerce.checkout.requested"
    assert bridge["target"]["capability_id"] == "mozaikspay.checkout.create_session"


def test_commerce_page_templates_use_canonical_primitives_and_app_owned_endpoints() -> None:
    allowed_primitives = {"PageHeader", "ResourceTable", "SummaryStrip", "Form", "ActionButton"}
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
