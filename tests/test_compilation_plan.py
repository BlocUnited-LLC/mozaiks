"""ADR 0007 Slice 4B proof gate: aggregate ``CompilationPlan`` derivation.

Adversarial matrix: input substitution, scope/version substitution, forged
plan documents (missing/extra units, duplicate paths, case-fold and prefix
collisions, dependency cycles), ordering determinism, cross-process canonical
equality, input immutability, unknown families and unresolved renderers as
typed gaps, digest propagation payload → graph → plan, regeneration closure,
family-plan non-authority, completeness over every registry row, and zero
production/AG2/authority surface.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.capabilities import advertised_semantic_compiler_capabilities
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    CompilationPlanError,
    FamilyInstancePlan,
    PlanDisposition,
    derive_compilation_plan,
    plan_regeneration_closure,
)
from mozaiksai.core.semantics.refs import (
    CompilationPlanRef,
    ExecutionAccessScopeRef,
    RefDocumentType,
)
from mozaiksai.core.semantics.resolver import (
    ReferenceResolutionError,
    SemanticReferenceResolver,
)
from tests.test_semantic_payload_graph_v2 import _corpus_graph

ROOT = Path(__file__).resolve().parents[1]

_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant1", workspace_id="ws1")
_OTHER_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant2")

# Golden aggregate digest for the full 2E corpus over the built-in registry.
# This corpus declares every optional family ABSENT_BY_DECLARATION while
# carrying auth/integration/workflow payloads — contradictory selection
# evidence. Slice 5D-0B2A's family-local selection-honesty completion keeps
# app_manifest, app_integrations_config, and app_secret_references as typed
# gaps here (each consumes the contradicted facts) and defers app_config
# outright (no application-level AI-launch authority exists). The route
# manifest consumes none of the contradicted facts — pages and default_route
# are complete and custom routes are unselected — so it renders alongside the
# page family. Plan identity also includes #475's consulted assignment-contract
# closure. The honest four-family closure is proven on the
# selection-consistent fixture in tests/test_app_family_materialization_b2a.py.
# Re-pinned once for the source-locality correction: app_secret_references no
# longer declares auth as a semantic input (security/secrets.yaml consumes no
# auth fact), which changes the registry row digest and therefore every plan
# identity. Proven before re-pinning: the removed source was unconsumed
# (roles-only auth mutation left secrets bytes identical), all four rendered
# outputs are byte-identical, selective reuse improved (the secret unit now
# survives auth mutations), and no required source was dropped (loader and
# mutation suites green). Re-pinned after rebasing onto #475 because the same
# canonical plan now also pins its consulted assignment-contract closure;
# rendered bytes and family activation are unchanged.
# Re-pinned once for the authority-enforcement PR: plans no longer emit the
# former unconditional "registry" renderer-resolution pseudo-gap (renderer
# resolution is ImplementationBinding authority enforced at materialization),
# so the gap set shrinks by exactly that one structural entry. Identity-only:
# no unit, byte, footprint, or assignment fact changed.
# The interface family adds exactly two layout rows and two units (one scope
# inactive). Its complete pre-family fixture proves all 59 prior units unchanged;
# only aggregate identity incorporates this newly declared family.
_GOLDEN_PLAN_DIGEST = "f37b6ef7cd20344a0908eff052cfd83eeaccd8e113f345ea1f37a9c5a73d127d"


def _registry():
    return build_app_layout_registry(())


def _plan(*, home_title: str = "Home"):
    graph, payloads = _corpus_graph(home_title=home_title)
    return derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())


# ---------------------------------------------------------------------------
# Derivation identity, determinism, immutability
# ---------------------------------------------------------------------------


def test_one_aggregate_plan_per_immutable_graph_identity() -> None:
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    assert plan.graph_id == graph.graph_id
    assert plan.graph_version == graph.version
    assert plan.scope == graph.scope
    assert plan.graph_digest == graph.graph_digest
    registry = _registry()
    from mozaiksai.core.semantics.compilation_plan import snapshot_layout_registry

    # Blocker-1 semantics: the plan pins the RECOMPUTED snapshot identity,
    # never the registry object's claimed digest.
    assert plan.registry_digest == snapshot_layout_registry(registry).snapshot_digest
    assert plan.registry_digest != registry.registry_digest
    assert plan.registry_schema_version == str(registry.schema_version)
    # Same immutable inputs -> byte-identical plan.
    again = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    assert again.plan_digest == plan.plan_digest
    assert again.canonical_payload() == plan.canonical_payload()


def test_ordering_is_deterministic_and_input_order_independent() -> None:
    graph, payloads = _corpus_graph()
    permuted = list(reversed(payloads))
    plan_a = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    plan_b = derive_compilation_plan(graph=graph, payloads=permuted, registry=_registry())
    assert plan_a.plan_digest == plan_b.plan_digest
    assert [u.unit_id for u in plan_a.units] == [u.unit_id for u in plan_b.units]
    # Family order respects the registry's dependency-respecting total order:
    # a unit never precedes a dependency unit of another family kind.
    position = {unit.unit_id: index for index, unit in enumerate(plan_a.units)}
    for unit in plan_a.units:
        for dependency in unit.depends_on_units:
            assert position[dependency] < position[unit.unit_id], (
                unit.unit_id,
                dependency,
            )


def test_cross_process_canonical_equality() -> None:
    plan = _plan()
    assert plan.plan_digest == _GOLDEN_PLAN_DIGEST
    probe = (
        "from tests.test_compilation_plan import _plan\n"
        "print(_plan().plan_digest)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == _GOLDEN_PLAN_DIGEST


def test_inputs_are_never_mutated() -> None:
    graph, payloads = _corpus_graph()
    graph_before = graph.model_dump(mode="json")
    payloads_before = [payload.model_dump(mode="json") for payload in payloads]
    registry = _registry()
    registry_digest_before = registry.registry_digest
    derive_compilation_plan(graph=graph, payloads=payloads, registry=registry)
    assert graph.model_dump(mode="json") == graph_before
    assert [payload.model_dump(mode="json") for payload in payloads] == payloads_before
    assert registry.registry_digest == registry_digest_before


# ---------------------------------------------------------------------------
# Input substitution
# ---------------------------------------------------------------------------


def test_forged_graph_fails_cold_validation() -> None:
    graph, payloads = _corpus_graph()
    forged = graph.model_copy(update={"graph_digest": "f" * 64})
    with pytest.raises(CompilationPlanError, match="cold validation"):
        derive_compilation_plan(graph=forged, payloads=payloads, registry=_registry())


def test_payload_substitution_fails_closure() -> None:
    graph, payloads = _corpus_graph()
    foreign = list(_corpus_graph(home_title="Home!")[1])
    swapped = [
        next(p for p in foreign if p.node_id == payload.node_id)
        if payload.node_id == "mozaiks.page.home"
        else payload
        for payload in payloads
    ]
    with pytest.raises(CompilationPlanError, match="closure"):
        derive_compilation_plan(graph=graph, payloads=swapped, registry=_registry())


def test_scope_and_version_substitution_produce_distinct_plans() -> None:
    base = _plan()
    graph_v2, payloads_v2 = _corpus_graph()
    from mozaiksai.core.semantics.graph import build_semantic_graph_v2

    successor_graph = build_semantic_graph_v2(
        graph_id=graph_v2.graph_id,
        version=2,
        scope=graph_v2.scope,
        nodes=graph_v2.nodes,
        edges=graph_v2.edges,
        namespace_grants=graph_v2.namespace_grants,
    )
    successor = derive_compilation_plan(
        graph=successor_graph, payloads=payloads_v2, registry=_registry()
    )
    assert successor.plan_digest != base.plan_digest
    assert successor.graph_version == 2

    other_scope_graph, other_scope_payloads = _corpus_graph(scope=_OTHER_SCOPE)
    other = derive_compilation_plan(
        graph=other_scope_graph, payloads=other_scope_payloads, registry=_registry()
    )
    assert other.plan_digest != base.plan_digest
    with pytest.raises(CompilationPlanError, match="lineage"):
        plan_regeneration_closure(base, other)


# ---------------------------------------------------------------------------
# Forged plan documents
# ---------------------------------------------------------------------------


def _document(plan: CompilationPlan) -> dict:
    return plan.model_dump(mode="json")


def test_missing_and_extra_units_fail_closed() -> None:
    plan = _plan()
    missing = _document(plan)
    removed = missing["units"].pop()
    with pytest.raises(ValidationError, match="plan_digest does not match"):
        CompilationPlan.model_validate(missing)

    extra = _document(plan)
    duplicate = copy.deepcopy(removed)
    extra["units"].append(duplicate)
    extra["units"].append(copy.deepcopy(duplicate))
    with pytest.raises(ValidationError, match="duplicate unit identities|plan_digest"):
        CompilationPlan.model_validate(extra)


def test_duplicate_output_ownership_fails_closed() -> None:
    plan = _plan()
    document = _document(plan)
    render_units = [u for u in document["units"] if u["outputs"]]
    victim, thief = render_units[0], render_units[1]
    thief["outputs"] = copy.deepcopy(victim["outputs"])
    thief["placeholder_values"] = copy.deepcopy(victim["placeholder_values"])
    with pytest.raises(ValidationError, match="duplicate output ownership|plan_digest"):
        CompilationPlan.model_validate(document)


def test_case_fold_and_prefix_collisions_fail_closed() -> None:
    plan = _plan()
    document = _document(plan)
    units = [u for u in document["units"] if u["outputs"] and not u["placeholder_values"]]
    first, second = units[0], units[1]
    first["outputs"] = [{"path_scope": "app_bundle_root", "path": "shared/Config.json"}]
    second["outputs"] = [{"path_scope": "app_bundle_root", "path": "shared/config.JSON"}]
    with pytest.raises(ValidationError, match="output collision|plan_digest"):
        CompilationPlan.model_validate(document)

    document = _document(plan)
    units = [u for u in document["units"] if u["outputs"] and not u["placeholder_values"]]
    first, second = units[0], units[1]
    first["outputs"] = [{"path_scope": "app_bundle_root", "path": "shared/config"}]
    second["outputs"] = [{"path_scope": "app_bundle_root", "path": "shared/config/x.json"}]
    with pytest.raises(ValidationError, match="output collision|plan_digest"):
        CompilationPlan.model_validate(document)


def test_dependency_cycles_fail_closed() -> None:
    plan = _plan()
    document = _document(plan)
    first, second = document["units"][:2]
    first["depends_on_units"] = [second["unit_id"]]
    second["depends_on_units"] = [first["unit_id"]]
    _redigest(document)
    with pytest.raises(ValidationError, match="dependency cycle"):
        CompilationPlan.model_validate(document)

    document = _document(plan)
    document["units"][0]["depends_on_units"] = [document["units"][0]["unit_id"]]
    with pytest.raises(ValidationError, match="depends on itself|plan_digest"):
        CompilationPlan.model_validate(document)


def test_stale_plan_digest_is_rejected() -> None:
    plan = _plan()
    document = _document(plan)
    document["graph_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="plan_digest does not match"):
        CompilationPlan.model_validate(document)


# ---------------------------------------------------------------------------
# Completeness, gaps, unknown families, unresolved renderers
# ---------------------------------------------------------------------------


def test_every_registry_row_is_disposed_or_explicitly_gapped() -> None:
    from mozaiksai.core.semantics.compilation_plan import snapshot_layout_registry

    plan = _plan()
    snapshot = snapshot_layout_registry(_registry())
    assert plan.registry_digest == snapshot.snapshot_digest
    disposed = {unit.family_identity_digest for unit in plan.units}
    gapped = {(gap.family_kind, gap.path_template) for gap in plan.gaps}
    for row in snapshot.rows:
        covered = row.row_digest in disposed or ((row.kind, row.path_template) in gapped)
        assert covered, (row.kind, row.path_template)


def test_binding_conditions_are_typed_gaps_never_guesses() -> None:
    from mozaiksai.core.semantics.compilation_plan import PlanGapCode

    plan = _plan()
    deferred_subjects = {
        gap.subject
        for gap in plan.gaps
        if gap.code is PlanGapCode.BINDING_CONDITION_DEFERRED
    }
    assert "when_runtime_support_selected" in deferred_subjects
    assert all(gap.adr_slice in (4, 5, 6, 7) for gap in plan.gaps)


def test_unknown_layout_family_condition_becomes_a_typed_gap() -> None:
    registry = _registry()

    class _NovelCondition:
        value = "when_something_new"

    class _NovelFamily:
        kind = type("K", (), {"value": "novel_family"})()
        requirement = type("R", (), {"value": "conditional"})()
        multiplicity = type("Mu", (), {"value": "single"})()
        condition = _NovelCondition()
        path_scope = type("S", (), {"value": "app_bundle_root"})()
        path_template = "novel/output.yaml"
        materializer = type("M", (), {"value": "unknown"})()
        disposition = type("D", (), {"value": "render"})()
        owner = type("O", (), {"value": "app_workspace"})()
        dependency_families = ()
        assignment_kinds = ()
        validator = type("V", (), {"value": "none"})()
        semantic_input_kinds = ()

    class _NovelRegistry:
        schema_version = registry.schema_version
        registry_digest = registry.registry_digest

        def ordered_families(self):
            return (*registry.ordered_families(), _NovelFamily())

    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=_NovelRegistry())
    from mozaiksai.core.semantics.compilation_plan import PlanGapCode

    assert any(
        gap.family_kind == "novel_family"
        and gap.code is PlanGapCode.CONDITION_UNDERIVABLE
        and gap.subject == "when_something_new"
        for gap in plan.gaps
    )


def test_renderer_resolution_is_binding_authority_not_a_plan_gap() -> None:
    """Renderer resolution belongs to the ImplementationBinding and is
    enforced at materialization; plans no longer carry the former
    unconditional "registry" pseudo-gap (which made the composition
    zero-gap contract unsatisfiable by construction), and plan units still
    cannot claim a renderer identity themselves."""
    from mozaiksai.core.semantics.compilation_plan import PlanGapCode

    plan = _plan()
    assert not any(
        gap.code is PlanGapCode.RENDERER_RESOLUTION_DEFERRED for gap in plan.gaps
    )
    assert "renderer" not in FamilyInstancePlan.model_fields
    assert "renderer_version" not in FamilyInstancePlan.model_fields


def test_dispositions_are_complete_and_external_handoff_covers_deployment() -> None:
    plan = _plan()
    assert {unit.disposition for unit in plan.units} <= set(PlanDisposition)
    handoff = [u for u in plan.units if u.disposition is PlanDisposition.EXTERNAL_HANDOFF]
    assert handoff, "deployment artifacts must be external handoff units"
    for unit in handoff:
        assert unit.family_kind in {"app_deployment_artifact"}, unit.family_kind


# ---------------------------------------------------------------------------
# Digest propagation and regeneration closure
# ---------------------------------------------------------------------------


def test_digest_propagates_payload_to_graph_to_plan() -> None:
    from mozaiksai.core.semantics.compilation_plan import PlanSourceScope

    base = _plan()
    changed = _plan(home_title="Home!")
    assert base.graph_digest != changed.graph_digest
    assert base.plan_digest != changed.plan_digest
    closure = plan_regeneration_closure(base, changed)
    affected = set(closure.affected)
    # Directly sourced units are affected...
    page_units = {
        unit.unit_id
        for unit in changed.units
        if any(source.node_id == "mozaiks.page.home" for source in unit.sources)
    }
    assert page_units and page_units <= affected
    # ...graph-wide consumers are affected by any graph change...
    graph_wide = {
        unit.unit_id
        for unit in changed.units
        if unit.source_scope is PlanSourceScope.GRAPH_WIDE
    }
    assert graph_wide <= affected
    # Route-manifest rendering now declares typed inputs (5D-0B2A); on this
    # corpus it either derives a render unit or remains an explicit typed gap —
    # never a silent omission.
    route_units = [u for u in changed.units if u.family_kind == "app_ui_route_manifest"]
    route_gaps = [g for g in changed.gaps if g.family_kind == "app_ui_route_manifest"]
    assert route_units or route_gaps
    # ...and unrelated declared units with unchanged footprints stay reusable.
    unrelated_units = {
        unit.unit_id
        for unit in changed.units
        if unit.family_kind == "app_deployment_artifact"
        and unit.disposition is PlanDisposition.EXTERNAL_HANDOFF
    }
    assert unrelated_units and unrelated_units <= set(closure.reusable)
    assert not closure.added and not closure.removed
    assert affected | set(closure.reusable) == {unit.unit_id for unit in changed.units}


def test_regeneration_closure_partitions_everything() -> None:
    base = _plan()
    closure = plan_regeneration_closure(base, base)
    assert closure.affected == () and closure.added == () and closure.removed == ()
    assert set(closure.reusable) == {unit.unit_id for unit in base.units}


def test_reuse_from_base_requires_base_plan_digest() -> None:
    plan = _plan()
    unit = plan.units[0]
    with pytest.raises(ValidationError, match="reuse_from_base"):
        FamilyInstancePlan(
            unit_id="x/y",
            family_kind="app_manifest",
            family_identity_digest=unit.family_identity_digest,
            disposition=PlanDisposition.REUSE_FROM_BASE,
            source_scope="declared",
            materializer="none",
            validator="none",
        )
    with pytest.raises(ValidationError, match="reuse_from_base"):
        FamilyInstancePlan(
            unit_id="x/y",
            family_kind="app_manifest",
            family_identity_digest=unit.family_identity_digest,
            disposition=PlanDisposition.RENDER,
            source_scope="declared",
            materializer="none",
            validator="none",
            base_plan_digest="a" * 64,
        )


def test_reuse_from_base_source_identity_invalidates_reuse() -> None:
    plan = _plan()
    target = next(unit for unit in plan.units if unit.assignment_kind is None)

    def _with_base_digest(base_digest: str) -> CompilationPlan:
        document = plan.model_dump(mode="json")
        for unit in document["units"]:
            if unit["unit_id"] == target.unit_id:
                unit["disposition"] = PlanDisposition.REUSE_FROM_BASE.value
                unit["base_plan_digest"] = base_digest
                break
        return CompilationPlan.model_validate(_redigest(document))

    base = _with_base_digest("a" * 64)
    successor = _with_base_digest("b" * 64)
    closure = plan_regeneration_closure(base, successor)
    assert target.unit_id in closure.affected
    assert target.unit_id not in closure.reusable


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------


def test_every_instance_output_traces_to_node_and_payload_identity() -> None:
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    payload_digest_by_node = {p.node_id: p.payload_digest for p in payloads}
    node_ids = {node.node_id for node in graph.nodes}
    for unit in plan.units:
        if unit.placeholder_values:
            assert unit.sources, unit.unit_id
        for source in unit.sources:
            assert source.node_id in node_ids
            assert source.payload_digest == payload_digest_by_node[source.node_id]


# ---------------------------------------------------------------------------
# Non-authority: family plans, resolver, production, AG2
# ---------------------------------------------------------------------------


def test_family_plans_cannot_register_or_resolve_independently() -> None:
    plan = _plan()
    resolver = SemanticReferenceResolver()
    resolver.register_compilation_plan(plan)
    ref = CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )
    resolved = resolver.resolve(ref, requesting_scope=plan.scope)
    assert isinstance(resolved, CompilationPlan)
    # Only the aggregate has a document type; nothing names a family plan.
    assert not any("family" in member.value for member in RefDocumentType)
    with pytest.raises(ReferenceResolutionError, match="content-bearing"):
        resolver.register_opaque_subject(
            kind=RefDocumentType.COMPILATION_PLAN,
            subject_id="freestanding-unit",
            version=1,
            digest="a" * 64,
            scope=plan.scope,
        )
    # Duplicate registration of the aggregate stays immutable.
    with pytest.raises(ReferenceResolutionError, match="immutable"):
        resolver.register_compilation_plan(plan)


def test_forged_plan_cannot_register() -> None:
    plan = _plan()
    forged = plan.model_copy(update={"plan_digest": "e" * 64})
    resolver = SemanticReferenceResolver()
    with pytest.raises(ReferenceResolutionError, match="cold validation"):
        resolver.register_compilation_plan(forged)


def test_no_production_imports_no_advertisement_no_ag2() -> None:
    assert advertised_semantic_compiler_capabilities() == ()
    source = (ROOT / "mozaiksai/core/semantics/compilation_plan.py").read_text(
        encoding="utf-8"
    )
    assert "import ag2" not in source and "from ag2" not in source
    # The registry object still arrives as a parameter; importing its closed
    # ValidatorIdentifier taxonomy does not create a second registry.
    assert "build_app_layout_registry" not in source

    offenders: list[str] = []
    excluded = {
        Path("mozaiksai/core/semantics/compilation_plan.py"),
        Path("mozaiksai/core/semantics/decl_bytes.py"),
            Path("mozaiksai/core/semantics/app_config_materialization.py"),
            Path("mozaiksai/core/semantics/workflow_interface_materialization.py"),
        Path("mozaiksai/core/semantics/resolver.py"),
        Path("mozaiksai/core/semantics/refs.py"),
        # Slice 4C offline materializer: consumes the plan inside the
        # semantics layer only; its own proof suite asserts it has no
        # production, AG2, or ambient-capability imports.
        Path("mozaiksai/core/semantics/materialization.py"),
        # Slice 5B offline composition consumes the aggregate plan only
        # after assignment/materialization output has been validated.
            Path("mozaiksai/core/semantics/composition_ledger.py"),
            # Canonical plan-authority contract: re-derives candidate plans
            # through the one derivation function; offline-only, and this
            # same scan proves no production module imports it.
            Path("mozaiksai/core/semantics/plan_authority.py"),
            # Slice 5C offline immutable revision closure and persistence owner.
            Path("mozaiksai/core/semantics/artifact_revision.py"),
            Path("mozaiksai/core/artifacts/revision_store.py"),
            Path("mozaiksai/core/workflow/plan_assignment_compiler.py"),
    }
    for root in (ROOT / "mozaiksai", ROOT / "factory_app"):
        for path in root.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if relative in excluded:
                continue
            if "compilation_plan" in path.read_text(encoding="utf-8"):
                offenders.append(relative.as_posix())
    assert offenders == []


def test_derived_plan_families_cover_produced_plan_owned_paths() -> None:
    """Bounded derived-vs-produced bridge: every artifact family the recorded
    agent-produced plan writes into is disposed or explicitly gapped by the
    derived plan's family universe (family-level coverage; instance-level
    equivalence is cutover work)."""
    import json

    fixture = json.loads(
        (ROOT / "tests/fixtures/appplan_persistent_projects_output.json").read_text(
            encoding="utf-8"
        )
    )
    produced = fixture.get("app_build_plan") or fixture.get("AppBuildPlan") or {}
    owned_paths = [
        path
        for task in (produced.get("build_tasks") or [])
        for path in (task.get("owned_paths") or [])
    ]
    assert owned_paths, "fixture must carry produced owned paths"

    registry = _registry()
    plan = _plan()
    disposed_kinds = {unit.family_kind for unit in plan.units}
    gapped_kinds = {gap.family_kind for gap in plan.gaps}
    uncovered: list[str] = []
    classified = 0
    for path in owned_paths:
        match = None
        for scope in ("app_bundle_root", "workspace_root"):
            try:
                match = registry.match_path(path, scope)
                break
            except (ValueError, KeyError):
                continue
        if match is None:
            continue  # unclassifiable paths are the scanner's concern
        classified += 1
        kind = match.family.kind.value
        if kind not in disposed_kinds and kind not in gapped_kinds:
            uncovered.append(f"{path} -> {kind}")
    assert classified, "fixture paths must classify against the registry"
    assert uncovered == []


def test_plan_models_carry_no_live_runtime_identifiers() -> None:
    """Amended Slice 4B boundary: the plan is provider-neutral and offline.

    The model field universe is closed and none of it names or can carry live
    execution-engine state — no agent/participant identity, transport channel,
    message envelope, connection, inbox/log, delivery/retry/reconnect state,
    live model assignment, or task-checkpoint location. Execution needs exist
    only as deterministic registry declarations for a later binding slice.
    """
    from mozaiksai.core.semantics.compilation_plan import (
        PlanEdgeSource,
        PlanGap,
        PlanOutput,
        PlanSource,
        PlanTaxonomySource,
        RegenerationClosure,
    )

    allowed_fields = {
        CompilationPlan: {
            "schema_version",
            "graph_id",
            "graph_version",
            "scope",
            "graph_digest",
            "scope_selection",
            "registry_schema_version",
            "registry_digest",
            "assignment_contracts_digest",
            "units",
            "gaps",
            "plan_digest",
        },
        FamilyInstancePlan: {
            "unit_id",
            "family_kind",
            "family_identity_digest",
            "disposition",
            "source_scope",
            "placeholder_values",
            "outputs",
            "sources",
            "edge_sources",
            "taxonomy_sources",
            "depends_on_units",
            "materializer",
            "assignment_kind",
            "validator",
            "required_structured_output_ref",
            "base_plan_digest",
        },
        PlanOutput: {"path_scope", "path"},
        PlanSource: {"node_id", "payload_digest"},
        PlanTaxonomySource: {"node_id", "category", "identifier"},
        PlanEdgeSource: {
            "kind",
            "source_node_id",
            "target_node_id",
            "discriminator",
            "edge_identity",
        },
        PlanGap: {"code", "family_kind", "path_template", "subject", "adr_slice"},
        RegenerationClosure: {
            "base_plan_digest",
            "successor_plan_digest",
            "affected",
            "reusable",
            "added",
            "removed",
        },
    }
    forbidden_tokens = (
        "agent_id",
        "passport",
        "channel",
        "envelope",
        "hub",
        "inbox",
        "wal",
        "checkpoint",
        "retry",
        "reconnect",
        "delivery",
        "connection",
        "session",
        "model_assignment",
    )
    for model, fields in allowed_fields.items():
        assert set(model.model_fields) == fields, model.__name__
        for name in fields:
            assert not any(token in name for token in forbidden_tokens), name

    source = (ROOT / "mozaiksai/core/semantics/compilation_plan.py").read_text(
        encoding="utf-8"
    ).lower()
    for token in (
        "passport",
        "envelope",
        "channel",
        "inbox",
        "reconnect",
        "task_checkpoint",
        "agent_id",
        "model_assignment",
        "websocket",
    ):
        assert token not in source, token

    # The canonical payload's key universe is closed too: a derived plan
    # cannot smuggle live identifiers through untyped keys.
    def _keys(value, into):
        if isinstance(value, dict):
            for key, item in value.items():
                into.add(key)
                _keys(item, into)
        elif isinstance(value, list):
            for item in value:
                _keys(item, into)

    seen: set[str] = set()
    _keys(_plan().canonical_payload(), seen)
    expected = (
        allowed_fields[CompilationPlan]
        | allowed_fields[FamilyInstancePlan]
        | allowed_fields[PlanOutput]
        | allowed_fields[PlanSource]
        | allowed_fields[PlanTaxonomySource]
        | allowed_fields[PlanEdgeSource]
        | allowed_fields[PlanGap]
            | {"ref_schema_version", "tenant_id", "workspace_id", "pre_app_scope_id"}
            | {"app_manifest_scope", "module_scope", "workflow_manifest_scope"}
        | {"source_scope", "code", "subject"}
    )
    assert seen <= expected, sorted(seen - expected)


# ---------------------------------------------------------------------------
# Correction round: regressions reproducing the five independent-review attacks
# ---------------------------------------------------------------------------


def _redigest(document: dict) -> dict:
    """Recompute plan_digest the way the model does — the attacker's move."""
    body = {key: value for key, value in document.items() if key != "plan_digest"}
    document["plan_digest"] = canonical_digest(body)
    return document


