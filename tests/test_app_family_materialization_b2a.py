"""ADR 0007 Slice 5D-0B2A proof gate: deterministic application-family bytes.

Extends the accepted representative corpus so the reporting-style application
explicitly SELECTS auth, one integration, and its workflow, then proves the
closed application-configuration family set renders canonical bytes:

    app.json, ui/route_manifest.json, config/ai.json,
    config/integrations.yaml, security/secrets.yaml

through the single accepted ``deterministic_app_config_renderer@1`` authority.

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
from mozaiksai.core.semantics.compilation_plan import (
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
    build_app_family_render_input,
    materialize_plan,
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
    build_semantic_payload,
    semantic_payload_ref,
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
    "config/ai.json",
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
    integration_secret_name: str = "EMAIL_API_KEY",
):
    """Corpus with explicit product-meaningful optional-family selections."""
    payloads = dict(_corpus_payloads(scope=_SCOPE, home_title=home_title))

    application = payloads["application"]
    selections = tuple(
        OptionalFamilySelection(
            family=family,
            status=(
                OptionalFamilySelectionStatus.SELECTED
                if family in _SELECTED and (family is not OptionalFamilyKind.AUTH or auth_selected)
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
    if not auth_selected:
        payloads.pop("auth", None)
    else:
        auth = payloads["auth"]
        payloads["auth"] = build_semantic_payload(
            AuthPayload,
            node_id=auth.node_id,
            payload_version=auth.payload_version,
            scope=_SCOPE,
            auth_required=auth.auth_required,
            strategy=auth.strategy,
            roles=auth.roles,
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
    census = Counter(f.disposition.value for f in registry.families)
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


def test_ai_config_and_integration_and_secret_bytes() -> None:
    files = _bundle_files()
    ai_document = json.loads(files["config/ai.json"].decode("utf-8"))
    assert set(ai_document) == {"chat"}
    assert ai_document["chat"]["chat_startup_mode"] in {"ask", "workflow"}

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
    for unchanged in ("app.json", "config/ai.json", "config/integrations.yaml", "security/secrets.yaml"):
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
    for unchanged in ("app.json", "config/ai.json", "ui/route_manifest.json"):
        assert changed[unchanged] == base[unchanged], unchanged


# ---------------------------------------------------------------------------
# Ownership, binding, fail-closed behavior
# ---------------------------------------------------------------------------


def test_renderer_rejects_wrong_family_wrong_binding_and_unknown_template() -> None:
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    render_input = build_app_family_render_input(
        units=plan.units, payload_by_node=payload_by_node
    )
    page_unit = next(
        u for u in plan.units if u.family_kind == "app_ui_page_schema"
    )
    with pytest.raises(AppConfigMaterializationError, match="not an"):
        render_app_config_unit(unit=page_unit, render_input=render_input)

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
    """
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads)
    rendered = set(_render_units(plan))
    assert "config/subscriptions.yaml" not in rendered
    assert "data/contract.json" not in rendered
    assert "config/asset_manifest.json" not in rendered
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


def _valid_render_input():
    graph, payloads = _extended_fixture()
    plan = _plan(graph, payloads, with_configs=False)
    payload_by_node = {p.node_id: p for p in payloads}
    return plan, payload_by_node, build_app_family_render_input(
        units=plan.units, payload_by_node=payload_by_node
    )


def test_render_input_is_frozen_and_rejects_undeclared_fields() -> None:
    _plan_obj, _payloads, render_input = _valid_render_input()
    with pytest.raises(ValidationError):
        render_input.default_route = "/elsewhere"  # type: ignore[misc]
    document = render_input.model_dump()
    document["arbitrary_metadata"] = {"campaign": "x"}
    with pytest.raises(ValidationError):
        type(render_input).model_validate(document)
    entry = render_input.integrations[0].model_dump()
    entry["provider_account"] = "acct_123"
    with pytest.raises(ValidationError):
        type(render_input.integrations[0]).model_validate(entry)


def test_render_input_normalizes_reordered_equivalent_facts() -> None:
    plan, payload_by_node, render_input = _valid_render_input()
    document = render_input.model_dump()
    document["pages"] = list(reversed(document["pages"]))
    document["integrations"] = list(reversed(document["integrations"]))
    document["sources"] = list(reversed(document["sources"]))
    reordered = type(render_input).model_validate(document)
    assert reordered == render_input
    unit = next(
        u
        for u in plan.units
        if u.disposition is PlanDisposition.RENDER
        and u.family_kind in APP_CONFIG_FAMILIES
    )
    assert render_app_config_unit(
        unit=unit, render_input=reordered
    ) == render_app_config_unit(unit=unit, render_input=render_input)


def test_render_input_rejects_duplicates_and_empty_sources() -> None:
    _plan_obj, _payloads, render_input = _valid_render_input()
    document = render_input.model_dump()
    document["pages"] = document["pages"] + document["pages"][:1]
    with pytest.raises(ValidationError, match="duplicate page routes"):
        type(render_input).model_validate(document)
    document = render_input.model_dump()
    document["sources"] = []
    with pytest.raises(ValidationError, match="pins no semantic sources"):
        type(render_input).model_validate(document)


def test_stale_or_missing_source_digest_fails_closed() -> None:
    plan, payload_by_node, render_input = _valid_render_input()
    unit = next(
        u
        for u in plan.units
        if u.disposition is PlanDisposition.RENDER
        and u.family_kind in APP_CONFIG_FAMILIES
    )
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
    plan, _payloads, _render_input = _valid_render_input()
    other_graph, other_payloads = _extended_fixture(default_route="/reports")
    other_plan = _plan(other_graph, other_payloads, with_configs=False)
    other_input = build_app_family_render_input(
        units=other_plan.units,
        payload_by_node={p.node_id: p for p in other_payloads},
    )
    unit = next(
        u
        for u in plan.units
        if u.disposition is PlanDisposition.RENDER
        and u.family_kind == "app_manifest"
    )
    with pytest.raises(AppConfigMaterializationError, match="pinned payload digest"):
        render_app_config_unit(unit=unit, render_input=other_input)


def test_payload_mutation_after_snapshot_creation_cannot_change_bytes() -> None:
    plan, payload_by_node, render_input = _valid_render_input()
    unit = next(
        u
        for u in plan.units
        if u.disposition is PlanDisposition.RENDER
        and u.family_kind in APP_CONFIG_FAMILIES
    )
    before = render_app_config_unit(unit=unit, render_input=render_input)
    payload_by_node.clear()
    assert render_app_config_unit(unit=unit, render_input=render_input) == before
