from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ruff: noqa: E402,I001

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mozaiksai.control_plane import (
    LLMChangeClassifier,
    RefinementTriggerRouteResolver,
    load_control_plane_config,
    load_control_plane_pack,
)
from mozaiksai.core.capabilities.simple_llm import SimpleLLMCapabilityService
from mozaiksai.core.workflow.llm_config import get_llm_config
from mozaiksai.core.workflow.pack.config import get_workflow_sequence, load_global_pack_graph

APP_ROOT = REPO_ROOT / "factory_app" / "app"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "refinement_classifier_smoke_output.json"
VALID_CHANGE_CLASSES = {"patch", "design", "feature", "core"}
PROPRIETARY_TERMS = ("app zero", "app_zero", "mozaiks-app", "blocunited")
SECRET_PATH_TERMS = (
    ".env",
    "apikey",
    "api_key",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "vault",
)


@dataclass(frozen=True)
class SmokeCase:
    id: str
    label: str
    request: str
    files_manifest: list[dict[str, Any]]
    allowed_change_classes: tuple[str, ...]


def base_manifest() -> list[dict[str, Any]]:
    return [
        {"path": "app.json"},
        {"path": "config/shell.json"},
        {"path": "config/database_intent.json"},
        {"path": "config/database_migrations/001_initial.json"},
        {"path": "config/integrations.json"},
        {"path": "docs/integrations.md"},
        {"path": "backend/integrations/analytics_provider_client.py"},
        {"path": "backend/integrations/analytics_provider_secret.py"},
        {"path": "config/integrations.credentials.json"},
        {"path": "modules/projects/module.yaml"},
        {"path": "modules/projects/contracts/events.yaml"},
        {"path": "modules/projects/contracts/admin.yaml"},
        {"path": "modules/projects/backend/handler.py"},
        {"path": "modules/projects/backend/service.py"},
        {"path": "modules/projects/backend/repo.py"},
        {"path": "modules/projects/backend/policy.py"},
        {"path": "modules/projects/backend/schemas.py"},
        {"path": "modules/reports/module.yaml"},
        {
            "path": "modules/reports/backend/service.py",
            "content": "CONNECTOR_ID = 'analytics_provider'\n",
        },
        {
            "path": "modules/reports/backend/schemas.py",
            "content": "connector_id = 'analytics_provider'\n",
        },
        {"path": "ui/pages/dashboard.yaml"},
        {"path": "ui/pages/reports.yaml"},
        {"path": "ui/route_manifest.json"},
        {"path": "ui/index.js"},
        {"path": ".env"},
    ]


def hosted_manifest() -> list[dict[str, Any]]:
    return [
        {"path": "backend/integrations/hosted_analytics_client.py"},
        {"path": "modules/analytics_dashboard/module.yaml"},
        {"path": "modules/analytics_dashboard/contracts/events.yaml"},
        {"path": "modules/analytics_dashboard/backend/handler.py"},
        {"path": "modules/analytics_dashboard/backend/service.py"},
        {"path": "modules/analytics_dashboard/backend/schemas.py"},
        {"path": "modules/hosted_analytics/module.yaml"},
        {
            "path": "ui/pages/analytics.yaml",
            "content": "api_endpoint: /api/modules/analytics_dashboard/get_metrics\n",
        },
        {"path": "ui/route_manifest.json"},
    ]


SMOKE_CASES: tuple[SmokeCase, ...] = (
    SmokeCase(
        id="ui_experience_spec",
        label="UI / ExperienceSpec",
        request="Change the dashboard experience to highlight reports first.",
        files_manifest=base_manifest(),
        allowed_change_classes=("design", "patch"),
    ),
    SmokeCase(
        id="module_backend",
        label="Module/backend",
        request="Add an archive_project action to the projects module API.",
        files_manifest=base_manifest(),
        allowed_change_classes=("feature", "patch"),
    ),
    SmokeCase(
        id="external_integration",
        label="External integration",
        request="Change the analytics_provider connector sync behavior.",
        files_manifest=base_manifest(),
        allowed_change_classes=("feature", "patch"),
    ),
    SmokeCase(
        id="data_model_migration",
        label="Data model migration",
        request="Add a required project phase field and migrate existing project records.",
        files_manifest=base_manifest(),
        allowed_change_classes=("feature", "core"),
    ),
    SmokeCase(
        id="hosted_capability_facade",
        label="Hosted capability facade",
        request="Change hosted analytics dashboard display.",
        files_manifest=hosted_manifest(),
        allowed_change_classes=("design", "feature"),
    ),
)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env")