def test_blocker1_registry_identity_is_recomputed_not_trusted() -> None:
    from mozaiksai.core.semantics.compilation_plan import (
        LayoutRegistrySnapshot,
        snapshot_layout_registry,
    )

    real = _registry()
    graph, payloads = _corpus_graph()

    class _Forged:
        """Different row content, same claimed digest as the real registry."""

        schema_version = real.schema_version
        registry_digest = real.registry_digest  # the retained/forged claim

        def ordered_families(self):
            rows = list(real.ordered_families())

            class _Swapped:
                kind = rows[0].kind
                owner = rows[0].owner
                requirement = rows[0].requirement
                multiplicity = rows[0].multiplicity
                condition = rows[0].condition
                path_scope = rows[0].path_scope
                path_template = "forged/other_output.json"
                materializer = rows[0].materializer
                disposition = rows[0].disposition
                dependency_families = rows[0].dependency_families
                assignment_kinds = rows[0].assignment_kinds
                validator = rows[0].validator
                semantic_input_kinds = rows[0].semantic_input_kinds

            return (_Swapped(), *rows[1:])

    honest = derive_compilation_plan(graph=graph, payloads=payloads, registry=real)
    forged = derive_compilation_plan(graph=graph, payloads=payloads, registry=_Forged())
    # Distinct registry semantics can never hide under one registry identity.
    assert forged.registry_digest != honest.registry_digest
    assert forged.plan_digest != honest.plan_digest

    # A snapshot that retains its digest while a row changes fails closed.
    snapshot = snapshot_layout_registry(real)
    document = snapshot.model_dump(mode="json")
    document["rows"][0]["path_template"] = "forged/other_output.json"
    with pytest.raises(ValidationError, match="snapshot_digest does not match"):
        LayoutRegistrySnapshot.model_validate(document)

    # Rows outside the closed domains are rejected, not consumed.
    class _BadRow:
        kind = "Bad Kind!"
        owner = "app_workspace"
        requirement = "conditional"
        multiplicity = "single"
        condition = "always"
        path_scope = "app_bundle_root"
        path_template = "x/y.json"
        materializer = "app_generator"
        disposition = "render"
        dependency_families = ()
        assignment_kinds = ()
        validator = "none"
        semantic_input_kinds = ()

    class _BadRegistry:
        schema_version = real.schema_version

        def ordered_families(self):
            return (_BadRow(),)

    with pytest.raises(CompilationPlanError, match="closed snapshot domains"):
        derive_compilation_plan(graph=graph, payloads=payloads, registry=_BadRegistry())

    # Internally inconsistent registries (dangling dependency) are rejected.
    class _DanglingRow(_BadRow):
        kind = "lonely_family"
        dependency_families = ("missing_family",)

    class _DanglingRegistry:
        schema_version = real.schema_version

        def ordered_families(self):
            return (_DanglingRow(),)

    with pytest.raises(CompilationPlanError, match="internally inconsistent"):
        derive_compilation_plan(
            graph=graph, payloads=payloads, registry=_DanglingRegistry()
        )


