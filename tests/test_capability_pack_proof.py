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
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    verify_pack_integrity,
)
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.outcome_feedback import WorkflowFeedbackEvidence
from mozaiksai.version import __version__ as FRAMEWORK_VERSION
from tests.appgenerator_subscription_fixture import subscription_contract
from tests.factory_context import factory_context
from tests.pack_proof_action_sweep import _bad_params, run_action_sweep
from tests.pack_proof_provider_fixture import PACKS as PROVIDER_FIXTURE_PACKS
from tests.pack_proof_provider_fixture import run_provider_probe
from tests.pack_proof_social_lifecycle import SOCIAL_PROOF_ROLES, SOCIAL_SEMANTIC_CHECKS
from tests.test_app_runtime_smoke import mongo  # noqa: F401 - real MongoDB and its leaked-database guard

BUILD_CONTEXT = Path(__file__).resolve().parents[1] / "factory_app" / "build_context"
BUILD_TIMESTAMP = "2026-01-01T00:00:00+00:00"
EMPTY_DATA_CONTRACT: dict[str, Any] = {"version": "1", "surfaces": [], "shared_collections": []}
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
BLOCKED_PACKS: dict[str, tuple[str, str]] = {
    "notifications": ("contains placeholder/not-implemented marker 'NotImplementedError'", (
        "The provider adapter stubs raise NotImplementedError by design "
        "(test_adapters_have_not_implemented_stub), and the acceptance gate's "
        "PLACEHOLDER_IMPLEMENTATION check fails every bundle that contains that marker."
    )),
}

_SUPPORT_CROSS_MODULE_EMIT = (
    "create_support_request creates its thread and first message by calling the messages "
    "service inside its own action, so the runtime attributes the messages events to support, "
    "which cannot declare another module's events. They are dropped: no reaction or "
    "notification runs for a support conversation."
)

# Runtime failures (smoke or action sweep) a pack is known to have, by check id,
# with the reason. The proof fails when one stops failing, so this list only shrinks.
KNOWN_RUNTIME_FAILURES: dict[str, dict[str, str]] = {
    "support": {
        f"event.support.create_support_request.domain.messages.{event}": _SUPPORT_CROSS_MODULE_EMIT
        for event in ("thread_created", "message_sent")
    },
}


# The generic physical-removal assertion remains failed and is included verbatim
# in the receipt. Only independently observed Social semantics can explain it.
OBSERVER_MISMATCHES = {"social": {
    "crud.user_posts.posts.a_delete": "The declared Social lifecycle retains a deleted post tombstone.",
}}


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


# Explicit authorization for the test app, reviewed against the shipped pack
# contracts. A new permission must be reviewed here; never grant permissions by
# collecting them from the generated bundle. Dependencies use their own entries.
APP_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "commerce": (
        "commerce.cart.manage", "commerce.catalog.manage", "commerce.catalog.read",
        "commerce.checkout.start", "commerce.inventory.manage", "commerce.orders.manage",
        "commerce.orders.read",
    ),
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
PROOF_ROLE = "pack_proof_operator"


