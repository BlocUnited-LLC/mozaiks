"""Exercise deterministic assembly and runtime wiring for each shipped pack.

For each factory build context with a ``pack:`` block, the pack, with the packs
its contract requires, is materialized by the real AppGenerator assembler with
no model. It is assembled twice and must come out byte-identical. The bundle
must pass every deterministic check of the product's own app-bundle acceptance
gate, then boot and serve its data actions as two signed-in people in the
runtime smoke's trusted-fixture child path against a real MongoDB. Finally
every action the bundle declares is called once (tests/pack_proof_action_sweep.py)
and must answer without a server error or a rejected event.

Contained runtime checks are excluded here; this suite does not prove Docker
containment. The trusted-fixture child runs repository-owned code. PLAN_INPUTS
and APP_PERMISSIONS declare this test app's build and authorization inputs
before assembly. They are not inferred from emitted candidate files. Business
4xx responses in the action sweep establish HTTP wiring, not successful business
execution. Missing grants and unobserved role-holder execution remain failures.
Managed Cloud and Pay facades use explicit loopback HTTP fixtures with synthetic
credentials and a connector-absence IO double. Their receipts prove OSS client
wiring and role enforcement, not real hosted-provider acceptance.
Cloud and Pay Factory bundles are reviewed with no custom facade grants. Their
role-holder runtime probes use separately authored test apps validated by the
canonical authored-app validator, with byte-identical verified implementations.
Those authored probes are not proof of Factory-generated facade role grants.
A failure that needs an owner's decision is listed, with its reason, in
BLOCKED_PACKS or KNOWN_RUNTIME_FAILURES; both lists fail when an entry stops
failing, so they can only shrink.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from factory_app.workflows.AppGenerator.tools.app_runtime_smoke import run_app_runtime_smoke
from factory_app.workflows.AppGenerator.tools.app_validation import (
    _write_files_to_dir,
    run_app_bundle_acceptance_gate,
)
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _resolve_scan_packs,
    _resolved_pack_context,
    _resolved_pack_rows,
)
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_managed_capability_templates,
    verified_pack_data_contracts,
    verified_pack_template_inventory,
    verify_pack_integrity,
)
from mozaiksai.core.validation.workspace import (
    registered_ui_components,
    validate_authored_app_file_map,
)
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from mozaiksai.core.workflow.generator_support.module_role_grants import verified_pack_authorization
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index_from_context,
)
from mozaiksai.core.workflow.outcome_feedback import WorkflowFeedbackEvidence
from mozaiksai.version import __version__ as FRAMEWORK_VERSION
from tests.appgenerator_subscription_fixture import subscription_contract
from tests.content_smoke_fixtures import content_smoke_fixtures
from tests.factory_context import factory_context
from tests.pack_proof_action_sweep import _bad_params, run_action_sweep
from tests.pack_proof_provider_fixture import PACKS as PROVIDER_FIXTURE_PACKS
from tests.pack_proof_provider_fixture import run_provider_probe
from tests.reviewed_pack_fixture import reviewed_pack_fixture
from tests.test_app_runtime_smoke import (
    mongo,  # noqa: F401 - real MongoDB and its leaked-database guard
)

BUILD_CONTEXT = Path(__file__).resolve().parents[1] / "factory_app" / "build_context"
BUILD_TIMESTAMP = "2026-01-01T00:00:00+00:00"
EMPTY_DATA_CONTRACT: dict[str, Any] = {"version": "1", "surfaces": [], "shared_collections": []}
PROOF_ROLE = "pack_proof_operator"
# Gate checks requiring a separately qualified contained runtime image.
CONTAINED_GATE_CHECKS = frozenset({"app_runtime_load", "app_runtime_load_worker", "app_runtime_smoke"})
# planned_completeness compares the bundle with the AppGenerator plan's build tasks.
# A pack built alone has no planned tasks, so the one diagnostic it may report is that.
NO_PLANNED_TASKS = "Factory generation requires approved app_build_plan.build_tasks"


def _shipped_packs() -> list[str]:
    return sorted(
        path.parent.name for path in BUILD_CONTEXT.glob("*/context.yaml")
        if isinstance((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("pack"), dict)
    )


SHIPPED_PACKS = _shipped_packs()

# A pack listed here cannot be built without a model: say why. Its contract
# still has to verify (test_every_shipped_pack_verifies).
NOT_MATERIALIZABLE_ALONE = {
    "onboarding": "Every required output is generator-owned and the pack ships no templates.",
}

# A pack listed here fails before it can run, for a reason that needs an owner's
# decision rather than a pack fix: (the failure's own message text, why). The proof
# must fail with exactly that message, and fails once it passes, so the entry is removed.
BLOCKED_PACKS: dict[str, tuple[str, str]] = {}

_SUPPORT_CROSS_MODULE_EMIT = (
    "create_support_request creates its thread and first message by calling the messages "
    "service inside its own action, so the runtime attributes the messages events to support, "
    "which cannot declare another module's events. They are dropped: no reaction or "
    "notification runs for a support conversation."
)

# Runtime failures (smoke or action sweep) a pack is known to have, by check id,
# with the reason. The proof fails when one stops failing, so this list only shrinks.
KNOWN_RUNTIME_FAILURES: dict[str, dict[str, str]] = {
    "social": {
        "crud.user_posts.posts.a_delete": (
            "delete_post has the canonical delete name but soft-deletes (status deleted, record "
            "kept). The data contract cannot declare a soft-delete lifecycle, so the smoke's "
            "canonical delete check expects the record gone."
        ),
    },
    "support": {
        f"event.support.create_support_request.domain.messages.{event}": _SUPPORT_CROSS_MODULE_EMIT
        for event in ("thread_created", "message_sent")
    },
}


def _feedback_records_contract() -> dict[str, Any]:
    """The approved plan declares outcome_feedback.records (the pack's app_owned_records rule)."""
    fields = [{"name": "feedback_id", "type": "string", "required": True}]
    for name, field in WorkflowFeedbackEvidence.model_fields.items():
        nested = isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
        fields.append({"name": name, "type": "object" if nested else "string", "required": True})
    return {**EMPTY_DATA_CONTRACT, "surfaces": [{
        "surface_id": "outcome_feedback",
        "surface_kind": "module",
        "collections": [{
            "name": "records", "scope": "app", "entity": "WorkflowFeedback", "tenancy": "per_user",
            "owner_field": "user_id", "fields": fields, "lifecycle": {"write_mode": "workflow_write"},
            "ownership": {"surface_id": "outcome_feedback", "surface_kind": "module"},
        }],
    }]}


def _plan_catalog_contract() -> dict[str, Any]:
    """An approved subscription contract with a labelled plan catalog and no gated actions."""
    contract = subscription_contract("billing_portal")
    contract["subscription_config_file"]["label"] = "Pack proof plans"
    return contract


# What a real build's approved plan supplies to the assembler for each pack.
PLAN_INPUTS: dict[str, dict[str, Any]] = {
    "content": {"code_files": [{
        "filename": "modules/content/contracts/fixtures.yaml",
        "content": content_smoke_fixtures(PROOF_ROLE),
    }]},
    "operator_readiness": {
        "readiness_profile": "host_operator_platform",
        "evidence_mode": "local",
        "evidence_ledger_path": "docs/operations/evidence-log.json",
        "launch_check_command": "python scripts/check_launch_readiness.py --json",
        "local_launcher": "scripts/check_operator_readiness_local.ps1",
        "monetization_check_command": "python scripts/check_monetization_readiness.py --json",
    },
    "mozaikspay": {"subscription_contract": _plan_catalog_contract()},
    "outcome_feedback": {
        "data_contract": _feedback_records_contract(),
        "primary_entities": {"outcome_feedback": ["WorkflowFeedback"]},
    },
}

APP_ADMIN_PAGES = {"commerce": ("commerce",), "mozaikspay": ("usage",), "support": ("support",)}


# Explicit authorization for the test app, reviewed against the shipped pack
# contracts. A new permission must be reviewed here; never grant permissions by
# collecting them from the generated bundle. Dependencies use their own entries.
APP_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "commerce": (
        "commerce.cart.manage", "commerce.catalog.manage", "commerce.catalog.read",
        "commerce.checkout.start", "commerce.inventory.manage", "commerce.orders.manage",
        "commerce.orders.read",
    ),
    "content": ("content.manage",),
    "entitlement_dispatch": (),
    "files": ("files.read", "files.write"),
    "messaging": ("messages.read", "messages.write"),
    "mobile": (),
    "mozaiks_cloud": (
        "cloud_deployment.read", "cloud_deployment.write", "cloud_domain.manage",
        "cloud_domain.read", "cloud_usage_reporter.read",
    ),
    "mozaikspay": ("billing_portal.manage", "billing_portal.read"),
    "notifications": (),
    "onboarding": (),
    "operator_readiness": (),
    "outcome_feedback": (),
    "social": (
        "activity_feed.admin", "activity_feed.read", "friends.manage", "friends.read",
        "user_posts.create", "user_posts.react", "user_posts.read",
    ),
    "support": ("support.manage", "support.submit"),
}


