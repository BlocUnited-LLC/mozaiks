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


def registry_before_service_package_markers() -> AppLayoutRegistry:
    current = build_app_layout_registry(())
    families = tuple(
        family for family in current.families
        if family.path_template not in SERVICE_PACKAGE_MARKER_PATHS
    )
    return AppLayoutRegistry(
        families=families,
        registry_digest=_stable_digest({
            "schema_version": current.schema_version,
            "families": [family.identity_payload for family in families],
        }),
    )


def corpus_plan_before_service_package_markers():
    from mozaiksai.core.semantics.compilation_plan import derive_compilation_plan
    from tests.test_semantic_payload_graph_v2 import _corpus_graph

    graph, payloads = _corpus_graph()
    return derive_compilation_plan(
        graph=graph, payloads=payloads, registry=registry_before_service_package_markers(),
    )
