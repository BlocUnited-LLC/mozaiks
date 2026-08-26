from __future__ import annotations

import json
from pathlib import Path

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_refinement_harness_manifest() -> dict:
    return yaml.safe_load(
        (
            _repo_root()
            / "factory_app"
            / "refinement_harness"
            / "config"
            / "harness.yaml"
        ).read_text(encoding="utf-8")
    )


def _load_extension_registry() -> dict:
    return json.loads(
        (
            _repo_root()
            / "factory_app"
            / "workflows"
            / "extended_orchestration"
            / "extension_registry.json"
        ).read_text(encoding="utf-8")
    )


def test_refinement_routes_reference_declared_workflow_sequences() -> None:
    harness = _load_refinement_harness_manifest()
    registry = _load_extension_registry()
    sequence_ids = {
        str(sequence.get("id") or "").strip()
        for sequence in registry.get("workflow_sequences", [])
    }

    missing: list[str] = []
    for artifact in harness["routing"]["artifacts"]:
        build_family = artifact["build_family"]
        for change_class, route in artifact["routes"].items():
            sequence_id = str(route.get("workflow_sequence") or "").strip()
            if sequence_id not in sequence_ids:
                missing.append(f"{build_family}.{change_class}->{sequence_id}")

    assert missing == []


def test_workflow_sequence_impact_families_match_artifact_dependency_graph() -> None:
    registry = _load_extension_registry()
    graph = registry.get("artifact_dependency_graph") or {}
    graph_families = set(graph.keys())

    unknown_sequence_families: list[str] = []
    for sequence in registry.get("workflow_sequences", []):
        sequence_id = sequence["id"]
        for family in sequence.get("affected_declarative_families", []):
            if family not in graph_families:
                unknown_sequence_families.append(f"{sequence_id}:{family}")

    unknown_graph_edges: list[str] = []
    for family, dependencies in graph.items():
        if family not in graph_families:
            unknown_graph_edges.append(f"{family}:<missing-node>")
        for dependency in dependencies:
            if dependency not in graph_families:
                unknown_graph_edges.append(f"{family}->{dependency}")

    assert unknown_sequence_families == []
    assert unknown_graph_edges == []


def test_artifact_graph_uses_persisted_theme_family_without_phantoms() -> None:
    registry = _load_extension_registry()
    graph = registry.get("artifact_dependency_graph") or {}

    assert graph["theme_capture"] == ["concept"]
    assert "theme_capture" in graph["app_bundle"]
    assert {"experience_spec", "build_plan", "brand", "theme_config"}.isdisjoint(graph)


def test_sequences_use_persisted_design_and_theme_families() -> None:
    registry = _load_extension_registry()
    sequences = {
        sequence["id"]: sequence.get("affected_declarative_families", [])
        for sequence in registry.get("workflow_sequences", [])
    }

    assert "theme_capture" in sequences["build"]
    assert "theme_capture" in sequences["full_rebuild"]
    assert sequences["app_surface_revision"] == ["design_docs", "app_bundle"]
    assert sequences["theme_revision"] == ["theme_capture", "app_bundle"]
    assert sequences["theme_patch"] == ["theme_capture", "app_bundle"]
    for families in sequences.values():
        assert {"experience_spec", "build_plan", "brand", "theme_config"}.isdisjoint(families)


def test_refinement_docs_define_experience_spec_as_first_class_intent_artifact() -> None:
    content = (
        _repo_root()
        / "docs"
        / "architecture"
        / "workflows"
        / "refinement-engine.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(content.split())

    assert "ExperienceSpec is the first-class experience intent artifact" in normalized
    assert "Page YAML" in normalized
    assert "route manifests" in normalized
    assert "custom React routes" in normalized
    assert "shell navigation are downstream" in normalized
    assert "Small UI patch requests may bypass ExperienceSpec" in normalized


def test_refinement_contract_examples_remain_provider_neutral() -> None:
    touched_content = "\n".join(
        [
            (
                _repo_root()
                / "docs"
                / "architecture"
                / "workflows"
                / "refinement-engine.md"
            ).read_text(encoding="utf-8"),
            json.dumps(_load_extension_registry()),
        ]
    ).lower()
    forbidden_terms = [
        "mozaikspay",
        "payment_provider",
        "billing",
        "wallet",
        "entitlement",
        "usage",
        "investor",
        "app zero",
        "payout",
    ]

    assert [term for term in forbidden_terms if term in touched_content] == []

