from __future__ import annotations

import json
from pathlib import Path

import yaml

from mozaiksai.core.app_context.refresh import (
    BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
    CONTEXT_REFRESH_EXPECTED_ARTIFACTS,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "factory_app/workflows/extended_orchestration/extension_registry.json"
CONTEXT_PATH = ROOT / "factory_app/workflows/ExistingAppDiscovery/context_variables.yaml"
APP_CONTEXT_DOC_PATH = ROOT / "docs/architecture/foundations/app-context-and-brownfield-adoption.md"
WORKFLOW_SEQUENCE_DOC_PATH = ROOT / "docs/architecture/workflows/workflow-routing-transitions.md"


def _registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _sequence(sequence_id: str) -> dict:
    for sequence in _registry().get("workflow_sequences", []):
        if sequence.get("id") == sequence_id:
            return sequence
    raise AssertionError(f"Missing workflow_sequence {sequence_id}")


def _step_workflows(sequence: dict) -> list[str]:
    workflows: list[str] = []
    for step in sequence.get("steps", []):
        workflows.extend(step.get("workflows", []))
    return workflows


def test_registry_declares_brownfield_discovery_refresh_sequence() -> None:
    sequence = _sequence(BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE)

    assert sequence["id"] == "brownfield_discovery_refresh"
    assert "non-mutating" in sequence["description"].lower()
    assert "does not run generation" in sequence["description"].lower()


def test_brownfield_discovery_refresh_contains_existing_app_discovery_only() -> None:
    sequence = _sequence(BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE)

    assert sequence["steps"] == [{"workflows": ["ExistingAppDiscovery"]}]
    assert _step_workflows(sequence) == ["ExistingAppDiscovery"]


def test_brownfield_discovery_refresh_excludes_generation_workflows() -> None:
    sequence = _sequence(BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE)
    workflows = set(_step_workflows(sequence))

    assert "AppGenerator" not in workflows
    assert "AgentGenerator" not in workflows
    assert "DesignDocs" not in workflows
    assert not any("transition" in step for step in sequence["steps"])


def test_brownfield_discovery_refresh_affected_families_are_valid_context_families() -> None:
    registry = _registry()
    sequence = _sequence(BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE)
    graph_families = set((registry.get("artifact_dependency_graph") or {}).keys())
    affected = sequence.get("affected_declarative_families") or []

    assert affected == list(CONTEXT_REFRESH_EXPECTED_ARTIFACTS)
    assert set(affected).issubset(graph_families)
    assert "module_decomposition_plan" not in affected
    assert "app_bundle" not in affected
    assert "workflow_bundle" not in affected


def test_context_refresh_plan_sequence_matches_registry() -> None:
    sequence_ids = {
        str(sequence.get("id") or "").strip()
        for sequence in _registry().get("workflow_sequences", [])
    }

    assert BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE in sequence_ids


def test_existing_app_discovery_declares_refresh_context_variables() -> None:
    context = yaml.safe_load(CONTEXT_PATH.read_text(encoding="utf-8")) or {}
    definitions = context["definitions"]
    discovery_vars = set(context["agents"]["DiscoveryHostAgent"]["variables"])
    assembler_vars = set(context["agents"]["DiscoveryArtifactAssemblerAgent"]["variables"])

    for key in (
        "context_refresh_request",
        "current_context_version_id",
        "source_refs",
        "refresh_scope",
        "refresh_reason",
        "expected_artifacts",
    ):
        assert key in definitions
        assert key in discovery_vars
        assert key in assembler_vars

    assert definitions["source_refs"]["type"] == "array"
    assert definitions["expected_artifacts"]["type"] == "array"


def test_legacy_brownfield_build_sequences_are_not_rewired_by_refresh() -> None:
    sequence_map = {
        sequence["id"]: sequence
        for sequence in _registry().get("workflow_sequences", [])
    }
    refresh = sequence_map[BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE]
    legacy_light_sequence = "brownfield_" + "build_light"
    legacy_full_sequence = "brownfield_" + "build_full"

    assert "brownfield_overlay_generation" in sequence_map
    assert "brownfield_module_generation" in sequence_map
    assert legacy_light_sequence not in sequence_map
    assert legacy_full_sequence not in sequence_map
    assert refresh["id"] not in {"brownfield_overlay_generation", "brownfield_module_generation"}
    assert _step_workflows(refresh) == ["ExistingAppDiscovery"]

    app_context_doc = APP_CONTEXT_DOC_PATH.read_text(encoding="utf-8")
    assert f"`{legacy_light_sequence}`" in app_context_doc
    assert "`brownfield_overlay_generation`" in app_context_doc
    assert f"`{legacy_full_sequence}`" in app_context_doc
    assert "`brownfield_module_generation`" in app_context_doc
    assert "renamed" in app_context_doc.lower()


def test_docs_describe_refresh_sequence_as_non_mutating() -> None:
    docs = "\n".join(
        [
            APP_CONTEXT_DOC_PATH.read_text(encoding="utf-8"),
            WORKFLOW_SEQUENCE_DOC_PATH.read_text(encoding="utf-8"),
        ]
    )
    normalized = " ".join(docs.split())

    assert "brownfield_discovery_refresh" in docs
    assert "ExistingAppDiscovery" in docs
    assert "does not run `AppGenerator`, `AgentGenerator`, or `DesignDocs`" in normalized
    assert "does not promote or mutate source repositories" in normalized


def test_brownfield_discovery_refresh_contract_has_no_graph_database_dependency() -> None:
    paths = [
        REGISTRY_PATH,
        CONTEXT_PATH,
        ROOT / "tests/test_brownfield_discovery_refresh_sequence.py",
    ]
    forbidden_terms = ("Fal" + "kor",)

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term.lower() not in text


def test_brownfield_discovery_refresh_contract_has_no_proprietary_examples() -> None:
    paths = [
        REGISTRY_PATH,
        CONTEXT_PATH,
        APP_CONTEXT_DOC_PATH,
        WORKFLOW_SEQUENCE_DOC_PATH,
        ROOT / "tests/test_brownfield_discovery_refresh_sequence.py",
    ]
    forbidden_terms = (
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term.lower() not in text

