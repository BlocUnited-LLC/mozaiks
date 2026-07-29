from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = "docs/architecture/foundations/app-context-and-brownfield-adoption.md"
INTELLIGENCE_DOC_PATH = "docs/architecture/foundations/app-intelligence-plane.md"
INTELLIGENCE_USER_JOURNEY_DOC_PATH = "docs/architecture/foundations/app-intelligence-user-journey.md"
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
    assert "AppIntelligenceSnapshot" in doc
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

    assert "ExistingAppDiscovery` is the onboarding and indexing workflow for existing apps" in normalized
    assert "current_app_context_version_id" in doc
    assert "selects context version and routing" in doc
    assert "`AppGenerator` produces generated app context, but AppGenerator is not the entire build system" in normalized


def test_app_context_doc_defines_source_of_truth_and_staging_boundaries() -> None:
    doc = _read(DOC_PATH)
    normalized = _normalized(doc)

    assert "The existing repo remains the source of truth until explicit transfer" in normalized
    assert "Discovery snapshots are evidence, not authority" in normalized
    assert "Staged patches are proposals" in normalized
    assert "Graph backend mirrors are never source of truth" in normalized


def test_app_context_doc_removes_legacy_placeholder_brownfield_framing() -> None:
    doc = _read(DOC_PATH)
    legacy_light_sequence = "brownfield_" + "build_light"
    legacy_full_sequence = "brownfield_" + "build_full"

    assert legacy_light_sequence not in doc
    assert "brownfield_overlay_generation" not in doc
    assert legacy_full_sequence not in doc
    assert "brownfield_module_generation" not in doc
    assert "native_migration" not in doc
    assert "module_decomposition_plan" not in doc
    assert "private sibling discovery shortcut" not in doc
    assert "AppGenerator-local `code_context` package" not in doc


def test_app_intelligence_plane_doc_defines_artifact_stack_and_agent_policy() -> None:
    doc = _read(INTELLIGENCE_DOC_PATH)
    normalized = _normalized(doc)

    assert "App Intelligence Plane" in doc
    assert "SourceContextBundle" in doc
    assert "AppContextGraph" in doc
    assert "AppIntelligenceSnapshot" in doc
    assert "AppContextVersion" in doc
    assert "Agents do not receive full repositories in prompt context" in normalized
    assert "FalkorDB is the recommended production graph backend" in normalized


def test_app_intelligence_user_journey_doc_defines_production_lifecycle() -> None:
    doc = _read(INTELLIGENCE_USER_JOURNEY_DOC_PATH)
    normalized = _normalized(doc)

    assert "create/import app -> index source -> build graph -> generate/refine -> validate -> review diff -> promote" in normalized
    assert "`repo_clone`" in doc
    assert "`workspace_scan`" in doc
    assert "`symbol_parse`" in doc
    assert "Tree-sitter is the local parser layer" in doc
    assert "FalkorDB is the production relationship-query mirror" in doc


def test_graph_authority_doc_mentions_app_context_graph_without_runtime_authority() -> None:
    doc = _read(GRAPH_DOC_PATH)
    normalized = _normalized(doc)

    assert "AppContextGraph" in doc
    assert "relationship artifact inside the App Intelligence Plane" in normalized
    assert "The in-repo canonical graph snapshot is `AppContextGraph`" in normalized
    assert "It is not the authority for:" in doc
    assert "request routing" in doc
    assert "workflow execution" in doc


def test_app_context_doc_is_registered_in_docs_navigation() -> None:
    target = "architecture/foundations/app-context-and-brownfield-adoption.md"

    assert "foundations/app-intelligence-plane.md" in _read("docs/architecture/index.md")
    assert "foundations/app-intelligence-user-journey.md" in _read("docs/architecture/index.md")
    assert "app-intelligence-plane.md" in _read("docs/architecture/foundations/overview.md")
    assert "app-intelligence-user-journey.md" in _read("docs/architecture/foundations/overview.md")
    assert "architecture/foundations/app-intelligence-plane.md" in _read("mkdocs.yml")
    assert "architecture/foundations/app-intelligence-user-journey.md" in _read("mkdocs.yml")
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

