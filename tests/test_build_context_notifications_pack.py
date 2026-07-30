"""Contract tests for the notifications build context pack.

This pack is for EXTERNAL notification delivery (email, push, SMS) beyond the
runtime in-app notification record. It is a contract-first pack: all required
outputs are owner: generator (provider-neutral stubs produced at build time)
with no pre-built templates, because external delivery adapters are highly
provider-specific.

Verifies that:
- context.yaml registers the pack as generated_module for AppGenerator
- context.yaml declares 4 capabilities (email.send, push.send, preferences.read/write)
- No templates/ directory ships (all output is generator-produced)
- contract.yaml required_outputs are all generator-owned
- Forbidden outputs prevent namespace collision with the runtime in-app channel
- 6 runtime boundaries cover: no duplicate in-app channel, adapter pattern,
  preferences as user data, no credentials in adapters, reactions-not-service-calls,
  provider-neutral interface
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


def test_notifications_context_declares_no_facades_at_pack_level() -> None:
    """notification_settings is a generated module, not a managed facade."""
    context = _read_yaml(NOTIFICATIONS / "context.yaml")
    # facades field not expected at context level for generated_module packs
    assert "facades" not in context or not context.get("facades"), (
        "notifications context.yaml must not declare managed facades at pack level"
    )


# ---------------------------------------------------------------------------
# No templates directory
# ---------------------------------------------------------------------------


def test_notifications_pack_has_no_templates_directory() -> None:
    """External delivery adapters are provider-specific — no pre-built templates ship."""
    templates_dir = NOTIFICATIONS / "templates"
    assert not templates_dir.exists(), (
        "notifications pack must not ship a templates/ directory — "
        "all output is generator-produced with provider-neutral stubs customized per deployment"
    )


def test_notifications_context_only_declares_contract_asset() -> None:
    """Without templates, only the contract asset should be declared."""
    context = _read_yaml(NOTIFICATIONS / "context.yaml")
    asset_kinds = {asset["kind"] for asset in (context.get("assets") or [])}
    assert asset_kinds == {"contract"}, (
        "notifications context.yaml must only declare the contract asset (no templates)"
    )


# ---------------------------------------------------------------------------
# Contract structure
# ---------------------------------------------------------------------------


def test_notifications_contract_all_required_outputs_are_generator_owned() -> None:
    """External delivery adapters are provider-specific — no fixed templates."""
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")

    template_owned = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "templates"
    ]
    assert template_owned == [], (
        f"notifications pack must have no template-owned outputs — "
        f"all provider-specific adapters are generator-produced. Got: {template_owned}"
    )


def test_notifications_contract_required_outputs_include_preferences_module_and_adapters() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    output_paths = {output["path"] for output in contract["required_outputs"]}

    assert "modules/notification_settings/module.yaml" in output_paths, (
        "contract must declare notification_settings module — user preferences for external channels"
    )
    assert "services/adapters/notifications/email.py" in output_paths, (
        "contract must declare email adapter stub"
    )
    assert "services/adapters/notifications/push.py" in output_paths, (
        "contract must declare push adapter stub"
    )


def test_notifications_contract_declares_preferences_migration() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    output_paths = {output["path"] for output in contract["required_outputs"]}

    assert any("migration" in p and "notification" in p for p in output_paths), (
        "contract must declare a data migration for the notification preferences collection"
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
    """Delivery logic belongs in service adapters, not a separate module."""
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    forbidden_prefixes = {o.get("path_prefix") for o in (contract.get("forbidden_outputs") or [])}
    assert "modules/notification_delivery/" in forbidden_prefixes


def test_notifications_contract_forbids_wrong_adapter_paths() -> None:
    """Adapters must live under services/adapters/notifications/, not services/email/ or services/push/."""
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
    assert "no_duplicate_in_app_channel" in boundary_ids, (
        "runtime_boundaries must declare no_duplicate_in_app_channel — "
        "the runtime provides websocket and notification record; generated pack must not re-implement it"
    )


def test_notifications_contract_runtime_boundary_adapter_pattern() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "adapter_pattern_only" in boundary_ids, (
        "runtime_boundaries must declare adapter_pattern_only — "
        "provider SDK calls belong in services/adapters/notifications/, not in service.py"
    )


def test_notifications_contract_runtime_boundary_preferences_are_user_data() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "preferences_are_user_data" in boundary_ids, (
        "runtime_boundaries must declare preferences_are_user_data — "
        "opt-in preferences are user PII requiring account_data_handler.py"
    )


def test_notifications_contract_runtime_boundary_no_credentials() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "no_credentials_in_adapters" in boundary_ids, (
        "runtime_boundaries must declare no_credentials_in_adapters — "
        "API keys and secrets must come from env vars, never committed to source"
    )


def test_notifications_contract_runtime_boundary_reactions_not_service_calls() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "reactions_not_service_calls" in boundary_ids, (
        "runtime_boundaries must declare reactions_not_service_calls — "
        "external delivery is triggered by reactions observing domain events, not by service.py calls"
    )


def test_notifications_contract_runtime_boundary_provider_neutral_interface() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}
    assert "provider_neutral_interface" in boundary_ids, (
        "runtime_boundaries must declare provider_neutral_interface — "
        "adapters must accept provider-neutral args; SDK details are internal to the adapter"
    )


# ---------------------------------------------------------------------------
# Selection guidance
# ---------------------------------------------------------------------------


def test_notifications_contract_declares_avoid_when() -> None:
    """Pack must document when NOT to select it (in-app only → use contracts/notifications.yaml)."""
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    assert contract.get("avoid_when"), (
        "contract.yaml must declare avoid_when — "
        "important to distinguish from runtime in-app notifications which need no pack"
    )
    avoid_text = " ".join(str(a) for a in (contract.get("avoid_when") or []))
    assert "contracts/notifications.yaml" in avoid_text or "in-app" in avoid_text, (
        "avoid_when must explain that in-app alerts use contracts/notifications.yaml, not this pack"
    )


# ---------------------------------------------------------------------------
# Cross-pack integrations
# ---------------------------------------------------------------------------


def test_notifications_contract_declares_cross_pack_integrations() -> None:
    contract = _read_yaml(NOTIFICATIONS / "contract.yaml")
    integration_pack_ids = {
        i["pack_id"] for i in (contract.get("cross_pack_integrations") or [])
    }
    assert "messaging" in integration_pack_ids, (
        "contract must document integration with messaging — "
        "message_sent events can trigger push/email for offline participants"
    )
    assert "commerce" in integration_pack_ids, (
        "contract must document integration with commerce — "
        "order events are common transactional email triggers"
    )


# ---------------------------------------------------------------------------
# AppGenerator wiring
# ---------------------------------------------------------------------------


def test_appgenerator_capability_directory_wires_notifications_as_operator_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "notifications_pack" in by_id, "capability_directory must have 'notifications_pack' entry"
    entry = by_id["notifications_pack"]
    assert entry["capability_kind"] == "operator_pack"
    assert "notifications" in entry.get("domains", [])


def test_appgenerator_capability_directory_notifications_has_managed_platform_alternative() -> None:
    """Apps using Courier/Knock/Novu should use a managed_capability alternative."""
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    entry = by_id["notifications_pack"]
    alternatives = entry.get("alternatives") or []
    alt_kinds = {a.get("capability_kind") for a in alternatives}
    assert "managed_capability" in alt_kinds, (
        "notifications_pack directory entry must declare a managed_capability alternative "
        "for apps using a managed notification platform (Courier, Knock, Novu)"
    )
