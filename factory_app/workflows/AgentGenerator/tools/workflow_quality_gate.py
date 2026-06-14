"""Quality gate for AgentGenerator workflow bundle outputs.

This module validates generated workflow bundle entries before they are packaged,
downloaded, registered, or promoted. It intentionally checks both contract shape
and semantic drift: generated YAML can be parseable and still lose the workflow
meaning the prompts asked for.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from factory_app.workflows._shared.workflow_integration import workflow_name_to_capability_id

REQUIRED_WORKFLOW_FILES = {
    "orchestrator.yaml",
    "agents.yaml",
    "transition_graph.yaml",
    "context_variables.yaml",
    "structured_outputs.yaml",
    "tools.yaml",
    "middleware.yaml",
    "ui_config.yaml",
}
SUPPORTED_TRANSITION_CONDITIONS = {"context_equals", "context_expression", "tool_called"}
EVENT_PREFIXES = ("domain.", "platform.", "hosted.")
WORKFLOW_BUNDLE_BUILDER_PROMPT_SURFACE = (
    "factory_app/workflows/AgentGenerator/agents.yaml#WorkflowBundleBuilderAgent"
)


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            return default if value is None else value
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
            return
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value


def _workflow_result_entries(workflow_bundle_results: Any) -> list[dict[str, Any]]:
    if not isinstance(workflow_bundle_results, dict):
        return []
    return [
        value
        for key, value in workflow_bundle_results.items()
        if isinstance(key, str) and not key.startswith("_") and isinstance(value, dict)
    ]


def workflow_bundle_entries_from_context(context_variables: Any | None) -> list[dict[str, Any]]:
    return _workflow_result_entries(_context_get(context_variables, "workflow_bundle_results"))


def expected_workflows_from_context(context_variables: Any | None) -> list[dict[str, Any]]:
    workflows_spec = _context_get(context_variables, "workflows_spec")
    if isinstance(workflows_spec, list):
        return [dict(item) for item in workflows_spec if isinstance(item, dict)]

    pattern_selection = _context_get(context_variables, "PatternSelection")
    if isinstance(pattern_selection, dict):
        workflows = pattern_selection.get("workflows")
        if isinstance(workflows, list):
            return [dict(item) for item in workflows if isinstance(item, dict)]

    return []


def load_bundle_entries_from_root(
    bundle_root: Path,
    expected_workflows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for spec in expected_workflows:
        workflow_name = str(spec.get("name") or "").strip()
        if not workflow_name:
            continue
        workflow_dir = bundle_root / workflow_name
        files: list[dict[str, Any]] = []
        if workflow_dir.is_dir():
            for path in sorted(workflow_dir.rglob("*")):
                if path.is_file():
                    files.append(
                        {
                            "filename": str(path.relative_to(workflow_dir)).replace("\\", "/"),
                            "content": path.read_text(encoding="utf-8-sig"),
                        }
                    )
        entries.append({"workflow_name": workflow_name, "files": files})
    return entries


def _safe_relpath(raw_path: Any) -> str | None:
    text = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
    if not text or "\x00" in text:
        return None
    parsed = Path(text)
    if parsed.is_absolute():
        return None
    if any(part in {"", ".", "..", "_shared"} for part in text.split("/")):
        return None
    return text


def _files_by_name(entry: dict[str, Any]) -> dict[str, str]:
    files = entry.get("files")
    if not isinstance(files, list):
        return {}
    resolved: dict[str, str] = {}
    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        filename = _safe_relpath(file_entry.get("filename"))
        if not filename:
            continue
        resolved[filename] = str(file_entry.get("content") or "")
    return resolved


def _yaml_payloads_from_files(files: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    payloads: dict[str, Any] = {}
    errors: list[str] = []
    for relpath, content in sorted(files.items()):
        if not relpath.endswith(".yaml"):
            continue
        try:
            payloads[relpath] = yaml.safe_load(content) or {}
        except Exception as exc:
            errors.append(f"{relpath} is not valid YAML: {exc}")
    return payloads, errors


def _read_agent_names(agents_payload: Any) -> list[str]:
    agents = agents_payload.get("agents") if isinstance(agents_payload, dict) else agents_payload
    if isinstance(agents, dict):
        return [str(name) for name in agents if str(name or "").strip()]
    if isinstance(agents, list):
        return [
            str(agent.get("name"))
            for agent in agents
            if isinstance(agent, dict) and str(agent.get("name") or "").strip()
        ]
    return []


def _event_type_from_trigger(trigger: Any) -> str | None:
    if not isinstance(trigger, dict):
        return None
    for key in ("event", "event_type"):
        value = str(trigger.get(key) or "").strip()
        if value.startswith(EVENT_PREFIXES):
            return value
    trigger_type = str(trigger.get("type") or "").strip()
    if trigger_type.startswith(EVENT_PREFIXES):
        return trigger_type
    return None


def _is_generic_trigger_description(value: Any) -> bool:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not text:
        return True
    if any(marker in text for marker in ("todo", "tbd", "placeholder", "example", "lorem")):
        return True
    if text in {
        "event trigger",
        "trigger event",
        "workflow trigger",
        "start workflow",
        "starts workflow",
        "trigger the workflow",
    }:
        return True
    return bool(re.fullmatch(r"trigger for [a-z0-9_. -]+ event\.?", text))


def _semantic_tokens(*values: Any) -> set[str]:
    stop_words = {
        "after",
        "agent",
        "automation",
        "event",
        "from",
        "into",
        "requested",
        "requests",
        "runs",
        "start",
        "the",
        "this",
        "through",
        "trigger",
        "workflow",
    }
    tokens: set[str] = set()
    for value in values:
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower()):
            if len(token) >= 4 and token not in stop_words:
                tokens.add(token)
    return tokens


def _issue(
    *,
    check_id: str,
    file: str,
    expected: Any,
    observed: Any,
    fix_suggestion: str,
    severity: str = "error",
    prompt_surface: str = WORKFLOW_BUNDLE_BUILDER_PROMPT_SURFACE,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "check_id": check_id,
        "file": file,
        "expected": expected,
        "observed": observed,
        "prompt_surface": prompt_surface,
        "fix_suggestion": fix_suggestion,
    }


def _issue_summary(workflow_name: str, issue: dict[str, Any]) -> str:
    return (
        f"{workflow_name}: {issue['check_id']} in {issue['file']}: "
        f"expected {issue['expected']!r}, observed {issue['observed']!r}; "
        f"prompt_surface={issue['prompt_surface']}"
    )


def _expected_by_name(
    *,
    bundle_entries: list[dict[str, Any]],
    expected_workflows: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    expected = {
        str(item.get("name") or item.get("workflow_name") or "").strip(): dict(item)
        for item in expected_workflows or []
        if isinstance(item, dict) and str(item.get("name") or item.get("workflow_name") or "").strip()
    }
    for entry in bundle_entries:
        workflow_name = str(entry.get("workflow_name") or "").strip()
        if workflow_name and workflow_name not in expected:
            expected[workflow_name] = {"name": workflow_name, "context_variables": {}}
    return expected


def validate_workflow_bundle_structure(
    *,
    bundle_entries: list[dict[str, Any]],
    expected_workflows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from mozaiksai.core.workflow.execution.network_graph import compile_transition_rules_to_graph
    from mozaiksai.core.workflow.task_batches import parse_task_batches_config

    errors: list[str] = []
    workflow_reports: list[dict[str, Any]] = []
    expected = _expected_by_name(bundle_entries=bundle_entries, expected_workflows=expected_workflows)
    entries_by_name = {
        str(entry.get("workflow_name") or "").strip(): entry
        for entry in bundle_entries
        if str(entry.get("workflow_name") or "").strip()
    }

    for workflow_name, spec in expected.items():
        entry = entries_by_name.get(workflow_name, {"workflow_name": workflow_name, "files": []})
        report: dict[str, Any] = {"workflow_name": workflow_name, "errors": []}
        workflow_reports.append(report)

        files = _files_by_name(entry)
        emitted_files = set(files)
        missing = sorted(REQUIRED_WORKFLOW_FILES.difference(emitted_files))
        if missing:
            report["errors"].append(f"missing required workflow files: {missing}")

        stale_files = sorted({"handoffs.yaml", "hooks.yaml"}.intersection(emitted_files))
        if stale_files:
            report["errors"].append(f"stale workflow files emitted: {stale_files}")

        payloads, yaml_errors = _yaml_payloads_from_files(files)
        report["errors"].extend(yaml_errors)

        orchestrator = payloads.get("orchestrator.yaml")
        if isinstance(orchestrator, dict):
            if orchestrator.get("workflow_name") != workflow_name:
                report["errors"].append(
                    f"orchestrator.workflow_name={orchestrator.get('workflow_name')!r} "
                    f"does not match {workflow_name!r}"
                )
            if "startup_mode" in orchestrator:
                report["errors"].append("orchestrator.yaml must not use startup_mode")
            if "visual_agents" in orchestrator:
                report["errors"].append("orchestrator.yaml must not contain visual_agents; use ui_config.yaml")
            startup_mode = orchestrator.get("workflow_startup_mode")
            if not startup_mode:
                report["errors"].append("orchestrator.yaml missing workflow_startup_mode")
            expected_mode = (spec.get("context_variables") or {}).get("expected_workflow_startup_mode")
            if expected_mode and startup_mode and startup_mode != expected_mode:
                report["errors"].append(
                    f"workflow_startup_mode={startup_mode!r} does not match expected {expected_mode!r}"
                )
            trigger_keys = {"type", "event", "capability_id", "endpoint", "method", "description"}
            triggers = orchestrator.get("triggers")
            if isinstance(triggers, list):
                for index, trigger in enumerate(triggers):
                    if not isinstance(trigger, dict):
                        report["errors"].append(f"orchestrator trigger {index} must be a mapping")
                        continue
                    extra_keys = sorted(set(trigger).difference(trigger_keys))
                    if extra_keys:
                        report["errors"].append(
                            f"orchestrator trigger {index} uses unsupported keys: {extra_keys}"
                        )
        else:
            report["errors"].append("orchestrator.yaml must contain a mapping")
            startup_mode = None

        transition_graph = payloads.get("transition_graph.yaml")
        agents_payload = payloads.get("agents.yaml")
        if isinstance(transition_graph, dict):
            transition_rules = transition_graph.get("transition_rules")
            if not isinstance(transition_rules, list) or not transition_rules:
                report["errors"].append("transition_graph.yaml must define non-empty transition_rules")
            else:
                for index, rule in enumerate(transition_rules):
                    if not isinstance(rule, dict):
                        report["errors"].append(f"transition rule {index} must be a mapping")
                        continue
                    if "condition" in rule:
                        report["errors"].append(f"transition rule {index} uses removed condition field")
                    if str(rule.get("transition_type") or "").strip() == "condition":
                        condition_type = str(rule.get("condition_type") or "").strip()
                        if condition_type not in SUPPORTED_TRANSITION_CONDITIONS:
                            report["errors"].append(
                                f"transition rule {index} uses unsupported condition_type={condition_type!r}"
                            )
                agent_names = _read_agent_names(agents_payload)
                initial_agent = str(orchestrator.get("initial_agent") or "").strip() if isinstance(orchestrator, dict) else ""
                if agent_names and initial_agent:
                    try:
                        compile_transition_rules_to_graph(
                            transition_rules,
                            initial_agent_name=initial_agent,
                            agent_id_by_name={name: name for name in agent_names},
                            max_turns=orchestrator.get("max_turns") if isinstance(orchestrator, dict) else None,
                        )
                    except Exception as exc:
                        report["errors"].append(f"transition graph does not compile through AG2 adapter: {exc}")
        else:
            report["errors"].append("transition_graph.yaml must contain a mapping")

        ui_config = payloads.get("ui_config.yaml")
        if isinstance(ui_config, dict):
            visual_agents = ui_config.get("visual_agents")
            if startup_mode == "BackendOnly" and visual_agents not in (None, []):
                report["errors"].append("BackendOnly workflow must not expose visual_agents")
        else:
            report["errors"].append("ui_config.yaml must contain a mapping")

        context_variables = payloads.get("context_variables.yaml")
        context_definitions = (
            set(context_variables.get("definitions") or {})
            if isinstance(context_variables, dict)
            else set()
        )
        if not isinstance(context_variables, dict):
            report["errors"].append("context_variables.yaml must contain a mapping")

        task_batches = payloads.get("extended_orchestration/task_batches.yaml")
        requires_task_batches = bool((spec.get("context_variables") or {}).get("require_task_batches"))
        if requires_task_batches and not isinstance(task_batches, dict):
            report["errors"].append("required extended_orchestration/task_batches.yaml missing")
        if isinstance(task_batches, dict):
            try:
                parsed = parse_task_batches_config(task_batches)
                if not parsed.conveyors:
                    report["errors"].append("task_batches.yaml must declare conveyors[]")
                if not parsed.batches:
                    report["errors"].append("task_batches.yaml did not materialize executable batches")
                expected_task_batch_id = (spec.get("context_variables") or {}).get("expected_task_batch_id")
                if expected_task_batch_id:
                    batch_ids = {batch.id for batch in parsed.batches}
                    if expected_task_batch_id not in batch_ids:
                        report["errors"].append(
                            f"task_batches.yaml must declare expected conveyor id {expected_task_batch_id!r}"
                        )
                for batch in parsed.batches:
                    for key in (batch.result.context_key, batch.result.status_key):
                        if key not in context_definitions:
                            report["errors"].append(
                                f"task_batches {batch.id!r} writes undeclared context variable {key!r}"
                            )
            except Exception as exc:
                report["errors"].append(f"task_batches.yaml is invalid: {exc}")

        errors.extend(f"{workflow_name}: {message}" for message in report["errors"])

    return {"valid": not errors, "errors": errors, "workflows": workflow_reports}


def validate_agentgenerator_semantic_drift(
    *,
    bundle_entries: list[dict[str, Any]] | None = None,
    bundle_root: Path | None = None,
    expected_workflows: list[dict[str, Any]] | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any]:
    expected = expected_workflows or expected_workflows_from_context(context_variables)
    entries = bundle_entries or []
    if not entries and bundle_root is not None:
        entries = load_bundle_entries_from_root(bundle_root, expected)

    errors: list[str] = []
    warnings: list[str] = []
    workflow_reports: list[dict[str, Any]] = []
    expected_by_name = _expected_by_name(bundle_entries=entries, expected_workflows=expected)
    entries_by_name = {
        str(entry.get("workflow_name") or "").strip(): entry
        for entry in entries
        if str(entry.get("workflow_name") or "").strip()
    }

    for workflow_name, spec in expected_by_name.items():
        entry = entries_by_name.get(workflow_name, {"workflow_name": workflow_name, "files": []})
        report: dict[str, Any] = {"workflow_name": workflow_name, "semantic_drifts": []}
        workflow_reports.append(report)

        files = _files_by_name(entry)
        payloads, yaml_errors = _yaml_payloads_from_files(files)
        for yaml_error in yaml_errors:
            issue = _issue(
                check_id="workflow_yaml_readable_for_semantic_audit",
                file="*.yaml",
                expected="parseable workflow YAML",
                observed=yaml_error,
                fix_suggestion=(
                    "Tighten WorkflowBundleBuilderAgent output instructions so every "
                    "generated YAML file is complete and parseable before emission."
                ),
            )
            report["semantic_drifts"].append(issue)
            errors.append(_issue_summary(workflow_name, issue))
        if yaml_errors:
            continue

        orchestrator = payloads.get("orchestrator.yaml")
        if not isinstance(orchestrator, dict):
            issue = _issue(
                check_id="orchestrator_mapping_required",
                file="orchestrator.yaml",
                expected="mapping",
                observed=type(orchestrator).__name__,
                fix_suggestion="Ensure WorkflowBundleBuilderAgent always emits orchestrator.yaml as a YAML object.",
            )
            report["semantic_drifts"].append(issue)
            errors.append(_issue_summary(workflow_name, issue))
            continue

        spec_context = spec.get("context_variables") if isinstance(spec.get("context_variables"), dict) else {}
        expected_human = spec_context.get("expected_human_in_the_loop")  # type: ignore[union-attr]
        if isinstance(expected_human, bool) and orchestrator.get("human_in_the_loop") is not expected_human:
            issue = _issue(
                check_id="human_in_the_loop_semantic_drift",
                file="orchestrator.yaml",
                expected=expected_human,
                observed=orchestrator.get("human_in_the_loop"),
                fix_suggestion=(
                    "Update WorkflowBundleBuilderAgent prompt handling so the HITL flag from "
                    "the task brief is copied into orchestrator.yaml without reinterpretation."
                ),
            )
            report["semantic_drifts"].append(issue)
            errors.append(_issue_summary(workflow_name, issue))

        triggers = orchestrator.get("triggers") if isinstance(orchestrator.get("triggers"), list) else []
        expected_event = str(spec_context.get("expected_event_trigger") or "").strip()  # type: ignore[union-attr]
        if expected_event:
            matching_triggers = [trigger for trigger in triggers if _event_type_from_trigger(trigger) == expected_event]  # type: ignore[union-attr]
            if not matching_triggers:
                issue = _issue(
                    check_id="event_trigger_missing",
                    file="orchestrator.yaml",
                    expected=expected_event,
                    observed=triggers,
                    fix_suggestion=(
                        "Make WorkflowBundleBuilderAgent copy the exact event trigger from "
                        "the generation brief into orchestrator.yaml triggers[]."
                    ),
                )
                report["semantic_drifts"].append(issue)
                errors.append(_issue_summary(workflow_name, issue))
        else:
            matching_triggers = [trigger for trigger in triggers if _event_type_from_trigger(trigger)]  # type: ignore[union-attr]

        explicit_expected_capability_id = str(spec_context.get("expected_workflow_capability_id") or "").strip()  # type: ignore[union-attr]
        expected_capability_id = explicit_expected_capability_id or workflow_name_to_capability_id(workflow_name)
        for matching_trigger in matching_triggers:
            trigger_type = str(matching_trigger.get("type") or "").strip()
            if trigger_type != "event":
                issue = _issue(
                    check_id="event_trigger_type_semantic_drift",
                    file="orchestrator.yaml",
                    expected="event",
                    observed=trigger_type,
                    fix_suggestion=(
                        "Prompt WorkflowBundleBuilderAgent to emit `type: event` for "
                        "domain/platform/hosted event triggers."
                    ),
                )
                report["semantic_drifts"].append(issue)
                errors.append(_issue_summary(workflow_name, issue))

            observed_capability_id = str(matching_trigger.get("capability_id") or "").strip()
            if not observed_capability_id:
                issue = _issue(
                    check_id="event_trigger_capability_id_semantic_drift",
                    file="orchestrator.yaml",
                    expected="non-empty stable workflow capability id",
                    observed=None,
                    fix_suggestion=(
                        "Prompt WorkflowBundleBuilderAgent to include the stable workflow "
                        "capability_id on every event trigger; AppGenerator reactions use "
                        "that id as the runtime-resolvable target."
                    ),
                )
                report["semantic_drifts"].append(issue)
                errors.append(_issue_summary(workflow_name, issue))
            elif explicit_expected_capability_id and observed_capability_id != expected_capability_id:
                issue = _issue(
                    check_id="event_trigger_capability_id_semantic_drift",
                    file="orchestrator.yaml",
                    expected=expected_capability_id,
                    observed=observed_capability_id,
                    fix_suggestion=(
                        "Prompt WorkflowBundleBuilderAgent to include the stable workflow "
                        "capability_id on every event trigger; AppGenerator reactions use "
                        "that id as the runtime-resolvable target."
                    ),
                )
                report["semantic_drifts"].append(issue)
                errors.append(_issue_summary(workflow_name, issue))

            trigger_description = matching_trigger.get("description")
            if _is_generic_trigger_description(trigger_description):
                issue = _issue(
                    check_id="event_trigger_description_semantic_drift",
                    file="orchestrator.yaml",
                    expected="domain-specific trigger purpose",
                    observed=trigger_description,
                    fix_suggestion=(
                        "Prompt WorkflowBundleBuilderAgent to preserve business meaning in "
                        "trigger descriptions instead of emitting generic text such as "
                        "`Trigger for ... event`."
                    ),
                )
                report["semantic_drifts"].append(issue)
                errors.append(_issue_summary(workflow_name, issue))
            else:
                expected_tokens = _semantic_tokens(_event_type_from_trigger(matching_trigger), spec.get("description"))
                matched_tokens = {
                    token
                    for token in expected_tokens
                    if token in str(trigger_description or "").lower()
                }
                if expected_tokens and len(matched_tokens) < 2:
                    issue = _issue(
                        check_id="event_trigger_description_domain_token_warning",
                        file="orchestrator.yaml",
                        expected=sorted(expected_tokens),
                        observed=trigger_description,
                        severity="warning",
                        fix_suggestion=(
                            "Consider tightening WorkflowBundleBuilderAgent examples so "
                            "trigger descriptions retain domain nouns from the brief."
                        ),
                    )
                    report["semantic_drifts"].append(issue)
                    warnings.append(_issue_summary(workflow_name, issue))

        task_batches = payloads.get("extended_orchestration/task_batches.yaml")
        if isinstance(task_batches, dict):
            conveyors = task_batches.get("conveyors")
            if isinstance(conveyors, list):
                for conveyor in conveyors:
                    if not isinstance(conveyor, dict):
                        continue
                    execution_agents = [
                        str(agent).strip()
                        for agent in conveyor.get("execution_agents", [])
                        if str(agent or "").strip()
                    ]
                    if len(set(execution_agents)) < 2:
                        issue = _issue(
                            check_id="task_conveyor_parallel_execution_agent_drift",
                            file="extended_orchestration/task_batches.yaml",
                            expected="at least two execution_agents for parallel downstream work",
                            observed=execution_agents,
                            fix_suggestion=(
                                "Prompt WorkflowBundleBuilderAgent to model conveyor workflows "
                                "as decomposition plus multiple downstream execution agents, not "
                                "a single serial worker."
                            ),
                        )
                        report["semantic_drifts"].append(issue)
                        errors.append(_issue_summary(workflow_name, issue))

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "workflows": workflow_reports,
    }


def run_workflow_bundle_quality_gate(
    *,
    bundle_entries: list[dict[str, Any]],
    expected_workflows: list[dict[str, Any]] | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any]:
    expected = expected_workflows or expected_workflows_from_context(context_variables)
    structure = validate_workflow_bundle_structure(
        bundle_entries=bundle_entries,
        expected_workflows=expected,
    )
    semantic_drift = validate_agentgenerator_semantic_drift(
        bundle_entries=bundle_entries,
        expected_workflows=expected,
        context_variables=context_variables,
    )
    errors = list(structure.get("errors") or []) + list(semantic_drift.get("errors") or [])
    warnings = list(semantic_drift.get("warnings") or [])
    result = {
        "status": "passed" if not errors else "failed",
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "structure": structure,
        "semantic_drift": semantic_drift,
    }
    _context_set(context_variables, "workflow_bundle_validation_status", result["status"])
    _context_set(context_variables, "workflow_bundle_validation_errors", errors)
    _context_set(context_variables, "workflow_bundle_validation_warnings", warnings)
    _context_set(context_variables, "workflow_bundle_semantic_drift", semantic_drift)
    _context_set(context_variables, "workflow_bundle_quality_gate", result)
    return result


def _workflow_issue_map(quality_gate: dict[str, Any]) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}
    structure = quality_gate.get("structure") if isinstance(quality_gate, dict) else {}
    for report in structure.get("workflows") or [] if isinstance(structure, dict) else []:
        if not isinstance(report, dict):
            continue
        workflow_name = str(report.get("workflow_name") or "").strip()
        if not workflow_name:
            continue
        for error in report.get("errors") or []:
            issues.setdefault(workflow_name, []).append(str(error))

    semantic_drift = quality_gate.get("semantic_drift") if isinstance(quality_gate, dict) else {}
    for report in semantic_drift.get("workflows") or [] if isinstance(semantic_drift, dict) else []:
        if not isinstance(report, dict):
            continue
        workflow_name = str(report.get("workflow_name") or "").strip()
        if not workflow_name:
            continue
        for item in report.get("semantic_drifts") or []:
            if not isinstance(item, dict) or item.get("severity") != "error":
                continue
            summary = (
                f"{item.get('check_id')}: expected {item.get('expected')!r}, "
                f"observed {item.get('observed')!r}; fix: {item.get('fix_suggestion')}"
            )
            issues.setdefault(workflow_name, []).append(summary)
    return issues


def _task_id_for_entry(entry: dict[str, Any]) -> str:
    task_id = str(entry.get("_task_id") or "").strip()
    if task_id:
        return task_id
    workflow_name = str(entry.get("workflow_name") or "").strip()
    return re.sub(r"[^a-z0-9]+", "_", workflow_name.lower()).strip("_")


def _workflow_spec_name(spec: dict[str, Any]) -> str:
    return str(spec.get("name") or spec.get("workflow_name") or spec.get("id") or "").strip()


def _repair_task_id_for_spec(spec: dict[str, Any]) -> str:
    task_id = str(spec.get("task_id") or "").strip()
    if task_id:
        return task_id
    workflow_name = _workflow_spec_name(spec)
    return re.sub(r"[^a-z0-9]+", "_", workflow_name.lower()).strip("_")


def _build_repair_request(
    *,
    workflow_issues: dict[str, list[str]],
    attempt: int,
    max_attempts: int,
) -> str:
    lines = [
        f"Workflow bundle quality gate failed. Automated repair attempt {attempt} of {max_attempts}.",
        "Regenerate only the failed workflow bundle(s). Preserve the original workflow intent.",
        "Do not simplify the workflow, remove task-batch/conveyor behavior, or invent alternate trigger semantics.",
    ]
    for workflow_name, issues in sorted(workflow_issues.items()):
        lines.append(f"\n{workflow_name}:")
        for issue in issues:
            lines.append(f"- {issue}")
    return "\n".join(lines)


def prepare_workflow_bundle_repair(
    *,
    quality_gate: dict[str, Any],
    bundle_entries: list[dict[str, Any]],
    context_variables: Any | None = None,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Prepare a bounded AgentGenerator task-batch retry for failed workflows."""

    max_attempts = max(0, int(max_attempts))
    workflow_issues = _workflow_issue_map(quality_gate)
    failed_workflows = sorted(workflow_issues)
    prior_attempts = int(_context_get(context_variables, "workflow_bundle_repair_count", 0) or 0)

    if not failed_workflows:
        result = {
            "status": "blocked",
            "repairable": False,
            "reason": "quality_gate_failed_without_workflow_issue_mapping",
            "failed_workflows": [],
            "attempt": prior_attempts,
            "max_attempts": max_attempts,
            "repair_request": None,
        }
        _context_set(context_variables, "workflow_bundle_repair_status", result["status"])
        _context_set(context_variables, "workflow_bundle_repair_result", result)
        return result

    if prior_attempts >= max_attempts:
        repair_request = _build_repair_request(
            workflow_issues=workflow_issues,
            attempt=prior_attempts,
            max_attempts=max_attempts,
        )
        result = {
            "status": "blocked",
            "repairable": False,
            "reason": "workflow_bundle_repair_attempts_exhausted",
            "failed_workflows": failed_workflows,
            "attempt": prior_attempts,
            "max_attempts": max_attempts,
            "repair_request": repair_request,
        }
        _context_set(context_variables, "workflow_bundle_repair_status", result["status"])
        _context_set(context_variables, "workflow_bundle_repair_request", repair_request)
        _context_set(context_variables, "workflow_bundle_repair_failed_workflows", failed_workflows)
        _context_set(context_variables, "workflow_bundle_repair_result", result)
        return result

    attempt = prior_attempts + 1
    repair_request = _build_repair_request(
        workflow_issues=workflow_issues,
        attempt=attempt,
        max_attempts=max_attempts,
    )

    current_results = _context_get(context_variables, "workflow_bundle_results")
    current_specs = _context_get(context_variables, "workflows_spec", [])
    if not _context_get(context_variables, "workflow_bundle_repair_original_workflows_spec"):
        _context_set(context_variables, "workflow_bundle_repair_original_workflows_spec", current_specs)
    _context_set(context_variables, "workflow_bundle_repair_base_results", current_results)

    specs_by_name = {
        _workflow_spec_name(spec): dict(spec)
        for spec in current_specs
        if isinstance(spec, dict) and _workflow_spec_name(spec)
    }
    entries_by_name = {
        str(entry.get("workflow_name") or "").strip(): entry
        for entry in bundle_entries
        if str(entry.get("workflow_name") or "").strip()
    }

    repair_specs: list[dict[str, Any]] = []
    for workflow_name in failed_workflows:
        spec = dict(specs_by_name.get(workflow_name) or {})
        if not spec:
            entry = entries_by_name.get(workflow_name, {})
            spec = {
                "name": workflow_name,
                "workflow_name": workflow_name,
                "task_id": _task_id_for_entry(entry),
                "initial_agent": "WorkflowBundleBuilderAgent",
                "initial_message": f"Regenerate the complete workflow bundle for {workflow_name}.",
                "context_variables": {},
            }
        spec["initial_agent"] = "WorkflowBundleBuilderAgent"
        spec["repair_attempt"] = attempt
        original_message = str(spec.get("initial_message") or "").strip()
        issue_lines = "\n".join(f"- {issue}" for issue in workflow_issues[workflow_name])
        spec["initial_message"] = (
            f"{original_message}\n\n"
            "[WORKFLOW BUNDLE REPAIR REQUEST]\n"
            f"Repair attempt: {attempt} of {max_attempts}\n"
            "The previous output failed the production quality gate. Regenerate the entire "
            "workflow bundle for this workflow and fix every issue below.\n"
            f"{issue_lines}\n"
        ).strip()
        context = spec.get("context_variables") if isinstance(spec.get("context_variables"), dict) else {}
        context["workflow_bundle_repair_attempt"] = attempt  # type: ignore[index]
        context["workflow_bundle_repair_issues"] = workflow_issues[workflow_name]  # type: ignore[index]
        spec["context_variables"] = context
        repair_specs.append(spec)

    result = {
        "status": "needs_revision",
        "repairable": True,
        "failed_workflows": failed_workflows,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "repair_request": repair_request,
        "repair_workflow_count": len(repair_specs),
    }
    _context_set(context_variables, "workflow_bundle_repair_status", result["status"])
    _context_set(context_variables, "workflow_bundle_repair_active", True)
    _context_set(context_variables, "workflow_bundle_repair_count", attempt)
    _context_set(context_variables, "workflow_bundle_repair_max_attempts", max_attempts)
    _context_set(context_variables, "workflow_bundle_repair_failed_workflows", failed_workflows)
    _context_set(context_variables, "workflow_bundle_repair_request", repair_request)
    _context_set(context_variables, "workflow_bundle_repair_result", result)
    _context_set(context_variables, "workflows_spec", repair_specs)
    return result


