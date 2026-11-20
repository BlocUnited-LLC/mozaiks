"""Read reuse cannot turn a prior admitted source into later authority."""
from copy import deepcopy
from importlib import import_module
from pathlib import Path
from shutil import copytree
from unittest.mock import Mock

import pytest
import yaml

from factory_app.workflows._shared import pack_selection as selection
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_reviewed_pack_selection,
)
from tests.test_app_plan_managed_facade_repair import _plan_and_context
from tests.test_continuous_deterministic_materialization import _load_models
from tests.test_factory_pack_selection import _enable_managed, _managed_offering


@pytest.fixture
def managed_registry(tmp_path, monkeypatch):
    _enable_managed(tmp_path, monkeypatch)
    return tmp_path / "registered-context/selection/context.yaml"


def _revoke(path):
    declaration = yaml.safe_load(path.read_text())
    declaration["values"]["operator_capabilities"] = []
    path.write_text(yaml.safe_dump(declaration))


def test_reads_are_copied_once_per_operation_and_registry_is_fresh_next_time(managed_registry, monkeypatch):
    context = {"capability_packs": [_managed_offering()]}
    pins = selection.pin_pack_choices(context, ["mozaikspay"])
    reader = Mock(wraps=selection.available_pack_rows)
    monkeypatch.setattr(selection, "available_pack_rows", reader)

    @selection.reviewed_pack_read_operation
    def inspect(*, context_variables):
        first = selection.rows_for_pack_choices(context_variables, pins)
        first[0]["version"] = "caller mutation"
        return selection.rows_for_pack_choices(context_variables, pins)

    assert inspect(context_variables=context)[0]["version"] == pins[0]["version"]
    assert reader.call_count == 1
    _revoke(managed_registry)
    with pytest.raises(ValueError, match="no longer available"):
        inspect(context_variables=context)
    assert reader.call_count == 2


def test_review_receipt_and_final_sources_remain_live_inside_operation(managed_registry):
    _, context = _plan_and_context()

    @selection.reviewed_pack_read_operation
    def inspect(*, context_variables):
        assert selection.selected_pack_rows(context_variables)
        record = deepcopy(context_variables.to_dict()["design_docs_artifact"])
        review = record["commit_metadata"]["metadata"]["summary_payload"]["access_policy_review"]
        review["reviewed_by"] = "different-owner"
        context_variables.set("design_docs_artifact", record)
        with pytest.raises(ValueError, match="explicit owner approval"):
            resolve_reviewed_pack_selection(context_variables)

    inspect(context_variables=context)


@pytest.mark.parametrize("change", ["registry", "digest"])
def test_source_change_before_ready_clears_the_cached_plan(managed_registry, monkeypatch, change):
    _load_models()
    review = import_module("factory_app.workflows.AppGenerator.tools.app_plan_review")
    source = managed_registry.parent.parent / "mozaikspay"
    if change == "digest":
        copytree(Path(__file__).resolve().parents[1] / "factory_app/build_context/mozaikspay", source)
    plan, context = _plan_and_context()
    if change == "digest":
        current_selection = import_module("factory_app.workflows._shared.pack_selection")
        selected = current_selection.available_pack_rows(context, pack_ids={"mozaikspay"})
        assert Path(selected["mozaikspay"]["pack_source_path"]).resolve() == source.resolve()
    publisher = review.app_build_plan
    published = False

    def publish_then_revoke(**kwargs):
        nonlocal published
        result = publisher(**kwargs)
        published = True
        if change == "registry":
            _revoke(managed_registry)
        else:
            contract = source / "contract.yaml"
            contract.write_text(contract.read_text() + "\n# changed after planning\n")
        return result

    monkeypatch.setattr(review, "app_build_plan", publish_then_revoke)
    result = review.review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert published, result
    assert result["outcome"] == "needs_revision", result
    if change == "registry":
        assert "no longer available" in result["error"]
    else:
        # Both the reviewed identity gate and the physical source verifier
        # reject this changed digest; whichever runs first supplies the error.
        assert any(message in result["error"] for message in (
            "Reviewed pack 'mozaikspay' changed; renew design review",
            "Pack 'mozaikspay' selected digest does not match freshly verified source",
        )), result
    assert context.get("app_build_plan") is None
    assert context.get("app_plan_ready") is False


