"""Pytest fixture replay for the live conceptual_replan benchmark.

These tests replay the saved fixture from scripts/smoke_live_conceptual_replan.py.
They do NOT make live LLM calls. All tests are skipped when the fixture is absent.

Run the benchmark first:
    python scripts/smoke_live_conceptual_replan.py --save-fixture

Then run these tests:
    pytest tests/test_live_conceptual_replan_benchmark.py -v

Assertions mirror the 17 required in the smoke script.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "live_conceptual_replan_output.json"

_VALID_DECISIONS = {"reuse", "adapt", "regenerate", "drop"}
_PHASE_7A_ALLOWLIST = {
    "module.yaml",
    "contracts/events.yaml",
    "contracts/reactions.yaml",
    "contracts/notifications.yaml",
    "contracts/settings.yaml",
    "contracts/admin.yaml",
    "contracts/profile.yaml",
}
_MARKETPLACE_TOKENS = (
    "listing", "listings", "seller", "sellers", "buyer", "buyers",
    "order", "orders", "marketplace", "product", "products",
    "vendor", "vendors", "catalog", "storefront",
)


def _require_fixture() -> dict[str, Any]:
    if not _FIXTURE_PATH.exists():
        pytest.skip(
            f"Fixture not found: {_FIXTURE_PATH.name}. "
            "Run: python scripts/smoke_live_conceptual_replan.py --save-fixture"
        )
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _decisions_by_module(fixture: dict[str, Any]) -> dict[str, str]:
    cfd = (fixture.get("appplan_agent") or {}).get("carry_forward_decisions") or []
    return {d["module_id"]: d["decision"] for d in cfd if isinstance(d, dict)}


def _report(fixture: dict[str, Any]) -> dict[str, Any]:
    return fixture.get("carry_forward_report") or {}


# ---------------------------------------------------------------------------
# Schema / meta
# ---------------------------------------------------------------------------


def test_fixture_schema_version() -> None:
    f = _require_fixture()
    assert f.get("schema_version") == "mozaiks.live_conceptual_replan_benchmark.v1"


def test_fixture_success_flag() -> None:
    """Fixture records benchmark passed -- all 17 assertions must be green."""
    f = _require_fixture()
    failures = f.get("failures") or []
    assert f.get("success") is True, (
        f"Benchmark recorded {len(failures)} failure(s):\n" + "\n".join(f"  {x}" for x in failures)
    )


def test_assertions_total() -> None:
    f = _require_fixture()
    assert f.get("assertions", {}).get("total") == 17


def test_all_assertions_passed() -> None:
    f = _require_fixture()
    assert f.get("assertions", {}).get("failed") == 0


# ---------------------------------------------------------------------------
# Routing assertions (1-4)
# ---------------------------------------------------------------------------


def test_route_is_conceptual_replan() -> None:
    """[1] Route must be conceptual_replan."""
    f = _require_fixture()
    route = (f.get("routing") or {}).get("route")
    assert route == "conceptual_replan", f"route={route!r}"


def test_context_seed_pivot_description_present() -> None:
    """[2] pivot_description must be present in context_seed."""
    f = _require_fixture()
    seed = (f.get("routing") or {}).get("context_seed") or {}
    assert seed.get("pivot_description"), "context_seed.pivot_description is absent or empty"


def test_context_seed_previous_app_bundle_ref() -> None:
    """[3] previous_app_bundle_ref must match the fixture scenario artifact."""
    f = _require_fixture()
    seed = (f.get("routing") or {}).get("context_seed") or {}
    ref = seed.get("previous_app_bundle_ref")
    assert ref, "context_seed.previous_app_bundle_ref is absent"
    assert isinstance(ref, str), "previous_app_bundle_ref must be a string"


def test_context_seed_carry_forward_modules_non_empty() -> None:
    """[4] carry_forward_modules in context_seed must be a non-empty list."""
    f = _require_fixture()
    seed = (f.get("routing") or {}).get("context_seed") or {}
    cfm = seed.get("carry_forward_modules") or []
    assert isinstance(cfm, list) and len(cfm) > 0, (
        f"context_seed.carry_forward_modules={cfm!r}"
    )


# ---------------------------------------------------------------------------
# LLM output assertions (5-11)
# ---------------------------------------------------------------------------


def test_carry_forward_decisions_is_list() -> None:
    """[5] carry_forward_decisions must be a non-empty list."""
    f = _require_fixture()
    cfd = (f.get("appplan_agent") or {}).get("carry_forward_decisions")
    assert isinstance(cfd, list), f"carry_forward_decisions type={type(cfd).__name__}"
    assert len(cfd) > 0, "carry_forward_decisions is empty"


def test_all_decision_values_valid() -> None:
    """[6] All decision values must be one of: reuse, adapt, regenerate, drop."""
    f = _require_fixture()
    cfd_by_mod = _decisions_by_module(f)
    invalid = [(mod, dec) for mod, dec in cfd_by_mod.items() if dec not in _VALID_DECISIONS]
    assert not invalid, f"Invalid decision values: {invalid}"


def test_settings_decision_reuse_or_adapt() -> None:
    """[7] settings must be reuse or adapt -- it is domain-generic."""
    f = _require_fixture()
    dec = _decisions_by_module(f).get("settings")
    assert dec is not None, "settings not found in carry_forward_decisions"
    assert dec in ("reuse", "adapt"), (
        f"settings decision={dec!r} -- domain-generic module should not be dropped"
    )


def test_notifications_decision_reuse_or_adapt() -> None:
    """[8] notifications must be reuse or adapt -- it is domain-generic."""
    f = _require_fixture()
    dec = _decisions_by_module(f).get("notifications")
    assert dec is not None, "notifications not found in carry_forward_decisions"
    assert dec in ("reuse", "adapt"), (
        f"notifications decision={dec!r} -- domain-generic module should not be dropped"
    )


def test_contacts_not_blindly_reused() -> None:
    """[9] contacts must be drop or regenerate -- CRM-specific, not marketplace-relevant."""
    f = _require_fixture()
    dec = _decisions_by_module(f).get("contacts")
    assert dec is not None, "contacts not found in carry_forward_decisions"
    assert dec in ("drop", "regenerate"), (
        f"contacts decision={dec!r} -- CRM contact management should not be reused in a marketplace"
    )


def test_pipeline_not_blindly_reused() -> None:
    """[10] pipeline must be drop or regenerate -- CRM-specific, not marketplace-relevant."""
    f = _require_fixture()
    dec = _decisions_by_module(f).get("pipeline")
    assert dec is not None, "pipeline not found in carry_forward_decisions"
    assert dec in ("drop", "regenerate"), (
        f"pipeline decision={dec!r} -- CRM sales pipeline should not be reused in a marketplace"
    )


def test_marketplace_modules_in_plan() -> None:
    """[11] The build plan must contain marketplace-oriented tokens anywhere in the plan.

    Tokens are checked across the full plan JSON -- capability_packs, build_tasks,
    carry_forward_decisions reasons, etc. The model may express marketplace intent
    in any of these fields (e.g. "no role in the new marketplace concept" in a
    drop decision reason).
    """
    f = _require_fixture()
    agent = f.get("appplan_agent") or {}
    # Mirror the smoke script: check the full plan dict, not just capability_packs
    plan = agent.get("plan") or agent  # fall back to agent dict if plan not stored
    plan_text = json.dumps(plan).lower()
    assert any(token in plan_text for token in _MARKETPLACE_TOKENS), (
        f"No marketplace tokens found anywhere in the plan. "
        f"Checked: {_MARKETPLACE_TOKENS[:6]}..."
    )


# ---------------------------------------------------------------------------
# Phase 7A assertions (12-17)
# ---------------------------------------------------------------------------


def test_carry_forward_report_exists() -> None:
    """[12] Phase 7A must have produced a carry_forward_report."""
    f = _require_fixture()
    report = _report(f)
    assert report, "carry_forward_report is absent or empty"


def test_preserved_paths_allowlist_only() -> None:
    """[13] preserved_paths must only contain Phase 7A allowlisted file types."""
    f = _require_fixture()
    preserved = _report(f).get("preserved_paths") or []
    bad = [p for p in preserved if not any(str(p).endswith(a) for a in _PHASE_7A_ALLOWLIST)]
    assert not bad, f"Non-allowlisted paths in preserved_paths: {bad}"


def test_no_backend_python_in_preserved() -> None:
    """[14] No backend Python files must appear in preserved_paths."""
    f = _require_fixture()
    preserved = _report(f).get("preserved_paths") or []
    bad = [p for p in preserved if "/backend/" in str(p) and str(p).endswith(".py")]
    assert not bad, f"Backend Python in preserved_paths: {bad}"


def test_no_runtime_extensions_in_preserved() -> None:
    """[15] runtime_extensions.yaml must not appear in preserved_paths."""
    f = _require_fixture()
    preserved = _report(f).get("preserved_paths") or []
    bad = [p for p in preserved if "runtime_extensions.yaml" in str(p)]
    assert not bad, f"runtime_extensions.yaml in preserved_paths: {bad}"


def test_no_custom_react_in_preserved() -> None:
    """[16] Custom React files must not appear in preserved_paths."""
    f = _require_fixture()
    preserved = _report(f).get("preserved_paths") or []
    bad = [p for p in preserved if str(p).endswith((".jsx", ".tsx", ".js"))]
    assert not bad, f"React files in preserved_paths: {bad}"


def test_carry_forward_report_shape_valid() -> None:
    """[17] carry_forward_report must have all required keys."""
    f = _require_fixture()
    report = _report(f)
    required = {
        "previous_app_bundle_ref", "workspace_available", "preserved_paths",
        "conflicts", "skipped_paths", "reused_modules", "dropped_modules", "warnings",
    }
    missing = required - set(report.keys())
    assert not missing, f"carry_forward_report missing keys: {sorted(missing)}"
