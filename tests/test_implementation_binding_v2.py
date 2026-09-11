"""``mozaiks.implementation_binding.v2`` — exact implementation authority.

The positive fixture is one realistic graph-v2 application: a workflow with
one capability, two results (one committed, one advisory), a
``consumes_action`` binding onto a split-certified module action, and a
``commits_result_through_action`` binding onto an explicit-certified module
action — realized end to end against REAL exact workflow documents and REAL
exact module implementation bytes in a content store:

    SemanticGraph -> ImplementationBinding v2 -> graph validation
    -> cold implementation validation

The adversarial matrix proves the hostile document space fails closed, and the
digest matrix proves every meaning-bearing implementation choice moves
``binding_digest`` while ordering-only changes never do.
"""

from __future__ import annotations

import hashlib

import pydantic
import pytest
import yaml

from mozaiksai.core.artifacts.content_store import LocalArtifactContentStore
from mozaiksai.core.runtime.app.layout_registry import PathScope
from mozaiksai.core.semantics.artifact_address import AccountedArtifact, ArtifactAddress
from mozaiksai.core.semantics.binding import (
    IMPLEMENTATION_BINDING_SCHEMA_VERSION,
    RESULT_PROJECTION_PROFILE_VERSION,
    ActionInputBinding,
    CommitApprovalRequirement,
    ConstantSource,
    FieldsProjection,
    IdentityProjection,
    ImplementationBinding,
    ImplementationBindingError,
    ModuleActionImplementationSelection,
    RequestContextPropertySource,
    ResultPropertySource,
    WorkflowImplementationSelection,
    WorkflowResultCommitBinding,
    WorkflowResultImplementationBinding,
    build_implementation_binding,
    validate_implementation_binding_against_graph,
    validate_implementation_binding_content_authority,
)
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.semantics.closed_contracts import (
    ContractProperty,
    ObjectContract,
    ScalarContract,
)
from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticNodeKind,
    SemanticNodeV2,
    build_semantic_graph_v2,
)
from mozaiksai.core.semantics.implementation_artifacts import (
    HandlerCertificationMode,
    HandlerMethodSource,
    ModuleActionExportProof,
    SelectedAccountedArtifact,
    SelectedContractArtifact,
)
from mozaiksai.core.semantics.offline_projection import _node_id as _projection_node_id
from mozaiksai.core.semantics.payloads import (
    ActionPayload,
    ModuleActionRef,
    ModulePayload,
    SemanticPayloadBase,
    WorkflowCapabilityBindingPayload,
    WorkflowCapabilityBindingRole,
    WorkflowCapabilityPayload,
    WorkflowPayload,
    WorkflowResultPayload,
    build_semantic_payload,
    semantic_payload_ref,
)
from mozaiksai.core.semantics.refs import (
    ChildContractRef,
    ExecutionAccessScopeRef,
    SemanticGraphRef,
)
from mozaiksai.core.workflow.declarative.contracts import parse_structured_outputs_config
from mozaiksai.core.workflow.structured_output_contracts import (
    build_structured_output_contract_ref,
)

SCOPE = ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="workspace")
OTHER_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="elsewhere")

_WORKFLOW = "mozaiks.workflow.versionprobe"
_CAPABILITY = "mozaiks.workflow_capability.tasks_analysis"
_RESULT = "mozaiks.workflow_result.analysis_result"
_ADVISORY = "mozaiks.workflow_result.advisory_summary"
_BINDING_READ = "mozaiks.workflow_capability_binding.reads_note"
_BINDING_COMMIT = "mozaiks.workflow_capability_binding.stores_result"
_TASKS_MODULE = "mozaiks.module.tasks"
_NOTES_MODULE = "mozaiks.module.notes"
# The EXACT node identities the canonical offline projection generates for
# these module actions — digest-suffixed slugs, not parseable module prefixes.
# ImplementationBinding must resolve them through the typed
# ``ActionPayload.action_id`` only; node-id format carries no meaning.
_ACTION_CREATE = _projection_node_id(SemanticNodeKind.ACTION, "tasks_create_task")
_ACTION_GET = _projection_node_id(SemanticNodeKind.ACTION, "notes_get_note")

ORCHESTRATOR_DOCUMENT = {
    "schema_version": "mozaiks.orchestrator.v1",
    "workflow_name": "VersionProbe",
    "workflow_startup_mode": "AgentDriven",
}

STRUCTURED_OUTPUTS_DOCUMENT = {
    "schema_version": "mozaiks.structured_outputs.v1",
    "registry": {"Author": "Output"},
    "models": {
        "Output": {
            "type": "model",
            "fields": {"title": {"type": "str"}, "message": {"type": "str"}},
        }
    },
}

CREATE_TASK_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "source": {"type": "string"},
        "priority": {"type": "integer"},
    },
    "required": ["title"],
}

GET_NOTE_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"note_id": {"type": "string"}},
    "required": ["note_id"],
}

TASKS_MODULE_DOCUMENT = {
    "schema_version": "mozaiks.module.v1",
    "module": {"id": "tasks", "handler": "backend.handler:TasksHandler"},
    "actions": [
        {
            "id": "create_task",
            "description": "Create one task.",
            "handler_method": "create_task",
            "input_schema": CREATE_TASK_INPUT_SCHEMA,
        }
    ],
}

NOTES_MODULE_DOCUMENT = {
    "schema_version": "mozaiks.module.v1",
    "module": {"id": "notes", "handler": "backend.handler:NotesHandler"},
    "actions": [
        {
            "id": "get_note",
            "description": "Read one note.",
            "handler_method": "get_note",
            "input_schema": GET_NOTE_INPUT_SCHEMA,
        }
    ],
}

TASKS_HANDLER_SOURCE = (
    '"""Tasks handler."""\n'
    "\n"
    "class TasksHandler:\n"
    "    async def create_task(self, ctx, payload):\n"
    '        return {"status": "created"}\n'
)

NOTES_LEAF_SOURCE = (
    '"""Notes handler leaf."""\n'
    "\n"
    "from .base_handler import BaseNotesHandler\n"
    "\n"
    "class NotesHandler(BaseNotesHandler):\n"
    "    pass\n"
)

NOTES_BASE_SOURCE = (
    '"""Notes base handler."""\n'
    "\n"
    "class BaseNotesHandler:\n"
    "    async def get_note(self, ctx, payload):\n"
    '        return {"status": "found"}\n'
)

NOTES_PACK_CONTRACT_DOCUMENT = {
    "contract_type": "build_pack_instructions",
    "contract_id": "notes_pack",
    "required_outputs": [
        {"path": "modules/notes/module.yaml", "owner": "templates"},
        {"path": "modules/notes/backend/handler.py", "owner": "workspace"},
        {"path": "modules/notes/backend/base_handler.py", "owner": "templates"},
    ],
}


