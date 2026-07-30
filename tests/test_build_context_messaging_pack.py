"""Contract tests for the messaging build context pack.

Verifies that:
- context.yaml registers the pack correctly for AppGenerator
- contract.yaml required_outputs all exist under templates; forbidden paths absent
- The messages module declares expected actions and capabilities with alignment
- user_data_scope declared and account_data_handler.py ships (private conversations = user PII)
- Data migration declares all three stable collection aliases
- Backend files compile and use canonical persistence pattern
- Service emits domain.messages.* events
- Notification contract covers message_sent; no notification delivery worker generated
- Profile tab declared for the DM inbox
- AppGenerator capability_directory and capability_routing wire the pack correctly
- No announcement, contacts, or monolithic messaging module generated
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
MESSAGING = BUILD_CONTEXT / "messaging"
TEMPLATES = MESSAGING / "templates"


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

def test_messaging_context_registers_active_appgenerator_pack() -> None:
    context = _read_yaml(MESSAGING / "context.yaml")

    assert context["context_id"] == "messaging"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "messaging"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "generated_module"
    assert {asset["kind"] for asset in context["assets"]} == {"contract", "templates"}

    capability_ids = {cap["capability_id"] for cap in context["capabilities"]}
    assert capability_ids == {
        "messaging.threads.list",
        "messaging.messages.send",
    }


# ---------------------------------------------------------------------------
# Required / forbidden outputs
# ---------------------------------------------------------------------------

def test_messaging_contract_required_outputs_exist_under_templates() -> None:
    contract = _read_yaml(MESSAGING / "contract.yaml")

    assert contract["contract_id"] == "messaging"
    assert contract["contract_type"] == "build_pack_instructions"
    assert contract["facades"] == []

    missing = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "templates" and not (TEMPLATES / output["path"]).exists()
    ]
    assert missing == [], f"Missing required template outputs: {missing}"


def test_messaging_pack_forbidden_outputs_are_absent() -> None:
    contract = _read_yaml(MESSAGING / "contract.yaml")

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
# Module: messages
# ---------------------------------------------------------------------------

def test_messages_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "messages" / "module.yaml")

    assert _action_ids(module_yaml) == {
        "create_thread",
        "list_threads",
        "get_thread",
        "send_message",
        "mark_thread_read",
    }


def test_messages_module_capabilities_target_existing_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "messages" / "module.yaml")
    actions = _action_ids(module_yaml)
    targets = _capability_targets(module_yaml)

    assert targets <= actions
    assert _capability_ids(module_yaml) == {
        "messaging.threads.list",
        "messaging.messages.send",
    }


def test_messages_module_declares_user_data_scope() -> None:
    """Private conversations are user-owned PII — module must declare user_data_scope."""
    module_yaml = _read_yaml(TEMPLATES / "modules" / "messages" / "module.yaml")
    assert module_yaml.get("module", {}).get("user_data_scope") is True, (
        "messages module stores private user conversations (PII) and must declare "
        "user_data_scope: true under the module: key"
    )


def test_messages_module_ships_account_data_handler() -> None:
    """Private conversations require GDPR delete + export support."""
    handler = TEMPLATES / "modules" / "messages" / "backend" / "account_data_handler.py"
    assert handler.exists(), (
        "messages module must ship account_data_handler.py — "
        "thread and message records are user-owned PII"
    )
    source = handler.read_text(encoding="utf-8")
    assert "delete_user_data" in source
    assert "export_user_data" in source


def test_messages_contract_declares_account_data_handler_as_required_output() -> None:
    contract = _read_yaml(MESSAGING / "contract.yaml")
    output_paths = {output["path"] for output in contract["required_outputs"]}
    assert "modules/messages/backend/account_data_handler.py" in output_paths, (
        "contract.yaml must declare account_data_handler.py as a required output "
        "since messages/threads are user PII"
    )


# ---------------------------------------------------------------------------
# Data migration
# ---------------------------------------------------------------------------

def test_messaging_data_migration_declares_stable_collection_aliases() -> None:
    migration_path = TEMPLATES / "data" / "migrations" / "001_messaging_collections.json"
    assert migration_path.exists()
    migration = json.loads(migration_path.read_text(encoding="utf-8"))

    aliases = {
        collection["data_alias"]
        for surface in migration.get("surfaces", [])
        for collection in surface.get("collections", [])
    }
    assert aliases == {
        "messages.threads",
        "messages.messages",
        "messages.thread_reads",
    }


# ---------------------------------------------------------------------------
# Backend template contracts
# ---------------------------------------------------------------------------

def test_messaging_backend_templates_compile() -> None:
    backend_root = TEMPLATES / "modules" / "messages" / "backend"
    for path in backend_root.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_messaging_backend_repo_uses_canonical_persistence_pattern() -> None:
    repo_text = _read_text(TEMPLATES / "modules" / "messages" / "backend" / "repo.py")

    assert "persistence.collection" in repo_text or "_collection(ctx" in repo_text, (
        "messages/repo.py must use canonical persistence.collection() pattern"
    )
    assert "get_mongo_client" not in repo_text
    assert "ctx.db" not in repo_text


def test_messaging_service_emits_domain_events() -> None:
    service_text = _read_text(TEMPLATES / "modules" / "messages" / "backend" / "service.py")

    assert "domain.messages.thread_created" in service_text
    assert "domain.messages.message_sent" in service_text


def test_messaging_service_does_not_deliver_notifications_directly() -> None:
    """Notification delivery is declared in notifications.yaml, not implemented in service.py."""
    service_text = _read_text(TEMPLATES / "modules" / "messages" / "backend" / "service.py")

    # Service must not import or call a notification delivery client
    assert "notification_client" not in service_text
    assert "send_notification" not in service_text
    assert "push_notification" not in service_text


def test_messaging_service_does_not_include_announcement_action() -> None:
    """create_announcement is hosted-platform only and must not appear in OSS templates."""
    service_text = _read_text(TEMPLATES / "modules" / "messages" / "backend" / "service.py")
    handler_text = _read_text(TEMPLATES / "modules" / "messages" / "backend" / "handler.py")

    assert "create_announcement" not in service_text
    assert "create_announcement" not in handler_text


# ---------------------------------------------------------------------------
# Event / notification / profile contracts
# ---------------------------------------------------------------------------

def test_messages_declares_canonical_domain_events() -> None:
    events = _read_yaml(
        TEMPLATES / "modules" / "messages" / "contracts" / "events.yaml"
    )
    event_types = {e["type"] for e in events.get("events") or []}

    assert "domain.messages.thread_created" in event_types
    assert "domain.messages.message_sent" in event_types
    assert "domain.messages.thread_read" in event_types


def test_messages_notification_covers_message_sent() -> None:
    notifications = _read_yaml(
        TEMPLATES / "modules" / "messages" / "contracts" / "notifications.yaml"
    )
    assert notifications.get("schema_version") == "mozaiks.notifications.v1"
    notif_event_types = {n["event_type"] for n in notifications.get("notifications") or []}
    assert "domain.messages.message_sent" in notif_event_types


def test_messages_profile_tab_declared_for_dm_inbox() -> None:
    profile = _read_yaml(
        TEMPLATES / "modules" / "messages" / "contracts" / "profile.yaml"
    )
    assert profile.get("schema_version") == "mozaiks.profile.v1"
    tab_ids = {tab["id"] for tab in profile.get("tabs") or []}
    assert "messages" in tab_ids


# ---------------------------------------------------------------------------
# UI contracts
# ---------------------------------------------------------------------------

def test_messaging_route_manifest_declares_messages_page() -> None:
    manifest = json.loads(_read_text(TEMPLATES / "ui" / "route_manifest.json"))
    page_ids = {p["id"] for p in manifest.get("pages") or []}
    assert "messages" in page_ids


def test_messaging_ui_component_exists() -> None:
    assert (TEMPLATES / "ui" / "pages" / "custom" / "Messages.jsx").exists()
    assert (TEMPLATES / "ui" / "components" / "MessagingProfileTab.jsx").exists()


# ---------------------------------------------------------------------------
# AppGenerator selection wiring
# ---------------------------------------------------------------------------

def test_appgenerator_selection_wiring_includes_messaging_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "messaging_pack" in by_id
    messaging = by_id["messaging_pack"]
    assert messaging["capability_kind"] == "operator_pack"
    assert "messaging.threads.list" in messaging["capabilities_provided"]

    routing = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_routing.yaml")
    packs = {
        pack["id"]: pack
        for pack in routing["layers"]["capability_pack"]["packs"]
    }
    assert "messaging" in packs
    assert packs["messaging"]["capability_kind"] == "operator_pack"


def test_messaging_pack_does_not_generate_announcement_or_contacts_module() -> None:
    """Announcements are hosted-only. Contacts belong to the social pack."""
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }
    assert not any(p.startswith("modules/messaging/") for p in generated_paths)
    assert not any(p.startswith("modules/chat/") for p in generated_paths)
    assert not any(p.startswith("modules/contacts/") for p in generated_paths)
    assert not any("announcement" in p for p in generated_paths)
