"""Contract tests for the notifications build context pack.

This pack is for EXTERNAL notification delivery (email, push, SMS) beyond the
runtime in-app notification record.

Verifies that:
- context.yaml registers the pack as generated_module for AppGenerator
- context.yaml declares 4 capabilities (email.send, push.send, preferences.read/write)
- context.yaml declares both contract and templates assets
- templates/ directory ships with the pack
- contract.yaml required_outputs are templates or workspace owned (no generator-owned)
- module.yaml: 2 actions, user_data_scope: true, no entitlement_gate
- base_handler.py has DO NOT EDIT header and NotificationSettingsBaseHandler
- handler.py subclasses base and is workspace-owned
- account_data_handler.py: delete_user_data and export_user_data
- repo.py uses ctx.persistence, not ctx.db or raw Motor
- service.py does not call adapter functions directly
- email.py and push.py declare provider-neutral interfaces
- No credentials hardcoded in adapter files
- data migration declares notifications.preferences alias
- reactions.yaml has correct schema_version
- Forbidden outputs prevent namespace collision with runtime in-app channel
- 6 runtime boundaries
- avoid_when is documented
- Cross-pack integrations declared (messaging, social, commerce)
- AppGenerator capability_directory has notifications_pack entry
- capability_directory entry has an alternative for managed notification platforms
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
NOTIFICATIONS = BUILD_CONTEXT / "notifications"
TEMPLATES = NOTIFICATIONS / "templates"
MODULE = TEMPLATES / "modules" / "notification_settings"
ADAPTERS = TEMPLATES / "services" / "adapters" / "notifications"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Pack registration
# ---------------------------------------------------------------------------


def test_notifications_context_registers_active_appgenerator_pack() -> None:
    context = _read_yaml(NOTIFICATIONS / "context.yaml")

    assert context["context_id"] == "notifications"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "notifications"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "generated_module"


def test_notifications_context_declares_four_capabilities() -> None:
    context = _read_yaml(NOTIFICATIONS / "context.yaml")
    capability_ids = {cap["capability_id"] for cap in (context.get("capabilities") or [])}

    assert capability_ids == {
        "notifications.email.send",
        "notifications.push.send",
        "notifications.preferences.read",
        "notifications.preferences.write",
    }


def test_notifications_context_capabilities_recommend_notification_settings_facade() -> None:
    context = _read_yaml(NOTIFICATIONS / "context.yaml")
    for cap in (context.get("capabilities") or []):
        assert cap.get("facade_recommended") == "notification_settings", (
            f"capability {cap['capability_id']} must recommend notification_settings facade"
        )


def test_notifications_context_declares_contract_and_templates_assets() -> None:
    context = _read_yaml(NOTIFICATIONS / "context.yaml")
    asset_kinds = {asset["kind"] for asset in (context.get("assets") or [])}
    assert "contract" in asset_kinds
    assert "templates" in asset_kinds, (
        "notifications context.yaml must declare templates/ asset now that the pack ships templates"
    )


# ---------------------------------------------------------------------------
# Templates directory
# ---------------------------------------------------------------------------


def test_notifications_pack_has_templates_directory() -> None:
    assert TEMPLATES.exists(), "notifications pack must ship a templates/ directory"


def test_notifications_templates_has_notification_settings_module() -> None:
    assert MODULE.is_dir(), "templates must have modules/notification_settings/"


def test_notifications_templates_has_adapter_stubs() -> None:
    assert (ADAPTERS / "email.py").exists(), "templates must have services/adapters/notifications/email.py"
    assert (ADAPTERS / "push.py").exists(), "templates must have services/adapters/notifications/push.py"


def test_notifications_templates_has_data_migration() -> None:
    migrations = list((TEMPLATES / "data" / "migrations").glob("*.json"))
    assert len(migrations) >= 1, "templates must have at least one data migration"


# ---------------------------------------------------------------------------
# Contract structure
# ---------------------------------------------------------------------------


def test_notifications_contract_no_generator_owned_required_outputs() -> None:
    """All outputs are now templates or workspace owned — no generator-only outputs remain."""
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")

    generator_owned = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "generator"
    ]
    assert generator_owned == [], (
        f"notifications pack now ships templates — all outputs must be owner: templates or owner: workspace. "
        f"Found generator-owned: {generator_owned}"
    )


def test_notifications_contract_required_outputs_include_preferences_module_and_adapters() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    output_paths = {output["path"] for output in contract["required_outputs"]}

    assert "modules/notification_settings/module.yaml" in output_paths
    assert "services/adapters/notifications/email.py" in output_paths
    assert "services/adapters/notifications/push.py" in output_paths


def test_notifications_contract_handler_is_workspace_owned() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    handler = next(
        (o for o in contract["required_outputs"] if o["path"] == "modules/notification_settings/backend/handler.py"),
        None,
    )
    assert handler is not None, "contract must declare handler.py as a required output"
    assert handler.get("owner") == "workspace", (
        "handler.py must be owner: workspace — preserved across regeneration"
    )


def test_notifications_contract_declares_preferences_migration() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    output_paths = {output["path"] for output in contract["required_outputs"]}

    assert any("migration" in p and "notification" in p for p in output_paths), (
        "contract must declare a data migration for the notification preferences collection"
    )


def test_notifications_contract_module_output_notes_require_user_data_scope() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    module_output = next(
        (o for o in contract["required_outputs"] if o["path"] == "modules/notification_settings/module.yaml"),
        None,
    )
    assert module_output is not None
    notes = (module_output.get("notes") or "").lower()
    assert "user_data_scope" in notes, (
        "module.yaml output notes must mention user_data_scope — preferences are user PII"
    )


# ---------------------------------------------------------------------------
# module.yaml
# ---------------------------------------------------------------------------


def test_notification_settings_module_yaml_has_user_data_scope() -> None:
    module_yaml = _read_yaml(MODULE / "module.yaml")
    module_block = module_yaml.get("module") or {}
    assert module_block.get("user_data_scope") is True, (
        "notification_settings module.yaml must set user_data_scope: true under module: key — "
        "opt-in preferences are user-owned PII"
    )


def test_notification_settings_module_yaml_has_two_preference_actions() -> None:
    module_yaml = _read_yaml(MODULE / "module.yaml")
    action_ids = {a["id"] for a in (module_yaml.get("actions") or [])}

    assert "get_notification_preferences" in action_ids
    assert "update_notification_preferences" in action_ids
    assert len(action_ids) == 2, (
        f"notification_settings must have exactly 2 actions. Got: {action_ids}"
    )


def test_notification_settings_module_yaml_no_entitlement_gate() -> None:
    module_yaml = _read_yaml(MODULE / "module.yaml")
    for action in (module_yaml.get("actions") or []):
        assert "entitlement_gate" not in action, (
            f"action {action['id']} must not have entitlement_gate — "
            "all users may manage their own notification preferences"
        )


def test_notification_settings_module_yaml_declares_capabilities() -> None:
    module_yaml = _read_yaml(MODULE / "module.yaml")
    cap_ids = {c["capability_id"] for c in (module_yaml.get("capabilities") or [])}
    assert "notifications.preferences.read" in cap_ids
    assert "notifications.preferences.write" in cap_ids


# ---------------------------------------------------------------------------
# Backend files
# ---------------------------------------------------------------------------


def test_notification_settings_base_handler_has_do_not_edit_header() -> None:
    base = _read_text(MODULE / "backend" / "base_handler.py")
    assert "DO NOT EDIT" in base, (
        "base_handler.py must have a DO NOT EDIT header — "
        "it is regenerated; overrides belong in handler.py"
    )
    assert "NotificationSettingsBaseHandler" in base


def test_notification_settings_handler_subclasses_base() -> None:
    handler = _read_text(MODULE / "backend" / "handler.py")
    assert "NotificationSettingsBaseHandler" in handler, (
        "handler.py must import and subclass NotificationSettingsBaseHandler"
    )


def test_notification_settings_account_data_handler_has_gdpr_methods() -> None:
    adh = _read_text(MODULE / "backend" / "account_data_handler.py")
    assert "delete_user_data" in adh, "account_data_handler.py must implement delete_user_data"
    assert "export_user_data" in adh, "account_data_handler.py must implement export_user_data"


def test_notification_settings_repo_uses_ctx_persistence() -> None:
    repo = _read_text(MODULE / "backend" / "repo.py")
    assert "ctx.persistence" in repo or "persistence.collection" in repo, (
        "repo.py must use ctx.persistence.collection() — not ctx.db or raw Motor"
    )
    assert "ctx.db" not in repo, "repo.py must not use ctx.db"
    assert "get_mongo_client" not in repo, "repo.py must not call get_mongo_client"


def test_notification_settings_service_does_not_call_adapters_directly() -> None:
    service = _read_text(MODULE / "backend" / "service.py")
    assert "send_email" not in service, (
        "service.py must not call send_email directly — delivery is triggered by reactions"
    )
    assert "send_push" not in service, (
        "service.py must not call send_push directly — delivery is triggered by reactions"
    )


# ---------------------------------------------------------------------------
# Adapter stubs
# ---------------------------------------------------------------------------


def test_email_adapter_declares_send_email_interface() -> None:
    email = _read_text(ADAPTERS / "email.py")
    assert "def send_email" in email or "async def send_email" in email, (
        "email.py must declare send_email function"
    )
    assert "to" in email and "subject" in email and "body" in email, (
        "send_email must accept provider-neutral args: to, subject, body"
    )


def test_push_adapter_declares_send_push_interface() -> None:
    push = _read_text(ADAPTERS / "push.py")
    assert "def send_push" in push or "async def send_push" in push, (
        "push.py must declare send_push function"
    )
    assert "token" in push and "title" in push and "body" in push, (
        "send_push must accept provider-neutral args: token, title, body"
    )


def test_adapters_do_not_hardcode_credentials() -> None:
    """API keys and secrets must come from env vars, never committed."""
    for adapter_file in [ADAPTERS / "email.py", ADAPTERS / "push.py"]:
        text = _read_text(adapter_file)
        # Should reference env vars, not raw secrets
        assert "os.environ" in text or "os.getenv" in text or "environ.get" in text, (
            f"{adapter_file.name} must load credentials from environment variables"
        )
        # Should not contain string patterns that look like hardcoded API keys
        assert "sk-" not in text, f"{adapter_file.name} must not contain hardcoded API keys"
        assert "SG." not in text, f"{adapter_file.name} must not contain hardcoded SendGrid keys"


def test_adapters_have_not_implemented_stub() -> None:
    """Adapter stubs must raise NotImplementedError so operators know they need to fill in the body."""
    for adapter_file in [ADAPTERS / "email.py", ADAPTERS / "push.py"]:
        text = _read_text(adapter_file)
        assert "NotImplementedError" in text, (
            f"{adapter_file.name} must raise NotImplementedError — "
            "it is a stub; operators fill in their provider SDK"
        )


# ---------------------------------------------------------------------------
# Reactions contract
# ---------------------------------------------------------------------------


def test_notification_settings_reactions_yaml_has_correct_schema() -> None:
    reactions = _read_yaml(MODULE / "contracts" / "reactions.yaml")
    assert reactions.get("schema_version") == "mozaiks.reactions.v1"
    assert reactions.get("module_id") == "notification_settings"
    assert "reactions" in reactions


# ---------------------------------------------------------------------------
# Data migration
# ---------------------------------------------------------------------------


def test_notification_preferences_migration_declares_alias() -> None:
    migrations_dir = TEMPLATES / "data" / "migrations"
    migration_files = list(migrations_dir.glob("*.json"))
    assert migration_files, "templates must include at least one data migration"

    import json
    for mf in migration_files:
        migration = json.loads(mf.read_text(encoding="utf-8"))
        aliases = migration.get("aliases", [])
        alias_names = [a.get("alias", "") for a in aliases]
        if any("notifications.preferences" in a for a in alias_names):
            return  # Found the expected alias

    raise AssertionError(
        "A migration file must declare the 'notifications.preferences' data alias"
    )


# ---------------------------------------------------------------------------
# Forbidden outputs
# ---------------------------------------------------------------------------


def test_notifications_contract_forbids_wrong_module_name() -> None:
    """Module must be named notification_settings, not notifications (runtime namespace collision)."""
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    forbidden_prefixes = {o.get("path_prefix") for o in (contract.get("forbidden_outputs") or [])}
    assert "modules/notifications/" in forbidden_prefixes, (
        "contract must forbid modules/notifications/ — runtime owns this namespace for in-app delivery"
    )


def test_notifications_contract_forbids_delivery_module() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    forbidden_prefixes = {o.get("path_prefix") for o in (contract.get("forbidden_outputs") or [])}
    assert "modules/notification_delivery/" in forbidden_prefixes


def test_notifications_contract_forbids_wrong_adapter_paths() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    forbidden_prefixes = {o.get("path_prefix") for o in (contract.get("forbidden_outputs") or [])}
    assert "services/email/" in forbidden_prefixes
    assert "services/push/" in forbidden_prefixes


# ---------------------------------------------------------------------------
# Runtime boundaries
# ---------------------------------------------------------------------------


def test_notifications_contract_runtime_boundary_no_duplicate_in_app_channel() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "no_duplicate_in_app_channel" in boundary_ids


def test_notifications_contract_runtime_boundary_adapter_pattern() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "adapter_pattern_only" in boundary_ids


def test_notifications_contract_runtime_boundary_preferences_are_user_data() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "preferences_are_user_data" in boundary_ids


def test_notifications_contract_runtime_boundary_no_credentials() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "no_credentials_in_adapters" in boundary_ids


def test_notifications_contract_runtime_boundary_reactions_not_service_calls() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "reactions_not_service_calls" in boundary_ids


def test_notifications_contract_runtime_boundary_provider_neutral_interface() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "provider_neutral_interface" in boundary_ids


# ---------------------------------------------------------------------------
# Selection guidance
# ---------------------------------------------------------------------------


def test_notifications_contract_declares_avoid_when() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    assert contract.get("avoid_when"), "contract.yaml must declare avoid_when"
    avoid_text = " ".join(str(a) for a in (contract.get("avoid_when") or []))
    assert "contracts/notifications.yaml" in avoid_text or "in-app" in avoid_text


# ---------------------------------------------------------------------------
# Cross-pack integrations
# ---------------------------------------------------------------------------


def test_notifications_contract_declares_cross_pack_integrations() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    integration_pack_ids = {
        i["pack_id"] for i in (contract.get("cross_pack_integrations") or [])
    }
    assert "messaging" in integration_pack_ids
    assert "commerce" in integration_pack_ids


# ---------------------------------------------------------------------------
# AppGenerator wiring
# ---------------------------------------------------------------------------


def test_appgenerator_capability_directory_wires_notifications_as_operator_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "notifications_pack" in by_id
    entry = by_id["notifications_pack"]
    assert entry["capability_kind"] == "operator_pack"
    assert "notifications" in entry.get("domains", [])


def test_appgenerator_capability_directory_notifications_has_managed_platform_alternative() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    entry = by_id["notifications_pack"]
    alternatives = entry.get("alternatives") or []
    alt_kinds = {a.get("capability_kind") for a in alternatives}
    assert "managed_capability" in alt_kinds