def test_rejected_operation_does_not_retain_sources(managed_registry):
    context = {"capability_packs": [_managed_offering()]}
    pins = selection.pin_pack_choices(context, ["mozaikspay"])

    @selection.reviewed_pack_read_operation
    def inspect(*, context_variables):
        selection.rows_for_pack_choices(context_variables, pins)
        raise RuntimeError("rejected")

    with pytest.raises(RuntimeError, match="rejected"):
        inspect(context_variables=context)
    _revoke(managed_registry)
    with pytest.raises(ValueError, match="no longer available"):
        selection.rows_for_pack_choices(context, pins)


def test_nested_operation_rechecks_then_restores_only_its_parent_scope(managed_registry):
    context = {"capability_packs": [_managed_offering()]}
    pins = selection.pin_pack_choices(context, ["mozaikspay"])

    @selection.reviewed_pack_read_operation
    def inner(*, context_variables):
        return selection.rows_for_pack_choices(context_variables, pins)

    @selection.reviewed_pack_read_operation
    def outer(*, context_variables):
        assert selection.rows_for_pack_choices(context_variables, pins)
        _revoke(managed_registry)
        with pytest.raises(ValueError, match="no longer available"):
            inner(context_variables=context_variables)
        # The parent owns its earlier synchronous view; its final gate is fresh.
        assert selection.rows_for_pack_choices(context_variables, pins)
        with selection.fresh_pack_reads(), pytest.raises(ValueError, match="no longer available"):
            selection.rows_for_pack_choices(context_variables, pins)

    outer(context_variables=context)
    with pytest.raises(ValueError, match="no longer available"):
        selection.rows_for_pack_choices(context, pins)


def test_authored_policy_projects_the_same_fresh_authorization_once(managed_registry, monkeypatch):
    from mozaiksai.core.workflow.generator_support import design_access_policy as policy

    _, context = _plan_and_context()
    reader = Mock(wraps=policy.verified_pack_authorization)
    monkeypatch.setattr(policy, "verified_pack_authorization", reader)
    assert policy.resolve_authored_module_access_policy(context) == {"roles": [], "grants": []}
    assert reader.call_count == 1
    _revoke(managed_registry)
    with pytest.raises(ValueError, match="no longer available"):
        policy.resolve_authored_module_access_policy(context)
    assert reader.call_count == 2


def test_physical_projection_preserves_verified_plan_inputs_without_mutation(managed_registry):
    from mozaiksai.core.workflow.context.frozen import detach

    plan, context = _plan_and_context()
    context.set("app_build_plan", plan)
    view = selection.PhysicalPackContext(context)
    original = detach(context.get("app_build_plan"))
    assert view.get("app_build_plan") == original
    projected = view.get("app_build_plan")
    projected["capability_packs"][0]["primary_entities"] = ["caller mutation"]
    assert view.get("app_build_plan") == original
    context.set("app_build_plan", {**plan, "capability_packs": []})
    with pytest.raises(ValueError, match="selected pack identities differ"):
        selection.PhysicalPackContext(context)


@pytest.mark.parametrize("phase", ["templates", "workspace"])
def test_synchronous_assembly_phase_owns_fresh_reads_and_rechecks_revocation(managed_registry, monkeypatch, phase):
    assembly = import_module("factory_app.workflows.AppGenerator.tools.assemble_app_tasks")
    # Workflow tool loading can replace these modules after test collection.
    # Observe the active reader and operation scope that assembly will use.
    current_selection = import_module("factory_app.workflows._shared.pack_selection")

    plan, context = _plan_and_context()
    context.set("build_timestamp", "2026-01-01T00:00:00Z")
    catalog = Mock(wraps=current_selection.available_pack_rows)
    monkeypatch.setattr(current_selection, "available_pack_rows", catalog)

    def invoke():
        if phase == "workspace":
            return assembly._workspace_paths(plan, context_variables=context)
        return assembly._apply_managed_capability_templates(
            [], app_build_plan=plan, context_variables=context, ui_contributions=[],
        )

    first = invoke()
    assert first
    assert catalog.call_count == 1
    assert current_selection._PACK_READS.get() is None
    assert invoke() == first
    assert catalog.call_count == 2
    assert current_selection._PACK_READS.get() is None
    _revoke(managed_registry)
    with pytest.raises(ValueError, match="no longer available"):
        invoke()
    assert catalog.call_count == 3
    assert current_selection._PACK_READS.get() is None
