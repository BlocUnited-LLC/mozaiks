"""ADR 0007 Slice 5D-0B2A proof gate: deterministic application-family bytes.

Extends the accepted representative corpus so the reporting-style application
explicitly SELECTS auth, one integration, and its workflow, then proves the
closed application-configuration family set renders canonical bytes:

    app.json, ui/route_manifest.json,
    config/integrations.yaml, security/secrets.yaml

through the single accepted ``deterministic_app_config_renderer@1`` authority.
``config/ai.json`` (``app_config``) is deliberately deferred: per-workflow
``workflow_startup_mode`` is not application-level chat launch authority, and
the semantic model has no application-level AI-launch facts yet; the family
stays a typed gap
(``test_app_config_stays_a_typed_gap_and_never_blocks_the_four_families``).

Families whose facts still lack a typed semantic home remain typed gaps with
their exact prerequisites recorded here (see
``test_blocked_families_remain_typed_with_exact_prerequisites``); nothing in
this slice claims a complete or runnable application.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app.layout_registry import (
    MaterializerIdentifier,
    build_app_layout_registry,
)
from mozaiksai.core.semantics.app_config_materialization import (
    APP_CONFIG_FAMILIES,
    APP_CONFIG_RENDERER_IMPLEMENTATION_ID,
    APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION,
    AppConfigMaterializationError,
    render_app_config_unit,
)
from mozaiksai.core.semantics.binding import (
    RendererSelection,
    build_implementation_binding,
)
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    PlanDisposition,
    derive_compilation_plan,
)
from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticNodeV2,
    build_semantic_graph_v2,
)
from mozaiksai.core.semantics.materialization import (
    MaterializationError,
    project_app_family_render_input,
)
from mozaiksai.core.semantics.materialization import (
    materialize_plan as _materialize_plan,
)
from mozaiksai.core.semantics.materialization import (
    rematerialize_plan as _rematerialize_plan,
)
from mozaiksai.core.semantics.payloads import (
    ApplicationPayload,
    AuthPayload,
    OptionalFamilyKind,
    OptionalFamilySelection,
    OptionalFamilySelectionStatus,
    PagePayload,
    SectionContentEntry,
    SectionEntryKind,
    SectionPayload,
    SemanticNodeKind,
    WorkflowPayload,
    WorkflowStartupMode,
    build_semantic_payload,
    semantic_payload_ref,
)
from mozaiksai.core.semantics.plan_authority import (
    build_compilation_plan_authority_inputs,
)
from mozaiksai.core.semantics.refs import SemanticGraphRef
from tests.test_semantic_payload_graph_v2 import _SCOPE, _corpus_payloads

ROOT = Path(__file__).resolve().parents[1]

_SELECTED = {
    OptionalFamilyKind.AUTH,
    OptionalFamilyKind.INTEGRATIONS,
    OptionalFamilyKind.WORKFLOWS,
}

_RENDERED_PATHS = {
    "app.json",
    "ui/route_manifest.json",
    "config/integrations.yaml",
    "security/secrets.yaml",
}


def _configs() -> dict:
    return {
        name: yaml.safe_load(
            (ROOT / "factory_app" / "workflows" / name / "structured_outputs.yaml").read_text(
                encoding="utf-8"
            )
        )
        for name in ("AppGenerator", "AgentGenerator")
    }


CONFIGS = _configs()


def _extended_fixture(
    *,
    home_title: str = "Home",
    default_route: str = "/home",
    auth_selected: bool = True,
    auth_roles: tuple[str, ...] | None = None,
    integrations_selected: bool = True,
    workflows_selected: bool = True,
    drop_workflow_payload: bool = False,
    workflow_startup_mode: WorkflowStartupMode | None = WorkflowStartupMode.EVENT_DRIVEN,
    extra_workflow_startup_mode: WorkflowStartupMode | None = None,
    custom_routes_selected: bool = False,
    integration_secret_name: str = "EMAIL_API_KEY",
):
    """Corpus with explicit product-meaningful optional-family selections."""
    payloads = dict(_corpus_payloads(scope=_SCOPE, home_title=home_title))

    def _family_selected(family: OptionalFamilyKind) -> bool:
        if family is OptionalFamilyKind.CUSTOM_ROUTES:
            return custom_routes_selected
        if family not in _SELECTED:
            return False
        if family is OptionalFamilyKind.AUTH:
            return auth_selected
        if family is OptionalFamilyKind.INTEGRATIONS:
            return integrations_selected
        if family is OptionalFamilyKind.WORKFLOWS:
            return workflows_selected
        return True

    application = payloads["application"]
    selections = tuple(
        OptionalFamilySelection(
            family=family,
            status=(
                OptionalFamilySelectionStatus.SELECTED
                if _family_selected(family)
                else OptionalFamilySelectionStatus.ABSENT_BY_DECLARATION
            ),
        )
        for family in OptionalFamilyKind
    )
    payloads["application"] = build_semantic_payload(
        ApplicationPayload,
        node_id=application.node_id,
        payload_version=application.payload_version,
        scope=_SCOPE,
        application_id=application.application_id,
        display_name=application.display_name,
        description=application.description,
        tagline=application.tagline,
        value_proposition=application.value_proposition,
        version=application.version,
        default_route=default_route,
        optional_families=selections,
    )
    if not integrations_selected:
        payloads.pop("integration", None)
    if not auth_selected:
        payloads.pop("auth", None)
    if drop_workflow_payload:
        payloads.pop(SemanticNodeKind.WORKFLOW, None)
    elif workflow_startup_mode is not WorkflowStartupMode.EVENT_DRIVEN:
        workflow = payloads[SemanticNodeKind.WORKFLOW]
        payloads[SemanticNodeKind.WORKFLOW] = build_semantic_payload(
            WorkflowPayload,
            node_id=workflow.node_id,
            payload_version=workflow.payload_version,
            scope=_SCOPE,
            workflow_id=workflow.workflow_id,
            description=workflow.description,
            startup_mode=workflow_startup_mode,
            topology=workflow.topology,
        )
    if extra_workflow_startup_mode is not None:
        payloads["workflow_extra"] = build_semantic_payload(
            WorkflowPayload,
            node_id="mozaiks.workflow.assistant",
            payload_version=1,
            scope=_SCOPE,
            workflow_id="assistant",
            description="Interactive assistant workflow",
            startup_mode=extra_workflow_startup_mode,
            topology=None,
        )
    else:
        auth = payloads["auth"]
        payloads["auth"] = build_semantic_payload(
            AuthPayload,
            node_id=auth.node_id,
            payload_version=auth.payload_version,
            scope=_SCOPE,
            auth_required=auth.auth_required,
            strategy=auth.strategy,
            roles=auth.roles if auth_roles is None else auth_roles,
        )
    if integration_secret_name != "EMAIL_API_KEY":
        integration = payloads["integration"]
        from mozaiksai.core.semantics.payloads import IntegrationPayload

        payloads["integration"] = build_semantic_payload(
            IntegrationPayload,
            node_id=integration.node_id,
            payload_version=integration.payload_version,
            scope=_SCOPE,
            integration_id=integration.integration_id,
            integration_kind=integration.integration_kind,
            purpose=integration.purpose,
            required_at=integration.required_at,
            optional=integration.optional,
            config_requirements=tuple(
                type(req)(
                    name=(
                        integration_secret_name
                        if req.value_kind.value == "secret"
                        else req.name
                    ),
                    value_kind=req.value_kind,
                    required=req.required,
                )
                for req in integration.config_requirements
            ),
        )

    # The kind-keyed corpus carries only the hero section payload, yet the
    # home page declares a second section; close it so the page family's
    # renderer inputs are byte-complete on this fixture.
    payloads["section_pricing"] = build_semantic_payload(
        SectionPayload,
        node_id="mozaiks.section.pricing",
        payload_version=1,
        scope=_SCOPE,
        section_id="pricing",
        title="Pricing",
        intent="Plan comparison",
        declarative={
            "id": "pricing",
            "primitive": "Panel",
            "config": {"title": "Plans"},
        },
        entries=(
            SectionContentEntry(
                position=0, entry_kind=SectionEntryKind.TEXT, text="Plans"
            ),
        ),
    )

    ordered = list(payloads.values())
    nodes = [
        SemanticNodeV2(
            node_id=p.node_id,
            kind=p.payload_kind,
            payload_ref=semantic_payload_ref(p),
        )
        for p in ordered
    ]
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.RENDERS,
            source_node_id="mozaiks.page.home",
            target_node_id="mozaiks.section.hero",
        ),
        SemanticEdge(
            kind=SemanticEdgeKind.RENDERS,
            source_node_id="mozaiks.page.home",
            target_node_id="mozaiks.section.pricing",
        ),
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id="mozaiks.action.create_report",
            target_node_id="mozaiks.event.report_created",
        ),
        SemanticEdge(
            kind=SemanticEdgeKind.OWNS,
            source_node_id="mozaiks.module.reports",
            target_node_id="mozaiks.artifact.report_hook",
        ),
    ]
    graph = build_semantic_graph_v2(
        graph_id="corpus-app",
        version=1,
        scope=_SCOPE,
        nodes=nodes,
        edges=edges,
    )
    return graph, ordered


def _plan(graph, payloads, *, with_configs: bool = True):
    """Derive the aggregate plan.

    Bundle materialization uses ``with_configs=False`` so agent-authored
    families remain typed ``output_contract_unresolved`` gaps: the 4C
    materializer intentionally rejects AGENT_AUTHOR units loudly, and B2A
    does not implement their execution. Gap-closure assertions use the
    config-full derivation.
    """
    return derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=build_app_layout_registry(()),
        structured_output_configs=CONFIGS if with_configs else None,
    )


def _authority_inputs(graph, payloads):
    return build_compilation_plan_authority_inputs(
        graph=graph,
        payloads=payloads,
        registry=build_app_layout_registry(()),
    )


def materialize_plan(**kwargs):
    kwargs["authority_inputs"] = _authority_inputs(
        kwargs["graph"], kwargs["payloads"]
    )
    return _materialize_plan(**kwargs)


def _binding(graph):
    return build_implementation_binding(
        binding_id="b2a_binding",
        version=1,
        scope=_SCOPE,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=graph.scope,
        ),
        renderer_selections=(
            RendererSelection(
                materializer_id=MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR,
                implementation_id="deterministic_page_schema_renderer",
                implementation_version="1",
                artifact_families=("app_ui_page_schema",),
            ),
            RendererSelection(
                materializer_id=MaterializerIdentifier.APP_CONFIG_EXECUTOR,
                implementation_id=APP_CONFIG_RENDERER_IMPLEMENTATION_ID,
                implementation_version=APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION,
                artifact_families=tuple(sorted(APP_CONFIG_FAMILIES)),
            ),
        ),
    )


def _materialize(graph, payloads, plan):
    return materialize_plan(
        plan=plan,
        graph=graph,
        payloads=payloads,
        binding=_binding(graph),
        layout_registry=build_app_layout_registry(()),
    )


# ---------------------------------------------------------------------------
# Entry gate: the accepted B1 shape is reproduced, not silently re-pinned.
# ---------------------------------------------------------------------------


def test_accepted_b1_registry_census_is_unchanged() -> None:
    registry = build_app_layout_registry(())
    # The new interface twins have their own layout proof; every B1 row stays.
    census = Counter(
        f.disposition.value for f in registry.families
        if f.kind.value != "workflow_module_interface"
    )
    assert dict(census) == {
        "render": 79,
        "agent_author": 19,
        "external_handoff": 11,
        "input_only": 1,
        "inapplicable": 13,
    }
    assert sum(census.values()) == 123


# ---------------------------------------------------------------------------
# Selected-family closure on the extended fixture
# ---------------------------------------------------------------------------


def _render_units(plan):
    return {
        unit.outputs[0].path: unit
        for unit in plan.units
        if unit.disposition is PlanDisposition.RENDER
        and unit.family_kind in APP_CONFIG_FAMILIES
    }


def test_extended_fixture_closes_selected_application_families() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads)
    units = _render_units(plan)
    assert set(units) == _RENDERED_PATHS
    for path, unit in units.items():
        assert unit.sources, path
    blocked = {
        gap.path_template
        for gap in plan.gaps
        if gap.family_kind in APP_CONFIG_FAMILIES
    }
    assert not blocked & _RENDERED_PATHS


def test_each_rendered_path_is_owned_by_exactly_one_unit() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads)
    owners: dict[str, list[str]] = {}
    for unit in plan.units:
        for output in unit.outputs:
            owners.setdefault(output.path, []).append(unit.unit_id)
    for path in _RENDERED_PATHS:
        assert len(owners.get(path, [])) == 1, (path, owners.get(path))
    collisions = {path: ids for path, ids in owners.items() if len(ids) > 1}
    assert collisions == {}


def test_selected_family_gap_count_is_zero_for_b2a_set() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads)
    b2a_gaps = [
        gap
        for gap in plan.gaps
        if gap.family_kind in APP_CONFIG_FAMILIES
        and gap.path_template in _RENDERED_PATHS
    ]
    assert b2a_gaps == []


# ---------------------------------------------------------------------------
# Canonical bytes + loader equivalence
# ---------------------------------------------------------------------------


def _bundle_files():
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    bundle = _materialize(graph, payloads, plan)
    return bundle.files()


def test_app_manifest_bytes_and_loader_semantics() -> None:
    files = _bundle_files()
    data = files["app.json"]
    assert b"\r" not in data
    assert data.endswith(b"\n")
    document = json.loads(data.decode("utf-8"))
    assert document["appId"] == "corpus-app"
    assert document["authRequired"] is True
    assert document["startup"] == {"landing_spot": "/home"}
    assert set(document) <= {
        "appId",
        "appName",
        "version",
        "description",
        "authRequired",
        "startup",
    }
    # Loader name mapping (mozaiksai.core.runtime.app.loader raw parse).
    assert document.get("appName")
    # No invented facts: version equals authored payload version.
    graph, payloads = _extended_fixture()
    app = next(p for p in payloads if isinstance(p, ApplicationPayload))
    assert document["version"] == app.version


def test_route_manifest_bytes_reference_declared_pages_only() -> None:
    files = _bundle_files()
    document = json.loads(files["ui/route_manifest.json"].decode("utf-8"))
    graph, payloads = _extended_fixture()
    pages = {p.route: p for p in payloads if isinstance(p, PagePayload)}
    assert [entry["path"] for entry in document["pages"]] == sorted(pages)
    for entry in document["pages"]:
        assert entry["component"] == "SchemaPage"
        assert entry["schema"] == pages[entry["path"]].page_id
        assert entry["label"] == pages[entry["path"]].title


def test_integration_and_secret_bytes() -> None:
    files = _bundle_files()
    assert "config/ai.json" not in files

    integrations = yaml.safe_load(files["config/integrations.yaml"].decode("utf-8"))
    assert list(integrations) == ["integrations"]
    entries = integrations["integrations"]
    assert [e["service"] for e in entries] == sorted(e["service"] for e in entries)
    for entry in entries:
        assert set(entry) <= {
            "service",
            "kind",
            "purpose",
            "required_at",
            "optional",
            "required_fields",
        }
        for requirement in entry["required_fields"]:
            assert set(requirement) == {"name", "type", "required"}
            assert requirement["type"] in {"text", "url", "secret"}

    secrets = yaml.safe_load(files["security/secrets.yaml"].decode("utf-8"))
    assert set(secrets) == {"version", "secrets"}
    assert secrets["version"] == 1
    secret_names = {
        requirement["name"]
        for entry in entries
        for requirement in entry["required_fields"]
        if requirement["type"] == "secret"
    }
    assert set(secrets["secrets"]) == secret_names
    # names only — nothing that looks like a value or token
    blob = files["security/secrets.yaml"].decode("utf-8").lower()
    for forbidden in ("sk_", "token:", "-----begin", "password:"):
        assert forbidden not in blob


def test_scanner_accepts_rendered_integration_contract() -> None:
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _load_integration_requirements,
    )

    files = _bundle_files()
    requirements, errors = _load_integration_requirements(
        {"config/integrations.yaml": files["config/integrations.yaml"].decode("utf-8")}
    )
    assert errors == []
    assert requirements
    assert all(isinstance(item, dict) for item in requirements)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_repeated_and_shuffled_materialization_is_byte_identical() -> None:
    first = _bundle_files()
    graph, payloads = _extended_fixture()
    plan = _plan(graph, list(reversed(payloads)), with_configs=False)
    bundle = materialize_plan(
        plan=plan,
        graph=graph,
        payloads=tuple(reversed(payloads)),
        binding=_binding(graph),
        layout_registry=build_app_layout_registry(()),
    )
    assert first == bundle.files()


def _cross_process_digest() -> str:
    import hashlib

    files = _bundle_files()
    joined = b"\x00".join(
        path.encode("utf-8") + b"\x01" + files[path] for path in sorted(files)
    )
    return hashlib.sha256(joined).hexdigest()


def test_cross_process_determinism() -> None:
    local = _cross_process_digest()
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.test_app_family_materialization_b2a import "
            "_cross_process_digest; print(_cross_process_digest())",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    assert completed.stdout.strip() == local


# ---------------------------------------------------------------------------
# Mutation footprints
# ---------------------------------------------------------------------------


def test_default_route_mutation_affects_manifest_and_routes_only() -> None:
    base = _bundle_files()
    graph, payloads = _extended_fixture(default_route="/home")
    # a second declared page is required for a different default route; use the
    # same page set but assert against an equal-route change instead: change
    # the home page title, which must move the route manifest and nothing else.
    graph2, payloads2 = _extended_fixture(home_title="Console")
    plan2 = _plan(graph2, payloads2, with_configs=False)
    changed = materialize_plan(
        plan=plan2,
        graph=graph2,
        payloads=payloads2,
        binding=_binding(graph2),
        layout_registry=build_app_layout_registry(()),
    ).files()
    assert changed["ui/route_manifest.json"] != base["ui/route_manifest.json"]
    for unchanged in ("app.json", "config/integrations.yaml", "security/secrets.yaml"):
        assert changed[unchanged] == base[unchanged], unchanged


def test_integration_secret_mutation_affects_integrations_and_secrets_only() -> None:
    base = _bundle_files()
    graph, payloads = _extended_fixture(integration_secret_name="EMAIL_PROVIDER_KEY")
    plan = _plan(graph, payloads, with_configs=False)
    changed = materialize_plan(
        plan=plan,
        graph=graph,
        payloads=payloads,
        binding=_binding(graph),
        layout_registry=build_app_layout_registry(()),
    ).files()
    assert changed["config/integrations.yaml"] != base["config/integrations.yaml"]
    assert changed["security/secrets.yaml"] != base["security/secrets.yaml"]
    for unchanged in ("app.json", "ui/route_manifest.json"):
        assert changed[unchanged] == base[unchanged], unchanged


# ---------------------------------------------------------------------------
# Ownership, binding, fail-closed behavior
# ---------------------------------------------------------------------------


def test_renderer_rejects_wrong_family_wrong_binding_and_unknown_template() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    manifest_unit = next(
        u
        for u in plan.units
        if u.disposition is PlanDisposition.RENDER
        and u.family_kind == "app_manifest"
    )
    render_input = project_app_family_render_input(
        unit=manifest_unit, payload_by_node=payload_by_node
    )
    page_unit = next(
        u for u in plan.units if u.family_kind == "app_ui_page_schema"
    )
    with pytest.raises(AppConfigMaterializationError, match="not an"):
        render_app_config_unit(unit=page_unit, render_input=render_input)
    # A family-local input from ANOTHER family's unit is rejected even when
    # both units are authorized application-configuration units.
    route_unit = next(
        u
        for u in plan.units
        if u.disposition is PlanDisposition.RENDER
        and u.family_kind == "app_ui_route_manifest"
    )
    with pytest.raises(AppConfigMaterializationError, match="does not match"):
        render_app_config_unit(unit=route_unit, render_input=render_input)

    from mozaiksai.core.semantics.materialization import MaterializationError

    bad_binding = build_implementation_binding(
        binding_id="b2a_bad",
        version=1,
        scope=_SCOPE,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=graph.scope,
        ),
        renderer_selections=(
            RendererSelection(
                materializer_id=MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR,
                implementation_id="deterministic_page_schema_renderer",
                implementation_version="1",
                artifact_families=("app_ui_page_schema",),
            ),
        ),
    )
    with pytest.raises((MaterializationError, AppConfigMaterializationError)):
        materialize_plan(
            plan=plan,
            graph=graph,
            payloads=payloads,
            binding=bad_binding,
            layout_registry=build_app_layout_registry(()),
        )


# ---------------------------------------------------------------------------
# Blocked families stay typed, with exact prerequisites recorded
# ---------------------------------------------------------------------------


def test_blocked_families_remain_typed_with_exact_prerequisites() -> None:
    """B2A does not render families whose facts have no typed semantic home.

    - ``app_subscription_config``: ``default_plan_id`` and assignment-store
      wiring have no typed semantic authority (PlanPayload has no default
      flag; store wiring is runtime configuration). Prerequisite: extend the
      subscription semantics or accept loader defaults before rendering.
    - ``app_data_contract``: collection->module surface ownership is edge
      knowledge, not payload knowledge. Prerequisite: pass typed edge
      identities into renderer inputs or embed the owner in
      ``DataCollectionPayload``.
    - ``config/asset_manifest.json`` (``app_config`` row): assets have
      selection evidence but no typed asset facts.
    - ``config/ai.json`` (``app_config``): per-workflow
      ``workflow_startup_mode`` is not application-level chat launch
      authority. Prerequisite: application-level AI-launch facts
      (chat startup mode, workflow entry point) with typed semantic homes.
    """
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads)
    rendered = set(_render_units(plan))
    assert "config/subscriptions.yaml" not in rendered
    assert "data/contract.json" not in rendered
    assert "config/asset_manifest.json" not in rendered
    assert "config/ai.json" not in rendered
    assert "config/ai.json" in {g.path_template for g in plan.gaps}
    # and they are not silently absent: each is a unit or typed gap elsewhere.
    gap_paths = {g.path_template for g in plan.gaps}
    unit_families = {u.family_kind for u in plan.units}
    for path, family in (
        ("config/subscriptions.yaml", "app_subscription_config"),
        ("data/contract.json", "app_data_contract"),
    ):
        assert path in gap_paths or family in unit_families, path


# ---------------------------------------------------------------------------
# Future-compatibility guards
# ---------------------------------------------------------------------------


def test_mobile_and_evaluation_vocabulary_is_absent_from_renderer_sources() -> None:
    sources = (
        (ROOT / "mozaiksai" / "core" / "semantics" / "app_config_materialization.py")
        .read_text(encoding="utf-8")
        .lower()
        + (ROOT / "mozaiksai" / "core" / "semantics" / "decl_bytes.py")
        .read_text(encoding="utf-8")
        .lower()
    )
    for forbidden in (
        "react-native",
        "expo",
        "swift",
        "kotlin",
        "android",
        "persona",
        "campaign",
        "conversion",
        "revenue",
        "portfolio",
    ):
        assert forbidden not in sources, forbidden


def test_renderer_modules_import_no_clock_env_or_filesystem() -> None:
    import ast

    for module_name in ("app_config_materialization", "decl_bytes"):
        source = (
            ROOT / "mozaiksai" / "core" / "semantics" / f"{module_name}.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("os", "time", "datetime", "random", "uuid", "pathlib",
                          "subprocess", "socket"):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), (module_name, forbidden)


# Stage N inventory: the production writers that emit application-configuration
# artifacts today, via AppBuildPlan. They stay authoritative until the 5D
# cutover; B2A must not be wired into them and they must not call the B2A
# renderer.
_PRODUCTION_APP_CONFIG_WRITERS = (
    "factory_app/workflows/AppGenerator/tools/materialize_app_config_contracts.py",
    "factory_app/workflows/AppGenerator/tools/generate_and_download.py",
    "factory_app/workflows/AppGenerator/tools/save_app_schema.py",
    "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
)

_B2A_MODULE_MARKERS = (
    "app_config_materialization",
    "decl_bytes",
)


def test_production_writers_exist_and_do_not_call_the_b2a_renderer() -> None:
    for rel in _PRODUCTION_APP_CONFIG_WRITERS:
        path = ROOT / Path(rel)
        assert path.is_file(), f"production writer inventory is stale: {rel}"
        source = path.read_text(encoding="utf-8")
        for marker in _B2A_MODULE_MARKERS:
            assert marker not in source, (rel, marker)


def test_b2a_renderer_is_unwired_from_production_code() -> None:
    """Only the semantics materializer (and tests) may reference the renderer."""
    allowed = {
        Path("mozaiksai/core/semantics/app_config_materialization.py"),
        Path("mozaiksai/core/semantics/decl_bytes.py"),
        Path("mozaiksai/core/semantics/workflow_interface_materialization.py"),
        Path("mozaiksai/core/semantics/materialization.py"),
    }
    offenders: list[str] = []
    for root_dir in ("mozaiksai", "factory_app"):
        for path in (ROOT / root_dir).rglob("*.py"):
            rel = path.relative_to(ROOT)
            if rel in allowed:
                continue
            source = path.read_text(encoding="utf-8")
            if any(marker in source for marker in _B2A_MODULE_MARKERS):
                offenders.append(str(rel))
    assert offenders == [], offenders


def test_renderer_module_imports_no_payload_or_graph_symbols() -> None:
    """The renderer consumes only the closed snapshot — never the semantic
    authoring model. This is the exact dependency direction the architecture
    scan in test_semantic_payload_graph_v2 enforces repo-wide; asserting it
    here keeps the boundary explicit at the renderer itself."""
    for module_name in ("app_config_materialization", "decl_bytes"):
        source = (
            ROOT / "mozaiksai" / "core" / "semantics" / f"{module_name}.py"
        ).read_text(encoding="utf-8")
        assert "mozaiksai.core.semantics.payloads" not in source, module_name
        assert "SemanticGraphV2" not in source, module_name
        assert "semantics.binding" not in source, module_name


def test_semantics_package_import_does_not_load_the_renderer() -> None:
    """Importing the semantics package (the convenience-export surface) must
    not transitively load the renderer or the offline materializer, so no
    production module can reach them through the package __init__."""
    probe = (
        "import sys\n"
        "import mozaiksai.core.semantics  # noqa: F401\n"
        "loaded = [m for m in sys.modules if 'app_config_materialization' in m\n"
        "          or m.endswith('semantics.materialization')\n"
        "          or m.endswith('semantics.decl_bytes')]\n"
        "print(loaded)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


# ---------------------------------------------------------------------------
# Render-input snapshot attacks: invalid input fails closed; equivalent
# reordered facts produce identical bytes.
# ---------------------------------------------------------------------------


def _valid_render_input(family: str = "app_manifest"):
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    unit = next(
        u
        for u in plan.units
        if u.disposition is PlanDisposition.RENDER and u.family_kind == family
    )
    return plan, payload_by_node, unit, project_app_family_render_input(
        unit=unit, payload_by_node=payload_by_node
    )


def test_render_input_is_frozen_and_rejects_undeclared_fields() -> None:
    _plan_obj, _payloads, _unit, render_input = _valid_render_input()
    with pytest.raises(ValidationError):
        render_input.default_route = "/elsewhere"  # type: ignore[misc]
    document = render_input.model_dump()
    document["arbitrary_metadata"] = {"campaign": "x"}
    with pytest.raises(ValidationError):
        type(render_input).model_validate(document)
    _p2, _pl2, _u2, integrations_input = _valid_render_input(
        "app_integrations_config"
    )
    entry = integrations_input.integrations[0].model_dump()
    entry["provider_account"] = "acct_123"
    with pytest.raises(ValidationError):
        type(integrations_input.integrations[0]).model_validate(entry)


def test_render_input_normalizes_reordered_equivalent_facts() -> None:
    plan, payload_by_node, unit, render_input = _valid_render_input(
        "app_ui_route_manifest"
    )
    document = render_input.model_dump()
    document["pages"] = list(reversed(document["pages"]))
    document["sources"] = list(reversed(document["sources"]))
    reordered = type(render_input).model_validate(document)
    assert reordered == render_input
    assert render_app_config_unit(
        unit=unit, render_input=reordered
    ) == render_app_config_unit(unit=unit, render_input=render_input)


def test_render_input_rejects_duplicates_and_empty_sources() -> None:
    _plan_obj, _payloads, _unit, route_input = _valid_render_input(
        "app_ui_route_manifest"
    )
    document = route_input.model_dump()
    document["pages"] = document["pages"] + document["pages"][:1]
    with pytest.raises(ValidationError, match="duplicate page routes"):
        type(route_input).model_validate(document)
    document = route_input.model_dump()
    document["sources"] = []
    with pytest.raises(ValidationError, match="pins no semantic sources"):
        type(route_input).model_validate(document)


def test_stale_or_missing_source_digest_fails_closed() -> None:
    plan, payload_by_node, unit, render_input = _valid_render_input()
    document = render_input.model_dump()
    for source in document["sources"]:
        source["payload_digest"] = "f" * 64
    stale = type(render_input).model_validate(document)
    with pytest.raises(AppConfigMaterializationError, match="pinned payload digest"):
        render_app_config_unit(unit=unit, render_input=stale)
    document = render_input.model_dump()
    document["sources"] = [
        s for s in document["sources"] if s["node_id"] not in {
            src.node_id for src in unit.sources
        }
    ]
    if document["sources"]:
        missing = type(render_input).model_validate(document)
        with pytest.raises(AppConfigMaterializationError, match="missing"):
            render_app_config_unit(unit=unit, render_input=missing)


def test_render_input_from_a_different_application_fails_closed() -> None:
    plan, _payloads, unit, _render_input = _valid_render_input()
    other_graph, other_payloads = _extended_fixture(default_route="/reports")
    other_plan = _plan(other_graph, other_payloads, with_configs=False)
    other_unit = next(
        u
        for u in other_plan.units
        if u.disposition is PlanDisposition.RENDER
        and u.family_kind == "app_manifest"
    )
    other_input = project_app_family_render_input(
        unit=other_unit,
        payload_by_node={p.node_id: p for p in other_payloads},
    )
    with pytest.raises(AppConfigMaterializationError, match="pinned payload digest"):
        render_app_config_unit(unit=unit, render_input=other_input)


# ---------------------------------------------------------------------------
# Renderer-selection authorization matrix: the ImplementationBinding selection
# is an authorization/capability boundary, not descriptive metadata. Version 1
# of deterministic_app_config_renderer supports exactly the five-family set;
# anything else fails closed at resolution, per-unit dispatch, or output
# closure.
# ---------------------------------------------------------------------------


def _binding_with_families(
    graph,
    families,
    *,
    implementation_version: str = APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION,
    materializer_id: MaterializerIdentifier = MaterializerIdentifier.APP_CONFIG_EXECUTOR,
):
    return build_implementation_binding(
        binding_id="b2a_matrix",
        version=1,
        scope=_SCOPE,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=graph.scope,
        ),
        renderer_selections=(
            RendererSelection(
                materializer_id=MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR,
                implementation_id="deterministic_page_schema_renderer",
                implementation_version="1",
                artifact_families=("app_ui_page_schema",),
            ),
            RendererSelection(
                materializer_id=materializer_id,
                implementation_id=APP_CONFIG_RENDERER_IMPLEMENTATION_ID,
                implementation_version=implementation_version,
                artifact_families=tuple(families),
            ),
        ),
    )


def _materialize_with(binding, *, graph, payloads, plan):
    from mozaiksai.core.semantics.materialization import MaterializationError

    return materialize_plan(
        plan=plan,
        graph=graph,
        payloads=payloads,
        binding=binding,
        layout_registry=build_app_layout_registry(()),
    ), MaterializationError


@pytest.mark.parametrize(
    "families",
    [
        pytest.param(("app_manifest",), id="only-app-manifest"),
        pytest.param(("app_config",), id="only-app-config"),
        pytest.param(
            tuple(sorted(APP_CONFIG_FAMILIES - {"app_secret_references"})),
            id="three-of-four",
        ),
        pytest.param(
            tuple(sorted(APP_CONFIG_FAMILIES | {"app_config"})),
            id="four-plus-app-config",
        ),
        pytest.param(
            tuple(sorted(APP_CONFIG_FAMILIES | {"app_subscription_config"})),
            id="four-plus-subscription",
        ),
        pytest.param(
            tuple(sorted(APP_CONFIG_FAMILIES | {"totally_unknown_family"})),
            id="four-plus-forged",
        ),
    ],
)
def test_non_exact_family_sets_fail_closed_and_emit_nothing(families) -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    binding = _binding_with_families(graph, families)
    from mozaiksai.core.semantics.materialization import MaterializationError

    with pytest.raises((AppConfigMaterializationError, MaterializationError)):
        materialize_plan(
            plan=plan,
            graph=graph,
            payloads=payloads,
            binding=binding,
            layout_registry=build_app_layout_registry(()),
        )


def test_subset_binding_attack_is_rejected_verbatim() -> None:
    """Codex 2's exact attack, preserved unchanged: an otherwise valid binding
    whose app-config selection authorizes only app_manifest must fail closed
    instead of emitting all five application families."""
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    binding = _binding_with_families(graph, ("app_manifest",))
    with pytest.raises(
        AppConfigMaterializationError, match="must declare exactly its"
    ):
        materialize_plan(
            plan=plan,
            graph=graph,
            payloads=payloads,
            binding=binding,
            layout_registry=build_app_layout_registry(()),
        )


def test_empty_and_duplicate_family_sets_fail_at_the_model() -> None:
    with pytest.raises(ValidationError):
        RendererSelection(
            materializer_id=MaterializerIdentifier.APP_CONFIG_EXECUTOR,
            implementation_id=APP_CONFIG_RENDERER_IMPLEMENTATION_ID,
            implementation_version=APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION,
            artifact_families=(),
        )
    with pytest.raises(ValidationError, match="unique"):
        RendererSelection(
            materializer_id=MaterializerIdentifier.APP_CONFIG_EXECUTOR,
            implementation_id=APP_CONFIG_RENDERER_IMPLEMENTATION_ID,
            implementation_version=APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION,
            artifact_families=("app_manifest", "app_manifest"),
        )


def test_exact_set_succeeds_in_canonical_and_shuffled_order() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    canonical = _binding_with_families(graph, tuple(sorted(APP_CONFIG_FAMILIES)))
    shuffled = _binding_with_families(
        graph, tuple(reversed(sorted(APP_CONFIG_FAMILIES)))
    )
    assert canonical.binding_digest == shuffled.binding_digest
    registry = build_app_layout_registry(())
    a = materialize_plan(
        plan=plan, graph=graph, payloads=payloads, binding=canonical,
        layout_registry=registry,
    ).files()
    b = materialize_plan(
        plan=plan, graph=graph, payloads=payloads, binding=shuffled,
        layout_registry=registry,
    ).files()
    assert a == b
    assert _RENDERED_PATHS <= set(a)


def test_wrong_version_and_wrong_materializer_fail_closed() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    registry = build_app_layout_registry(())
    from mozaiksai.core.semantics.materialization import MaterializationError

    wrong_version = _binding_with_families(
        graph, tuple(sorted(APP_CONFIG_FAMILIES)), implementation_version="2"
    )
    with pytest.raises((AppConfigMaterializationError, MaterializationError)):
        materialize_plan(
            plan=plan, graph=graph, payloads=payloads, binding=wrong_version,
            layout_registry=registry,
        )
    wrong_materializer = _binding_with_families(
        graph,
        tuple(sorted(APP_CONFIG_FAMILIES)),
        materializer_id=MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR,
    )
    with pytest.raises((AppConfigMaterializationError, MaterializationError)):
        materialize_plan(
            plan=plan, graph=graph, payloads=payloads, binding=wrong_materializer,
            layout_registry=registry,
        )


def test_forged_rebuilt_binding_with_recomputed_digest_still_fails() -> None:
    """Serializing a valid binding, shrinking its family set, and rebuilding a
    structurally valid document with a freshly computed digest must not
    restore authorization: the family-set semantics are validated, not the
    claimed digest."""
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    valid = _binding(graph)
    document = valid.model_dump(mode="json")
    forged_families = ("app_manifest",)
    forged = _binding_with_families(graph, forged_families)
    assert forged.binding_digest != valid.binding_digest
    assert document["renderer_selections"][1]["artifact_families"] != list(
        forged_families
    )
    with pytest.raises(AppConfigMaterializationError):
        materialize_plan(
            plan=plan,
            graph=graph,
            payloads=payloads,
            binding=forged,
            layout_registry=build_app_layout_registry(()),
        )


def test_plan_from_another_application_fails_before_authorization() -> None:
    graph, payloads = _extended_fixture()
    other_graph, other_payloads = _extended_fixture(default_route="/reports")
    other_plan = _plan(other_graph, other_payloads, with_configs=False)
    from mozaiksai.core.semantics.materialization import MaterializationError

    with pytest.raises(MaterializationError):
        materialize_plan(
            plan=other_plan,
            graph=graph,
            payloads=payloads,
            binding=_binding(graph),
            layout_registry=build_app_layout_registry(()),
        )


def test_registry_from_another_snapshot_fails_before_authorization() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)

    class _MutatedRegistry:
        """Same schema version, one mutated row -> different snapshot digest."""

        def __init__(self) -> None:
            source = build_app_layout_registry(())
            self.schema_version = source.schema_version
            families = []
            for family in source.ordered_families():
                if family.path_template == "app.json":
                    family = family.model_copy(
                        update={"path_template": "app_renamed.json"}
                    )
                families.append(family)
            self._families = tuple(families)

        def ordered_families(self):
            return self._families

    from mozaiksai.core.semantics.materialization import MaterializationError

    with pytest.raises(MaterializationError):
        materialize_plan(
            plan=plan,
            graph=graph,
            payloads=payloads,
            binding=_binding(graph),
            layout_registry=_MutatedRegistry(),
        )


def test_inactive_conditional_family_is_absent_without_error() -> None:
    """The binding pins the renderer's exact five-family capability; the plan
    decides which of those families are active. An integrations-absent app
    still binds all five, renders no integrations bytes, and raises no
    missing-output error."""
    graph, payloads = _extended_fixture(integrations_selected=False)
    plan = _plan(graph, payloads, with_configs=False)
    bundle = materialize_plan(
        plan=plan,
        graph=graph,
        payloads=payloads,
        binding=_binding(graph),
        layout_registry=build_app_layout_registry(()),
    )
    files = bundle.files()
    assert "config/integrations.yaml" not in files
    assert "config/ai.json" not in files
    for expected in ("app.json", "ui/route_manifest.json",
                     "security/secrets.yaml"):
        assert expected in files, expected
    assert yaml.safe_load(files["security/secrets.yaml"]) == {
        "version": 1,
        "secrets": [],
    }


def test_output_closure_rejects_extra_and_missing_outputs() -> None:
    from mozaiksai.core.semantics.materialization import (
        _assert_app_config_output_closure,
    )

    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    binding = _binding(graph)
    selection = next(
        s
        for s in binding.renderer_selections
        if s.materializer_id is MaterializerIdentifier.APP_CONFIG_EXECUTOR
    )
    bundle = materialize_plan(
        plan=plan,
        graph=graph,
        payloads=payloads,
        binding=binding,
        layout_registry=build_app_layout_registry(()),
    )
    app_outputs = [
        o
        for o in bundle.outputs
        if o.path in _RENDERED_PATHS
    ]
    # The real bundle passes the closure.
    _assert_app_config_output_closure(plan, bundle.outputs, selection)
    # An extra unauthorized duplicate output fails.
    with pytest.raises(AppConfigMaterializationError, match="exactly one output"):
        _assert_app_config_output_closure(
            plan, tuple(bundle.outputs) + (app_outputs[0],), selection
        )
    # Omitting one active authorized output fails.
    dropped = tuple(o for o in bundle.outputs if o is not app_outputs[0])
    with pytest.raises(AppConfigMaterializationError, match="exactly one output"):
        _assert_app_config_output_closure(plan, dropped, selection)
    # No selection at all cannot authorize emitted app-config outputs.
    with pytest.raises(AppConfigMaterializationError, match="unauthorized"):
        _assert_app_config_output_closure(plan, bundle.outputs, None)


# ---------------------------------------------------------------------------
# Selection honesty and family-local independence. app_config (config/ai.json)
# is a deferred prerequisite in EVERY workflow scenario: per-workflow
# workflow_startup_mode is not application-level chat launch authority, and no
# application-level AI-launch facts exist. Its typed gap must never block the
# four renderable families, and no chat startup mode is ever inferred.
# ---------------------------------------------------------------------------


def _gap_paths(plan, family_kind):
    return {
        gap.path_template for gap in plan.gaps if gap.family_kind == family_kind
    }


def _materialized_files(graph, payloads, plan):
    return materialize_plan(
        plan=plan,
        graph=graph,
        payloads=payloads,
        binding=_binding(graph),
        layout_registry=build_app_layout_registry(()),
    ).files()


def _assert_four_families_render_and_ai_json_absent(graph, payloads, plan):
    assert "config/ai.json" in _gap_paths(plan, "app_config")
    assert not any(
        u.family_kind == "app_config" and u.disposition is PlanDisposition.RENDER
        for u in plan.units
    )
    files = _materialized_files(graph, payloads, plan)
    assert "config/ai.json" not in files
    for expected in _RENDERED_PATHS:
        assert expected in files, expected
    return files


def test_workflows_selected_with_zero_payloads_stays_a_typed_gap() -> None:
    """Codex 2 Attack A, preserved: WORKFLOWS selected, zero WorkflowPayloads.

    app_config stays a typed renderer_input_incomplete gap with no
    config/ai.json unit or bytes — and the four complete application families
    render anyway: a missing app_config input must not abort them through a
    globally coupled snapshot."""
    graph, payloads = _extended_fixture(drop_workflow_payload=True)
    plan = _plan(graph, payloads, with_configs=False)
    _assert_four_families_render_and_ai_json_absent(graph, payloads, plan)


def test_two_workflows_without_entry_point_render_no_ai_config() -> None:
    """Codex 2 Attack B, preserved: one agent_driven and one on_demand
    workflow with no application-level workflow entry point. Previously
    chat_startup_mode was inferred as "workflow" from per-workflow facts; now
    nothing is inferred — app_config stays a typed gap, no config/ai.json and
    no entry_point are emitted, and the four families render."""
    graph, payloads = _extended_fixture(
        workflow_startup_mode=WorkflowStartupMode.AGENT_DRIVEN,
        extra_workflow_startup_mode=WorkflowStartupMode.ON_DEMAND,
    )
    plan = _plan(graph, payloads, with_configs=False)
    files = _assert_four_families_render_and_ai_json_absent(graph, payloads, plan)
    blob = b"".join(files.values())
    assert b"entry_point" not in blob
    assert b"chat_startup_mode" not in blob


@pytest.mark.parametrize(
    ("kwargs", "case"),
    [
        pytest.param({}, "one-complete-workflow", id="one-complete-workflow"),
        pytest.param(
            {"workflow_startup_mode": WorkflowStartupMode.AGENT_DRIVEN},
            "agent-driven",
            id="one-agent-driven",
        ),
        pytest.param(
            {
                "workflow_startup_mode": WorkflowStartupMode.AGENT_DRIVEN,
                "extra_workflow_startup_mode": WorkflowStartupMode.AGENT_DRIVEN,
            },
            "two-agent-driven",
            id="two-agent-driven",
        ),
        pytest.param(
            {"workflow_startup_mode": None},
            "startup-mode-missing",
            id="startup-mode-missing",
        ),
        pytest.param(
            {"workflows_selected": False, "drop_workflow_payload": True},
            "absent-no-payloads",
            id="absent-no-payloads",
        ),
        pytest.param(
            {"workflows_selected": False},
            "absent-with-payload",
            id="absent-with-payload",
        ),
    ],
)
def test_app_config_stays_a_typed_gap_and_never_blocks_the_four_families(
    kwargs, case
) -> None:
    """Every workflow scenario — complete, ambiguous, missing, absent, or
    contradictory — leaves app_config a typed gap (no AI-launch authority
    exists) while the four renderable families materialize independently."""
    graph, payloads = _extended_fixture(**kwargs)
    plan = _plan(graph, payloads, with_configs=False)
    _assert_four_families_render_and_ai_json_absent(graph, payloads, plan)


def test_workflow_node_with_missing_payload_fails_derivation_closed() -> None:
    from mozaiksai.core.semantics.compilation_plan import CompilationPlanError

    graph, payloads = _extended_fixture()
    without = [
        p for p in payloads if p.payload_kind is not SemanticNodeKind.WORKFLOW
    ]
    with pytest.raises(CompilationPlanError, match="payload closure failed"):
        derive_compilation_plan(
            graph=graph,
            payloads=without,
            registry=build_app_layout_registry(()),
        )


def test_workflow_mutations_change_none_of_the_four_outputs() -> None:
    """No retained family consumes workflow facts: startup-mode and
    description mutations leave all four outputs byte-identical."""
    base = _bundle_files()
    for kwargs in (
        {"workflow_startup_mode": WorkflowStartupMode.AGENT_DRIVEN},
        {"extra_workflow_startup_mode": WorkflowStartupMode.ON_DEMAND},
    ):
        graph, payloads = _extended_fixture(**kwargs)
        plan = _plan(graph, payloads, with_configs=False)
        files = _materialized_files(graph, payloads, plan)
        for path in _RENDERED_PATHS:
            assert files[path] == base[path], (kwargs, path)


def test_renderer_cannot_emit_ai_json_even_for_a_forged_unit() -> None:
    """Attack: hand the renderer a unit whose plan-owned output claims
    config/ai.json. No deterministic contract exists for that path in this
    slice, so rendering fails closed instead of inventing AI-launch bytes."""
    plan, payload_by_node, unit, render_input = _valid_render_input()
    forged_output = unit.outputs[0].model_copy(update={"path": "config/ai.json"})
    forged_unit = unit.model_copy(update={"outputs": (forged_output,)})
    with pytest.raises(
        AppConfigMaterializationError, match="no deterministic"
    ):
        render_app_config_unit(unit=forged_unit, render_input=render_input)


def test_selected_auth_without_payload_stays_a_typed_gap() -> None:
    graph, payloads = _extended_fixture(auth_selected=True)
    without_auth = [
        p for p in payloads if p.payload_kind is not SemanticNodeKind.AUTH
    ]
    nodes = [
        SemanticNodeV2(
            node_id=p.node_id, kind=p.payload_kind, payload_ref=semantic_payload_ref(p)
        )
        for p in without_auth
    ]
    kept_ids = {n.node_id for n in nodes}
    edges = [
        e
        for e in graph.edges
        if e.source_node_id in kept_ids and e.target_node_id in kept_ids
    ]
    stripped_graph = build_semantic_graph_v2(
        graph_id=graph.graph_id,
        version=graph.version,
        scope=_SCOPE,
        nodes=nodes,
        edges=edges,
    )
    plan = _plan(stripped_graph, without_auth, with_configs=False)
    # app.json consumes authRequired: it gaps. app_config stays its deferred
    # typed gap. The families that do not consume auth facts remain
    # independently renderable — family-local completion, no shared gate.
    for gapped in ("app_manifest", "app_config"):
        assert not any(
            u.family_kind == gapped and u.disposition is PlanDisposition.RENDER
            for u in plan.units
        ), gapped
    for renderable in (
        "app_ui_route_manifest",
        "app_integrations_config",
        "app_secret_references",
    ):
        assert any(
            u.family_kind == renderable
            and u.disposition is PlanDisposition.RENDER
            for u in plan.units
        ), renderable


def test_selected_integrations_without_payload_gap_secret_references() -> None:
    graph, payloads = _extended_fixture()
    without = [
        p for p in payloads if p.payload_kind is not SemanticNodeKind.INTEGRATION
    ]
    nodes = [
        SemanticNodeV2(
            node_id=p.node_id, kind=p.payload_kind, payload_ref=semantic_payload_ref(p)
        )
        for p in without
    ]
    kept_ids = {n.node_id for n in nodes}
    edges = [
        e
        for e in graph.edges
        if e.source_node_id in kept_ids and e.target_node_id in kept_ids
    ]
    stripped_graph = build_semantic_graph_v2(
        graph_id=graph.graph_id,
        version=graph.version,
        scope=_SCOPE,
        nodes=nodes,
        edges=edges,
    )
    plan = _plan(stripped_graph, without, with_configs=False)
    # Selected integrations with zero payloads: neither the integrations
    # config nor the names-only secret surface may render empty output.
    for family in ("app_integrations_config", "app_secret_references"):
        assert not any(
            u.family_kind == family and u.disposition is PlanDisposition.RENDER
            for u in plan.units
        ), family
    # Unrelated families remain independently renderable.
    for renderable in ("app_manifest", "app_ui_route_manifest"):
        assert any(
            u.family_kind == renderable
            and u.disposition is PlanDisposition.RENDER
            for u in plan.units
        ), renderable


def test_selected_custom_routes_without_declaration_gap_route_manifest() -> None:
    graph, payloads = _extended_fixture(custom_routes_selected=True)
    plan = _plan(graph, payloads, with_configs=False)
    assert not any(
        u.family_kind == "app_ui_route_manifest"
        and u.disposition is PlanDisposition.RENDER
        for u in plan.units
    )
    assert "ui/route_manifest.json" in _gap_paths(plan, "app_ui_route_manifest")
    # Integrations and the other families remain independently renderable.
    for renderable in (
        "app_manifest",
        "app_integrations_config",
        "app_secret_references",
    ):
        assert any(
            u.family_kind == renderable
            and u.disposition is PlanDisposition.RENDER
            for u in plan.units
        ), renderable


def test_payload_mutation_after_projection_cannot_change_bytes() -> None:
    plan, payload_by_node, unit, render_input = _valid_render_input()
    before = render_app_config_unit(unit=unit, render_input=render_input)
    payload_by_node.clear()
    assert render_app_config_unit(unit=unit, render_input=render_input) == before


# ---------------------------------------------------------------------------
# Source locality: a family's PlanUnit footprint contains exactly the payload
# kinds whose facts influence its existence, path, bytes, or validator
# obligation, and the family render input must bind exactly that footprint —
# not a subset, not a superset. (Codex 2 attacks preserved below.)
# ---------------------------------------------------------------------------

_EXPECTED_FAMILY_SOURCE_KINDS = {
    "app_manifest": {"application", "auth"},
    "app_ui_route_manifest": {"application", "page"},
    "app_integrations_config": {"application", "integration"},
    "app_secret_references": {"application", "integration"},
}


def _unit_for(plan, family):
    return next(
        u
        for u in plan.units
        if u.disposition is PlanDisposition.RENDER and u.family_kind == family
    )


def test_auth_roles_mutation_leaves_secret_unit_and_bytes_unchanged() -> None:
    """Codex 2 Attack A, preserved: security/secrets.yaml consumes no auth
    fact, so AuthPayload is not a source of app_secret_references. A
    roles-only auth mutation leaves the secret unit's identity, footprint,
    reuse, and bytes unchanged."""
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    unit = _unit_for(plan, "app_secret_references")
    assert {s.node_id.split(".")[1] for s in unit.sources} == {
        "application",
        "integration",
    }
    mutated_graph, mutated = _extended_fixture(
        auth_roles=("admin", "viewer", "editor")
    )
    mutated_plan = _plan(mutated_graph, mutated, with_configs=False)
    mutated_unit = _unit_for(mutated_plan, "app_secret_references")
    assert mutated_unit.unit_digest == unit.unit_digest
    assert mutated_unit.sources == unit.sources
    from mozaiksai.core.semantics.compilation_plan import plan_regeneration_closure

    closure = plan_regeneration_closure(plan, mutated_plan)
    assert unit.unit_id in closure.reusable
    # app.json DOES consume auth (authRequired): its unit is conservatively
    # invalidated at payload-level granularity even though roles do not enter
    # its bytes.
    manifest_unit = _unit_for(plan, "app_manifest")
    mutated_manifest = _unit_for(mutated_plan, "app_manifest")
    assert mutated_manifest.unit_digest != manifest_unit.unit_digest
    base = _bundle_files()
    registry = build_app_layout_registry(())
    changed = materialize_plan(
        plan=mutated_plan,
        graph=mutated_graph,
        payloads=mutated,
        binding=_binding(mutated_graph),
        layout_registry=registry,
    ).files()
    for path in _RENDERED_PATHS:
        assert changed[path] == base[path], path


def test_extra_unrelated_source_in_valid_render_input_is_rejected() -> None:
    """Codex 2 Attack B, preserved: a fully valid render input carrying every
    expected source plus one unrelated valid payload source from the same
    graph must be rejected — the source set is exact, never 'at least'."""
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    unit = _unit_for(plan, "app_secret_references")
    valid = project_app_family_render_input(
        unit=unit, payload_by_node=payload_by_node
    )
    page = payload_by_node["mozaiks.page.home"]
    document = valid.model_dump()
    document["sources"] = list(document["sources"]) + [
        {"node_id": page.node_id, "payload_digest": page.payload_digest}
    ]
    forged = type(valid).model_validate(document)
    with pytest.raises(
        AppConfigMaterializationError, match="does not bind exactly"
    ):
        render_app_config_unit(unit=unit, render_input=forged)


@pytest.mark.parametrize(
    ("family", "extra_node"),
    [
        pytest.param(
            "app_secret_references", "mozaiks.auth.corpus", id="auth-into-secrets"
        ),
        pytest.param(
            "app_manifest", "mozaiks.workflow.digest", id="workflow-into-manifest"
        ),
        pytest.param(
            "app_ui_route_manifest",
            "mozaiks.integration.email",
            id="integration-into-routes",
        ),
        pytest.param(
            "app_integrations_config",
            "mozaiks.page.home",
            id="page-into-integrations",
        ),
    ],
)
def test_every_family_rejects_an_unrelated_extra_source(family, extra_node) -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    unit = _unit_for(plan, family)
    valid = project_app_family_render_input(
        unit=unit, payload_by_node=payload_by_node
    )
    extra = payload_by_node[extra_node]
    document = valid.model_dump()
    document["sources"] = list(document["sources"]) + [
        {"node_id": extra.node_id, "payload_digest": extra.payload_digest}
    ]
    forged = type(valid).model_validate(document)
    with pytest.raises(
        AppConfigMaterializationError, match="does not bind exactly"
    ):
        render_app_config_unit(unit=unit, render_input=forged)


def _forge_plan_with_extra_source(plan, family, extra):
    document = plan.model_dump(mode="json")
    identity = plan.canonical_payload(include_digest=False)
    index = next(
        i
        for i, unit in enumerate(plan.units)
        if unit.disposition is PlanDisposition.RENDER
        and unit.family_kind == family
    )
    source = {"node_id": extra.node_id, "payload_digest": extra.payload_digest}
    for target in (document, identity):
        target["units"][index]["sources"] = sorted(
            target["units"][index]["sources"] + [source],
            key=lambda item: item["node_id"],
        )
    document["plan_digest"] = canonical_digest(identity)
    return CompilationPlan.model_validate(document)


@pytest.mark.parametrize(
    ("family", "extra_node"),
    [
        pytest.param(
            "app_secret_references", "mozaiks.page.home", id="page-into-secrets"
        ),
        pytest.param(
            "app_manifest", "mozaiks.workflow.digest", id="workflow-into-manifest"
        ),
        pytest.param(
            "app_ui_route_manifest",
            "mozaiks.integration.email",
            id="integration-into-routes",
        ),
        pytest.param(
            "app_integrations_config",
            "mozaiks.page.home",
            id="page-into-integrations",
        ),
    ],
)
def test_canonical_authority_rejects_forged_family_plan_before_render_or_reuse(
    family, extra_node, monkeypatch
) -> None:
    """A re-digested plan cannot redefine any family's source footprint.

    Canonical authority validation runs before fresh rendering and before
    rematerialization can copy historical bytes.
    """
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {payload.node_id: payload for payload in payloads}
    forged = _forge_plan_with_extra_source(plan, family, payload_by_node[extra_node])
    registry = build_app_layout_registry(())
    authority_inputs = _authority_inputs(graph, payloads)
    base_bundle = _materialize_plan(
        plan=plan,
        authority_inputs=authority_inputs,
        graph=graph,
        payloads=payloads,
        binding=_binding(graph),
        layout_registry=registry,
    )

    def _must_not_materialize(*args, **kwargs):
        raise AssertionError("forged plan reached unit materialization")

    monkeypatch.setattr(
        "mozaiksai.core.semantics.materialization._materialize_unit",
        _must_not_materialize,
    )
    with pytest.raises(MaterializationError, match="canonical authority"):
        _materialize_plan(
            plan=forged,
            authority_inputs=authority_inputs,
            graph=graph,
            payloads=payloads,
            binding=_binding(graph),
            layout_registry=registry,
        )
    with pytest.raises(MaterializationError, match="canonical authority"):
        _rematerialize_plan(
            base_bundle=base_bundle,
            base_plan=plan,
            base_authority_inputs=authority_inputs,
            successor_plan=forged,
            successor_authority_inputs=authority_inputs,
            graph=graph,
            payloads=payloads,
            binding=_binding(graph),
            layout_registry=registry,
        )


def test_plan_source_added_to_a_family_is_rejected() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    plan_payload = next(
        p for p in payloads if p.payload_kind is SemanticNodeKind.PLAN
    )
    unit = _unit_for(plan, "app_manifest")
    valid = project_app_family_render_input(
        unit=unit, payload_by_node=payload_by_node
    )
    document = valid.model_dump()
    document["sources"] = list(document["sources"]) + [
        {
            "node_id": plan_payload.node_id,
            "payload_digest": plan_payload.payload_digest,
        }
    ]
    forged = type(valid).model_validate(document)
    with pytest.raises(
        AppConfigMaterializationError, match="does not bind exactly"
    ):
        render_app_config_unit(unit=unit, render_input=forged)


def test_omitted_required_source_and_shuffled_exact_set() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    unit = _unit_for(plan, "app_integrations_config")
    valid = project_app_family_render_input(
        unit=unit, payload_by_node=payload_by_node
    )
    document = valid.model_dump()
    document["sources"] = list(document["sources"])[:-1]
    if document["sources"]:
        trimmed = type(valid).model_validate(document)
        with pytest.raises(
            AppConfigMaterializationError, match="does not bind exactly"
        ):
            render_app_config_unit(unit=unit, render_input=trimmed)
    # The exact expected set succeeds in canonical and shuffled input order.
    document = valid.model_dump()
    document["sources"] = list(reversed(document["sources"]))
    shuffled = type(valid).model_validate(document)
    assert render_app_config_unit(
        unit=unit, render_input=shuffled
    ) == render_app_config_unit(unit=unit, render_input=valid)


def test_registry_plan_and_render_input_source_kinds_are_consistent() -> None:
    """Permanent guard: for every renderer-ready family the registry
    declaration, the derived PlanUnit footprint, and the projected render
    input agree on exactly the same source kinds."""
    registry = build_app_layout_registry(())
    for family in registry.ordered_families():
        expected = _EXPECTED_FAMILY_SOURCE_KINDS.get(family.kind.value)
        if expected is None or family.path_template not in _RENDERED_PATHS:
            continue
        assert set(family.semantic_input_kinds) == expected, family.kind
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    for family_kind, expected in _EXPECTED_FAMILY_SOURCE_KINDS.items():
        unit = _unit_for(plan, family_kind)
        derived_kinds = {
            payload_by_node[s.node_id].payload_kind.value for s in unit.sources
        }
        assert derived_kinds <= expected, (family_kind, derived_kinds)
        render_input = project_app_family_render_input(
            unit=unit, payload_by_node=payload_by_node
        )
        input_kinds = {
            payload_by_node[s.node_id].payload_kind.value
            for s in render_input.sources
        }
        assert input_kinds == derived_kinds, family_kind


def test_no_family_consumes_graph_edge_identities() -> None:
    """Edge audit: none of the four family inputs consume edge facts. Adding
    an unrelated edge changes graph identity but leaves every unit's
    footprint, identity, and rendered bytes unchanged."""
    base = _bundle_files()
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    extra_edge = SemanticEdge(
        kind=SemanticEdgeKind.DEPENDS_ON,
        source_node_id="mozaiks.module.reports",
        target_node_id="mozaiks.page.home",
    )
    edged_graph = build_semantic_graph_v2(
        graph_id=graph.graph_id,
        version=graph.version,
        scope=_SCOPE,
        nodes=list(graph.nodes),
        edges=[*graph.edges, extra_edge],
    )
    edged_plan = _plan(edged_graph, payloads, with_configs=False)
    for family_kind in _EXPECTED_FAMILY_SOURCE_KINDS:
        assert (
            _unit_for(edged_plan, family_kind).unit_digest
            == _unit_for(plan, family_kind).unit_digest
        ), family_kind
    files = materialize_plan(
        plan=edged_plan,
        graph=edged_graph,
        payloads=payloads,
        binding=_binding(edged_graph),
        layout_registry=build_app_layout_registry(()),
    ).files()
    for path in _RENDERED_PATHS:
        assert files[path] == base[path], path


def test_unrelated_page_and_workflow_mutations_keep_secret_unit_reusable() -> None:
    from mozaiksai.core.semantics.compilation_plan import plan_regeneration_closure

    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    secret_unit = _unit_for(plan, "app_secret_references")
    for kwargs in (
        {"home_title": "Console"},
        {"workflow_startup_mode": WorkflowStartupMode.AGENT_DRIVEN},
    ):
        m_graph, m_payloads = _extended_fixture(**kwargs)
        m_plan = _plan(m_graph, m_payloads, with_configs=False)
        assert (
            _unit_for(m_plan, "app_secret_references").unit_digest
            == secret_unit.unit_digest
        ), kwargs
        closure = plan_regeneration_closure(plan, m_plan)
        assert secret_unit.unit_id in closure.reusable, kwargs
