from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mozaiksai.control_plane import (  # noqa: E402
    LLMChangeClassifier,
    RefinementTriggerRouteResolver,
    load_refinement_harness,
    load_refinement_policy_config,
)
from mozaiksai.core.capabilities.simple_llm import SimpleLLMCapabilityService  # noqa: E402
from mozaiksai.core.workflow.pack.config import (  # noqa: E402
    get_workflow_sequence,
    load_global_pack_graph,
)
from scripts import run_live_workflow_smoke, smoke_refinement_classifier  # noqa: E402

APP_ROOT = REPO_ROOT / "factory_app" / "app"
WORKFLOWS_ROOT = REPO_ROOT / "factory_app" / "workflows"
SCHEMA_VERSION = "mozaiks.refinement_harness.v1_task_batch_smoke.v1"


@dataclass(frozen=True)
class CombinedSmokeSpec:
    id: str
    request: str
    workflow_prompt: str
    files_manifest: list[dict[str, Any]]
    workflow_name: str = "RuntimeTaskBatchSmoke"


DEFAULT_SPEC = CombinedSmokeSpec(
    id="social_platform_expansion",
    request=(
        "Expand the existing product to add creator onboarding, moderation tooling, "
        "notifications, and analytics surfaces while preserving the core concept and brand."
    ),
    workflow_prompt=(
        "Build a social media app with profiles, feed ranking, creator onboarding, "
        "moderation, notifications, and an admin dashboard. Plan it as "
        "bounded independent module, service, page, and workflow work units, run "
        "the task batch, and summarize the result."
    ),
    files_manifest=smoke_refinement_classifier.base_manifest(),
)


def _serialize_sequence(sequence: Any) -> dict[str, Any] | None:
    if sequence is None:
        return None
    steps: list[dict[str, Any]] = []
    workflow_ids: list[str] = []
    transition_ids: list[str] = []
    for index, step in enumerate(getattr(sequence, "steps", []) or []):
        workflows = list(getattr(step, "workflows", []) or [])
        transition = str(getattr(step, "transition", "") or "").strip() or None
        entry: dict[str, Any] = {"index": index}
        if workflows:
            entry["workflows"] = workflows
            for workflow_id in workflows:
                if workflow_id not in workflow_ids:
                    workflow_ids.append(workflow_id)
        if transition:
            entry["transition"] = transition
            if transition not in transition_ids:
                transition_ids.append(transition)
        steps.append(entry)
    return {
        "id": str(getattr(sequence, "id", "") or ""),
        "description": str(getattr(sequence, "description", "") or ""),
        "affected_declarative_families": list(getattr(sequence, "affected_declarative_families", []) or []),
        "step_count": len(steps),
        "steps": steps,
        "workflow_ids": workflow_ids,
        "transition_ids": transition_ids,
    }


def _request_payload(spec: CombinedSmokeSpec) -> dict[str, Any]:
    return {
        "refinement_request": {
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "artifact_version_id": f"av_refinement_task_batch_{spec.id}",
            "raw_user_request": spec.request,
            "source_surface": "manual_refinement_task_batch_smoke",
            "extra": {"files_manifest": spec.files_manifest},
        }
    }