def _yaml_bytes(document: dict) -> bytes:
    return yaml.safe_dump(document, sort_keys=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def content_store(tmp_path):
    return LocalArtifactContentStore(root=tmp_path)


async def _put(content_store, data: bytes) -> str:
    digest = _sha(data)
    await content_store.put_blob(data, expected_digest=digest)
    return digest


def _workflow_document_selection(
    *, path: str, family: str, digest: str, schema_version: str,
    workflow: str = "versionprobe", scope: ExecutionAccessScopeRef = SCOPE,
) -> SelectedContractArtifact:
    return SelectedContractArtifact(
        contract_ref=ChildContractRef(
            subject_id="probe-app",
            subject_version=1,
            content_digest=digest,
            scope=scope,
            artifact_family=family,
            canonical_relative_path=path,
            contract_schema_version=schema_version,
        ),
        address=ArtifactAddress(
            path_scope=PathScope.WORKFLOW_RELATIVE,
            placeholder_values=(("workflow_id", workflow),),
            path=path,
        ),
    )


def _module_manifest_selection(
    *,
    digest: str,
    module: str,
    scope: ExecutionAccessScopeRef = SCOPE,
    app_root: bool = False,
) -> SelectedContractArtifact:
    # The canonical split sources resolve at app-bundle scope (the
    # module-relative base-handler row does not exist in the registry), so
    # split-mode fixtures address the whole module at app scope.
    path = f"modules/{module}/module.yaml" if app_root else "module.yaml"
    return SelectedContractArtifact(
        contract_ref=ChildContractRef(
            subject_id="probe-app",
            subject_version=1,
            content_digest=digest,
            scope=scope,
            artifact_family="module_manifest",
            canonical_relative_path=path,
            contract_schema_version="mozaiks.module.v1",
        ),
        address=ArtifactAddress(
            path_scope=PathScope.APP_BUNDLE_ROOT if app_root else PathScope.MODULE_RELATIVE,
            placeholder_values=() if app_root else (("module_id", module),),
            path=path,
        ),
    )


def _module_source_selection(
    *,
    digest: str,
    module: str,
    path: str,
    scope: ExecutionAccessScopeRef = SCOPE,
    app_root: bool = False,
) -> SelectedAccountedArtifact:
    return SelectedAccountedArtifact(
        scope=scope,
        artifact=AccountedArtifact(
            address=ArtifactAddress(
                path_scope=PathScope.APP_BUNDLE_ROOT if app_root else PathScope.MODULE_RELATIVE,
                placeholder_values=() if app_root else (("module_id", module),),
                path=f"modules/{module}/{path}" if app_root else path,
            ),
            content_digest=digest,
        ),
    )


def _pack_contract_selection(
    *, digest: str, scope: ExecutionAccessScopeRef = SCOPE
) -> SelectedAccountedArtifact:
    return SelectedAccountedArtifact(
        scope=scope,
        artifact=AccountedArtifact(
            address=ArtifactAddress(
                path_scope=PathScope.WORKSPACE_ROOT,
                placeholder_values=(),
                path="build_context/notes_pack/contract.yaml",
            ),
            content_digest=digest,
        ),
    )


def _payloads(
    *,
    create_node_id: str = _ACTION_CREATE,
    create_action_id: str = "create_task",
) -> dict[str, SemanticPayloadBase]:
    payloads: dict[str, SemanticPayloadBase] = {}

    def _add(payload: SemanticPayloadBase) -> None:
        payloads[payload.node_id] = payload

    _add(
        build_semantic_payload(
            ModulePayload, node_id=_TASKS_MODULE, payload_version=1, scope=SCOPE,
            module_id="tasks", description="Durable task facts",
        )
    )
    _add(
        build_semantic_payload(
            ModulePayload, node_id=_NOTES_MODULE, payload_version=1, scope=SCOPE,
            module_id="notes", description="Durable note facts",
        )
    )
    _add(
        build_semantic_payload(
            ActionPayload, node_id=create_node_id, payload_version=1, scope=SCOPE,
            action_id=create_action_id,
            description="Create one task",
            request_contract=import_closed_contract_schema(CREATE_TASK_INPUT_SCHEMA),
        )
    )
    _add(
        build_semantic_payload(
            ActionPayload, node_id=_ACTION_GET, payload_version=1, scope=SCOPE,
            action_id="get_note",
            description="Read one note",
            request_contract=import_closed_contract_schema(GET_NOTE_INPUT_SCHEMA),
        )
    )
    _add(
        build_semantic_payload(
            WorkflowPayload, node_id=_WORKFLOW, payload_version=1, scope=SCOPE,
            workflow_id="versionprobe", description="Analyze notes into tasks",
            startup_mode=None, topology=None,
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityPayload, node_id=_CAPABILITY, payload_version=1,
            scope=SCOPE, capability_id="tasks.analysis",
            description="Analyze one note", workflow_node_id=_WORKFLOW,
        )
    )
    _add(
        build_semantic_payload(
            WorkflowResultPayload, node_id=_RESULT, payload_version=1, scope=SCOPE,
            result_id="analysis_result", description="One committed analysis",
            workflow_capability_node_id=_CAPABILITY,
        )
    )
    _add(
        build_semantic_payload(
            WorkflowResultPayload, node_id=_ADVISORY, payload_version=1, scope=SCOPE,
            result_id="advisory_summary", description="Advisory only",
            workflow_capability_node_id=_CAPABILITY,
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityBindingPayload, node_id=_BINDING_READ,
            payload_version=1, scope=SCOPE,
            binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
            workflow_capability_node_id=_CAPABILITY,
            module_action=ModuleActionRef(
                module_node_id=_NOTES_MODULE, action_node_id=_ACTION_GET
            ),
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityBindingPayload, node_id=_BINDING_COMMIT,
            payload_version=1, scope=SCOPE,
            binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
            workflow_capability_node_id=_CAPABILITY,
            module_action=ModuleActionRef(
                module_node_id=_TASKS_MODULE, action_node_id=create_node_id
            ),
            workflow_result_node_id=_RESULT,
        )
    )
    return payloads


def _graph(payloads: dict[str, SemanticPayloadBase]):
    from mozaiksai.core.semantics.payloads import (
        derive_workflow_capability_binding_edges,
        derive_workflow_result_edges,
    )

    # Module ownership edges come from the typed binding refs — never from
    # parsing action node-id text, which carries no semantic meaning.
    ownership: dict[str, str] = {}
    for payload in payloads.values():
        if (
            isinstance(payload, WorkflowCapabilityBindingPayload)
            and payload.module_action is not None
        ):
            ownership[payload.module_action.action_node_id] = (
                payload.module_action.module_node_id
            )
    edges: list[SemanticEdge] = [
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id=module_node_id,
            target_node_id=action_node_id,
        )
        for action_node_id, module_node_id in sorted(ownership.items())
    ]
    for payload in payloads.values():
        if isinstance(payload, WorkflowCapabilityPayload):
            edges.append(
                SemanticEdge(
                    kind=SemanticEdgeKind.DECLARES,
                    source_node_id=payload.workflow_node_id,
                    target_node_id=payload.node_id,
                )
            )
        elif isinstance(payload, WorkflowResultPayload):
            edges.extend(derive_workflow_result_edges(payload))
        elif isinstance(payload, WorkflowCapabilityBindingPayload):
            edges.extend(derive_workflow_capability_binding_edges(payload))
    nodes = [
        SemanticNodeV2(
            node_id=payload.node_id,
            kind=payload.payload_kind,
            payload_ref=semantic_payload_ref(payload),
        )
        for payload in payloads.values()
    ]
    return build_semantic_graph_v2(
        graph_id="binding-v2-proof", version=1, scope=SCOPE, nodes=nodes, edges=edges
    )


def _request_context_contract() -> ObjectContract:
    return ObjectContract(
        nullable=False,
        additional_properties=False,
        properties=(
            ContractProperty(
                name="origin",
                required=True,
                contract=ScalarContract(kind="string", nullable=False),
            ),
        ),
    )


def _output_contract_ref():
    return build_structured_output_contract_ref(
        workflow_name="VersionProbe",
        model_id="Output",
        configs={
            "VersionProbe": parse_structured_outputs_config(STRUCTURED_OUTPUTS_DOCUMENT)
        },
        exact_model_ids=frozenset(),
    )


