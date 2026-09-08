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

Module handler sources additionally carry a bounded static export proof with
exactly two certification modes.  ``EXPLICIT_HANDLER`` certifies a true
standalone one-source class: the declared handler class has zero bases and
explicitly defines the selected ``handler_method``.  ``CANONICAL_BASE_HANDLER``
certifies the bounded canonical ``workspace_handler_split`` two-source closure:
the split is proven from the exact verified bytes of the module's owning
capability-pack contract (never asserted by the caller, never inferred from
filenames), and exactly ``handler.py`` plus ``base_handler.py`` participate.
Monkeypatching, ``__getattr__`` tricks, arbitrary inheritance, and every other
dynamic export are rejected, never executed to find out.  Every handler, base
handler, and pack-contract selection is execution-scope-bound: the selection's
:class:`ExecutionAccessScopeRef` must equal both the requesting scope and the
module selection's scope.

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
    {
        "setattr",
        "delattr",
        "getattr",
        "globals",
        "locals",
        "vars",
        "eval",
        "exec",
        "__import__",
        "type",
    }
)

#: ``type X = ...`` statements bind ``X``; the node exists on Python >= 3.12.
_TYPE_ALIAS_NODE: type[ast.stmt] | None = getattr(ast, "TypeAlias", None)


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


class SelectedAccountedArtifact(SemanticsModel):
    """One scope-bound exact source selection: execution scope plus artifact.

    Handler, base-handler, and pack-contract selections all pass through this
    wrapper so no source can be selected without carrying the execution scope
    it was selected under.  Resolution requires the scope to equal both the
    requesting scope and the parent module selection's scope: identical bytes
    under a different tenant or workspace scope are not the same selection.
    """

    scope: ExecutionAccessScopeRef
    artifact: AccountedArtifact


class HandlerCertificationMode(StrEnum):
    """The two bounded implementation-certification modes.

    ``EXPLICIT_HANDLER``: a true standalone one-source class — the declared
    handler class has ZERO bases and explicitly defines the selected
    ``handler_method`` in the selected ``handler.py``.  A class that declares
    any base is not eligible for this mode, even when it explicitly defines
    the selected method.

    ``CANONICAL_BASE_HANDLER``: the bounded canonical
    ``workspace_handler_split`` two-source closure — a preserved
    workspace-owned ``handler.py`` leaf that directly subclasses the single
    regenerated template-owned ``base_handler.py`` class.  Both source
    digests are meaning-bearing for every split-certified action, including a
    leaf override of the selected method.  Exactly these two sources may
    participate; there is no general Python source closure.
    """

    EXPLICIT_HANDLER = "explicit_handler"
    CANONICAL_BASE_HANDLER = "canonical_base_handler"


class HandlerMethodSource(StrEnum):
    """Which certified source explicitly defines the selected method."""

    HANDLER = "handler"
    BASE_HANDLER = "base_handler"