def merge_workflow_bundle_repair_results(context_variables: Any | None = None) -> dict[str, Any]:
    """Merge repaired task-batch outputs back into the pre-repair bundle set."""

    if _context_get(context_variables, "workflow_bundle_repair_active") is not True:
        return {"status": "skipped", "reason": "repair_not_active"}
    base_results = _context_get(context_variables, "workflow_bundle_repair_base_results")
    repair_results = _context_get(context_variables, "workflow_bundle_results")
    if not isinstance(base_results, dict) or not isinstance(repair_results, dict):
        return {"status": "skipped", "reason": "missing_repair_results"}

    repair_entries = {
        key: value
        for key, value in repair_results.items()
        if isinstance(key, str) and not key.startswith("_") and isinstance(value, dict)
    }
    repair_names = {
        str(value.get("workflow_name") or "").strip()
        for value in repair_entries.values()
        if str(value.get("workflow_name") or "").strip()
    }
    merged: dict[str, Any] = {}
    for key, value in base_results.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if not isinstance(value, dict):
            continue
        workflow_name = str(value.get("workflow_name") or "").strip()
        if key in repair_entries or workflow_name in repair_names:
            continue
        merged[key] = value
    merged.update(repair_entries)
    merged["_meta"] = {
        **(base_results.get("_meta") if isinstance(base_results.get("_meta"), dict) else {}),  # type: ignore[dict-item]
        "repair": {
            "status": "merged",
            "attempt": _context_get(context_variables, "workflow_bundle_repair_count", 0),
            "repaired_tasks": sorted(repair_entries),
            "repaired_workflows": sorted(repair_names),
        },
    }

    original_specs = _context_get(context_variables, "workflow_bundle_repair_original_workflows_spec")
    if isinstance(original_specs, list):
        _context_set(context_variables, "workflows_spec", original_specs)
    _context_set(context_variables, "workflow_bundle_results", merged)
    _context_set(context_variables, "workflow_bundle_repair_active", False)
    _context_set(context_variables, "workflow_bundle_repair_status", "merged")
    _context_set(context_variables, "workflow_bundle_repair_merged", True)
    result = {
        "status": "merged",
        "merged_workflow_count": len([key for key in merged if not key.startswith("_")]),
        "repaired_workflows": sorted(repair_names),
    }
    _context_set(context_variables, "workflow_bundle_repair_merge_result", result)
    return result


__all__ = [
    "REQUIRED_WORKFLOW_FILES",
    "expected_workflows_from_context",
    "load_bundle_entries_from_root",
    "merge_workflow_bundle_repair_results",
    "prepare_workflow_bundle_repair",
    "run_workflow_bundle_quality_gate",
    "validate_agentgenerator_semantic_drift",
    "validate_workflow_bundle_structure",
    "workflow_bundle_entries_from_context",
]