async def _fixture(
    content_store,
    *,
    create_node_id: str = _ACTION_CREATE,
    create_action_id: str = "create_task",
) -> dict:
    """The realistic positive scenario over real exact bytes."""
    orchestrator_digest = await _put(content_store, _yaml_bytes(ORCHESTRATOR_DOCUMENT))
    outputs_digest = await _put(content_store, _yaml_bytes(STRUCTURED_OUTPUTS_DOCUMENT))
    tasks_module_digest = await _put(content_store, _yaml_bytes(TASKS_MODULE_DOCUMENT))
    notes_module_digest = await _put(content_store, _yaml_bytes(NOTES_MODULE_DOCUMENT))
    tasks_handler_digest = await _put(content_store, TASKS_HANDLER_SOURCE.encode())
    notes_leaf_digest = await _put(content_store, NOTES_LEAF_SOURCE.encode())
    notes_base_digest = await _put(content_store, NOTES_BASE_SOURCE.encode())
    notes_pack_digest = await _put(
        content_store, _yaml_bytes(NOTES_PACK_CONTRACT_DOCUMENT)
    )

    payloads = _payloads(
        create_node_id=create_node_id, create_action_id=create_action_id
    )
    graph = _graph(payloads)

    workflow_selection = WorkflowImplementationSelection(
        workflow_node_id=_WORKFLOW,
        orchestrator_selection=_workflow_document_selection(
            path="orchestrator.yaml",
            family="workflow_manifest",
            digest=orchestrator_digest,
            schema_version="mozaiks.orchestrator.v1",
        ),
        structured_outputs_selection=_workflow_document_selection(
            path="structured_outputs.yaml",
            family="workflow_config",
            digest=outputs_digest,
            schema_version="mozaiks.structured_outputs.v1",
        ),
    )
    create_proof = ModuleActionExportProof(
        mode=HandlerCertificationMode.EXPLICIT_HANDLER,
        method_source=HandlerMethodSource.HANDLER,
        handler_class="TasksHandler",
        handler_method="create_task",
        content_digest=tasks_handler_digest,
    )
    create_selection = ModuleActionImplementationSelection(
        action_node_id=create_node_id,
        module_selection=_module_manifest_selection(
            digest=tasks_module_digest, module="tasks"
        ),
        handler_selection=_module_source_selection(
            digest=tasks_handler_digest, module="tasks", path="backend/handler.py"
        ),
        certification_mode=HandlerCertificationMode.EXPLICIT_HANDLER,
        method_source=HandlerMethodSource.HANDLER,
        implementation_digest=create_proof.implementation_digest,
    )
    get_proof = ModuleActionExportProof(
        mode=HandlerCertificationMode.CANONICAL_BASE_HANDLER,
        method_source=HandlerMethodSource.BASE_HANDLER,
        handler_class="NotesHandler",
        handler_method="get_note",
        content_digest=notes_leaf_digest,
        base_handler_class="BaseNotesHandler",
        base_content_digest=notes_base_digest,
        split_contract_content_digest=notes_pack_digest,
    )
    get_selection = ModuleActionImplementationSelection(
        action_node_id=_ACTION_GET,
        module_selection=_module_manifest_selection(
            digest=notes_module_digest, module="notes", app_root=True
        ),
        handler_selection=_module_source_selection(
            digest=notes_leaf_digest, module="notes", path="backend/handler.py",
            app_root=True,
        ),
        base_handler_selection=_module_source_selection(
            digest=notes_base_digest, module="notes", path="backend/base_handler.py",
            app_root=True,
        ),
        pack_contract_selection=_pack_contract_selection(digest=notes_pack_digest),
        certification_mode=HandlerCertificationMode.CANONICAL_BASE_HANDLER,
        method_source=HandlerMethodSource.BASE_HANDLER,
        implementation_digest=get_proof.implementation_digest,
    )
    commit = WorkflowResultCommitBinding(
        workflow_capability_binding_node_id=_BINDING_COMMIT,
        approval_requirement=CommitApprovalRequirement.HUMAN_APPROVAL_REQUIRED,
        action_inputs=(
            ActionInputBinding(
                target_property="title",
                source=ResultPropertySource(property_name="title"),
            ),
            ActionInputBinding(
                target_property="source",
                source=RequestContextPropertySource(property_name="origin"),
            ),
            ActionInputBinding(
                target_property="priority", source=ConstantSource(value=3)
            ),
        ),
    )
    result_binding = WorkflowResultImplementationBinding(
        workflow_result_node_id=_RESULT,
        structured_output_contract_ref=_output_contract_ref(),
        projection=FieldsProjection(fields=("title",)),
        commit_bindings=(commit,),
    )
    advisory_binding = WorkflowResultImplementationBinding(
        workflow_result_node_id=_ADVISORY,
        structured_output_contract_ref=_output_contract_ref(),
        projection=IdentityProjection(),
        commit_bindings=(),
    )
    fields = {
        "binding_id": "binding-v2-proof",
        "version": 1,
        "scope": SCOPE,
        "semantic_graph_ref": SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=SCOPE,
        ),
        "request_context_contract": _request_context_contract(),
        "workflow_implementation_selections": (workflow_selection,),
        "module_action_implementation_selections": (create_selection, get_selection),
        "workflow_result_bindings": (result_binding, advisory_binding),
    }
    return {
        "graph": graph,
        "payloads": tuple(payloads.values()),
        "fields": fields,
        "binding": build_implementation_binding(**fields),
        "digests": {
            "orchestrator": orchestrator_digest,
            "outputs": outputs_digest,
            "tasks_module": tasks_module_digest,
            "notes_module": notes_module_digest,
            "tasks_handler": tasks_handler_digest,
            "notes_leaf": notes_leaf_digest,
            "notes_base": notes_base_digest,
            "notes_pack": notes_pack_digest,
        },
    }


def _rebuilt(fixture: dict, **overrides) -> ImplementationBinding:
    return build_implementation_binding(**{**fixture["fields"], **overrides})


def _replaced(model, **changes):
    return type(model).model_validate(
        {**model.model_dump(mode="json"), **{k: v for k, v in changes.items()}}
    )


# ---------------------------------------------------------------------------
# Positive acceptance
# ---------------------------------------------------------------------------


async def test_realistic_binding_passes_graph_and_cold_validation(content_store):
    fixture = await _fixture(content_store)
    binding = fixture["binding"]
    assert binding.schema_version == IMPLEMENTATION_BINDING_SCHEMA_VERSION
    assert binding.projection_profile_version == RESULT_PROJECTION_PROFILE_VERSION
    validate_implementation_binding_against_graph(
        binding, fixture["graph"], payloads=fixture["payloads"]
    )
    authority = await validate_implementation_binding_content_authority(
        binding,
        fixture["graph"],
        payloads=fixture["payloads"],
        content_store=content_store,
    )
    implementation = authority.workflow_implementations[_WORKFLOW]
    assert implementation.workflow_name == "VersionProbe"
    assert implementation.workflow_instance == "versionprobe"
    create = authority.module_action_implementations[_ACTION_CREATE]
    assert create.export_proof.mode is HandlerCertificationMode.EXPLICIT_HANDLER
    assert create.action.id == "create_task"
    get = authority.module_action_implementations[_ACTION_GET]
    assert get.export_proof.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER
    assert get.export_proof.method_source is HandlerMethodSource.BASE_HANDLER
    assert get.split_authority is not None
    model = authority.result_output_models[_RESULT]
    assert set(model.model_fields) == {"title", "message"}
    assert authority.result_output_models[_ADVISORY] is not None