def _auth_fixture(pack_id: str) -> str:
    """Admitted test-app auth; the existing runtime derives holder and non-holder callers."""
    template = BUILD_CONTEXT / "webapp_builder/templates/config/auth.yaml"
    auth = yaml.safe_load(template.read_text(encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
    permissions = sorted({permission for member in _required_packs(pack_id)
                          for permission in APP_PERMISSIONS[member]})
    auth["authorization"] = {"roles": [{"id": PROOF_ROLE, "permissions": permissions}] if permissions else []}
    return yaml.safe_dump(auth, sort_keys=False)


def _matches_known_runtime_failure(row: dict[str, Any]) -> bool:
    """Pin the canonical observer's specific diagnostic, not just its check ID."""
    check, message = row.get("check"), row.get("message")
    if not isinstance(message, str) or row.get("details", {}) != {}:
        return False
    if check == "crud.user_posts.posts.a_delete":
        return row.get("path") == "modules/user_posts/backend/repo.py" and re.fullmatch(
            r"user_posts\.delete_post as user A returned 200 but record 'pst_[0-9a-f]{32}' is still stored\.",
            message,
        ) is not None
    if check in {
        "event.support.create_support_request.domain.messages.thread_created",
        "event.support.create_support_request.domain.messages.message_sent",
    }:
        event = check.removeprefix("event.support.create_support_request.")
        return row.get("path") == "modules/support/module.yaml" and re.fullmatch(
            r"support\.create_support_request as user (?:A|sweep user|role check support\.create_support_request) "
            r"roles=pack_proof_operator "
            r"emitted " + re.escape(event) + r" \(event evt_[0-9a-f]{32}\) which support\.create_support_request "
            r"does not declare in its module\.yaml emits\. The runtime did not dispatch it, so no reaction or "
            r"notification ran for it, although the action itself succeeded\.",
            message,
        ) is not None
    return False


def _assert_runtime_results(pack_id: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    """Retain every failure and require each known row to match its recorded cause."""
    failures = [row for row in rows if row.get("status") == "failed"]
    known = KNOWN_RUNTIME_FAILURES.get(pack_id, {})
    unexpected = [row for row in failures
                  if row["check"] not in known or not _matches_known_runtime_failure(row)]
    assert not unexpected, f"{pack_id} fails at runtime: {unexpected}"
    failed_checks = {row["check"] for row in failures}
    assert set(known) <= failed_checks, (
        f"{pack_id} no longer fails {sorted(set(known) - failed_checks)}: remove them from KNOWN_RUNTIME_FAILURES"
    )
    return known


def _declared_actions(files: dict[str, str]) -> set[str]:
    """The sweep check id of every action the bundle's module manifests declare."""
    return {
        f"action.{path.split('/')[1]}.{action['id']}"
        for path, content in files.items()
        if path.startswith("modules/") and path.endswith("/module.yaml") and path.count("/") == 2
        for action in (yaml.safe_load(content) or {}).get("actions") or []
    }


def _required_packs(pack_id: str) -> list[str]:
    """The pack and every pack its contract requires, dependencies first."""
    contract_path = BUILD_CONTEXT / pack_id / "contract.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) if contract_path.is_file() else {}
    ordered: list[str] = []
    for row in ((contract or {}).get("requires") or {}).get("packs") or []:
        for member in _required_packs(row["pack_id"]):
            if member not in ordered:
                ordered.append(member)
    return [*ordered, pack_id]


AUTHORED_ROLE_PACKS = frozenset({"mozaiks_cloud", "mozaikspay"})


@pytest.fixture(autouse=True)
def proof_offerings(tmp_path, monkeypatch):
    """Offer the stock facades through a real disposable build-context registry.

    Availability neither reviews selection nor activates a provider or account.
    """
    context = tmp_path / "build_context" / "proof_offerings"
    context.mkdir(parents=True)
    (context / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "proof_offerings", "applies_to_workflows": ["AppGenerator"], "assets": [],
        "values": {"operator_capabilities": sorted(AUTHORED_ROLE_PACKS)},
        "projections": {"context_variables": {"operator_capabilities": {"from": "operator_capabilities"}}},
    }), encoding="utf-8")
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(context.parent))

