from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs/architecture/workflows/ag2-harness-journey-boundary.md"


def test_ag2_boundary_document_names_each_runtime_owner() -> None:
    text = DOC.read_text(encoding="utf-8")

    for required in (
        "AG2 Agent Harness",
        "AG2 Middleware",
        "AG2 Network",
        "mozaiksai.core.session.router",
        "mozaiksai.control_plane",
        "factory_app/refinement_harness",
        "ACP adapter",
        "journey_id",
        "workflow_run_id",
    ):
        assert required in text


def test_ag2_boundary_document_rejects_parallel_orchestration() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "not a second\nAG2 Agent Harness" in text
    assert "must not be used to create an untracked replacement" in text
    assert "Do not add a dedicated AG2-based `RefinementWorkflow`" in text


def test_ag2_boundary_document_links_official_ag2_contracts() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "https://docs.ag2.ai/docs/user-guide/agent_harness/" in text
    assert "https://docs.ag2.ai/docs/user-guide/middleware/" in text
    assert "https://docs.ag2.ai/docs/user-guide/acp/client/" in text