def test_blocker2_multi_placeholder_rows_gap_instead_of_leaking_braces() -> None:
    import re as _re

    from mozaiksai.core.semantics.compilation_plan import (
        PlanOutput,
        snapshot_layout_registry,
    )

    plan = _plan()
    # The built-in registry carries real multi-placeholder rows (for example
    # modules/{module_id}/ui/admin/{page_id}.jsx): every such row must be an
    # explicit typed gap and no validated output may carry a brace.
    snapshot = snapshot_layout_registry(_registry())
    multi_rows = [
        row
        for row in snapshot.rows
        if len(set(_re.findall(r"\{([a-z][a-z0-9_]*)\}", row.path_template))) > 1
        and row.requirement != "prohibited"  # prohibited rows dispose as units
    ]
    assert multi_rows, "registry must contain multi-placeholder rows for this proof"
    gapped = {(gap.family_kind, gap.path_template) for gap in plan.gaps}
    disposed = {unit.family_identity_digest for unit in plan.units}
    for row in multi_rows:
        assert (
            (row.kind, row.path_template) in gapped or row.row_digest in disposed
        ), row.path_template
    for unit in plan.units:
        for output in unit.outputs:
            assert "{" not in output.path and "}" not in output.path

    # A unit output can never carry an unresolved placeholder at all.
    with pytest.raises(ValidationError, match="unresolved placeholder"):
        PlanOutput(path_scope="app_bundle_root", path="modules/reports/ui/{page_id}.jsx")

    # Deterministic gap emission independent of payload input ordering.
    graph, payloads = _corpus_graph()
    again = derive_compilation_plan(
        graph=graph, payloads=list(reversed(payloads)), registry=_registry()
    )
    assert [g.model_dump(mode="json") for g in again.gaps] == [
        g.model_dump(mode="json") for g in plan.gaps
    ]


