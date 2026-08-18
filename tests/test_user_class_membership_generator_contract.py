from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
APPGEN_CONTEXT = REPO_ROOT / "factory_app" / "build_context" / "AppGenerator"
APPGEN_AGENTS = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
APPGEN_STRUCTURED_OUTPUTS = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml"
OSS_DOC = REPO_ROOT / "docs" / "architecture" / "app" / "user-classes-and-resource-relationships.md"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_fixture_yaml(files: dict[str, str], path: str) -> dict[str, Any]:
    return yaml.safe_load(files[path]) or {}


def _private_participation_generated_app_fixture() -> dict[str, str]:
    return {
        "modules/project_membership/module.yaml": dedent(
            """
            schema_version: mozaiks.module.v1
            module:
              id: project_membership
              display_name: Project Membership
              version: 1.0.0
              type: membership
              description: Resource-scoped project classes, invitations, route access, relationships, and deterministic policy inputs.
              owner: app
              visibility: private
              handler: backend.handler:ProjectMembershipHandler
              user_data_scope: true
            permissions:
              - id: project_membership.read
                description: Read current-user project membership summaries.
              - id: project_membership.manage
                description: Manage project invitations and class assignments.
            actions:
              - id: invite_member
                description: Invite a user to a project class.
                handler_method: invite_member
                api_surface: public
                input_schema:
                  type: object
                  required: [project_id, invitee_email, class_id]
                  properties:
                    project_id: { type: string }
                    invitee_email: { type: string }
                    class_id: { type: string, enum: [owner, builder, contributor, viewer] }
                output_schema:
                  type: object
                  required: [success, invitation_id]
                  properties:
                    success: { type: boolean }
                    invitation_id: { type: string }
                permissions: [project_membership.manage]
              - id: accept_invitation
                description: Accept an invitation for ctx.user_id.
                handler_method: accept_invitation
                api_surface: public
                input_schema:
                  type: object
                  required: [invitation_token]
                  properties:
                    invitation_token: { type: string }
                output_schema:
                  type: object
                  required: [success, project_id, class_id]
                  properties:
                    success: { type: boolean }
                    project_id: { type: string }
                    class_id: { type: string }
                permissions: []
              - id: update_member_class
                description: Assign or update a user's app-local class on a project.
                handler_method: update_member_class
                api_surface: public
                input_schema:
                  type: object
                  required: [project_id, member_user_id, class_id]
                  properties:
                    project_id: { type: string }
                    member_user_id: { type: string }
                    class_id: { type: string, enum: [owner, builder, contributor, viewer] }
                output_schema:
                  type: object
                  required: [success]
                  properties:
                    success: { type: boolean }
                permissions: [project_membership.manage]
              - id: list_members
                description: List project members for authorized project managers.
                handler_method: list_members
                api_surface: public
                input_schema:
                  type: object
                  required: [project_id]
                  properties:
                    project_id: { type: string }
                output_schema:
                  type: object
                  required: [members]
                  properties:
                    members:
                      type: array
                      items: { type: object }
                permissions: [project_membership.read]
              - id: get_my_membership
                description: Return the project membership summary for ctx.user_id.
                handler_method: get_my_membership
                api_surface: public
                input_schema:
                  type: object
                  required: [project_id]
                  properties:
                    project_id: { type: string }
                output_schema:
                  type: object
                  required: [project_id, class_id, capabilities]
                  properties:
                    project_id: { type: string }
                    class_id: { type: string }
                    capabilities:
                      type: array
                      items: { type: string }
                permissions: [project_membership.read]
              - id: authorize_project_route
                description: Return a minimal route access summary for ctx.user_id and a project route.
                handler_method: authorize_project_route
                api_surface: public
                input_schema:
                  type: object
                  required: [project_id]
                  properties:
                    project_id: { type: string }
                output_schema:
                  type: object
                  required: [allowed, status, capabilities]
                  properties:
                    allowed: { type: boolean }
                    status: { type: string }
                    capabilities:
                      type: array
                      items: { type: string }
                permissions: []
              - id: list_user_relationships
                description: List resource relationships for ctx.user_id.
                handler_method: list_user_relationships
                api_surface: internal
                input_schema:
                  type: object
                  properties:
                    resource_type: { type: string }
                    project_id: { type: string }
                output_schema:
                  type: object
                  required: [relationships]
                  properties:
                    relationships:
                      type: array
                      items: { type: object }
                permissions: []
              - id: evaluate_policy_hook
                description: Return deterministic participation inputs for a consuming module; this action does not write freeze records.
                handler_method: evaluate_policy_hook
                api_surface: internal
                input_schema:
                  type: object
                  required: [project_id, proposal_id]
                  properties:
                    project_id: { type: string }
                    proposal_id: { type: string }
                output_schema:
                  type: object
                  required: [inputs]
                  properties:
                    inputs:
                      type: array
                      items:
                        type: object
                        required: [user_id, class_id, vote_weight, distribution_weight]
                        properties:
                          user_id: { type: string }
                          class_id: { type: string }
                          vote_weight: { type: number }
                          distribution_weight: { type: number }
                          reason: { type: string }
                permissions: []
            capabilities: []
            """
        ).lstrip(),
        "modules/project_membership/contracts/relationships.yaml": dedent(
            """
            schema_version: mozaiks.relationships.v1
            providers:
              - id: project-class-relationships
                label: Project Class Relationships
                description: Resource-scoped owner, builder, contributor, and viewer classes for the authenticated user.
                order: 20
                action: list_user_relationships
                resource_types: [project]
                relationship_types: [owner, builder, contributor, viewer]
            """
        ).lstrip(),
        "modules/project_membership/contracts/policy_hooks.yaml": dedent(
            """
            schema_version: mozaiks.policy_hooks.v1
            hooks:
              - id: project-participation-inputs
                label: Project Participation Inputs
                description: Computes deterministic class-based vote and distribution inputs for a project decision.
                order: 20
                hook_type: decision_input
                action: evaluate_policy_hook
                resource_types: [project, proposal]
                input_schema:
                  type: object
                  required: [project_id, proposal_id]
                  properties:
                    project_id: { type: string }
                    proposal_id: { type: string }
                output_schema:
                  type: object
                  required: [inputs]
                  properties:
                    inputs:
                      type: array
                      items:
                        type: object
                        required: [user_id, class_id, vote_weight, distribution_weight]
                        properties:
                          user_id: { type: string }
                          class_id: { type: string }
                          vote_weight: { type: number }
                          distribution_weight: { type: number }
                deterministic: true
            """
        ).lstrip(),
        "modules/project_membership/contracts/settings.yaml": dedent(
            """
            schema_version: mozaiks.settings.v1
            settings:
              - id: class_catalog_version
                label: Class Catalog Version
                type: string
                default: private-project.classes.v1
            features:
              - id: participation_class_catalog
                label: Participation Class Catalog
                description: Configurable class defaults for private project participation.
                classes:
                  - id: owner
                    label: Owner
                    categories: [governance, economics]
                    governance_weight: 100
                    distribution_weight: 100
                  - id: builder
                    label: Builder
                    categories: [governance, economics]
                    governance_weight: 25
                    distribution_weight: 25
                  - id: contributor
                    label: Contributor
                    categories: [governance]
                    governance_weight: 10
                    distribution_weight: 0
                  - id: viewer
                    label: Viewer
                    categories: [access]
                    governance_weight: 0
                    distribution_weight: 0
            """
        ).lstrip(),
        "modules/project_governance/module.yaml": dedent(
            """
            schema_version: mozaiks.module.v1
            module:
              id: project_governance
              display_name: Project Governance
              version: 1.0.0
              type: workflow
              description: App-owned proposal voting that freezes participation inputs into its own decision record.
              owner: app
              visibility: private
              handler: backend.handler:ProjectGovernanceHandler
              user_data_scope: false
            permissions:
              - id: project_governance.read
                description: Read project proposals and outcomes.
              - id: project_governance.manage
                description: Open proposals and manage voting windows.
              - id: project_governance.vote
                description: Cast votes on open proposals.
            actions:
              - id: open_proposal
                description: Open a proposal and write a project_governance decision_record containing frozen_participation_inputs.
                handler_method: open_proposal
                api_surface: public
                input_schema:
                  type: object
                  required: [project_id, proposal_id]
                  properties:
                    project_id: { type: string }
                    proposal_id: { type: string }
                output_schema:
                  type: object
                  required: [success, decision_record_id]
                  properties:
                    success: { type: boolean }
                    decision_record_id: { type: string }
                permissions: [project_governance.manage]
              - id: cast_vote
                description: Cast a vote using the weight already frozen on the project_governance decision record.
                handler_method: cast_vote
                api_surface: public
                input_schema:
                  type: object
                  required: [proposal_id, choice]
                  properties:
                    proposal_id: { type: string }
                    choice: { type: string }
                output_schema:
                  type: object
                  required: [success]
                  properties:
                    success: { type: boolean }
                permissions: [project_governance.vote]
              - id: calculate_outcome
                description: Calculate the proposal outcome from votes and the stored decision record.
                handler_method: calculate_outcome
                api_surface: internal
                input_schema:
                  type: object
                  required: [proposal_id]
                  properties:
                    proposal_id: { type: string }
                output_schema:
                  type: object
                  required: [status]
                  properties:
                    status: { type: string }
                permissions: [project_governance.read]
            capabilities: []
            """
        ).lstrip(),
        "ui/pages/ProjectGovernance.yaml": dedent(
            """
            schema_version: mozaiks.app_page.v1
            name: Project Governance
            route: /projects/:projectId/governance
            title: Project Governance
            page_type: settings
            layout: full-width
            meta:
              requiresAuth: true
              routeAuth:
                module: project_membership
                action: authorize_project_route
                params:
                  project_id: $route.projectId
            sections:
              - id: governance-header
                primitive: PageHeader
                config:
                  title: Project Governance
            """
        ).lstrip(),
    }


