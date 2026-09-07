"""Content-resolved implementation artifact authority for ADR 0007.

Future ``ImplementationBinding v2`` must be able to truthfully claim that one
exact implementation realizes one semantic workflow/action.  A
``ChildContractRef``/digest alone cannot carry that claim.  This module is the
selection boundary that can: a typed selection couples the existing
``ChildContractRef`` identity to one canonical ``ArtifactAddress``, the
canonical layout registry independently proves family, path scope,
placeholders, owner, and physical representation, the immutable blob store
returns the exact verified bytes, the strict document parser accepts them, and
the parsed document's own schema version must equal the reference's declared
``contract_schema_version``.

Module handler sources additionally carry a bounded static export proof: the
declared handler class and each certified action ``handler_method`` must be
explicitly present in the selected verified source.  Certified implementation
selection requires the selected source to expose the declared handler
explicitly — inherited methods, monkeypatching, ``__getattr__`` tricks, and
other dynamic exports are rejected, never executed to find out.

There is no filesystem fallback, sibling checkout, glob/open/path discovery,
mutable alias, or caller assertion anywhere on this path.  Opaque resolver
registration with ``content=None`` never satisfies this boundary: bytes come
only from ``ArtifactContentStore.get_verified_blob``.
"""

from __future__ import annotations

import ast
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import model_validator

