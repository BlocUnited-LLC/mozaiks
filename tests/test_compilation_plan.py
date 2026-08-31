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
_GOLDEN_PLAN_DIGEST = "b99aafc84caa39a867f2bf7eb09a0dbfed51f7b94de4e2219d7a64728e28226a"


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
    assert plan.registry_digest == registry.registry_digest
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
    with_deps = [u for u in document["units"] if u["depends_on_units"]]
    dependent = with_deps[0]
    dependency_id = dependent["depends_on_units"][0]
    dependency = next(u for u in document["units"] if u["unit_id"] == dependency_id)
    dependency["depends_on_units"] = [dependent["unit_id"]]
    with pytest.raises(ValidationError, match="dependency cycle|plan_digest"):
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
    plan = _plan()
    registry = _registry()
    disposed = {unit.family_identity_digest for unit in plan.units}
    gapped = {(gap.family_kind, gap.path_template) for gap in plan.gaps}
    for family in registry.ordered_families():
        digest = canonical_digest(family.identity_payload)
        covered = digest in disposed or (
            (family.kind.value, family.path_template) in gapped
        )
        assert covered, (family.kind.value, family.path_template)


def test_binding_conditions_are_typed_gaps_never_guesses() -> None:
    from mozaiksai.core.semantics.compilation_plan import _BINDING_CONDITIONS

    plan = _plan()
    reasons = "\n".join(gap.reason for gap in plan.gaps)
    registry_conditions = {
        family.condition.value for family in _registry().ordered_families()
    }
    expected = sorted(_BINDING_CONDITIONS & registry_conditions)
    assert expected, "registry must carry binding-owned conditions"
    for condition in expected:
        assert condition in reasons, condition
    assert all(gap.adr_slice in (4, 5, 6, 7) for gap in plan.gaps)


def test_unknown_layout_family_condition_becomes_a_typed_gap() -> None:
    registry = _registry()

    class _NovelCondition:
        value = "when_something_new"

    class _NovelFamily:
        kind = type("K", (), {"value": "novel_family"})()
        requirement = type("R", (), {"value": "conditional"})()
        condition = _NovelCondition()
        path_scope = type("S", (), {"value": "app_bundle_root"})()
        path_template = "novel/output.yaml"
        materializer = type("M", (), {"value": "unknown"})()
        owner = type("O", (), {"value": "app_workspace"})()
        dependency_families = ()
        identity_payload = {"kind": "novel_family", "path_template": "novel/output.yaml"}

    class _NovelRegistry:
        schema_version = registry.schema_version
        registry_digest = registry.registry_digest

        def ordered_families(self):
            return (*registry.ordered_families(), _NovelFamily())

    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=_NovelRegistry())
    assert any(
        gap.family_kind == "novel_family" and "no graph-v2 derivation rule" in gap.reason
        for gap in plan.gaps
    )


def test_renderer_resolution_is_an_explicit_gap_and_units_cannot_claim_one() -> None:
    plan = _plan()
    assert any("renderer resolution" in gap.reason for gap in plan.gaps)
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
    base = _plan()
    changed = _plan(home_title="Home!")
    assert base.graph_digest != changed.graph_digest
    assert base.plan_digest != changed.plan_digest
    closure = plan_regeneration_closure(base, changed)
    # The page payload changed: exactly the units sourcing that node flip to
    # affected; everything else stays reusable. Nothing is omitted.
    page_units = {
        unit.unit_id
        for unit in changed.units
        if any(source.node_id == "mozaiks.page.home" for source in unit.sources)
    }
    assert page_units
    assert set(closure.affected) == page_units
    assert not closure.added and not closure.removed
    assert set(closure.affected) | set(closure.reusable) == {
        unit.unit_id for unit in changed.units
    }


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
            materializer="none",
        )
    with pytest.raises(ValidationError, match="reuse_from_base"):
        FamilyInstancePlan(
            unit_id="x/y",
            family_kind="app_manifest",
            family_identity_digest=unit.family_identity_digest,
            disposition=PlanDisposition.RENDER,
            materializer="none",
            base_plan_digest="a" * 64,
        )


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
    # The semantics layer must not import the runtime registry module; the
    # registry arrives as a parameter.
    assert "runtime.app.layout_registry" not in source

    offenders: list[str] = []
    excluded = {
        Path("mozaiksai/core/semantics/compilation_plan.py"),
        Path("mozaiksai/core/semantics/resolver.py"),
        Path("mozaiksai/core/semantics/refs.py"),
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
        PlanGap,
        PlanOutput,
        PlanSource,
        RegenerationClosure,
    )

    allowed_fields = {
        CompilationPlan: {
            "schema_version",
            "graph_id",
            "graph_version",
            "scope",
            "graph_digest",
            "registry_schema_version",
            "registry_digest",
            "units",
            "gaps",
            "plan_digest",
        },
        FamilyInstancePlan: {
            "unit_id",
            "family_kind",
            "family_identity_digest",
            "disposition",
            "placeholder_values",
            "outputs",
            "sources",
            "depends_on_units",
            "materializer",
            "base_plan_digest",
        },
        PlanOutput: {"path_scope", "path"},
        PlanSource: {"node_id", "payload_digest"},
        PlanGap: {"family_kind", "path_template", "reason", "adr_slice"},
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
        "agent",
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
        | allowed_fields[PlanGap]
        | {"ref_schema_version", "tenant_id", "workspace_id", "pre_app_scope_id"}
    )
    assert seen <= expected, sorted(seen - expected)