def test_blocker3_cross_instance_physical_ownership_conflicts_fail() -> None:
    from mozaiksai.core.runtime.app.layout_registry import PathScope
    from mozaiksai.core.semantics.compilation_plan import CompilationScopeSelection

    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_registry(),
        scope_selection=CompilationScopeSelection(module_scope=PathScope.MODULE_RELATIVE),
    )

    # THE review attack: a page unit and a module unit both claim
    # ui/pages/home.yaml in the app bundle root, with the document re-digested
    # so the digest check cannot save us — physical ownership must.
    document = _document(plan)
    render_units = [
        u for u in document["units"] if u["outputs"] and u["placeholder_values"]
    ]
    page_like = render_units[0]
    module_like = next(
        u
        for u in render_units
        if u["placeholder_values"] != page_like["placeholder_values"]
    )
    for victim in (page_like, module_like):
        victim["outputs"] = [
            {"path_scope": "app_bundle_root", "path": "ui/pages/home.yaml"}
        ]
    _redigest(document)
    with pytest.raises(ValidationError, match="duplicate output ownership"):
        CompilationPlan.model_validate(document)

    # Duplicate unit identities cannot slip through a re-digested document.
    document = _document(plan)
    clone = copy.deepcopy(document["units"][0])
    document["units"].append(clone)
    _redigest(document)
    with pytest.raises(ValidationError, match="duplicate unit identities"):
        CompilationPlan.model_validate(document)

    # Case-fold and prefix collisions across DIFFERENT units in one global root.
    for second_path in ("UI/Pages/Home.YAML", "ui/pages/home.yaml/extra.txt"):
        document = _document(plan)
        units = [u for u in document["units"] if u["outputs"]]
        units[0]["outputs"] = [
            {"path_scope": "app_bundle_root", "path": "ui/pages/home.yaml"}
        ]
        units[1]["outputs"] = [{"path_scope": "app_bundle_root", "path": second_path}]
        _redigest(document)
        with pytest.raises(
            ValidationError, match="output collision|duplicate output ownership"
        ):
            CompilationPlan.model_validate(document)

