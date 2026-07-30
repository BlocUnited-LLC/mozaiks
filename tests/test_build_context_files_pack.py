"""Contract tests for the files build context pack.

Verifies that:
- context.yaml registers the pack correctly for AppGenerator
- contract.yaml required_outputs all exist under templates; forbidden paths absent
- The files module declares expected actions and capabilities with alignment
- user_data_scope declared and account_data_handler.py ships (file records = user PII)
- Data migration declares the stable files.records collection alias
- Backend files compile and use canonical persistence pattern
- Service emits domain.files.* events
- Service does not deliver notifications directly
- Notification contract covers file_uploaded
- AppGenerator capability_directory has files_pack entry
- No modules/storage/, modules/media/, or modules/uploads/ generated
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
FILES = BUILD_CONTEXT / "files"
TEMPLATES = FILES / "templates"


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

def test_files_context_registers_active_appgenerator_pack() -> None:
    context = _read_yaml(FILES / "context.yaml")

    assert context["context_id"] == "files"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "files"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "generated_module"
    assert {asset["kind"] for asset in context["assets"]} == {"contract", "templates"}

    capability_ids = {cap["capability_id"] for cap in context["capabilities"]}
    assert capability_ids == {
        "files.upload",
        "files.read",
        "files.delete",
    }


# ---------------------------------------------------------------------------
# Required / forbidden outputs
# ---------------------------------------------------------------------------

def test_files_contract_required_outputs_exist_under_templates() -> None:
    contract = _read_yaml(FILES / "contract.yaml")

    assert contract["contract_id"] == "files"
    assert contract["contract_type"] == "build_pack_instructions"
    assert contract["facades"] == []

    missing = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "templates" and not (TEMPLATES / output["path"]).exists()
    ]
    assert missing == [], f"Missing required template outputs: {missing}"


def test_files_pack_forbidden_outputs_are_absent() -> None:
    contract = _read_yaml(FILES / "contract.yaml")

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
# Module: files
# ---------------------------------------------------------------------------

def test_files_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "files" / "module.yaml")

    assert _action_ids(module_yaml) == {
        "upload_file",
        "get_file",
        "list_files",
        "delete_file",
    }


def test_files_module_capabilities_target_existing_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "files" / "module.yaml")
    actions = _action_ids(module_yaml)
    targets = _capability_targets(module_yaml)

    assert targets <= actions, f"Capability targets not in actions: {targets - actions}"
    assert _capability_ids(module_yaml) == {
        "files.upload",
        "files.read",
        "files.delete",
    }


def test_files_module_declares_user_data_scope() -> None:
    """File records are user-owned PII — module must declare user_data_scope."""
    module_yaml = _read_yaml(TEMPLATES / "modules" / "files" / "module.yaml")
    assert module_yaml.get("module", {}).get("user_data_scope") is True, (
        "files module stores user-generated file records (PII) and must declare "
        "user_data_scope: true under the module: key"
    )


def test_files_module_ships_account_data_handler() -> None:
    """File records require GDPR delete + export support."""
    handler = TEMPLATES / "modules" / "files" / "backend" / "account_data_handler.py"
    assert handler.exists(), (
        "files module must ship account_data_handler.py — "
        "file records are user-owned PII"
    )
    source = handler.read_text(encoding="utf-8")
    assert "delete_user_data" in source
    assert "export_user_data" in source


def test_files_contract_declares_account_data_handler_as_required_output() -> None:
    contract = _read_yaml(FILES / "contract.yaml")
    output_paths = {output["path"] for output in contract["required_outputs"]}
    assert "modules/files/backend/account_data_handler.py" in output_paths, (
        "contract.yaml must declare account_data_handler.py as a required output "
        "since file records are user PII"
    )


# ---------------------------------------------------------------------------
# Data migration
# ---------------------------------------------------------------------------

def test_files_data_migration_declares_files_records_alias() -> None:
    migration_path = TEMPLATES / "data" / "migrations" / "001_files_collections.json"
    assert migration_path.exists()
    migration = json.loads(migration_path.read_text(encoding="utf-8"))

    aliases = {
        collection["data_alias"]
        for surface in migration.get("surfaces", [])
        for collection in surface.get("collections", [])
    }
    assert "files.records" in aliases, f"Expected files.records in aliases, got: {aliases}"


# ---------------------------------------------------------------------------
# Backend template contracts
# ---------------------------------------------------------------------------

def test_files_backend_templates_compile() -> None:
    backend_root = TEMPLATES / "modules" / "files" / "backend"
    for path in backend_root.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_files_backend_repo_uses_canonical_persistence_pattern() -> None:
    repo_text = _read_text(TEMPLATES / "modules" / "files" / "backend" / "repo.py")

    assert "persistence.collection" in repo_text or "_collection(ctx" in repo_text, (
        "files/repo.py must use canonical persistence.collection() pattern"
    )
    assert "get_mongo_client" not in repo_text, "repo.py must not use get_mongo_client"
    assert "ctx.db" not in repo_text, "repo.py must not use ctx.db"


def test_files_service_emits_domain_events() -> None:
    service_text = _read_text(TEMPLATES / "modules" / "files" / "backend" / "service.py")

    assert "domain.files.file_uploaded" in service_text, (
        "service.py must emit domain.files.file_uploaded"
    )
    assert "domain.files.file_deleted" in service_text, (
        "service.py must emit domain.files.file_deleted"
    )


def test_files_service_does_not_deliver_notifications_directly() -> None:
    """Notification delivery is declared in notifications.yaml, not implemented in service.py."""
    service_text = _read_text(TEMPLATES / "modules" / "files" / "backend" / "service.py")

    assert "notification_client" not in service_text
    assert "send_notification" not in service_text
    assert "push_notification" not in service_text


# ---------------------------------------------------------------------------
# Event / notification contracts
# ---------------------------------------------------------------------------

def test_files_declares_canonical_domain_events() -> None:
    events = _read_yaml(
        TEMPLATES / "modules" / "files" / "contracts" / "events.yaml"
    )
    event_types = {e["type"] for e in events.get("events") or []}

    assert "domain.files.file_uploaded" in event_types
    assert "domain.files.file_deleted" in event_types


def test_files_notification_covers_file_uploaded() -> None:
    notifications = _read_yaml(
        TEMPLATES / "modules" / "files" / "contracts" / "notifications.yaml"
    )
    assert notifications.get("schema_version") == "mozaiks.notifications.v1"
    notif_event_types = {n["event_type"] for n in notifications.get("notifications") or []}
    assert "domain.files.file_uploaded" in notif_event_types


# ---------------------------------------------------------------------------
# AppGenerator selection wiring
# ---------------------------------------------------------------------------

def test_appgenerator_capability_directory_has_files_pack_entry() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "files_pack" in by_id, "capability_directory.yaml must have a files_pack entry"
    files_entry = by_id["files_pack"]
    assert files_entry["capability_kind"] == "operator_pack"
    assert "files.upload" in files_entry["capabilities_provided"]
    assert "files.read" in files_entry["capabilities_provided"]
    assert "files.delete" in files_entry["capabilities_provided"]


# ---------------------------------------------------------------------------
# No wrong module names generated
# ---------------------------------------------------------------------------

def test_files_pack_does_not_generate_wrong_module_names() -> None:
    """Module must be named 'files', not storage, media, or uploads."""
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }
    assert not any(p.startswith("modules/storage/") for p in generated_paths), (
        "modules/storage/ is a forbidden output — module must be named 'files'"
    )
    assert not any(p.startswith("modules/media/") for p in generated_paths), (
        "modules/media/ is a forbidden output — module must be named 'files'"
    )
    assert not any(p.startswith("modules/uploads/") for p in generated_paths), (
        "modules/uploads/ is a forbidden output — module must be named 'files'"
    )