class ModuleActionExportProof(SemanticsModel):
    """Static proof that one certified selection explicitly exports one method.

    ``implementation_digest`` is the certified source-closure identity: for
    ``CANONICAL_BASE_HANDLER`` it covers BOTH meaning-bearing source digests
    plus the exact pack-contract digest that authorized the split, so changing
    only ``base_handler.py``, only the preserved ``handler.py``, or only the
    certification authority changes the certified identity.  Module and
    action identity stay deliberately outside this digest — a future
    ``ImplementationBinding v2`` pins them separately.
    """

    mode: HandlerCertificationMode
    method_source: HandlerMethodSource
    handler_class: str
    handler_method: str
    content_digest: str
    base_handler_class: str | None = None
    base_content_digest: str | None = None
    split_contract_content_digest: str | None = None

    @model_validator(mode="after")
    def _mode_shape(self) -> ModuleActionExportProof:
        if self.mode is HandlerCertificationMode.EXPLICIT_HANDLER:
            if self.method_source is not HandlerMethodSource.HANDLER:
                raise ValueError(
                    "EXPLICIT_HANDLER proofs certify the handler source method"
                )
            if (
                self.base_handler_class is not None
                or self.base_content_digest is not None
                or self.split_contract_content_digest is not None
            ):
                raise ValueError(
                    "EXPLICIT_HANDLER proofs carry no base or pack-contract identity"
                )
        else:
            if self.base_handler_class is None or self.base_content_digest is None:
                raise ValueError(
                    "CANONICAL_BASE_HANDLER proofs require both source identities"
                )
            if self.split_contract_content_digest is None:
                raise ValueError(
                    "CANONICAL_BASE_HANDLER proofs require the pack-contract digest"
                )
        return self

    @property
    def implementation_digest(self) -> str:
        payload: dict[str, str | None] = {
            "mode": self.mode.value,
            "method_source": self.method_source.value,
            "handler_class": self.handler_class,
            "handler_method": self.handler_method,
            "content_digest": self.content_digest,
        }
        if self.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER:
            payload["base_handler_class"] = self.base_handler_class
            payload["base_content_digest"] = self.base_content_digest
            payload["split_contract_content_digest"] = self.split_contract_content_digest
        return canonical_digest(payload)


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

    Participates in certification only under the module's content-resolved
    :class:`ResolvedWorkspaceHandlerSplitAuthority` for the same instance.
    """

    artifact: AccountedArtifact
    module_instance: str
    content_digest: str
    source_text: str


@dataclass(frozen=True, slots=True)
class ResolvedWorkspaceHandlerSplitAuthority:
    """Content-resolved proof that one module uses the canonical handler split.

    Produced only by :func:`resolve_workspace_handler_split_authority`, which
    derives every field from the exact verified bytes of the module's owning
    capability-pack contract.  This is a resolution RESULT, not an input
    token: no public resolution API accepts a caller-authored ownership
    claim, and the certified proof pins ``contract_content_digest`` so
    changing the certification authority invalidates the implementation
    identity.
    """

    contract_selection: SelectedAccountedArtifact
    contract_content_digest: str
    scope: ExecutionAccessScopeRef
    contract_id: str
    module_instance: str
    handler_path: str
    base_handler_path: str


@dataclass(frozen=True, slots=True)
class ResolvedModuleActionImplementation:
    """One certified module action: manifest, handler source(s), export proof.

    ``split_authority`` carries the exact resolved pack-contract authority for
    ``CANONICAL_BASE_HANDLER`` certifications so a future
    ``ImplementationBinding`` can pin and revalidate it.
    """

    module: ResolvedModuleManifest
    handler: ResolvedModuleHandlerSource
    action: ActionDef
    request_contract: ClosedContract
    export_proof: ModuleActionExportProof
    base_handler: ResolvedModuleBaseHandlerSource | None = None
    split_authority: ResolvedWorkspaceHandlerSplitAuthority | None = None


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


def _verify_scoped_source_selection(
    selection: SelectedAccountedArtifact,
    *,
    module: ResolvedModuleManifest,
    requesting_scope: ExecutionAccessScopeRef,
    description: str,
) -> tuple[SelectedAccountedArtifact, str]:
    """Cold-verify one scope-bound source selection against both scopes.

    Every certified source selection must carry the execution scope it was
    selected under, and that scope must equal the requesting scope AND the
    parent module selection's scope.  Identical module ids and identical bytes
    under a different tenant or workspace scope fail closed here, before any
    bytes are read.  Returns the verified selection and its mandatory digest.
    """
    try:
        verified = SelectedAccountedArtifact.model_validate(selection.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError(
            f"{description} selection failed cold validation: {exc}"
        ) from exc
    if verified.scope != requesting_scope:
        raise ImplementationArtifactError(
            f"cross-scope {description} selection fails closed"
        )
    if verified.scope != module.selection.contract_ref.scope:
        raise ImplementationArtifactError(
            f"{description} selection scope does not equal the module "
            "selection's execution scope"
        )
    if verified.artifact.content_digest is None:
        raise ImplementationArtifactError(
            f"certified {description} selection requires a non-null content digest"
        )
    return verified, verified.artifact.content_digest


async def resolve_module_handler_source(
    selection: SelectedAccountedArtifact,
    *,
    module: ResolvedModuleManifest,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    layout_registry: AppLayoutRegistry | None = None,
) -> ResolvedModuleHandlerSource:
    """Resolve the exact handler source the module manifest declares.

    The selection is scope-bound and its ``content_digest`` is mandatory at
    this boundary: an address without exact content identity, or under the
    wrong execution scope, is not a certified selection.
    """
    verified_selection, content_digest = _verify_scoped_source_selection(
        selection,
        module=module,
        requesting_scope=requesting_scope,
        description="handler source",
    )
    verified = verified_selection.artifact
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
        data = await content_store.get_verified_blob(content_digest)
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
        content_digest=content_digest,
        source_text=source_text,
    )


async def resolve_workspace_handler_split_authority(
    contract_selection: SelectedAccountedArtifact,
    *,
    module: ResolvedModuleManifest,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
) -> ResolvedWorkspaceHandlerSplitAuthority:
    """Resolve the workspace_handler_split authority from exact contract bytes.

    The caller supplies only the exact scope-bound pack-contract selection;
    every authority fact — contract type, contract id, canonical path, and the
    ownership of this module's manifest, leaf, and base — is derived from the
    digest-verified bytes.  The selection is a bounded workspace-root
    build-context input: its path must equal
    ``build_context/{contract_id}/contract.yaml`` for the contract id the
    verified bytes themselves declare.  The contract must own THIS module —
    declaring its ``module.yaml`` (canonical ``owner: templates``), its
    ``handler.py`` as the preserved workspace-owned leaf
    (``owner: workspace``), and its ``base_handler.py`` as the regenerated
    template-owned implementation (``owner: templates``).  The verified
    bytes must be unambiguous: every ``required_outputs`` entry must be a
    mapping with a unique path, and authority-relevant ownership must be
    EXPLICIT — this boundary never defaults an omitted owner.  Absent,
    duplicate, inconsistent, or unrelated declarations reject: the split is
    contract authority, never a filename coincidence and never a caller
    assertion.
    """
    verified_selection, contract_digest = _verify_scoped_source_selection(
        contract_selection,
        module=module,
        requesting_scope=requesting_scope,
        description="pack-contract",
    )
    address = verified_selection.artifact.address
    if address.path_scope is not PathScope.WORKSPACE_ROOT:
        raise ImplementationArtifactError(
            "pack-contract selections are bounded workspace-root build-context "
            f"inputs, not {address.path_scope.value!r} artifacts"
        )
    try:
        data = await content_store.get_verified_blob(contract_digest)
    except (ContentNotFoundError, ContentIntegrityError) as exc:
        raise ImplementationArtifactError(
            f"selected pack-contract bytes did not resolve exactly: {exc}"
        ) from exc
    raw = _parse_yaml_mapping(data, description="pack contract.yaml")
    contract_type = str(raw.get("contract_type") or "").strip()
    if contract_type != "build_pack_instructions":
        raise ImplementationArtifactError(
            "workspace_handler_split authority requires a build_pack_instructions "
            f"contract, got {contract_type!r}"
        )
    declared_id = str(raw.get("contract_id") or "").strip()
    if not declared_id:
        raise ImplementationArtifactError("pack contract declares no contract_id")
    try:
        contract_id = canonical_instance_identity_value(declared_id)
    except ValueError as exc:
        raise ImplementationArtifactError(
            f"pack contract_id {declared_id!r} is not a canonical identity: {exc}"
        ) from exc
    expected_contract_path = f"build_context/{contract_id}/contract.yaml"
    if address.path != expected_contract_path:
        raise ImplementationArtifactError(
            f"pack contract {contract_id!r} lives at {expected_contract_path!r}, "
            f"not {address.path!r}"
        )
    manifest_path = f"modules/{module.module_instance}/module.yaml"
    handler_path = f"modules/{module.module_instance}/backend/handler.py"
    base_handler_path = f"modules/{module.module_instance}/backend/base_handler.py"
    owners: dict[str, str] = {}
    seen_paths: set[str] = set()
    for entry in raw.get("required_outputs") or []:
        if not isinstance(entry, Mapping):
            raise ImplementationArtifactError(
                f"pack contract {contract_id!r} has a non-mapping required_outputs "
                "entry; ownership is ambiguous"
            )
        path = str(entry.get("path") or "").strip()
        if path in seen_paths:
            raise ImplementationArtifactError(
                f"pack contract {contract_id!r} declares required output {path!r} "
                "more than once; ownership is ambiguous"
            )
        seen_paths.add(path)
        if path in {manifest_path, handler_path, base_handler_path}:
            declared_owner = entry.get("owner")
            owner = declared_owner.strip() if isinstance(declared_owner, str) else ""
            if not owner:
                raise ImplementationArtifactError(
                    f"pack contract {contract_id!r} declares {path!r} without an "
                    "explicit owner; this certification authority does not default "
                    "ownership"
                )
            owners[path] = owner
    for required in (manifest_path, handler_path, base_handler_path):
        if required not in owners:
            raise ImplementationArtifactError(
                f"pack contract {contract_id!r} does not declare {required!r}; "
                "the workspace_handler_split authority is absent"
            )
    if owners[manifest_path] != "templates":
        raise ImplementationArtifactError(
            f"pack contract {contract_id!r} declares {manifest_path!r} with owner "
            f"{owners[manifest_path]!r}; the canonical manifest owner is 'templates'"
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
    return ResolvedWorkspaceHandlerSplitAuthority(
        contract_selection=verified_selection,
        contract_content_digest=contract_digest,
        scope=verified_selection.scope,
        contract_id=contract_id,
        module_instance=module.module_instance,
        handler_path=handler_path,
        base_handler_path=base_handler_path,
    )


async def _resolve_base_handler_with_authority(
    selection: SelectedAccountedArtifact,
    *,
    module: ResolvedModuleManifest,
    handler: ResolvedModuleHandlerSource,
    split_authority: ResolvedWorkspaceHandlerSplitAuthority,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    layout_registry: AppLayoutRegistry,
) -> ResolvedModuleBaseHandlerSource:
    if split_authority.module_instance != module.module_instance:
        raise ImplementationArtifactError(
            f"workspace_handler_split authority for module "
            f"{split_authority.module_instance!r} cannot certify module "
            f"{module.module_instance!r}"
        )
    verified_selection, content_digest = _verify_scoped_source_selection(
        selection,
        module=module,
        requesting_scope=requesting_scope,
        description="base-handler source",
    )
    verified = verified_selection.artifact
    _family, instance = _prove_canonical_layout(
        verified.address,
        expected_kind=ArtifactKind.MODULE_BACKEND_BASE_HANDLER,
        expected_owner=LayoutOwner.MODULE,
        layout_registry=layout_registry,
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
    authorized_path = f"modules/{module.module_instance}/{base_relative}"
    if authorized_path != split_authority.base_handler_path:
        raise ImplementationArtifactError(
            f"module {module.module_instance!r} declares its handler outside the "
            f"canonical split layout {split_authority.base_handler_path!r}"
        )
    try:
        data = await content_store.get_verified_blob(content_digest)
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
        content_digest=content_digest,
        source_text=source_text,
    )


async def resolve_module_base_handler_source(
    selection: SelectedAccountedArtifact,
    *,
    module: ResolvedModuleManifest,
    handler: ResolvedModuleHandlerSource,
    pack_contract_selection: SelectedAccountedArtifact,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    layout_registry: AppLayoutRegistry | None = None,
) -> ResolvedModuleBaseHandlerSource:
    """Resolve the exact canonical base-handler source for one module.

    The base participates only under the module's content-resolved split
    authority — the caller supplies the exact scope-bound pack-contract
    selection, never a preconstructed ownership claim — in the SAME module
    scope and instance as the selected leaf handler, at the canonical
    ``base_handler.py`` sibling of the declared handler entrypoint, with a
    mandatory verified content digest.
    """
    split_authority = await resolve_workspace_handler_split_authority(
        pack_contract_selection,
        module=module,
        content_store=content_store,
        requesting_scope=requesting_scope,
    )
    return await _resolve_base_handler_with_authority(
        selection,
        module=module,
        handler=handler,
        split_authority=split_authority,
        content_store=content_store,
        requesting_scope=requesting_scope,
        layout_registry=layout_registry or default_app_layout_registry(),
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


def _assignment_target_names(target: ast.expr) -> set[str]:
    """Names bound in the current scope by one assignment/loop/with target.

    Attribute and subscript stores mutate objects, not the scope's namespace;
    they bind no name here (the blanket protected-name reference walk rejects
    them separately).
    """
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return _assignment_target_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        names: set[str] = set()
        for element in target.elts:
            names |= _assignment_target_names(element)
        return names
    return set()


def _pattern_binding_names(pattern: ast.pattern) -> set[str]:
    """Every name one match pattern captures into the current scope."""
    names: set[str] = set()
    if isinstance(pattern, ast.MatchAs):
        if pattern.name is not None:
            names.add(pattern.name)
        if pattern.pattern is not None:
            names |= _pattern_binding_names(pattern.pattern)
    elif isinstance(pattern, ast.MatchStar):
        if pattern.name is not None:
            names.add(pattern.name)
    elif isinstance(pattern, ast.MatchMapping):
        if pattern.rest is not None:
            names.add(pattern.rest)
        for sub_pattern in pattern.patterns:
            names |= _pattern_binding_names(sub_pattern)
    elif isinstance(pattern, ast.MatchSequence | ast.MatchOr):
        for sub_pattern in pattern.patterns:
            names |= _pattern_binding_names(sub_pattern)
    elif isinstance(pattern, ast.MatchClass):
        for sub_pattern in (*pattern.patterns, *pattern.kwd_patterns):
            names |= _pattern_binding_names(sub_pattern)
    return names


def _enclosing_scope_children(node: ast.AST) -> list[ast.AST]:
    """Child nodes of one AST node that evaluate in the ENCLOSING scope.

    Nested function/class/lambda bodies bind their own locals, not this
    scope — but their decorators, argument defaults, annotations, and class
    bases DO evaluate in the enclosing scope and can smuggle bindings (for
    example a walrus inside a default argument).
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        children: list[ast.AST] = [*node.decorator_list, *node.args.defaults]
        children.extend(default for default in node.args.kw_defaults if default is not None)
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *((node.args.vararg,) if node.args.vararg is not None else ()),
            *((node.args.kwarg,) if node.args.kwarg is not None else ()),
        )
        children.extend(arg.annotation for arg in arguments if arg.annotation is not None)
        if node.returns is not None:
            children.append(node.returns)
        return children
    if isinstance(node, ast.Lambda):
        lambda_children: list[ast.AST] = [*node.args.defaults]
        lambda_children.extend(
            default for default in node.args.kw_defaults if default is not None
        )
        return lambda_children
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords]
    return list(ast.iter_child_nodes(node))