async def _route_spec(spec: CombinedSmokeSpec) -> dict[str, Any]:
    smoke_refinement_classifier._load_dotenv()
    refinement_policy_config = load_refinement_policy_config(APP_ROOT)
    llm_profile_used = str(refinement_policy_config.classifier.llm_profile or "raw_llm_config")
    classifier_llm_config = refinement_policy_config.resolve_capability_llm_config("classifier")
    provider_ok, provider_message = await smoke_refinement_classifier._provider_available()
    if not provider_ok:
        return {
            "success": False,
            "error": provider_message,
            "llm_profile_used": llm_profile_used,
            "classifier_llm_config": smoke_refinement_classifier._safe_llm_config(classifier_llm_config),
        }

    def pack_loader():
        return load_refinement_harness(app_root=APP_ROOT)

    service = SimpleLLMCapabilityService(timeout=60.0)
    try:
        classifier = LLMChangeClassifier(
            capability_service=service,
            config_loader=lambda: refinement_policy_config,
            pack_loader=pack_loader,
        )
        resolver = RefinementTriggerRouteResolver(classifier=classifier, pack_loader=pack_loader)
        request = resolver.request_from_payload(
            payload=_request_payload(spec),
            requested_workflow_id="AppGenerator",
        )
        if request is None:
            return {
                "success": False,
                "error": "request_from_payload returned None",
                "llm_profile_used": llm_profile_used,
                "classifier_llm_config": smoke_refinement_classifier._safe_llm_config(classifier_llm_config),
            }

        decision = await resolver.route(request)
    finally:
        await service.aclose()

    pack = load_global_pack_graph()
    sequence = get_workflow_sequence(pack, decision.workflow_sequence) if pack is not None else None
    return {
        "success": True,
        "llm_profile_used": llm_profile_used,
        "classifier_llm_config": smoke_refinement_classifier._safe_llm_config(classifier_llm_config),
        "classifier": {
            "change_class": decision.change_intent.change_class.value,
            "source": decision.change_intent.source,
            "rationale": decision.change_intent.rationale,
            "confidence": decision.change_intent.confidence,
            "signals": list(decision.change_intent.signals),
        },
        "route": {
            "workflow_id": decision.workflow_id,
            "workflow_sequence": decision.workflow_sequence,
            "sequence_exists": smoke_refinement_classifier._sequence_exists(decision.workflow_sequence),
            "affected_workflows": list(decision.impact_set.affected_workflows),
            "sequence": _serialize_sequence(sequence),
        },
        "impact": {
            "affected_declarative_families": list(decision.impact_set.affected_declarative_families),
            "affected_bundle_paths": list(decision.impact_set.affected_bundle_paths),
            "scope_summary": decision.impact_set.scope_summary,
        },
    }


