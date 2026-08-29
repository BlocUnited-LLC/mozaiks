"""ADR 0007 Slice 2E proof gate: typed payloads + Merkle-rooted graph v2.

Adversarial matrix: kind closure, node/scope/type substitution, version
drift, digest tampering, duplicate identity, Merkle-root chain, payload
reuse, v1-shape rejection, partial closure, deterministic bytes,
immutability, binding non-authority, and no capability advertisement.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import get_args

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

ROOT = Path(__file__).resolve().parents[1]

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


def test_stub_kind_literal_mirrors_layout_registry_enum() -> None:
    from mozaiksai.core.runtime.app.layout_registry import StubKind
    from mozaiksai.core.semantics.payloads import StubKindLiteral

    assert set(get_args(StubKindLiteral)) == {kind.value for kind in StubKind}


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


def test_duplicate_identity_fails_closed() -> None:
    resolver, graph, payloads = _registered_resolver()
    page = next(p for p in payloads if p.node_id == "mozaiks.page.home")
    with pytest.raises(ReferenceResolutionError, match="immutable"):
        resolver.register_semantic_payload(page)
    with pytest.raises(ReferenceResolutionError, match="immutable"):
        resolver.register_semantic_graph_v2(graph)
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
    with pytest.raises(ReferenceResolutionError, match="no semantic payload"):
        resolver.register_semantic_graph_v2(graph)

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
    with pytest.raises(ValidationError, match="currency"):
        PriceSpec(amount_minor_units=1900, currency="usd", period=BillingPeriod.MONTHLY)


def test_ordering_permutations_do_not_change_digests() -> None:
    base = _corpus_payloads()[SemanticNodeKind.PAGE]
    permuted = build_semantic_payload(
        PagePayload,
        node_id=base.node_id,
        payload_version=base.payload_version,
        scope=base.scope,
        title=base.title,
        intent=base.intent,
        sections=tuple(reversed(base.sections)),
    )
    assert permuted.payload_digest == base.payload_digest
    assert [entry.section_node_id for entry in permuted.sections] == [
        entry.section_node_id for entry in base.sections
    ]


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
    offenders: list[str] = []
    excluded = {
        Path("mozaiksai/core/semantics/payloads.py"),
        Path("mozaiksai/core/semantics/graph.py"),
        Path("mozaiksai/core/semantics/refs.py"),
        Path("mozaiksai/core/semantics/resolver.py"),
        Path("tests/test_semantic_payload_graph_v2.py"),
    }
    markers = (
        "semantics.payloads",
        "SemanticGraphV2",
        "SemanticNodeV2",
        "SemanticPayloadRef",
        "semantic_payload",
    )
    roots = [ROOT / "mozaiksai", ROOT / "factory_app"]
    for root in roots:
        for path in root.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if relative in excluded:
                continue
            text = path.read_text(encoding="utf-8")
            if any(marker in text for marker in markers):
                offenders.append(relative.as_posix())
    assert offenders == []


def test_payload_modules_have_no_ag2_imports() -> None:
    for name in ("payloads.py", "graph.py", "refs.py", "resolver.py"):
        text = (ROOT / "mozaiksai/core/semantics" / name).read_text(encoding="utf-8")
        assert "import ag2" not in text and "from ag2" not in text, name
