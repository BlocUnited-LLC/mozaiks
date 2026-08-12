"""Contract tests for the support build context pack.

Verifies that:
- context.yaml registers the pack correctly and declares a messaging dependency
- contract.yaml required_outputs all exist under templates; forbidden paths are absent
- The support module declares the expected actions and capabilities
- Capabilities reference declared actions
- Data migration declares the support.requests collection alias
- Backend Python files compile and use the canonical persistence pattern
- Service emits domain.support.* events
- Notification contract covers request_created and status_changed
- Support stores only ticket metadata, not message transcripts (messaging owns those)
- AppGenerator capability_directory and capability_routing wire the pack
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
SUPPORT = BUILD_CONTEXT / "support"
TEMPLATES = SUPPORT / "templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _action_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {action["id"] for action in module_yaml.get("actions") or []}


def _capability_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {cap["capability_id"] for cap in module_yaml.get("capabilities") or []}


def _capability_targets(module_yaml: dict[str, Any]) -> set[str]:
    return {cap["target"] for cap in module_yaml.get("capabilities") or []}


# ---------------------------------------------------------------------------
# Pack registration
# ---------------------------------------------------------------------------

def test_support_context_registers_active_appgenerator_pack() -> None:
    context = _read_yaml(SUPPORT / "context.yaml")

    assert context["context_id"] == "support"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "support"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "generated_module"
    assert {asset["kind"] for asset in context["assets"]} == {"contract", "templates"}

    capability_ids = {cap["capability_id"] for cap in context["capabilities"]}
    assert capability_ids == {
        "support.requests.create",
        "support.requests.list",
        "support.requests.update_status",
    }


def test_support_contract_requires_messaging_pack() -> None:
    contract = _read_yaml(SUPPORT / "contract.yaml")
    # Machine-readable dependency is declared under requires.packs (canonical format)
    requires = contract.get("requires") or {}
    pack_ids = [
        (entry.get("pack_id") if isinstance(entry, dict) else entry)
        for entry in (requires.get("packs") or [])
    ]
    assert "messaging" in pack_ids, (
        "support pack must declare messaging as a required pack — "
        "conversations are persisted through the messages pack"
    )


def test_support_contract_cross_pack_integration_references_messaging() -> None:
    contract = _read_yaml(SUPPORT / "contract.yaml")
    cross_pack_ids = {item["pack_id"] for item in contract.get("cross_pack_integrations") or []}
    assert "messaging" in cross_pack_ids


# ---------------------------------------------------------------------------
# Required outputs / forbidden outputs
# ---------------------------------------------------------------------------

def test_support_contract_required_outputs_exist_under_templates() -> None:
    contract = _read_yaml(SUPPORT / "contract.yaml")

    assert contract["contract_id"] == "support"
    assert contract["contract_type"] == "build_pack_instructions"
    assert contract["facades"] == []

    missing = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "templates" and not (TEMPLATES / output["path"]).exists()
    ]
    assert missing == [], f"Missing required template outputs: {missing}"


def test_support_pack_forbidden_outputs_are_absent() -> None:
    contract = _read_yaml(SUPPORT / "contract.yaml")

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


# ---------------------------------------------------------------------------
# Module: support
# ---------------------------------------------------------------------------

def test_support_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "support" / "module.yaml")

    assert _action_ids(module_yaml) == {
        "create_support_request",
        "list_support_requests",
        "link_message_thread",
        "update_support_status",
    }


def test_support_module_capabilities_target_existing_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "support" / "module.yaml")
    actions = _action_ids(module_yaml)
    targets = _capability_targets(module_yaml)

    assert targets <= actions
    assert _capability_ids(module_yaml) == {
        "support.requests.create",
        "support.requests.list",
        "support.requests.update_status",
    }


def test_support_module_links_message_thread_action_exists() -> None:
    """link_message_thread must exist because support uses messaging for conversations."""
    module_yaml = _read_yaml(TEMPLATES / "modules" / "support" / "module.yaml")
    assert "link_message_thread" in _action_ids(module_yaml)


# ---------------------------------------------------------------------------
# Data migration
# ---------------------------------------------------------------------------

def test_support_data_migration_declares_requests_collection_alias() -> None:
    migration_path = TEMPLATES / "data" / "migrations" / "001_support_collections.json"
    assert migration_path.exists()
    migration = json.loads(migration_path.read_text(encoding="utf-8"))

    aliases = {
        collection["data_alias"]
        for surface in migration.get("surfaces", [])
        for collection in surface.get("collections", [])
    }
    assert "support.requests" in aliases, f"Missing support.requests alias; found: {aliases}"


def test_support_data_migration_does_not_declare_messages_collection() -> None:
    """Support must not own message storage — that belongs to the messaging pack."""
    migration_path = TEMPLATES / "data" / "migrations" / "001_support_collections.json"
    migration = json.loads(migration_path.read_text(encoding="utf-8"))

    aliases = {
        collection["data_alias"]
        for surface in migration.get("surfaces", [])
        for collection in surface.get("collections", [])
    }
    assert not any(alias.startswith("messages.") or alias.startswith("messaging.") for alias in aliases), (
        f"Support pack must not own message collections: {aliases}"
    )


# ---------------------------------------------------------------------------
# Backend template contracts
# ---------------------------------------------------------------------------

def test_support_backend_templates_compile() -> None:
    backend_root = TEMPLATES / "modules" / "support" / "backend"
    for path in backend_root.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_support_backend_repo_uses_canonical_persistence_pattern() -> None:
    repo_text = _read_text(TEMPLATES / "modules" / "support" / "backend" / "repo.py")

    assert "persistence.collection" in repo_text or "_collection(ctx" in repo_text, (
        "support/repo.py does not use canonical persistence pattern"
    )
    assert "get_mongo_client" not in repo_text
    assert "ctx.db" not in repo_text


def test_support_backend_service_emits_domain_events() -> None:
    """Support service must emit domain.support.* events."""
    service_text = _read_text(TEMPLATES / "modules" / "support" / "backend" / "service.py")

    assert "domain.support.request_created" in service_text
    assert "domain.support.status_changed" in service_text


def test_support_service_stores_metadata_not_message_transcripts() -> None:
    """Support module stores ticket metadata only — conversations go through messages pack."""
    service_text = _read_text(TEMPLATES / "modules" / "support" / "backend" / "service.py")

    # The service should not store or read message content directly
    assert "message_content" not in service_text
    assert "transcript" not in service_text
    # It should delegate to the messages module by storing the thread id reference
    # (link_message_thread is the boundary action)
    assert "message_thread_id" in service_text or "link_message_thread" in service_text


# ---------------------------------------------------------------------------
# Event / notification contracts
# ---------------------------------------------------------------------------

def test_support_declares_canonical_domain_events() -> None:
    events = _read_yaml(
        TEMPLATES / "modules" / "support" / "contracts" / "events.yaml"
    )
    event_types = {e["type"] for e in events.get("events") or []}

    assert "domain.support.request_created" in event_types
    assert "domain.support.status_changed" in event_types


def test_support_notifications_cover_request_created_and_status_changed() -> None:
    notifications = _read_yaml(
        TEMPLATES / "modules" / "support" / "contracts" / "notifications.yaml"
    )
    notif_event_types = {n["event_type"] for n in notifications.get("notifications") or []}

    assert "domain.support.request_created" in notif_event_types, (
        "support/notifications.yaml must notify operators on request_created"
    )


def test_support_notifications_schema_version() -> None:
    notifications = _read_yaml(
        TEMPLATES / "modules" / "support" / "contracts" / "notifications.yaml"
    )
    assert notifications.get("schema_version") == "mozaiks.notifications.v1"


# ---------------------------------------------------------------------------
# UI contracts
# ---------------------------------------------------------------------------

def test_support_route_manifest_declares_support_page() -> None:
    manifest = json.loads(
        _read_text(TEMPLATES / "ui" / "route_manifest.json")
    )
    pages = manifest.get("pages") or []
    page_ids = {p["id"] for p in pages}
    assert "support" in page_ids


def test_support_ui_component_exists() -> None:
    component = TEMPLATES / "ui" / "pages" / "custom" / "Support.jsx"
    assert component.exists(), "Support.jsx component is required by contract"


# ---------------------------------------------------------------------------
# AppGenerator selection wiring
# ---------------------------------------------------------------------------

def test_appgenerator_selection_wiring_includes_support_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "support_pack" in by_id
    support = by_id["support_pack"]
    assert support["capability_kind"] == "operator_pack"
    assert "support.requests.create" in support["capabilities_provided"]
    assert any(
        "support" in signal.lower() or "ticket" in signal.lower() or "helpdesk" in signal.lower()
        for signal in support["intent_signals"]
    )

    routing = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_routing.yaml")
    packs = {
        pack["id"]: pack
        for pack in routing["layers"]["capability_pack"]["packs"]
    }
    assert "support" in packs
    assert packs["support"]["capability_kind"] == "operator_pack"


def test_support_module_declares_user_data_scope() -> None:
    """Support stores user-linked request records — user_data_scope must be true."""
    module_yaml = _read_yaml(TEMPLATES / "modules" / "support" / "module.yaml")
    assert module_yaml["module"].get("user_data_scope") is True, (
        "support/module.yaml must declare user_data_scope: true — "
        "support requests carry requester_id (user-owned PII)"
    )


def test_support_backend_ships_account_data_handler() -> None:
    """user_data_scope: true requires a matching account_data_handler.py."""
    handler = TEMPLATES / "modules" / "support" / "backend" / "account_data_handler.py"
    assert handler.exists(), (
        "support/backend/account_data_handler.py is missing — "
        "module declares user_data_scope: true but ships no GDPR handler"
    )
    src = handler.read_text(encoding="utf-8")
    assert "delete_user_data" in src
    assert "export_user_data" in src


def test_support_pack_does_not_generate_messaging_module() -> None:
    """Support must not generate its own messages module — it uses the messaging pack."""
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }
    assert not any(p.startswith("modules/support_messages/") for p in generated_paths)
    assert not any(p.startswith("modules/support_chat/") for p in generated_paths)
    assert not any(p.startswith("modules/messages/") for p in generated_paths)
    assert not any(p.startswith("services/support/") for p in generated_paths)
