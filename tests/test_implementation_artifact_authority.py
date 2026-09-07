"""Implementation artifacts are selected by exact content, never by path trust.

Every test resolves through the required path only:

    SelectedContractArtifact -> ChildContractRef -> ArtifactAddress
    -> ArtifactContentStore.get_verified_blob() -> exact bytes -> strict parser

and proves the hostile matrix fails closed: missing or tampered blobs,
cross-scope selection, wrong family/scope/placeholders, ref/address path
mismatch, wrong document schema versions, cross-workflow and cross-module
borrowing, digestless handler selection, wrong handler addresses, and every
non-explicit handler export (inherited, monkeypatched, conditional, dynamic).
"""

from __future__ import annotations

import hashlib

import pytest
import yaml

from mozaiksai.core.artifacts.content_store import LocalArtifactContentStore
from mozaiksai.core.runtime.app.layout_registry import PathScope
from mozaiksai.core.semantics.closed_contracts import ClosedContractUnsupported, ObjectContract
from mozaiksai.core.semantics.composition_ledger import AccountedArtifact, ArtifactAddress
from mozaiksai.core.semantics.implementation_artifacts import (
    ImplementationArtifactError,
    SelectedContractArtifact,
    pair_workflow_implementation_artifacts,
    prove_module_action_export,
    resolve_module_action_implementation,
    resolve_module_handler_source,
    resolve_module_manifest_artifact,
    resolve_selected_structured_output_contract,
    resolve_workflow_orchestrator_artifact,
    resolve_workflow_structured_outputs_artifact,
)
from mozaiksai.core.semantics.refs import ChildContractRef, ExecutionAccessScopeRef
from mozaiksai.core.semantics.resolver import SemanticReferenceResolver
from mozaiksai.core.workflow.declarative.contracts import parse_structured_outputs_config
from mozaiksai.core.workflow.structured_output_contracts import (
    build_structured_output_contract_ref,
)

SCOPE = ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="workspace")
OTHER_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="elsewhere")

ORCHESTRATOR_DOCUMENT = {
    "schema_version": "mozaiks.orchestrator.v1",
    "workflow_name": "VersionProbe",
    "workflow_startup_mode": "AgentDriven",
}

STRUCTURED_OUTPUTS_DOCUMENT = {
    "schema_version": "mozaiks.structured_outputs.v1",
    "registry": {"Author": "Output"},
    "models": {"Output": {"type": "model", "fields": {"message": {"type": "str"}}}},
}

MODULE_DOCUMENT = {
    "schema_version": "mozaiks.module.v1",
    "module": {"id": "tasks", "handler": "backend.handler:TasksHandler"},
    "actions": [
        {
            "id": "create_task",
            "description": "Create one task.",
            "handler_method": "create_task",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        }
    ],
}

HANDLER_SOURCE = (
    '"""Tasks handler."""\n'
    "\n"
    "class TasksHandler:\n"
    "    async def create_task(self, ctx, payload):\n"
    '        return {"status": "created"}\n'
)


@pytest.fixture
def content_store(tmp_path):
    return LocalArtifactContentStore(root=tmp_path)