# These are predeclared fixture grants, never inferred from emitted code.
# Keys bind actions to their actual verified collection; values pin permissions.
FIXTURE_ACTION_GRANTS = {
    "files": {"records": {
        "upload_file": ["files.write"], "get_file": ["files.read"],
        "list_files": ["files.read"], "delete_file": ["files.write"],
    }},
    "messages": {
        "threads": {"create_thread": ["messages.write"], "list_threads": ["messages.read"], "get_thread": ["messages.read"]},
        "messages": {"send_message": ["messages.write"]},
        "thread_reads": {"mark_thread_read": ["messages.write"]},
    },
    "content": {"entries": {action: ["content.manage"] for action in ("create_entry", "update_entry", "archive_entry")}},
    "support": {"requests": {
        "get_support_profile": ["support.submit"], "create_support_request": ["support.submit"],
        "list_support_requests": ["support.submit"], "get_support_conversation": ["support.submit"],
        "reply_support_request": ["support.submit"], "update_support_status": ["support.manage"],
    }},
    "friends": {"requests": {
        **{action: ["friends.manage"] for action in ("send_friend_request", "accept_friend_request", "decline_friend_request", "withdraw_friend_request", "remove_friend")},
        **{action: ["friends.read"] for action in ("list_friends", "list_friends_of", "list_friend_requests", "get_friendship_status", "get_friends_count")},
    }},
    "activity_feed": {"events": {
        "get_feed": ["activity_feed.read"], "get_profile_activity": ["activity_feed.read"], "record_activity": ["activity_feed.admin"],
    }},
    "user_posts": {
        "posts": {"create_post": ["user_posts.create"], "get_post": ["user_posts.read"], "delete_post": ["user_posts.create"],
                  "list_posts": ["user_posts.read"], "list_user_posts": ["user_posts.read"]},
        "reactions": {"react_to_post": ["user_posts.react"], "get_reaction_summary": ["user_posts.read"]},
        "comments": {"add_comment": ["user_posts.create"], "delete_comment": ["user_posts.create"], "list_comments": ["user_posts.read"]},
    },
    "commerce": {
        "products": {"list_products": ["commerce.catalog.read"], "get_product": ["commerce.catalog.read"],
                     "create_product": ["commerce.catalog.manage"], "update_product": ["commerce.catalog.manage"],
                     "archive_product": ["commerce.catalog.manage"], "adjust_inventory": ["commerce.inventory.manage"]},
        "carts": {action: ["commerce.cart.manage"] for action in ("get_cart", "add_cart_item", "update_cart_item", "remove_cart_item", "clear_cart")},
        "orders": {"list_orders": ["commerce.orders.read"], "get_order": ["commerce.orders.read"], "update_order_status": ["commerce.orders.manage"]},
        "checkout_requests": {"start_checkout": ["commerce.checkout.start"]},
    },
}