def test_membership_archetype_is_host_agnostic_and_resource_scoped() -> None:
    archetypes = _read_yaml(APPGEN_CONTEXT / "module_archetypes.yaml")["archetypes"]

    assert "membership" in archetypes
    membership = archetypes["membership"]

    assert "resource-scoped user classes" in membership["summary"]
    assert "contracts/relationships.yaml" in membership["canonical_yaml_family"]["optional"]
    assert "contracts/policy_hooks.yaml" in membership["canonical_yaml_family"]["optional"]
    assert "contracts/settings.yaml" in membership["canonical_yaml_family"]["optional"]
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
        "evaluate_policy_hook",
    } <= key_action_ids

    constraints = "\n".join(membership["hard_constraints"])
    assert "/api/me remain platform-owned" in constraints
    assert "ctx.user_id" in constraints
    assert "resource_type/resource_id" in constraints
    assert "meta.routeAuth" in constraints
    assert "contracts/relationships.yaml" in constraints
    assert "contracts/policy_hooks.yaml" in constraints
    assert "contracts/settings.yaml" in constraints
    assert "Do not hardcode class defaults" in constraints
    assert "decision records or freeze semantics" in constraints

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
    assert "contracts/policy_hooks.yaml" in module_constraints
    assert "contracts/settings.yaml" in module_constraints
    assert "Do not hardcode class defaults" in module_constraints
    assert "consuming feature module owns any decision record" in module_constraints
    assert "Do not encode MozaiksPay" in module_constraints