async def _put(content_store, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    await content_store.put_blob(data, expected_digest=digest)
    return digest


def _yaml_bytes(document: dict) -> bytes:
    return yaml.safe_dump(document, sort_keys=False).encode("utf-8")


def _workflow_selection(
    *,
    path: str,
    family: str,
    digest: str,
    schema_version: str,
    workflow: str = "versionprobe",
    scope: ExecutionAccessScopeRef = SCOPE,
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


def _module_selection(
    *,
    digest: str,
    module: str = "tasks",
    schema_version: str = "mozaiks.module.v1",
    scope: ExecutionAccessScopeRef = SCOPE,
) -> SelectedContractArtifact:
    return SelectedContractArtifact(
        contract_ref=ChildContractRef(
            subject_id="probe-app",
            subject_version=1,
            content_digest=digest,
            scope=scope,
            artifact_family="module_manifest",
            canonical_relative_path="module.yaml",
            contract_schema_version=schema_version,
        ),
        address=ArtifactAddress(
            path_scope=PathScope.MODULE_RELATIVE,
            placeholder_values=(("module_id", module),),
            path="module.yaml",
        ),
    )


def _handler_artifact(
    *, digest: str | None, module: str = "tasks", path: str = "backend/handler.py"
) -> AccountedArtifact:
    return AccountedArtifact(
        address=ArtifactAddress(
            path_scope=PathScope.MODULE_RELATIVE,
            placeholder_values=(("module_id", module),),
            path=path,
        ),
        content_digest=digest,
    )


async def _resolved_workflow_pair(content_store):
    orchestrator_digest = await _put(content_store, _yaml_bytes(ORCHESTRATOR_DOCUMENT))
    outputs_digest = await _put(content_store, _yaml_bytes(STRUCTURED_OUTPUTS_DOCUMENT))
    orchestrator = await resolve_workflow_orchestrator_artifact(
        _workflow_selection(
            path="orchestrator.yaml",
            family="workflow_manifest",
            digest=orchestrator_digest,
            schema_version="mozaiks.orchestrator.v1",
        ),
        content_store=content_store,
        requesting_scope=SCOPE,
    )
    structured_outputs = await resolve_workflow_structured_outputs_artifact(
        _workflow_selection(
            path="structured_outputs.yaml",
            family="workflow_config",
            digest=outputs_digest,
            schema_version="mozaiks.structured_outputs.v1",
        ),
        content_store=content_store,
        requesting_scope=SCOPE,
    )
    return orchestrator, structured_outputs


async def _resolved_module(content_store, document=MODULE_DOCUMENT):
    digest = await _put(content_store, _yaml_bytes(document))
    return await resolve_module_manifest_artifact(
        _module_selection(digest=digest),
        content_store=content_store,
        requesting_scope=SCOPE,
    )


async def test_workflow_documents_resolve_exact_content(content_store):
    orchestrator, structured_outputs = await _resolved_workflow_pair(content_store)
    assert orchestrator.workflow_name == "VersionProbe"
    assert orchestrator.workflow_instance == "versionprobe"
    assert orchestrator.config.workflow_startup_mode == "AgentDriven"
    assert orchestrator.document["schema_version"] == "mozaiks.orchestrator.v1"
    assert structured_outputs.workflow_instance == "versionprobe"
    assert structured_outputs.document["models"] == STRUCTURED_OUTPUTS_DOCUMENT["models"]
    implementation = pair_workflow_implementation_artifacts(orchestrator, structured_outputs)
    assert implementation.workflow_name == "VersionProbe"
    assert implementation.workflow_instance == "versionprobe"


async def test_structured_output_ref_resolves_against_selected_configuration(content_store):
    orchestrator, structured_outputs = await _resolved_workflow_pair(content_store)
    implementation = pair_workflow_implementation_artifacts(orchestrator, structured_outputs)
    ref = build_structured_output_contract_ref(
        workflow_name="VersionProbe",
        model_id="Output",
        configs={"VersionProbe": structured_outputs.document},
        exact_model_ids=frozenset(),
    )
    model = resolve_selected_structured_output_contract(implementation, ref)
    assert model.model_validate({"message": "hello"}).message == "hello"


async def test_structured_output_ref_from_other_workflow_rejects(content_store):
    orchestrator, structured_outputs = await _resolved_workflow_pair(content_store)
    implementation = pair_workflow_implementation_artifacts(orchestrator, structured_outputs)
    foreign_name_ref = build_structured_output_contract_ref(
        workflow_name="OtherFlow",
        model_id="Output",
        configs={"OtherFlow": structured_outputs.document},
        exact_model_ids=frozenset(),
    )
    with pytest.raises(ImplementationArtifactError, match="not interchangeable"):
        resolve_selected_structured_output_contract(implementation, foreign_name_ref)

    foreign_schema = parse_structured_outputs_config(
        {
            "schema_version": "mozaiks.structured_outputs.v1",
            "registry": {"Author": "Output"},
            "models": {"Output": {"type": "model", "fields": {"message": {"type": "int"}}}},
        }
    )
    foreign_schema_ref = build_structured_output_contract_ref(
        workflow_name="VersionProbe",
        model_id="Output",
        configs={"VersionProbe": foreign_schema},
        exact_model_ids=frozenset(),
    )
    with pytest.raises(ImplementationArtifactError, match="did not resolve"):
        resolve_selected_structured_output_contract(implementation, foreign_schema_ref)


async def test_workflow_documents_are_not_interchangeable_across_workflows(content_store):
    orchestrator_digest = await _put(content_store, _yaml_bytes(ORCHESTRATOR_DOCUMENT))
    outputs_digest = await _put(content_store, _yaml_bytes(STRUCTURED_OUTPUTS_DOCUMENT))
    orchestrator = await resolve_workflow_orchestrator_artifact(
        _workflow_selection(
            path="orchestrator.yaml",
            family="workflow_manifest",
            digest=orchestrator_digest,
            schema_version="mozaiks.orchestrator.v1",
            workflow="versionprobe",
        ),
        content_store=content_store,
        requesting_scope=SCOPE,
    )
    borrowed = await resolve_workflow_structured_outputs_artifact(
        _workflow_selection(
            path="structured_outputs.yaml",
            family="workflow_config",
            digest=outputs_digest,
            schema_version="mozaiks.structured_outputs.v1",
            workflow="otherflow",
        ),
        content_store=content_store,
        requesting_scope=SCOPE,
    )
    with pytest.raises(ImplementationArtifactError, match="cannot implement workflow"):
        pair_workflow_implementation_artifacts(orchestrator, borrowed)


async def test_missing_blob_rejects_even_with_opaque_registration(content_store):
    data = _yaml_bytes(ORCHESTRATOR_DOCUMENT)
    digest = hashlib.sha256(data).hexdigest()
    selection = _workflow_selection(
        path="orchestrator.yaml",
        family="workflow_manifest",
        digest=digest,
        schema_version="mozaiks.orchestrator.v1",
    )
    resolver = SemanticReferenceResolver()
    resolver.register_opaque_subject(
        kind=selection.contract_ref.document_type,
        subject_id=selection.contract_ref.subject_id,
        version=selection.contract_ref.subject_version,
        digest=digest,
        scope=SCOPE,
        artifact_family="workflow_manifest",
        canonical_relative_path="orchestrator.yaml",
        contract_schema_version="mozaiks.orchestrator.v1",
    )
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await resolve_workflow_orchestrator_artifact(
            selection, content_store=content_store, requesting_scope=SCOPE
        )


async def test_tampered_blob_rejects(content_store, tmp_path):
    data = _yaml_bytes(ORCHESTRATOR_DOCUMENT)
    digest = hashlib.sha256(data).hexdigest()
    blob_path = tmp_path / "sha256" / digest[:2] / digest
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(b"tampered bytes")
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await resolve_workflow_orchestrator_artifact(
            _workflow_selection(
                path="orchestrator.yaml",
                family="workflow_manifest",
                digest=digest,
                schema_version="mozaiks.orchestrator.v1",
            ),
            content_store=content_store,
            requesting_scope=SCOPE,
        )


async def test_cross_scope_selection_rejects(content_store):
    digest = await _put(content_store, _yaml_bytes(ORCHESTRATOR_DOCUMENT))
    with pytest.raises(ImplementationArtifactError, match="cross-scope"):
        await resolve_workflow_orchestrator_artifact(
            _workflow_selection(
                path="orchestrator.yaml",
                family="workflow_manifest",
                digest=digest,
                schema_version="mozaiks.orchestrator.v1",
            ),
            content_store=content_store,
            requesting_scope=OTHER_SCOPE,
        )


def test_ref_and_address_paths_must_agree():
    digest = "0" * 64
    with pytest.raises(ValueError, match="does not equal its artifact address path"):
        SelectedContractArtifact(
            contract_ref=ChildContractRef(
                subject_id="probe-app",
                subject_version=1,
                content_digest=digest,
                scope=SCOPE,
                artifact_family="workflow_manifest",
                canonical_relative_path="orchestrator.yaml",
                contract_schema_version="mozaiks.orchestrator.v1",
            ),
            address=ArtifactAddress(
                path_scope=PathScope.WORKFLOW_RELATIVE,
                placeholder_values=(("workflow_id", "versionprobe"),),
                path="structured_outputs.yaml",
            ),
        )


async def test_wrong_family_rejects(content_store):
    digest = await _put(content_store, _yaml_bytes(ORCHESTRATOR_DOCUMENT))
    # agents.yaml is a canonical workflow_config path, not a workflow manifest.
    with pytest.raises(ImplementationArtifactError, match="not 'workflow_manifest'"):
        await resolve_workflow_orchestrator_artifact(
            _workflow_selection(
                path="agents.yaml",
                family="workflow_manifest",
                digest=digest,
                schema_version="mozaiks.orchestrator.v1",
            ),
            content_store=content_store,
            requesting_scope=SCOPE,
        )
    # The proven layout family must also equal the reference's claimed family.
    with pytest.raises(ImplementationArtifactError, match="does not equal the proven layout"):
        await resolve_workflow_orchestrator_artifact(
            _workflow_selection(
                path="orchestrator.yaml",
                family="workflow_config",
                digest=digest,
                schema_version="mozaiks.orchestrator.v1",
            ),
            content_store=content_store,
            requesting_scope=SCOPE,
        )


async def test_wrong_path_scope_rejects(content_store):
    digest = await _put(content_store, _yaml_bytes(ORCHESTRATOR_DOCUMENT))
    selection = SelectedContractArtifact(
        contract_ref=ChildContractRef(
            subject_id="probe-app",
            subject_version=1,
            content_digest=digest,
            scope=SCOPE,
            artifact_family="workflow_manifest",
            canonical_relative_path="orchestrator.yaml",
            contract_schema_version="mozaiks.orchestrator.v1",
        ),
        address=ArtifactAddress(
            path_scope=PathScope.APP_BUNDLE_ROOT,
            placeholder_values=(),
            path="orchestrator.yaml",
        ),
    )
    with pytest.raises(ImplementationArtifactError, match="no canonical layout row"):
        await resolve_workflow_orchestrator_artifact(
            selection, content_store=content_store, requesting_scope=SCOPE
        )


async def test_wrong_placeholders_reject(content_store):
    digest = await _put(content_store, _yaml_bytes(ORCHESTRATOR_DOCUMENT))
    mis_named = SelectedContractArtifact(
        contract_ref=ChildContractRef(
            subject_id="probe-app",
            subject_version=1,
            content_digest=digest,
            scope=SCOPE,
            artifact_family="workflow_manifest",
            canonical_relative_path="orchestrator.yaml",
            contract_schema_version="mozaiks.orchestrator.v1",
        ),
        address=ArtifactAddress(
            path_scope=PathScope.WORKFLOW_RELATIVE,
            placeholder_values=(("module_id", "versionprobe"),),
            path="orchestrator.yaml",
        ),
    )
    with pytest.raises(ImplementationArtifactError, match="require exactly the 'workflow_id'"):
        await resolve_workflow_orchestrator_artifact(
            mis_named, content_store=content_store, requesting_scope=SCOPE
        )
    outside_domain = _workflow_selection(
        path="orchestrator.yaml",
        family="workflow_manifest",
        digest=digest,
        schema_version="mozaiks.orchestrator.v1",
        workflow="VersionProbe",
    )
    with pytest.raises(ImplementationArtifactError, match="outside the closed domain"):
        await resolve_workflow_orchestrator_artifact(
            outside_domain, content_store=content_store, requesting_scope=SCOPE
        )


@pytest.mark.parametrize(
    "document,path,family,claimed_version",
    [
        (ORCHESTRATOR_DOCUMENT, "orchestrator.yaml", "workflow_manifest", "mozaiks.orchestrator.v2"),
        (
            STRUCTURED_OUTPUTS_DOCUMENT,
            "structured_outputs.yaml",
            "workflow_config",
            "mozaiks.structured_outputs.v2",
        ),
    ],
)
async def test_wrong_workflow_schema_version_rejects(
    content_store, document, path, family, claimed_version
):
    digest = await _put(content_store, _yaml_bytes(document))
    selection = _workflow_selection(
        path=path, family=family, digest=digest, schema_version=claimed_version
    )
    resolve = (
        resolve_workflow_orchestrator_artifact
        if path == "orchestrator.yaml"
        else resolve_workflow_structured_outputs_artifact
    )
    with pytest.raises(ImplementationArtifactError, match="pins"):
        await resolve(selection, content_store=content_store, requesting_scope=SCOPE)


async def test_byte_variant_documents_keep_distinct_exact_identity(content_store):
    first = _yaml_bytes(ORCHESTRATOR_DOCUMENT)
    reordered = yaml.safe_dump(
        dict(reversed(list(ORCHESTRATOR_DOCUMENT.items()))), sort_keys=False
    ).encode("utf-8")
    assert first != reordered
    first_digest = await _put(content_store, first)
    reordered_digest = await _put(content_store, reordered)
    assert first_digest != reordered_digest
    for digest in (first_digest, reordered_digest):
        resolved = await resolve_workflow_orchestrator_artifact(
            _workflow_selection(
                path="orchestrator.yaml",
                family="workflow_manifest",
                digest=digest,
                schema_version="mozaiks.orchestrator.v1",
            ),
            content_store=content_store,
            requesting_scope=SCOPE,
        )
        assert resolved.selection.contract_ref.content_digest == digest
        assert resolved.workflow_name == "VersionProbe"


async def test_module_manifest_resolves_exact_facts(content_store):
    module = await _resolved_module(content_store)
    assert module.module_instance == "tasks"
    assert module.definition.module.id == "tasks"
    assert module.handler_class == "TasksHandler"
    assert module.handler_relative_path == "backend/handler.py"
    action = module.action("create_task")
    assert action.handler_method == "create_task"
    contract = module.action_request_contract("create_task")
    assert isinstance(contract, ObjectContract)
    with pytest.raises(ImplementationArtifactError, match="declares no action"):
        module.action("delete_task")


async def test_module_manifest_borrowed_across_modules_rejects(content_store):
    digest = await _put(content_store, _yaml_bytes(MODULE_DOCUMENT))
    with pytest.raises(ImplementationArtifactError, match="cannot be selected for module"):
        await resolve_module_manifest_artifact(
            _module_selection(digest=digest, module="billing"),
            content_store=content_store,
            requesting_scope=SCOPE,
        )


async def test_wrong_module_schema_version_rejects(content_store):
    digest = await _put(content_store, _yaml_bytes(MODULE_DOCUMENT))
    with pytest.raises(ImplementationArtifactError, match="pins 'mozaiks.module.v2'"):
        await resolve_module_manifest_artifact(
            _module_selection(digest=digest, schema_version="mozaiks.module.v2"),
            content_store=content_store,
            requesting_scope=SCOPE,
        )


async def test_unsupported_action_request_schema_rejects(content_store):
    document = {
        **MODULE_DOCUMENT,
        "actions": [
            {
                **MODULE_DOCUMENT["actions"][0],
                "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
            }
        ],
    }
    module = await _resolved_module(content_store, document)
    with pytest.raises(ClosedContractUnsupported):
        module.action_request_contract("create_task")


async def test_handler_selection_requires_content_digest(content_store):
    module = await _resolved_module(content_store)
    with pytest.raises(ImplementationArtifactError, match="non-null content digest"):
        await resolve_module_handler_source(
            _handler_artifact(digest=None), module=module, content_store=content_store
        )


async def test_handler_missing_blob_rejects(content_store):
    module = await _resolved_module(content_store)
    absent_digest = hashlib.sha256(HANDLER_SOURCE.encode("utf-8")).hexdigest()
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await resolve_module_handler_source(
            _handler_artifact(digest=absent_digest),
            module=module,
            content_store=content_store,
        )


async def test_handler_from_other_module_rejects(content_store):
    module = await _resolved_module(content_store)
    digest = await _put(content_store, HANDLER_SOURCE.encode("utf-8"))
    with pytest.raises(ImplementationArtifactError, match="cannot implement module"):
        await resolve_module_handler_source(
            _handler_artifact(digest=digest, module="billing"),
            module=module,
            content_store=content_store,
        )


async def test_handler_at_wrong_family_or_path_rejects(content_store):
    module = await _resolved_module(content_store)
    digest = await _put(content_store, HANDLER_SOURCE.encode("utf-8"))
    with pytest.raises(ImplementationArtifactError, match="not 'module_backend_handler'"):
        await resolve_module_handler_source(
            _handler_artifact(digest=digest, path="backend/service.py"),
            module=module,
            content_store=content_store,
        )
    custom_document = {
        **MODULE_DOCUMENT,
        "module": {"id": "tasks", "handler": "backend.custom:TasksHandler"},
    }
    custom_module = await _resolved_module(content_store, custom_document)
    with pytest.raises(ImplementationArtifactError, match="declares its handler at"):
        await resolve_module_handler_source(
            _handler_artifact(digest=digest),
            module=custom_module,
            content_store=content_store,
        )


async def test_module_action_implementation_resolves_end_to_end(content_store):
    module_digest = await _put(content_store, _yaml_bytes(MODULE_DOCUMENT))
    handler_digest = await _put(content_store, HANDLER_SOURCE.encode("utf-8"))
    resolved = await resolve_module_action_implementation(
        _module_selection(digest=module_digest),
        _handler_artifact(digest=handler_digest),
        action_id="create_task",
        content_store=content_store,
        requesting_scope=SCOPE,
    )
    assert resolved.module.module_instance == "tasks"
    assert resolved.action.id == "create_task"
    assert resolved.export_proof.handler_class == "TasksHandler"
    assert resolved.export_proof.handler_method == "create_task"
    assert resolved.export_proof.content_digest == handler_digest
    assert isinstance(resolved.request_contract, ObjectContract)


async def test_handler_at_app_bundle_scope_resolves(content_store):
    module = await _resolved_module(content_store)
    digest = await _put(content_store, HANDLER_SOURCE.encode("utf-8"))
    artifact = AccountedArtifact(
        address=ArtifactAddress(
            path_scope=PathScope.APP_BUNDLE_ROOT,
            placeholder_values=(),
            path="modules/tasks/backend/handler.py",
        ),
        content_digest=digest,
    )
    handler = await resolve_module_handler_source(
        artifact, module=module, content_store=content_store
    )
    assert handler.module_instance == "tasks"
    proof = prove_module_action_export(handler, handler_method="create_task")
    assert proof.content_digest == digest


async def _handler_for_source(content_store, source: str):
    module = await _resolved_module(content_store)
    digest = await _put(content_store, source.encode("utf-8"))
    return await resolve_module_handler_source(
        _handler_artifact(digest=digest), module=module, content_store=content_store
    )


@pytest.mark.parametrize(
    "source,reason",
    [
        # Unrelated source at a valid digest: the declared class is absent.
        ("class OtherHandler:\n    async def create_task(self, ctx, payload):\n        return {}\n", "absent"),
        # Inherited action methods are not explicit exports of the selected source.
        (
            "from .base_handler import TasksBaseHandler\n\n"
            "class TasksHandler(TasksBaseHandler):\n"
            '    """Thin preserved subclass."""\n',
            "not explicitly defined",
        ),
        # Method on the wrong class.
        (
            "class TasksHandler:\n    pass\n\n"
            "class OtherHandler:\n    async def create_task(self, ctx, payload):\n        return {}\n",
            "not explicitly defined",
        ),
        # Dynamic monkeypatch after the definition.
        (
            "class TasksHandler:\n    pass\n\n"
            "async def create_task(self, ctx, payload):\n    return {}\n\n"
            "TasksHandler.create_task = create_task\n",
            "referenced dynamically|rebound|not explicitly defined",
        ),
        # setattr-style patching.
        (
            "class TasksHandler:\n    async def create_task(self, ctx, payload):\n        return {}\n\n"
            'setattr(TasksHandler, "create_task", None)\n',
            "not statically provable|referenced dynamically",
        ),
        # Module-level __getattr__ trick.
        (
            "class TasksHandler:\n    async def create_task(self, ctx, payload):\n        return {}\n\n"
            "def __getattr__(name):\n    raise AttributeError(name)\n",
            "__getattr__",
        ),
        # Class-level __getattr__ trick.
        (
            "class TasksHandler:\n"
            "    async def create_task(self, ctx, payload):\n        return {}\n"
            "    def __getattr__(self, name):\n        raise AttributeError(name)\n",
            "__getattr__",
        ),
        # Conditional definition is not statically provable.
        (
            "FLAG = True\n\n"
            "class TasksHandler:\n"
            "    if FLAG:\n"
            "        async def create_task(self, ctx, payload):\n"
            "            return {}\n",
            "conditionally",
        ),
        # Decorated definition is not statically provable.
        (
            "def wrap(fn):\n    return fn\n\n"
            "class TasksHandler:\n"
            "    @wrap\n"
            "    async def create_task(self, ctx, payload):\n        return {}\n",
            "decorated",
        ),
        # Duplicate definition is ambiguous.
        (
            "class TasksHandler:\n"
            "    async def create_task(self, ctx, payload):\n        return {}\n"
            "    async def create_task(self, ctx, payload):  # noqa: F811\n        return {}\n",
            "more than once",
        ),
        # Rebinding the method name in the class body.
        (
            "def other(self, ctx, payload):\n    return {}\n\n"
            "class TasksHandler:\n"
            "    async def create_task(self, ctx, payload):\n        return {}\n"
            "    create_task = other\n",
            "rebound|more than once",
        ),
        # Rebinding the class name after its definition.
        (
            "class TasksHandler:\n    async def create_task(self, ctx, payload):\n        return {}\n\n"
            "class _Shadow:\n    pass\n\n"
            "TasksHandler = _Shadow\n",
            "more than once|rebound|referenced dynamically",
        ),
        # Unparseable source is unprovable.
        ("class TasksHandler(:\n", "not statically parseable"),
    ],
)
async def test_static_export_proof_rejects_non_explicit_exports(content_store, source, reason):
    handler = await _handler_for_source(content_store, source)
    with pytest.raises(ImplementationArtifactError, match=reason):
        prove_module_action_export(handler, handler_method="create_task")


async def test_static_export_proof_accepts_explicit_export(content_store):
    handler = await _handler_for_source(content_store, HANDLER_SOURCE)
    proof = prove_module_action_export(handler, handler_method="create_task")
    assert proof.handler_class == "TasksHandler"
    assert proof.handler_method == "create_task"