def test_blocker4_closed_domains_reject_runtime_identifier_smuggling() -> None:
    plan = _plan()
    hostile = "resume AG2 channel_id=chan-live envelope_id=env-live"

    # Through a gap subject (the review's attack surface, now grammar-closed).
    document = _document(plan)
    document["gaps"][0]["subject"] = hostile
    _redigest(document)
    with pytest.raises(ValidationError, match="subject must be a lowercase identifier"):
        CompilationPlan.model_validate(document)

    # Through a unit materializer.
    document = _document(plan)
    document["units"][0]["materializer"] = hostile
    _redigest(document)
    with pytest.raises(
        ValidationError, match="materializer must be a lowercase identifier"
    ):
        CompilationPlan.model_validate(document)

    # Through a placeholder value.
    document = _document(plan)
    with_values = next(u for u in document["units"] if u["placeholder_values"])
    with_values["placeholder_values"] = [["module_id", hostile]]
    _redigest(document)
    with pytest.raises(ValidationError, match="outside the closed domain"):
        CompilationPlan.model_validate(document)

    # Registration cold-validates: a forged in-memory object whose digest was
    # re-computed over hostile content never registers.
    resolver = SemanticReferenceResolver()
    document = _document(plan)
    document["gaps"][0]["subject"] = hostile
    _redigest(document)
    forged = CompilationPlan.model_construct(
        **{
            **{name: getattr(plan, name) for name in CompilationPlan.model_fields},
            "gaps": tuple(
                type(plan.gaps[0]).model_construct(**{**gap_fields, "subject": hostile})
                if index == 0
                else plan.gaps[index]
                for index, gap_fields in enumerate(
                    {name: getattr(gap, name) for name in type(gap).model_fields}
                    for gap in plan.gaps
                )
            ),
            "plan_digest": document["plan_digest"],
        }
    )
    with pytest.raises(ReferenceResolutionError, match="cold validation"):
        resolver.register_compilation_plan(forged)


