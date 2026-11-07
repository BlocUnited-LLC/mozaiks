"""Role grants cross an explicit draft-bound review before Factory may use them."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.DesignDocs.tools import save_design_doc
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    access_policy_sha256,
    resolve_design_access_policy,
)
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory

persistence = inventory.persistence


def _bundle():
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [
        {**ownership._collection("reports", "reports"), "entity": "Report"},
    ]
    bundle["access_policy"] = {
        "roles": [{"role_id": "coordinator", "label": "Coordinator", "description": "People maintaining shared reports."}],
        "grants": [{"role_id": "coordinator", "collection": "reports", "actions": [
            "create_report", "update_report", "delete_report",
        ]}],
    }
    return bundle


def _approve(monkeypatch, persistence, *, action="approve", overrides=None):
    async def respond(_tool_id, payload, **kwargs):
        # Saving, downstream state, and artifact authority must all wait for review.
        persistence[1].assert_not_called()
        persistence[2].assert_not_awaited()
        assert kwargs["chat_id"] == "pricing_chat"
        assert kwargs["workflow_name"] == "DesignDocs"
        assert "Coordinator may create_report, update_report, delete_report on reports." in payload["checkpoints"]
        assert all(item["payload"]["review_id"] == payload["review_id"] for item in payload["actions"])
        return {
            "action": action, "approved": action == "approve", "review_id": payload["review_id"],
            "rationale": "Only coordinators should maintain reports.", **(overrides or {}),
        }
    ui = AsyncMock(side_effect=respond)
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    return ui


def _record(summary):
    return {
        "app_id": "reports-app", "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "current",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": summary}},
    }


def _approved_context(monkeypatch, persistence):
    context = ownership._context(managed=False)
    _approve(monkeypatch, persistence)
    result = inventory._save(context, _bundle())
    assert result["outcome"] == "saved", result
    summary = persistence[2].await_args.kwargs["summary_payload"]
    context.set("design_docs_artifact", _record(deepcopy(summary)))
    return context, summary


def test_design_model_preserves_typed_access_policy():
    models, _ = load_workflow_structured_outputs("DesignDocs")
    bundle = _bundle()
    parsed = models["DesignDocsBundle"].model_validate(bundle).model_dump(mode="json")
    assert parsed["access_policy"] == bundle["access_policy"]
    bundle["access_policy"]["grants"][0]["actions"] = "*"
    with pytest.raises(ValueError):
        models["DesignDocsBundle"].model_validate(bundle)


def test_owner_approval_persists_exact_policy_and_projects_from_summary(monkeypatch, persistence):
    context, summary = _approved_context(monkeypatch, persistence)
    policy = _bundle()["access_policy"]
    review = summary["access_policy_review"]
    assert review["status"] == "approved"
    assert review["reviewed_by"] == "owner"
    assert review["policy_sha256"] == access_policy_sha256(policy)
    assert review["build_id"] == "pricing_build"
    assert review["target_app_id"] == "reports-app"
    assert detach(context.get("access_policy")) == policy
    assert resolve_design_access_policy(context) == policy
    backend = next(call.kwargs for call in persistence[0].upsert_design_doc.await_args_list if call.kwargs["kind"] == "backend")
    assert backend["extra_fields"]["access_policy"] == policy
    assert backend["extra_fields"]["access_policy_review"] == review
    assert "## App role permissions" in backend["content"]
    assert "role_id: coordinator" in backend["content"]


@pytest.mark.parametrize("action,outcome", [("request_changes", "review_changes")])
def test_rejected_policy_never_becomes_an_artifact(monkeypatch, persistence, action, outcome):
    ui = _approve(monkeypatch, persistence, action=action)
    context = ownership._context(managed=False)
    result = inventory._save(context, _bundle())
    assert result["outcome"] == outcome, result
    ui.assert_awaited_once()
    persistence[1].assert_not_called()
    persistence[2].assert_not_awaited()
    assert context.get("access_policy") is None
    assert context.get("data_contract") is None


@pytest.mark.parametrize("prior_failures", [0, 1, 2])
def test_owner_corrections_preserve_structural_budget_and_still_require_fresh_approval(
    monkeypatch, persistence, prior_failures,
):
    context = ownership._context(managed=False)
    ui = _approve(monkeypatch, persistence, action="request_changes")
    invalid = _bundle()
    invalid["access_policy"]["grants"][0]["actions"] = ["create_report"]
    for attempt in range(prior_failures):
        assert inventory._save(context, invalid)["outcome"] == "revise"
        assert context.get("design_docs_save_attempts") == attempt + 1
    ui.assert_not_awaited()

    for _ in range(2):
        assert inventory._save(context, _bundle())["outcome"] == "review_changes"
        assert context.get("design_docs_save_attempts") == prior_failures
        assert context.get("access_policy") is None
    assert len({call.args[1]["review_id"] for call in ui.await_args_list}) == 2
    persistence[1].assert_not_called()
    persistence[2].assert_not_awaited()

    _approve(monkeypatch, persistence)
    revised = _bundle()
    revised["access_policy"]["roles"][0]["description"] = "Only the designated report coordinators."
    assert inventory._save(context, revised)["outcome"] == "saved"
    assert context.get("design_docs_save_attempts") == prior_failures + 1
    summary = persistence[2].await_args.kwargs["summary_payload"]
    assert summary["access_policy_review"]["policy_sha256"] == access_policy_sha256(revised["access_policy"])
    assert summary["access_policy_review"]["status"] == "approved"


def test_owner_correction_does_not_allow_an_extra_structural_failure(monkeypatch, persistence):
    context = ownership._context(managed=False)
    ui = _approve(monkeypatch, persistence, action="request_changes")
    invalid = _bundle()
    invalid["access_policy"]["grants"][0]["actions"] = ["create_report"]
    for _ in range(2):
        assert inventory._save(context, invalid)["outcome"] == "revise"
    assert inventory._save(context, _bundle())["outcome"] == "review_changes"
    assert context.get("design_docs_save_attempts") == 2

    rejected = inventory._save(context, invalid)
    assert rejected["outcome"] == "blocked"
    assert rejected["outcome_error"] == "attempts_exhausted"
    assert "omits canonical write grants" in rejected["error"]
    assert context.get("design_docs_save_attempts") == 3
    assert inventory._save(context, _bundle())["outcome_error"] == "attempts_exhausted"
    ui.assert_awaited_once()
    persistence[1].assert_not_called()
    persistence[2].assert_not_awaited()


@pytest.mark.parametrize("overrides", [{"review_id": "old-review"}, {"approved": True}, {"approved": "false"}])
def test_invalid_owner_correction_never_refunds_attempt(monkeypatch, persistence, overrides):
    context = ownership._context(managed=False)
    _approve(monkeypatch, persistence, action="request_changes", overrides=overrides)
    assert inventory._save(context, _bundle())["outcome"] == "blocked"
    assert context.get("design_docs_save_attempts") == 1
    assert inventory._save(context, _bundle())["outcome_error"] == "retry_not_permitted"
    persistence[1].assert_not_called()
    persistence[2].assert_not_awaited()


def test_builtin_cancel_is_the_only_cancel_control_and_never_persists(monkeypatch, persistence):
    ui = AsyncMock(return_value={"status": "cancelled", "action": "cancel"})
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    result = inventory._save(ownership._context(managed=False), _bundle())
    assert result["outcome"] == "blocked", result
    assert [action["id"] for action in ui.await_args.args[1]["actions"]] == ["approve", "request_changes"]
    persistence[1].assert_not_called()
    persistence[2].assert_not_awaited()


@pytest.mark.parametrize("overrides", [{"review_id": "old-draft"}, {"approved": False}, {"approved": "true"}])
def test_stale_or_malformed_review_fails_closed(monkeypatch, persistence, overrides):
    _approve(monkeypatch, persistence, overrides=overrides)
    result = inventory._save(ownership._context(managed=False), _bundle())
    assert result["outcome"] == "blocked", result
    persistence[1].assert_not_called()


def test_empty_policy_needs_no_review(monkeypatch, persistence):
    ui = AsyncMock(side_effect=AssertionError("No grants need review"))
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    context = ownership._context(managed=False)
    result = inventory._save(context, inventory._bundle(pricing=False))
    assert result["outcome"] == "saved", result
    ui.assert_not_awaited()
    summary = persistence[2].await_args.kwargs["summary_payload"]
    assert summary["access_policy"] == {"roles": [], "grants": []}
    assert summary["access_policy_review"]["status"] == "not_required"
    context.set("design_docs_artifact", _record(summary))
    assert resolve_design_access_policy(context) == {"roles": [], "grants": []}


@pytest.mark.parametrize("field,value,error", [
    ("role_id", "unknown", "unknown role"),
    ("collection", "missing", "unknown app-owned collection"),
    ("actions", ["create_report"], "omits canonical write"),
    ("actions", ["grant_admin"], "not an approved mutation"),
])
def test_invalid_policy_is_repaired_before_review(monkeypatch, persistence, field, value, error):
    ui = AsyncMock()
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    bundle = _bundle()
    bundle["access_policy"]["grants"][0][field] = value
    result = inventory._save(ownership._context(managed=False), bundle)
    assert result["outcome"] == "revise", result
    assert error in result["error"]
    ui.assert_not_awaited()
    persistence[1].assert_not_called()


def test_current_lifecycle_alone_does_not_approve_grants(monkeypatch, persistence):
    context, summary = _approved_context(monkeypatch, persistence)
    summary.pop("access_policy_review")
    context.set("design_docs_artifact", _record(summary))
    with pytest.raises(ValueError, match="review does not match"):
        resolve_design_access_policy(context)


def test_review_digest_cannot_approve_changed_policy(monkeypatch, persistence):
    context, summary = _approved_context(monkeypatch, persistence)
    summary["access_policy"]["roles"][0]["description"] = "Changed unreviewed authority"
    context.set("design_docs_artifact", _record(summary))
    with pytest.raises(ValueError, match="review does not match"):
        resolve_design_access_policy(context)


def test_refinement_draft_is_only_usable_by_its_bound_build(monkeypatch, persistence):
    context, summary = _approved_context(monkeypatch, persistence)
    record = {**_record(summary), "lifecycle_status": "draft"}
    context.set("design_docs_build_artifact", record)
    assert resolve_design_access_policy(context) == summary["access_policy"]
    record["commit_metadata"]["metadata"]["summary_payload"]["access_policy_review"]["build_id"] = "another-build"
    context.set("design_docs_build_artifact", record)
    with pytest.raises(ValueError, match="different build"):
        resolve_design_access_policy(context)


def test_review_from_another_app_cannot_grant_permissions(monkeypatch, persistence):
    context, summary = _approved_context(monkeypatch, persistence)
    context.set("design_docs_artifact", {**_record(summary), "app_id": "other-app"})
    with pytest.raises(ValueError, match="this app's DesignDocs"):
        resolve_design_access_policy(context)


def test_review_cannot_be_rebound_to_changed_collection_ownership(monkeypatch, persistence):
    context, _ = _approved_context(monkeypatch, persistence)
    contract = detach(context.get("data_contract"))
    contract["surfaces"][0]["collections"][0]["ownership"]["surface_id"] = "other_module"
    context.set("data_contract", contract)
    with pytest.raises(ValueError, match="does not match data_contract"):
        resolve_design_access_policy(context)


def test_candidate_state_cannot_self_grant():
    assert resolve_design_access_policy(None) is None
    assert resolve_design_access_policy({}) is None
    with pytest.raises(ValueError, match="no reviewed DesignDocs artifact"):
        resolve_design_access_policy({"access_policy": _bundle()["access_policy"]})


def test_only_runtime_can_load_review_artifacts_and_lifecycle_can_project_grants():
    definitions = yaml.safe_load((inventory.ROOT / "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(
        workflow_name="AppGenerator", definitions={key: definitions[key] for key in (
            "design_docs_artifact", "design_docs_build_artifact", "access_policy",
        )},
    )
    for key in ("design_docs_artifact", "design_docs_build_artifact"):
        assert policy.can_write(key, writer_id="runtime_system")
        for writer in ("caller_input", "agent_text", "context_bridge", "structured_output", "persisted_replay", "deterministic_tool"):
            assert not policy.can_write(key, writer_id=writer), (key, writer)
    assert policy.can_write("access_policy", writer_id="lifecycle_tool")
    assert not policy.can_write("access_policy", writer_id="context_bridge")
    assert not policy.can_write("access_policy", writer_id="persisted_replay")


def test_shared_role_projection_exposes_only_resolved_code_derived_permissions(monkeypatch, persistence):
    from factory_app.workflows._shared.design_access_policy import inject_design_access_policy
    from mozaiksai.core.workflow.generator_support.module_role_grants import compiled_role_grants

    context, summary = _approved_context(monkeypatch, persistence)
    before = detach(context.get("design_docs_artifact"))
    agent = SimpleNamespace(name="ConfigMiddlewareAgent", context_variables=context, _system_message="Original")
    inject_design_access_policy(agent, [])
    marker = "Code-derived authorization.roles (reference only; do not edit grants):\n"
    assert json.loads(agent._system_message.split(marker, 1)[1]) == compiled_role_grants(summary["access_policy"])
    assert "reports.update_report" in agent._system_message
    assert detach(context.get("design_docs_artifact")) == before
    original = agent._system_message
    inject_design_access_policy(agent, [])
    assert agent._system_message == original
    context.set("design_docs_artifact", None)
    with pytest.raises(ValueError, match="no reviewed DesignDocs artifact"):
        inject_design_access_policy(agent, [])
    assert agent._system_message == original


@pytest.mark.parametrize("operation", ["get", "list"])
def test_native_protected_read_closure_and_fixture_reuse_existing_approved_role(operation):
    from mozaiksai.core.workflow.generator_support.code_files import (
        extract_code_file_map_from_payload,
    )
    from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
        load_module_smoke_fixtures,
    )
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_app_runtime_role_smoke import _role_bundle
    from tests.test_generated_role_grants import _arguments, _closed

    payload = _closed()
    action_id = f"{operation}_tasks"
    action = next(item for item in payload["module_contract"]["module_yaml"]["actions"] if item["id"] == action_id)
    assert action["permissions"] == []
    permission = "tasks.update_task"
    action["permissions"] = [permission]
    if operation == "get":
        action["input_schema"] = {"type": "object", "properties": {"lookup": {"type": "string"}},
                                  "required": ["lookup"], "additionalProperties": False}
    params = [{"field": "lookup", "source": "record", "record_field": "task_id"}] if operation == "get" else []
    fixture = {"action": action_id, "roles": ["coordinator"], "setup": [], "params": params}
    payload["module_contract"]["fixtures_yaml"] = {"schema_version": "mozaiks.module_smoke_fixtures.v1", "fixtures": [fixture]}
    before = deepcopy(payload)
    closed = close_module_actions(payload, **_arguments())
    assert payload == before
    actual = next(item for item in closed["module_contract"]["module_yaml"]["actions"] if item["id"] == action_id)
    assert actual["permissions"] == [permission] and actual["input_schema"] == action["input_schema"]
    files = _role_bundle("app_wide")
    auth_before = files["config/auth.yaml"]
    files.update(extract_code_file_map_from_payload(closed))
    files["data/contract.json"] = json.dumps(_arguments()["data_contract"])
    admitted = load_module_smoke_fixtures(files)
    assert admitted["task_management", action_id].roles == ["coordinator"]
    assert files["config/auth.yaml"] == auth_before
    manifest_path = "modules/task_management/module.yaml"
    manifest = yaml.safe_load(files[manifest_path])
    next(item for item in manifest["actions"] if item["id"] == action_id)["permissions"] = []
    files[manifest_path] = yaml.safe_dump(manifest)
    with pytest.raises(ValueError, match="unrelated or redundant"):
        load_module_smoke_fixtures(files)