def _fixture_inputs(pack_id):
    rows, plan_rows, identities = [], [], []
    for member in _required_packs(pack_id):
        source_root = BUILD_CONTEXT / member
        identity = verify_pack_integrity(source_root, member)
        source = yaml.safe_load((source_root / "context.yaml").read_text(encoding="utf-8"))["pack"].get("capability_source")
        row = {"id": member, "pack_source_path": str(source_root), "capability_source": source,
               "version": identity["version"], "digest": identity["digest"]}
        rows.append(row)
        plan_rows.append({**row, "capability_pack_id": member})
        identities.append({key: identity[key] for key in ("pack_id", "version", "digest")})
    inputs = deepcopy(PLAN_INPUTS.get(pack_id, {}))
    if pack_id == "entitlement_dispatch":
        inputs["subscription_contract"] = _plan_catalog_contract()
    approved = inputs.pop("data_contract", deepcopy(EMPTY_DATA_CONTRACT))
    entities = inputs.pop("primary_entities", {})
    for row in plan_rows:
        if row["id"] in entities:
            row["primary_entities"] = entities[row["id"]]
    seed = {**inputs, "capability_packs": rows, "app_build_plan": {"capability_packs": plan_rows},
            "data_contract": approved, "build_timestamp": BUILD_TIMESTAMP}
    physical = _resolved_pack_context(seed, _resolved_pack_rows(_resolve_scan_packs(rows, seed)))
    inventory = verified_pack_template_inventory(rows, context_variables=physical)
    seed["data_contract"] = compile_data_contract(approved,
        subscription_contract=inputs.get("subscription_contract"), context_variables=physical,
        pack_data_contracts=verified_pack_data_contracts(inventory))
    return seed, identities


def _expected_roles(pack_id):
    permissions = sorted({permission for member in _required_packs(pack_id) for permission in APP_PERMISSIONS[member]})
    return [{"id": PROOF_ROLE, "permissions": permissions}] if permissions else []


def _reviewed_fixture_policy(pack_id, seed, identities):
    authorization = verified_pack_authorization(seed, selection=identities)
    allowed = {permission for row in _expected_roles(pack_id) for permission in row["permissions"]}
    grants = []
    surfaces = []
    for surface in seed["data_contract"].get("surfaces", []):
        if surface["surface_kind"] != "module":
            continue
        module_id = surface["surface_id"]
        actions = authorization.actions.get(module_id, {})
        surfaces.append({"surface_id": module_id, "surface_kind": "module", "owner": "app",
                         "primary_entities": [row["entity"] for row in surface["collections"]],
                         "owned_mutations": list(actions), "custom_reads": []})
        declared = FIXTURE_ACTION_GRANTS.get(module_id, {})
        assert declared or not any(actions.values()), f"Review explicit collection grants for {module_id}"
        selected = [action for entries in declared.values() for action in entries]
        if selected:
            assert {action: sorted(permissions) for action, permissions in actions.items() if permissions} == {
                action: sorted(permissions) for entries in declared.values() for action, permissions in entries.items()
            }, "Verified pack actions changed: review the fixture declaration"
            for collection, entries in declared.items():
                assert collection in {row["name"] for row in surface["collections"]}
                selected_actions = [action for action, permissions in entries.items() if set(permissions) <= allowed]
                if selected_actions:
                    grants.append({"role_id": PROOF_ROLE, "collection": collection, "actions": sorted(selected_actions)})
    seed["design_surface_map"] = {"surfaces": surfaces}
    if pack_id in AUTHORED_ROLE_PACKS:
        return {"roles": [], "grants": []}
    granted_modules = {surface["surface_id"] for surface in surfaces} & FIXTURE_ACTION_GRANTS.keys()
    assert not {module for module, actions in authorization.actions.items()
                if any(permissions and set(permissions) <= allowed for permissions in actions.values())
                and module not in granted_modules}, "A collectionless module needs an explicit authored-proof lane"
    return {"roles": [{"role_id": PROOF_ROLE, "label": "Pack proof operator",
                       "description": "The repository fixture's predeclared permissions."}] if allowed else [],
            "grants": grants}


def _fixture_files(pack_id, seed):
    app_id = f"pack_proof_{pack_id}"
    auth = yaml.safe_load(_auth_fixture(pack_id))
    pages = ["overview", *sorted({page for member in _required_packs(pack_id) for page in APP_ADMIN_PAGES.get(member, ())})]
    return app_id, {"app.json": json.dumps({"appId": app_id, "appName": f"{pack_id} proof", "authRequired": True}),
                    "config/auth.yaml": yaml.safe_dump(auth, sort_keys=False),
                    "data/contract.json": json.dumps(seed["data_contract"]),
                    "admin/admin_registry.yaml": yaml.safe_dump({"pages": [
                        {"id": page, "label": page.title(), "path": "/admin" if page == "overview" else "/admin/" + page}
                        for page in pages]})}


