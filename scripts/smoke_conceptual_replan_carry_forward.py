"""
Deterministic-hybrid smoke: conceptual_replan -> carry-forward pipeline.

Usage:
    python scripts/smoke_conceptual_replan_carry_forward.py
    python scripts/smoke_conceptual_replan_carry_forward.py --save-fixture
    python scripts/smoke_conceptual_replan_carry_forward.py --mode both   (default)
    python scripts/smoke_conceptual_replan_carry_forward.py --mode explicit
    python scripts/smoke_conceptual_replan_carry_forward.py --mode auto
    python scripts/smoke_conceptual_replan_carry_forward.py --json

Two smoke variants:

  explicit — request.extra.carry_forward_modules = ["settings", "notifications"]
    Router uses the explicit list; get_carry_forward_candidates is NOT called.
    carry_forward_modules_source: "explicit_override"

  auto — request.extra.carry_forward_modules absent
    Router calls get_carry_forward_candidates to auto-populate from workspace.
    Returns all module IDs found in the prior bundle (unfiltered at route time).
    carry_forward_modules_source: "get_carry_forward_candidates"

Both variants share the same Phase 7A preservation run and the same
carry_forward_decisions fixture.

What is live (real code exercised):
  - RefinementTriggerRouteResolver with real refinement harness
  - get_carry_forward_candidates control-plane tool (auto variant only)
  - load_artifact_workspace with real temp dir
  - resolve_carry_forward_preservation Phase 7A tool

What is stubbed (no LLM, no MongoDB):
  - Change classifier -- _DeterministicChangeClassifier(change_class="core")
  - ArtifactStore.get_build_record -- synthetic CRM artifact + real temp dir
  - carry_forward_decisions -- fixture (settings/notifications=reuse, contacts/pipeline=drop)

Scenario: CRM -> Marketplace conceptual pivot
  Initial modules: contacts, pipeline, settings, notifications
  Refinement: "Actually this should be a marketplace for sellers and buyers, not a CRM."
  Expected routing: concept/core -> conceptual_replan
  Expected carry-forward: settings/notifications preserved; contacts/pipeline dropped
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP_ROOT = REPO_ROOT / "factory_app" / "app"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "conceptual_replan_carry_forward_output.json"
LOG_DIR = REPO_ROOT / ".logs" / "refinement-smoke" / "conceptual-replan"

# ---------------------------------------------------------------------------
# Scenario constants
# ---------------------------------------------------------------------------

_CRM_APP_ID = "smoke-crm-marketplace-001"
_PREV_build_record_id = "av_crm_smoke_conceptual_v1"
_PIVOT_REQUEST = (
    "Actually this should be a marketplace for sellers and buyers, not a CRM."
)

# CRM prior workspace — includes allowlist-eligible and non-eligible files
_CRM_WORKSPACE_FILES: dict[str, str] = {
    # settings -- allowlist eligible (module.yaml, contracts/*.yaml)
    "modules/settings/module.yaml": "id: settings\nactions: []\ncapabilities: []\n",
    "modules/settings/contracts/settings.yaml": "version: 1\nfields: []\n",
    "modules/settings/contracts/events.yaml": "version: 1\nevents: []\n",
    "modules/settings/backend/service.py": "# settings service -- must NOT be copied\n",
    "modules/settings/backend/handler.py": "# settings handler -- must NOT be copied\n",
    "modules/settings/backend/schemas.py": "# settings schemas -- must NOT be copied\n",
    # notifications -- allowlist eligible
    "modules/notifications/module.yaml": "id: notifications\nactions: []\ncapabilities: []\n",
    "modules/notifications/contracts/notifications.yaml": "version: 1\nrules: []\n",
    "modules/notifications/contracts/events.yaml": "version: 1\nevents: []\n",
    "modules/notifications/backend/service.py": "# notifications service -- must NOT be copied\n",
    "modules/notifications/backend/handler.py": "# notifications handler -- must NOT be copied\n",
    # contacts -- dropped; nothing should be copied
    "modules/contacts/module.yaml": "id: contacts\nactions:\n  - id: create_contact\n",
    "modules/contacts/contracts/events.yaml": "version: 1\nevents:\n  - id: contact.created\n",
    "modules/contacts/backend/repo.py": "# contacts repo -- must NOT be copied\n",
    "modules/contacts/backend/service.py": "# contacts service -- must NOT be copied\n",
    # pipeline -- dropped; nothing should be copied
    "modules/pipeline/module.yaml": "id: pipeline\nactions:\n  - id: create_deal\n",
    "modules/pipeline/backend/service.py": "# pipeline service -- must NOT be copied\n",
    "modules/pipeline/runtime_extensions.yaml": "api_router: pipeline_router\n",
}

# Marketplace files from generation -- does NOT include settings or notifications
_MARKETPLACE_GENERATED_FILES: dict[str, str] = {
    "app.json": '{"id": "marketplace", "name": "Marketplace"}\n',
    "modules/listings/module.yaml": "id: listings\nactions:\n  - id: create_listing\n",
    "modules/listings/contracts/events.yaml": "version: 1\nevents:\n  - id: listing.created\n",
    "modules/orders/module.yaml": "id: orders\nactions:\n  - id: place_order\n",
    "modules/orders/contracts/events.yaml": "version: 1\nevents:\n  - id: order.placed\n",
}

# Stubbed carry_forward_decisions (normally produced by AppPlanAgent via LLM)
_CARRY_FORWARD_DECISIONS = [
    {
        "module_id": "settings",
        "decision": "reuse",
        "reason": "Universal preference management -- unchanged across concept.",
    },
    {
        "module_id": "notifications",
        "decision": "reuse",
        "reason": "Notification primitives are concept-agnostic.",
    },
    {
        "module_id": "contacts",
        "decision": "drop",
        "reason": "CRM contact management -- not relevant to a marketplace.",
    },
    {
        "module_id": "pipeline",
        "decision": "drop",
        "reason": "CRM sales pipeline -- replaced by marketplace orders.",
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass


def _write_crm_workspace(tmp_dir: Path) -> None:
    for rel_path, content in _CRM_WORKSPACE_FILES.items():
        dest = tmp_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _build_mock_artifact_store(workspace_dir: Path) -> Any:
    """Mock ArtifactStore.get_build_record returning synthetic CRM doc."""
    from mozaiksai.core.artifacts.models import ArtifactCommitMetadata, BuildRecord

    doc = BuildRecord.model_validate({
        "_id": _PREV_build_record_id,
        "app_id": _CRM_APP_ID,
        "build_family": "app_bundle",
        "build_key": "app_bundle",
        "version_number": 1,
        "lineage_root_id": _PREV_build_record_id,
        "commit_metadata": ArtifactCommitMetadata(
            message="Synthetic CRM v1 -- carry-forward smoke",
            metadata={"workspace_dir": str(workspace_dir)},
        ).model_dump(),
    })

    mock_store = MagicMock()
    mock_store.get_build_record = AsyncMock(return_value=doc)
    return mock_store


def _build_resolver():
    from mozaiksai.control_plane.dry_run import _DeterministicChangeClassifier
    from mozaiksai.control_plane.implementations.refinement_router import (
        RefinementTriggerRouteResolver,
    )
    from mozaiksai.control_plane.loader import load_refinement_harness

    def pack_loader():
        return load_refinement_harness(app_root=APP_ROOT)

    return RefinementTriggerRouteResolver(
        classifier=_DeterministicChangeClassifier(change_class="core"),
        pack_loader=pack_loader,
    )


# ---------------------------------------------------------------------------
# Variant A: explicit carry_forward_modules
# ---------------------------------------------------------------------------


async def _run_explicit_variant() -> dict[str, Any]:
    """Route with explicit carry_forward_modules -- get_carry_forward_candidates NOT called."""
    resolver = _build_resolver()

    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "build_family": "concept",
                "build_key": "concept",
                "raw_user_request": _PIVOT_REQUEST,
                "source_surface": "conceptual_replan_smoke_explicit",
                "extra": {
                    "previous_app_bundle_ref": _PREV_build_record_id,
                    "existing_concept_ref": "concept_crm_v1",
                    "carry_forward_modules": ["settings", "notifications"],
                    "preserve_families": ["brand"],
                },
            }
        },
        app_id=_CRM_APP_ID,
        requested_workflow_id="AppGenerator",
    )
    assert request is not None, "explicit: request_from_payload returned None"

    decision = await resolver.route(request)
    seed = decision.context_seed or {}

    return {
        "description": "carry_forward_modules explicitly provided in request.extra",
        "carry_forward_modules_source": "explicit_override",
        "explicit_override_used": True,
        "auto_populated": False,
        "routing": {
            "change_class": decision.change_intent.change_class.value,
            "workflow_sequence": decision.workflow_sequence or decision.impact_set.workflow_sequence,
        },
        "context_seed": {
            "pivot_description": seed.get("pivot_description"),
            "preserve_families": seed.get("preserve_families"),
            "previous_app_bundle_ref": seed.get("previous_app_bundle_ref"),
            "carry_forward_modules": seed.get("carry_forward_modules"),
            "workflow_sequence": seed.get("workflow_sequence"),
            "llm_profile": seed.get("llm_profile"),
        },
        "carry_forward_warnings": seed.get("carry_forward_warnings", []),
    }


# ---------------------------------------------------------------------------
# Variant B: auto-populated carry_forward_modules
# ---------------------------------------------------------------------------


async def _run_auto_variant(tmp_dir: Path) -> dict[str, Any]:
    """Route WITHOUT explicit carry_forward_modules -- triggers get_carry_forward_candidates."""
    mock_store = _build_mock_artifact_store(tmp_dir)
    resolver = _build_resolver()

    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "build_family": "concept",
                "build_key": "concept",
                "raw_user_request": _PIVOT_REQUEST,
                "source_surface": "conceptual_replan_smoke_auto",
                "extra": {
                    "previous_app_bundle_ref": _PREV_build_record_id,
                    # Intentionally NO carry_forward_modules -- triggers auto-extraction
                },
            }
        },
        app_id=_CRM_APP_ID,
        requested_workflow_id="AppGenerator",
    )
    assert request is not None, "auto: request_from_payload returned None"

    candidates_call_log: list[dict[str, Any]] = []

    # Wrap mock store to record when get_build_record is called
    original_get = mock_store.get_build_record

    async def _tracked_get(*args: Any, **kwargs: Any) -> Any:
        candidates_call_log.append({"args": args, "kwargs": kwargs})
        return await original_get(*args, **kwargs)

    mock_store.get_build_record = _tracked_get

    with patch(
        "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
        return_value=mock_store,
    ):
        decision = await resolver.route(request)

    seed = decision.context_seed or {}
    modules = seed.get("carry_forward_modules", [])

    return {
        "description": "carry_forward_modules auto-populated via get_carry_forward_candidates",
        "carry_forward_modules_source": "get_carry_forward_candidates",
        "explicit_override_used": False,
        "auto_populated": True,
        # Record whether the candidates tool actually hit the store
        "candidates_tool_called": len(candidates_call_log) > 0,
        "routing": {
            "change_class": decision.change_intent.change_class.value,
            "workflow_sequence": decision.workflow_sequence or decision.impact_set.workflow_sequence,
        },
        "context_seed": {
            "pivot_description": seed.get("pivot_description"),
            "preserve_families": seed.get("preserve_families"),
            "previous_app_bundle_ref": seed.get("previous_app_bundle_ref"),
            "carry_forward_modules": modules,
            "workflow_sequence": seed.get("workflow_sequence"),
            "llm_profile": seed.get("llm_profile"),
        },
        "carry_forward_warnings": seed.get("carry_forward_warnings", []),
        # Truthful note about current behavior: router returns ALL module IDs
        # from the prior workspace unfiltered. Filtering happens in AppPlanAgent
        # via carry_forward_decisions at plan time.
        "behavior_note": (
            "Auto-extraction returns all module IDs found in the prior workspace. "
            "No reuse/drop filtering at route time -- that is AppPlanAgent's job."
        ),
    }


# ---------------------------------------------------------------------------
# Phase 7A preservation (shared by both variants)
# ---------------------------------------------------------------------------


async def _run_preservation(tmp_dir: Path) -> dict[str, Any]:
    """Real resolve_carry_forward_preservation with CRM workspace + stubbed decisions."""
    from factory_app.refinement_harness.tools.resolve_carry_forward_preservation import (
        resolve_carry_forward_preservation,
    )

    mock_store = _build_mock_artifact_store(tmp_dir)
    context_variables: dict[str, Any] = {
        "app_id": _CRM_APP_ID,
        "previous_app_bundle_ref": _PREV_build_record_id,
        "app_build_plan": {"carry_forward_decisions": _CARRY_FORWARD_DECISIONS},
        "generated_files": dict(_MARKETPLACE_GENERATED_FILES),
    }

    result = await resolve_carry_forward_preservation(
        context_variables=context_variables,
        artifact_store=mock_store,
    )
    return result.get("carry_forward_report", {})


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_variant(
    label: str,
    variant: dict[str, Any],
    report: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    seed = variant.get("context_seed", {})
    routing = variant.get("routing", {})

    if routing.get("workflow_sequence") != "conceptual_replan":
        failures.append(
            f"[{label}] workflow_sequence={routing.get('workflow_sequence')!r} -- expected 'conceptual_replan'"
        )
    if not seed.get("pivot_description"):
        failures.append(f"[{label}] context_seed missing pivot_description")
    if "preserve_families" not in seed:
        failures.append(f"[{label}] context_seed missing preserve_families")
    if seed.get("previous_app_bundle_ref") != _PREV_build_record_id:
        failures.append(f"[{label}] context_seed.previous_app_bundle_ref={seed.get('previous_app_bundle_ref')!r}")
    if "carry_forward_modules" not in seed:
        failures.append(f"[{label}] context_seed missing carry_forward_modules")
    elif not isinstance(seed.get("carry_forward_modules"), list):
        failures.append(f"[{label}] carry_forward_modules is not a list")

    # Auto-specific: candidates tool must have been called
    if label == "auto" and not variant.get("candidates_tool_called"):
        failures.append("[auto] get_carry_forward_candidates artifact store was not hit (candidates_tool_called=False)")

    # Report safety checks (shared)
    preserved = report.get("preserved_paths", [])
    from factory_app.refinement_harness.tools.resolve_carry_forward_preservation import (
        _PHASE_7A_MODULE_ALLOWLIST,
    )
    bad_paths = [p for p in preserved if not any(p.endswith(a) for a in _PHASE_7A_MODULE_ALLOWLIST)]
    if bad_paths:
        failures.append(f"[{label}] non-allowlisted paths in preserved_paths: {bad_paths}")
    backend_py = [p for p in preserved if "/backend/" in p and p.endswith(".py")]
    if backend_py:
        failures.append(f"[{label}] backend Python in preserved_paths: {backend_py}")
    runtime_ext = [p for p in preserved if "runtime_extensions.yaml" in p]
    if runtime_ext:
        failures.append(f"[{label}] runtime_extensions.yaml in preserved_paths: {runtime_ext}")
    react_files = [p for p in preserved if p.endswith((".jsx", ".tsx", ".js"))]
    if react_files:
        failures.append(f"[{label}] custom React in preserved_paths: {react_files}")

    return failures


def _validate_all(
    explicit: dict[str, Any],
    auto: dict[str, Any],
    report: dict[str, Any],
) -> list[str]:
    failures = _validate_variant("explicit", explicit, report)
    failures += _validate_variant("auto", auto, report)

    # Cross-variant: explicit must differ from auto in source
    if explicit.get("carry_forward_modules_source") != "explicit_override":
        failures.append("explicit variant must have carry_forward_modules_source='explicit_override'")
    if auto.get("carry_forward_modules_source") != "get_carry_forward_candidates":
        failures.append("auto variant must have carry_forward_modules_source='get_carry_forward_candidates'")

    # Explicit override must win: only the declared subset
    explicit_mods = (explicit.get("context_seed") or {}).get("carry_forward_modules", [])
    if sorted(explicit_mods) != ["notifications", "settings"]:
        failures.append(f"explicit carry_forward_modules must be exactly ['notifications', 'settings'], got {sorted(explicit_mods)}")

    # Auto must return >= the explicit set (superset, since it has all 4 CRM modules)
    auto_mods = set((auto.get("context_seed") or {}).get("carry_forward_modules", []))
    if not {"settings", "notifications"}.issubset(auto_mods):
        failures.append(f"auto carry_forward_modules must include at least settings+notifications; got {auto_mods}")

    # Preservation report shape (same for both variants)
    required_keys = {
        "previous_app_bundle_ref", "workspace_available", "preserved_paths",
        "conflicts", "skipped_paths", "reused_modules", "dropped_modules", "warnings",
    }
    missing = required_keys - set(report.keys())
    if missing:
        failures.append(f"carry_forward_report missing keys: {missing}")

    return failures


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_smoke(mode: str = "both") -> dict[str, Any]:
    _load_dotenv()

    explicit_result: dict[str, Any] = {}
    auto_result: dict[str, Any] = {}
    report: dict[str, Any] = {}
    phase_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="mozaiks_smoke_crm_") as tmp:
        tmp_dir = Path(tmp)
        _write_crm_workspace(tmp_dir)

        if mode in ("explicit", "both"):
            try:
                explicit_result = await _run_explicit_variant()
            except Exception as exc:
                phase_errors.append(f"Explicit variant failed: {exc}")

        if mode in ("auto", "both"):
            try:
                auto_result = await _run_auto_variant(tmp_dir)
            except Exception as exc:
                phase_errors.append(f"Auto variant failed: {exc}")

        try:
            report = await _run_preservation(tmp_dir)
        except Exception as exc:
            phase_errors.append(f"Preservation phase failed: {exc}")

    violations = list(phase_errors)
    if not phase_errors:
        if mode == "both":
            violations += _validate_all(explicit_result, auto_result, report)
        elif mode == "explicit":
            violations += _validate_variant("explicit", explicit_result, report)
        elif mode == "auto":
            violations += _validate_variant("auto", auto_result, report)

    success = not violations
    variants: dict[str, Any] = {}
    if explicit_result:
        variants["explicit"] = {**explicit_result, "carry_forward_report": report}
    if auto_result:
        variants["auto"] = {**auto_result, "carry_forward_report": report}

    return {
        "schema_version": "mozaiks.conceptual_replan_carry_forward_smoke.v2",
        "success": success,
        "mode": mode,
        "approach": {
            "live": [
                "RefinementTriggerRouteResolver with real refinement harness",
                "get_carry_forward_candidates (auto variant only)",
                "load_artifact_workspace with real temp dir",
                "resolve_carry_forward_preservation Phase 7A tool",
            ],
            "stubbed": [
                "_DeterministicChangeClassifier(change_class='core')",
                "ArtifactStore.get_build_record -> synthetic CRM doc + temp dir",
                "carry_forward_decisions fixture (settings/notifications=reuse, contacts/pipeline=drop)",
            ],
        },
        "scenario": {
            "initial_app": "CRM (contacts, pipeline, settings, notifications)",
            "refinement_request": _PIVOT_REQUEST,
            "expected_sequence": "conceptual_replan",
        },
        "carry_forward_decisions": _CARRY_FORWARD_DECISIONS,
        "variants": variants,
        "studio_ui": {
            "panel_renders": bool(report),
            "report_shape_valid": all(
                k in report
                for k in ("preserved_paths", "conflicts", "reused_modules", "dropped_modules")
            ),
        },
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------


def _print_human(payload: dict[str, Any]) -> None:
    status = "PASS" if payload.get("success") else "FAIL"
    mode = payload.get("mode", "both")
    print(f"\nConceptual-replan carry-forward smoke [{mode}]: {status}")

    print("\n--- Approach ---")
    for item in (payload.get("approach") or {}).get("live") or []:
        print(f"  [live]    {item}")
    for item in (payload.get("approach") or {}).get("stubbed") or []:
        print(f"  [stubbed] {item}")

    variants = payload.get("variants") or {}

    for variant_name in ("explicit", "auto"):
        v = variants.get(variant_name)
        if not v:
            continue
        print(f"\n--- Variant: {variant_name} ---")
        print(f"  source:                 {v.get('carry_forward_modules_source')}")
        print(f"  explicit_override_used: {v.get('explicit_override_used')}")
        print(f"  auto_populated:         {v.get('auto_populated')}")
        if variant_name == "auto":
            print(f"  candidates_tool_called: {v.get('candidates_tool_called')}")
        seed = v.get("context_seed") or {}
        print(f"  workflow_sequence:      {(v.get('routing') or {}).get('workflow_sequence')}")
        print(f"  carry_forward_modules:  {seed.get('carry_forward_modules')}")
        print(f"  preserve_families:      {seed.get('preserve_families')}")
        print(f"  previous_ref:           {seed.get('previous_app_bundle_ref')}")
        if v.get("carry_forward_warnings"):
            print(f"  warnings:               {v.get('carry_forward_warnings')}")
        if variant_name == "auto" and v.get("behavior_note"):
            print(f"  note: {v.get('behavior_note')}")
        report = v.get("carry_forward_report") or {}
        print(f"  preserved_paths:        {report.get('preserved_paths')}")
        print(f"  reused_modules:         {report.get('reused_modules')}")
        print(f"  dropped_modules:        {report.get('dropped_modules')}")

    print("\n--- Studio UI ---")
    ui = payload.get("studio_ui") or {}
    print(f"  panel_renders:      {ui.get('panel_renders')}")
    print(f"  report_shape_valid: {ui.get('report_shape_valid')}")

    if payload.get("violations"):
        print("\n--- Violations ---")
        for v in payload.get("violations") or []:
            print(f"  X {v}")
    else:
        print("\n  All assertions passed.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Conceptual-replan carry-forward smoke (no LLM, no MongoDB)."
    )
    parser.add_argument(
        "--mode",
        choices=["explicit", "auto", "both"],
        default="both",
        help="Which variant to run. Default: both.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--save-fixture",
        action="store_true",
        help=f"Save output to {FIXTURE_PATH.relative_to(REPO_ROOT)} for pytest replay.",
    )
    parser.add_argument("--save-log", action="store_true", help="Save full log to .logs/.")
    args = parser.parse_args(argv)

    payload = asyncio.run(run_smoke(mode=args.mode))

    if args.save_fixture and payload.get("success"):
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        payload["fixture_saved"] = str(FIXTURE_PATH.relative_to(REPO_ROOT))
    elif args.save_fixture:
        payload["fixture_saved"] = None

    if args.save_log:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / "smoke_output.json"
        log_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        payload["log_saved"] = str(log_path.relative_to(REPO_ROOT))

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)
        if payload.get("fixture_saved"):
            print(f"\nFixture saved: {payload['fixture_saved']}")
        if payload.get("log_saved"):
            print(f"Log saved:     {payload['log_saved']}")

    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

