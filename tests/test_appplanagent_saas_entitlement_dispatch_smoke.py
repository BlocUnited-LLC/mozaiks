"""
AppPlanAgent self-hosted SaaS entitlement_dispatch smoke test.

Two test modes:

  Live LLM (manual only):
    The TestAppPlanAgentSaasLiveRun class is always skipped in CI.
    Run the smoke script instead:
        python scripts/smoke_appplan_saas_entitlement_dispatch.py
        python scripts/smoke_appplan_saas_entitlement_dispatch.py --save-fixture

  Fixture replay (CI-compatible):
    TestAppPlanAgentSaasFixtureReplay loads and validates the fixture at
    tests/fixtures/appplan_saas_entitlement_dispatch_output.json (if present).
    All tests skip when the fixture is absent.

To generate the fixture for CI replay:
    1. Set OPENAI_API_KEY in your environment (or .env).
    2. Run: python scripts/smoke_appplan_saas_entitlement_dispatch.py --save-fixture
    3. Commit tests/fixtures/appplan_saas_entitlement_dispatch_output.json.
    4. Future CI runs validate the saved plan without calling the LLM.

Validated invariants (fixture replay):
    1. Plan validates with app_build_plan.py without raising.
    2. No managed assignment writer such as the mozaikspay managed_capability pack.
    3. subscription_config task present with config/subscriptions.yaml path.
    4. entitlement_dispatch module_contract task present.
    5. entitlement_dispatch module_contract task owns modules/entitlement_dispatch/module.yaml.
    6. entitlement_dispatch business_services task present.
    7. page_bundle task present.
    8. No owned_paths under modules/entitlement_dispatch/ outside designated tasks.
    9. Dependency graph has no unknown task ids.
    10. generation_order present.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_WORKSPACE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools"
_FIXTURE_PATH = _WORKSPACE / "tests" / "fixtures" / "appplan_saas_entitlement_dispatch_output.json"

_FIXTURE_PRESENT = _FIXTURE_PATH.exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module(relative_path: str, module_name: str):
    file_path = _WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Context:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


def _load_fixture() -> dict[str, Any]:
    raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    if "AppBuildPlan" in raw:
        return raw["AppBuildPlan"]
    return raw


def _get_tasks_by_type(build_tasks: list[dict[str, Any]], task_type: str) -> list[dict[str, Any]]:
    return [t for t in build_tasks if isinstance(t, dict) and t.get("task_type") == task_type]


def _get_tasks_by_cap(
    build_tasks: list[dict[str, Any]], task_type: str, cap_id: str
) -> list[dict[str, Any]]:
    return [
        t for t in build_tasks
        if isinstance(t, dict)
        and t.get("task_type") == task_type
        and t.get("capability_pack_id") == cap_id
    ]


# ---------------------------------------------------------------------------
# Live LLM class (always skipped in CI — run via smoke script)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "Live LLM test — not run in CI. "
        "To run manually: python scripts/smoke_appplan_saas_entitlement_dispatch.py\n"
        "To generate fixture for CI replay: "
        "python scripts/smoke_appplan_saas_entitlement_dispatch.py --save-fixture"
    )
)
class TestAppPlanAgentSaasLiveRun:
    """
    Documents the manual smoke script entry point.

    Run instructions:
        cd mozaiks
        python scripts/smoke_appplan_saas_entitlement_dispatch.py
        python scripts/smoke_appplan_saas_entitlement_dispatch.py --model gpt-4o --save-fixture

    Expected output:
        - subscription_config task with config/subscriptions.yaml.
        - entitlement_dispatch module_contract + business_services tasks.
        - No managed assignment writer such as the mozaikspay managed_capability pack.
        - Validation: PASSED.
    """

    def test_live_run_placeholder(self) -> None:
        raise AssertionError("This test must never run in CI — use the smoke script.")


# ---------------------------------------------------------------------------
# Fixture replay tests (CI-compatible, skip if fixture absent)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _FIXTURE_PRESENT,
    reason=(
        "Fixture not present. Generate with: "
        "python scripts/smoke_appplan_saas_entitlement_dispatch.py --save-fixture"
    ),
)
class TestAppPlanAgentSaasFixtureReplay:
    """
    Validates the saved AppBuildPlan fixture without calling the LLM.

    All tests skip when tests/fixtures/appplan_saas_entitlement_dispatch_output.json is absent.
    To generate the fixture: python scripts/smoke_appplan_saas_entitlement_dispatch.py --save-fixture
    """

    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.plan = _load_fixture()
        self.capability_packs = self.plan.get("capability_packs") or []
        self.build_tasks = self.plan.get("build_tasks") or []

    # --- Plan-level structure ---

    def test_fixture_plan_validates_with_app_build_plan_tool(self) -> None:
        """Plan must pass app_build_plan.py validation without raising."""
        mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            f"tests.saas_dispatch_fixture_validation.{id(self)}",
        )
        ctx = _Context()
        result = mod.app_build_plan(AppBuildPlan=self.plan, context_variables=ctx)
        assert ctx.data.get("app_plan_ready") is True, (
            f"app_plan_ready expected True, got {ctx.data.get('app_plan_ready')!r}. "
            f"Validation result: {result}"
        )

    def test_fixture_has_capability_packs(self) -> None:
        assert len(self.capability_packs) > 0, "Plan has no capability_packs"

    def test_fixture_has_build_tasks(self) -> None:
        assert len(self.build_tasks) > 0, "Plan has no build_tasks"

    def test_fixture_has_pages(self) -> None:
        assert len(self.plan.get("pages") or []) > 0, "Plan has no pages"

    # --- No mozaikspay managed pack ---

    def test_no_mozaikspay_managed_capability_pack(self) -> None:
        bad = [
            p for p in self.capability_packs
            if isinstance(p, dict)
            and p.get("capability_pack_id") == "mozaikspay"
            and p.get("capability_source") == "managed_capability"
        ]
        assert not bad, (
            "Self-hosted SaaS plan must not include mozaikspay as managed_capability. "
            f"Found: {[p.get('capability_pack_id') for p in bad]}"
        )

    # --- subscription_config task ---

    def test_subscription_config_task_present(self) -> None:
        tasks = _get_tasks_by_type(self.build_tasks, "subscription_config")
        assert tasks, (
            "Missing subscription_config task. "
            "AppPlanAgent must plan config/subscriptions.yaml when contract_required is true."
        )

    def test_subscription_config_task_owns_subscriptions_yaml(self) -> None:
        tasks = _get_tasks_by_type(self.build_tasks, "subscription_config")
        if not tasks:
            pytest.skip("subscription_config task not present")
        for t in tasks:
            paths = t.get("owned_paths") or []
            assert any("subscriptions.yaml" in str(p) for p in paths), (
                f"subscription_config task '{t.get('task_id')}' must own "
                f"config/subscriptions.yaml. Got: {paths}"
            )

    def test_subscription_config_task_initial_agent_is_config_middleware(self) -> None:
        tasks = _get_tasks_by_type(self.build_tasks, "subscription_config")
        if not tasks:
            pytest.skip("subscription_config task not present")
        for t in tasks:
            assert t.get("initial_agent") == "ConfigMiddlewareAgent", (
                f"subscription_config task initial_agent={t.get('initial_agent')!r}, "
                "expected 'ConfigMiddlewareAgent'"
            )

    # --- entitlement_dispatch module_contract ---

    def test_entitlement_dispatch_module_contract_task_present(self) -> None:
        tasks = _get_tasks_by_cap(self.build_tasks, "module_contract", "entitlement_dispatch")
        assert tasks, (
            "Missing module_contract task for entitlement_dispatch. "
            "AppPlanAgent must include this when assignment_store is declared without a managed assignment writer."
        )

    def test_entitlement_dispatch_module_contract_owns_module_yaml(self) -> None:
        tasks = _get_tasks_by_cap(self.build_tasks, "module_contract", "entitlement_dispatch")
        if not tasks:
            pytest.skip("entitlement_dispatch module_contract task not present")
        for t in tasks:
            paths = t.get("owned_paths") or []
            assert any("entitlement_dispatch/module.yaml" in str(p) for p in paths), (
                f"entitlement_dispatch module_contract task '{t.get('task_id')}' must own "
                f"modules/entitlement_dispatch/module.yaml. Got: {paths}"
            )

    def test_entitlement_dispatch_module_contract_initial_agent(self) -> None:
        tasks = _get_tasks_by_cap(self.build_tasks, "module_contract", "entitlement_dispatch")
        if not tasks:
            pytest.skip("entitlement_dispatch module_contract task not present")
        for t in tasks:
            assert t.get("initial_agent") == "ConfigMiddlewareAgent", (
                f"entitlement_dispatch module_contract initial_agent={t.get('initial_agent')!r}, "
                "expected 'ConfigMiddlewareAgent'"
            )

    def test_entitlement_dispatch_module_contract_message_mentions_subscription(self) -> None:
        tasks = _get_tasks_by_cap(self.build_tasks, "module_contract", "entitlement_dispatch")
        if not tasks:
            pytest.skip("entitlement_dispatch module_contract task not present")
        for t in tasks:
            msg = str(t.get("initial_message") or "").lower()
            initial_msg_preview = repr(str(t.get("initial_message") or "")[:200])
            assert "subscription" in msg or "entitlement" in msg or "assignment" in msg, (
                f"entitlement_dispatch module_contract initial_message must mention "
                f"subscription, entitlement, or assignment. Got: {initial_msg_preview}"
            )

    # --- entitlement_dispatch business_services ---

    def test_entitlement_dispatch_business_services_task_present(self) -> None:
        tasks = _get_tasks_by_cap(self.build_tasks, "business_services", "entitlement_dispatch")
        assert tasks, (
            "Missing business_services task for entitlement_dispatch. "
            "AppPlanAgent must plan the handler/service/repo backend files."
        )

    def test_entitlement_dispatch_business_services_owns_handler(self) -> None:
        tasks = _get_tasks_by_cap(self.build_tasks, "business_services", "entitlement_dispatch")
        if not tasks:
            pytest.skip("entitlement_dispatch business_services task not present")
        for t in tasks:
            paths = t.get("owned_paths") or []
            assert any("entitlement_dispatch/backend/handler.py" in str(p) for p in paths), (
                f"entitlement_dispatch business_services must own handler.py. Got: {paths}"
            )

    # --- No stray entitlement_dispatch paths in other tasks ---

    def test_no_stray_entitlement_dispatch_paths(self) -> None:
        designated_ids = {
            t.get("task_id")
            for t in self.build_tasks
            if isinstance(t, dict) and t.get("capability_pack_id") == "entitlement_dispatch"
        }
        violations = []
        for task in self.build_tasks:
            if not isinstance(task, dict) or task.get("task_id") in designated_ids:
                continue
            for path in (task.get("owned_paths") or []):
                if "modules/entitlement_dispatch/" in str(path):
                    violations.append(f"{task.get('task_id')}: {path}")
        assert not violations, (
            f"Stray entitlement_dispatch owned_paths in non-dispatch tasks: {violations}"
        )

    # --- page_bundle ---

    def test_page_bundle_task_present(self) -> None:
        tasks = _get_tasks_by_type(self.build_tasks, "page_bundle")
        assert tasks, "Missing page_bundle task"

    # --- Dependency graph integrity ---

    def test_dependency_graph_has_no_unknown_task_ids(self) -> None:
        task_ids = {t.get("task_id") for t in self.build_tasks if isinstance(t, dict)}
        for task in self.build_tasks:
            if not isinstance(task, dict):
                continue
            for dep in (task.get("depends_on") or []):
                assert dep in task_ids, (
                    f"Task '{task.get('task_id')}' declares unknown dependency '{dep}'"
                )

    def test_generation_order_is_present(self) -> None:
        assert self.plan.get("generation_order"), "Plan must have generation_order phases"
