"""ADR 0007 Slice 2E proof gate: typed payloads + Merkle-rooted graph v2.

Adversarial matrix: kind closure, node/scope/type substitution, version
drift, digest tampering, duplicate identity, Merkle-root chain, payload
reuse, v1-shape rejection, partial closure, deterministic bytes,
immutability, binding non-authority, and no capability advertisement.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.archive import (
    ArchiveEntry,
    archive_digest,
    build_deterministic_archive,
)
from mozaiksai.core.semantics.binding import ImplementationBinding
from mozaiksai.core.semantics.capabilities import advertised_semantic_compiler_capabilities
from mozaiksai.core.semantics.graph import (
    SEMANTIC_GRAPH_V2_SCHEMA_VERSION,
    SemanticEdge,
    SemanticEdgeKind,
    SemanticGraphV2,
    SemanticNodeKind,
    SemanticNodeV2,
    build_semantic_graph,
    build_semantic_graph_v2,
)
from mozaiksai.core.semantics.payloads import (
    PAYLOAD_MODEL_BY_KIND,
    ActionPayload,
    BillingPeriod,
    CapabilityPayload,
    DataAliasPayload,
    DataCollectionPayload,
    DeploymentTargetKind,
    DeploymentTargetPayload,
    EventPayload,
    FieldType,
    IndexSpec,
    LimitPayload,
    MeterPayload,
    ModulePayload,
    NotificationChannel,
    NotificationPayload,
    PagePayload,
    PageSectionEntry,
    PermissionPayload,
    PlanPayload,
    PriceSpec,
    ProductPayload,
    ReactionPayload,
    SectionContentEntry,
    SectionEntryKind,
    SectionPayload,
    SemanticPayloadBase,
    SemanticPayloadError,
    StubDeclarationPayload,
    SurfacePayload,
    TriggerKind,
    TriggerPayload,
    TypedFieldSpec,
    WorkflowPayload,
    WorkflowStartupMode,
    build_semantic_payload,
    parse_semantic_payload,
    semantic_payload_ref,
    validate_semantic_graph_v2_payload_closure,
)
from mozaiksai.core.semantics.refs import (
    ExecutionAccessScopeRef,
    SemanticPayloadRef,
)
from mozaiksai.core.semantics.resolver import (
    ReferenceResolutionError,
    SemanticReferenceResolver,
)
from mozaiksai.core.stub_kinds import StubKind

ROOT = Path(__file__).resolve().parents[1]

_PRODUCTION_ROOTS = frozenset({"mozaiksai", "factory_app"})
_SEMANTICS_OWNER_FILES = frozenset(
    {
        Path("mozaiksai/core/semantics/payloads.py"),
        Path("mozaiksai/core/semantics/graph.py"),
        Path("mozaiksai/core/semantics/refs.py"),
        Path("mozaiksai/core/semantics/resolver.py"),
        # Slice 3E: the offline projection emits graph v2 + typed payloads.
        # It stays outside production imports itself (proven by the Slice 3
        # hygiene test scanning for offline_projection references).
        Path("mozaiksai/core/semantics/offline_projection.py"),
    }
)
_FORBIDDEN_PRODUCTION_MODULES = frozenset({"mozaiksai.core.semantics.payloads"})
_FORBIDDEN_PRODUCTION_SYMBOLS = frozenset(
    {
        "SemanticGraphV2",
        "SemanticNodeV2",
        "SemanticPayloadRef",
        "parse_semantic_payload",
        "register_semantic_payload",
        "resolve_semantic_payload",
        "semantic_payload_ref",
        "validate_semantic_graph_v2_payload_closure",
    }
)
_SEMANTICS_SYMBOL_MODULES = frozenset(
    {
        "mozaiksai.core.semantics",
        "mozaiksai.core.semantics.graph",
        "mozaiksai.core.semantics.refs",
        "mozaiksai.core.semantics.resolver",
    }
)
_DECLARATIVE_SUFFIXES = (
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".json.j2",
    ".toml.j2",
    ".yaml.j2",
    ".yml.j2",
)


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string(node.left)
        right = _constant_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _contains_forbidden_production_reference(value: str) -> bool:
    markers = _FORBIDDEN_PRODUCTION_MODULES | _FORBIDDEN_PRODUCTION_SYMBOLS
    return any(marker in value for marker in markers)


def _python_source_has_forbidden_production_reference(source: str, *, filename: str) -> bool:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        # Tracked generator templates can contain placeholders that become
        # Python only after rendering. They still receive a conservative raw
        # reference scan instead of disappearing from the proof.
        return _contains_forbidden_production_reference(source)

    for node in ast.walk(tree):
        resolved_string = _constant_string(node)
        if resolved_string is not None and _contains_forbidden_production_reference(
            resolved_string
        ):
            return True
        if isinstance(node, ast.Import):
            if any(alias.name in _FORBIDDEN_PRODUCTION_MODULES for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module in _FORBIDDEN_PRODUCTION_MODULES:
                return True
            if any(
                alias.name in _FORBIDDEN_PRODUCTION_SYMBOLS
                or (alias.name == "*" and node.module in _SEMANTICS_SYMBOL_MODULES)
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Call) and node.args:
            target = _constant_string(node.args[0])
            if target is not None and _contains_forbidden_production_reference(target):
                return True
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_PRODUCTION_SYMBOLS:
            return True
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_PRODUCTION_SYMBOLS:
            return True
    return False


def _tracked_production_paths(*, suffixes: tuple[str, ...]) -> tuple[Path, ...]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "mozaiksai", "factory_app"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    return tuple(
        Path(relative)
        for relative in tracked
        if relative
        and Path(relative).parts[0] in _PRODUCTION_ROOTS
        and relative.endswith(suffixes)
    )

_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant1", workspace_id="ws1")
_OTHER_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant2")

# Golden Merkle-root vector: pinned digests for the full-corpus v2 graph and
# its archived fixture.  Independent of host, process, and input order.
_GOLDEN_GRAPH_DIGEST = "a4eaa86709134dc5677a0b67b99e00a02b0cedfa4b45d39e37ade35f4f8b85f4"
_GOLDEN_ARCHIVE_DIGEST = "sha256:53b569a6c62e9ae4c1ce0bed9b86cd2eed5a18147f5bdb531cd763c7bafff20b"


def _corpus_payloads(*, scope: ExecutionAccessScopeRef = _SCOPE, home_title: str = "Home"):
    """One payload of every kind, so kind closure is exercised end to end."""
    field = TypedFieldSpec(name="name", field_type=FieldType.STRING, required=True)
    return {
        SemanticNodeKind.SURFACE: build_semantic_payload(
            SurfacePayload,
            node_id="mozaiks.surface.web",
            payload_version=1,
            scope=scope,
            description="Primary web surface",
        ),
        SemanticNodeKind.PAGE: build_semantic_payload(
            PagePayload,
            node_id="mozaiks.page.home",
            payload_version=1,
            scope=scope,
            title=home_title,
            intent="Landing page",
            sections=(
                PageSectionEntry(position=1, section_node_id="mozaiks.section.pricing"),
                PageSectionEntry(position=0, section_node_id="mozaiks.section.hero"),
            ),
        ),
        SemanticNodeKind.SECTION: build_semantic_payload(
            SectionPayload,
            node_id="mozaiks.section.hero",
            payload_version=1,
            scope=scope,
            title="Hero",
            intent="Welcome banner",
            entries=(
                SectionContentEntry(
                    position=0, entry_kind=SectionEntryKind.TEXT, text="Welcome"
                ),
                SectionContentEntry(
                    position=1,
                    entry_kind=SectionEntryKind.API_BINDING,
                    api_method="GET",
                    api_path="/api/status",
                ),
            ),
        ),
        SemanticNodeKind.MODULE: build_semantic_payload(
            ModulePayload,
            node_id="mozaiks.module.reports",
            payload_version=1,
            scope=scope,
            description="Reporting module",
        ),
        SemanticNodeKind.ACTION: build_semantic_payload(
            ActionPayload,
            node_id="mozaiks.action.create_report",
            payload_version=1,
            scope=scope,
            description="Create one report",
            request_fields=(field,),
            response_fields=(
                TypedFieldSpec(name="report_id", field_type=FieldType.REFERENCE, required=True),
            ),
            emits=("reports.report_created",),
            entitlement_gate="reports.premium",
        ),
        SemanticNodeKind.CAPABILITY: build_semantic_payload(
            CapabilityPayload,
            node_id="mozaiks.capability.reporting",
            payload_version=1,
            scope=scope,
            description="Reporting capability",
        ),
        SemanticNodeKind.PERMISSION: build_semantic_payload(
            PermissionPayload,
            node_id="mozaiks.permission.report_admin",
            payload_version=1,
            scope=scope,
            description="Administer reports",
        ),
        SemanticNodeKind.EVENT: build_semantic_payload(
            EventPayload,
            node_id="mozaiks.event.report_created",
            payload_version=1,
            scope=scope,
            description="A report was created",
            payload_fields=(field,),
        ),
        SemanticNodeKind.REACTION: build_semantic_payload(
            ReactionPayload,
            node_id="mozaiks.reaction.notify_owner",
            payload_version=1,
            scope=scope,
            description="Notify the owner",
            consumed_event="reports.report_created",
        ),
        SemanticNodeKind.NOTIFICATION: build_semantic_payload(
            NotificationPayload,
            node_id="mozaiks.notification.report_ready",
            payload_version=1,
            scope=scope,
            template_text="Your report is ready",
            channel=NotificationChannel.IN_APP,
        ),
        SemanticNodeKind.DATA_COLLECTION: build_semantic_payload(
            DataCollectionPayload,
            node_id="mozaiks.data.reports",
            payload_version=1,
            scope=scope,
            description="Report documents",
            fields=(
                field,
                TypedFieldSpec(name="created_at", field_type=FieldType.DATETIME, required=True),
            ),
            indexes=(IndexSpec(name="by_name", field_names=("name",), unique=False),),
        ),
        SemanticNodeKind.DATA_ALIAS: build_semantic_payload(
            DataAliasPayload,
            node_id="mozaiks.alias.reports",
            payload_version=1,
            scope=scope,
            alias="reports",
            collection="report_documents",
            owner_node_id="mozaiks.module.reports",
        ),
        SemanticNodeKind.WORKFLOW: build_semantic_payload(
            WorkflowPayload,
            node_id="mozaiks.workflow.digest",
            payload_version=1,
            scope=scope,
            description="Weekly digest workflow",
            startup_mode=WorkflowStartupMode.EVENT_DRIVEN,
        ),
        SemanticNodeKind.TRIGGER: build_semantic_payload(
            TriggerPayload,
            node_id="mozaiks.trigger.on_report",
            payload_version=1,
            scope=scope,
            description="Start on report creation",
            trigger_kind=TriggerKind.EVENT,
            event_id="reports.report_created",
        ),
        SemanticNodeKind.PLAN: build_semantic_payload(
            PlanPayload,
            node_id="mozaiks.plan.pro",
            payload_version=1,
            scope=scope,
            title="Pro",
            prices=(
                PriceSpec(amount_minor_units=1900, currency="USD", period=BillingPeriod.MONTHLY),
            ),
            granted_capabilities=("reports.premium",),
        ),
        SemanticNodeKind.PRODUCT: build_semantic_payload(
            ProductPayload,
            node_id="mozaiks.product.addon",
            payload_version=1,
            scope=scope,
            title="Add-on",
            description="Extra seats",
            prices=(
                PriceSpec(amount_minor_units=500, currency="USD", period=BillingPeriod.ONE_TIME),
            ),
        ),
        SemanticNodeKind.METER: build_semantic_payload(
            MeterPayload,
            node_id="mozaiks.meter.report_runs",
            payload_version=1,
            scope=scope,
            description="Report executions",
            unit="runs",
        ),
        SemanticNodeKind.LIMIT: build_semantic_payload(
            LimitPayload,
            node_id="mozaiks.limit.report_runs",
            payload_version=1,
            scope=scope,
            description="Monthly run cap",
            limit_value=100,
            period=BillingPeriod.MONTHLY,
        ),
        SemanticNodeKind.DEPLOYMENT_TARGET: build_semantic_payload(
            DeploymentTargetPayload,
            node_id="mozaiks.deploy.container",
            payload_version=1,
            scope=scope,
            target_kind=DeploymentTargetKind.CONTAINER,
            profile_id="generic_container",
            output_hints=("Dockerfile", "docker-compose.yml"),
        ),
        SemanticNodeKind.STUB_DECLARATION: build_semantic_payload(
            StubDeclarationPayload,
            node_id="mozaiks.stub.report_hook",
            payload_version=1,
            scope=scope,
            stub_kind="python_backend",
            path="modules/reports/backend/hooks.py",
            entrypoint="on_report_created",
        ),
    }


def _pricing_section(scope: ExecutionAccessScopeRef = _SCOPE) -> SectionPayload:
    return build_semantic_payload(
        SectionPayload,
        node_id="mozaiks.section.pricing",
        payload_version=1,
        scope=scope,
        title="Pricing",
        intent="Plans overview",
    )


def _corpus_graph(
    *, scope: ExecutionAccessScopeRef = _SCOPE, home_title: str = "Home"
) -> tuple[SemanticGraphV2, list[SemanticPayloadBase]]:
    payloads = list(_corpus_payloads(scope=scope, home_title=home_title).values())
    payloads.append(_pricing_section(scope))
    nodes = [
        SemanticNodeV2(
            node_id=payload.node_id,
            kind=payload.payload_kind,
            payload_ref=semantic_payload_ref(payload),
        )
        for payload in payloads
    ]
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.RENDERS,
            source_node_id="mozaiks.page.home",
            target_node_id="mozaiks.section.hero",
        ),
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id="mozaiks.action.create_report",
            target_node_id="mozaiks.event.report_created",
        ),
    ]
    graph = build_semantic_graph_v2(
        graph_id="corpus-app",
        version=1,
        scope=scope,
        nodes=nodes,
        edges=edges,
    )
    return graph, payloads


# ---------------------------------------------------------------------------
# Kind closure
# ---------------------------------------------------------------------------


def test_every_node_kind_has_exactly_one_payload_variant() -> None:
    assert set(PAYLOAD_MODEL_BY_KIND) == set(SemanticNodeKind)
    models = list(PAYLOAD_MODEL_BY_KIND.values())
    assert len(models) == len(set(models)), "one variant must not serve two kinds"
    for kind, model in PAYLOAD_MODEL_BY_KIND.items():
        assert model.model_fields["payload_kind"].default is kind


@pytest.mark.parametrize("kind", list(SemanticNodeKind))
def test_each_kind_round_trips_through_the_discriminated_union(
    kind: SemanticNodeKind,
) -> None:
    payload = _corpus_payloads()[kind]
    parsed = parse_semantic_payload(json.loads(json.dumps(payload.model_dump(mode="json"))))
    assert type(parsed) is PAYLOAD_MODEL_BY_KIND[kind]
    assert parsed == payload


def test_union_rejects_kind_content_mismatch() -> None:
    document = _corpus_payloads()[SemanticNodeKind.PAGE].model_dump(mode="json")
    document["payload_kind"] = "module"  # page content under module discriminant
    with pytest.raises(ValidationError):
        parse_semantic_payload(document)


def test_stub_kind_has_one_shared_leaf_authority() -> None:
    from mozaiksai.core.runtime.app.layout_registry import StubKind as LayoutStubKind

    assert LayoutStubKind is StubKind


# ---------------------------------------------------------------------------
# Substitution, drift, tampering, duplicates
# ---------------------------------------------------------------------------


def _registered_resolver() -> tuple[SemanticReferenceResolver, SemanticGraphV2, list]:
    graph, payloads = _corpus_graph()
    resolver = SemanticReferenceResolver()
    for payload in payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(graph)
    return resolver, graph, payloads


def test_node_substitution_fails_closed() -> None:
    resolver, _graph, payloads = _registered_resolver()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")
    wrong_node = SemanticPayloadRef(
        node_id="mozaiks.page.other",
        payload_kind="page",
        payload_version=page.payload_version,
        content_digest=page.payload_digest,
        scope=page.scope,
    )
    with pytest.raises(ReferenceResolutionError, match="no semantic payload"):
        resolver.resolve_semantic_payload(wrong_node, requesting_scope=_SCOPE)
    with pytest.raises(ValidationError, match="pins node"):
        SemanticNodeV2(
            node_id="mozaiks.page.home",
            kind=SemanticNodeKind.PAGE,
            payload_ref=wrong_node,
        )


def test_type_substitution_fails_closed() -> None:
    resolver, _graph, payloads = _registered_resolver()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")
    wrong_kind = SemanticPayloadRef(
        node_id=page.node_id,
        payload_kind="module",
        payload_version=page.payload_version,
        content_digest=page.payload_digest,
        scope=page.scope,
    )
    with pytest.raises(ReferenceResolutionError, match="kind mismatch"):
        resolver.resolve_semantic_payload(wrong_kind, requesting_scope=_SCOPE)
    with pytest.raises(ValidationError, match="does not match node kind"):
        SemanticNodeV2(
            node_id=page.node_id,
            kind=SemanticNodeKind.MODULE,
            payload_ref=semantic_payload_ref(page),
        )


def test_scope_substitution_fails_closed() -> None:
    resolver, _graph, payloads = _registered_resolver()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")
    foreign = SemanticPayloadRef(
        node_id=page.node_id,
        payload_kind="page",
        payload_version=page.payload_version,
        content_digest=page.payload_digest,
        scope=_OTHER_SCOPE,
    )
    with pytest.raises(ReferenceResolutionError, match="scope"):
        resolver.resolve_semantic_payload(foreign, requesting_scope=_OTHER_SCOPE)
    with pytest.raises(ReferenceResolutionError, match="cross-scope"):
        resolver.resolve_semantic_payload(
            semantic_payload_ref(page), requesting_scope=_OTHER_SCOPE
        )
    # A graph cannot even be constructed around a foreign-scope pin.
    with pytest.raises(ValidationError, match="different scope"):
        build_semantic_graph_v2(
            graph_id="cross-scope",
            version=1,
            scope=_OTHER_SCOPE,
            nodes=[
                SemanticNodeV2(
                    node_id=page.node_id,
                    kind=SemanticNodeKind.PAGE,
                    payload_ref=semantic_payload_ref(page),
                )
            ],
        )


def test_version_drift_fails_closed() -> None:
    resolver, _graph, payloads = _registered_resolver()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")
    drifted = SemanticPayloadRef(
        node_id=page.node_id,
        payload_kind="page",
        payload_version=2,
        content_digest=page.payload_digest,
        scope=page.scope,
    )
    with pytest.raises(ReferenceResolutionError, match="never fall back"):
        resolver.resolve_semantic_payload(drifted, requesting_scope=_SCOPE)


def test_digest_tampering_fails_closed() -> None:
    resolver, _graph, payloads = _registered_resolver()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")
    tampered_ref = SemanticPayloadRef(
        node_id=page.node_id,
        payload_kind="page",
        payload_version=page.payload_version,
        content_digest="f" * 64,
        scope=page.scope,
    )
    with pytest.raises(ReferenceResolutionError, match="digest mismatch"):
        resolver.resolve_semantic_payload(tampered_ref, requesting_scope=_SCOPE)

    document = page.model_dump(mode="json")
    document["title"] = "Tampered"
    with pytest.raises(ValidationError, match="payload_digest does not match"):
        parse_semantic_payload(document)

    document = page.model_dump(mode="json")
    document["payload_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="payload_digest does not match"):
        parse_semantic_payload(document)


@pytest.mark.parametrize("construction", ["model_copy", "model_construct"])
def test_forged_payload_axes_fail_cold_validation_atomically(construction: str) -> None:
    _graph, payloads = _corpus_graph()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")

    def forge(model, **updates):
        if construction == "model_copy":
            return model.model_copy(update=updates)
        return type(model).model_construct(**{**model.__dict__, **updates})

    invalid_sections = tuple(forge(entry, position=0) for entry in page.sections)
    cases = {
        "stale digest": (forge(page, title="Forged"), "payload_digest does not match"),
        "invalid field": (forge(page, title=""), "title must be non-empty text"),
        "invalid ordering": (
            forge(page, sections=invalid_sections),
            "sections positions must be dense",
        ),
        "wrong kind": (
            forge(page, payload_kind=SemanticNodeKind.MODULE),
            "Extra inputs are not permitted",
        ),
        "wrong node": (
            forge(page, node_id="mozaiks.page.other"),
            "payload_digest does not match",
        ),
        "wrong version": (
            forge(page, payload_version=2),
            "payload_digest does not match",
        ),
        "wrong scope": (
            forge(page, scope=_OTHER_SCOPE),
            "payload_digest does not match",
        ),
    }

    sentinel = next(payload for payload in payloads if payload is not page)
    for label, (forged_page, match) in cases.items():
        resolver = SemanticReferenceResolver()
        resolver.register_semantic_payload(sentinel)
        before = resolver._subjects.copy()
        with pytest.raises(ReferenceResolutionError, match=match):
            resolver.register_semantic_payload(forged_page)
        assert resolver._subjects == before, label
        resolver.register_semantic_payload(page)
        resolved = resolver.resolve_semantic_payload(
            semantic_payload_ref(page), requesting_scope=page.scope
        )
        assert resolved.model_dump(mode="json") == page.model_dump(mode="json")


@pytest.mark.parametrize("construction", ["model_copy", "model_construct"])
def test_forged_graph_axes_and_nested_nodes_fail_registration_atomically(
    construction: str,
) -> None:
    graph, payloads = _corpus_graph()
    page_node = graph.node("mozaiks.page.home")

    def forge(model, **updates):
        if construction == "model_copy":
            return model.model_copy(update=updates)
        return type(model).model_construct(**{**model.__dict__, **updates})

    def replace_page_node(replacement):
        return tuple(replacement if node.node_id == page_node.node_id else node for node in graph.nodes)

    cases = {
        "stale graph digest": forge(graph, graph_digest="f" * 64),
        "wrong version": forge(graph, version=2),
        "wrong scope": forge(graph, scope=_OTHER_SCOPE),
        "wrong nested node": forge(
            graph,
            nodes=replace_page_node(forge(page_node, node_id="mozaiks.page.other")),
        ),
        "wrong nested kind": forge(
            graph,
            nodes=replace_page_node(forge(page_node, kind=SemanticNodeKind.MODULE)),
        ),
    }

    for label, forged_graph in cases.items():
        resolver = SemanticReferenceResolver()
        for payload in payloads:
            resolver.register_semantic_payload(payload)
        before = resolver._subjects.copy()
        with pytest.raises(ReferenceResolutionError, match="cold validation"):
            resolver.register_semantic_graph_v2(forged_graph)
        assert resolver._subjects == before, label

    resolver = SemanticReferenceResolver()
    for payload in payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(graph)
    registered = resolver._subjects[(graph.graph_id, graph.version)].content
    assert registered.model_dump(mode="json") == graph.model_dump(mode="json")


@pytest.mark.parametrize("construction", ["model_copy", "model_construct"])
def test_payload_closure_cold_validates_payloads_graphs_and_nested_nodes(
    construction: str,
) -> None:
    graph, payloads = _corpus_graph()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")
    page_node = graph.node(page.node_id)

    def forge(model, **updates):
        if construction == "model_copy":
            return model.model_copy(update=updates)
        return type(model).model_construct(**{**model.__dict__, **updates})

    forged_payloads = [forge(page, title="Forged") if payload is page else payload for payload in payloads]
    with pytest.raises(SemanticPayloadError, match="payload failed cold validation"):
        validate_semantic_graph_v2_payload_closure(graph, forged_payloads)

    forged_graph = forge(graph, graph_digest="f" * 64)
    with pytest.raises(SemanticPayloadError, match="graph v2 failed cold validation"):
        validate_semantic_graph_v2_payload_closure(forged_graph, payloads)

    forged_node = forge(page_node, node_id="mozaiks.page.other")
    forged_nested_graph = forge(
        graph,
        nodes=tuple(forged_node if node.node_id == page.node_id else node for node in graph.nodes),
    )
    with pytest.raises(SemanticPayloadError, match="graph v2 failed cold validation"):
        validate_semantic_graph_v2_payload_closure(forged_nested_graph, payloads)

def test_duplicate_identity_fails_closed() -> None:
    resolver, graph, payloads = _registered_resolver()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")
    before = resolver._subjects.copy()
    with pytest.raises(ReferenceResolutionError, match="immutable"):
        resolver.register_semantic_payload(page)
    assert resolver._subjects == before

    before = resolver._subjects.copy()
    with pytest.raises(ReferenceResolutionError, match="immutable"):
        resolver.register_semantic_graph_v2(graph)
    assert resolver._subjects == before

    with pytest.raises(SemanticPayloadError, match="duplicate payload supplied"):
        validate_semantic_graph_v2_payload_closure(graph, [*payloads, page])


# ---------------------------------------------------------------------------
# Merkle root
# ---------------------------------------------------------------------------


def test_merkle_chain_payload_byte_change_reroots_the_graph() -> None:
    graph, _payloads = _corpus_graph()
    changed_graph, _changed = _corpus_graph(home_title="Home!")

    original_page = graph.node("mozaiks.page.home")
    changed_page = changed_graph.node("mozaiks.page.home")
    # payload digest changes...
    assert original_page.payload_ref.content_digest != changed_page.payload_ref.content_digest
    # ...which changes the node identity payload...
    assert original_page.identity_payload != changed_page.identity_payload
    # ...which changes the graph digest (Merkle root).
    assert graph.graph_digest != changed_graph.graph_digest
    # Everything else about the two graphs is identical.
    assert [n.node_id for n in graph.nodes] == [n.node_id for n in changed_graph.nodes]


def test_stale_graph_digest_is_rejected() -> None:
    graph, _payloads = _corpus_graph()
    changed_graph, _changed = _corpus_graph(home_title="Home!")
    stale = graph.model_dump(mode="json")
    stale["nodes"] = changed_graph.model_dump(mode="json")["nodes"]
    with pytest.raises(ValidationError, match="graph_digest does not match"):
        SemanticGraphV2.model_validate(stale)


def test_golden_merkle_root_pinned_and_stable_across_processes() -> None:
    graph, payloads = _corpus_graph()
    assert graph.graph_digest == _GOLDEN_GRAPH_DIGEST

    fixture = build_deterministic_archive(
        [
            ArchiveEntry(
                path=f"payloads/{payload.node_id}.json",
                content=json.dumps(
                    payload.canonical_payload(), sort_keys=True, ensure_ascii=True
                ).encode("ascii"),
            )
            for payload in payloads
        ]
        + [
            ArchiveEntry(
                path="graph.json",
                content=json.dumps(
                    graph.canonical_payload(), sort_keys=True, ensure_ascii=True
                ).encode("ascii"),
            )
        ]
    )
    assert archive_digest(fixture) == _GOLDEN_ARCHIVE_DIGEST

    probe = (
        "import json\n"
        "from tests.test_semantic_payload_graph_v2 import _corpus_graph\n"
        "graph, _payloads = _corpus_graph()\n"
        "print(graph.graph_digest)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == _GOLDEN_GRAPH_DIGEST


def test_archive_identity_is_transport_evidence_not_semantics() -> None:
    """Archiving the fixture differently must not perturb graph identity."""
    graph, payloads = _corpus_graph()
    alternate = build_deterministic_archive(
        [
            ArchiveEntry(
                path="bundle.json",
                content=json.dumps(
                    [payload.canonical_payload() for payload in payloads],
                    sort_keys=True,
                    ensure_ascii=True,
                ).encode("ascii"),
            )
        ]
    )
    assert archive_digest(alternate) != _GOLDEN_ARCHIVE_DIGEST
    assert graph.graph_digest == _GOLDEN_GRAPH_DIGEST


# ---------------------------------------------------------------------------
# Payload reuse across graph versions
# ---------------------------------------------------------------------------


def test_payload_reuse_across_graph_versions_of_one_scope() -> None:
    graph, payloads = _corpus_graph()
    resolver = SemanticReferenceResolver()
    for payload in payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(graph)

    successor = build_semantic_graph_v2(
        graph_id=graph.graph_id,
        version=2,
        scope=graph.scope,
        nodes=graph.nodes,
        edges=graph.edges,
        namespace_grants=graph.namespace_grants,
    )
    resolver.register_semantic_graph_v2(successor)  # same payloads, no re-registration
    assert successor.graph_digest != graph.graph_digest  # version participates


def test_partial_closure_fails_closed() -> None:
    graph, payloads = _corpus_graph()
    resolver = SemanticReferenceResolver()
    for payload in payloads[:-1]:  # one pinned payload missing
        resolver.register_semantic_payload(payload)
    before = resolver._subjects.copy()
    with pytest.raises(ReferenceResolutionError, match="no semantic payload"):
        resolver.register_semantic_graph_v2(graph)
    assert resolver._subjects == before
    assert (graph.graph_id, graph.version) not in resolver._subjects

    with pytest.raises(SemanticPayloadError, match="not supplied"):
        validate_semantic_graph_v2_payload_closure(graph, payloads[:-1])

    unpinned = build_semantic_payload(
        SurfacePayload,
        node_id="mozaiks.surface.extra",
        payload_version=1,
        scope=_SCOPE,
        description="Not pinned by the graph",
    )
    with pytest.raises(SemanticPayloadError, match="not pinned"):
        validate_semantic_graph_v2_payload_closure(graph, [*payloads, unpinned])


# ---------------------------------------------------------------------------
# V1 shapes, strictness, determinism, immutability
# ---------------------------------------------------------------------------


def test_v1_graph_document_is_rejected_by_v2_model() -> None:
    scope = _SCOPE
    from mozaiksai.core.semantics.graph import SemanticNode

    v1 = build_semantic_graph(
        graph_id="v1-doc",
        version=1,
        scope=scope,
        nodes=[SemanticNode(node_id="mozaiks.page.home", kind=SemanticNodeKind.PAGE)],
    )
    with pytest.raises(ValidationError):
        SemanticGraphV2.model_validate(v1.model_dump(mode="json"))
    assert SEMANTIC_GRAPH_V2_SCHEMA_VERSION != v1.schema_version


def test_unknown_fields_and_untyped_shapes_are_rejected() -> None:
    page = _corpus_payloads()[SemanticNodeKind.PAGE]
    document = page.model_dump(mode="json")
    document["extra_blob"] = {"anything": 1}
    with pytest.raises(ValidationError):
        parse_semantic_payload(document)


@pytest.mark.parametrize(
    ("model", "fields", "match"),
    [
        (
            PagePayload,
            {
                "title": "T",
                "intent": "I",
                "sections": (
                    PageSectionEntry(position=0, section_node_id="mozaiks.section.a"),
                    PageSectionEntry(position=2, section_node_id="mozaiks.section.b"),
                ),
            },
            "dense",
        ),
        (
            TriggerPayload,
            {"description": "D", "trigger_kind": TriggerKind.EVENT},
            "requires event_id",
        ),
        (
            TriggerPayload,
            {
                "description": "D",
                "trigger_kind": TriggerKind.EVENT,
                "event_id": "reports.report_created",
                "endpoint_path": "/api/x",
            },
            "must not set",
        ),
        (
            DataCollectionPayload,
            {
                "description": "D",
                "fields": (
                    TypedFieldSpec(name="name", field_type=FieldType.STRING, required=True),
                ),
                "indexes": (IndexSpec(name="bad", field_names=("missing",), unique=False),),
            },
            "undeclared field",
        ),
        (
            StubDeclarationPayload,
            {
                "stub_kind": "python_backend",
                "path": "modules\\reports\\hook.py",
                "entrypoint": "run",
            },
            "backslash",
        ),
    ],
)
def test_typed_variant_rules_fail_closed(model, fields, match) -> None:
    with pytest.raises((ValidationError, ValueError), match=match):
        build_semantic_payload(
            model, node_id="mozaiks.subject.x", payload_version=1, scope=_SCOPE, **fields
        )


def test_prices_are_integer_minor_units_never_floats() -> None:
    with pytest.raises(ValidationError):
        PriceSpec(amount_minor_units=19.99, currency="USD", period=BillingPeriod.MONTHLY)


@pytest.mark.parametrize("currency", ["XAD", "XAU", "XTS", "XXX"])
def test_prices_accept_representative_iso_4217_list_one_special_codes(currency: str) -> None:
    price = PriceSpec(
        amount_minor_units=1900,
        currency=currency,
        period=BillingPeriod.MONTHLY,
    )
    assert price.currency == currency


@pytest.mark.parametrize("currency", ["usd", "US", "USDD", "US$", "ZZZ", "BGN", ""])
def test_prices_reject_lowercase_malformed_unassigned_and_historic_codes(
    currency: str,
) -> None:
    with pytest.raises(ValidationError, match="ISO-4217"):
        PriceSpec(
            amount_minor_units=1900,
            currency=currency,
            period=BillingPeriod.MONTHLY,
        )


def test_order_bearing_input_permutations_do_not_change_page_or_section_digests() -> None:
    payloads = _corpus_payloads()
    page = payloads[SemanticNodeKind.PAGE]
    permuted_page = build_semantic_payload(
        PagePayload,
        node_id=page.node_id,
        payload_version=page.payload_version,
        scope=page.scope,
        title=page.title,
        intent=page.intent,
        sections=tuple(reversed(page.sections)),
    )
    assert permuted_page.payload_digest == page.payload_digest
    assert [entry.section_node_id for entry in permuted_page.sections] == [
        entry.section_node_id for entry in page.sections
    ]

    section = payloads[SemanticNodeKind.SECTION]
    permuted_section = build_semantic_payload(
        SectionPayload,
        node_id=section.node_id,
        payload_version=section.payload_version,
        scope=section.scope,
        title=section.title,
        intent=section.intent,
        entries=tuple(reversed(section.entries)),
    )
    assert permuted_section.payload_digest == section.payload_digest
    assert permuted_section.entries == section.entries


def test_unordered_identity_collections_remain_permutation_stable() -> None:
    action = _corpus_payloads()[SemanticNodeKind.ACTION]
    fields = (
        TypedFieldSpec(name="zeta", field_type=FieldType.STRING, required=False),
        TypedFieldSpec(name="alpha", field_type=FieldType.INTEGER, required=True),
    )
    emits = ("reports.report_updated", "reports.report_created")
    first = build_semantic_payload(
        ActionPayload,
        node_id=action.node_id,
        payload_version=action.payload_version,
        scope=action.scope,
        description=action.description,
        request_fields=fields,
        response_fields=fields,
        emits=emits,
        entitlement_gate=action.entitlement_gate,
    )
    permuted = build_semantic_payload(
        ActionPayload,
        node_id=action.node_id,
        payload_version=action.payload_version,
        scope=action.scope,
        description=action.description,
        request_fields=tuple(reversed(fields)),
        response_fields=tuple(reversed(fields)),
        emits=tuple(reversed(emits)),
        entitlement_gate=action.entitlement_gate,
    )
    assert permuted.payload_digest == first.payload_digest
    assert permuted.request_fields == first.request_fields
    assert permuted.response_fields == first.response_fields
    assert permuted.emits == first.emits


def test_meaningful_page_and_section_order_changes_identity() -> None:
    payloads = _corpus_payloads()
    page = payloads[SemanticNodeKind.PAGE]
    reordered_page = build_semantic_payload(
        PagePayload,
        node_id=page.node_id,
        payload_version=page.payload_version,
        scope=page.scope,
        title=page.title,
        intent=page.intent,
        sections=tuple(
            entry.model_copy(update={"position": 1 - entry.position})
            for entry in page.sections
        ),
    )
    assert reordered_page.payload_digest != page.payload_digest
    assert [entry.section_node_id for entry in reordered_page.sections] == list(
        reversed([entry.section_node_id for entry in page.sections])
    )

    section = payloads[SemanticNodeKind.SECTION]
    reordered_section = build_semantic_payload(
        SectionPayload,
        node_id=section.node_id,
        payload_version=section.payload_version,
        scope=section.scope,
        title=section.title,
        intent=section.intent,
        entries=tuple(
            entry.model_copy(update={"position": 1 - entry.position})
            for entry in section.entries
        ),
    )
    assert reordered_section.payload_digest != section.payload_digest
    assert [entry.entry_kind for entry in reordered_section.entries] == list(
        reversed([entry.entry_kind for entry in section.entries])
    )


def test_negative_duplicate_sparse_and_non_dense_positions_fail_closed() -> None:
    payloads = _corpus_payloads()
    page = payloads[SemanticNodeKind.PAGE]
    section = payloads[SemanticNodeKind.SECTION]
    for positions in ((0, 0), (0, 2), (-1, 0)):
        with pytest.raises(ValidationError, match="position"):
            build_semantic_payload(
                PagePayload,
                node_id=page.node_id,
                payload_version=page.payload_version,
                scope=page.scope,
                title=page.title,
                intent=page.intent,
                sections=tuple(
                    entry.model_copy(update={"position": position})
                    for entry, position in zip(page.sections, positions, strict=True)
                ),
            )
        with pytest.raises(ValidationError, match="position"):
            build_semantic_payload(
                SectionPayload,
                node_id=section.node_id,
                payload_version=section.payload_version,
                scope=section.scope,
                title=section.title,
                intent=section.intent,
                entries=tuple(
                    entry.model_copy(update={"position": position})
                    for entry, position in zip(section.entries, positions, strict=True)
                ),
            )


def test_payloads_and_v2_nodes_are_frozen() -> None:
    page = _corpus_payloads()[SemanticNodeKind.PAGE]
    with pytest.raises(ValidationError):
        page.title = "Mutated"
    node = SemanticNodeV2(
        node_id=page.node_id,
        kind=SemanticNodeKind.PAGE,
        payload_ref=semantic_payload_ref(page),
    )
    with pytest.raises(ValidationError):
        node.node_id = "mozaiks.page.other"


# ---------------------------------------------------------------------------
# Authority boundaries
# ---------------------------------------------------------------------------


def test_binding_cannot_author_or_perturb_payload_facts() -> None:
    graph, _payloads = _corpus_graph()
    assert "payload" not in " ".join(ImplementationBinding.model_fields)
    binding_fields = set(ImplementationBinding.model_fields)
    assert not any("payload" in name for name in binding_fields)
    # Graph identity is computed before and without any binding: rebuilding
    # the same graph yields the same digest regardless of binding existence.
    rebuilt, _ = _corpus_graph()
    assert rebuilt.graph_digest == graph.graph_digest


def test_no_payload_field_accepts_a_child_contract_ref() -> None:
    for model in PAYLOAD_MODEL_BY_KIND.values():
        for name, field in model.model_fields.items():
            assert "ChildContractRef" not in str(field.annotation), (model, name)


def test_capability_advertisement_is_unchanged() -> None:
    assert advertised_semantic_compiler_capabilities() == ()


def test_production_sources_do_not_import_payloads_or_v2_symbols() -> None:
    python_paths = _tracked_production_paths(suffixes=(".py", ".pyi"))
    declarative_paths = _tracked_production_paths(suffixes=_DECLARATIVE_SUFFIXES)

    # The proof itself is non-vacuous across both production roots, rendered
    # Python stubs, and declarative templates that can become loader input.
    assert {path.parts[0] for path in python_paths} == _PRODUCTION_ROOTS
    assert (
        Path("factory_app/build_context/commerce/templates/modules/commerce/backend/handler.py")
        in python_paths
    )
    assert (
        Path(
            "factory_app/build_context/operator_readiness/templates/config/"
            "operator_readiness.yaml.j2"
        )
        in declarative_paths
    )

    offenders: set[str] = set()
    for relative in python_paths:
        if relative in _SEMANTICS_OWNER_FILES:
            continue
        source = (ROOT / relative).read_text(encoding="utf-8")
        if _python_source_has_forbidden_production_reference(
            source, filename=relative.as_posix()
        ):
            offenders.add(relative.as_posix())

    for relative in declarative_paths:
        source = (ROOT / relative).read_text(encoding="utf-8")
        if _contains_forbidden_production_reference(source):
            offenders.add(relative.as_posix())

    assert sorted(offenders) == []


@pytest.mark.parametrize(
    ("reference_class", "filename", "source"),
    [
        (
            "direct import",
            "probe.py",
            "from mozaiksai.core.semantics.graph import SemanticGraphV2\n",
        ),
        (
            "direct module import",
            "probe.py",
            "import mozaiksai.core.semantics.payloads\n",
        ),
        (
            "aliased import",
            "probe.py",
            "from mozaiksai.core.semantics.graph import SemanticGraphV2 as Graph\n",
        ),
        (
            "dynamic import",
            "probe.py",
            "import importlib\nimportlib.import_module('mozaiksai.core.semantics.payloads')\n",
        ),
        (
            "module-qualified access",
            "probe.py",
            "import mozaiksai.core.semantics.graph as graph\ngraph.SemanticGraphV2\n",
        ),
        (
            "Python string reference",
            "probe.py",
            "entrypoint = 'mozaiksai.core.semantics.graph:SemanticGraphV2'\n",
        ),
        (
            "unrendered Python stub",
            "stub.py",
            "{{ invalid_python }}\nSemanticPayloadRef\n",
        ),
        (
            "JSON reference",
            "probe.json",
            '{"entrypoint":"mozaiksai.core.semantics.graph:SemanticGraphV2"}',
        ),
        (
            "YAML reference",
            "probe.yaml",
            "entrypoint: mozaiksai.core.semantics.payloads:parse_semantic_payload\n",
        ),
        (
            "declarative template reference",
            "probe.yaml.j2",
            "entrypoint: mozaiksai.core.semantics.graph:SemanticGraphV2\n",
        ),
        (
            "TOML reference",
            "probe.toml",
            "entrypoint = 'mozaiksai.core.semantics.refs:SemanticPayloadRef'\n",
        ),
    ],
)
def test_production_hygiene_guard_mutation_probe_detects_every_reference_class(
    reference_class: str,
    filename: str,
    source: str,
) -> None:
    if filename.endswith((".py", ".pyi")):
        detected = _python_source_has_forbidden_production_reference(
            source, filename=filename
        )
    else:
        detected = _contains_forbidden_production_reference(source)
    assert detected, reference_class


def test_payload_modules_have_no_ag2_imports() -> None:
    for name in ("payloads.py", "graph.py", "refs.py", "resolver.py"):
        text = (ROOT / "mozaiksai/core/semantics" / name).read_text(encoding="utf-8")
        assert "import ag2" not in text and "from ag2" not in text, name