def test_blocker5_reverse_dependency_and_graph_wide_propagation() -> None:
    from mozaiksai.core.semantics.compilation_plan import PlanSourceScope

    def _mini_unit(
        unit_id: str,
        *,
        source_digest: str | None,
        deps: tuple[str, ...] = (),
        graph_wide: bool = False,
        path: str,
    ) -> dict:
        return {
            "unit_id": unit_id,
            "family_kind": "app_config",
            "family_identity_digest": canonical_digest(unit_id),
            "disposition": "render",
            "source_scope": "graph_wide" if graph_wide else "declared",
            "placeholder_values": [],
            "outputs": [{"path_scope": "app_bundle_root", "path": path}],
            "sources": (
                []
                if graph_wide or source_digest is None
                else [{"node_id": "mozaiks.node.x", "payload_digest": source_digest}]
            ),
            "edge_sources": [],
            "depends_on_units": list(deps),
            "materializer": "app_generator",
            "validator": "generated_app_validator",
            "assignment_kind": None,
            "required_structured_output_ref": None,
            "base_plan_digest": None,
        }

    def _mini_plan(graph_digest: str, units: list[dict]) -> CompilationPlan:
        document = {
            "schema_version": "mozaiks.compilation_plan.v1",
            "graph_id": "mini",
            "graph_version": 1,
                "scope": _SCOPE.model_dump(mode="json"),
                "scope_selection": {
                    "app_manifest_scope": "app_bundle_root",
                    "module_scope": "app_bundle_root",
                    "workflow_manifest_scope": "workflow_relative",
                },
            "graph_digest": graph_digest,
            "registry_schema_version": "mozaiks.app_layout.v2",
            "registry_digest": canonical_digest("registry"),
            "assignment_contracts_digest": canonical_digest("no-assignments"),
            "units": units,
            "gaps": [],
        }
        return CompilationPlan.model_validate(_redigest(document))

    d1, d2 = canonical_digest("payload-1"), canonical_digest("payload-2")
    g1, g2 = canonical_digest("graph-1"), canonical_digest("graph-2")

    def _fleet(graph_digest: str, a_source: str, last: dict) -> CompilationPlan:
        return _mini_plan(
            graph_digest,
            [
                _mini_unit("a/1", source_digest=a_source, path="a.json"),
                _mini_unit(
                    "b/1",
                    source_digest=canonical_digest("b"),
                    deps=("a/1",),
                    path="b.json",
                ),
                _mini_unit(
                    "c/1",
                    source_digest=canonical_digest("c"),
                    deps=("b/1",),
                    path="c.json",
                ),
                _mini_unit("g/1", source_digest=None, graph_wide=True, path="g.json"),
                _mini_unit("z/1", source_digest=canonical_digest("z"), path="z.json"),
                last,
            ],
        )

    base = _fleet(
        g1, d1, _mini_unit("gone/1", source_digest=canonical_digest("gone"), path="gone.json")
    )
    successor = _fleet(
        g2, d2, _mini_unit("new/1", source_digest=canonical_digest("new"), path="new.json")
    )
    closure = plan_regeneration_closure(base, successor)
    # Direct change, transitive reverse-dependency chain, and graph-wide unit
    # are all affected; the independent unit is provably reusable.
    assert set(closure.affected) == {"a/1", "b/1", "c/1", "g/1"}
    assert closure.reusable == ("z/1",)
    assert closure.added == ("new/1",)
    assert closure.removed == ("gone/1",)

    # Graph-only change (no payload change): exactly the graph-wide unit moves.
    successor_graph_only = _fleet(
        g2, d1, _mini_unit("gone/1", source_digest=canonical_digest("gone"), path="gone.json")
    )
    closure2 = plan_regeneration_closure(base, successor_graph_only)
    assert set(closure2.affected) == {"g/1"}
    assert set(closure2.reusable) == {"a/1", "b/1", "c/1", "z/1", "gone/1"}

    # Real derivation cross-check: a graph-only change (extra edge, unchanged
    # payloads) affects graph-wide units and leaves independent declared
    # instance units reusable.
    from mozaiksai.core.semantics.graph import (
        SemanticEdge,
        SemanticEdgeKind,
        build_semantic_graph_v2,
    )

    graph, payloads = _corpus_graph()
    surface = next(n for n in graph.nodes if n.kind.value == "surface")
    module = next(n for n in graph.nodes if n.kind.value == "module")
    extra_edge = SemanticEdge(
        kind=SemanticEdgeKind.OWNS,
        source_node_id=surface.node_id,
        target_node_id=module.node_id,
    )
    changed_graph = build_semantic_graph_v2(
        graph_id=graph.graph_id,
        version=graph.version,
        scope=graph.scope,
        nodes=graph.nodes,
        edges=(*graph.edges, extra_edge),
        namespace_grants=graph.namespace_grants,
    )
    base_plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    edge_plan = derive_compilation_plan(
        graph=changed_graph, payloads=payloads, registry=_registry()
    )
    closure3 = plan_regeneration_closure(base_plan, edge_plan)
    graph_wide_ids = {
        u.unit_id for u in edge_plan.units if u.source_scope is PlanSourceScope.GRAPH_WIDE
    }
    assert graph_wide_ids <= set(closure3.affected)
    independent_instance_ids = {
        u.unit_id
        for u in edge_plan.units
        if u.source_scope is PlanSourceScope.DECLARED
        and u.placeholder_values
        and not u.depends_on_units
        and u.disposition is PlanDisposition.RENDER
    }
    assert independent_instance_ids
    assert independent_instance_ids & set(closure3.reusable) == independent_instance_ids