def _nested_binding_names(stmt: ast.stmt) -> set[str]:
    """Current-scope bindings created inside one statement by non-target forms.

    Collects exception-handler names (``except E as name``), match-pattern
    captures (``case name`` / ``case [*name]`` / ``case {**name}``), and
    assignment expressions — walrus targets bind in the containing scope,
    including from inside comprehensions.  Nested function/class/lambda
    scopes are not entered except through their enclosing-scope expression
    positions; comprehension iteration targets stay comprehension-local and
    are never collected.
    """
    names: set[str] = set()
    pending: list[ast.AST] = _enclosing_scope_children(stmt)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.ExceptHandler) and node.name is not None:
            names.add(node.name)
        elif isinstance(node, ast.match_case):
            names |= _pattern_binding_names(node.pattern)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        pending.extend(_enclosing_scope_children(node))
    return names


def _statement_binds_name(stmt: ast.stmt, name: str) -> bool:
    """True when one scope statement (re)binds ``name`` in the current scope.

    Closed over the Python 3.12 binding forms: definition statements, import
    aliases, assignment/annotated/augmented assignment targets, deletes, loop
    and with-statement targets, global/nonlocal declarations, type-alias
    statements, exception-handler names, match-pattern captures, and
    assignment expressions.  Nested function/class/lambda scopes bind their
    own locals, not this scope.
    """
    direct: set[str] = set()
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        direct.add(stmt.name)
    elif isinstance(stmt, ast.Assign | ast.Delete):
        for target in stmt.targets:
            direct |= _assignment_target_names(target)
    elif isinstance(stmt, ast.AnnAssign | ast.AugAssign):
        direct |= _assignment_target_names(stmt.target)
    elif isinstance(stmt, ast.For | ast.AsyncFor):
        direct |= _assignment_target_names(stmt.target)
    elif isinstance(stmt, ast.With | ast.AsyncWith):
        for item in stmt.items:
            if item.optional_vars is not None:
                direct |= _assignment_target_names(item.optional_vars)
    elif isinstance(stmt, ast.Import | ast.ImportFrom):
        direct |= {alias.asname or alias.name.split(".")[0] for alias in stmt.names}
    elif isinstance(stmt, ast.Global | ast.Nonlocal):
        direct |= set(stmt.names)
    elif _TYPE_ALIAS_NODE is not None and isinstance(stmt, _TYPE_ALIAS_NODE):
        alias_name = getattr(stmt, "name", None)
        if isinstance(alias_name, ast.Name):
            direct.add(alias_name.id)
    if name in direct:
        return True
    return name in _nested_binding_names(stmt)


