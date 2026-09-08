"""Shared v2 implementation-binding fixture derivation.

``mozaiks.implementation_binding.v2`` demands complete workflow, action, and
result realization for any graph that declares workflow-capability semantics.
Graph-level fixtures (materialization, rematerialization, renderer closure)
need structurally valid selections without a content store; this helper
derives them deterministically from the graph's own typed payloads with
content digests fabricated from stable identities.  Cold content-authority
tests never use this helper — they resolve real bytes through the #488 path.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from mozaiksai.core.runtime.app.layout_registry import PathScope
from mozaiksai.core.semantics.artifact_address import AccountedArtifact, ArtifactAddress
from mozaiksai.core.semantics.binding import (
    ActionInputBinding,
    CommitApprovalRequirement,
    ConstantSource,
    IdentityProjection,
    ModuleActionImplementationSelection,
    WorkflowImplementationSelection,
    WorkflowResultCommitBinding,
    WorkflowResultImplementationBinding,
)
from mozaiksai.core.semantics.implementation_artifacts import (
    HandlerCertificationMode,
    HandlerMethodSource,
    SelectedAccountedArtifact,
    SelectedContractArtifact,
)
from mozaiksai.core.semantics.payloads import (
    ActionPayload,
    ModulePayload,
    SemanticPayloadBase,
    WorkflowCapabilityBindingPayload,
    WorkflowCapabilityBindingRole,
    WorkflowCapabilityPayload,
    WorkflowPayload,
    WorkflowResultPayload,
)
from mozaiksai.core.semantics.refs import ChildContractRef
from mozaiksai.core.workflow.structured_output_contracts import (
    StructuredOutputContractRef,
)

_ACTION_ROLES = frozenset(
    {
        WorkflowCapabilityBindingRole.CONSUMES_ACTION,
        WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
    }
)


def _digest(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _workflow_document_selection(
    workflow: WorkflowPayload, *, path: str, family: str, schema_version: str
) -> SelectedContractArtifact:
    return SelectedContractArtifact(
        contract_ref=ChildContractRef(
            subject_id="fixture-app",
            subject_version=1,
            content_digest=_digest(f"{workflow.node_id}:{path}"),
            scope=workflow.scope,
            artifact_family=family,
            canonical_relative_path=path,
            contract_schema_version=schema_version,
        ),
        address=ArtifactAddress(
            path_scope=PathScope.WORKFLOW_RELATIVE,
            placeholder_values=(("workflow_id", workflow.workflow_id),),
            path=path,
        ),
    )


def derive_v2_selection_fields(
    payloads: Iterable[SemanticPayloadBase],
) -> dict[str, tuple]:
    """Derive the complete v2 selection fields one graph's payloads demand.

    Returns ``build_implementation_binding`` keyword fields:
    ``workflow_implementation_selections``,
    ``module_action_implementation_selections``, and
    ``workflow_result_bindings``.
    """
    payload_by_node = {payload.node_id: payload for payload in payloads}
    capabilities = [
        payload
        for payload in payload_by_node.values()
        if isinstance(payload, WorkflowCapabilityPayload)
    ]
    bindings = [
        payload
        for payload in payload_by_node.values()
        if isinstance(payload, WorkflowCapabilityBindingPayload)
    ]

    workflow_selections = []
    for workflow_node_id in sorted(
        {capability.workflow_node_id for capability in capabilities}
    ):
        workflow = payload_by_node[workflow_node_id]
        assert isinstance(workflow, WorkflowPayload)
        workflow_selections.append(
            WorkflowImplementationSelection(
                workflow_node_id=workflow_node_id,
                orchestrator_selection=_workflow_document_selection(
                    workflow,
                    path="orchestrator.yaml",
                    family="workflow_manifest",
                    schema_version="mozaiks.orchestrator.v1",
                ),
                structured_outputs_selection=_workflow_document_selection(
                    workflow,
                    path="structured_outputs.yaml",
                    family="workflow_config",
                    schema_version="mozaiks.structured_outputs.v1",
                ),
            )
        )

    demanded_actions: dict[str, str] = {}
    for binding in bindings:
        if binding.binding_role in _ACTION_ROLES and binding.module_action is not None:
            demanded_actions[binding.module_action.action_node_id] = (
                binding.module_action.module_node_id
            )
    action_selections = []
    for action_node_id in sorted(demanded_actions):
        module = payload_by_node[demanded_actions[action_node_id]]
        assert isinstance(module, ModulePayload)
        module_placeholder = (("module_id", module.module_id),)
        action_selections.append(
            ModuleActionImplementationSelection(
                action_node_id=action_node_id,
                module_selection=SelectedContractArtifact(
                    contract_ref=ChildContractRef(
                        subject_id="fixture-app",
                        subject_version=1,
                        content_digest=_digest(f"{module.node_id}:module.yaml"),
                        scope=module.scope,
                        artifact_family="module_manifest",
                        canonical_relative_path="module.yaml",
                        contract_schema_version="mozaiks.module.v1",
                    ),
                    address=ArtifactAddress(
                        path_scope=PathScope.MODULE_RELATIVE,
                        placeholder_values=module_placeholder,
                        path="module.yaml",
                    ),
                ),
                handler_selection=SelectedAccountedArtifact(
                    scope=module.scope,
                    artifact=AccountedArtifact(
                        address=ArtifactAddress(
                            path_scope=PathScope.MODULE_RELATIVE,
                            placeholder_values=module_placeholder,
                            path="backend/handler.py",
                        ),
                        content_digest=_digest(f"{module.node_id}:handler.py"),
                    ),
                ),
                certification_mode=HandlerCertificationMode.EXPLICIT_HANDLER,
                method_source=HandlerMethodSource.HANDLER,
                implementation_digest=_digest(f"{action_node_id}:implementation"),
            )
        )

    commits_by_result: dict[str, list[WorkflowCapabilityBindingPayload]] = {}
    for binding in bindings:
        if (
            binding.binding_role
            is WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION
            and binding.workflow_result_node_id is not None
        ):
            commits_by_result.setdefault(binding.workflow_result_node_id, []).append(
                binding
            )
    result_bindings = []
    for payload in payload_by_node.values():
        if not isinstance(payload, WorkflowResultPayload):
            continue
        commit_bindings = []
        for binding in commits_by_result.get(payload.node_id, ()):
            module_action = binding.module_action
            assert module_action is not None
            action = payload_by_node[module_action.action_node_id]
            assert isinstance(action, ActionPayload)
            commit_bindings.append(
                WorkflowResultCommitBinding(
                    workflow_capability_binding_node_id=binding.node_id,
                    approval_requirement=CommitApprovalRequirement.AUTOMATIC,
                    action_inputs=tuple(
                        ActionInputBinding(
                            target_property=prop.name,
                            source=ConstantSource(value="fixture"),
                        )
                        for prop in action.request_contract.properties
                        if prop.required
                    ),
                )
            )
        result_bindings.append(
            WorkflowResultImplementationBinding(
                workflow_result_node_id=payload.node_id,
                structured_output_contract_ref=StructuredOutputContractRef(
                    workflow_name="FixtureWorkflow",
                    model_id="FixtureOutput",
                    schema_digest=_digest(f"{payload.node_id}:schema"),
                ),
                projection=IdentityProjection(),
                commit_bindings=tuple(commit_bindings),
            )
        )

    return {
        "workflow_implementation_selections": tuple(workflow_selections),
        "module_action_implementation_selections": tuple(action_selections),
        "workflow_result_bindings": tuple(
            sorted(result_bindings, key=lambda item: item.workflow_result_node_id)
        ),
    }