def _safe_llm_config(llm_config: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in dict(llm_config or {}).items():
        lower = str(key).lower()
        if any(term in lower for term in ("api_key", "apikey", "secret", "password", "token")):
            safe[key] = "***REDACTED***" if value else value
        elif key == "config_list" and isinstance(value, list):
            safe[key] = [_safe_llm_config(entry) if isinstance(entry, dict) else entry for entry in value]
        else:
            safe[key] = value
    return safe


async def _provider_available() -> tuple[bool, str]:
    try:
        _, llm_config = await get_llm_config(cache=False)
    except Exception as exc:
        return False, f"Could not load LLM provider config: {exc}"

    config_list = llm_config.get("config_list") if isinstance(llm_config, dict) else None
    if not isinstance(config_list, list):
        return False, "LLM provider config did not include config_list."
    if any(isinstance(entry, dict) and entry.get("api_key") and entry.get("model") for entry in config_list):
        return True, "LLM provider config found."
    return False, "No LLM provider API key is configured. Set OPENAI_API_KEY or configure a provider."


def _git_generated_status() -> list[str] | None:
    try:
        completed = subprocess.run(
            ["git", "status", "--short", "--", "generated", "generated_apps"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return [line.rstrip() for line in completed.stdout.splitlines() if line.strip()]


def _sequence_exists(sequence_id: str | None) -> bool:
    if not sequence_id:
        return False
    pack = load_global_pack_graph()
    return pack is not None and get_workflow_sequence(pack, sequence_id) is not None


def _path_has_secret_marker(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    return any(term in normalized for term in SECRET_PATH_TERMS) or any(part == ".env" for part in parts)


def _rendered_has_proprietary_term(payload: Any) -> bool:
    rendered = json.dumps(payload, sort_keys=True).lower()
    return any(term in rendered for term in PROPRIETARY_TERMS)


def _request_payload(case: SmokeCase) -> dict[str, Any]:
    return {
        "refinement_request": {
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "artifact_version_id": f"av_classifier_smoke_{case.id}",
            "raw_user_request": case.request,
            "source_surface": "manual_refinement_classifier_smoke",
            "extra": {"files_manifest": case.files_manifest},
        }
    }


async def run_smoke() -> dict[str, Any]:
    _load_dotenv()

    control_plane_config = load_control_plane_config(APP_ROOT)
    llm_profile_used = str(control_plane_config.classifier.llm_profile or "raw_llm_config")
    classifier_llm_config = control_plane_config.resolve_capability_llm_config("classifier")
    provider_ok, provider_message = await _provider_available()
    if not provider_ok:
        return {
            "schema_version": "mozaiks.refinement_classifier_smoke.v1",
            "success": False,
            "error": provider_message,
            "llm_profile_used": llm_profile_used,
            "classifier_llm_config": _safe_llm_config(classifier_llm_config),
            "cases": [],
        }

    def pack_loader():
        return load_control_plane_pack(app_root=APP_ROOT)

    service = SimpleLLMCapabilityService(timeout=60.0)
    classifier = LLMChangeClassifier(
        capability_service=service,
        config_loader=lambda: control_plane_config,
        pack_loader=pack_loader,
    )
    resolver = RefinementTriggerRouteResolver(classifier=classifier, pack_loader=pack_loader)

    generated_before = _git_generated_status()
    cases: list[dict[str, Any]] = []
    try:
        for case in SMOKE_CASES:
            request = resolver.request_from_payload(
                payload=_request_payload(case),
                requested_workflow_id="AppGenerator",
            )
            if request is None:
                cases.append(
                    {
                        "id": case.id,
                        "label": case.label,
                        "request": case.request,
                        "success": False,
                        "error": "request_from_payload returned None",
                    }
                )
                continue

            decision = await resolver.route(request)
            paths = list(decision.impact_set.affected_bundle_paths)
            case_payload = {
                "id": case.id,
                "label": case.label,
                "request": case.request,
                "allowed_change_classes": list(case.allowed_change_classes),
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
                    "sequence_exists": _sequence_exists(decision.workflow_sequence),
                    "affected_workflows": list(decision.impact_set.affected_workflows),
                },
                "impact": {
                    "affected_declarative_families": list(
                        decision.impact_set.affected_declarative_families
                    ),
                    "affected_bundle_paths": paths,
                    "scope_summary": decision.impact_set.scope_summary,
                },
                "llm_profile_used": llm_profile_used,
            }
            violations = validate_case(case_payload)
            case_payload["success"] = not violations
            case_payload["violations"] = violations
            cases.append(case_payload)
    finally:
        await service.aclose()

    generated_after = _git_generated_status()
    payload = {
        "schema_version": "mozaiks.refinement_classifier_smoke.v1",
        "success": False,
        "control_plane_profile": control_plane_config.profile,
        "llm_profile_used": llm_profile_used,
        "classifier_llm_config": _safe_llm_config(classifier_llm_config),
        "cases": cases,
        "generated_status_before": generated_before,
        "generated_status_after": generated_after,
        "generated_files_unchanged": generated_before == generated_after,
        "notes": [
            "No generated app files are written by this script.",
            "No refinement workflow is executed by this script.",
        ],
    }
    violations = validate_smoke_output(payload)
    payload["success"] = not violations
    payload["violations"] = violations
    return payload


def validate_case(case: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    case_id = str(case.get("id") or "")
    allowed = set(case.get("allowed_change_classes") or [])
    classifier = case.get("classifier") if isinstance(case.get("classifier"), dict) else {}
    route = case.get("route") if isinstance(case.get("route"), dict) else {}
    impact = case.get("impact") if isinstance(case.get("impact"), dict) else {}
    change_class = str(classifier.get("change_class") or "")
    families = [str(item) for item in impact.get("affected_declarative_families") or []]
    paths = [str(path) for path in impact.get("affected_bundle_paths") or []]
    scope_summary = str(impact.get("scope_summary") or "")

    if change_class not in VALID_CHANGE_CLASSES:
        violations.append(f"{case_id}: invalid change_class {change_class!r}")
    if allowed and change_class not in allowed:
        violations.append(f"{case_id}: change_class {change_class!r} not in expected set {sorted(allowed)}")
    sequence_id = str(route.get("workflow_sequence") or "")
    if not sequence_id:
        violations.append(f"{case_id}: missing workflow_sequence")
    elif not _sequence_exists(sequence_id):
        violations.append(f"{case_id}: workflow_sequence does not exist")
    if case.get("llm_profile_used") != "classifier":
        violations.append(f"{case_id}: llm_profile_used is not classifier")
    for path in paths:
        if _path_has_secret_marker(path):
            violations.append(f"{case_id}: secret-bearing path emitted: {path}")

    if case_id == "ui_experience_spec":
        if "experience_spec" in families:
            if not any(path.startswith("ui/pages/") for path in paths):
                violations.append(f"{case_id}: experience_spec route did not emit ui/pages path")
            if "ui/route_manifest.json" not in paths:
                violations.append(f"{case_id}: experience_spec route did not emit route manifest")
    elif case_id == "module_backend":
        required = {
            "modules/projects/module.yaml",
            "modules/projects/backend/handler.py",
            "modules/projects/backend/service.py",
            "modules/projects/backend/repo.py",
            "modules/projects/backend/schemas.py",
        }
        missing = sorted(required.difference(paths))
        if missing:
            violations.append(f"{case_id}: missing module/backend paths {missing}")
    elif case_id == "external_integration":
        required = {
            "backend/integrations/analytics_provider_client.py",
            "modules/reports/backend/service.py",
            "modules/reports/backend/schemas.py",
        }
        missing = sorted(required.difference(paths))
        if missing:
            violations.append(f"{case_id}: missing integration paths {missing}")
        if "Integration readiness may need to be rechecked." not in scope_summary:
            violations.append(f"{case_id}: missing integration readiness scope note")
    elif case_id == "data_model_migration":
        required = {
            "config/database_intent.json",
            "modules/projects/module.yaml",
            "modules/projects/backend/repo.py",
            "modules/projects/backend/policy.py",
            "modules/projects/backend/schemas.py",
        }
        missing = sorted(required.difference(paths))
        if missing:
            violations.append(f"{case_id}: missing data model paths {missing}")
        if not any(path.startswith("config/database_migrations/") for path in paths):
            violations.append(f"{case_id}: missing database migration path")
        if "Destructive changes require explicit review." not in scope_summary:
            violations.append(f"{case_id}: missing destructive-review warning")
    elif case_id == "hosted_capability_facade":
        required = {
            "backend/integrations/hosted_analytics_client.py",
            "modules/analytics_dashboard/module.yaml",
            "modules/analytics_dashboard/backend/service.py",
            "ui/pages/analytics.yaml",
        }
        missing = sorted(required.difference(paths))
        if missing:
            violations.append(f"{case_id}: missing hosted facade paths {missing}")
        if "modules/hosted_analytics/module.yaml" in paths:
            violations.append(f"{case_id}: emitted hosted provider module internals")

    return violations


def validate_smoke_output(payload: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if payload.get("schema_version") != "mozaiks.refinement_classifier_smoke.v1":
        violations.append("Unexpected or missing schema_version")
    if payload.get("llm_profile_used") != "classifier":
        violations.append("Top-level llm_profile_used is not classifier")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return violations + ["cases must be a list"]
    expected_ids = {case.id for case in SMOKE_CASES}
    actual_ids = {str(case.get("id") or "") for case in cases if isinstance(case, dict)}
    missing_ids = sorted(expected_ids.difference(actual_ids))
    if missing_ids:
        violations.append(f"Missing smoke case(s): {missing_ids}")
    for case in cases:
        if not isinstance(case, dict):
            violations.append("Case entry is not an object")
            continue
        violations.extend(validate_case(case))
    if _rendered_has_proprietary_term(payload):
        violations.append("Smoke output includes a proprietary term")
    if payload.get("generated_files_unchanged") is False:
        violations.append("Generated app file git status changed during smoke run")
    return violations


def _print_human(payload: dict[str, Any]) -> None:
    print("Refinement classifier live smoke:", "PASS" if payload.get("success") else "FAIL")
    if payload.get("error"):
        print(f"Error: {payload['error']}")
    print(f"LLM profile: {payload.get('llm_profile_used')}")
    classifier_config = payload.get("classifier_llm_config") or {}
    if isinstance(classifier_config, dict):
        print(f"Classifier model: {classifier_config.get('model', 'unknown')}")
    for case in payload.get("cases") or []:
        if not isinstance(case, dict):
            continue
        classifier = case.get("classifier") or {}
        route = case.get("route") or {}
        impact = case.get("impact") or {}
        print("")
        print(f"[{case.get('id')}] {case.get('request')}")
        print(f"  change_class: {classifier.get('change_class')}")
        print(f"  workflow_sequence: {route.get('workflow_sequence')}")
        print(f"  workflow_id: {route.get('workflow_id')}")
        print(f"  families: {impact.get('affected_declarative_families')}")
        print(f"  paths: {impact.get('affected_bundle_paths')}")
        print(f"  scope: {impact.get('scope_summary')}")
        if case.get("violations"):
            print(f"  violations: {case.get('violations')}")
    if payload.get("violations"):
        print("")
        print("Violations:")
        for violation in payload.get("violations") or []:
            print(f"  - {violation}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the live refinement classifier smoke harness.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    parser.add_argument(
        "--save-fixture",
        action="store_true",
        help=f"Save output to {FIXTURE_PATH.relative_to(REPO_ROOT)} for fixture replay tests.",
    )
    args = parser.parse_args(argv)

    payload = asyncio.run(run_smoke())
    if args.save_fixture and payload.get("success"):
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        payload["fixture_saved"] = str(FIXTURE_PATH.relative_to(REPO_ROOT))
    elif args.save_fixture:
        payload["fixture_saved"] = None

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)
        if args.save_fixture:
            saved = payload.get("fixture_saved") or "not saved"
            print(f"\nFixture: {saved}")
    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
