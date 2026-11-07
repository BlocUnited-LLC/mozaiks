"""Reconstruct the registry used by historical semantic migration proofs."""

from mozaiksai.core.runtime.app.layout_registry import (
    AppLayoutRegistry,
    _stable_digest,
    build_app_layout_registry,
)

SERVICE_PACKAGE_MARKER_PATHS = frozenset({
    "services/__init__.py",
    "services/integrations/__init__.py",
    "services/routes/__init__.py",
    "services/adapters/__init__.py",
    "services/adapters/{adapter_area}/__init__.py",
})

# Registry families added after the historical corpus captures. Excluded with
# the package markers so pinned migration proofs keep reconstructing exactly
# the registry they were captured against.
POST_BASELINE_FAMILY_PATHS = SERVICE_PACKAGE_MARKER_PATHS | frozenset({
    "config/metrics.yaml",
    # Registered after these captures for the same reason as metrics.yaml: the
    # platform's profile-layout endpoint reads app/config/profile.yaml and the
    # scanner was rejecting it as noncanonical.
    "config/profile.yaml",
    # Build-time smoke fixtures were registered after these historical captures.
    "modules/{module_id}/contracts/fixtures.yaml",
    # A plugin module's declaration of the end-user account connection it
    # needs (connected accounts), at app scope and module scope.
    "modules/{module_id}/contracts/account_connections.yaml",
    "contracts/account_connections.yaml",
})


def registry_before_service_package_markers() -> AppLayoutRegistry:
    current = build_app_layout_registry(())
    families = tuple(
        family for family in current.families
        if family.path_template not in POST_BASELINE_FAMILY_PATHS
    )
    return AppLayoutRegistry(
        families=families,
        registry_digest=_stable_digest({
            "schema_version": current.schema_version,
            "families": [family.identity_payload for family in families],
        }),
    )


def post_baseline_row_digests() -> frozenset[str]:
    """Row digests of the families registered after the historical captures."""
    from mozaiksai.core.semantics.compilation_plan import snapshot_layout_registry

    return frozenset(
        row.row_digest for row in snapshot_layout_registry(build_app_layout_registry(())).rows
        if row.path_template in POST_BASELINE_FAMILY_PATHS
    )


def historical_units(plan):
    """*plan*'s units without those post-baseline families added.

    In the app-scope corpus a module-scope family can only add an
    inapplicable scope-inactive unit that carries nothing; that is asserted
    here, so such a family cannot hide any other change to the plan.
    """
    from mozaiksai.core.semantics.compilation_plan import PlanDisposition

    added = post_baseline_row_digests()
    kept = []
    for unit in plan.units:
        if unit.family_identity_digest not in added:
            kept.append(unit)
            continue
        assert unit.unit_id == f"{unit.family_kind}/scope_inactive/{unit.family_identity_digest[:12]}"
        assert unit.disposition is PlanDisposition.INAPPLICABLE
        assert unit.outputs == unit.placeholder_values == unit.sources == unit.edge_sources == ()
    return kept


def corpus_plan_before_service_package_markers():
    from mozaiksai.core.semantics.compilation_plan import derive_compilation_plan
    from tests.test_semantic_payload_graph_v2 import _corpus_graph

    graph, payloads = _corpus_graph()
    return derive_compilation_plan(
        graph=graph, payloads=payloads, registry=registry_before_service_package_markers(),
    )
