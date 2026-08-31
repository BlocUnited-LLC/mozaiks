"""ADR 0007 Slice 4C proof gate: deterministic offline page materialization.

Proves the first honest deterministic materialization path:

    SemanticGraphV2 -> CompilationPlan -> accepted ImplementationBinding
    -> canonical ``app_ui_page_schema`` bytes -> existing validation/loading
    -> successor-bundle runtime proof

Covers: exact canonical bytes, repeated/cross-process/input-order determinism,
fail-closed implementation resolution, plan output ownership, byte-exact
opaque preservation, explicit gaps, selective regeneration through the 4B
closure (linked-section change -> page affected; unrelated change -> page
reusable), successor validation + AppLoader + lifespan-owned platform boot,
the recursive closed-domain gate, and the renderer's structural isolation
from AppBuildPlan/AG2/runtime state.

The bounded claim proven here: a typed semantic page change deterministically
produces a validated, loadable/bootable successor bundle while unrelated
opaque artifacts are preserved byte-exact. Nothing here claims whole-app
regeneration; unsupported families remain typed gaps or authored files.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.app_validation import (
    run_app_bundle_acceptance_gate,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    scan_generated_bundle,
)
from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
from mozaiksai.core.runtime.app.layout_registry import (
    MaterializerIdentifier,
    build_app_layout_registry,
)
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.app.page_schema import AppPageSchema
from mozaiksai.core.semantics.binding import (
    RendererSelection,
    build_implementation_binding,
)
from mozaiksai.core.semantics.compilation_plan import (
    PlanDisposition,
    derive_compilation_plan,
)
from mozaiksai.core.semantics.materialization import (
    PAGE_SCHEMA_FAMILY,
    PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID,
    PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION,
    MaterializationError,
    compose_bundle,
    materialize_plan,
    rematerialize_plan,
    render_app_ui_page_schema_unit,
    resolve_page_schema_renderer_selection,
    validate_page_renderer_input_closure,
)
from mozaiksai.core.semantics.offline_projection import project_semantic_graph
from mozaiksai.core.semantics.opaque_artifact import PreservedOpaqueArtifact
from mozaiksai.core.semantics.refs import (
    ChildContractRef,
    ExecutionAccessScopeRef,
    SemanticGraphRef,
)
from mozaiksai.core.validation.functional_generated_app import (
    scan_functional_generated_app,
)
from tests.test_continuous_deterministic_materialization import (
    _FakeMongoClient,
    _write_bundle,
)
from tests.test_materialized_bundle_production_runtime import (
    _restore_platform_state,
    _save_platform_state,
)
from tests.test_semantic_offline_projection import _pinned_registry

ROOT = Path(__file__).resolve().parents[1]

_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant1", workspace_id="ws1")

_HANDLER_BYTES = b"""from .service import OrdersService


class OrdersModule:
    def __init__(self):
        self.service = OrdersService()

    async def list_orders(self, ctx, **params):
        return await self.service.list_orders(ctx, **params)

    async def create_order(self, ctx, **params):
        return await self.service.create_order(ctx, **params)
"""

_SERVICE_BYTES = b"""class OrdersService:
    async def list_orders(self, ctx, **params):
        return {"orders": []}

    async def create_order(self, ctx, **params):
        return {"order": {"customer_name": params.get("customer_name")}}