def validate_smoke_output(payload: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        violations.append("Unexpected or missing schema_version")
    if payload.get("llm_profile_used") != "classifier":
        violations.append("Top-level llm_profile_used is not classifier")
    if payload.get("generated_files_unchanged") is False:
        violations.append("Generated app file git status changed during smoke run")

    refinement_engine = payload.get("refinement_engine") if isinstance(payload.get("refinement_engine"), dict) else {}
    route = refinement_engine.get("route") if isinstance(refinement_engine.get("route"), dict) else {}
    impact = refinement_engine.get("impact") if isinstance(refinement_engine.get("impact"), dict) else {}
    sequence = route.get("sequence") if isinstance(route.get("sequence"), dict) else {}

    if not route.get("workflow_sequence"):
        violations.append("Refinement route is missing workflow_sequence")
    if route.get("sequence_exists") is not True:
        violations.append("Refinement workflow_sequence does not resolve in the pack graph")
    if int(sequence.get("step_count") or 0) < 1:
        violations.append("Resolved workflow sequence does not include any steps")
    if not list(sequence.get("workflow_ids") or []) and not list(sequence.get("transition_ids") or []):
        violations.append("Resolved workflow sequence did not expose workflows or transitions")

    for path in impact.get("affected_bundle_paths") or []:
        if smoke_refinement_classifier._path_has_secret_marker(str(path)):
            violations.append(f"Refinement engine emitted a secret-bearing path: {path}")

    live_workflow = payload.get("live_workflow") if isinstance(payload.get("live_workflow"), dict) else {}
    structured_output = live_workflow.get("structured_output") if isinstance(live_workflow.get("structured_output"), dict) else {}
    final_context = live_workflow.get("final_context") if isinstance(live_workflow.get("final_context"), dict) else {}
    task_batch_results = (
        final_context.get("runtime_smoke_tasks_results")
        if isinstance(final_context.get("runtime_smoke_tasks_results"), dict)
        else {}
    )
    task_batch_meta = task_batch_results.get("_meta") if isinstance(task_batch_results.get("_meta"), dict) else {}
    if live_workflow.get("success") is not True:
        violations.append("Live workflow smoke did not succeed")
    if live_workflow.get("workflow_name") != DEFAULT_SPEC.workflow_name:
        violations.append("Live workflow smoke used an unexpected workflow")
    if structured_output.get("task_batch_execution_used") is not True:
        violations.append("Live workflow smoke did not report task batch execution")
    if int(structured_output.get("work_unit_count") or 0) < 3:
        violations.append("Live workflow smoke produced fewer than 3 work units")
    if not task_batch_meta:
        violations.append("Live workflow smoke did not persist runtime_smoke_tasks_results._meta")
    else:
        completed_tasks = [str(task_id) for task_id in task_batch_meta.get("completed_tasks") or []]
        failed_tasks = [str(task_id) for task_id in task_batch_meta.get("failed_tasks") or []]
        task_count = int(task_batch_meta.get("task_count") or 0)
        concurrency = int(task_batch_meta.get("concurrency") or 0)
        result_context_key = str(task_batch_meta.get("result_context_key") or "")
        meta_status = str(task_batch_meta.get("status") or "")
        worker_agents = {
            str((task_batch_results.get(task_id) or {}).get("_worker_agent") or "")
            for task_id in completed_tasks
            if isinstance(task_batch_results.get(task_id), dict)
        }
        expected_success = (
            final_context.get("runtime_smoke_tasks_status") == "completed"
            and meta_status == "completed"
            and not failed_tasks
            and len(completed_tasks) == task_count
        )

        output_work_unit_count = structured_output.get("work_unit_count")
        output_max_parallelism = structured_output.get("max_parallelism")
        output_failure_count = structured_output.get("failure_count")
        if int(output_work_unit_count if output_work_unit_count is not None else -1) != task_count:
            violations.append("Structured output work_unit_count does not match runtime_smoke_tasks_results._meta.task_count")
        if int(output_max_parallelism if output_max_parallelism is not None else -1) != concurrency:
            violations.append("Structured output max_parallelism does not match runtime_smoke_tasks_results._meta.concurrency")
        if list(structured_output.get("executed_task_ids") or []) != completed_tasks:
            violations.append("Structured output executed_task_ids does not match runtime_smoke_tasks_results._meta.completed_tasks")
        if int(output_failure_count if output_failure_count is not None else -1) != len(failed_tasks):
            violations.append("Structured output failure_count does not match runtime_smoke_tasks_results._meta.failed_tasks")
        if str(structured_output.get("result_context_key") or "") != result_context_key:
            violations.append("Structured output result_context_key does not match runtime_smoke_tasks_results._meta.result_context_key")
        if bool(structured_output.get("all_units_succeeded")) != expected_success:
            violations.append("Structured output all_units_succeeded does not match task batch executor status")
        expected_kinds = {"module", "page", "integration"}
        if not expected_kinds.issubset(set(structured_output.get("executed_kinds") or [])):
            violations.append("Live workflow smoke did not execute module, page, and integration task kinds")
        expected_workers = {
            "ModuleTaskWorkerAgent",
            "PageTaskWorkerAgent",
            "IntegrationTaskWorkerAgent",
        }
        if not expected_workers.issubset(worker_agents):
            violations.append("Live workflow smoke did not use all expected task worker agents")
    if smoke_refinement_classifier._rendered_has_proprietary_term(payload):
        violations.append("Smoke output includes a proprietary term")
    return violations


async def run_smoke(spec: CombinedSmokeSpec = DEFAULT_SPEC) -> dict[str, Any]:
    generated_before = smoke_refinement_classifier._git_generated_status()
    refinement_engine = await _route_spec(spec)

    if not refinement_engine.get("success"):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "success": False,
            "request": spec.request,
            "workflow_prompt": spec.workflow_prompt,
            "workflow_name": spec.workflow_name,
            "llm_profile_used": refinement_engine.get("llm_profile_used"),
            "classifier_llm_config": refinement_engine.get("classifier_llm_config"),
            "refinement_engine": refinement_engine,
            "live_workflow": {"success": False, "error": "refinement route failed"},
            "generated_status_before": generated_before,
            "generated_status_after": generated_before,
            "generated_files_unchanged": True,
            "notes": [
                "The refinement routing leg failed before the live workflow smoke could start.",
            ],
        }
        payload["violations"] = validate_smoke_output(payload)
        return payload

    live_error: str | None = None
    live_payload: dict[str, Any]
    try:
        result = await run_live_workflow_smoke.run_live_workflow_smoke(
            workflow_name=spec.workflow_name,
            workflows_root=WORKFLOWS_ROOT,
            prompt=spec.workflow_prompt,
            timeout_seconds=240.0,
        )
        live_payload = result.as_dict()
    except Exception as exc:
        live_error = str(exc)
        live_payload = {
            "success": False,
            "workflow_name": spec.workflow_name,
            "error": live_error,
            "structured_output": {},
        }

    generated_after = smoke_refinement_classifier._git_generated_status()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "success": False,
        "request": spec.request,
        "workflow_prompt": spec.workflow_prompt,
        "workflow_name": spec.workflow_name,
        "llm_profile_used": refinement_engine.get("llm_profile_used"),
        "classifier_llm_config": refinement_engine.get("classifier_llm_config"),
        "refinement_engine": refinement_engine,
        "live_workflow": live_payload,
        "generated_status_before": generated_before,
        "generated_status_after": generated_after,
        "generated_files_unchanged": generated_before == generated_after,
        "notes": [
            "The refinement route is evaluated first and recorded with its resolved workflow sequence graph.",
            "The live workflow leg runs RuntimeTaskBatchSmoke independently to validate AG2-native workflow-local task batch execution.",
        ],
    }
    if live_error:
        payload["live_workflow_error"] = live_error
    violations = validate_smoke_output(payload)
    payload["success"] = not violations
    payload["violations"] = violations
    return payload


