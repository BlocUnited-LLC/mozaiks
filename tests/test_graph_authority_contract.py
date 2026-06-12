from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_agentgenerator_context_prompt_does_not_emit_kg_fields() -> None:
    agents = _read("factory_app/workflows/AgentGenerator/agents.yaml")
    section = agents.split("- name: WorkflowBundleBuilderAgent", 1)[1].split("- name:", 1)[0]
    lowered = section.lower()

    assert "falkordb" not in lowered
    assert "falkor" not in lowered
    assert "redisgraph" not in lowered
    assert "cypher" not in lowered
    assert "memory graph" not in lowered
    assert "graph_nodes" not in section
    assert "graph_edges" not in section


def test_active_workflow_prompts_do_not_require_falkor_nodes_or_edges() -> None:
    disallowed = (
        "falkordb",
        "falkor",
        "redisgraph",
        "cypher",
        "memory graph",
        "graph_nodes",
        "graph_edges",
    )

    prompt_files = sorted((ROOT / "factory_app" / "workflows").glob("*/agents.yaml"))
    assert prompt_files

    failures: list[str] = []
    for path in prompt_files:
        text = path.read_text(encoding="utf-8").lower()
        for term in disallowed:
            if term in text:
                failures.append(f"{path.relative_to(ROOT)} contains {term!r}")

    assert failures == []


def test_falkordb_is_not_a_declared_dependency() -> None:
    dependency_files = [
        ROOT / "pyproject.toml",
        ROOT / "requirements.txt",
        ROOT / "requirements-docs.txt",
    ]

    failures: list[str] = []
    for path in dependency_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for term in ("falkor", "falkordb", "redisgraph"):
            if term in text:
                failures.append(f"{path.relative_to(ROOT)} contains {term!r}")

    assert failures == []


def test_appgenerator_local_code_context_subsystem_was_removed() -> None:
    assert not (ROOT / "factory_app/workflows/AppGenerator/tools/code_context").exists()
    assert not (ROOT / "factory_app/workflows/AppGenerator/tools/hook_code_context.py").exists()


def test_runtime_paths_do_not_import_falkordb() -> None:
    paths = list((ROOT / "mozaiksai").rglob("*.py"))

    failures: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        if "falkordb" in text or "falkor" in text:
            failures.append(str(path.relative_to(ROOT)))

    assert failures == []


def test_graph_authority_doc_declares_matrix_and_kg_boundaries() -> None:
    doc = _read("docs/architecture/foundations/graph-authority-boundaries.md")
    lowered = doc.lower()

    assert "| graph | source of truth | runtime critical? | falkordb role |" in lowered
    assert "`artifact_dependency_graph`" in doc
    assert "`workflow_sequence` / handoffs" in doc
    assert "control-plane refinement impact graph" in lowered
    assert "module event/reaction/notification graph" in lowered
    assert "ui route/component graph" in lowered
    assert "context graph intelligence layer" in lowered
    assert "integration readiness graph" in lowered
    assert "primary intelligence layer" in lowered
    assert "optional backend mirror" in lowered

    forbidden_runtime_authorities = [
        "request routing",
        "workflow execution",
        "ag2 handoffs",
        "module action execution",
        "event dispatch",
        "permission or entitlement enforcement",
        "payment or billing enforcement",
        "connector secret storage",
        "generated app database access",
        "ui route rendering",
    ]
    for phrase in forbidden_runtime_authorities:
        assert phrase in lowered

    assert "never store connector secrets" in lowered
    assert "avoid proprietary provider or hosted-product examples" in lowered


def test_graph_authority_doc_is_registered_in_docs_navigation() -> None:
    target = "architecture/foundations/graph-authority-boundaries.md"

    assert "foundations/graph-authority-boundaries.md" in _read("docs/architecture/index.md")
    assert "graph-authority-boundaries.md" in _read("docs/architecture/foundations/overview.md")
    assert target in _read("mkdocs.yml")