async def _assemble(pack_id: str) -> tuple[dict[str, str], Any, list[dict[str, str]]]:
    seed, identities = _fixture_inputs(pack_id)
    policy = _reviewed_fixture_policy(pack_id, seed, identities)
    app_id, files = _fixture_files(pack_id, seed)
    seed.update(app_id=app_id, generated_files=files)
    context = create_context_container(initial=reviewed_pack_fixture(factory_context(seed),
        _required_packs(pack_id), access_policy=policy))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], f"{pack_id} cannot be assembled alone: {result.get('error')}"
    files = {file["filename"]: file["content"] for file in result["code_files"]}
    expected = [] if pack_id in AUTHORED_ROLE_PACKS else _expected_roles(pack_id)
    assert yaml.safe_load(files["config/auth.yaml"])["authorization"]["roles"] == expected
    return files, context, identities


@pytest.mark.asyncio
async def test_page_repair_reads_compiler_added_actions_from_the_admitted_partial_pack():
    files, context, _ = await _assemble("outcome_feedback")
    path = "modules/outcome_feedback/module.yaml"
    manifest = yaml.safe_load(files[path])
    actions = {action["id"]: action for action in manifest["actions"]}
    assert {"list_my_feedback", "get_records", "list_records"} <= actions.keys()
    context.set("generated_files", files)
    proposed = {**manifest, "actions": [{"id": "get_records", "description": "replacement"}, {"id": "invented"}]}
    index = module_action_index_from_context(context, authored_files={path: yaml.safe_dump(proposed)})
    assert index["outcome_feedback"] == actions


def _assemble_authored_role_fixture(pack_id):
    assert pack_id in AUTHORED_ROLE_PACKS
    seed, identities = _fixture_inputs(pack_id)
    app_id, files = _fixture_files(pack_id, seed)
    seed.update(app_id=app_id, generated_files=files)
    physical = _resolved_pack_context(seed, _resolved_pack_rows(_resolve_scan_packs(seed["capability_packs"], seed)))
    templates = resolve_managed_capability_templates(seed["capability_packs"], context_variables=physical)
    output = _merge_code_files([{"code_files": templates}], app_build_plan=seed["app_build_plan"],
        data_contract=seed["data_contract"], subscription_contract=seed.get("subscription_contract"),
        context_variables=physical)
    authored = {row["filename"]: row["content"] for row in output}
    authored.update({path: text for path, text in files.items() if path not in authored})
    authored.update(materialize_auth_scaffold(authored))
    assert yaml.safe_load(authored["config/auth.yaml"])["authorization"]["roles"] == _expected_roles(pack_id)
    diagnostics = validate_authored_app_file_map(authored,
        inherited_components=registered_ui_components(BUILD_CONTEXT.parents[1] / "chat-ui/src"))
    assert not [item for item in diagnostics if item.severity == "error"], diagnostics
    return authored, identities


def _runtime_fixture(pack_id, factory_files, factory_identities):
    """Keep provider-role wiring evidence outside the approved Factory bundle."""
    if pack_id not in AUTHORED_ROLE_PACKS:
        return factory_files, "reviewed_factory_fixture"
    authored, identities = _assemble_authored_role_fixture(pack_id)
    assert identities == factory_identities
    repeated, repeated_identities = _assemble_authored_role_fixture(pack_id)
    assert authored == repeated and identities == repeated_identities
    implementation_paths = {
        path for path in factory_files
        if path.startswith(("modules/", "services/"))
    }
    assert implementation_paths
    assert implementation_paths == {path for path in authored if path.startswith(("modules/", "services/"))}
    assert {path: factory_files[path] for path in implementation_paths} == {
        path: authored[path] for path in implementation_paths
    }
    assert authored[".mozaiks/pack_provenance.json"] == factory_files[".mozaiks/pack_provenance.json"]
    return authored, "authored_provider_role_fixture"


@pytest.mark.asyncio
@pytest.mark.parametrize("pack_id", sorted(AUTHORED_ROLE_PACKS))
async def test_factory_review_does_not_adopt_candidate_provider_role(pack_id):
    # _fixture_files supplies the predeclared candidate role; the empty reviewed
    # grants must remove it rather than borrowing authority from candidate auth.
    files, _context, _identities = await _assemble(pack_id)
    assert yaml.safe_load(_auth_fixture(pack_id))["authorization"]["roles"] == _expected_roles(pack_id)
    assert yaml.safe_load(files["config/auth.yaml"])["authorization"]["roles"] == []
    runtime_files, origin = _runtime_fixture(pack_id, files, _identities)
    assert origin == "authored_provider_role_fixture"
    assert yaml.safe_load(runtime_files["config/auth.yaml"])["authorization"]["roles"] == _expected_roles(pack_id)