async def test_cold_reparse_is_identical_and_v1_documents_reject(content_store):
    fixture = await _fixture(content_store)
    binding = fixture["binding"]
    assert ImplementationBinding.model_validate(binding.model_dump(mode="json")) == binding
    document = binding.model_dump(mode="json")
    document["schema_version"] = "mozaiks.implementation_binding.v1"
    with pytest.raises(pydantic.ValidationError):
        ImplementationBinding.model_validate(document)


async def test_binding_digest_tamper_fails_closed(content_store):
    fixture = await _fixture(content_store)
    document = fixture["binding"].model_dump(mode="json")
    document["binding_digest"] = "0" * 64
    with pytest.raises(pydantic.ValidationError, match="binding_digest"):
        ImplementationBinding.model_validate(document)


# ---------------------------------------------------------------------------
# Binding-digest mutation matrix
# ---------------------------------------------------------------------------


async def test_every_meaning_bearing_change_moves_binding_digest(content_store):
    fixture = await _fixture(content_store)
    base = fixture["binding"]
    workflow_selection = base.workflow_implementation_selections[0]
    get_selection, create_selection = base.module_action_implementation_selections
    advisory, result_binding = (
        base.workflow_result_bindings[0],
        base.workflow_result_bindings[1],
    )
    commit = result_binding.commit_bindings[0]
    other = _sha(b"other-content")

    def _selection_with_digest(selection: SelectedContractArtifact, digest: str):
        document = selection.model_dump(mode="json")
        document["contract_ref"]["content_digest"] = digest
        return SelectedContractArtifact.model_validate(document)

    def _accounted_with_digest(selection: SelectedAccountedArtifact, digest: str):
        document = selection.model_dump(mode="json")
        document["artifact"]["content_digest"] = digest
        return SelectedAccountedArtifact.model_validate(document)

    variants = {
        "orchestrator-digest": _rebuilt(
            fixture,
            workflow_implementation_selections=(
                _replaced(
                    workflow_selection,
                    orchestrator_selection=_selection_with_digest(
                        workflow_selection.orchestrator_selection, other
                    ).model_dump(mode="json"),
                ),
            ),
        ),
        "structured-outputs-digest": _rebuilt(
            fixture,
            workflow_implementation_selections=(
                _replaced(
                    workflow_selection,
                    structured_outputs_selection=_selection_with_digest(
                        workflow_selection.structured_outputs_selection, other
                    ).model_dump(mode="json"),
                ),
            ),
        ),
        "handler-digest": _rebuilt(
            fixture,
            module_action_implementation_selections=(
                _replaced(
                    create_selection,
                    handler_selection=_accounted_with_digest(
                        create_selection.handler_selection, other
                    ).model_dump(mode="json"),
                ),
                get_selection,
            ),
        ),
        "base-digest": _rebuilt(
            fixture,
            module_action_implementation_selections=(
                create_selection,
                _replaced(
                    get_selection,
                    base_handler_selection=_accounted_with_digest(
                        get_selection.base_handler_selection, other
                    ).model_dump(mode="json"),
                ),
            ),
        ),
        "pack-contract-digest": _rebuilt(
            fixture,
            module_action_implementation_selections=(
                create_selection,
                _replaced(
                    get_selection,
                    pack_contract_selection=_accounted_with_digest(
                        get_selection.pack_contract_selection, other
                    ).model_dump(mode="json"),
                ),
            ),
        ),
        "implementation-digest": _rebuilt(
            fixture,
            module_action_implementation_selections=(
                _replaced(create_selection, implementation_digest=other),
                get_selection,
            ),
        ),
        "method-source": _rebuilt(
            fixture,
            module_action_implementation_selections=(
                create_selection,
                _replaced(get_selection, method_source="handler"),
            ),
        ),
        "projection": _rebuilt(
            fixture,
            workflow_result_bindings=(
                _replaced(result_binding, projection={"projection_kind": "identity"}),
                advisory,
            ),
        ),
        "commit-approval": _rebuilt(
            fixture,
            workflow_result_bindings=(
                _replaced(
                    result_binding,
                    commit_bindings=(
                        _replaced(commit, approval_requirement="automatic").model_dump(
                            mode="json"
                        ),
                    ),
                ),
                advisory,
            ),
        ),
        "action-input-source": _rebuilt(
            fixture,
            workflow_result_bindings=(
                _replaced(
                    result_binding,
                    commit_bindings=(
                        _replaced(
                            commit,
                            action_inputs=[
                                item.model_dump(mode="json")
                                if item.target_property != "priority"
                                else {
                                    "target_property": "priority",
                                    "source": {"source_kind": "constant", "value": 4},
                                }
                                for item in commit.action_inputs
                            ],
                        ).model_dump(mode="json"),
                    ),
                ),
                advisory,
            ),
        ),
        "request-context-contract": _rebuilt(
            fixture,
            request_context_contract=ObjectContract(
                nullable=False,
                additional_properties=False,
                properties=(
                    ContractProperty(
                        name="origin",
                        required=True,
                        contract=ScalarContract(kind="string", nullable=False),
                    ),
                    ContractProperty(
                        name="locale",
                        required=False,
                        contract=ScalarContract(kind="string", nullable=False),
                    ),
                ),
            ),
        ),
    }
    digests = {name: variant.binding_digest for name, variant in variants.items()}
    digests["base"] = base.binding_digest
    assert len(set(digests.values())) == len(digests), digests


async def test_ordering_only_changes_never_move_binding_digest(content_store):
    fixture = await _fixture(content_store)
    fields = fixture["fields"]
    reordered = _rebuilt(
        fixture,
        module_action_implementation_selections=tuple(
            reversed(fields["module_action_implementation_selections"])
        ),
        workflow_result_bindings=tuple(reversed(fields["workflow_result_bindings"])),
    )
    assert reordered.binding_digest == fixture["binding"].binding_digest


# ---------------------------------------------------------------------------
# Adversarial matrix — schema boundary
# ---------------------------------------------------------------------------


def test_projection_grammar_rejects_every_traversal_form():
    for hostile in ("a.b", "a[0]", "*", "$.a", "a b", "", " a", "items[*]"):
        with pytest.raises(pydantic.ValidationError):
            FieldsProjection(fields=(hostile,))
        with pytest.raises(pydantic.ValidationError):
            ResultPropertySource(property_name=hostile)
        with pytest.raises(pydantic.ValidationError):
            RequestContextPropertySource(property_name=hostile)
    with pytest.raises(pydantic.ValidationError, match="duplicate projected"):
        FieldsProjection(fields=("title", "title"))


def test_split_mode_selection_shape_fails_closed(tmp_path):
    digest = _sha(b"content")
    module_selection = _module_manifest_selection(digest=digest, module="notes")
    handler = _module_source_selection(
        digest=digest, module="notes", path="backend/handler.py"
    )
    base = _module_source_selection(
        digest=digest, module="notes", path="backend/base_handler.py"
    )
    pack = _pack_contract_selection(digest=digest)
    with pytest.raises(pydantic.ValidationError, match="require the exact base"):
        ModuleActionImplementationSelection(
            action_node_id=_ACTION_GET,
            module_selection=module_selection,
            handler_selection=handler,
            certification_mode=HandlerCertificationMode.CANONICAL_BASE_HANDLER,
            method_source=HandlerMethodSource.BASE_HANDLER,
            implementation_digest=digest,
        )
    with pytest.raises(pydantic.ValidationError, match="require the exact base"):
        ModuleActionImplementationSelection(
            action_node_id=_ACTION_GET,
            module_selection=module_selection,
            handler_selection=handler,
            base_handler_selection=base,
            certification_mode=HandlerCertificationMode.CANONICAL_BASE_HANDLER,
            method_source=HandlerMethodSource.BASE_HANDLER,
            implementation_digest=digest,
        )
    with pytest.raises(pydantic.ValidationError, match="no base or pack-contract"):
        ModuleActionImplementationSelection(
            action_node_id=_ACTION_GET,
            module_selection=module_selection,
            handler_selection=handler,
            base_handler_selection=base,
            pack_contract_selection=pack,
            certification_mode=HandlerCertificationMode.EXPLICIT_HANDLER,
            method_source=HandlerMethodSource.HANDLER,
            implementation_digest=digest,
        )
    with pytest.raises(pydantic.ValidationError, match="handler source method"):
        ModuleActionImplementationSelection(
            action_node_id=_ACTION_GET,
            module_selection=module_selection,
            handler_selection=handler,
            certification_mode=HandlerCertificationMode.EXPLICIT_HANDLER,
            method_source=HandlerMethodSource.BASE_HANDLER,
            implementation_digest=digest,
        )