def test_appgenerator_agents_prompt_resource_scoped_user_class_modules() -> None:
    agents = APPGEN_AGENTS.read_text(encoding="utf-8")

    assert "Resource-scoped user class modules" in agents
    assert "membership-style module using the injected `membership` module archetype" in agents
    assert "contracts/relationships.yaml" in agents
    assert "authorize_<resource>_route" in agents
    assert "module.user_data_scope=true" in agents
    assert "backend/account_data_handler.py" in agents
    assert "context.user_id" in agents
    assert "Never accept request-body `user_id`" in agents
    assert "safe routing inventory only" in agents
    assert "contracts/policy_hooks.yaml" in agents
    assert "contracts/settings.yaml" in agents
    assert "class labels/categories/default weights" in agents
    assert "consuming feature module owns any decision record" in agents


def test_membership_structured_output_description_keeps_freeze_records_out_of_membership() -> None:
    structured_outputs = _read_yaml(APPGEN_STRUCTURED_OUTPUTS)
    module_fields = structured_outputs["models"]["ModuleIdentity"]["fields"]
    module_type_description = module_fields["type"]["description"]

    assert "membership = resource-scoped user classes" in module_type_description
    assert "deterministic policy inputs" in module_type_description
    assert "consuming modules own any decision records or freeze semantics" in module_type_description
    assert "immutable snapshots for app-owned participation flows" not in module_type_description


def test_user_class_architecture_doc_is_linked_and_boundary_aware() -> None:
    index = (REPO_ROOT / "docs" / "architecture" / "app" / "index.md").read_text(encoding="utf-8")
    doc = OSS_DOC.read_text(encoding="utf-8")

    assert "user-classes-and-resource-relationships.md" in index
    assert "Authentication identifies the caller" in doc
    assert "App-specific user classes belong in app-owned modules" in doc
    assert "relationships.yaml" in doc
    assert "policy_hooks.yaml" in doc
    assert "routeAuth" in doc
    assert "No proprietary Mozaiks App billing, wallet, campaign, or payout policy" in doc


