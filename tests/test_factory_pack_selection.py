"""A catalog offering is not a build choice or a grant."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from factory_app.workflows._shared.pack_selection import (
    available_pack_rows,
    pin_pack_choices,
    rows_for_pack_choices,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackDependencyError,
    PackIntegrityError,
)
from mozaiksai.core.session.build_context import discover_pack_descriptors, load_build_context

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def installed_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(ROOT / "factory_app"))
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(ROOT / "factory_app/workflows"))
    empty = tmp_path / "empty-context"
    empty.mkdir()
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(empty))


def _managed_offering():
    root = ROOT / "factory_app/build_context/mozaikspay"
    return discover_pack_descriptors(root, load_build_context(root / "context.yaml"))[0]


def _enable_managed(tmp_path, monkeypatch):
    root = tmp_path / "registered-context"
    selection = root / "selection"
    selection.mkdir(parents=True)
    (selection / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "selection", "applies_to_workflows": ["AppGenerator"], "assets": [],
        "values": {"operator_capabilities": ["mozaikspay"]},
        "projections": {"context_variables": {"operator_capabilities": {"from": "operator_capabilities"}}},
    }))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))


def test_shipped_native_packs_are_available_but_not_selected_by_availability():
    context = {"capability_packs": []}
    before = deepcopy(context)
    catalog = available_pack_rows(context)
    assert {"social", "messaging", "support"} <= catalog.keys()
    assert "mozaikspay" not in catalog
    assert pin_pack_choices(context, []) == []
    assert rows_for_pack_choices(context, []) == []
    assert context == before


def test_operator_managed_offering_does_not_change_explicit_empty_selection(tmp_path, monkeypatch):
    context = {"capability_packs": [_managed_offering()]}
    with pytest.raises(ValueError):
        pin_pack_choices(context, ["mozaikspay"])
    _enable_managed(tmp_path, monkeypatch)
    assert "mozaikspay" in available_pack_rows(context)
    assert pin_pack_choices(context, []) == []
    assert rows_for_pack_choices(context, []) == []
    # This is a candidate only; acceptance requires the separate owner review.
    assert [row["pack_id"] for row in pin_pack_choices(context, ["mozaikspay"])] == ["mozaikspay"]


def test_choices_pin_stock_sources_and_require_dependency_closure():
    with pytest.raises(PackDependencyError, match="messaging"):
        pin_pack_choices({}, ["support"])
    pins = pin_pack_choices({}, ["support", "messaging"])
    assert {row["pack_id"] for row in pins} == {"support", "messaging"}
    assert all(set(row) == {"pack_id", "version", "digest"} for row in pins)
    rows = rows_for_pack_choices({}, pins)
    assert {row["id"] for row in rows} == {"support", "messaging"}


@pytest.mark.parametrize("ids", [["unknown_pack"], ["../../private"], ["mozaikspay"], ["support", "support"], "support"])
def test_unknown_unregistered_paths_and_duplicate_choices_fail(ids):
    with pytest.raises(ValueError):
        pin_pack_choices({}, ids)


def test_registry_additions_cannot_expand_an_existing_choice(tmp_path, monkeypatch):
    pins = pin_pack_choices({}, ["messaging"])
    _enable_managed(tmp_path, monkeypatch)
    rows = rows_for_pack_choices({"capability_packs": [_managed_offering()]}, pins)
    assert [row["id"] for row in rows] == ["messaging"]


def test_removed_managed_registration_and_changed_pins_require_renewed_review(tmp_path, monkeypatch):
    _enable_managed(tmp_path, monkeypatch)
    pins = pin_pack_choices({"capability_packs": [_managed_offering()]}, ["mozaikspay"])
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path / "empty-context"))
    with pytest.raises(ValueError, match="no longer available"):
        rows_for_pack_choices({}, pins)
    native = pin_pack_choices({}, ["messaging"])
    native[0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(PackIntegrityError, match="renew design review"):
        rows_for_pack_choices({}, native)


def test_missing_selection_and_unreviewed_extra_identity_fields_fail():
    with pytest.raises(ValueError, match="Explicit pack selection"):
        rows_for_pack_choices({}, None)
    pins = pin_pack_choices({}, ["messaging"])
    pins[0]["pack_source_path"] = "untrusted/path"
    with pytest.raises(ValidationError):
        rows_for_pack_choices({}, pins)


def test_selector_uses_the_existing_deterministic_tool_writer_only():
    from factory_app.workflows.DesignDocs.tools.select_capability_packs import (
        select_capability_packs,
    )
    from mozaiksai.core.workflow.agents.factory import (
        ContextVariablesBridge,
        _workflow_tool_invocation,
    )
    from mozaiksai.core.workflow.context.authority import (
        ContextAuthorityError,
        build_context_authority_policy,
    )

    declarations = yaml.safe_load((ROOT / "factory_app/workflows/DesignDocs/context_variables.yaml").read_text())
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=declarations["definitions"])
    context = ContextVariablesBridge({
        "workflow_name": "DesignDocs", "app_id": "host", "chat_id": "chat", "user_id": "owner",
        "run_build_binding": {"target_app_id": "target", "build_registry_id": "registry",
                              "build_id": "genesis", "phase": "genesis"},
    }, authority_policy=policy)
    context._bind_run(("DesignDocs", "host", "chat"), policy)
    with pytest.raises(ContextAuthorityError):
        context.set("pack_selection", [])
    with _workflow_tool_invocation(context):
        result = select_capability_packs([], context_variables=context)
    assert result == {"status": "selected", "pack_selection": [], "review_required": False, "approved": False}
    with pytest.raises(ContextAuthorityError):
        context.set("pack_selection", [])
    for writer in ("agent_text", "ui_response"):
        with pytest.raises(ContextAuthorityError):
            policy.require_can_write("pack_selection", writer_id=writer)


def test_false_subscription_contract_cannot_materialize_assignment_packs():
    from factory_app.workflows.AppGenerator.tools.app_build_plan import (
        _validate_monetization_provider_selection,
    )
    for pack_id in ("mozaikspay", "entitlement_dispatch"):
        with pytest.raises(ValueError, match="contract_required=true"):
            _validate_monetization_provider_selection([{"id": pack_id}], contract_required=False,
                                                      monetization_provider=None)


def test_physical_projection_cannot_fall_back_to_inventory_or_unreviewed_plan():
    from factory_app.workflows._shared.pack_selection import PhysicalPackContext
    from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge

    context = ContextVariablesBridge({"capability_packs": [_managed_offering()],
                                      "app_build_plan": {"capability_packs": [_managed_offering()]}})
    before = context.snapshot()
    with pytest.raises(ValueError, match="selected pack identities"):
        PhysicalPackContext(context)
    assert context.snapshot() == before

    context = ContextVariablesBridge({"capability_packs": [_managed_offering()],
                                      "app_build_plan": {"capability_packs": []}})
    before = context.snapshot()
    projected = PhysicalPackContext(context)
    assert projected.get("capability_packs") == []
    assert projected.get("app_build_plan")["capability_packs"] == []
    projected.get("capability_packs").append({"id": "injected"})
    assert projected.get("capability_packs") == []
    assert context.snapshot() == before


def test_unreviewed_plan_pack_is_denied_before_any_template_render(monkeypatch):
    from unittest.mock import Mock

    from factory_app.workflows.AppGenerator.tools import app_build_plan as planning
    from tests.test_app_plan_managed_facade_repair import _plan_and_context

    plan, context = _plan_and_context(monetized=False)
    plan["capability_packs"].append({"capability_pack_id": "messaging", "capability_source": "generated_module",
                                    "pack_source_path": str(ROOT / "factory_app/build_context/messaging")})
    renderer = Mock(side_effect=AssertionError("unreviewed templates must not render"))
    monkeypatch.setattr(planning, "_apply_selected_pack_files", renderer)
    with pytest.raises(ValueError, match="selected pack identities"):
        planning.app_build_plan(AppBuildPlan=plan, context_variables=context)
    renderer.assert_not_called()


def test_installed_component_is_available_through_existing_verified_state(tmp_path, monkeypatch):
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        install_verified_local_pack,
    )

    root = tmp_path / "components"
    root.mkdir()
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    source = ROOT / "tests/fixtures/community_packs/greetings"
    installed = install_verified_local_pack(pack_source_path=source, build_context_root=root)
    pins = pin_pack_choices({}, ["greetings"])
    assert pins[0]["digest"] == installed.digest
    assert rows_for_pack_choices({}, pins)[0]["pack_source_path"] == str(source.resolve())
    assert pin_pack_choices({}, []) == []
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path / "empty-context"))
    with pytest.raises(ValueError, match="no longer available"):
        rows_for_pack_choices({}, pins)


def test_appgen_prompt_does_not_select_catalog_offerings_or_keep_stale_contracts():
    from types import SimpleNamespace

    from factory_app.workflows.AppGenerator.tools.hook_managed_capabilities_context import (
        inject_managed_capabilities_context,
    )

    agent = SimpleNamespace(name="AppPlanAgent", context_variables={"capability_packs": [_managed_offering()]},
                            system_message="[MANAGED CAPABILITIES CONTEXT]\nSTALE PROVIDER INSTRUCTIONS")
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    inject_managed_capabilities_context(agent, [])
    assert "Authorized reusable packs: []" in agent.system_message
    assert "recommendations, not authorization" in agent.system_message
    assert "STALE PROVIDER INSTRUCTIONS" not in agent.system_message
    assert "mozaikspay" not in agent.system_message


def test_subscription_renderer_requires_a_reviewed_writer_and_never_assigns_pack_tasks():
    from factory_app.workflows._shared.subscription_contract_context import _render_contract

    contract = {"contract_required": True, "app_generator_instructions": ["STALE MODEL OWNED TASKS"]}
    absent = _render_contract(contract)
    assert "Return to DesignDocs" in absent
    assert "STALE MODEL OWNED TASKS" not in absent
    for writer, source in (("entitlement_dispatch", "generated_module"), ("custom_billing", "managed_capability")):
        rendered = _render_contract(contract, assignment_writers={writer: source})
        assert "Reviewed subscription assignment writer: " + writer in rendered
        assert "Do not create model-owned" in rendered
        assert "must also include three" not in rendered
        assert "STALE MODEL OWNED TASKS" not in rendered


def test_custom_verified_writer_requires_a_later_required_contract(tmp_path, monkeypatch):
    from factory_app.workflows._shared.pack_selection import (
        rows_for_pack_choices,
        validate_subscription_write_path,
    )
    from tests.test_designdocs_capability_ownership import _search_pack

    _search_pack(tmp_path, monkeypatch)
    path = tmp_path / "managed_search/contract.yaml"
    contract = yaml.safe_load(path.read_text())
    contract["provides_capabilities"] = ["subscription_write_path"]
    path.write_text(yaml.safe_dump(contract))
    rows = rows_for_pack_choices({}, pin_pack_choices({}, ["managed_search"]))
    for approved in (None, {"contract_required": False}):
        with pytest.raises(ValueError, match="contract_required=true"):
            validate_subscription_write_path(None, approved, rows=rows)
    assert validate_subscription_write_path(None, {"contract_required": True}, rows=rows) == {"managed_search": "managed_capability"}


@pytest.mark.asyncio
@pytest.mark.parametrize("approved_contract", [None, {"contract_required": False}])
async def test_durable_compilation_gate_refuses_reviewed_writer_without_required_contract(tmp_path, monkeypatch, approved_contract):
    import hashlib

    from mozaiksai.core.artifacts.models import BuildRecord
    from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes
    from mozaiksai.core.validation.compilation_inputs import _CompilerInputs, _context_from_inputs
    from tests.reviewed_pack_fixture import reviewed_pack_fixture
    from tests.test_app_compilation_inputs import Store
    from tests.test_designdocs_capability_ownership import _search_pack

    _search_pack(tmp_path, monkeypatch)
    path = tmp_path / "managed_search/contract.yaml"
    instructions = yaml.safe_load(path.read_text())
    instructions["provides_capabilities"] = ["subscription_write_path"]
    path.write_text(yaml.safe_dump(instructions))
    rows = rows_for_pack_choices({}, pin_pack_choices({}, ["managed_search"]))
    context = reviewed_pack_fixture({"app_build_plan": {"capability_packs": rows, "build_tasks": []}}, ["managed_search"])
    binding = context["run_build_binding"]
    summary = context["design_docs_artifact"]["commit_metadata"]["metadata"]["summary_payload"]
    store = Store()

    def record(record_id, family, workflow, payload):
        raw = _summary_bytes(payload)
        digest = hashlib.sha256(raw).hexdigest()
        stored = BuildRecord(id=record_id, app_id=binding["target_app_id"], build_family=family, build_key=family,
            version_number=1, lineage_root_id=record_id, source_workflow=workflow, lifecycle_status="current",
            files_manifest=[{"path": family + ".json", "sha256": digest, "size_bytes": len(raw)}],
            commit_metadata={"author_user_id": "fixture-owner", "metadata": {"summary_payload": payload}})
        store.records[record_id] = stored
        return {"record_id": record_id, "sha256": digest}

    design = record("design", "design_docs", "DesignDocs", summary)
    subscription = (record("subscription", "subscription_contract", "SubscriptionContractDesigner", approved_contract)
                    if approved_contract is not None else None)
    inputs = _CompilerInputs(binding=binding, app_build_plan=context["app_build_plan"],
                             app_task_batch_items=[], app_task_batch_results={},
                             design_docs=design, subscription_contract=subscription)
    with pytest.raises(ValueError, match="contract_required=true"):
        await _context_from_inputs(inputs, store=store, owner_user_id="fixture-owner", capability_packs=rows)


def test_clearing_subscription_contract_removes_previous_prompt_instructions():
    from types import SimpleNamespace

    from factory_app.workflows._shared.subscription_contract_context import (
        inject_subscription_contract_context,
    )

    context = {"subscription_contract": {"contract_required": False, "rationale": "OLD CONTRACT RATIONALE"}}
    agent = SimpleNamespace(name="AppPlanAgent", context_variables=context, system_message="Base")
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    inject_subscription_contract_context(agent, [])
    assert "OLD CONTRACT RATIONALE" in agent.system_message
    context["subscription_contract"] = None
    inject_subscription_contract_context(agent, [])
    assert "OLD CONTRACT RATIONALE" not in agent.system_message
    assert "No approved subscription contract is available" in agent.system_message
    assert agent._mozaiks_base_system_message == agent.system_message


def test_required_contract_without_reviewed_writer_cannot_publish_a_cached_plan(monkeypatch):
    from unittest.mock import Mock

    from factory_app.workflows.AppGenerator.tools import app_build_plan as planning
    from tests.appgenerator_subscription_fixture import subscription_contract
    from tests.test_app_plan_managed_facade_repair import _plan_and_context

    plan, context = _plan_and_context(monetized=False)
    context.set("subscription_contract", subscription_contract("task_management"))
    renderer = Mock(side_effect=AssertionError("no writer was reviewed"))
    monkeypatch.setattr(planning, "_apply_selected_pack_files", renderer)
    with pytest.raises(ValueError, match="monetization_provider is required"):
        planning.app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert context.get("app_build_plan") is None
    assert context.get("app_plan_ready") is False
    renderer.assert_not_called()
