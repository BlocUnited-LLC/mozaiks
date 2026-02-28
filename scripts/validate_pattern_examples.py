from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import yaml


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOB = "docs/pattern_examples/*.yaml"


REQUIRED_LAYER_MARKERS: Sequence[str] = (
    "LAYER 0",
    "LAYER 1",
    "LAYER 2a",
    "LAYER 2b",
    "LAYER 3a",
    "LAYER 3b",
    "LAYER 4",
    "LAYER 5",
    "LAYER 6",
    "LAYER 7a",
    "LAYER 7b",
    "LAYER 7c",
    "LAYER 8",
    "LAYER 9",
    "LAYER 10",
    "LAYER 11",
    "LAYER 12",
    "LAYER 13",
)


@dataclass(frozen=True)
class ValidationIssue:
    file: Path
    message: str


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _load_yaml_documents(path: Path) -> List[Any]:
    text = _read_text(path)
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse error: {exc}") from exc

    # Drop empty docs (e.g., trailing separators)
    return [d for d in docs if d is not None]


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _has_key(doc: Any, key: str) -> bool:
    return _is_mapping(doc) and key in doc


def _expect_layer_markers(text: str, path: Path) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for marker in REQUIRED_LAYER_MARKERS:
        if marker not in text:
            issues.append(ValidationIssue(path, f"Missing marker comment '{marker}'"))
    return issues


def _expect_min_shapes(docs: Sequence[Any], path: Path) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []

    def require_root_key(key: str) -> None:
        if not any(_has_key(d, key) for d in docs):
            issues.append(ValidationIssue(path, f"Missing YAML document with root key '{key}'"))

    require_root_key("PatternSelection")
    require_root_key("WorkflowStrategy")
    require_root_key("StateArchitecture")
    require_root_key("UXArchitecture")
    require_root_key("AgentRoster")
    require_root_key("ToolPlanning")
    require_root_key("ContextVariablesPlan")
    require_root_key("PackMetadata")

    # MermaidSequenceDiagram doc must include both MermaidSequenceDiagram and agent_message
    has_mermaid_doc = any(
        _is_mapping(d) and ("MermaidSequenceDiagram" in d) and ("agent_message" in d)
        for d in docs
    )
    if not has_mermaid_doc:
        issues.append(
            ValidationIssue(
                path,
                "Missing YAML document containing both 'MermaidSequenceDiagram' and 'agent_message'",
            )
        )

    # ToolsManifestOutput shape: top-level tools + lifecycle_tools
    has_tools_manifest = any(
        _is_mapping(d) and ("tools" in d) and ("lifecycle_tools" in d) for d in docs
    )
    if not has_tools_manifest:
        issues.append(ValidationIssue(path, "Missing ToolsManagerAgent doc with keys: tools, lifecycle_tools"))

    # HookFilesOutput shape: hook_files list
    if not any(_has_key(d, "hook_files") for d in docs):
        issues.append(ValidationIssue(path, "Missing HookAgent doc with key: hook_files"))

    # RuntimeAgentsOutput shape: agents list
    if not any(_has_key(d, "agents") for d in docs):
        issues.append(ValidationIssue(path, "Missing AgentsAgent doc with key: agents"))

    # OrchestrationConfigOutput shape: required scalar keys
    orchestration_required = {
        "workflow_name",
        "max_turns",
        "human_in_the_loop",
        "startup_mode",
        "orchestration_pattern",
        "initial_agent",
        "visual_agents",
    }
    has_orchestration_doc = any(
        _is_mapping(d) and orchestration_required.issubset(set(d.keys())) for d in docs
    )
    if not has_orchestration_doc:
        issues.append(
            ValidationIssue(
                path,
                "Missing OrchestratorAgent doc with keys: "
                + ", ".join(sorted(orchestration_required)),
            )
        )

    # HandoffRulesOutput shape
    if not any(_has_key(d, "handoff_rules") for d in docs):
        issues.append(ValidationIssue(path, "Missing HandoffsAgent doc with key: handoff_rules"))

    # StructuredModelsOutput shape
    has_structured_outputs = any(
        _is_mapping(d) and ("models" in d) and ("registry" in d) for d in docs
    )
    if not has_structured_outputs:
        issues.append(ValidationIssue(path, "Missing StructuredOutputsAgent doc with keys: models, registry"))

    # DownloadRequestOutput shape: a doc that is ONLY agent_message (to distinguish from layer 4)
    has_download_only_doc = any(_is_mapping(d) and set(d.keys()) == {"agent_message"} for d in docs)
    if not has_download_only_doc:
        issues.append(ValidationIssue(path, "Missing DownloadAgent doc (single key: agent_message)"))

    return issues


def validate_file(path: Path) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []

    text = _read_text(path)
    issues.extend(_expect_layer_markers(text, path))

    try:
        docs = _load_yaml_documents(path)
    except ValueError as exc:
        return [ValidationIssue(path, str(exc))]

    issues.extend(_expect_min_shapes(docs, path))
    return issues


def iter_files(glob_expr: str) -> Iterable[Path]:
    for path in WORKSPACE_ROOT.glob(glob_expr):
        if path.is_file():
            yield path


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate docs/pattern_examples YAML examples are full Layer 0–13 outputs.")
    parser.add_argument(
        "--glob",
        default=DEFAULT_GLOB,
        help=f"Glob (relative to repo root). Default: {DEFAULT_GLOB}",
    )
    args = parser.parse_args(list(argv))

    files = list(iter_files(args.glob))
    if not files:
        print(f"No files matched glob: {args.glob}")
        return 2

    all_issues: List[ValidationIssue] = []
    for file_path in files:
        all_issues.extend(validate_file(file_path))

    if all_issues:
        for issue in all_issues:
            rel = issue.file.relative_to(WORKSPACE_ROOT)
            print(f"{rel}: {issue.message}")
        print(f"\nFAILED: {len(all_issues)} issue(s) across {len(files)} file(s)")
        return 1

    print(f"OK: {len(files)} pattern example YAML file(s) validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
