"""Explicit unit-test review inputs; never live owner approval evidence."""
from copy import deepcopy

import yaml

from factory_app.workflows._shared.pack_selection import available_pack_rows, pin_pack_choices
from mozaiksai.core.workflow.generator_support.design_access_policy import access_policy_sha256
from tests.factory_context import factory_context


def registered_pack_fixture(tmp_path, monkeypatch, pack_id, capability_source, *, facades=()):
    """Declare a real temporary offering; this alone never selects/reviews it."""
    registry = tmp_path / "pack-registry"
    root = registry / pack_id
    root.mkdir(parents=True)
    config = {
        "context_id": pack_id, "applies_to_workflows": ["DesignDocs", "AppGenerator"],
        "assets": [{"path": "contract.yaml", "kind": "contract"}],
        "pack": {"id": pack_id, "version": "1.0.0", "status": "active", "capability_source": capability_source},
        "projections": {"context_variables": {"capability_packs": {"from": "capability_packs"}}},
    }
    contract = {"contract_id": pack_id, "contract_type": "build_pack_instructions"}
    if facades:
        config["facades"] = contract["facades"] = deepcopy(list(facades))
    (root / "context.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (root / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(registry))
    return available_pack_rows({}, pack_ids={pack_id})[pack_id]


def reviewed_pack_fixture(initial, pack_ids):
    """Bind explicitly named test packs to a deterministic no-grants review.

    Callers register any managed offering through the real test registry first.
    This supplies no roles, provider activation, payment or identity membership.
    Tests of approval/denial use the real review tool separately.
    """
    values = factory_context(deepcopy(initial))
    pins = pin_pack_choices(values, pack_ids)
    policy = {"roles": [], "grants": []}
    binding = values["run_build_binding"]
    summary = {
        "data_contract": values.get("data_contract"),
        "surface_map": values.get("design_surface_map"),
        "experience_spec": values.get("experience_spec"),
        "access_policy": policy,
        "access_policy_review": {
            "status": "approved", "review_id": "explicit-unit-fixture-review",
            "reviewed_by": "fixture-owner", "target_app_id": binding["target_app_id"],
            "build_id": binding["build_id"], "pack_identities": pins,
            "policy_sha256": access_policy_sha256(policy, values.get("experience_spec"), pack_identities=pins),
        },
    }
    values.update(workflow_name="AppGenerator", design_docs_artifact={
        "app_id": binding["target_app_id"], "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "current",
        "commit_metadata": {"author_user_id": "fixture-owner", "metadata": {"summary_payload": summary}},
    })
    return values