from mozaiksai.core.artifacts.content_store import (
    ArtifactContentStore,
    ContentIntegrityError,
    ContentNotFoundError,
)
from mozaiksai.core.runtime.app.layout_registry import (
    AppLayoutRegistry,
    ArtifactFamily,
    ArtifactKind,
    LayoutOwner,
    PathScope,
    PlaceholderIdentifier,
    Requirement,
    default_app_layout_registry,
)
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.semantics.closed_contracts import ClosedContract
from mozaiksai.core.semantics.compilation_plan import canonical_instance_identity_value
from mozaiksai.core.semantics.composition_ledger import AccountedArtifact, ArtifactAddress
from mozaiksai.core.semantics.refs import (
    ChildContractRef,
    ExecutionAccessScopeRef,
    SemanticsModel,
)
from mozaiksai.core.workflow.structured_output_contracts import (
    StructuredOutputContractRef,
    resolve_structured_output_contract_ref,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from mozaiksai.core.runtime.app.module_loader import ActionDef, ModuleDefinition
    from mozaiksai.core.workflow.declarative.contracts import (
        OrchestratorConfig,
        StructuredOutputsConfig,
    )


class ImplementationArtifactError(ValueError):
    """The selection fails one of the exact implementation-artifact proofs."""


#: The single instance placeholder each instance-relative collision domain uses.
_INSTANCE_PLACEHOLDER_BY_SCOPE: dict[PathScope, PlaceholderIdentifier] = {
    PathScope.MODULE_RELATIVE: PlaceholderIdentifier.MODULE_ID,
    PathScope.WORKFLOW_RELATIVE: PlaceholderIdentifier.WORKFLOW_ID,
}

#: Builtins whose presence in module/class statement space defeats static
#: export proof (they can create, replace, or hide exports at import time).
_DYNAMIC_EXPORT_BUILTINS = frozenset(
    {"setattr", "delattr", "getattr", "globals", "vars", "eval", "exec", "__import__", "type"}
)


class SelectedContractArtifact(SemanticsModel):
    """One immutable contract selection: typed reference plus physical address.

    Path equality between the reference and the address is required but is
    never authority by itself — resolution independently proves the canonical
    layout facts and the exact bytes.
    """

    contract_ref: ChildContractRef
    address: ArtifactAddress

    @model_validator(mode="after")
    def _paths_agree(self) -> SelectedContractArtifact:
        if self.contract_ref.canonical_relative_path != self.address.path:
            raise ValueError(
                "selected contract reference path does not equal its artifact address path"
            )
        return self


class HandlerCertificationMode(StrEnum):
    """The two bounded implementation-certification modes.

    ``EXPLICIT_HANDLER``: the selected canonical ``handler.py`` explicitly
    defines the selected ``handler_method``.

    ``CANONICAL_BASE_HANDLER``: the module uses the canonical
    ``workspace_handler_split`` contract — a preserved workspace-owned
    ``handler.py`` leaf that directly subclasses the regenerated
    template-owned ``base_handler.py`` class, which explicitly defines the
    selected method.  Exactly these two sources may participate; there is no
    general Python source closure.
    """

    EXPLICIT_HANDLER = "explicit_handler"
    CANONICAL_BASE_HANDLER = "canonical_base_handler"


class WorkspaceHandlerSplitAuthority(SemanticsModel):
    """Proof that one module legitimately uses the canonical handler split.

    The split is never inferred merely because files carry the split names:
    the owning capability-pack contract must declare the canonical ownership —
    ``handler.py`` as the preserved workspace-owned leaf and
    ``base_handler.py`` as the regenerated template-owned implementation.
    Build it with :func:`workspace_handler_split_authority_from_pack_contract`.
    """

    contract_id: str
    module_instance: str
    handler_path: str
    base_handler_path: str


class ModuleActionExportProof(SemanticsModel):
    """Static proof that one certified selection explicitly exports one method.

    ``implementation_digest`` is the certified implementation identity: for
    ``CANONICAL_BASE_HANDLER`` it covers BOTH meaning-bearing source digests,
    so changing only ``base_handler.py`` — or only the preserved
    ``handler.py`` — changes the certified identity.
    """

    mode: HandlerCertificationMode
    handler_class: str
    handler_method: str
    content_digest: str
    base_handler_class: str | None = None
    base_content_digest: str | None = None

    @property
    def implementation_digest(self) -> str:
        return canonical_digest(
            {
                "mode": self.mode.value,
                "handler_class": self.handler_class,
                "handler_method": self.handler_method,
                "content_digest": self.content_digest,
                "base_handler_class": self.base_handler_class,
                "base_content_digest": self.base_content_digest,
            }
        )


@dataclass(frozen=True, slots=True)
class ResolvedWorkflowOrchestrator:
    """Exact content-resolved ``orchestrator.yaml`` for one workflow instance."""

    selection: SelectedContractArtifact
    workflow_instance: str
    config: OrchestratorConfig
    document: dict[str, Any]

    @property
    def workflow_name(self) -> str:
        """The runtime workflow name declared by the exact parsed document."""
        return self.config.workflow_name


@dataclass(frozen=True, slots=True)
class ResolvedWorkflowStructuredOutputs:
    """Exact content-resolved ``structured_outputs.yaml`` for one workflow instance."""

    selection: SelectedContractArtifact
    workflow_instance: str
    config: StructuredOutputsConfig
    document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ResolvedWorkflowImplementation:
    """One workflow instance's paired exact implementation documents."""

    orchestrator: ResolvedWorkflowOrchestrator
    structured_outputs: ResolvedWorkflowStructuredOutputs

    @property
    def workflow_instance(self) -> str:
        return self.orchestrator.workflow_instance

    @property
    def workflow_name(self) -> str:
        return self.orchestrator.workflow_name


@dataclass(frozen=True, slots=True)
class ResolvedModuleManifest:
    """Exact content-resolved ``module.yaml`` for one module instance."""

    selection: SelectedContractArtifact
    module_instance: str
    definition: ModuleDefinition
    handler_relative_path: str
    handler_class: str

    def action(self, action_id: str) -> ActionDef:
        """Return the exact declared action; unknown action ids fail closed."""
        for candidate in self.definition.actions:
            if candidate.id == action_id:
                return candidate
        raise ImplementationArtifactError(
            f"module {self.module_instance!r} declares no action {action_id!r}"
        )

    def action_request_contract(self, action_id: str) -> ClosedContract:
        """Import the declared action input schema as one closed contract."""
        return import_closed_contract_schema(self.action(action_id).input_schema)


@dataclass(frozen=True, slots=True)
class ResolvedModuleHandlerSource:
    """Exact digest-verified handler source selected for one module instance."""

    artifact: AccountedArtifact
    module_instance: str
    handler_class: str
    content_digest: str
    source_text: str


@dataclass(frozen=True, slots=True)
class ResolvedModuleBaseHandlerSource:
    """Exact digest-verified canonical base-handler source for one module.

    Participates in certification only under a proven
    :class:`WorkspaceHandlerSplitAuthority` for the same module instance.
    """

    artifact: AccountedArtifact
    module_instance: str
    content_digest: str
    source_text: str


@dataclass(frozen=True, slots=True)
class ResolvedModuleActionImplementation:
    """One certified module action: manifest, handler source(s), export proof."""

    module: ResolvedModuleManifest
    handler: ResolvedModuleHandlerSource
    action: ActionDef
    request_contract: ClosedContract
    export_proof: ModuleActionExportProof
    base_handler: ResolvedModuleBaseHandlerSource | None = None


def _prove_canonical_layout(
    address: ArtifactAddress,
    *,
    expected_kind: ArtifactKind,
    expected_owner: LayoutOwner,
    layout_registry: AppLayoutRegistry,
) -> tuple[ArtifactFamily, dict[str, str]]:
    """Prove family, scope, placeholders, and ownership from the layout registry."""
    try:
        match = layout_registry.match_path(address.path, address.path_scope)
    except ValueError as exc:
        raise ImplementationArtifactError(
            f"artifact address {address.path!r} has no canonical layout row in "
            f"scope {address.path_scope.value!r}: {exc}"
        ) from exc
    family = match.family
    if family.requirement is Requirement.PROHIBITED:
        raise ImplementationArtifactError(
            f"artifact address {address.path!r} is prohibited by the canonical layout"
        )
    if family.kind is not expected_kind:
        raise ImplementationArtifactError(
            f"artifact address {address.path!r} resolves to family "
            f"{family.kind.value!r}, not {expected_kind.value!r}"
        )
    if family.owner is not expected_owner:
        raise ImplementationArtifactError(
            f"artifact family {family.kind.value!r} is owned by {family.owner.value!r}, "
            f"not {expected_owner.value!r}"
        )

    instance: dict[str, str] = {}
    scope_placeholder = _INSTANCE_PLACEHOLDER_BY_SCOPE.get(address.path_scope)
    if scope_placeholder is not None:
        names = tuple(name for name, _value in address.placeholder_values)
        if names != (scope_placeholder.value,):
            raise ImplementationArtifactError(
                f"{address.path_scope.value} artifact addresses require exactly the "
                f"{scope_placeholder.value!r} placeholder, got {names!r}"
            )
        raw_value = address.placeholder_values[0][1]
        try:
            instance[scope_placeholder.value] = canonical_instance_identity_value(raw_value)
        except ValueError as exc:
            raise ImplementationArtifactError(str(exc)) from exc
    for name, value in match.values.items():
        try:
            instance[name.value] = canonical_instance_identity_value(value)
        except ValueError as exc:
            raise ImplementationArtifactError(str(exc)) from exc
    return family, instance


def _require_instance(instance: dict[str, str], placeholder: PlaceholderIdentifier) -> str:
    value = instance.get(placeholder.value)
    if value is None:
        raise ImplementationArtifactError(
            f"canonical layout proof produced no {placeholder.value!r} instance identity"
        )
    return value


async def _resolve_selection_bytes(
    selection: SelectedContractArtifact,
    *,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    expected_kind: ArtifactKind,
    expected_owner: LayoutOwner,
    layout_registry: AppLayoutRegistry,
) -> tuple[SelectedContractArtifact, bytes, dict[str, str]]:
    """Cold-verify one selection and return its exact bytes and instance identity."""
    try:
        verified = SelectedContractArtifact.model_validate(selection.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError(
            f"selected contract artifact failed cold validation: {exc}"
        ) from exc
    if requesting_scope != verified.contract_ref.scope:
        raise ImplementationArtifactError(
            "cross-scope implementation artifact selection fails closed"
        )
    family, instance = _prove_canonical_layout(
        verified.address,
        expected_kind=expected_kind,
        expected_owner=expected_owner,
        layout_registry=layout_registry,
    )
    if verified.contract_ref.artifact_family != family.kind.value:
        raise ImplementationArtifactError(
            f"reference artifact family {verified.contract_ref.artifact_family!r} does "
            f"not equal the proven layout family {family.kind.value!r}"
        )
    try:
        data = await content_store.get_verified_blob(verified.contract_ref.content_digest)
    except (ContentNotFoundError, ContentIntegrityError) as exc:
        raise ImplementationArtifactError(
            f"selected contract bytes did not resolve exactly: {exc}"
        ) from exc
    return verified, data, instance


def _parse_yaml_mapping(data: bytes, *, description: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImplementationArtifactError(f"{description} bytes are not UTF-8") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ImplementationArtifactError(f"{description} bytes are not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ImplementationArtifactError(f"{description} document root must be a mapping")
    return raw


def _require_schema_version(declared: str, ref: ChildContractRef, *, description: str) -> None:
    if declared != ref.contract_schema_version:
        raise ImplementationArtifactError(
            f"{description} declares schema version {declared!r} but the reference "
            f"pins {ref.contract_schema_version!r}"
        )


async def resolve_workflow_orchestrator_artifact(
    selection: SelectedContractArtifact,
    *,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    layout_registry: AppLayoutRegistry | None = None,
) -> ResolvedWorkflowOrchestrator:
    """Resolve one workflow's exact ``orchestrator.yaml`` implementation document."""
    from mozaiksai.core.workflow.declarative.contracts import (
        OrchestratorConfig,
        parse_orchestrator_config,
    )

    verified, data, instance = await _resolve_selection_bytes(
        selection,
        content_store=content_store,
        requesting_scope=requesting_scope,
        expected_kind=ArtifactKind.WORKFLOW_MANIFEST,
        expected_owner=LayoutOwner.WORKFLOW,
        layout_registry=layout_registry or default_app_layout_registry(),
    )
    raw = _parse_yaml_mapping(data, description="orchestrator.yaml")
    try:
        document = parse_orchestrator_config(raw)
        config = OrchestratorConfig.model_validate(raw)
    except ValueError as exc:
        raise ImplementationArtifactError(
            f"orchestrator.yaml bytes failed the strict document contract: {exc}"
        ) from exc
    _require_schema_version(
        config.schema_version, verified.contract_ref, description="orchestrator.yaml"
    )
    return ResolvedWorkflowOrchestrator(
        selection=verified,
        workflow_instance=_require_instance(instance, PlaceholderIdentifier.WORKFLOW_ID),
        config=config,
        document=document,
    )


async def resolve_workflow_structured_outputs_artifact(
    selection: SelectedContractArtifact,
    *,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    layout_registry: AppLayoutRegistry | None = None,
) -> ResolvedWorkflowStructuredOutputs:
    """Resolve one workflow's exact ``structured_outputs.yaml`` implementation document."""
    from mozaiksai.core.workflow.declarative.contracts import (
        StructuredOutputsConfig,
        parse_structured_outputs_config,
    )

    verified, data, instance = await _resolve_selection_bytes(
        selection,
        content_store=content_store,
        requesting_scope=requesting_scope,
        expected_kind=ArtifactKind.WORKFLOW_CONFIG,
        expected_owner=LayoutOwner.WORKFLOW,
        layout_registry=layout_registry or default_app_layout_registry(),
    )
    if verified.address.path != "structured_outputs.yaml":
        raise ImplementationArtifactError(
            f"structured-output selection must address structured_outputs.yaml, "
            f"got {verified.address.path!r}"
        )
    raw = _parse_yaml_mapping(data, description="structured_outputs.yaml")
    try:
        document = parse_structured_outputs_config(raw)
        config = StructuredOutputsConfig.model_validate(raw)
    except ValueError as exc:
        raise ImplementationArtifactError(
            f"structured_outputs.yaml bytes failed the strict document contract: {exc}"
        ) from exc
    _require_schema_version(
        config.schema_version, verified.contract_ref, description="structured_outputs.yaml"
    )
    return ResolvedWorkflowStructuredOutputs(
        selection=verified,
        workflow_instance=_require_instance(instance, PlaceholderIdentifier.WORKFLOW_ID),
        config=config,
        document=document,
    )


def pair_workflow_implementation_artifacts(
    orchestrator: ResolvedWorkflowOrchestrator,
    structured_outputs: ResolvedWorkflowStructuredOutputs,
) -> ResolvedWorkflowImplementation:
    """Pair the two documents of one workflow instance; cross-workflow pairs reject."""
    if (
        orchestrator.selection.contract_ref.scope
        != structured_outputs.selection.contract_ref.scope
    ):
        raise ImplementationArtifactError(
            "workflow implementation documents belong to different execution scopes"
        )
    if orchestrator.workflow_instance != structured_outputs.workflow_instance:
        raise ImplementationArtifactError(
            f"structured outputs of workflow {structured_outputs.workflow_instance!r} "
            f"cannot implement workflow {orchestrator.workflow_instance!r}"
        )
    return ResolvedWorkflowImplementation(
        orchestrator=orchestrator, structured_outputs=structured_outputs
    )


def resolve_selected_structured_output_contract(
    implementation: ResolvedWorkflowImplementation,
    ref: StructuredOutputContractRef,
    *,
    exact_model_ids: frozenset[str] = frozenset(),
) -> type[BaseModel]:
    """Resolve one #485 contract ref against exactly this selected configuration.

    A same-schema contract from another workflow is not interchangeable: the
    reference must name this implementation's runtime workflow, and the model
    is compiled only from this selection's exact parsed document.
    """
    try:
        verified_ref = StructuredOutputContractRef.model_validate(ref.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError(
            f"structured-output contract ref failed cold validation: {exc}"
        ) from exc
    if verified_ref.workflow_name != implementation.workflow_name:
        raise ImplementationArtifactError(
            f"structured-output contract of workflow {verified_ref.workflow_name!r} is "
            f"not interchangeable into workflow {implementation.workflow_name!r}"
        )
    try:
        return resolve_structured_output_contract_ref(
            verified_ref,
            configs={verified_ref.workflow_name: implementation.structured_outputs.document},
            exact_model_ids=exact_model_ids,
        )
    except ValueError as exc:
        raise ImplementationArtifactError(
            f"structured-output contract did not resolve against the selected "
            f"configuration: {exc}"
        ) from exc


async def resolve_module_manifest_artifact(
    selection: SelectedContractArtifact,
    *,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    layout_registry: AppLayoutRegistry | None = None,
) -> ResolvedModuleManifest:
    """Resolve one module's exact ``module.yaml`` implementation document."""
    from mozaiksai.core.runtime.app.module_loader import (
        ModuleDefinition,
        _validate_entrypoint,
    )

    verified, data, instance = await _resolve_selection_bytes(
        selection,
        content_store=content_store,
        requesting_scope=requesting_scope,
        expected_kind=ArtifactKind.MODULE_MANIFEST,
        expected_owner=LayoutOwner.MODULE,
        layout_registry=layout_registry or default_app_layout_registry(),
    )
    raw = _parse_yaml_mapping(data, description="module.yaml")
    try:
        definition = ModuleDefinition.model_validate(raw)
    except ValueError as exc:
        raise ImplementationArtifactError(
            f"module.yaml bytes failed the strict document contract: {exc}"
        ) from exc
    _require_schema_version(
        definition.schema_version, verified.contract_ref, description="module.yaml"
    )
    module_instance = _require_instance(instance, PlaceholderIdentifier.MODULE_ID)
    if definition.module.id != module_instance:
        raise ImplementationArtifactError(
            f"module manifest declares module {definition.module.id!r} and cannot be "
            f"selected for module instance {module_instance!r}"
        )
    handler_relative_path, handler_class = _validate_entrypoint(definition.module.handler)
    return ResolvedModuleManifest(
        selection=verified,
        module_instance=module_instance,
        definition=definition,
        handler_relative_path=handler_relative_path,
        handler_class=handler_class,
    )


async def resolve_module_handler_source(
    artifact: AccountedArtifact,
    *,
    module: ResolvedModuleManifest,
    content_store: ArtifactContentStore,
    layout_registry: AppLayoutRegistry | None = None,
) -> ResolvedModuleHandlerSource:
    """Resolve the exact handler source the module manifest declares.

    The selection is an ``AccountedArtifact`` whose ``content_digest`` is
    mandatory at this boundary: an address without exact content identity is
    not a certified selection.
    """
    try:
        verified = AccountedArtifact.model_validate(artifact.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError(
            f"handler source artifact failed cold validation: {exc}"
        ) from exc
    if verified.content_digest is None:
        raise ImplementationArtifactError(
            "certified handler selection requires a non-null content digest"
        )
    _family, instance = _prove_canonical_layout(
        verified.address,
        expected_kind=ArtifactKind.MODULE_BACKEND_HANDLER,
        expected_owner=LayoutOwner.MODULE,
        layout_registry=layout_registry or default_app_layout_registry(),
    )
    module_instance = _require_instance(instance, PlaceholderIdentifier.MODULE_ID)
    if module_instance != module.module_instance:
        raise ImplementationArtifactError(
            f"handler source of module {module_instance!r} cannot implement module "
            f"{module.module_instance!r}"
        )
    if verified.address.path_scope is PathScope.MODULE_RELATIVE:
        expected_path = module.handler_relative_path
    else:
        expected_path = f"modules/{module.module_instance}/{module.handler_relative_path}"
    if verified.address.path != expected_path:
        raise ImplementationArtifactError(
            f"module {module.module_instance!r} declares its handler at "
            f"{expected_path!r}, not {verified.address.path!r}"
        )
    try:
        data = await content_store.get_verified_blob(verified.content_digest)
    except (ContentNotFoundError, ContentIntegrityError) as exc:
        raise ImplementationArtifactError(
            f"selected handler bytes did not resolve exactly: {exc}"
        ) from exc
    try:
        source_text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImplementationArtifactError("handler source bytes are not UTF-8") from exc
    return ResolvedModuleHandlerSource(
        artifact=verified,
        module_instance=module_instance,
        handler_class=module.handler_class,
        content_digest=verified.content_digest,
        source_text=source_text,
    )


def workspace_handler_split_authority_from_pack_contract(
    contract: Mapping[str, Any], *, module_instance: str
) -> WorkspaceHandlerSplitAuthority:
    """Prove the canonical workspace_handler_split ownership from a pack contract.

    The owning capability-pack ``contract.yaml`` must declare, in
    ``required_outputs``, this module's ``handler.py`` as the preserved
    workspace-owned leaf (``owner: workspace``) and its ``base_handler.py`` as
    the regenerated template-owned implementation (``owner: templates``, the
    canonical materializer default).  Absent or inconsistent declarations
    reject: the split is contract authority, never a filename coincidence.
    """
    if not isinstance(contract, Mapping):
        raise ImplementationArtifactError("pack contract must be a mapping document")
    contract_type = str(contract.get("contract_type") or "").strip()
    if contract_type != "build_pack_instructions":
        raise ImplementationArtifactError(
            "workspace_handler_split authority requires a build_pack_instructions "
            f"contract, got {contract_type!r}"
        )
    contract_id = str(contract.get("contract_id") or "").strip()
    if not contract_id:
        raise ImplementationArtifactError("pack contract declares no contract_id")
    instance = canonical_instance_identity_value(str(module_instance or "").strip())
    handler_path = f"modules/{instance}/backend/handler.py"
    base_handler_path = f"modules/{instance}/backend/base_handler.py"
    owners: dict[str, str] = {}
    for entry in contract.get("required_outputs") or []:
        if not isinstance(entry, Mapping):
            continue
        path = str(entry.get("path") or "").strip()
        if path in {handler_path, base_handler_path}:
            owners[path] = str(entry.get("owner") or "templates").strip()
    if handler_path not in owners:
        raise ImplementationArtifactError(
            f"pack contract {contract_id!r} does not declare {handler_path!r}; "
            "the workspace_handler_split authority is absent"
        )
    if base_handler_path not in owners:
        raise ImplementationArtifactError(
            f"pack contract {contract_id!r} does not declare {base_handler_path!r}; "
            "the workspace_handler_split authority is absent"
        )
    if owners[handler_path] != "workspace":
        raise ImplementationArtifactError(
            f"pack contract {contract_id!r} declares {handler_path!r} with owner "
            f"{owners[handler_path]!r}; the canonical split requires the preserved "
            "leaf to be workspace-owned"
        )
    if owners[base_handler_path] != "templates":
        raise ImplementationArtifactError(
            f"pack contract {contract_id!r} declares {base_handler_path!r} with owner "
            f"{owners[base_handler_path]!r}; the canonical split requires the "
            "regenerated implementation to be template-owned"
        )
    return WorkspaceHandlerSplitAuthority(
        contract_id=contract_id,
        module_instance=instance,
        handler_path=handler_path,
        base_handler_path=base_handler_path,
    )


async def resolve_module_base_handler_source(
    artifact: AccountedArtifact,
    *,
    module: ResolvedModuleManifest,
    handler: ResolvedModuleHandlerSource,
    split_authority: WorkspaceHandlerSplitAuthority,
    content_store: ArtifactContentStore,
    layout_registry: AppLayoutRegistry | None = None,
) -> ResolvedModuleBaseHandlerSource:
    """Resolve the exact canonical base-handler source for one module.

    The base participates only under the module's proven split authority, in
    the SAME module scope and instance as the selected leaf handler, at the
    canonical ``base_handler.py`` sibling of the declared handler entrypoint,
    with a mandatory verified content digest.
    """
    try:
        verified_authority = WorkspaceHandlerSplitAuthority.model_validate(
            split_authority.model_dump(mode="json")
        )
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError(
            f"workspace_handler_split authority failed cold validation: {exc}"
        ) from exc
    if verified_authority.module_instance != module.module_instance:
        raise ImplementationArtifactError(
            f"workspace_handler_split authority for module "
            f"{verified_authority.module_instance!r} cannot certify module "
            f"{module.module_instance!r}"
        )
    try:
        verified = AccountedArtifact.model_validate(artifact.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError(
            f"base-handler source artifact failed cold validation: {exc}"
        ) from exc
    if verified.content_digest is None:
        raise ImplementationArtifactError(
            "certified base-handler selection requires a non-null content digest"
        )
    _family, instance = _prove_canonical_layout(
        verified.address,
        expected_kind=ArtifactKind.MODULE_BACKEND_BASE_HANDLER,
        expected_owner=LayoutOwner.MODULE,
        layout_registry=layout_registry or default_app_layout_registry(),
    )
    module_instance = _require_instance(instance, PlaceholderIdentifier.MODULE_ID)
    if module_instance != module.module_instance:
        raise ImplementationArtifactError(
            f"base handler of module {module_instance!r} cannot implement module "
            f"{module.module_instance!r}"
        )
    if verified.address.path_scope is not handler.artifact.address.path_scope:
        raise ImplementationArtifactError(
            "leaf handler and base handler must be selected in the same module scope"
        )
    base_relative = posixpath.join(
        posixpath.dirname(module.handler_relative_path), "base_handler.py"
    )
    if verified.address.path_scope is PathScope.MODULE_RELATIVE:
        expected_path = base_relative
    else:
        expected_path = f"modules/{module.module_instance}/{base_relative}"
    if verified.address.path != expected_path:
        raise ImplementationArtifactError(
            f"module {module.module_instance!r} declares its canonical base handler "
            f"at {expected_path!r}, not {verified.address.path!r}"
        )
    try:
        data = await content_store.get_verified_blob(verified.content_digest)
    except (ContentNotFoundError, ContentIntegrityError) as exc:
        raise ImplementationArtifactError(
            f"selected base-handler bytes did not resolve exactly: {exc}"
        ) from exc
    try:
        source_text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImplementationArtifactError("base-handler source bytes are not UTF-8") from exc
    return ResolvedModuleBaseHandlerSource(
        artifact=verified,
        module_instance=module_instance,
        content_digest=verified.content_digest,
        source_text=source_text,
    )


def _iter_scope_statements(body: list[ast.stmt]):
    """Yield every statement of one scope without entering nested def/class bodies."""
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.stmt):
            yield node
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
        pending.extend(ast.iter_child_nodes(node))


def _target_names(target: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _statement_binds_name(stmt: ast.stmt, name: str) -> bool:
    """True when one scope statement (re)binds ``name`` in that scope."""
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return stmt.name == name
    if isinstance(stmt, ast.Assign):
        return any(name in _target_names(target) for target in stmt.targets)
    if isinstance(stmt, ast.AnnAssign | ast.AugAssign):
        return name in _target_names(stmt.target)
    if isinstance(stmt, ast.Delete):
        return any(name in _target_names(target) for target in stmt.targets)
    if isinstance(stmt, ast.For | ast.AsyncFor):
        return name in _target_names(stmt.target)
    if isinstance(stmt, ast.With | ast.AsyncWith):
        return any(
            item.optional_vars is not None and name in _target_names(item.optional_vars)
            for item in stmt.items
        )
    if isinstance(stmt, ast.Import | ast.ImportFrom):
        return any((alias.asname or alias.name.split(".")[0]) == name for alias in stmt.names)
    if isinstance(stmt, ast.Global | ast.Nonlocal):
        return name in stmt.names
    return any(
        isinstance(node, ast.NamedExpr)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
        for node in ast.walk(stmt)
    )


def _reject_dynamic_builtins(statements: list[ast.stmt], *, where: str) -> None:
    for stmt in statements:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _DYNAMIC_EXPORT_BUILTINS
            ):
                raise ImplementationArtifactError(
                    f"{where} calls {node.func.id!r}; the export is not statically provable"
                )


def prove_module_action_export(
    handler: ResolvedModuleHandlerSource, *, handler_method: str
) -> ModuleActionExportProof:
    """Prove the declared class explicitly exports ``handler_method`` in this source.

    The proof is bounded and static: the source is parsed, never executed.  It
    fails closed on every construct that could make the visible definition not
    be the effective export — redefinition, conditional or decorated
    definitions, name rebinding, monkeypatching, ``__getattr__`` tricks, and
    any further reference to the handler class name.
    """
    handler_class = handler.handler_class
    try:
        tree = ast.parse(handler.source_text)
    except SyntaxError as exc:
        raise ImplementationArtifactError(
            f"handler source is not statically parseable: {exc}"
        ) from exc

    module_statements = list(_iter_scope_statements(tree.body))
    class_defs = [
        stmt
        for stmt in module_statements
        if isinstance(stmt, ast.ClassDef) and stmt.name == handler_class
    ]
    if not class_defs:
        raise ImplementationArtifactError(
            f"declared handler class {handler_class!r} is absent from the selected source"
        )
    if len(class_defs) != 1:
        raise ImplementationArtifactError(
            f"handler class {handler_class!r} is bound more than once; the export is "
            "not statically provable"
        )
    class_def = class_defs[0]
    if class_def not in tree.body:
        raise ImplementationArtifactError(
            f"handler class {handler_class!r} is defined conditionally; the export is "
            "not statically provable"
        )
    if class_def.decorator_list or class_def.keywords:
        raise ImplementationArtifactError(
            f"handler class {handler_class!r} uses decorators or class keywords; the "
            "export is not statically provable"
        )
    for stmt in module_statements:
        if stmt is class_def:
            continue
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "__getattr__":
            raise ImplementationArtifactError(
                "module-level __getattr__ defeats static export proof"
            )
        if _statement_binds_name(stmt, handler_class):
            raise ImplementationArtifactError(
                f"handler class {handler_class!r} is rebound outside its definition"
            )
    _reject_dynamic_builtins(module_statements, where="handler module scope")

    class_statements = list(_iter_scope_statements(class_def.body))
    method_defs = [
        stmt
        for stmt in class_statements
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and stmt.name == handler_method
    ]
    if not method_defs:
        raise ImplementationArtifactError(
            f"action handler_method {handler_method!r} is not explicitly defined on "
            f"class {handler_class!r} in the selected source"
        )
    if len(method_defs) != 1:
        raise ImplementationArtifactError(
            f"handler_method {handler_method!r} is bound more than once on class "
            f"{handler_class!r}; the export is not statically provable"
        )
    method_def = method_defs[0]
    if method_def not in class_def.body:
        raise ImplementationArtifactError(
            f"handler_method {handler_method!r} is defined conditionally; the export "
            "is not statically provable"
        )
    if method_def.decorator_list:
        raise ImplementationArtifactError(
            f"handler_method {handler_method!r} is decorated; the export is not "
            "statically provable"
        )
    for stmt in class_statements:
        if stmt is method_def:
            continue
        if (
            isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            and stmt.name in {"__getattr__", "__getattribute__"}
        ):
            raise ImplementationArtifactError(
                f"class-level {stmt.name} defeats static export proof"
            )
        if _statement_binds_name(stmt, handler_method):
            raise ImplementationArtifactError(
                f"handler_method {handler_method!r} is rebound in the class body; the "
                "export is not statically provable"
            )
    _reject_dynamic_builtins(class_statements, where="handler class scope")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == handler_class:
            raise ImplementationArtifactError(
                f"handler class {handler_class!r} is referenced dynamically; the "
                "export is not statically provable"
            )
    return ModuleActionExportProof(
        mode=HandlerCertificationMode.EXPLICIT_HANDLER,
        handler_class=handler_class,
        handler_method=handler_method,
        content_digest=handler.content_digest,
    )


def _leaf_explicitly_defines_method(source_text: str, *, handler_class: str, handler_method: str) -> bool:
    """Cheap mode probe: does the leaf directly define the selected method?

    Only decides WHICH bounded certification mode applies; every actual
    guarantee is enforced by the full mode-specific proof afterwards.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return True  # let the explicit proof produce the canonical rejection
    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == handler_class:
            return any(
                isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                and child.name == handler_method
                for child in stmt.body
            )
    return True  # absent class: the explicit proof owns that rejection too


def _prove_canonical_leaf_subclass(
    handler: ResolvedModuleHandlerSource, *, handler_method: str
) -> str:
    """Prove the leaf is the bounded canonical subclass and return its base name.

    Exactly one top-level handler class with exactly one plain-name base,
    bound by exactly one ``from .base_handler import <Base>`` — no aliasing,
    no star imports, no second base, no mixins, no conditional or dynamic
    construction, no rebinding, no ``__getattr__``/setattr tricks, and no
    leaf binding of the selected method (a leaf override is certified through
    EXPLICIT_HANDLER instead).
    """
    handler_class = handler.handler_class
    try:
        tree = ast.parse(handler.source_text)
    except SyntaxError as exc:
        raise ImplementationArtifactError(
            f"handler source is not statically parseable: {exc}"
        ) from exc

    module_statements = list(_iter_scope_statements(tree.body))
    class_defs = [
        stmt
        for stmt in module_statements
        if isinstance(stmt, ast.ClassDef) and stmt.name == handler_class
    ]
    if not class_defs:
        raise ImplementationArtifactError(
            f"declared handler class {handler_class!r} is absent from the selected source"
        )
    if len(class_defs) != 1:
        raise ImplementationArtifactError(
            f"handler class {handler_class!r} is bound more than once; the export is "
            "not statically provable"
        )
    class_def = class_defs[0]
    if class_def not in tree.body:
        raise ImplementationArtifactError(
            f"handler class {handler_class!r} is defined conditionally; the export is "
            "not statically provable"
        )
    if class_def.decorator_list or class_def.keywords:
        raise ImplementationArtifactError(
            f"handler class {handler_class!r} uses decorators or class keywords; the "
            "export is not statically provable"
        )
    if len(class_def.bases) != 1:
        raise ImplementationArtifactError(
            f"canonical split leaf {handler_class!r} must declare exactly one base "
            f"class, found {len(class_def.bases)}; multiple inheritance and mixins "
            "are not certifiable"
        )
    base_expr = class_def.bases[0]
    if not isinstance(base_expr, ast.Name):
        raise ImplementationArtifactError(
            f"canonical split leaf {handler_class!r} base class must be a plain "
            "imported name"
        )
    base_name = base_expr.id

    canonical_imports: list[ast.ImportFrom] = []
    for stmt in module_statements:
        if stmt is class_def:
            continue
        if isinstance(stmt, ast.ImportFrom):
            if any(alias.name == "*" for alias in stmt.names):
                raise ImplementationArtifactError(
                    "star imports defeat static export proof"
                )
            binds_base = any(
                (alias.asname or alias.name) == base_name for alias in stmt.names
            )
            if binds_base:
                if (
                    stmt.level == 1
                    and stmt.module == "base_handler"
                    and len(stmt.names) == 1
                    and stmt.names[0].name == base_name
                    and stmt.names[0].asname is None
                ):
                    canonical_imports.append(stmt)
                    continue
                raise ImplementationArtifactError(
                    f"base class {base_name!r} must be imported exactly as "
                    f"'from .base_handler import {base_name}'"
                )
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "__getattr__":
            raise ImplementationArtifactError(
                "module-level __getattr__ defeats static export proof"
            )
        if _statement_binds_name(stmt, handler_class):
            raise ImplementationArtifactError(
                f"handler class {handler_class!r} is rebound outside its definition"
            )
        if _statement_binds_name(stmt, base_name):
            raise ImplementationArtifactError(
                f"base class {base_name!r} is rebound outside its canonical import"
            )
    if len(canonical_imports) != 1:
        raise ImplementationArtifactError(
            f"base class {base_name!r} must be bound by exactly one "
            f"'from .base_handler import {base_name}' import"
        )
    _reject_dynamic_builtins(module_statements, where="handler module scope")

    class_statements = list(_iter_scope_statements(class_def.body))
    for stmt in class_statements:
        if (
            isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            and stmt.name in {"__getattr__", "__getattribute__"}
        ):
            raise ImplementationArtifactError(
                f"class-level {stmt.name} defeats static export proof"
            )
        if _statement_binds_name(stmt, handler_method):
            raise ImplementationArtifactError(
                f"handler_method {handler_method!r} is bound dynamically in the leaf "
                "class body; the export is not statically provable"
            )
    _reject_dynamic_builtins(class_statements, where="handler class scope")

    base_references = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == base_name
    ]
    if len(base_references) != 1 or base_references[0] is not base_expr:
        raise ImplementationArtifactError(
            f"base class {base_name!r} may be referenced only as the leaf's single "
            "base; other references are not statically provable"
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == handler_class:
            raise ImplementationArtifactError(
                f"handler class {handler_class!r} is referenced dynamically; the "
                "export is not statically provable"
            )
    return base_name


def _prove_base_handler_method(
    base_handler: ResolvedModuleBaseHandlerSource,
    *,
    base_class: str,
    handler_method: str,
) -> None:
    """Prove the canonical base class explicitly defines the selected method.

    The base class must be the single top-level class of the regenerated
    base-handler source, with no bases of its own (no transitive
    inheritance), no dynamic construction, and an explicit undecorated
    definition of the selected method.
    """
    try:
        tree = ast.parse(base_handler.source_text)
    except SyntaxError as exc:
        raise ImplementationArtifactError(
            f"base-handler source is not statically parseable: {exc}"
        ) from exc

    module_statements = list(_iter_scope_statements(tree.body))
    class_defs = [
        stmt
        for stmt in module_statements
        if isinstance(stmt, ast.ClassDef) and stmt.name == base_class
    ]
    if not class_defs:
        raise ImplementationArtifactError(
            f"canonical base class {base_class!r} is absent from the selected "
            "base-handler source"
        )
    if len(class_defs) != 1:
        raise ImplementationArtifactError(
            f"base class {base_class!r} is bound more than once; the export is not "
            "statically provable"
        )
    class_def = class_defs[0]
    if class_def not in tree.body:
        raise ImplementationArtifactError(
            f"base class {base_class!r} is defined conditionally; the export is not "
            "statically provable"
        )
    if class_def.decorator_list or class_def.keywords:
        raise ImplementationArtifactError(
            f"base class {base_class!r} uses decorators or class keywords; the "
            "export is not statically provable"
        )
    if class_def.bases:
        raise ImplementationArtifactError(
            f"canonical base class {base_class!r} must not inherit from anything; "
            "transitive inheritance is not certifiable"
        )
    for stmt in module_statements:
        if stmt is class_def:
            continue
        if isinstance(stmt, ast.ImportFrom) and any(
            alias.name == "*" for alias in stmt.names
        ):
            raise ImplementationArtifactError("star imports defeat static export proof")
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "__getattr__":
            raise ImplementationArtifactError(
                "module-level __getattr__ defeats static export proof"
            )
        if _statement_binds_name(stmt, base_class):
            raise ImplementationArtifactError(
                f"base class {base_class!r} is rebound outside its definition"
            )
    _reject_dynamic_builtins(module_statements, where="base-handler module scope")

    class_statements = list(_iter_scope_statements(class_def.body))
    method_defs = [
        stmt
        for stmt in class_statements
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and stmt.name == handler_method
    ]
    if not method_defs:
        raise ImplementationArtifactError(
            f"action handler_method {handler_method!r} is not explicitly defined on "
            f"canonical base class {base_class!r} in the selected source"
        )
    if len(method_defs) != 1:
        raise ImplementationArtifactError(
            f"handler_method {handler_method!r} is bound more than once on base "
            f"class {base_class!r}; the export is not statically provable"
        )
    method_def = method_defs[0]
    if method_def not in class_def.body:
        raise ImplementationArtifactError(
            f"handler_method {handler_method!r} is defined conditionally; the export "
            "is not statically provable"
        )
    if method_def.decorator_list:
        raise ImplementationArtifactError(
            f"handler_method {handler_method!r} is decorated; the export is not "
            "statically provable"
        )
    for stmt in class_statements:
        if stmt is method_def:
            continue
        if (
            isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            and stmt.name in {"__getattr__", "__getattribute__"}
        ):
            raise ImplementationArtifactError(
                f"class-level {stmt.name} defeats static export proof"
            )
        if _statement_binds_name(stmt, handler_method):
            raise ImplementationArtifactError(
                f"handler_method {handler_method!r} is rebound in the base class "
                "body; the export is not statically provable"
            )
    _reject_dynamic_builtins(class_statements, where="base-handler class scope")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == base_class:
            raise ImplementationArtifactError(
                f"base class {base_class!r} is referenced dynamically; the export is "
                "not statically provable"
            )


def certify_module_action_export(
    handler: ResolvedModuleHandlerSource,
    *,
    handler_method: str,
    base_handler: ResolvedModuleBaseHandlerSource | None = None,
) -> ModuleActionExportProof:
    """Certify one action export through exactly one of the two bounded modes.

    A leaf that explicitly defines the selected method certifies as
    ``EXPLICIT_HANDLER`` (the strict single-source proof).  Otherwise, and
    only when the canonical base-handler source is selected under proven
    split authority, the ``CANONICAL_BASE_HANDLER`` proof runs over exactly
    the two canonical sources.  No other source may participate.
    """
    if base_handler is not None and base_handler.module_instance != handler.module_instance:
        raise ImplementationArtifactError(
            f"base handler of module {base_handler.module_instance!r} cannot certify "
            f"module {handler.module_instance!r}"
        )
    if _leaf_explicitly_defines_method(
        handler.source_text,
        handler_class=handler.handler_class,
        handler_method=handler_method,
    ):
        return prove_module_action_export(handler, handler_method=handler_method)
    if base_handler is None:
        # Preserve the strict single-source rejection.
        return prove_module_action_export(handler, handler_method=handler_method)
    base_class = _prove_canonical_leaf_subclass(handler, handler_method=handler_method)
    _prove_base_handler_method(
        base_handler, base_class=base_class, handler_method=handler_method
    )
    return ModuleActionExportProof(
        mode=HandlerCertificationMode.CANONICAL_BASE_HANDLER,
        handler_class=handler.handler_class,
        handler_method=handler_method,
        content_digest=handler.content_digest,
        base_handler_class=base_class,
        base_content_digest=base_handler.content_digest,
    )


async def resolve_module_action_implementation(
    module_selection: SelectedContractArtifact,
    handler_artifact: AccountedArtifact,
    *,
    action_id: str,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    layout_registry: AppLayoutRegistry | None = None,
    base_handler_artifact: AccountedArtifact | None = None,
    split_authority: WorkspaceHandlerSplitAuthority | None = None,
) -> ResolvedModuleActionImplementation:
    """Resolve and prove one certified module action implementation end to end.

    Without a base selection, certification is the strict single-source
    ``EXPLICIT_HANDLER`` proof.  A ``base_handler_artifact`` participates only
    together with the module's proven :class:`WorkspaceHandlerSplitAuthority`;
    a base selection without split authority rejects.
    """
    registry = layout_registry or default_app_layout_registry()
    module = await resolve_module_manifest_artifact(
        module_selection,
        content_store=content_store,
        requesting_scope=requesting_scope,
        layout_registry=registry,
    )
    handler = await resolve_module_handler_source(
        handler_artifact,
        module=module,
        content_store=content_store,
        layout_registry=registry,
    )
    base_handler: ResolvedModuleBaseHandlerSource | None = None
    if base_handler_artifact is not None:
        if split_authority is None:
            raise ImplementationArtifactError(
                "a base-handler selection requires the module's proven "
                "workspace_handler_split authority"
            )
        base_handler = await resolve_module_base_handler_source(
            base_handler_artifact,
            module=module,
            handler=handler,
            split_authority=split_authority,
            content_store=content_store,
            layout_registry=registry,
        )
    action = module.action(action_id)
    export_proof = certify_module_action_export(
        handler, handler_method=action.handler_method, base_handler=base_handler
    )
    return ResolvedModuleActionImplementation(
        module=module,
        handler=handler,
        action=action,
        request_contract=module.action_request_contract(action_id),
        export_proof=export_proof,
        base_handler=base_handler,
    )


__all__ = [
    "HandlerCertificationMode",
    "ImplementationArtifactError",
    "ModuleActionExportProof",
    "ResolvedModuleActionImplementation",
    "ResolvedModuleBaseHandlerSource",
    "ResolvedModuleHandlerSource",
    "ResolvedModuleManifest",
    "ResolvedWorkflowImplementation",
    "ResolvedWorkflowOrchestrator",
    "ResolvedWorkflowStructuredOutputs",
    "SelectedContractArtifact",
    "WorkspaceHandlerSplitAuthority",
    "certify_module_action_export",
    "pair_workflow_implementation_artifacts",
    "prove_module_action_export",
    "resolve_module_action_implementation",
    "resolve_module_base_handler_source",
    "resolve_module_handler_source",
    "resolve_module_manifest_artifact",
    "resolve_selected_structured_output_contract",
    "resolve_workflow_orchestrator_artifact",
    "resolve_workflow_structured_outputs_artifact",
]