def test_cross_workflow_document_pair_rejects_at_the_selection_boundary():
    digest = _sha(b"content")
    with pytest.raises(pydantic.ValidationError, match="pairs documents"):
        WorkflowImplementationSelection(
            workflow_node_id=_WORKFLOW,
            orchestrator_selection=_workflow_document_selection(
                path="orchestrator.yaml",
                family="workflow_manifest",
                digest=digest,
                schema_version="mozaiks.orchestrator.v1",
                workflow="versionprobe",
            ),
            structured_outputs_selection=_workflow_document_selection(
                path="structured_outputs.yaml",
                family="workflow_config",
                digest=digest,
                schema_version="mozaiks.structured_outputs.v1",
                workflow="otherflow",
            ),
        )


async def test_runtime_authority_fields_cannot_enter_the_schema(content_store):
    fixture = await _fixture(content_store)
    commit = fixture["binding"].workflow_result_bindings[1].commit_bindings[0]
    with pytest.raises(pydantic.ValidationError):
        WorkflowResultCommitBinding(
            **commit.model_dump(mode="json"), approved_by="runtime-user"
        )
    with pytest.raises(pydantic.ValidationError):
        build_implementation_binding(**fixture["fields"], approval_state="granted")
    for model in (
        ImplementationBinding,
        WorkflowImplementationSelection,
        ModuleActionImplementationSelection,
        WorkflowResultImplementationBinding,
        WorkflowResultCommitBinding,
        ActionInputBinding,
    ):
        hostile = {
            "approval_state", "approver_id", "approved_by", "token",
            "runtime_user", "provider_id", "model_id", "agent_id", "task_id",
            "network_id", "channel_id",
        }
        assert not hostile & set(model.model_fields), model.__name__


async def test_cross_scope_selections_fail_closed(content_store):
    fixture = await _fixture(content_store)
    foreign_orchestrator = _workflow_document_selection(
        path="orchestrator.yaml",
        family="workflow_manifest",
        digest=fixture["digests"]["orchestrator"],
        schema_version="mozaiks.orchestrator.v1",
        scope=OTHER_SCOPE,
    )
    foreign_outputs = _workflow_document_selection(
        path="structured_outputs.yaml",
        family="workflow_config",
        digest=fixture["digests"]["outputs"],
        schema_version="mozaiks.structured_outputs.v1",
        scope=OTHER_SCOPE,
    )
    with pytest.raises(pydantic.ValidationError, match="different execution scope"):
        build_implementation_binding(
            **{
                **fixture["fields"],
                "workflow_implementation_selections": (
                    WorkflowImplementationSelection(
                        workflow_node_id=_WORKFLOW,
                        orchestrator_selection=foreign_orchestrator,
                        structured_outputs_selection=foreign_outputs,
                    ),
                ),
            }
        )
    foreign_module = _module_manifest_selection(
        digest=fixture["digests"]["tasks_module"], module="tasks", scope=OTHER_SCOPE
    )
    foreign_handler = _module_source_selection(
        digest=fixture["digests"]["tasks_handler"],
        module="tasks",
        path="backend/handler.py",
        scope=OTHER_SCOPE,
    )
    create_selection = fixture["binding"].module_action_implementation_selections[1]
    with pytest.raises(pydantic.ValidationError, match="different execution scope"):
        build_implementation_binding(
            **{
                **fixture["fields"],
                "module_action_implementation_selections": (
                    _replaced(
                        create_selection,
                        module_selection=foreign_module.model_dump(mode="json"),
                        handler_selection=foreign_handler.model_dump(mode="json"),
                    ),
                    fixture["binding"].module_action_implementation_selections[0],
                ),
            }
        )


# ---------------------------------------------------------------------------
# Adversarial matrix — typed graph validation
# ---------------------------------------------------------------------------


async def test_graph_validation_requires_payload_closure(content_store):
    fixture = await _fixture(content_store)
    with pytest.raises(ImplementationBindingError, match="payload"):
        validate_implementation_binding_against_graph(
            fixture["binding"], fixture["graph"]
        )


async def test_workflow_selection_completeness(content_store):
    fixture = await _fixture(content_store)
    absent = _rebuilt(fixture, workflow_implementation_selections=())
    with pytest.raises(ImplementationBindingError, match="absent workflow implementation"):
        validate_implementation_binding_against_graph(
            absent, fixture["graph"], payloads=fixture["payloads"]
        )
    with pytest.raises(pydantic.ValidationError, match="duplicate workflow implementation"):
        _rebuilt(
            fixture,
            workflow_implementation_selections=(
                fixture["binding"].workflow_implementation_selections[0],
                fixture["binding"].workflow_implementation_selections[0],
            ),
        )
    stray = _replaced(
        fixture["binding"].workflow_implementation_selections[0],
        workflow_node_id=_TASKS_MODULE,
    )
    widened = _rebuilt(
        fixture,
        workflow_implementation_selections=(
            fixture["binding"].workflow_implementation_selections[0],
            stray,
        ),
    )
    with pytest.raises(ImplementationBindingError, match="cannot widen graph semantics"):
        validate_implementation_binding_against_graph(
            widened, fixture["graph"], payloads=fixture["payloads"]
        )


async def test_workflow_selection_must_address_the_semantic_workflow(content_store):
    fixture = await _fixture(content_store)
    selection = fixture["binding"].workflow_implementation_selections[0]
    borrowed = WorkflowImplementationSelection(
        workflow_node_id=_WORKFLOW,
        orchestrator_selection=_workflow_document_selection(
            path="orchestrator.yaml",
            family="workflow_manifest",
            digest=selection.orchestrator_selection.contract_ref.content_digest,
            schema_version="mozaiks.orchestrator.v1",
            workflow="otherflow",
        ),
        structured_outputs_selection=_workflow_document_selection(
            path="structured_outputs.yaml",
            family="workflow_config",
            digest=selection.structured_outputs_selection.contract_ref.content_digest,
            schema_version="mozaiks.structured_outputs.v1",
            workflow="otherflow",
        ),
    )
    binding = _rebuilt(fixture, workflow_implementation_selections=(borrowed,))
    with pytest.raises(ImplementationBindingError, match="semantic workflow"):
        validate_implementation_binding_against_graph(
            binding, fixture["graph"], payloads=fixture["payloads"]
        )