def _iter_construction_nodes(stmt: ast.stmt):
    """Every AST node of one scope statement that executes at construction time.

    Class bodies execute when the module is imported, so they are entered;
    function and lambda bodies are deferred and are inspected only through
    their enclosing-scope expression positions (decorators, defaults,
    annotations, bases), consistent with the binding grammar.
    """
    pending: list[ast.AST] = [stmt]
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            pending.extend(_enclosing_scope_children(node))
        elif isinstance(node, ast.ClassDef):
            pending.extend(_enclosing_scope_children(node))
            pending.extend(node.body)
        else:
            pending.extend(ast.iter_child_nodes(node))


def _reject_dynamic_builtins(statements: list[ast.stmt], *, where: str) -> None:
    """Reject direct dynamic export/execution primitives in construction scope.

    This is a bounded OWN-SOURCE export/binding proof, not a proof of
    arbitrary imported dependency behavior.  Within the certified source's
    module/class construction scope it fails closed on any reference to a
    dynamic primitive name (``exec``, ``eval``, ``setattr``, ...), on
    importing such a primitive (or ``*``) from ``builtins``, and on any use
    of a name bound to the ``builtins`` module (including ``__builtins__``)
    — so ``builtins.exec``, aliased imports, and simple rebinding of a
    primitive name cannot reach the same escape.  Function and lambda bodies
    are deferred execution and are out of scope by design.
    """
    builtins_aliases = {"__builtins__"}
    for stmt in statements:
        for node in _iter_construction_nodes(stmt):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "builtins" or alias.name.startswith("builtins."):
                        builtins_aliases.add(alias.asname or alias.name.split(".")[0])
    for stmt in statements:
        for node in _iter_construction_nodes(stmt):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module == "builtins"
            ):
                for alias in node.names:
                    if alias.name == "*" or alias.name in _DYNAMIC_EXPORT_BUILTINS:
                        raise ImplementationArtifactError(
                            f"{where} imports {alias.name!r} from builtins; the export "
                            "is not statically provable"
                        )
            if isinstance(node, ast.Name):
                if node.id in _DYNAMIC_EXPORT_BUILTINS:
                    raise ImplementationArtifactError(
                        f"{where} references dynamic export primitive {node.id!r}; the "
                        "export is not statically provable"
                    )
                if node.id in builtins_aliases:
                    raise ImplementationArtifactError(
                        f"{where} accesses the builtins module through {node.id!r}; the "
                        "export is not statically provable"
                    )