"""


def _source(*, column_label: str = "Order") -> dict:
    """Semantic source corpus: one page linked to one module, v1 page doc."""
    return {
        "pages": [
            {
                "schema_version": "mozaiks.app_page.v1",
                "name": "orders",
                "route": "/orders",
                "title": "Orders",
                "page_type": "record_list",
                "layout": "full-width",
                "sections": [
                    {
                        "id": "orders-list",
                        "primitive": "DataTable",
                        "config": {
                            "columns": [{"key": "order_id", "label": column_label}],
                            "api_endpoint": "/api/modules/orders/list_orders",
                        },
                    },
                    {
                        "id": "create-order",
                        "primitive": "Form",
                        "config": {
                            "fields": [
                                {"name": "customer_name", "label": "Customer", "type": "text"}
                            ],
                            "submit_action": {
                                "label": "Create Order",
                                "action_type": "submit",
                                "href": "/api/modules/orders/create_order",
                            },
                        },
                    },
                ],
            }
        ],
        "modules": [
            {
                "manifest": {
                    "module": {"id": "orders", "description": "Order management"},
                    "actions": [
                        {"id": "list_orders", "description": "List orders."},
                        {"id": "create_order", "description": "Create an order."},
                    ],
                }
            }
        ],
    }


def _registry():
    return build_app_layout_registry(())


def _build(source: dict, *, graph_id: str = "slice4c"):
    result = project_semantic_graph(
        source, graph_id=graph_id, version=1, scope=_SCOPE, taxonomy_registry=_pinned_registry()
    )
    plan = derive_compilation_plan(
        graph=result.graph, payloads=result.payloads, registry=_registry()
    )
    return result, plan


def _selection(**overrides) -> RendererSelection:
    fields = {
        "materializer_id": MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR,
        "implementation_id": PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID,
        "implementation_version": PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION,
        "artifact_families": (PAGE_SCHEMA_FAMILY,),
    }
    fields.update(overrides)
    return RendererSelection(**fields)


def _binding(graph, selection: RendererSelection | None = None):
    return build_implementation_binding(
        binding_id="slice4c_binding",
        version=1,
        scope=_SCOPE,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=graph.scope,
        ),
        renderer_selections=(selection if selection is not None else _selection(),),
    )


def _opaque(family: str, path: str, content: bytes) -> PreservedOpaqueArtifact:
    return PreservedOpaqueArtifact(
        contract_ref=ChildContractRef(
            subject_id=path.replace("/", "_").replace(".", "_"),
            subject_version=1,
            scope=_SCOPE,
            content_digest=hashlib.sha256(content).hexdigest(),
            artifact_family=family,
            canonical_relative_path=path,
            contract_schema_version="mozaiks.child_contract.v1",
        ),
        content=content,
    )


def _preserved_artifacts() -> tuple[PreservedOpaqueArtifact, ...]:
    return (
        _opaque("module_backend_handler", "modules/orders/backend/handler.py", _HANDLER_BYTES),
        _opaque("module_backend_service", "modules/orders/backend/service.py", _SERVICE_BYTES),
    )


def _materialize(source: dict):
    result, plan = _build(source)
    bundle = materialize_plan(
        plan=plan,
        graph=result.graph,
        payloads=result.payloads,
        binding=_binding(result.graph),
        layout_registry=_registry(),
        preserved_artifacts=_preserved_artifacts(),
    )
    return result, plan, bundle


def _authored_skeleton() -> dict[str, str]:
    """Families whose legitimate disposition today is a typed gap or authored
    content — never produced by the renderer, never overlapping plan outputs."""
    return {
        "app.json": json.dumps(
            {
                "appId": "slice4c-orders",
                "appName": "Slice4C Orders",
                "version": "1.0.0",
                "startup": {"landing_spot": "/orders"},
            }
        ),
        "config/ai.json": json.dumps({"chat": {"chat_startup_mode": "ask"}}),
        "config/shell.json": json.dumps({"navigation": {"autoFromPages": True}}),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "path": "/orders",
                        "component": "SchemaPage",
                        "label": "Orders",
                        "schema": "orders",
                    }
                ]
            }
        ),
        "data/contract.json": json.dumps(
            {
                "version": "1",
                "app_id": "slice4c-orders",
                "surfaces": [
                    {
                        "surface_id": "orders",
                        "surface_kind": "module",
                        "collections": [{"name": "orders"}],
                    }
                ],
            }
        ),
        "security/secrets.yaml": "version: 1\nsecrets: []\n",
        "modules/orders/module.yaml": (
            "schema_version: mozaiks.module.v1\n"
            "module:\n"
            "  id: orders\n"
            "  display_name: Orders\n"
            "  version: 1.0.0\n"
            "  handler: backend.handler:OrdersModule\n"
            "actions:\n"
            "  - id: list_orders\n"
            "    description: List orders.\n"
            "    handler_method: list_orders\n"
            "    input_schema: {type: object, properties: {}}\n"
            "    output_schema: {type: object}\n"
            "  - id: create_order\n"
            "    description: Create an order.\n"
            "    handler_method: create_order\n"
            "    input_schema:\n"
            "      type: object\n"
            "      required: [customer_name]\n"
            "      properties:\n"
            "        customer_name: {type: string}\n"
            "    output_schema: {type: object}\n"
        ),
        "modules/orders/backend/__init__.py": "",
    }


def _page_unit(plan):
    units = [u for u in plan.units if u.family_kind == PAGE_SCHEMA_FAMILY]
    assert len(units) == 1
    return units[0]


# ---------------------------------------------------------------------------
# Canonical bytes + determinism
# ---------------------------------------------------------------------------


def test_exact_canonical_page_bytes() -> None:
    _result, _plan, bundle = _materialize(_source())
    rendered = bundle.files()["ui/pages/orders.yaml"]
    text = rendered.decode("utf-8")
    assert "\r" not in text
    assert text.endswith("\n")
    assert "{" not in text or "{customer_name" not in text  # no placeholders
    document = yaml.safe_load(text)
    assert list(document)[:7] == [
        "schema_version",
        "name",
        "route",
        "title",
        "page_type",
        "layout",
        "sections",
    ]
    schema = AppPageSchema.model_validate(document)
    assert schema.name == "orders"
    assert schema.route == "/orders"
    assert [section.id for section in schema.sections] == ["orders-list", "create-order"]
    assert schema.sections[0].config["api_endpoint"] == "/api/modules/orders/list_orders"
    # None-valued optionals are omitted, never serialized as nulls.
    assert "shell_mode" not in document
    assert "null" not in text


def test_repeated_materialization_is_byte_identical() -> None:
    _r1, _p1, first = _materialize(_source())
    _r2, _p2, second = _materialize(_source())
    assert first.files() == second.files()
    assert first.plan_digest == second.plan_digest
    assert [o.content_digest for o in first.outputs] == [
        o.content_digest for o in second.outputs
    ]


def test_input_order_invariance() -> None:
    result, plan = _build(_source())
    reference = materialize_plan(
        plan=plan,
        graph=result.graph,
        payloads=result.payloads,
        binding=_binding(result.graph),
        layout_registry=_registry(),
        preserved_artifacts=_preserved_artifacts(),
    )
    reordered = materialize_plan(
        plan=plan,
        graph=result.graph,
        payloads=tuple(reversed(list(result.payloads))),
        binding=_binding(result.graph),
        layout_registry=_registry(),
        preserved_artifacts=tuple(reversed(_preserved_artifacts())),
    )
    assert reference.files() == reordered.files()


def _cross_process_page_digest() -> str:
    """Subprocess entry point: rebuild everything fresh and digest the page."""
    _result, _plan, bundle = _materialize(_source())
    return hashlib.sha256(bundle.files()["ui/pages/orders.yaml"]).hexdigest()


def test_cross_process_determinism() -> None:
    local_digest = _cross_process_page_digest()
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.test_deterministic_page_materialization import "
            "_cross_process_page_digest; print(_cross_process_page_digest())",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    assert completed.stdout.strip() == local_digest


# ---------------------------------------------------------------------------
# Implementation resolution fails closed
# ---------------------------------------------------------------------------


def test_binding_without_page_selection_is_rejected() -> None:
    result, plan = _build(_source())
    binding = build_implementation_binding(
        binding_id="slice4c_binding",
        version=1,
        scope=_SCOPE,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=result.graph.graph_id,
            subject_version=result.graph.version,
            content_digest=result.graph.graph_digest,
            scope=result.graph.scope,
        ),
    )
    with pytest.raises(MaterializationError, match="no renderer selection"):
        materialize_plan(
            plan=plan,
            graph=result.graph,
            payloads=result.payloads,
            binding=binding,
            layout_registry=_registry(),
        )


def test_unaccepted_implementation_identity_is_rejected() -> None:
    result, _plan = _build(_source())
    for override in (
        {"implementation_id": "historical_app_generator"},
        {"implementation_version": "2"},
    ):
        binding = _binding(result.graph, _selection(**override))
        with pytest.raises(MaterializationError, match="unaccepted page renderer"):
            resolve_page_schema_renderer_selection(
                binding, graph=result.graph, layout_registry=_registry()
            )


def test_wrong_materializer_and_wrong_family_are_rejected() -> None:
    result, plan = _build(_source())
    mismatched = _binding(
        result.graph,
        _selection(materializer_id=MaterializerIdentifier.MODULE_CONTRACT_EXECUTOR),
    )
    with pytest.raises(MaterializationError, match="implementation binding rejected"):
        resolve_page_schema_renderer_selection(
            mismatched, graph=result.graph, layout_registry=_registry()
        )
    preserved_unit = next(
        u for u in plan.units if u.disposition is PlanDisposition.PRESERVE_UNOWNED
    )
    payload_by_node = {p.node_id: p for p in result.payloads}
    with pytest.raises(MaterializationError, match="not renderer-ready"):
        render_app_ui_page_schema_unit(unit=preserved_unit, payload_by_node=payload_by_node)


def test_binding_pinned_to_a_different_graph_is_rejected() -> None:
    base, plan = _build(_source())
    other, _other_plan = _build(_source(column_label="Other"), graph_id="slice4c-other")
    with pytest.raises(MaterializationError, match="implementation binding rejected"):
        materialize_plan(
            plan=plan,
            graph=base.graph,
            payloads=base.payloads,
            binding=_binding(other.graph),
            layout_registry=_registry(),
        )


def test_renderer_rejects_page_missing_required_fields() -> None:
    """The runtime completeness guard fails closed on absent required facts."""
    from mozaiksai.core.semantics.compilation_plan import FamilyInstancePlan, PlanSource
    from mozaiksai.core.semantics.payloads import PagePayload, build_semantic_payload

    page = build_semantic_payload(
        PagePayload,
        node_id="mozaiks.page.incomplete",
        payload_version=1,
        scope=_SCOPE,
        page_id="incomplete",
        route=None,
        title="Incomplete",
        intent=None,
        page_type=None,
        layout="full-width",
        shell_mode=None,
        roles=None,
        navigation=None,
        meta=None,
    )
    unit = FamilyInstancePlan(
        unit_id="app_ui_page_schema/incomplete/aaaaaaaaaaaa",
        family_kind=PAGE_SCHEMA_FAMILY,
        family_identity_digest="a" * 64,
        disposition=PlanDisposition.RENDER,
        source_scope="declared",
        placeholder_values=(("page_id", "incomplete"),),
        sources=(PlanSource(node_id=page.node_id, payload_digest=page.payload_digest),),
        materializer="page_schema_executor",
    )
    with pytest.raises(MaterializationError, match="not renderer-input complete"):
        render_app_ui_page_schema_unit(
            unit=unit, payload_by_node={page.node_id: page}
        )


def test_registry_identity_mismatch_is_rejected() -> None:
    result, plan = _build(_source())
    extended = build_app_layout_registry(())
    snapshot = None
    from mozaiksai.core.semantics.compilation_plan import snapshot_layout_registry

    snapshot = snapshot_layout_registry(extended)
    forged = snapshot.model_copy(update={"snapshot_digest": "a" * 64})
    with pytest.raises(MaterializationError):
        materialize_plan(
            plan=plan,
            graph=result.graph,
            payloads=result.payloads,
            binding=_binding(result.graph),
            layout_registry=forged,
        )


# ---------------------------------------------------------------------------
# Output ownership
# ---------------------------------------------------------------------------


def test_outputs_are_exactly_the_plan_assigned_paths() -> None:
    _result, plan, bundle = _materialize(_source())
    page_unit = _page_unit(plan)
    assert bundle.files().keys() == {
        "ui/pages/orders.yaml",
        "modules/orders/backend/handler.py",
        "modules/orders/backend/service.py",
    }
    assert page_unit.outputs[0].path == "ui/pages/orders.yaml"
    origins = {o.path: o.origin for o in bundle.outputs}
    assert origins["ui/pages/orders.yaml"] == "rendered"
    assert origins["modules/orders/backend/handler.py"] == "preserved"


def test_compose_rejects_overlap_and_collisions() -> None:
    _result, _plan, bundle = _materialize(_source())
    with pytest.raises(MaterializationError, match="overlap"):
        compose_bundle(bundle, {"ui/pages/orders.yaml": "authored duplicate"})
    with pytest.raises(ValueError):
        compose_bundle(bundle, {"UI/pages/orders.yaml": "case-fold twin"})
    with pytest.raises(ValueError):
        compose_bundle(bundle, {"ui/pages/orders.yaml/extra.txt": "prefix collision"})


def test_unmatched_preserved_artifact_is_rejected() -> None:
    result, plan = _build(_source())
    stray = _opaque("module_backend_handler", "modules/ghost/backend/handler.py", b"x\n")
    with pytest.raises(MaterializationError, match="matches 0 plan units"):
        materialize_plan(
            plan=plan,
            graph=result.graph,
            payloads=result.payloads,
            binding=_binding(result.graph),
            layout_registry=_registry(),
            preserved_artifacts=(stray,),
        )


# ---------------------------------------------------------------------------
# Preserved opaque artifacts
# ---------------------------------------------------------------------------


def test_preserved_bytes_are_exact_including_empty() -> None:
    result, plan = _build(_source())
    empty = _opaque("module_backend_policy", "modules/orders/backend/policy.py", b"")
    bundle = materialize_plan(
        plan=plan,
        graph=result.graph,
        payloads=result.payloads,
        binding=_binding(result.graph),
        layout_registry=_registry(),
        preserved_artifacts=(*_preserved_artifacts(), empty),
    )
    files = bundle.files()
    assert files["modules/orders/backend/handler.py"] == _HANDLER_BYTES
    assert files["modules/orders/backend/policy.py"] == b""
    # Explicit report: preserved units without supplied bytes are named.
    assert bundle.unsupplied_preserved_units
    assert all(
        unit_id not in bundle.unsupplied_preserved_units
        for unit_id in (o.unit_id for o in bundle.outputs)
    )


def test_mutated_opaque_digest_is_rejected() -> None:
    ref = _opaque("module_backend_handler", "modules/orders/backend/handler.py", _HANDLER_BYTES)
    with pytest.raises(ValueError, match="do not match"):
        PreservedOpaqueArtifact(
            contract_ref=ref.contract_ref, content=_HANDLER_BYTES + b"# tampered\n"
        )


# ---------------------------------------------------------------------------
# Explicit gaps: unsupported families never materialize
# ---------------------------------------------------------------------------


def test_unsupported_families_stay_gapped_and_unrendered() -> None:
    _result, plan, bundle = _materialize(_source())
    files = bundle.files()
    for forbidden in (
        "app.json",
        "modules/orders/module.yaml",
        "config/subscriptions.yaml",
        "data/contract.json",
        "Dockerfile",
    ):
        assert forbidden not in files
    assert bundle.gap_count == len(plan.gaps) > 0
    gap_families = {gap.family_kind for gap in plan.gaps}
    assert "module_manifest" in gap_families or any(
        u.family_kind == "module_manifest" for u in plan.units
    )
    # Deployment stays an external Download-renderer handoff, never rendered.
    deployment_units = [
        u.unit_id
        for u in plan.units
        if u.disposition is PlanDisposition.EXTERNAL_HANDOFF
    ]
    assert set(deployment_units) == set(bundle.external_handoff_units)


# ---------------------------------------------------------------------------
# Recursive closed-domain gate
# ---------------------------------------------------------------------------


def test_recursive_closed_domain_gate_passes_on_current_models() -> None:
    validate_page_renderer_input_closure()


def test_closed_domain_gate_rejects_an_open_model(monkeypatch) -> None:
    from typing import Any as _Any

    from mozaiksai.core.semantics import materialization as module
    from mozaiksai.core.semantics.payloads import SectionPayload as _SectionPayload

    class OpenLeaf(_SectionPayload.__mro__[1].__mro__[0]):  # SemanticsModel base
        blob: dict[str, _Any]

    monkeypatch.setattr(module, "_closure_gate_passed", False)
    original = module._walk_model_closure

    def _walk_with_injection(model, path, violations, visited):
        original(model, path, violations, visited)
        if model is _SectionPayload:
            original(OpenLeaf, f"{path}.injected", violations, visited)

    monkeypatch.setattr(module, "_walk_model_closure", _walk_with_injection)
    with pytest.raises(MaterializationError, match="open annotation"):
        module.validate_page_renderer_input_closure()
    monkeypatch.setattr(module, "_closure_gate_passed", False)


# ---------------------------------------------------------------------------
# Structural isolation: no AppBuildPlan, no AG2, no ambient state
# ---------------------------------------------------------------------------


def test_renderer_module_imports_no_runtime_or_ambient_capabilities() -> None:
    source = (ROOT / "mozaiksai" / "core" / "semantics" / "materialization.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden_roots = (
        "ag2",
        "os",
        "time",
        "datetime",
        "random",
        "uuid",
        "pathlib",
        "subprocess",
        "socket",
        "asyncio",
        "httpx",
        "requests",
        "factory_app",
        "mozaiksai.core.transport",
        "mozaiksai.core.workflow",
        "mozaiksai.core.adapters",
        "mozaiksai.hosts",
    )
    violations = [
        name
        for name in imported
        if any(name == root or name.startswith(root + ".") for root in forbidden_roots)
    ]
    assert violations == [], violations
    # No CODE reference to the retiring plan authority (the module docstring
    # documenting the prohibition is not a reference).
    code_identifiers = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "AppBuildPlan" not in code_identifiers
    assert "app_build_plan" not in code_identifiers
    assert "app_build_plan" not in imported and "AppBuildPlan" not in imported


# ---------------------------------------------------------------------------
# Selective regeneration + successor validation/load/boot
# ---------------------------------------------------------------------------


def _boot_and_assert(
    app_root: Path, monkeypatch: pytest.MonkeyPatch, *, expected_column_label: str
) -> None:
    fake_mongo_client = _FakeMongoClient()
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    monkeypatch.setattr(
        "mozaiksai.hosts.runtime.get_mongo_client", lambda: fake_mongo_client
    )
    monkeypatch.setattr(
        "mozaiksai.core.startup.validation.get_mongo_client", lambda: fake_mongo_client
    )
    reset_auth_adapter()

    from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
    from mozaiksai.hosts import platform

    saved = _save_platform_state(platform)
    platform.executor_registry = ExecutorRegistry()
    platform.app.state.executor_registry = platform.executor_registry
    monkeypatch.setattr(platform.runtime_app, "mongo_client", fake_mongo_client)
    try:
        with TestClient(platform.app, raise_server_exceptions=False) as client:
            health = client.get("/health")
            assert health.status_code == 200, health.text

            page = client.get("/api/pages/orders")
            assert page.status_code == 200, page.text
            body = page.json()
            table = next(s for s in body["sections"] if s["id"] == "orders-list")
            assert table["config"]["columns"][0]["label"] == expected_column_label
            assert table["config"]["api_endpoint"] == "/api/modules/orders/list_orders"

            action = client.post("/api/modules/orders/list_orders", json={"params": {}})
            assert action.status_code == 200, action.text
            assert action.json() == {"orders": []}
    finally:
        _restore_platform_state(platform, saved)


async def _validate_and_load(files: dict[str, bytes], app_root: Path):
    text_files = {path: content.decode("utf-8") for path, content in files.items()}
    assert scan_generated_bundle(text_files) == []
    assert scan_functional_generated_app(text_files) == []
    gate = await run_app_bundle_acceptance_gate(files=text_files)
    assert gate["passed"] is True, gate
    _write_bundle(app_root, text_files)
    loaded = await AppLoader.load(str(app_root))
    assert loaded.failed_module_names == []
    return loaded


@pytest.mark.asyncio
async def test_semantic_page_change_selectively_rematerializes_and_boots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_result, base_plan, base_bundle = _materialize(_source())
    page_unit_id = _page_unit(base_plan).unit_id
    base_files = compose_bundle(base_bundle, _authored_skeleton())
    base_page_bytes = base_files["ui/pages/orders.yaml"]

    await _validate_and_load(base_files, tmp_path / "base_app")
    _boot_and_assert(tmp_path / "base_app", monkeypatch, expected_column_label="Order")

    # ONE linked semantic section mutation; route untouched, manifest untouched.
    successor_result, successor_plan = _build(_source(column_label="Order #"))
    successor_bundle = rematerialize_plan(
        base_bundle=base_bundle,
        base_plan=base_plan,
        successor_plan=successor_plan,
        graph=successor_result.graph,
        payloads=successor_result.payloads,
        binding=_binding(successor_result.graph),
        layout_registry=_registry(),
    )
    closure = successor_bundle.closure
    assert closure is not None

    # The changed linked section makes the page unit affected...
    assert page_unit_id in closure.affected
    assert not closure.added and not closure.removed
    # ...its bytes change...
    successor_files = compose_bundle(successor_bundle, _authored_skeleton())
    assert successor_files["ui/pages/orders.yaml"] != base_page_bytes
    assert b"Order #" in successor_files["ui/pages/orders.yaml"]
    # ...only the affected renderer-ready unit was rerendered...
    origins = {o.path: o.origin for o in successor_bundle.outputs}
    assert origins["ui/pages/orders.yaml"] == "rendered"
    assert origins["modules/orders/backend/handler.py"] == "reused"
    assert origins["modules/orders/backend/service.py"] == "reused"
    # ...unrelated outputs and preserved digests are byte-identical...
    assert successor_files["modules/orders/backend/handler.py"] == _HANDLER_BYTES
    assert successor_files["modules/orders/backend/service.py"] == _SERVICE_BYTES
    base_digests = {o.path: o.content_digest for o in base_bundle.outputs}
    successor_digests = {o.path: o.content_digest for o in successor_bundle.outputs}
    for path in (
        "modules/orders/backend/handler.py",
        "modules/orders/backend/service.py",
    ):
        assert successor_digests[path] == base_digests[path]
    # ...gapped families did not magically materialize...
    assert successor_bundle.files().keys() == base_bundle.files().keys()
    # ...and the successor bundle validates, loads, and boots.
    await _validate_and_load(successor_files, tmp_path / "successor_app")
    _boot_and_assert(
        tmp_path / "successor_app", monkeypatch, expected_column_label="Order #"
    )


def test_unrelated_semantic_change_keeps_page_reusable() -> None:
    base_result, base_plan, base_bundle = _materialize(_source())
    page_unit_id = _page_unit(base_plan).unit_id
    unrelated = _source()
    unrelated["modules"][0]["manifest"]["module"]["description"] = "Order management v2"
    successor_result, successor_plan = _build(unrelated)
    successor_bundle = rematerialize_plan(
        base_bundle=base_bundle,
        base_plan=base_plan,
        successor_plan=successor_plan,
        graph=successor_result.graph,
        payloads=successor_result.payloads,
        binding=_binding(successor_result.graph),
        layout_registry=_registry(),
    )
    assert page_unit_id in successor_bundle.closure.reusable
    assert (
        successor_bundle.files()["ui/pages/orders.yaml"]
        == base_bundle.files()["ui/pages/orders.yaml"]
    )
    origins = {o.path: o.origin for o in successor_bundle.outputs}
    assert origins["ui/pages/orders.yaml"] == "reused"
