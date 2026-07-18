from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
APPGEN_CONTEXT = REPO_ROOT / "factory_app" / "build_context" / "AppGenerator"
OSS_DOC = REPO_ROOT / "docs" / "architecture" / "app" / "user-classes-and-resource-relationships.md"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_membership_archetype_is_host_agnostic_and_resource_scoped() -> None:
    archetypes = _read_yaml(APPGEN_CONTEXT / "module_archetypes.yaml")["archetypes"]

    assert "membership" in archetypes
    membership = archetypes["membership"]

    assert "resource-scoped user classes" in membership["summary"]
    assert "contracts/relationships.yaml" in membership["canonical_yaml_family"]["optional"]
    assert "backend/account_data_handler.py" in membership["backend_stub_defaults"]

    key_action_ids = {next(iter(item)) for item in membership["key_actions"]}
    assert {
        "invite_member",
        "accept_invitation",
        "update_member_class",
        "list_members",
        "get_my_membership",
        "authorize_resource_route",
        "list_user_relationships",
        "create_membership_snapshot",
    } <= key_action_ids

    constraints = "\n".join(membership["hard_constraints"])
    assert "/api/me remain platform-owned" in constraints
    assert "ctx.user_id" in constraints
    assert "resource_type/resource_id" in constraints
    assert "meta.routeAuth" in constraints
    assert "contracts/relationships.yaml" in constraints
    assert "immutable snapshot" in constraints

    forbidden = [
        "MozaiksPay",
        "wallet",
        "payout",
        "hosted billing",
        "managed hosting",
        "growth campaign returns",
        "investor marketplace policy",
        "hosted product revenue-share",
    ]
    for term in forbidden:
        assert term in constraints


def test_file_contracts_require_membership_module_for_durable_user_classes() -> None:
    contracts = _read_yaml(APPGEN_CONTEXT / "file_contracts.yaml")
    module_constraints = "\n".join(contracts["task_contracts"]["module_contract"]["hard_constraints"])

    assert "durable user classes" in module_constraints
    assert "membership-style module" in module_constraints
    assert "resource-scoped class assignment records" in module_constraints
    assert "ctx.user_id" in module_constraints
    assert "Do not trust request body user_id" in module_constraints
    assert "meta.routeAuth" in module_constraints
    assert "snapshot eligible class/weight rows" in module_constraints
    assert "Do not encode MozaiksPay" in module_constraints


def test_user_class_architecture_doc_is_linked_and_boundary_aware() -> None:
    index = (REPO_ROOT / "docs" / "architecture" / "app" / "index.md").read_text(encoding="utf-8")
    doc = OSS_DOC.read_text(encoding="utf-8")

    assert "user-classes-and-resource-relationships.md" in index
    assert "Authentication identifies the caller" in doc
    assert "App-specific user classes belong in app-owned modules" in doc
    assert "relationships.yaml" in doc
    assert "routeAuth" in doc
    assert "No proprietary Mozaiks App billing, wallet, campaign, or payout policy" in doc