@pytest.mark.parametrize("pack_id", SHIPPED_PACKS)
def test_every_shipped_pack_verifies(pack_id):
    identity = verify_pack_integrity(BUILD_CONTEXT / pack_id, pack_id)
    assert identity["pack_id"] == pack_id and identity["version"] and identity["digest"].startswith("sha256:")


def test_every_shipped_pack_is_proven_or_says_why_not():
    assert SHIPPED_PACKS, "no shipped capability packs were found"
    assert set(NOT_MATERIALIZABLE_ALONE) <= set(SHIPPED_PACKS)
    for pack_id, reason in NOT_MATERIALIZABLE_ALONE.items():
        assert reason.strip()
        assert not (BUILD_CONTEXT / pack_id / "templates").exists(), (
            f"{pack_id} ships templates now: prove it instead of excluding it"
        )
    assert set(PLAN_INPUTS) <= set(SHIPPED_PACKS)
    assert set(APP_PERMISSIONS) == set(SHIPPED_PACKS)
    assert set(BLOCKED_PACKS) | set(KNOWN_RUNTIME_FAILURES) <= set(SHIPPED_PACKS) - set(NOT_MATERIALIZABLE_ALONE)
    assert all(message.strip() and reason.strip() for message, reason in BLOCKED_PACKS.values())
    assert all(reason.strip() for failures in KNOWN_RUNTIME_FAILURES.values() for reason in failures.values())


def test_action_sweep_counts_a_handler_that_cannot_take_its_inputs_as_failed():
    """The executor answers INVALID_PARAMS for a handler TypeError; the sweep reads its own log line for it."""
    source = (Path(__file__).resolve().parents[1] / "mozaiksai/core/runtime/composition/module_executor.py").read_text(
        encoding="utf-8")
    assert '"MODULE_ACTION_BAD_PARAMS: module=%s action=%s error=%s"' in source

    def record(module: str, action: str) -> logging.LogRecord:
        return logging.LogRecord("mozaiks.workflow.module_executor", logging.WARNING, __file__, 1,
                                 "MODULE_ACTION_BAD_PARAMS: module=%s action=%s error=%s",
                                 (module, action, TypeError("missing 1 required positional argument")), None)

    records = [record("files", "list_files"), record("notification_settings", "get_notification_preferences")]
    assert _bad_params(records, "notification_settings", "get_notification_preferences") == (
        "missing 1 required positional argument"
    )
    assert _bad_params(records, "notification_settings", "get_notification") is None
    assert _bad_params(records[:1], "notification_settings", "get_notification_preferences") is None


@pytest.mark.flaky(reruns=0)
@pytest.mark.asyncio
@pytest.mark.parametrize("pack_id", [pack for pack in SHIPPED_PACKS if pack not in NOT_MATERIALIZABLE_ALONE])
async def test_shipped_pack_built_alone_passes_acceptance_and_runs(pack_id, mongo, tmp_path):  # noqa: F811
    if pack_id not in BLOCKED_PACKS:
        await _prove(pack_id, mongo, tmp_path)
        return
    message, reason = BLOCKED_PACKS[pack_id]
    with pytest.raises(AssertionError, match=re.escape(message)):
        await _prove(pack_id, mongo, tmp_path)
    pytest.xfail(reason)


