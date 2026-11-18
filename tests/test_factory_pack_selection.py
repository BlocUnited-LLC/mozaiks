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