def _print_human(payload: dict[str, Any]) -> None:
    print("Refinement Engine + task batch smoke:", "PASS" if payload.get("success") else "FAIL")
    print(f"Classifier profile: {payload.get('llm_profile_used')}")
    print(f"Workflow: {payload.get('workflow_name')}")

    refinement_engine = payload.get("refinement_engine") or {}
    classifier = refinement_engine.get("classifier") or {}
    route = refinement_engine.get("route") or {}
    impact = refinement_engine.get("impact") or {}
    sequence = route.get("sequence") or {}

    print("")
    print(f"Request: {payload.get('request')}")
    print(f"Change class: {classifier.get('change_class')}")
    print(f"Workflow sequence: {route.get('workflow_sequence')}")
    print(f"Affected workflows: {route.get('affected_workflows')}")
    print(f"Sequence steps: {sequence.get('steps')}")
    print(f"Affected paths: {impact.get('affected_bundle_paths')}")
    print(f"Scope: {impact.get('scope_summary')}")

    live = payload.get("live_workflow") or {}
    structured = live.get("structured_output") or {}
    print("")
    print(f"Live workflow success: {live.get('success')}")
    print(f"Task batch execution used: {structured.get('task_batch_execution_used')}")
    print(f"Work unit count: {structured.get('work_unit_count')}")
    print(f"Assistant message: {live.get('assistant_message') or structured.get('agent_message')}")

    if payload.get("violations"):
        print("")
        print("Violations:")
        for violation in payload.get("violations") or []:
            print(f"  - {violation}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the combined refinement and live task batch smoke harness."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = parser.parse_args(argv)

    payload = asyncio.run(run_smoke())
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)
    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