async def _prove(pack_id: str, mongo: Any, tmp_path: Path) -> None:  # noqa: F811
    files, context, identities = await _assemble(pack_id)
    again, _, _ = await _assemble(pack_id)
    assert again == files, f"{pack_id} does not materialize deterministically"

    acceptance = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    checks = {
        name: result for name, result in acceptance.items()
        if isinstance(result, dict) and "passed" in result and name not in CONTAINED_GATE_CHECKS
    }
    planned = checks.pop("planned_completeness", None)
    assert planned is not None and [item["block_reason"] for item in planned["diagnostics"]] == [NO_PLANNED_TASKS], (
        f"{pack_id} has plan completeness diagnostics beyond its missing build tasks: {planned}"
    )
    failed = {
        name: result.get("errors") or result.get("failed_tests") or result.get("checks")
        for name, result in checks.items() if result.get("passed") is not True
    }
    assert checks and not failed, f"{pack_id} fails the app-bundle acceptance gate: {failed}"

    runtime_files, runtime_origin = _runtime_fixture(pack_id, files, identities)
    app_root = tmp_path / "app"
    _write_files_to_dir(app_root, runtime_files)
    provider_fixture = None
    if pack_id in PROVIDER_FIXTURE_PACKS:
        smoke = await run_provider_probe(app_root, mongo_uri=mongo.uri, pack_id=pack_id, mode="smoke")
        sweep_result = await run_provider_probe(app_root, mongo_uri=mongo.uri, pack_id=pack_id, mode="sweep")
        swept = sweep_result["results"]
        provider_fixture = {"smoke": smoke["provider_fixture"], "sweep": sweep_result["provider_fixture"]}
    else:
        smoke = await run_app_runtime_smoke(app_root, mongo_uri=mongo.uri)
        swept = await run_action_sweep(app_root, mongo_uri=mongo.uri)
    rows = smoke.get("results") or []
    assert rows and smoke.get("skipped_reason") is None, f"{pack_id} smoke did not run: {smoke.get('skipped_reason')}"
    declared = _declared_actions(runtime_files)
    assert {row["check"] for row in swept if row["check"].startswith("action.")} == declared, (
        f"{pack_id} action sweep did not reach exactly its declared actions"
    )

    known = _assert_runtime_results(pack_id, [*rows, *swept])

    receipt = {
        "schema_version": "mozaiks.pack_proof.v1",
        "framework_version": FRAMEWORK_VERSION,
        "pack": identities[-1],
        "requires": identities[:-1],
        "bundle_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest(),
        "acceptance_checks": sorted(checks),
        "runtime_fixture_origin": runtime_origin,
        "runtime_bundle_sha256": hashlib.sha256(json.dumps(runtime_files, sort_keys=True).encode("utf-8")).hexdigest(),
        "factory_authorization": yaml.safe_load(files["config/auth.yaml"])["authorization"],
        "runtime_smoke": {
            "checks_passed": [row["check"] for row in rows if row.get("status") == "passed"],
            "not_run": [row["check"] for row in rows if row.get("status") == "not_run"],
        },
        "action_sweep": {
            "coverage": "HTTP wiring; business 4xx is not successful business execution",
            "actions_answered": sorted(row["check"] for row in swept if row.get("status") == "passed"
                                       and row["check"].startswith("action.")),
            "not_run": sorted(row["check"] for row in swept if row.get("status") == "not_run"),
        },
        "authorization": yaml.safe_load(runtime_files["config/auth.yaml"])["authorization"],
        "provider_fixture": provider_fixture,
        "known_failures": {check: known[check] for check in sorted(known)},
        "outcome": "wiring_smoke_passed" if not known else "wiring_smoke_with_known_failures",
    }
    text = json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    (tmp_path / f"{pack_id}.pack-proof.json").write_text(text, encoding="utf-8")
    if destination := os.environ.get("MOZAIKS_PACK_PROOF_DIR"):
        Path(destination).mkdir(parents=True, exist_ok=True)
        (Path(destination) / f"{pack_id}.pack-proof.json").write_text(text, encoding="utf-8")


@pytest.mark.parametrize("pack_id", SHIPPED_PACKS)
def test_pack_proof_permission_inventory_requires_explicit_review(pack_id):
    required = {
        permission
        for path in (BUILD_CONTEXT / pack_id / "templates/modules").glob("*/module.yaml")
        for action in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("actions", [])
        for permission in action.get("permissions", [])
    }
    assert set(APP_PERMISSIONS[pack_id]) == required
    auth = yaml.safe_load(_auth_fixture(pack_id))
    assert auth["schema_version"] == "mozaiks.auth.v2"
    assert auth["frontend"]["default_scopes"] == ["openid", "profile", "email"]
    assert auth["signup_enabled"] is False


@pytest.mark.asyncio
async def test_pack_proof_missing_grant_is_a_failure(tmp_path, mongo, monkeypatch):  # noqa: F811
    monkeypatch.setitem(APP_PERMISSIONS, "files", ("files.write",))
    files, _context, _identities = await _assemble("files")
    auth = yaml.safe_load(files["config/auth.yaml"])
    assert auth["authorization"]["roles"] == [{"id": PROOF_ROLE, "permissions": ["files.write"]}]
    app_root = tmp_path / "app"
    _write_files_to_dir(app_root, files)
    rows = await run_action_sweep(app_root, mongo_uri=mongo.uri)
    failures = {row["check"] for row in rows if row.get("status") == "failed"}
    assert {"permission.files.get_file", "permission.files.list_files"} <= failures
    with pytest.raises(AssertionError, match=r"permission\.files\."):
        _assert_runtime_results("files", rows)