def _permitted_lambda_binding(stmt: ast.stmt) -> tuple[str, ast.Lambda] | None:
    """The ONLY permitted construction-scope lambda position: a simple binding.

    ``helper = lambda: ...`` or ``helper: T = lambda: ...`` — exactly one
    plain ``Name`` storage target with the lambda as the ENTIRE direct value.
    Returns the bound name and the lambda node, or ``None``.  A walrus
    (``(f := lambda: ...)``) is NOT a permitted binding: it is
    expression-valued and lets the new callable object flow directly into
    another construction-time operation without a subsequent Name Load.
    """
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and isinstance(stmt.value, ast.Lambda)
    ):
        return stmt.targets[0].id, stmt.value
    if (
        isinstance(stmt, ast.AnnAssign)
        and isinstance(stmt.target, ast.Name)
        and isinstance(stmt.value, ast.Lambda)
    ):
        return stmt.target.id, stmt.value
    return None


def _local_callable_names(body: list[ast.stmt]) -> set[str]:
    """Names bound to locally-defined callables in one lexical scope.

    Covers def/class statements (classes are callables too — constructing one
    executes uninspected ``__new__``/``__init__`` bodies) and permitted
    simple lambda bindings (``name = lambda``, ``name: T = lambda``).  Every
    other construction-scope lambda position rejects outright, so no other
    form can define a callable here.
    """
    names: set[str] = set()
    for stmt in _iter_scope_statements(body):
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(stmt.name)
        else:
            binding = _permitted_lambda_binding(stmt)
            if binding is not None:
                names.add(binding[0])
    return names


def _construction_expression_nodes(stmt: ast.stmt):
    """Expression nodes of one scope statement that evaluate at construction.

    Deferred function/lambda bodies are excluded (only their enclosing-scope
    positions — decorators, defaults, annotations — are entered), and nested
    class BODIES are excluded here because each class scope is scanned
    recursively with its own local-callable vocabulary; class decorators,
    bases, and keywords still evaluate in this scope and are entered.
    """
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        pending: list[ast.AST] = _enclosing_scope_children(stmt)
    else:
        pending = list(ast.iter_child_nodes(stmt))
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            pending.extend(_enclosing_scope_children(node))
        else:
            pending.extend(ast.iter_child_nodes(node))