def _auth_fixture(pack_id: str) -> str:
    """Admitted test-app auth; the existing runtime derives holder and non-holder callers."""
    template = BUILD_CONTEXT / "webapp_builder/templates/config/auth.yaml"
    auth = yaml.safe_load(template.read_text(encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
    permissions = sorted({permission for member in _required_packs(pack_id)
                          for permission in APP_PERMISSIONS[member]})
    auth["authorization"] = {"roles": [{"id": PROOF_ROLE, "permissions": permissions}] if permissions else []}
    if pack_id == "social":
        auth["authorization"]["roles"].extend(
            {"id": role, "permissions": permissions} for role, permissions in SOCIAL_PROOF_ROLES.items()
        )
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
    expected = {**known, **OBSERVER_MISMATCHES.get(pack_id, {})}
    unexpected = [row for row in failures
                  if row["check"] not in expected or not _matches_known_runtime_failure(row)]
    assert not unexpected, f"{pack_id} fails at runtime: {unexpected}"
    failed_checks = {row["check"] for row in failures}
    assert set(expected) <= failed_checks, (
        f"{pack_id} no longer fails {sorted(set(expected) - failed_checks)}: remove the obsolete expected failure"
    )
    if pack_id == "social":
        semantic = [row for row in rows if row["check"].startswith("semantic.social.posts.")]
        assert len(semantic) == len(SOCIAL_SEMANTIC_CHECKS) and {
            row["check"] for row in semantic if row["status"] == "passed"
            and row.get("details", {}).get("coverage") == "social_post_lifecycle"
        } == SOCIAL_SEMANTIC_CHECKS, "Social observer mismatch requires complete independent semantic assertions"
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


async def _assemble(pack_id: str) -> tuple[dict[str, str], Any, list[dict[str, str]]]:
    rows, plan_rows, identities = [], [], []
    for member in _required_packs(pack_id):
        root = BUILD_CONTEXT / member
        identity = verify_pack_integrity(root, member)
        source = yaml.safe_load((root / "context.yaml").read_text(encoding="utf-8"))["pack"].get("capability_source")
        rows.append({"id": member, "pack_source_path": str(root), "capability_source": source,
                     "version": identity["version"], "digest": identity["digest"]})
        plan_rows.append({"capability_pack_id": member, "capability_source": source})
        identities.append({key: identity[key] for key in ("pack_id", "version", "digest")})
    inputs = deepcopy(PLAN_INPUTS.get(pack_id, {}))
    approved = inputs.pop("data_contract", deepcopy(EMPTY_DATA_CONTRACT))
    for row in plan_rows:
        if entities := inputs.get("primary_entities", {}).get(row["capability_pack_id"]):
            row["primary_entities"] = entities
    inputs.pop("primary_entities", None)
    app_id = f"pack_proof_{pack_id}"
    context = create_context_container(initial=factory_context({
        **inputs,
        "capability_packs": rows,
        "app_build_plan": {"capability_packs": plan_rows},
        "app_id": app_id,
        "data_contract": approved,
        "build_timestamp": BUILD_TIMESTAMP,
        "generated_files": {
            "app.json": json.dumps({"appId": app_id, "appName": f"{pack_id} proof", "authRequired": True}),
            "config/auth.yaml": _auth_fixture(pack_id),
            "data/contract.json": json.dumps(approved),
        },
    }))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], f"{pack_id} cannot be assembled alone: {result.get('error')}"
    return {file["filename"]: file["content"] for file in result["code_files"]}, context, identities


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

    app_root = tmp_path / "app"
    _write_files_to_dir(app_root, files)
    provider_fixture = None
    if pack_id in PROVIDER_FIXTURE_PACKS:
        smoke = await run_provider_probe(app_root, mongo_uri=mongo.uri, pack_id=pack_id, mode="smoke")
        sweep_result = await run_provider_probe(app_root, mongo_uri=mongo.uri, pack_id=pack_id, mode="sweep")
        swept = sweep_result["results"]
        provider_fixture = {"smoke": smoke["provider_fixture"], "sweep": sweep_result["provider_fixture"]}
    else:
        smoke = await run_app_runtime_smoke(app_root, mongo_uri=mongo.uri)
        swept = await run_action_sweep(app_root, mongo_uri=mongo.uri, semantic_pack="social" if pack_id == "social" else None)
    rows = smoke.get("results") or []
    assert rows and smoke.get("skipped_reason") is None, f"{pack_id} smoke did not run: {smoke.get('skipped_reason')}"
    declared = _declared_actions(files)
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
        "authorization": yaml.safe_load(files["config/auth.yaml"])["authorization"],
        "provider_fixture": provider_fixture,
        "known_failures": {check: known[check] for check in sorted(known)},
        "observer_mismatches": [row for row in rows if row["check"] in OBSERVER_MISMATCHES.get(pack_id, {})],
        "semantic_assertions": [row for row in swept if row["check"].startswith("semantic.")],
        "semantic_scope": "Mounted HTTP actions, authorization and Mongo storage; no downstream reaction delivery" if pack_id == "social" else None,
        "outcome": ("semantic_proof_passed_with_observer_mismatch" if pack_id in OBSERVER_MISMATCHES
                    else "wiring_smoke_passed" if not known else "wiring_smoke_with_known_failures"),
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
    rows = _known_failure_rows(pack_id)
    if pack_id == "social":
        rows += _social_semantic_rows()
    assert _assert_runtime_results(pack_id, rows) == KNOWN_RUNTIME_FAILURES.get(pack_id, {})


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


def test_social_observer_mismatch_requires_independent_semantics():
    with pytest.raises(AssertionError, match="semantic"):
        _assert_runtime_results("social", _known_failure_rows("social"))


def _social_semantic_rows():
    return [{"check": check, "status": "passed", "details": {"coverage": "social_post_lifecycle"}}
            for check in sorted(SOCIAL_SEMANTIC_CHECKS)]


@pytest.mark.parametrize("change", ["missing", "not_run", "duplicate", "http_only", "failed"])
def test_social_semantic_evidence_cannot_be_incomplete_or_only_http_success(change):
    semantic = _social_semantic_rows()
    if change == "missing":
        semantic.pop()
    elif change == "not_run":
        semantic[0]["status"] = "not_run"
    elif change == "duplicate":
        semantic[-1] = semantic[0]
    elif change == "http_only":
        semantic[0]["details"] = {"http_status": 200, "coverage": "http_wiring"}
    else:
        semantic[0]["status"] = "failed"
    with pytest.raises(AssertionError):
        _assert_runtime_results("social", [*_known_failure_rows("social"), *semantic])


@pytest.mark.asyncio
@pytest.mark.flaky(reruns=0)
@pytest.mark.parametrize("fault,expected", [
    ("no_op", "author_tombstone"),
    ("physical_delete", "author_tombstone"),
    ("deleted_detail_visible", "author_hidden"),
    ("deleted_list_visible", "author_hidden"),
    ("foreign_author_allowed", "foreign_member_denied"),
    ("action_permission_removed", "missing_create_denied"),
    ("missing_post_reports_success", "foreign_app_denied"),
])
async def test_social_semantic_probe_rejects_broken_candidate_bytes(fault, expected, tmp_path, mongo):  # noqa: F811
    """Mutation controls exercise the observer through actual HTTP and MongoDB."""
    files, _context, _identities = await _assemble("social")
    root = "modules/user_posts/"
    if fault == "action_permission_removed":
        module = yaml.safe_load(files[root + "module.yaml"])
        action = next(row for row in module["actions"] if row["id"] == "delete_post")
        assert action["permissions"] == ["user_posts.create"]
        action["permissions"] = []
        files[root + "module.yaml"] = yaml.safe_dump(module, sort_keys=False)
    else:
        mutations = {
            "no_op": ("backend/repo.py", '"status": "deleted"', '"status": "published"'),
            "physical_delete": ("backend/repo.py", '''update_one(
            {"post_id": post_id},
            {"$set": {"status": "deleted", "updated_at": deleted_at}},
        )''', '''delete_one({"post_id": post_id})'''),
            "deleted_detail_visible": ("backend/policy.py",
                'and post.get("status") == POST_STATUS_PUBLISHED',
                'and post.get("status") in {POST_STATUS_PUBLISHED, "deleted"}'),
            "deleted_list_visible": ("backend/policy.py",
                '"status": {"$eq": POST_STATUS_PUBLISHED, "$not": {"$type": "array"}},',
                '"status": {"$in": [POST_STATUS_PUBLISHED, "deleted"], "$not": {"$type": "array"}},'),
            "foreign_author_allowed": ("backend/policy.py",
                'return post.get("author_id") == user_id or bool(',
                'return True or post.get("author_id") == user_id or bool('),
            "missing_post_reports_success": ("backend/service.py",
                'return {"success": False, "error": "Post not found."}',
                'return {"success": True}'),
        }
        path, original, replacement = mutations[fault]
        assert files[root + path].count(original) == 1
        files[root + path] = files[root + path].replace(original, replacement)
    app_root = tmp_path / "app"
    _write_files_to_dir(app_root, files)
    rows = await run_action_sweep(app_root, mongo_uri=mongo.uri, semantic_pack="social")
    assert any(row["check"] == f"semantic.social.posts.{expected}" and row["status"] == "failed" for row in rows), rows
    assert any(row["check"] == "sweep.error" and row["status"] == "failed" for row in rows)