def test_private_participation_generated_app_fixture_uses_oss_primitives() -> None:
    from factory_app.workflows._shared.generated_ui_contract import audit_app_ui_bundle_integrity
    from mozaiksai.core.runtime.app.module_loader import (
        ModuleDefinition,
        ModulePolicyHooksManifest,
        ModuleRelationshipsManifest,
        ModuleSettingsManifest,
    )

    files = _private_participation_generated_app_fixture()

    membership = ModuleDefinition.model_validate(
        _read_fixture_yaml(files, "modules/project_membership/module.yaml")
    )
    governance = ModuleDefinition.model_validate(
        _read_fixture_yaml(files, "modules/project_governance/module.yaml")
    )
    relationships = ModuleRelationshipsManifest.model_validate(
        _read_fixture_yaml(files, "modules/project_membership/contracts/relationships.yaml")
    )
    policy_hooks = ModulePolicyHooksManifest.model_validate(
        _read_fixture_yaml(files, "modules/project_membership/contracts/policy_hooks.yaml")
    )
    settings = ModuleSettingsManifest.model_validate(
        _read_fixture_yaml(files, "modules/project_membership/contracts/settings.yaml")
    )

    assert membership.module.type == "membership"
    assert membership.module.user_data_scope is True
    assert governance.module.type == "workflow"

    membership_actions = {action.id: action for action in membership.actions}
    assert {
        "invite_member",
        "accept_invitation",
        "update_member_class",
        "list_members",
        "get_my_membership",
        "authorize_project_route",
        "list_user_relationships",
        "evaluate_policy_hook",
    } <= set(membership_actions)
    assert "user_id" not in membership_actions["accept_invitation"].input_schema["properties"]
    assert "user_id" not in membership_actions["authorize_project_route"].input_schema["properties"]

    relationship_provider = relationships.providers[0]
    assert relationship_provider.action == "list_user_relationships"
    assert relationship_provider.resource_types == ["project"]
    assert relationship_provider.relationship_types == ["owner", "builder", "contributor", "viewer"]

    policy_hook = policy_hooks.hooks[0]
    assert policy_hook.hook_type == "decision_input"
    assert policy_hook.action == "evaluate_policy_hook"
    assert policy_hook.resource_types == ["project", "proposal"]
    assert policy_hook.deterministic is True
    policy_input_fields = policy_hook.output_schema["properties"]["inputs"]["items"]["properties"]
    assert {"vote_weight", "distribution_weight"} <= set(policy_input_fields)

    class_catalog = settings.features[0]
    assert class_catalog["id"] == "participation_class_catalog"
    classes = {row["id"]: row for row in class_catalog["classes"]}
    assert classes["owner"]["governance_weight"] == 100
    assert classes["builder"]["distribution_weight"] == 25
    assert classes["viewer"]["governance_weight"] == 0

    governance_actions = {action.id: action for action in governance.actions}
    assert {"open_proposal", "cast_vote", "calculate_outcome"} <= set(governance_actions)
    assert "decision_record" in governance_actions["open_proposal"].description
    assert "frozen_participation_inputs" in governance_actions["open_proposal"].description

    page = _read_fixture_yaml(files, "ui/pages/ProjectGovernance.yaml")
    assert page["meta"]["routeAuth"] == {
        "module": "project_membership",
        "action": "authorize_project_route",
        "params": {"project_id": "$route.projectId"},
    }
    warnings = audit_app_ui_bundle_integrity(
        [{"path": path, "content": content} for path, content in files.items()],
        source_label="private participation fixture",
    )
    assert warnings == []

    membership_source = files["modules/project_membership/module.yaml"].lower()
    assert "decision_record" not in membership_source
    assert "frozen_participation_inputs" not in membership_source
    assert "default_class_weights" not in membership_source

    combined_source = "\n".join(files.values()).lower()
    for forbidden in (
        "mozaikspay",
        "stripe",
        "wallet",
        "payout",
        "hosted_billing",
        "hosted billing",
        "managed hosting",
        "growth campaign",
        "investor",
        "revenue-share",
        "mozaiks-app",
        "cloudflare",
        "azure",
    ):
        assert forbidden not in combined_source