def _reject_construction_time_local_callables(
    body: list[ast.stmt], *, where: str, inherited: frozenset[str] = frozenset()
) -> None:
    """Enforce the finite construction grammar over uninspected local callables.

    Locally-defined callables (functions, classes, bound lambdas) may exist,
    but their bodies are uninspected — so nothing may reference them while
    the certified module or a class in it is being CONSTRUCTED: no direct or
    aliased invocation, no local class construction, no decorator
    application, no argument laundering, no lambda invoked or applied as a
    decorator.  Any construction-time ``Name`` reference to such a callable
    fails closed (this deliberately subsumes every invocation form without
    building a call graph or dataflow).

    Anonymous lambdas are additionally position-restricted: a
    construction-scope lambda is permitted ONLY as the entire direct value
    of a simple named binding (``helper = lambda: ...``).  Every other
    position — walrus, call arguments, containers, subscripts, conditional
    expressions, decorators, defaults, annotations, class bases — rejects
    generically, so a laundered lambda can never cross the construction
    boundary as an executable object.  A stored lambda becomes an ordinary
    local callable name covered by the Load-reference rule.

    Deferred function/method bodies may freely use local helpers — that is
    runtime behavior, outside this import-time proof.  Class bodies execute
    during import, so each nested class scope is scanned recursively against
    its own locals plus every enclosing scope's (a fail-closed
    over-approximation of Python's actual class-scope name resolution).
    """
    known = frozenset(_local_callable_names(body)) | inherited
    for stmt in _iter_scope_statements(body):
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in stmt.decorator_list:
                if isinstance(decorator, ast.Lambda):
                    raise ImplementationArtifactError(
                        f"{where} applies a lambda decorator during construction; the "
                        "effective export is not statically provable"
                    )
        binding = _permitted_lambda_binding(stmt)
        permitted_lambda = binding[1] if binding is not None else None
        for node in _construction_expression_nodes(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Lambda):
                raise ImplementationArtifactError(
                    f"{where} invokes a lambda during construction; the effective "
                    "export is not statically provable"
                )
            if isinstance(node, ast.Lambda) and node is not permitted_lambda:
                raise ImplementationArtifactError(
                    f"{where} places an anonymous lambda in a construction-time "
                    "expression; only a lambda stored directly in a simple named "
                    "binding is certifiable"
                )
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in known
            ):
                # Only Load references can cause or enable execution; the
                # binding statement's own Store target is the definition.
                raise ImplementationArtifactError(
                    f"{where} references locally-defined callable {node.id!r} during "
                    "construction; its uninspected body must not execute at import "
                    "time"
                )
    for stmt in _iter_scope_statements(body):
        if isinstance(stmt, ast.ClassDef):
            _reject_construction_time_local_callables(
                stmt.body, where=where, inherited=known
            )


def prove_module_action_export(
    handler: ResolvedModuleHandlerSource, *, handler_method: str
) -> ModuleActionExportProof:
    """Prove one TRUE STANDALONE class explicitly exports ``handler_method``.

    ``EXPLICIT_HANDLER`` certification means a one-source class: the declared
    handler class must have ZERO bases — a class that declares any base
    (single, multiple, or mixin) is not eligible for this mode even when it
    explicitly defines the selected method, because its behavior is not
    provable from this source alone.  The proof is bounded and static: the
    source is parsed, never executed.  It fails closed on every construct
    that could make the visible definition not be the effective export —
    redefinition, conditional or decorated definitions, name rebinding,
    monkeypatching, ``__getattr__`` tricks, and any further reference to the
    handler class name.
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
    if class_def.bases:
        raise ImplementationArtifactError(
            f"handler class {handler_class!r} declares base classes and is not a "
            "standalone one-source class; EXPLICIT_HANDLER certification requires "
            "zero bases"
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
    _reject_construction_time_local_callables(tree.body, where="handler module scope")
    return ModuleActionExportProof(
        mode=HandlerCertificationMode.EXPLICIT_HANDLER,
        method_source=HandlerMethodSource.HANDLER,
        handler_class=handler_class,
        handler_method=handler_method,
        content_digest=handler.content_digest,
    )


def _prove_canonical_leaf_subclass(
    handler: ResolvedModuleHandlerSource, *, handler_method: str
) -> tuple[str, bool]:
    """Prove the leaf is the bounded canonical subclass.

    Exactly one top-level handler class with exactly one plain-name base,
    bound by exactly one ``from .base_handler import <Base>`` that is itself
    an unconditional DIRECT member of the module body appearing BEFORE the
    handler class definition — so the class construction provably sees the
    exact proven base binding.  No aliasing, no star imports, no second base,
    no mixins, no conditional/nested/late imports, no conditional or dynamic
    construction, no rebinding, and no ``__getattr__``/setattr tricks.

    A leaf override of the selected method is allowed under the same strict
    direct-method grammar as every explicit definition; the certification
    stays two-source (``CANONICAL_BASE_HANDLER``).  Returns the base class
    name and whether the leaf explicitly defines the selected method.
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

    class_index = next(
        index for index, stmt in enumerate(tree.body) if stmt is class_def
    )
    canonical_import: ast.ImportFrom | None = None
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
        if _statement_binds_name(stmt, handler_class):
            raise ImplementationArtifactError(
                f"handler class {handler_class!r} is rebound outside its definition"
            )
        if _statement_binds_name(stmt, base_name):
            if not isinstance(stmt, ast.ImportFrom):
                raise ImplementationArtifactError(
                    f"base class {base_name!r} is rebound outside its canonical import"
                )
            if not (
                stmt.level == 1
                and stmt.module == "base_handler"
                and len(stmt.names) == 1
                and stmt.names[0].name == base_name
                and stmt.names[0].asname is None
            ):
                raise ImplementationArtifactError(
                    f"base class {base_name!r} must be imported exactly as "
                    f"'from .base_handler import {base_name}'"
                )
            import_index = next(
                (index for index, body_stmt in enumerate(tree.body) if body_stmt is stmt),
                None,
            )
            if import_index is None:
                raise ImplementationArtifactError(
                    f"the canonical base import of {base_name!r} must be an "
                    "unconditional top-level module statement, not nested in "
                    "control flow"
                )
            if import_index > class_index:
                raise ImplementationArtifactError(
                    f"the canonical base import of {base_name!r} must appear before "
                    "the handler class definition"
                )
            if canonical_import is not None:
                raise ImplementationArtifactError(
                    f"base class {base_name!r} must be bound by exactly one "
                    f"'from .base_handler import {base_name}' import"
                )
            canonical_import = stmt
    if canonical_import is None:
        raise ImplementationArtifactError(
            f"base class {base_name!r} must be bound by exactly one "
            f"'from .base_handler import {base_name}' import"
        )
    _reject_dynamic_builtins(module_statements, where="handler module scope")

    class_statements = list(_iter_scope_statements(class_def.body))
    method_defs = [
        stmt
        for stmt in class_statements
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and stmt.name == handler_method
    ]
    leaf_method: ast.stmt | None = None
    if method_defs:
        if len(method_defs) != 1:
            raise ImplementationArtifactError(
                f"handler_method {handler_method!r} is bound more than once on class "
                f"{handler_class!r}; the export is not statically provable"
            )
        leaf_method = method_defs[0]
        if leaf_method not in class_def.body:
            raise ImplementationArtifactError(
                f"handler_method {handler_method!r} is defined conditionally; the "
                "export is not statically provable"
            )
        if isinstance(leaf_method, ast.FunctionDef | ast.AsyncFunctionDef) and (
            leaf_method.decorator_list
        ):
            raise ImplementationArtifactError(
                f"handler_method {handler_method!r} is decorated; the export is not "
                "statically provable"
            )
    for stmt in class_statements:
        if stmt is leaf_method:
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
    _reject_construction_time_local_callables(tree.body, where="handler module scope")
    return base_name, leaf_method is not None