async def test_action_selection_completeness(content_store):
    fixture = await _fixture(content_store)
    get_selection, create_selection = (
        fixture["binding"].module_action_implementation_selections
    )
    missing = _rebuilt(
        fixture, module_action_implementation_selections=(get_selection,)
    )
    with pytest.raises(ImplementationBindingError, match="absent action implementation"):
        validate_implementation_binding_against_graph(
            missing, fixture["graph"], payloads=fixture["payloads"]
        )
    stray = _replaced(
        create_selection, action_node_id="mozaiks.action.tasks_ghost_action"
    )
    widened = _rebuilt(
        fixture,
        module_action_implementation_selections=(
            create_selection,
            get_selection,
            stray,
        ),
    )
    with pytest.raises(ImplementationBindingError, match="cannot widen graph semantics"):
        validate_implementation_binding_against_graph(
            widened, fixture["graph"], payloads=fixture["payloads"]
        )
    with pytest.raises(pydantic.ValidationError, match="duplicate action implementation"):
        _rebuilt(
            fixture,
            module_action_implementation_selections=(
                create_selection,
                create_selection,
                get_selection,
            ),
        )
    borrowed_module = _replaced(
        create_selection,
        module_selection=_module_manifest_selection(
            digest=fixture["digests"]["notes_module"], module="notes"
        ).model_dump(mode="json"),
        handler_selection=_module_source_selection(
            digest=fixture["digests"]["tasks_handler"],
            module="notes",
            path="backend/handler.py",
        ).model_dump(mode="json"),
    )
    wrong_owner = _rebuilt(
        fixture,
        module_action_implementation_selections=(borrowed_module, get_selection),
    )
    with pytest.raises(ImplementationBindingError, match="owning semantic module"):
        validate_implementation_binding_against_graph(
            wrong_owner, fixture["graph"], payloads=fixture["payloads"]
        )


async def test_result_realization_completeness(content_store):
    fixture = await _fixture(content_store)
    advisory, result_binding = fixture["binding"].workflow_result_bindings
    missing = _rebuilt(fixture, workflow_result_bindings=(result_binding,))
    with pytest.raises(ImplementationBindingError, match="absent result realization"):
        validate_implementation_binding_against_graph(
            missing, fixture["graph"], payloads=fixture["payloads"]
        )
    with pytest.raises(pydantic.ValidationError, match="duplicate result realization"):
        _rebuilt(
            fixture,
            workflow_result_bindings=(result_binding, result_binding, advisory),
        )
    stray = _replaced(
        advisory, workflow_result_node_id="mozaiks.workflow_result.ghost"
    )
    widened = _rebuilt(
        fixture, workflow_result_bindings=(result_binding, advisory, stray)
    )
    with pytest.raises(ImplementationBindingError, match="cannot widen graph semantics"):
        validate_implementation_binding_against_graph(
            widened, fixture["graph"], payloads=fixture["payloads"]
        )


async def test_semantic_commit_closure(content_store):
    fixture = await _fixture(content_store)
    advisory, result_binding = fixture["binding"].workflow_result_bindings
    commit = result_binding.commit_bindings[0]

    uncommitted = _rebuilt(
        fixture,
        workflow_result_bindings=(
            _replaced(result_binding, commit_bindings=()),
            advisory,
        ),
    )
    with pytest.raises(ImplementationBindingError, match="lacks commit bindings"):
        validate_implementation_binding_against_graph(
            uncommitted, fixture["graph"], payloads=fixture["payloads"]
        )

    with pytest.raises(pydantic.ValidationError, match="duplicate commit bindings"):
        _replaced(
            result_binding,
            commit_bindings=(
                commit.model_dump(mode="json"),
                commit.model_dump(mode="json"),
            ),
        )

    extra = _replaced(
        commit, workflow_capability_binding_node_id=_BINDING_READ, action_inputs=()
    )
    surplus = _rebuilt(
        fixture,
        workflow_result_bindings=(
            _replaced(
                result_binding,
                commit_bindings=(
                    commit.model_dump(mode="json"),
                    extra.model_dump(mode="json"),
                ),
            ),
            advisory,
        ),
    )
    with pytest.raises(ImplementationBindingError, match="not semantic"):
        validate_implementation_binding_against_graph(
            surplus, fixture["graph"], payloads=fixture["payloads"]
        )

    misplaced = _rebuilt(
        fixture,
        workflow_result_bindings=(
            _replaced(result_binding, commit_bindings=()),
            _replaced(advisory, commit_bindings=(commit.model_dump(mode="json"),)),
        ),
    )
    with pytest.raises(ImplementationBindingError):
        validate_implementation_binding_against_graph(
            misplaced, fixture["graph"], payloads=fixture["payloads"]
        )

    with pytest.raises(pydantic.ValidationError, match="more than one workflow result"):
        _rebuilt(
            fixture,
            workflow_result_bindings=(
                result_binding,
                _replaced(advisory, commit_bindings=(commit.model_dump(mode="json"),)),
            ),
        )


async def test_action_input_closure_against_the_semantic_contract(content_store):
    fixture = await _fixture(content_store)
    advisory, result_binding = fixture["binding"].workflow_result_bindings
    commit = result_binding.commit_bindings[0]

    unknown_target = _replaced(
        commit,
        action_inputs=[
            *[item.model_dump(mode="json") for item in commit.action_inputs],
            {
                "target_property": "ghost",
                "source": {"source_kind": "constant", "value": None},
            },
        ],
    )
    binding = _rebuilt(
        fixture,
        workflow_result_bindings=(
            _replaced(
                result_binding,
                commit_bindings=(unknown_target.model_dump(mode="json"),),
            ),
            advisory,
        ),
    )
    with pytest.raises(ImplementationBindingError, match="unknown target action properties"):
        validate_implementation_binding_against_graph(
            binding, fixture["graph"], payloads=fixture["payloads"]
        )

    unwired = _replaced(
        commit,
        action_inputs=[
            item.model_dump(mode="json")
            for item in commit.action_inputs
            if item.target_property != "title"
        ],
    )
    binding = _rebuilt(
        fixture,
        workflow_result_bindings=(
            _replaced(
                result_binding, commit_bindings=(unwired.model_dump(mode="json"),)
            ),
            advisory,
        ),
    )
    with pytest.raises(ImplementationBindingError, match="unwired"):
        validate_implementation_binding_against_graph(
            binding, fixture["graph"], payloads=fixture["payloads"]
        )

    with pytest.raises(pydantic.ValidationError, match="duplicate action-input"):
        _replaced(
            commit,
            action_inputs=[
                *[item.model_dump(mode="json") for item in commit.action_inputs],
                {
                    "target_property": "title",
                    "source": {"source_kind": "constant", "value": "twice"},
                },
            ],
        )


async def test_result_and_context_property_sources_must_exist(content_store):
    fixture = await _fixture(content_store)
    advisory, result_binding = fixture["binding"].workflow_result_bindings
    commit = result_binding.commit_bindings[0]

    with pytest.raises(pydantic.ValidationError, match="outside the projected fields"):
        _replaced(
            result_binding,
            commit_bindings=(
                _replaced(
                    commit,
                    action_inputs=[
                        item.model_dump(mode="json")
                        if item.target_property != "title"
                        else {
                            "target_property": "title",
                            "source": {
                                "source_kind": "result_property",
                                "property_name": "message",
                            },
                        }
                        for item in commit.action_inputs
                    ],
                ).model_dump(mode="json"),
            ),
        )

    with pytest.raises(
        pydantic.ValidationError, match="request_context_contract does not declare"
    ):
        _rebuilt(
            fixture,
            workflow_result_bindings=(
                _replaced(
                    result_binding,
                    commit_bindings=(
                        _replaced(
                            commit,
                            action_inputs=[
                                item.model_dump(mode="json")
                                if item.target_property != "source"
                                else {
                                    "target_property": "source",
                                    "source": {
                                        "source_kind": "request_context_property",
                                        "property_name": "ghost",
                                    },
                                }
                                for item in commit.action_inputs
                            ],
                        ).model_dump(mode="json"),
                    ),
                ),
                advisory,
            ),
        )


