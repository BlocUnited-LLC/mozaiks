"""Contract tests for the entitlement_dispatch build context pack.

Verifies that:
- context.yaml registers the pack correctly for AppGenerator as generated_module
- The pack declares no user-facing capabilities (internal write-path only)
- contract.yaml required_outputs all exist under templates; forbidden paths absent
- managed_assignment_writer_contract declares the selection mechanism
- The entitlement_dispatch module declares exactly activate_subscription and
  deactivate_subscription with no entitlement_gate, no permissions, no capabilities
- Module type is entitlement_dispatch and visibility is private
- Data migration declares billing.subscriptions as the only collection alias
- Backend files compile
- repo.py writes to the billing.subscriptions data alias, not ctx.db or raw Motor
- service.py does not call payment providers, check entitlements, or emit billing events
- module_archetypes.yaml names the entitlement_dispatch archetype
- No modules/billing/, modules/subscriptions/, services/billing/ paths generated
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
ENTITLEMENT_DISPATCH = BUILD_CONTEXT / "entitlement_dispatch"
TEMPLATES = ENTITLEMENT_DISPATCH / "templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _action_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {action["id"] for action in module_yaml.get("actions") or []}


# ---------------------------------------------------------------------------
# Pack registration
# ---------------------------------------------------------------------------


def test_entitlement_dispatch_context_registers_active_appgenerator_pack() -> None:
    context = _read_yaml(ENTITLEMENT_DISPATCH / "context.yaml")

    assert context["context_id"] == "entitlement_dispatch"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "entitlement_dispatch"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "generated_module"


def test_entitlement_dispatch_context_declares_no_user_capabilities() -> None:
    """entitlement_dispatch is an internal write-path module; it must not expose user capabilities."""
    context = _read_yaml(ENTITLEMENT_DISPATCH / "context.yaml")
    capabilities = context.get("capabilities") or []
    assert capabilities == [], (
        "entitlement_dispatch is internal — it must declare no user-facing capabilities in context.yaml"
    )


def test_entitlement_dispatch_context_declares_no_facades() -> None:
    context = _read_yaml(ENTITLEMENT_DISPATCH / "context.yaml")
    facades = context.get("facades") or []
    assert facades == [], (
        "entitlement_dispatch is an internal write-path module and must declare no facades"
    )


# ---------------------------------------------------------------------------
# Required / forbidden outputs
# ---------------------------------------------------------------------------


def test_entitlement_dispatch_contract_required_outputs_exist_under_templates() -> None:
    contract = _read_yaml(ENTITLEMENT_DISPATCH / "contract.yaml")

    assert contract["contract_id"] == "entitlement_dispatch"
    assert contract["contract_type"] == "build_pack_instructions"

    missing = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "templates" and not (TEMPLATES / output["path"]).exists()
    ]
    assert missing == [], f"Missing required template outputs: {missing}"


def test_entitlement_dispatch_pack_forbidden_outputs_are_absent() -> None:
    contract = _read_yaml(ENTITLEMENT_DISPATCH / "contract.yaml")

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


def test_entitlement_dispatch_contract_declares_managed_assignment_writer_contract() -> None:
    """The contract must explain why and when entitlement_dispatch is selected vs. managed pack."""
    contract = _read_yaml(ENTITLEMENT_DISPATCH / "contract.yaml")
    writer_contract = contract.get("managed_assignment_writer_contract", {})

    assert writer_contract, "contract.yaml must declare managed_assignment_writer_contract"
    # Must explain that managed capability packs own the write path when present
    selection_text = str(writer_contract.get("selection_mechanism", "")).lower()
    assert "managed" in selection_text or "provides_capabilities" in selection_text, (
        "managed_assignment_writer_contract must explain the managed-pack selection mechanism"
    )


# ---------------------------------------------------------------------------
# Module: entitlement_dispatch
# ---------------------------------------------------------------------------


def test_entitlement_dispatch_module_declares_exactly_two_actions() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "entitlement_dispatch" / "module.yaml"
    )

    assert _action_ids(module_yaml) == {
        "activate_subscription",
        "deactivate_subscription",
    }, "entitlement_dispatch must declare exactly activate_subscription and deactivate_subscription"


def test_entitlement_dispatch_module_has_no_capabilities() -> None:
    """This module is internal — it must not publish any user-facing capabilities."""
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "entitlement_dispatch" / "module.yaml"
    )
    capabilities = module_yaml.get("capabilities") or []
    assert capabilities == [], (
        "entitlement_dispatch module must declare no capabilities — it is internal-only"
    )


def test_entitlement_dispatch_module_has_no_permissions() -> None:
    """Internal write-path module — no permission declarations, no entitlement_gate."""
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "entitlement_dispatch" / "module.yaml"
    )
    permissions = module_yaml.get("permissions") or []
    assert permissions == [], (
        "entitlement_dispatch module must declare no permissions — actions are called by billing hooks, not by end users"
    )


def test_entitlement_dispatch_module_actions_have_no_entitlement_gate() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "entitlement_dispatch" / "module.yaml"
    )
    for action in (module_yaml.get("actions") or []):
        assert "entitlement_gate" not in action, (
            f"action '{action['id']}' must not declare entitlement_gate — "
            "this module writes entitlement state; gating it would create a bootstrap deadlock"
        )


def test_entitlement_dispatch_module_type_and_visibility() -> None:
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "entitlement_dispatch" / "module.yaml"
    )
    module = module_yaml.get("module", {})
    assert module.get("type") == "entitlement_dispatch", (
        "module.yaml must declare type: entitlement_dispatch"
    )
    assert module.get("visibility") == "private", (
        "module.yaml must declare visibility: private"
    )


def test_entitlement_dispatch_module_has_no_user_data_scope() -> None:
    """Subscription records are operational state, not personal PII that requires GDPR export."""
    module_yaml = _read_yaml(
        TEMPLATES / "modules" / "entitlement_dispatch" / "module.yaml"
    )
    assert not module_yaml.get("module", {}).get("user_data_scope"), (
        "entitlement_dispatch module stores operational state, not user-owned PII — "
        "must not declare user_data_scope"
    )


# ---------------------------------------------------------------------------
# Data migration
# ---------------------------------------------------------------------------


def test_entitlement_dispatch_migration_declares_billing_subscriptions_alias() -> None:
    """The migration must declare billing.subscriptions — the alias ConfiguredEntitlementAdapter reads."""
    migration_path = (
        TEMPLATES / "data" / "migrations" / "001_entitlement_dispatch_collections.json"
    )
    assert migration_path.exists()
    migration = json.loads(migration_path.read_text(encoding="utf-8"))

    aliases = {
        collection["data_alias"]
        for surface in migration.get("surfaces", [])
        for collection in surface.get("collections", [])
    }
    assert "billing.subscriptions" in aliases, (
        "Migration must declare billing.subscriptions — "
        "ConfiguredEntitlementAdapter and the repo write side must resolve to the same collection"
    )


def test_entitlement_dispatch_migration_declares_only_one_alias() -> None:
    """entitlement_dispatch owns a single collection — billing.subscriptions."""
    migration_path = (
        TEMPLATES / "data" / "migrations" / "001_entitlement_dispatch_collections.json"
    )
    migration = json.loads(migration_path.read_text(encoding="utf-8"))

    aliases = [
        collection["data_alias"]
        for surface in migration.get("surfaces", [])
        for collection in surface.get("collections", [])
    ]
    assert len(aliases) == 1, (
        f"entitlement_dispatch migration must declare exactly 1 collection alias, got: {aliases}"
    )


# ---------------------------------------------------------------------------
# Backend template contracts
# ---------------------------------------------------------------------------


def test_entitlement_dispatch_backend_templates_compile() -> None:
    backend_root = TEMPLATES / "modules" / "entitlement_dispatch" / "backend"
    for path in backend_root.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_entitlement_dispatch_repo_writes_to_billing_subscriptions_alias() -> None:
    """repo.py must use the billing.subscriptions data alias, not ctx.db or a raw collection name."""
    repo_text = _read_text(
        TEMPLATES / "modules" / "entitlement_dispatch" / "backend" / "repo.py"
    )

    assert "billing.subscriptions" in repo_text, (
        "repo.py must reference the billing.subscriptions data alias explicitly"
    )
    assert "ctx.db" not in repo_text, (
        "repo.py must not use ctx.db — it is non-canonical"
    )


def test_entitlement_dispatch_repo_does_not_use_raw_motor_connection() -> None:
    """repo.py must not open its own MongoDB connection — it uses a context-scoped persistence handle."""
    repo_text = _read_text(
        TEMPLATES / "modules" / "entitlement_dispatch" / "backend" / "repo.py"
    )
    assert "AsyncIOMotorClient" not in repo_text, (
        "repo.py must not create a raw Motor client — use a context-scoped persistence handle"
    )
    assert "get_mongo_client" not in repo_text


def test_entitlement_dispatch_service_does_not_call_payment_providers() -> None:
    """service.py is a write-path coordinator only — no payment provider calls, no entitlement reads."""
    service_text = _read_text(
        TEMPLATES / "modules" / "entitlement_dispatch" / "backend" / "service.py"
    )

    forbidden = ["stripe", "mozaikspay", "checkout", "invoice", "webhook", "EntitlementPort"]
    for pattern in forbidden:
        assert pattern.lower() not in service_text.lower(), (
            f"service.py must not reference '{pattern}' — "
            "entitlement_dispatch writes assignment records; billing provider logic belongs elsewhere"
        )


def test_entitlement_dispatch_service_does_not_emit_billing_events() -> None:
    """service.py must not emit billing domain events — it only writes subscription records."""
    service_text = _read_text(
        TEMPLATES / "modules" / "entitlement_dispatch" / "backend" / "service.py"
    )
    assert "domain.billing" not in service_text
    assert "ctx.emit" not in service_text or "domain.billing" not in service_text, (
        "service.py must not emit billing domain events directly"
    )


# ---------------------------------------------------------------------------
# AppGenerator / archetype wiring
# ---------------------------------------------------------------------------


def test_entitlement_dispatch_archetype_declared_in_module_archetypes() -> None:
    archetypes_path = BUILD_CONTEXT / "AppGenerator" / "module_archetypes.yaml"
    archetypes_text = _read_text(archetypes_path)

    assert "entitlement_dispatch:" in archetypes_text, (
        "module_archetypes.yaml must declare the entitlement_dispatch archetype"
    )


def test_entitlement_dispatch_archetype_selects_for_self_hosted_saas() -> None:
    archetypes_path = BUILD_CONTEXT / "AppGenerator" / "module_archetypes.yaml"
    archetypes_text = _read_text(archetypes_path)

    assert "self-hosted" in archetypes_text or "Self-hosted" in archetypes_text, (
        "entitlement_dispatch archetype must document self-hosted selection context"
    )


# ---------------------------------------------------------------------------
# No forbidden module paths
# ---------------------------------------------------------------------------


def test_entitlement_dispatch_pack_does_not_generate_forbidden_module_paths() -> None:
    """No billing module, no subscriptions module, no billing service."""
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }
    assert not any(p.startswith("modules/billing/") for p in generated_paths)
    assert not any(p.startswith("modules/subscriptions/") for p in generated_paths)
    assert not any(p.startswith("services/billing/") for p in generated_paths)
    assert not any(p.startswith("services/adapters/payments/") for p in generated_paths)
    assert not any(p.startswith("app/capability_packs/") for p in generated_paths)
