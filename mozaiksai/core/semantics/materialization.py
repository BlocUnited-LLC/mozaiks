"""ADR 0007 Slice 4C deterministic offline materialization (offline-only).

This module implements the first honest deterministic materialization path:

    SemanticGraphV2 -> CompilationPlan -> accepted ImplementationBinding
    -> canonical ``app_ui_page_schema`` bytes -> composed candidate bundle

Exactly one renderer family is renderer-ready in this slice:
``app_ui_page_schema``. Its renderer is a pure function of the plan unit's
complete semantic source footprint (the page payload plus its linked section
payloads) and nothing else. It consumes no AppBuildPlan, agent context,
generated files, conversation history, runtime state, environment variables,
clocks, randomness, or filesystem enumeration — this module deliberately
imports none of those capabilities.

Authorities are unchanged: ``layout_registry`` remains the sole
family/materializer authority; the ``CompilationPlan`` owns dispositions,
source footprints, and physical output paths; the graph-v2
``ImplementationBinding`` pins the one accepted deterministic implementation
identity/version for the page-schema materializer. This module adds no
registry, no alternate family map, no alternate output ownership, and no
alternate artifact identity.

Canonical serialization contract (``mozaiks.page_bytes.v1``): the rendered
page document is the normative :class:`AppPageSchema` runtime model dumped in
declaration order with ``None`` fields omitted, serialized as YAML with
stable key order (no re-sorting), UTF-8 encoding, LF newlines, unbounded line
width (no content-dependent wrapping), and no timestamps, identifiers,
comments, or provenance. Same canonical input -> same path -> same bytes ->
same content digest, across repeated invocations and fresh processes.

Non-renderable dispositions stay truthful: ``preserve_unowned`` units consume
exact caller-supplied bytes pinned by the existing ``ChildContractRef``
identity (digest verified, never normalized or re-encoded);
``external_handoff`` and ``inapplicable`` units and every typed ``PlanGap``
are reported explicitly; nothing is silently omitted and no gap is turned
into guessed content. Instance-relative output scopes (``module_relative``,
``workflow_relative``) have no declared physical-root binding in this slice
and are reported explicitly as deferred rather than being invented here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

import yaml
from pydantic import BaseModel

from mozaiksai.core.runtime.app.layout_registry import MaterializerIdentifier
from mozaiksai.core.runtime.app.page_schema import (
    _CHILD_CONFIG_MODELS,
    _TOP_LEVEL_CONFIG_MODELS,
    AppPageChildSection,
    AppPageSchema,
    AppPageSection,
)
from mozaiksai.core.semantics.binding import (
    ImplementationBinding,
    RendererSelection,
    validate_implementation_binding_against_graph,
)
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    FamilyInstancePlan,
    LayoutRegistrySnapshot,
    PlanDisposition,
    RegenerationClosure,
    plan_regeneration_closure,
    snapshot_layout_registry,
)
from mozaiksai.core.semantics.graph import SemanticGraphV2
from mozaiksai.core.semantics.opaque_artifact import PreservedOpaqueArtifact
from mozaiksai.core.semantics.payloads import (
    PagePayload,
    SectionPayload,
    SemanticPayloadBase,
    parse_semantic_payload,
)
from mozaiksai.core.semantics.portable_path import detect_collisions

PAGE_SCHEMA_FAMILY: Literal["app_ui_page_schema"] = "app_ui_page_schema"
PAGE_BYTES_SCHEMA_VERSION: Literal["mozaiks.page_bytes.v1"] = "mozaiks.page_bytes.v1"

#: The single accepted deterministic implementation identity for the
#: ``page_schema_executor`` materializer in this slice. An ImplementationBinding
#: must pin exactly this identity/version or materialization fails closed.
PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID = "deterministic_page_schema_renderer"
PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION = "1"

#: Physical destination roots this slice can compose into one bundle file map.
#: Instance-relative scopes have no declared physical-root binding yet.
_COMPOSABLE_PATH_SCOPES = frozenset({"app_bundle_root", "workspace_root"})

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class MaterializationError(ValueError):
    """The materialization inputs violate the Slice 4C contract."""


# ---------------------------------------------------------------------------
# Recursive structural closure gate (the general #450 compiler gate)
# ---------------------------------------------------------------------------

#: The only fields allowed to carry a generic mapping annotation: the page
#: section config dispatchers, whose model validators exhaustively convert the
#: raw mapping into a closed primitive-specific model before it can become
#: authoritative content. Their closure is proven by probe, not trusted.
_DISPATCHER_FIELDS = frozenset(
    {(AppPageSection, "config"), (AppPageChildSection, "config")}
)

_CLOSED_SCALARS = (str, int, float, bool, bytes, type(None))


def _is_closed_annotation(annotation: Any) -> bool:
    if annotation in _CLOSED_SCALARS or annotation is Any:
        return annotation is not Any
    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return True
        if issubclass(annotation, BaseModel):
            return True  # walked separately
        return annotation in _CLOSED_SCALARS
    origin = get_origin(annotation)
    if origin is Literal:
        return True
    if origin in (Union, UnionType):
        return all(_is_closed_annotation(arg) for arg in get_args(annotation))
    if origin in (list, tuple, frozenset, set):
        return all(
            _is_closed_annotation(arg) for arg in get_args(annotation) if arg is not Ellipsis
        )
    if origin is dict:
        key_type, value_type = get_args(annotation)
        return key_type is str and _is_closed_annotation(value_type)
    return False


def _walk_model_closure(
    model: type[BaseModel], path: str, violations: list[str], visited: set[type[BaseModel]]
) -> None:
    if model in visited:
        return
    visited.add(model)
    if model.model_config.get("extra") != "forbid":
        violations.append(f"{path}: {model.__name__} does not forbid unknown fields")
    for name, model_field in model.model_fields.items():
        annotation = model_field.annotation
        if (model, name) in _DISPATCHER_FIELDS:
            continue  # closed by exhaustive dispatch, proven below
        if not _is_closed_annotation(annotation):
            violations.append(f"{path}.{name}: open annotation {annotation!r}")
        stack: list[Any] = [annotation]
        while stack:
            candidate = stack.pop()
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                _walk_model_closure(candidate, f"{path}.{name}", violations, visited)
            else:
                stack.extend(get_args(candidate))


_closure_gate_passed = False


def validate_page_renderer_input_closure() -> None:
    """Fail closed unless every reachable renderer input domain is closed.

    Walks every model reachable from the semantic page/section payloads and
    from the section-config primitive dispatch, then proves the dispatcher
    exception empirically: every registered primitive must reject an unknown
    config key, so the generic dispatcher annotation can never admit content
    that survives into semantic authority. No family-specific exceptions.
    """
    global _closure_gate_passed
    if _closure_gate_passed:
        return
    violations: list[str] = []
    visited: set[type[BaseModel]] = set()
    for entry_model in (PagePayload, SectionPayload, AppPageSchema, AppPageSection):
        _walk_model_closure(entry_model, entry_model.__name__, violations, visited)
    dispatch: dict[str, type[BaseModel]] = {}
    dispatch.update(_TOP_LEVEL_CONFIG_MODELS)
    dispatch.update(_CHILD_CONFIG_MODELS)
    for primitive, config_model in sorted(dispatch.items()):
        _walk_model_closure(config_model, f"config[{primitive}]", violations, visited)
    for primitive in sorted(_TOP_LEVEL_CONFIG_MODELS):
        try:
            AppPageSection(
                id="closure_probe",
                primitive=primitive,
                config={"mozaiks_closure_probe_key": 1},
            )
        except ValueError:
            continue
        violations.append(
            f"config[{primitive}]: dispatcher accepted an unknown config key"
        )
    if violations:
        raise MaterializationError(
            "renderer input closure violated: " + "; ".join(sorted(violations))
        )
    _closure_gate_passed = True


# ---------------------------------------------------------------------------
# Implementation resolution (binding is the only implementation authority)
# ---------------------------------------------------------------------------


def resolve_page_schema_renderer_selection(
    binding: ImplementationBinding,
    *,
    graph: SemanticGraphV2,
    layout_registry: Any,
) -> RendererSelection:
    """Resolve the accepted page-schema renderer through the binding.

    Fails closed when the binding carries no selection for the page family,
    claims a materializer the registry does not declare for it, targets a
    non-v2 graph, or pins any implementation identity/version other than the
    single accepted deterministic implementation of this slice. There is no
    fallback to any historical generator path.
    """
    try:
        validate_implementation_binding_against_graph(
            binding, graph, layout_registry=layout_registry
        )
    except ValueError as exc:
        raise MaterializationError(f"implementation binding rejected: {exc}") from exc
    matches = [
        selection
        for selection in binding.renderer_selections
        if PAGE_SCHEMA_FAMILY in selection.artifact_families
    ]
    if not matches:
        raise MaterializationError(
            f"binding carries no renderer selection for {PAGE_SCHEMA_FAMILY!r}"
        )
    selection = matches[0]
    if selection.materializer_id is not MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR:
        raise MaterializationError(
            f"renderer selection for {PAGE_SCHEMA_FAMILY!r} pins materializer "
            f"{selection.materializer_id.value!r}, not the page schema executor"
        )
    if (
        selection.implementation_id != PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID
        or selection.implementation_version != PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION
    ):
        raise MaterializationError(
            "binding pins unaccepted page renderer implementation "
            f"{selection.implementation_id!r}@{selection.implementation_version!r}; "
            f"accepted: {PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID!r}"
            f"@{PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION!r}"
        )
    return selection


# ---------------------------------------------------------------------------
# Canonical page bytes
# ---------------------------------------------------------------------------


def _canonical_yaml_bytes(document: Mapping[str, Any]) -> bytes:
    text = yaml.safe_dump(
        dict(document),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=2_000_000_000,
    )
    if not text.endswith("\n"):
        text += "\n"
    return text.replace("\r\n", "\n").encode("utf-8")


def render_app_ui_page_schema_unit(
    *,
    unit: FamilyInstancePlan,
    payload_by_node: Mapping[str, SemanticPayloadBase],
) -> bytes:
    """Render one page unit's canonical bytes from its footprint alone.

    The renderer reads only payloads pinned by the unit's declared source
    footprint; a page whose section list references any node outside that
    footprint fails closed instead of reaching for wider state.
    """
    validate_page_renderer_input_closure()
    if unit.family_kind != PAGE_SCHEMA_FAMILY:
        raise MaterializationError(
            f"unit {unit.unit_id!r} family {unit.family_kind!r} is not renderer-ready "
            f"in this slice; only {PAGE_SCHEMA_FAMILY!r} renders"
        )
    if unit.disposition is not PlanDisposition.RENDER:
        raise MaterializationError(
            f"unit {unit.unit_id!r} disposition {unit.disposition.value!r} is not render"
        )
    footprint = {source.node_id for source in unit.sources}
    pages = [
        payload_by_node[node_id]
        for node_id in sorted(footprint)
        if isinstance(payload_by_node.get(node_id), PagePayload)
    ]
    if len(pages) != 1:
        raise MaterializationError(
            f"unit {unit.unit_id!r} footprint must pin exactly one page payload, "
            f"found {len(pages)}"
        )
    page = pages[0]
    for source in unit.sources:
        pinned = payload_by_node.get(source.node_id)
        if pinned is None or pinned.payload_digest != source.payload_digest:
            raise MaterializationError(
                f"unit {unit.unit_id!r} source {source.node_id!r} is missing or does "
                "not match its pinned payload digest"
            )
    if unit.placeholder_values != (("page_id", page.page_id),):
        raise MaterializationError(
            f"unit {unit.unit_id!r} instance identity {unit.placeholder_values!r} does "
            f"not match page canonical id {page.page_id!r}"
        )
    required = {
        "route": page.route,
        "title": page.title,
        "page_type": page.page_type,
        "layout": page.layout,
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing or not page.sections:
        raise MaterializationError(
            f"page {page.node_id!r} is not renderer-input complete; missing "
            f"{missing or ['sections']}"
        )
    declaratives = []
    for entry in page.sections:
        if entry.section_node_id not in footprint:
            raise MaterializationError(
                f"page {page.node_id!r} references section {entry.section_node_id!r} "
                "outside the unit's declared source footprint"
            )
        section = payload_by_node[entry.section_node_id]
        if not isinstance(section, SectionPayload) or section.declarative is None:
            raise MaterializationError(
                f"section {entry.section_node_id!r} carries no normative declarative; "
                "the page is not renderer-input complete"
            )
        declaratives.append(section.declarative)
    schema = AppPageSchema(
        schema_version="mozaiks.app_page.v1",
        name=page.page_id,
        route=page.route,
        title=page.title,
        page_type=page.page_type,
        layout=page.layout,
        shell_mode=page.shell_mode,
        roles=None if page.roles is None else list(page.roles),
        navigation=page.navigation,
        meta=page.meta,
        sections=list(declaratives),
    )
    rendered = _canonical_yaml_bytes(schema.model_dump(mode="json", exclude_none=True))
    reparsed = AppPageSchema.model_validate(yaml.safe_load(rendered.decode("utf-8")))
    if reparsed != schema:
        raise MaterializationError(
            f"canonical page bytes for {page.page_id!r} failed round-trip verification"
        )
    return rendered


# ---------------------------------------------------------------------------
# Bundle materialization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterializedOutput:
    """One physical output produced under plan authority."""

    unit_id: str
    path_scope: str
    path: str
    content: bytes
    origin: Literal["rendered", "preserved", "reused"]
    content_digest: str


@dataclass(frozen=True)
class MaterializedBundle:
    """Result of materializing one CompilationPlan. Not a wire contract."""

    plan_digest: str
    outputs: tuple[MaterializedOutput, ...]
    external_handoff_units: tuple[str, ...]
    inapplicable_units: tuple[str, ...]
    unsupplied_preserved_units: tuple[str, ...]
    instance_scope_deferred_units: tuple[str, ...]
    gap_count: int
    closure: RegenerationClosure | None = field(default=None)

    def files(self) -> dict[str, bytes]:
        return {output.path: output.content for output in self.outputs}


def _cold_validate(plan: CompilationPlan, graph: SemanticGraphV2, payloads: Iterable[SemanticPayloadBase]):
    try:
        verified_plan = CompilationPlan.model_validate(plan.model_dump(mode="json"))
        verified_graph = SemanticGraphV2.model_validate(graph.model_dump(mode="json"))
        verified_payloads = [
            parse_semantic_payload(payload.model_dump(mode="json")) for payload in payloads
        ]
    except ValueError as exc:
        raise MaterializationError(f"materialization inputs failed cold validation: {exc}") from exc
    if verified_plan.graph_digest != verified_graph.graph_digest:
        raise MaterializationError("plan does not pin the supplied graph identity")
    return verified_plan, verified_graph, {p.node_id: p for p in verified_payloads}


def _assert_registry_identity(plan: CompilationPlan, layout_registry: Any) -> None:
    """The plan must pin the identity of the sole registry actually supplied.

    A pre-built snapshot is cold-revalidated, so a forged or tampered
    snapshot identity fails here rather than being trusted.
    """
    try:
        snapshot = (
            LayoutRegistrySnapshot.model_validate(layout_registry.model_dump(mode="json"))
            if isinstance(layout_registry, LayoutRegistrySnapshot)
            else snapshot_layout_registry(layout_registry)
        )
    except ValueError as exc:
        raise MaterializationError(f"layout registry snapshot rejected: {exc}") from exc
    if snapshot.snapshot_digest != plan.registry_digest:
        raise MaterializationError(
            "layout registry snapshot does not match the plan's pinned registry identity"
        )


def _match_preserved(
    plan: CompilationPlan,
    artifacts: Iterable[PreservedOpaqueArtifact],
) -> dict[str, PreservedOpaqueArtifact]:
    by_unit: dict[str, PreservedOpaqueArtifact] = {}
    for artifact in artifacts:
        ref = artifact.contract_ref
        matches = [
            unit
            for unit in plan.units
            if unit.disposition is PlanDisposition.PRESERVE_UNOWNED
            and unit.family_kind == ref.artifact_family
            and any(
                output.path == ref.canonical_relative_path
                and output.path_scope in _COMPOSABLE_PATH_SCOPES
                for output in unit.outputs
            )
        ]
        if len(matches) != 1:
            raise MaterializationError(
                f"preserved artifact {ref.artifact_family!r}:{ref.canonical_relative_path!r} "
                f"matches {len(matches)} plan units; exactly one required"
            )
        unit = matches[0]
        if unit.unit_id in by_unit:
            raise MaterializationError(
                f"unit {unit.unit_id!r} received more than one preserved artifact"
            )
        by_unit[unit.unit_id] = artifact
    return by_unit


def _materialize_unit(
    unit: FamilyInstancePlan,
    *,
    payload_by_node: Mapping[str, SemanticPayloadBase],
    preserved_by_unit: Mapping[str, PreservedOpaqueArtifact],
    bundle_outputs: list[MaterializedOutput],
    external: list[str],
    inapplicable: list[str],
    unsupplied: list[str],
    deferred: list[str],
) -> None:
    composable = [o for o in unit.outputs if o.path_scope in _COMPOSABLE_PATH_SCOPES]
    non_composable = [o for o in unit.outputs if o.path_scope not in _COMPOSABLE_PATH_SCOPES]
    if non_composable:
        deferred.append(unit.unit_id)
    if unit.disposition is PlanDisposition.RENDER:
        if not composable:
            return
        if len(composable) != 1:
            raise MaterializationError(
                f"render unit {unit.unit_id!r} must own exactly one composable output"
            )
        content = render_app_ui_page_schema_unit(unit=unit, payload_by_node=payload_by_node)
        target = composable[0]
        bundle_outputs.append(
            MaterializedOutput(
                unit_id=unit.unit_id,
                path_scope=target.path_scope,
                path=target.path,
                content=content,
                origin="rendered",
                content_digest=hashlib.sha256(content).hexdigest(),
            )
        )
    elif unit.disposition is PlanDisposition.PRESERVE_UNOWNED:
        artifact = preserved_by_unit.get(unit.unit_id)
        if artifact is None:
            if composable:
                unsupplied.append(unit.unit_id)
            return
        target = next(
            output
            for output in composable
            if output.path == artifact.contract_ref.canonical_relative_path
        )
        bundle_outputs.append(
            MaterializedOutput(
                unit_id=unit.unit_id,
                path_scope=target.path_scope,
                path=target.path,
                content=artifact.content,
                origin="preserved",
                content_digest=artifact.contract_ref.content_digest,
            )
        )
    elif unit.disposition is PlanDisposition.EXTERNAL_HANDOFF:
        external.append(unit.unit_id)
    elif unit.disposition is PlanDisposition.INAPPLICABLE:
        inapplicable.append(unit.unit_id)
    else:
        raise MaterializationError(
            f"unit {unit.unit_id!r} disposition {unit.disposition.value!r} has no "
            "materialization path in this slice"
        )


def materialize_plan(
    *,
    plan: CompilationPlan,
    graph: SemanticGraphV2,
    payloads: Iterable[SemanticPayloadBase],
    binding: ImplementationBinding,
    layout_registry: Any,
    preserved_artifacts: Iterable[PreservedOpaqueArtifact] = (),
) -> MaterializedBundle:
    """Materialize every plan-authorized output for one CompilationPlan.

    Renders exactly the renderer-ready page units through the bound accepted
    implementation, places exact preserved bytes for supplied
    ``preserve_unowned`` units, and reports every other unit's disposition
    explicitly. The registry snapshot identity must match the plan's pinned
    registry digest — a plan derived from a different registry fails closed.
    """
    verified_plan, verified_graph, payload_by_node = _cold_validate(plan, graph, payloads)
    _assert_registry_identity(verified_plan, layout_registry)
    resolve_page_schema_renderer_selection(
        binding, graph=verified_graph, layout_registry=layout_registry
    )
    preserved_by_unit = _match_preserved(verified_plan, preserved_artifacts)

    outputs: list[MaterializedOutput] = []
    external: list[str] = []
    inapplicable: list[str] = []
    unsupplied: list[str] = []
    deferred: list[str] = []
    for unit in verified_plan.units:
        _materialize_unit(
            unit,
            payload_by_node=payload_by_node,
            preserved_by_unit=preserved_by_unit,
            bundle_outputs=outputs,
            external=external,
            inapplicable=inapplicable,
            unsupplied=unsupplied,
            deferred=deferred,
        )
    _assert_output_ownership(verified_plan, outputs)
    return MaterializedBundle(
        plan_digest=verified_plan.plan_digest,
        outputs=tuple(sorted(outputs, key=lambda o: o.path)),
        external_handoff_units=tuple(sorted(external)),
        inapplicable_units=tuple(sorted(inapplicable)),
        unsupplied_preserved_units=tuple(sorted(unsupplied)),
        instance_scope_deferred_units=tuple(sorted(set(deferred))),
        gap_count=len(verified_plan.gaps),
    )


def _assert_output_ownership(plan: CompilationPlan, outputs: list[MaterializedOutput]) -> None:
    """Every produced path must be the plan-assigned path of its own unit."""
    assigned: dict[tuple[str, str], str] = {}
    for unit in plan.units:
        for output in unit.outputs:
            assigned[(output.path_scope, output.path)] = unit.unit_id
    seen: set[str] = set()
    for produced in outputs:
        owner = assigned.get((produced.path_scope, produced.path))
        if owner != produced.unit_id:
            raise MaterializationError(
                f"output {produced.path!r} is not assigned to unit {produced.unit_id!r} "
                "by the compilation plan"
            )
        if produced.path in seen:
            raise MaterializationError(f"duplicate materialized output {produced.path!r}")
        seen.add(produced.path)
    detect_collisions(sorted(seen))


def rematerialize_plan(
    *,
    base_bundle: MaterializedBundle,
    base_plan: CompilationPlan,
    successor_plan: CompilationPlan,
    graph: SemanticGraphV2,
    payloads: Iterable[SemanticPayloadBase],
    binding: ImplementationBinding,
    layout_registry: Any,
    preserved_artifacts: Iterable[PreservedOpaqueArtifact] = (),
) -> MaterializedBundle:
    """Selectively rematerialize a successor plan against a base bundle.

    Affected and added units are produced fresh; reusable units are copied
    byte-for-byte from the base bundle (preserved reuse re-verifies the
    pinned content digest); removed units' outputs are absent. The closure is
    computed by the 4B authority and attached to the result for inspection.
    """
    if base_bundle.plan_digest != base_plan.plan_digest:
        raise MaterializationError("base bundle does not correspond to the base plan")
    closure = plan_regeneration_closure(base_plan, successor_plan)
    reusable_ids = set(closure.reusable)

    verified_plan, verified_graph, payload_by_node = _cold_validate(
        successor_plan, graph, payloads
    )
    _assert_registry_identity(verified_plan, layout_registry)
    resolve_page_schema_renderer_selection(
        binding, graph=verified_graph, layout_registry=layout_registry
    )
    preserved_by_unit = _match_preserved(verified_plan, preserved_artifacts)
    base_by_unit: dict[str, list[MaterializedOutput]] = {}
    for output in base_bundle.outputs:
        base_by_unit.setdefault(output.unit_id, []).append(output)

    outputs: list[MaterializedOutput] = []
    external: list[str] = []
    inapplicable: list[str] = []
    unsupplied: list[str] = []
    deferred: list[str] = []
    for unit in verified_plan.units:
        if unit.unit_id in reusable_ids:
            reused = base_by_unit.get(unit.unit_id, [])
            expected_paths = {
                (o.path_scope, o.path)
                for o in unit.outputs
                if o.path_scope in _COMPOSABLE_PATH_SCOPES
            }
            if unit.disposition in (PlanDisposition.RENDER, PlanDisposition.PRESERVE_UNOWNED):
                produced_paths = {(o.path_scope, o.path) for o in reused}
                if expected_paths and produced_paths != expected_paths:
                    if unit.disposition is PlanDisposition.PRESERVE_UNOWNED and not reused:
                        unsupplied.append(unit.unit_id)
                        continue
                    raise MaterializationError(
                        f"reusable unit {unit.unit_id!r} has no matching base outputs "
                        "to reuse byte-for-byte"
                    )
                for prior in reused:
                    if hashlib.sha256(prior.content).hexdigest() != prior.content_digest:
                        raise MaterializationError(
                            f"base output {prior.path!r} failed digest re-verification"
                        )
                    outputs.append(
                        MaterializedOutput(
                            unit_id=prior.unit_id,
                            path_scope=prior.path_scope,
                            path=prior.path,
                            content=prior.content,
                            origin="reused",
                            content_digest=prior.content_digest,
                        )
                    )
            elif unit.disposition is PlanDisposition.EXTERNAL_HANDOFF:
                external.append(unit.unit_id)
            elif unit.disposition is PlanDisposition.INAPPLICABLE:
                inapplicable.append(unit.unit_id)
            if any(o.path_scope not in _COMPOSABLE_PATH_SCOPES for o in unit.outputs):
                deferred.append(unit.unit_id)
            continue
        _materialize_unit(
            unit,
            payload_by_node=payload_by_node,
            preserved_by_unit=preserved_by_unit,
            bundle_outputs=outputs,
            external=external,
            inapplicable=inapplicable,
            unsupplied=unsupplied,
            deferred=deferred,
        )
    _assert_output_ownership(verified_plan, outputs)
    return MaterializedBundle(
        plan_digest=verified_plan.plan_digest,
        outputs=tuple(sorted(outputs, key=lambda o: o.path)),
        external_handoff_units=tuple(sorted(external)),
        inapplicable_units=tuple(sorted(inapplicable)),
        unsupplied_preserved_units=tuple(sorted(unsupplied)),
        instance_scope_deferred_units=tuple(sorted(set(deferred))),
        gap_count=len(verified_plan.gaps),
        closure=closure,
    )


def compose_bundle(
    bundle: MaterializedBundle,
    authored_files: Mapping[str, str | bytes],
) -> dict[str, bytes]:
    """Compose plan-owned outputs with authored files for gapped families.

    Authored files represent families whose legitimate disposition is a typed
    gap or authored content in this slice. They may never overlap — or
    collide under case-fold/prefix rules — with any plan-owned output path.
    """
    plan_owned = bundle.files()
    authored: dict[str, bytes] = {
        path: (content.encode("utf-8") if isinstance(content, str) else bytes(content))
        for path, content in authored_files.items()
    }
    overlap = sorted(set(plan_owned) & set(authored))
    if overlap:
        raise MaterializationError(
            f"authored files overlap plan-owned outputs: {overlap}"
        )
    detect_collisions(sorted(set(plan_owned) | set(authored)))
    combined = dict(authored)
    combined.update(plan_owned)
    return combined


__all__ = [
    "PAGE_BYTES_SCHEMA_VERSION",
    "PAGE_SCHEMA_FAMILY",
    "PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID",
    "PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION",
    "MaterializationError",
    "MaterializedBundle",
    "MaterializedOutput",
    "compose_bundle",
    "materialize_plan",
    "rematerialize_plan",
    "render_app_ui_page_schema_unit",
    "resolve_page_schema_renderer_selection",
    "validate_page_renderer_input_closure",
]