# ---------------------------------------------------------------------------
# Adversarial matrix — cold content authority
# ---------------------------------------------------------------------------


async def test_fabricated_certification_identity_rejects_cold(content_store):
    fixture = await _fixture(content_store)
    get_selection, create_selection = (
        fixture["binding"].module_action_implementation_selections
    )
    fabricated = _rebuilt(
        fixture,
        module_action_implementation_selections=(
            _replaced(create_selection, implementation_digest=_sha(b"forged")),
            get_selection,
        ),
    )
    with pytest.raises(ImplementationBindingError, match="source-closure"):
        await validate_implementation_binding_content_authority(
            fabricated,
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=content_store,
        )


async def test_wrong_method_source_rejects_cold(content_store):
    fixture = await _fixture(content_store)
    get_selection, create_selection = (
        fixture["binding"].module_action_implementation_selections
    )
    wrong = _rebuilt(
        fixture,
        module_action_implementation_selections=(
            create_selection,
            _replaced(get_selection, method_source="handler"),
        ),
    )
    with pytest.raises(ImplementationBindingError, match="method_source"):
        await validate_implementation_binding_content_authority(
            wrong,
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=content_store,
        )


async def test_same_schema_output_of_another_workflow_rejects_cold(content_store):
    fixture = await _fixture(content_store)
    advisory, result_binding = fixture["binding"].workflow_result_bindings
    foreign_ref = build_structured_output_contract_ref(
        workflow_name="OtherFlow",
        model_id="Output",
        configs={
            "OtherFlow": parse_structured_outputs_config(STRUCTURED_OUTPUTS_DOCUMENT)
        },
        exact_model_ids=frozenset(),
    )
    borrowed = _rebuilt(
        fixture,
        workflow_result_bindings=(
            _replaced(
                result_binding,
                structured_output_contract_ref=foreign_ref.model_dump(mode="json"),
            ),
            advisory,
        ),
    )
    with pytest.raises(ImplementationBindingError, match="structured-output contract"):
        await validate_implementation_binding_content_authority(
            borrowed,
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=content_store,
        )


async def test_unknown_output_model_and_unknown_projection_field_reject_cold(
    content_store,
):
    fixture = await _fixture(content_store)
    advisory, result_binding = fixture["binding"].workflow_result_bindings
    ghost_model = _replaced(
        result_binding,
        structured_output_contract_ref={
            **result_binding.structured_output_contract_ref.model_dump(mode="json"),
            "model_id": "Ghost",
        },
    )
    binding = _rebuilt(fixture, workflow_result_bindings=(ghost_model, advisory))
    with pytest.raises(ImplementationBindingError, match="structured-output contract"):
        await validate_implementation_binding_content_authority(
            binding,
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=content_store,
        )

    ghost_field = _replaced(
        result_binding,
        projection={"projection_kind": "fields", "fields": ["title", "ghost"]},
        commit_bindings=[
            item.model_dump(mode="json") for item in result_binding.commit_bindings
        ],
    )
    binding = _rebuilt(fixture, workflow_result_bindings=(ghost_field, advisory))
    with pytest.raises(ImplementationBindingError, match="absent from the exact resolved"):
        await validate_implementation_binding_content_authority(
            binding,
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=content_store,
        )


async def test_identity_projection_still_bounds_result_properties_cold(content_store):
    fixture = await _fixture(content_store)
    advisory, result_binding = fixture["binding"].workflow_result_bindings
    commit = result_binding.commit_bindings[0]
    reads_ghost = _replaced(
        result_binding,
        projection={"projection_kind": "identity"},
        commit_bindings=(
            _replaced(
                commit,
                action_inputs=[
                    item.model_dump(mode="json")
                    if item.target_property != "title"
                    else {
                        "target_property": "title",
                        "source": {
                            "source_kind": "result_property",
                            "property_name": "ghost",
                        },
                    }
                    for item in commit.action_inputs
                ],
            ).model_dump(mode="json"),
        ),
    )
    binding = _rebuilt(fixture, workflow_result_bindings=(reads_ghost, advisory))
    with pytest.raises(ImplementationBindingError, match="projected result shape"):
        await validate_implementation_binding_content_authority(
            binding,
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=content_store,
        )


async def test_missing_blob_rejects_cold(content_store, tmp_path):
    fixture = await _fixture(content_store)
    empty_store = LocalArtifactContentStore(root=tmp_path / "empty")
    with pytest.raises(ImplementationBindingError, match="did not resolve exactly"):
        await validate_implementation_binding_content_authority(
            fixture["binding"],
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=empty_store,
        )


async def test_workflow_bytes_of_another_runtime_workflow_reject_cold(content_store):
    """Exact bytes declaring another runtime workflow cannot realize this one."""
    foreign_document = {**ORCHESTRATOR_DOCUMENT, "workflow_name": "OtherFlow"}
    foreign_digest = await _put(content_store, _yaml_bytes(foreign_document))
    fixture = await _fixture(content_store)
    selection = fixture["binding"].workflow_implementation_selections[0]
    swapped = WorkflowImplementationSelection(
        workflow_node_id=_WORKFLOW,
        orchestrator_selection=_workflow_document_selection(
            path="orchestrator.yaml",
            family="workflow_manifest",
            digest=foreign_digest,
            schema_version="mozaiks.orchestrator.v1",
        ),
        structured_outputs_selection=selection.structured_outputs_selection,
    )
    binding = _rebuilt(fixture, workflow_implementation_selections=(swapped,))
    # Graph validation passes (the addressed instance is still versionprobe);
    # cold validation rejects at the UNCONDITIONAL workflow identity join —
    # long before any result binding could be needed to notice, and the
    # selection address claiming the right instance cannot rescue the bytes.
    validate_implementation_binding_against_graph(
        binding, fixture["graph"], payloads=fixture["payloads"]
    )
    with pytest.raises(
        ImplementationBindingError, match="workflow identity comparison"
    ):
        await validate_implementation_binding_content_authority(
            binding,
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=content_store,
        )


# ---------------------------------------------------------------------------
# Workflow identity join — unconditional, results or no results
# ---------------------------------------------------------------------------


def _capability_only_graph() -> tuple:
    """A capability-owning workflow with ZERO results and ZERO actions."""
    workflow = build_semantic_payload(
        WorkflowPayload, node_id=_WORKFLOW, payload_version=1, scope=SCOPE,
        workflow_id="versionprobe", description="Analyze notes into tasks",
        startup_mode=None, topology=None,
    )
    capability = build_semantic_payload(
        WorkflowCapabilityPayload, node_id=_CAPABILITY, payload_version=1,
        scope=SCOPE, capability_id="tasks.analysis",
        description="Analyze one note", workflow_node_id=_WORKFLOW,
    )
    payloads = (workflow, capability)
    graph = build_semantic_graph_v2(
        graph_id="binding-v2-zero-results",
        version=1,
        scope=SCOPE,
        nodes=[
            SemanticNodeV2(
                node_id=payload.node_id,
                kind=payload.payload_kind,
                payload_ref=semantic_payload_ref(payload),
            )
            for payload in payloads
        ],
        edges=[
            SemanticEdge(
                kind=SemanticEdgeKind.DECLARES,
                source_node_id=_WORKFLOW,
                target_node_id=_CAPABILITY,
            )
        ],
    )
    return graph, payloads