@pytest.mark.asyncio
async def test_pack_proof_role_holder_and_non_holder_evidence(tmp_path, mongo):  # noqa: F811
    files, _context, _identities = await _assemble("files")
    auth = yaml.safe_load(files["config/auth.yaml"])
    assert auth["authorization"]["roles"] == [
        {"id": PROOF_ROLE, "permissions": ["files.read", "files.write"]},
    ]
    assert auth["frontend"]["default_scopes"] == ["openid", "profile", "email"]
    app_root = tmp_path / "app"
    _write_files_to_dir(app_root, files)
    result = await run_app_runtime_smoke(app_root, mongo_uri=mongo.uri)
    rows = {row["check"]: row for row in result["results"]}
    actions = {"upload_file", "get_file", "list_files", "delete_file"}
    holders = {f"role.files.{action}.holder_allowed" for action in actions}
    non_holders = {f"role.files.{action}.non_holder_denied" for action in actions}
    assert {check for check in rows if check.startswith("role.files.")} == holders | non_holders
    for check in holders:
        assert rows[check]["status"] == "passed", rows[check]
        assert rows[check]["details"]["roles"] == [PROOF_ROLE]
    for check in non_holders:
        assert rows[check]["status"] == "passed", rows[check]
        assert rows[check]["details"]["roles"] == []
        assert rows[check]["details"]["records_unchanged"] is True
    assert not [check for check in rows if check.startswith("permission.")]
    assert all(not any(scope.startswith("files.") for scope in principal["scopes"])
               for principal in result["principals"].values())
    _assert_runtime_results("files", result["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize("pack_id", sorted(PROVIDER_FIXTURE_PACKS))
async def test_unconfigured_managed_pack_cannot_count_as_successful_execution(pack_id, tmp_path, mongo):  # noqa: F811
    files, _context, _identities = await _assemble(pack_id)
    files, origin = _runtime_fixture(pack_id, files, _identities)
    assert origin == "authored_provider_role_fixture"
    app_root = tmp_path / "app"
    _write_files_to_dir(app_root, files)
    result = await run_app_runtime_smoke(app_root, mongo_uri=mongo.uri)
    assert any(row["status"] == "failed" and row["check"].startswith("role.")
               and row["check"].endswith(".holder_allowed") for row in result["results"])
    with pytest.raises(AssertionError, match="holder_allowed"):
        _assert_runtime_results(pack_id, result["results"])


def _known_failure_rows(pack_id: str) -> list[dict[str, Any]]:
    if pack_id == "social":
        return [{
            "check": "crud.user_posts.posts.a_delete", "status": "failed",
            "path": "modules/user_posts/backend/repo.py",
            "message": "user_posts.delete_post as user A returned 200 but record "
                       "'pst_0123456789abcdef0123456789abcdef' is still stored.",
        }]
    return [{
        "check": f"event.support.create_support_request.domain.messages.{event}", "status": "failed",
        "path": "modules/support/module.yaml",
        "message": f"support.create_support_request as user sweep user roles=pack_proof_operator emitted domain.messages.{event} "
                   "(event evt_0123456789abcdef0123456789abcdef) which support.create_support_request "
                   "does not declare in its module.yaml emits. The runtime did not dispatch it, so no "
                   "reaction or notification ran for it, although the action itself succeeded.",
    } for event in ("thread_created", "message_sent")]


@pytest.mark.parametrize("pack_id", ["social", "support"])
def test_known_runtime_failure_accepts_only_recorded_cause(pack_id):
    assert _assert_runtime_results(pack_id, _known_failure_rows(pack_id)) == KNOWN_RUNTIME_FAILURES[pack_id]


@pytest.mark.parametrize("pack_id", ["social", "support"])
@pytest.mark.parametrize("change", ["cause", "path", "details", "identifier", "principal"])
def test_known_runtime_failure_rejects_same_id_with_different_cause(pack_id, change):
    rows = _known_failure_rows(pack_id)
    if change == "cause":
        rows[0]["message"] = "The handler returned 500 EXECUTION_ERROR."
    elif change == "path":
        rows[0]["path"] = "modules/another_module/backend/service.py"
    elif change == "details":
        rows[0]["details"] = {"error_code": "EXECUTION_ERROR"}
    elif change == "identifier":
        rows[0]["message"] = rows[0]["message"].replace("0123456789abcdef0123456789abcdef", "invalid-id")
    else:
        rows[0]["message"] = rows[0]["message"].replace("as user ", "as user foreign ", 1)
    with pytest.raises(AssertionError, match="fails at runtime"):
        _assert_runtime_results(pack_id, rows)


@pytest.mark.parametrize("pack_id", ["social", "support"])
@pytest.mark.parametrize("unexpected_first", [False, True])
def test_known_runtime_failure_cannot_hide_duplicate_wrong_cause(pack_id, unexpected_first):
    rows = _known_failure_rows(pack_id)
    unexpected = {**rows[0], "message": "The handler returned 500 EXECUTION_ERROR."}
    combined = [unexpected, *rows] if unexpected_first else [*rows, unexpected]
    with pytest.raises(AssertionError, match="fails at runtime"):
        _assert_runtime_results(pack_id, combined)


@pytest.mark.parametrize("pack_id", ["social", "support"])
def test_known_runtime_failure_requires_removal_when_fixed(pack_id):
    rows = [{**row, "status": "passed"} for row in _known_failure_rows(pack_id)]
    with pytest.raises(AssertionError, match="no longer fails"):
        _assert_runtime_results(pack_id, rows)


@pytest.mark.parametrize("label", ["A", "sweep user", "role check support.create_support_request"])
def test_known_runtime_failure_matches_canonical_event_observer(label):
    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke
    from mozaiksai.core.runtime.composition.module_event_provenance import ModuleEventRejection

    rows = []
    run = SimpleNamespace(
        principals={}, scopes=[], reported_rejections=set(),
        role_grants=lambda: {PROOF_ROLE: {"support.submit"}},
        record=lambda check, passed, message, **details: rows.append({
            "check": check, "status": "passed" if passed else "failed", "message": message, **details,
        }),
    )
    principal = smoke._Principal(label, "member-a", "test-token", None)
    principal = smoke._SmokeRun._with_action_roles(run, principal, ["support.submit"])
    for event in ("thread_created", "message_sent"):
        rejection = ModuleEventRejection(
            event_id="evt_0123456789abcdef0123456789abcdef", event_type=f"domain.messages.{event}",
            category="undeclared", reason="Action does not declare this event.", validator="emits",
        )
        smoke._SmokeRun._record_rejected_event(run, "support", "create_support_request", principal, rejection)
    assert _assert_runtime_results("support", rows) == KNOWN_RUNTIME_FAILURES["support"]
