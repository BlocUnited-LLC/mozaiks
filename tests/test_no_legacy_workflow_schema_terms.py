from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

LEGACY_WORKFLOW_SCHEMA_TERMS = (
    "hooks.yaml",
    "handoffs.yaml",
    "update_agent_state",
    "hook_agent:",
    "hook_type:",
    "handoff_rules",
    "handoff_type:",
    "after_work",
    "parse_hooks_config",
    "parse_handoffs_config",
    "compile_handoffs_to_transition_graph",
    "load_prompt_hook_entries",
    "build_prompt_hook_middleware",
    "MozaiksPromptHookMiddleware",
    "AG2TerminationHandler",
    "detect_terminate_target",
    "get_performance_manager",
    "WorkflowStats",
    "execution.hooks",
    "agents.handoffs",
)

SCAN_ROOTS = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    ".agents",
    ".claude",
    "docs",
    "factory_app",
    "mozaiks_cli",
    "mozaiksai",
    "scripts",
    "tests",
)

SKIP_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "tmp",
}

SKIP_FILES = {
    "tests/test_no_legacy_workflow_schema_terms.py",
}

TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".txt",
    ".yaml",
    ".yml",
}


def _iter_scan_files():
    for root_name in SCAN_ROOTS:
        root = ROOT / root_name
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            if path.relative_to(ROOT).as_posix() in SKIP_FILES:
                continue
            if path.suffix.lower() in TEXT_SUFFIXES:
                yield path


def test_legacy_workflow_schema_terms_do_not_return() -> None:
    hits: list[str] = []
    for path in _iter_scan_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(ROOT).as_posix()
        for term in LEGACY_WORKFLOW_SCHEMA_TERMS:
            if term in text:
                hits.append(f"{rel}: {term}")

    assert not hits, "Prohibited workflow schema terms found:\n" + "\n".join(hits[:200])