async def _capability_only_binding(content_store, orchestrator_document: dict):
    orchestrator_digest = await _put(content_store, _yaml_bytes(orchestrator_document))
    outputs_digest = await _put(content_store, _yaml_bytes(STRUCTURED_OUTPUTS_DOCUMENT))
    graph, payloads = _capability_only_graph()
    binding = build_implementation_binding(
        binding_id="binding-v2-zero-results",
        version=1,
        scope=SCOPE,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=SCOPE,
        ),
        workflow_implementation_selections=(
            WorkflowImplementationSelection(
                workflow_node_id=_WORKFLOW,
                orchestrator_selection=_workflow_document_selection(
                    path="orchestrator.yaml",
                    family="workflow_manifest",
                    digest=orchestrator_digest,
                    schema_version="mozaiks.orchestrator.v1",
                ),
                structured_outputs_selection=_workflow_document_selection(
                    path="structured_outputs.yaml",
                    family="workflow_config",
                    digest=outputs_digest,
                    schema_version="mozaiks.structured_outputs.v1",
                ),
            ),
        ),
    )
    return binding, graph, payloads


async def test_foreign_workflow_name_rejects_cold_even_with_zero_results(
    content_store,
):
    """No result binding is needed to complete the workflow identity proof."""
    binding, graph, payloads = await _capability_only_binding(
        content_store, {**ORCHESTRATOR_DOCUMENT, "workflow_name": "ForeignWorkflow"}
    )
    validate_implementation_binding_against_graph(binding, graph, payloads=payloads)
    with pytest.raises(
        ImplementationBindingError, match="workflow identity comparison"
    ):
        await validate_implementation_binding_content_authority(
            binding, graph, payloads=payloads, content_store=content_store
        )


async def test_workflow_name_case_variation_passes_and_preserves_spelling(
    content_store,
):
    """The loader-accepted case-insensitive spelling passes; bytes stay exact."""
    binding, graph, payloads = await _capability_only_binding(
        content_store, {**ORCHESTRATOR_DOCUMENT, "workflow_name": "VERSIONPROBE"}
    )
    authority = await validate_implementation_binding_content_authority(
        binding, graph, payloads=payloads, content_store=content_store
    )
    implementation = authority.workflow_implementations[_WORKFLOW]
    # Normalization is comparison-only: the resolved config and document keep
    # the original declared spelling byte for byte.
    assert implementation.workflow_name == "VERSIONPROBE"
    assert implementation.orchestrator.document["workflow_name"] == "VERSIONPROBE"
    assert implementation.workflow_instance == "versionprobe"


async def test_exact_matching_workflow_name_passes_zero_results(content_store):
    binding, graph, payloads = await _capability_only_binding(
        content_store, dict(ORCHESTRATOR_DOCUMENT)
    )
    authority = await validate_implementation_binding_content_authority(
        binding, graph, payloads=payloads, content_store=content_store
    )
    assert authority.workflow_implementations[_WORKFLOW].workflow_name == "VersionProbe"


# ---------------------------------------------------------------------------
# Typed ActionPayload.action_id — node-id format never carries meaning
# ---------------------------------------------------------------------------


def test_fixture_action_nodes_are_canonical_projection_identities():
    """The fixture ACTION nodes use the digest-suffixed producer format."""
    import re as _re

    for node_id, local in (
        (_ACTION_CREATE, "tasks_create_task"),
        (_ACTION_GET, "notes_get_note"),
    ):
        pattern = rf"mozaiks\.action\.{local}_[0-9a-f]{{12}}"
        assert _re.fullmatch(pattern, node_id), node_id


async def test_canonical_projected_action_node_resolves_declared_action(
    content_store,
):
    """The reachable-producer probe: a canonical digest-suffixed ACTION node
    id resolves the DECLARED manifest action, never a digest-bearing guess."""
    fixture = await _fixture(content_store)
    authority = await validate_implementation_binding_content_authority(
        fixture["binding"],
        fixture["graph"],
        payloads=fixture["payloads"],
        content_store=content_store,
    )
    get = authority.module_action_implementations[_ACTION_GET]
    assert get.action.id == "get_note"
    create = authority.module_action_implementations[_ACTION_CREATE]
    assert create.action.id == "create_task"


async def test_action_node_id_format_cannot_change_action_selection(content_store):
    """An opaque ACTION node id with the same typed action_id resolves the
    same exact implementation: node identity carries no action meaning."""
    opaque = "mozaiks.action.opaque_reachability_probe"
    fixture = await _fixture(content_store, create_node_id=opaque)
    validate_implementation_binding_against_graph(
        fixture["binding"], fixture["graph"], payloads=fixture["payloads"]
    )
    authority = await validate_implementation_binding_content_authority(
        fixture["binding"],
        fixture["graph"],
        payloads=fixture["payloads"],
        content_store=content_store,
    )
    assert authority.module_action_implementations[opaque].action.id == "create_task"


async def test_semantic_action_id_absent_from_manifest_fails_cold(content_store):
    """A typed action_id the exact module manifest does not declare fails
    closed through the existing #488 resolver."""
    fixture = await _fixture(content_store, create_action_id="ghost_action")
    validate_implementation_binding_against_graph(
        fixture["binding"], fixture["graph"], payloads=fixture["payloads"]
    )
    with pytest.raises(ImplementationBindingError, match="did not resolve exactly"):
        await validate_implementation_binding_content_authority(
            fixture["binding"],
            fixture["graph"],
            payloads=fixture["payloads"],
            content_store=content_store,
        )


# ---------------------------------------------------------------------------
# Resolved authority — immutable snapshots
# ---------------------------------------------------------------------------


async def test_cold_validation_returns_immutable_mapping_snapshots(content_store):
    fixture = await _fixture(content_store)
    authority = await validate_implementation_binding_content_authority(
        fixture["binding"],
        fixture["graph"],
        payloads=fixture["payloads"],
        content_store=content_store,
    )
    with pytest.raises(TypeError):
        authority.workflow_implementations["evil"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        authority.module_action_implementations["evil"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        authority.result_output_models["evil"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        del authority.workflow_implementations[_WORKFLOW]  # type: ignore[attr-defined]


async def test_direct_construction_snapshots_detach_from_source_dicts(content_store):
    import dataclasses

    from mozaiksai.core.semantics.binding import ResolvedImplementationBindingAuthority

    fixture = await _fixture(content_store)
    authority = await validate_implementation_binding_content_authority(
        fixture["binding"],
        fixture["graph"],
        payloads=fixture["payloads"],
        content_store=content_store,
    )
    source_workflows = dict(authority.workflow_implementations)
    source_actions = dict(authority.module_action_implementations)
    source_models = dict(authority.result_output_models)
    direct = ResolvedImplementationBindingAuthority(
        workflow_implementations=source_workflows,
        module_action_implementations=source_actions,
        result_output_models=source_models,
    )
    # Mutating the caller-owned source dicts after construction does not
    # alter the snapshot: construction copies FIRST, then proxies.
    source_workflows["evil"] = None  # type: ignore[assignment]
    source_actions.clear()
    source_models["evil"] = None  # type: ignore[assignment]
    assert "evil" not in direct.workflow_implementations
    assert set(direct.module_action_implementations) == {_ACTION_CREATE, _ACTION_GET}
    assert "evil" not in direct.result_output_models
    with pytest.raises(TypeError):
        direct.workflow_implementations["evil"] = None  # type: ignore[index]
    # Attribute rebinding stays blocked, and nested values remain the exact
    # frozen resolved-authority models.
    with pytest.raises(dataclasses.FrozenInstanceError):
        direct.workflow_implementations = {}  # type: ignore[misc]
    resolved_workflow = direct.workflow_implementations[_WORKFLOW]
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved_workflow.orchestrator = None  # type: ignore[misc]
    resolved_action = direct.module_action_implementations[_ACTION_CREATE]
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved_action.export_proof = None  # type: ignore[misc]
