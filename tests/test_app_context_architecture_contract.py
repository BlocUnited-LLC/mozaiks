from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = "docs/architecture/foundations/app-context-and-brownfield-adoption.md"
GRAPH_DOC_PATH = "docs/architecture/foundations/graph-authority-boundaries.md"


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_app_context_architecture_doc_exists_and_defines_core_models() -> None:
    path = ROOT / DOC_PATH
    assert path.exists()

    doc = _read(DOC_PATH)
    lowered = doc.lower()

    assert "AppContextVersion" in doc
    assert "AppContextGraph" in doc
    assert "greenfield" in lowered
    assert "brownfield" in lowered
    assert "hybrid" in lowered


def test_app_context_doc_declares_required_ownership_classes() -> None:
    doc = _read(DOC_PATH)

    for ownership_class in (
        "read_only_discovered",
        "generated_overlay",
        "staged_patch",
        "migrated_owned",
        "external_system",
    ):
        assert ownership_class in doc


def test_app_context_doc_defines_workflow_and_control_plane_boundaries() -> None:
    doc = _read(DOC_PATH)
    normalized = _normalized(doc)

    assert "ExistingAppDiscovery should become the onboarding/indexing context producer" in normalized
    assert "current_app_context_version_id" in doc
    assert "context version selection" in doc
    assert "AppGenerator produces generated app context, but AppGenerator is not the entire build system" in normalized


def test_app_context_doc_defines_source_of_truth_and_staging_boundaries() -> None:
    doc = _read(DOC_PATH)
    normalized = _normalized(doc)

    assert "the brownfield existing repo remains the source of truth until explicit transfer" in normalized
    assert "Discovery snapshots are evidence, not authority" in normalized
    assert "Staged patches are proposals" in normalized
    assert "KG/FalkorDB is optional mirror only" in normalized


def test_app_context_doc_identifies_placeholder_brownfield_concepts_as_not_canonical() -> None:
    doc = _read(DOC_PATH)
    normalized = _normalized(doc)
    legacy_light_sequence = "brownfield_" + "build_light"
    legacy_full_sequence = "brownfield_" + "build_full"

    assert "implementation evidence, not canonical architecture" in normalized
    assert legacy_light_sequence in doc
    assert "brownfield_overlay_generation" in doc
    assert legacy_full_sequence in doc
    assert "brownfield_module_generation" in doc
    assert "native_migration" in doc
    assert "module_decomposition_plan" in doc
    assert "workspace_app" in doc
    assert "private sibling discovery shortcut" in normalized
    assert "hook_workflow_artifacts.py" in doc


def test_graph_authority_doc_mentions_app_context_graph_without_runtime_authority() -> None:
    doc = _read(GRAPH_DOC_PATH)
    normalized = _normalized(doc)

    assert "AppContextGraph" in doc
    assert "FalkorDB remains optional mirror only and non-authoritative" in normalized
    assert "It is derived from authoritative source refs" in normalized
    assert "must not be used for:" in doc
    assert "request routing" in doc
    assert "workflow execution" in doc


def test_app_context_doc_is_registered_in_docs_navigation() -> None:
    target = "architecture/foundations/app-context-and-brownfield-adoption.md"

    assert "foundations/app-context-and-brownfield-adoption.md" in _read("docs/architecture/index.md")
    assert "app-context-and-brownfield-adoption.md" in _read("docs/architecture/foundations/overview.md")
    assert target in _read("mkdocs.yml")


def test_app_context_doc_has_no_proprietary_examples_or_product_concepts() -> None:
    doc = _read(DOC_PATH).lower()

    forbidden = (
        "app zero",
        "app_zero",
        "mozaiks-app",
        "mozaikspay",
        "blocunited app",
    )
    for term in forbidden:
        assert term not in doc