def _prove_base_handler_closure(
    base_handler: ResolvedModuleBaseHandlerSource,
    *,
    base_class: str,
    handler_method: str,
    require_method: bool,
) -> None:
    """Prove the canonical base class closure, and the method when required.

    The base class must be the single top-level class of the regenerated
    base-handler source, with no bases of its own (no transitive
    inheritance) and no dynamic construction.  With ``require_method`` the
    selected method must be explicitly defined there under the strict
    direct-method grammar; without it (the leaf override case) the base
    remains meaning-bearing for construction, inherited helpers, and lookup —
    the full closure proof still runs, and a base definition of the selected
    method is allowed only in the same strict explicit form.
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
    if not method_defs and require_method:
        raise ImplementationArtifactError(
            f"action handler_method {handler_method!r} is not explicitly defined on "
            f"canonical base class {base_class!r} in the selected source"
        )
    method_def: ast.stmt | None = None
    if method_defs:
        if len(method_defs) != 1:
            raise ImplementationArtifactError(
                f"handler_method {handler_method!r} is bound more than once on base "
                f"class {base_class!r}; the export is not statically provable"
            )
        method_def = method_defs[0]
        if method_def not in class_def.body:
            raise ImplementationArtifactError(
                f"handler_method {handler_method!r} is defined conditionally; the "
                "export is not statically provable"
            )
        if isinstance(method_def, ast.FunctionDef | ast.AsyncFunctionDef) and (
            method_def.decorator_list
        ):
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
    _reject_construction_time_local_callables(
        tree.body, where="base-handler module scope"
    )


def _certify_module_action_export(
    handler: ResolvedModuleHandlerSource,
    *,
    handler_method: str,
    base_handler: ResolvedModuleBaseHandlerSource | None = None,
    split_authority: ResolvedWorkspaceHandlerSplitAuthority | None = None,
) -> ModuleActionExportProof:
    """Certify one action export through exactly one of the two bounded modes.

    INTERNAL: :func:`resolve_module_action_implementation` is the only public
    authority-producing API — it derives ``split_authority`` from exact
    verified pack-contract bytes before this proof runs, so no caller can
    substitute a preconstructed authority object for those bytes.  The public
    standalone helper surface is :func:`prove_module_action_export`, the
    zero-base ``EXPLICIT_HANDLER`` proof only.

    Without a base selection, certification is the strict standalone
    ``EXPLICIT_HANDLER`` proof (zero bases).  With the canonical base-handler
    source AND the content-resolved split authority — both are required
    together — the ``CANONICAL_BASE_HANDLER`` proof runs over exactly the two
    canonical sources.  A canonical split leaf that explicitly overrides the
    selected method stays a two-source ``CANONICAL_BASE_HANDLER``
    certification with ``method_source = handler``; both source digests and
    the pack-contract digest remain meaning-bearing.  No other source may
    participate.
    """
    if base_handler is None and split_authority is None:
        return prove_module_action_export(handler, handler_method=handler_method)
    if base_handler is None or split_authority is None:
        raise ImplementationArtifactError(
            "canonical split certification requires the base-handler source and "
            "the content-resolved workspace_handler_split authority together"
        )
    if base_handler.module_instance != handler.module_instance:
        raise ImplementationArtifactError(
            f"base handler of module {base_handler.module_instance!r} cannot certify "
            f"module {handler.module_instance!r}"
        )
    if split_authority.module_instance != handler.module_instance:
        raise ImplementationArtifactError(
            f"workspace_handler_split authority for module "
            f"{split_authority.module_instance!r} cannot certify module "
            f"{handler.module_instance!r}"
        )
    base_class, leaf_defines_method = _prove_canonical_leaf_subclass(
        handler, handler_method=handler_method
    )
    _prove_base_handler_closure(
        base_handler,
        base_class=base_class,
        handler_method=handler_method,
        require_method=not leaf_defines_method,
    )
    return ModuleActionExportProof(
        mode=HandlerCertificationMode.CANONICAL_BASE_HANDLER,
        method_source=(
            HandlerMethodSource.HANDLER
            if leaf_defines_method
            else HandlerMethodSource.BASE_HANDLER
        ),
        handler_class=handler.handler_class,
        handler_method=handler_method,
        content_digest=handler.content_digest,
        base_handler_class=base_class,
        base_content_digest=base_handler.content_digest,
        split_contract_content_digest=split_authority.contract_content_digest,
    )


async def resolve_module_action_implementation(
    module_selection: SelectedContractArtifact,
    handler_selection: SelectedAccountedArtifact,
    *,
    action_id: str,
    content_store: ArtifactContentStore,
    requesting_scope: ExecutionAccessScopeRef,
    layout_registry: AppLayoutRegistry | None = None,
    base_handler_selection: SelectedAccountedArtifact | None = None,
    pack_contract_selection: SelectedAccountedArtifact | None = None,
) -> ResolvedModuleActionImplementation:
    """Resolve and prove one certified module action implementation end to end.

    Without a base selection, certification is the strict standalone
    ``EXPLICIT_HANDLER`` proof.  A ``base_handler_selection`` participates
    only together with ``pack_contract_selection`` — the exact scope-bound
    selection of the module's owning capability-pack contract, from whose
    verified bytes the split authority is derived.  There is no way to
    substitute a caller-authored ownership claim for those bytes.
    """
    registry = layout_registry or default_app_layout_registry()
    module = await resolve_module_manifest_artifact(
        module_selection,
        content_store=content_store,
        requesting_scope=requesting_scope,
        layout_registry=registry,
    )
    handler = await resolve_module_handler_source(
        handler_selection,
        module=module,
        content_store=content_store,
        requesting_scope=requesting_scope,
        layout_registry=registry,
    )
    if (base_handler_selection is None) != (pack_contract_selection is None):
        raise ImplementationArtifactError(
            "canonical split certification requires the base-handler selection and "
            "the module's exact pack-contract selection together"
        )
    base_handler: ResolvedModuleBaseHandlerSource | None = None
    split_authority: ResolvedWorkspaceHandlerSplitAuthority | None = None
    if base_handler_selection is not None and pack_contract_selection is not None:
        split_authority = await resolve_workspace_handler_split_authority(
            pack_contract_selection,
            module=module,
            content_store=content_store,
            requesting_scope=requesting_scope,
        )
        base_handler = await _resolve_base_handler_with_authority(
            base_handler_selection,
            module=module,
            handler=handler,
            split_authority=split_authority,
            content_store=content_store,
            requesting_scope=requesting_scope,
            layout_registry=registry,
        )
    action = module.action(action_id)
    export_proof = _certify_module_action_export(
        handler,
        handler_method=action.handler_method,
        base_handler=base_handler,
        split_authority=split_authority,
    )
    return ResolvedModuleActionImplementation(
        module=module,
        handler=handler,
        action=action,
        request_contract=module.action_request_contract(action_id),
        export_proof=export_proof,
        base_handler=base_handler,
        split_authority=split_authority,
    )


__all__ = [
    "HandlerCertificationMode",
    "HandlerMethodSource",
    "ImplementationArtifactError",
    "ModuleActionExportProof",
    "ResolvedModuleActionImplementation",
    "ResolvedModuleBaseHandlerSource",
    "ResolvedModuleHandlerSource",
    "ResolvedModuleManifest",
    "ResolvedWorkflowImplementation",
    "ResolvedWorkflowOrchestrator",
    "ResolvedWorkflowStructuredOutputs",
    "ResolvedWorkspaceHandlerSplitAuthority",
    "SelectedAccountedArtifact",
    "SelectedContractArtifact",
    "pair_workflow_implementation_artifacts",
    "prove_module_action_export",
    "resolve_module_action_implementation",
    "resolve_module_base_handler_source",
    "resolve_module_handler_source",
    "resolve_module_manifest_artifact",
    "resolve_selected_structured_output_contract",
    "resolve_workflow_orchestrator_artifact",
    "resolve_workflow_structured_outputs_artifact",
    "resolve_workspace_handler_split_authority",
]
