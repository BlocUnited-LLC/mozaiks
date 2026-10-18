"""Residual app data needs a domain owner, even when a selected facade is app-owned."""

import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows._shared.surface_ownership import normalize_surface_ownership
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge


@pytest.fixture(autouse=True)
def _workspace_build_context(tmp_path, monkeypatch):
    """The workspace build context the launch loader registers packs from (MOZAIKS_BUILD_CONTEXT_PATH)."""
    workspace = tmp_path / "build_context"
    workspace.mkdir()
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(workspace))
    return workspace


def _context(facade_id="billing_portal", *, ownership_rules=(), platform_hints=()):
    """managed_billing registered in the workspace build context and selected as the projection selects it."""
    contract = {
        "contract_id": "managed_billing", "contract_type": "build_pack_instructions",
        # Facade declarations are authoritative even without ownership rules.
        "surface_ownership": list(ownership_rules),
        "facades": [{
            "module_id": facade_id, "provider_module": "billing_provider",
            "pages": [{"primary_actions": ["read_billing"]}],
        }],
    }
    root = Path(os.environ["MOZAIKS_BUILD_CONTEXT_PATH"]) / "managed_billing"
    root.mkdir(exist_ok=True)
    (root / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "managed_billing", "applies_to_workflows": ["DesignDocs", "AppGenerator"],
        "assets": [{"kind": "contract", "path": "contract.yaml"}],
        "pack": {"id": "managed_billing", "version": "1.0.0", "status": "active", "capability_source": "managed_capability"},
    }), encoding="utf-8")
    (root / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    return ContextVariablesBridge({
        "capability_packs": [{
            "id": "managed_billing", "capability_source": "managed_capability", "version": "1.0.0",
            "pack_source_path": str(root),
        }],
        "operator_contracts": [contract],
        "concept_blueprint": {"surface_candidate_hints": list(platform_hints)},
    })


def _surface(surface_id, *, owner="app", actions=()):
    return {
        "surface_id": surface_id, "surface_kind": "module", "owner": owner,
        "primary_entities": [], "owned_mutations": list(actions),
    }


def _design(*surfaces, residual_field="preferences"):
    fields = [{"name": name, "type": "string", "required": True}
              for name in ("user_id", "email", "role")]
    if residual_field:
        fields.append({
            "name": residual_field, "type": "object" if residual_field == "preferences" else "integer",
            "required": False,
        })
    return {"surfaces": [_surface("auth"), *surfaces]}, {"surfaces": [{
        "surface_id": "auth", "surface_kind": "module", "collections": [{
            "name": "users", "ownership": {"surface_id": "auth", "surface_kind": "module"},
            "fields": fields, "indexes": [],
        }],
    }]}


def _assert_task_split(surface_map, data_contract, residual_field):
    assert next(surface for surface in surface_map["surfaces"]
                if surface["surface_id"] == "auth")["owner"] == "platform"
    groups = {group["surface_id"]: group["collections"] for group in data_contract["surfaces"]}
    assert groups["auth"] == []
    assert len(groups["tasks"]) == 1
    residual = groups["tasks"][0]
    assert residual["name"] == "users"
    assert residual["ownership"] == {"surface_id": "tasks", "surface_kind": "module"}
    assert {field["name"] for field in residual["fields"]} == {"user_id", residual_field}
    assert any(index["unique"] and index["keys"] == [{"field": "user_id", "order": 1}]
               for index in residual["indexes"])
    assert all(not collections for surface_id, collections in groups.items() if surface_id != "tasks")


@pytest.mark.parametrize("facade_id", ["billing_portal", "account_portal"])
@pytest.mark.parametrize("residual_field", ["preferences", "task_count"])
def test_live_selected_facade_does_not_compete_for_residual_app_data(facade_id, residual_field):
    context = _context(facade_id)
    assert isinstance(context, ContextVariablesBridge)
    facade = _surface(facade_id, actions=["read_billing"])
    surface_map, data_contract = _design(_surface("tasks"), facade, residual_field=residual_field)
    expected_facade = deepcopy(facade)

    records = normalize_surface_ownership(surface_map, context_variables=context, data_contract=data_contract)

    _assert_task_split(surface_map, data_contract, residual_field)
    assert expected_facade in surface_map["surfaces"]
    assert records[0]["split_collections"][0]["target_surface_id"] == "tasks"


def test_live_facade_exclusion_loads_selected_pack_contract_from_source_path():
    """The facade comes from the verified source's own contract, not from a projected copy."""
    context = _context()
    context.set("operator_contracts", [])
    surface_map, data_contract = _design(_surface("tasks"), _surface("billing_portal", actions=["read_billing"]))

    normalize_surface_ownership(surface_map, context_variables=context, data_contract=data_contract)

    _assert_task_split(surface_map, data_contract, "preferences")


@pytest.mark.parametrize("fault", ["unregistered", "id_only"])
def test_an_unverifiable_selected_managed_pack_refuses_normalization(monkeypatch, fault):
    """A managed selection the resolver cannot verify is never silently omitted, or replaced by its projection."""
    context = _context()
    if fault == "unregistered":
        monkeypatch.delenv("MOZAIKS_BUILD_CONTEXT_PATH")
    else:
        context.set("capability_packs", [{"id": "managed_billing", "capability_source": "managed_capability"}])
    surface_map, data_contract = _design(_surface("tasks"), _surface("billing_portal", actions=["read_billing"]))
    submitted = deepcopy((surface_map, data_contract))

    with pytest.raises(ValueError, match="Selected managed pack 'managed_billing' cannot be verified"):
        normalize_surface_ownership(surface_map, context_variables=context, data_contract=data_contract)

    assert (surface_map, data_contract) == submitted


def test_live_two_domain_modules_still_reject_residual_ownership_atomically():
    context = _context()
    surface_map, data_contract = _design(_surface("tasks"), _surface("projects"))
    submitted = deepcopy((surface_map, data_contract))

    with pytest.raises(ValueError, match="candidates=\\['projects', 'tasks'\\]"):
        normalize_surface_ownership(surface_map, context_variables=context, data_contract=data_contract)

    assert (surface_map, data_contract) == submitted


def test_live_identity_only_users_still_normalize_without_residual_data():
    context = _context()
    surface_map, data_contract = _design(
        _surface("tasks"), _surface("billing_portal", actions=["read_billing"]), residual_field=None,
    )
    expected_other_surfaces = deepcopy(surface_map["surfaces"][1:])

    records = normalize_surface_ownership(surface_map, context_variables=context, data_contract=data_contract)

    assert surface_map["surfaces"][0]["owner"] == "platform"
    assert surface_map["surfaces"][1:] == expected_other_surfaces
    assert data_contract["surfaces"][0]["collections"] == []
    assert records == [{"surface_id": "auth", "owner": "platform", "removed_collections": ["users"]}]


def test_live_facade_without_domain_module_rejects_residual_ownership_atomically():
    context = _context()
    surface_map, data_contract = _design(_surface("billing_portal", actions=["read_billing"]))
    submitted = deepcopy((surface_map, data_contract))

    with pytest.raises(ValueError, match="candidates=\\[\\]"):
        normalize_surface_ownership(surface_map, context_variables=context, data_contract=data_contract)

    assert (surface_map, data_contract) == submitted


@pytest.mark.parametrize("other_owner", ["platform", "hosted", "platform_hint", "managed_rule"])
def test_live_non_domain_surfaces_do_not_compete_for_residual_app_data(other_owner):
    other = _surface("account_operations", owner=other_owner if other_owner in {"platform", "hosted"} else "app")
    rules = [{
        "owner": "Managed billing", "facade_module": "billing_portal", "surface_ids": ["account_operations"],
    }] if other_owner == "managed_rule" else []
    hints = [{"surface_id": "account_operations", "owner_hint": "platform"}] if other_owner == "platform_hint" else []
    context = _context(ownership_rules=rules, platform_hints=hints)
    surface_map, data_contract = _design(_surface("tasks"), other)

    normalize_surface_ownership(surface_map, context_variables=context, data_contract=data_contract)

    _assert_task_split(surface_map, data_contract, "preferences")
