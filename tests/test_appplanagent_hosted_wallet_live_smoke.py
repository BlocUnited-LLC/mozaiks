"""
AppPlanAgent hosted-wallet planning smoke test.

Two test modes:

  Live LLM (manual only):
    The TestAppPlanAgentLiveRun class is always skipped in CI.
    Run the smoke script instead:
        python scripts/smoke_appplan_hosted_wallet.py
        python scripts/smoke_appplan_hosted_wallet.py --save-fixture

  Fixture replay (CI-compatible):
    TestAppPlanAgentFixtureReplay loads and validates the fixture at
    tests/fixtures/appplan_hosted_wallet_output.json (if present).
    All tests skip when the fixture is absent.

To generate the fixture for CI replay:
    1. Set OPENAI_API_KEY in your environment (or .env).
    2. Run: python scripts/smoke_appplan_hosted_wallet.py --save-fixture
    3. Commit tests/fixtures/appplan_hosted_wallet_output.json.
    4. Future CI runs validate the saved plan without calling the LLM.

Validated invariants (fixture replay):
    1. Plan validates with app_build_plan.py without raising.
    2. wallet in capability_packs as hosted_pack / external_integration.
    3. No module_contract task for wallet.
    4. api_surface adapter task exists for services/integrations/wallet_client.py.
    5. module_contract task exists for an app-owned facade module (not wallet).
    6. page_bundle task exists.
    7. No owned_paths under modules/wallet/.
    8. Facade module initial_message delegates to the wallet adapter.
    9. Facade module depends on the adapter task.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_WORKSPACE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools"
_FIXTURE_PATH = _WORKSPACE / "tests" / "fixtures" / "appplan_hosted_wallet_output.json"

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
    # Fixture may be {"AppBuildPlan": {...}} or the plan directly
    if "AppBuildPlan" in raw:
        return raw["AppBuildPlan"]
    return raw


def _get_tasks_by_type(build_tasks: list[dict[str, Any]], task_type: str) -> list[dict[str, Any]]:
    return [t for t in build_tasks if isinstance(t, dict) and t.get("task_type") == task_type]


def _get_pack(capability_packs: list[dict[str, Any]], pack_id: str) -> dict[str, Any] | None:
    return next(
        (p for p in capability_packs if isinstance(p, dict) and p.get("capability_pack_id") == pack_id),
        None,
    )


def _find_adapter_task(build_tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            t for t in build_tasks
            if isinstance(t, dict)
            and t.get("task_type") == "api_surface"
            and any("wallet_client.py" in str(p) for p in (t.get("owned_paths") or []))
        ),
        None,
    )


def _find_facade_module_task(build_tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the module_contract task for the app-owned facade (not wallet itself)."""
    return next(
        (
            t for t in build_tasks
            if isinstance(t, dict)
            and t.get("task_type") == "module_contract"
            and t.get("capability_pack_id") != "wallet"
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Live LLM class (always skipped in CI — run via smoke script)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "Live LLM test — not run in CI. "
        "To run manually: python scripts/smoke_appplan_hosted_wallet.py\n"
        "To generate fixture for CI replay: "
        "python scripts/smoke_appplan_hosted_wallet.py --save-fixture"
    )
)
class TestAppPlanAgentLiveRun:
    """
    Documents the manual smoke script entry point.

    Run instructions:
        cd mozaiks
        python scripts/smoke_appplan_hosted_wallet.py
        python scripts/smoke_appplan_hosted_wallet.py --model gpt-4o --save-fixture

    Expected output:
        - Capability packs includes wallet as hosted_pack / external_integration.
        - Build tasks: api_surface wallet adapter, module_contract facade, page_bundle.
        - No module_contract task for wallet.
        - No owned_paths under modules/wallet/.
        - Validation: PASSED.
    """

    def test_live_run_placeholder(self) -> None:
        """This test is always skipped. See the class docstring."""
        assert False, "This test must never run in CI — use the smoke script."


# ---------------------------------------------------------------------------
# Fixture replay tests (CI-compatible, skip if fixture absent)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _FIXTURE_PRESENT,
    reason=(
        "Fixture not present. Generate with: "
        "python scripts/smoke_appplan_hosted_wallet.py --save-fixture"
    ),
)
class TestAppPlanAgentFixtureReplay:
    """
    Validates the saved AppBuildPlan fixture without calling the LLM.

    All tests skip when tests/fixtures/appplan_hosted_wallet_output.json is absent.
    To generate the fixture: python scripts/smoke_appplan_hosted_wallet.py --save-fixture
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
            f"tests.appplan_wallet_fixture_validation.{id(self)}",
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

    # --- Hosted wallet pack invariants ---

    def test_wallet_pack_present_in_capability_packs(self) -> None:
        wallet = _get_pack(self.capability_packs, "wallet")
        assert wallet is not None, (
            "wallet capability_pack entry missing from plan. "
            "Expected: capability_pack_id='wallet' with capability_source='hosted_pack'."
        )

    def test_wallet_pack_is_hosted_pack(self) -> None:
        wallet = _get_pack(self.capability_packs, "wallet")
        if wallet is None:
            pytest.skip("wallet pack not present — see test_wallet_pack_present_in_capability_packs")
        assert wallet.get("capability_source") == "hosted_pack", (
            f"wallet.capability_source={wallet.get('capability_source')!r}, expected 'hosted_pack'"
        )

    def test_wallet_pack_surface_kind_is_external_integration(self) -> None:
        wallet = _get_pack(self.capability_packs, "wallet")
        if wallet is None:
            pytest.skip("wallet pack not present")
        assert wallet.get("surface_kind") == "external_integration", (
            f"wallet.surface_kind={wallet.get('surface_kind')!r}, expected 'external_integration'"
        )

    def test_wallet_pack_implementation_mode_is_external_integration(self) -> None:
        wallet = _get_pack(self.capability_packs, "wallet")
        if wallet is None:
            pytest.skip("wallet pack not present")
        assert wallet.get("implementation_mode") == "external_integration", (
            f"wallet.implementation_mode={wallet.get('implementation_mode')!r}, "
            "expected 'external_integration'"
        )

    # --- No module_contract for hosted wallet ---

    def test_no_module_contract_task_for_wallet(self) -> None:
        bad = [
            t for t in self.build_tasks
            if isinstance(t, dict)
            and t.get("task_type") == "module_contract"
            and t.get("capability_pack_id") == "wallet"
        ]
        assert not bad, (
            f"Drift: found module_contract task(s) for wallet (hosted_pack): "
            f"{[t.get('task_id') for t in bad]}"
        )

    def test_no_modules_wallet_owned_paths(self) -> None:
        violations = []
        for task in self.build_tasks:
            if not isinstance(task, dict):
                continue
            for path in (task.get("owned_paths") or []):
                if str(path).startswith("modules/wallet/"):
                    violations.append(f"{task.get('task_id')}: {path}")
        assert not violations, (
            f"Drift: owned_paths under modules/wallet/: {violations}"
        )

    def test_no_capability_packs_in_owned_paths(self) -> None:
        violations = []
        for task in self.build_tasks:
            if not isinstance(task, dict):
                continue
            for path in (task.get("owned_paths") or []):
                if "capability_packs" in str(path):
                    violations.append(f"{task.get('task_id')}: {path}")
        assert not violations, (
            f"Drift: owned_paths contain capability_packs: {violations}"
        )

    # --- Adapter task ---

    def test_adapter_task_exists_for_wallet_client(self) -> None:
        adapter = _find_adapter_task(self.build_tasks)
        assert adapter is not None, (
            "Missing api_surface task with services/integrations/wallet_client.py. "
            "AppPlanAgent must generate a thin hosted-wallet adapter task."
        )

    def test_adapter_task_type_is_api_surface(self) -> None:
        adapter = _find_adapter_task(self.build_tasks)
        if adapter is None:
            pytest.skip("adapter task not present")
        assert adapter.get("task_type") == "api_surface"

    def test_adapter_task_initial_agent_is_controller(self) -> None:
        adapter = _find_adapter_task(self.build_tasks)
        if adapter is None:
            pytest.skip("adapter task not present")
        assert adapter.get("initial_agent") == "ControllerAgent", (
            f"adapter.initial_agent={adapter.get('initial_agent')!r}, expected 'ControllerAgent'"
        )

    def test_adapter_task_owned_path_is_backend_integrations(self) -> None:
        adapter = _find_adapter_task(self.build_tasks)
        if adapter is None:
            pytest.skip("adapter task not present")
        paths = adapter.get("owned_paths") or []
        backend_paths = [p for p in paths if "services/integrations/" in str(p)]
        assert backend_paths, (
            f"adapter owned_paths has no services/integrations/ entry: {paths}"
        )

    # --- Facade module ---

    def test_facade_module_task_exists(self) -> None:
        facade = _find_facade_module_task(self.build_tasks)
        assert facade is not None, (
            "Missing module_contract task for app-owned facade module. "
            "AppPlanAgent must generate a facade module (e.g. wallet_dashboard) "
            "that wraps the hosted wallet adapter."
        )

    def test_facade_module_task_type_is_module_contract(self) -> None:
        facade = _find_facade_module_task(self.build_tasks)
        if facade is None:
            pytest.skip("facade module task not present")
        assert facade.get("task_type") == "module_contract"

    def test_facade_module_is_not_wallet(self) -> None:
        facade = _find_facade_module_task(self.build_tasks)
        if facade is None:
            pytest.skip("facade module task not present")
        assert facade.get("capability_pack_id") != "wallet", (
            "Facade module must have a different id from the hosted wallet pack"
        )

    def test_facade_module_owned_paths_are_not_under_modules_wallet(self) -> None:
        facade = _find_facade_module_task(self.build_tasks)
        if facade is None:
            pytest.skip("facade module task not present")
        for path in (facade.get("owned_paths") or []):
            assert not str(path).startswith("modules/wallet/"), (
                f"Facade module must not own paths under modules/wallet/: {path}"
            )

    def test_facade_module_owned_paths_include_module_yaml(self) -> None:
        facade = _find_facade_module_task(self.build_tasks)
        if facade is None:
            pytest.skip("facade module task not present")
        paths = facade.get("owned_paths") or []
        assert any("module.yaml" in str(p) for p in paths), (
            f"Facade module owned_paths must include module.yaml: {paths}"
        )

    def test_facade_module_initial_message_references_wallet_adapter(self) -> None:
        """Facade service must reference the hosted wallet adapter (wallet_client)."""
        facade = _find_facade_module_task(self.build_tasks)
        if facade is None:
            pytest.skip("facade module task not present")
        initial_message = str(facade.get("initial_message") or "").lower()
        description = str(facade.get("description") or "").lower()
        combined = initial_message + " " + description
        # Should reference the wallet adapter client or hosted wallet capability
        assert (
            "wallet_client" in combined
            or "wallet" in combined
        ), (
            f"Facade module initial_message/description should reference wallet adapter. "
            f"Got: {facade.get('initial_message')!r}"
        )

    def test_facade_module_depends_on_adapter_task(self) -> None:
        """Facade module should declare a dependency on the wallet adapter task."""
        facade = _find_facade_module_task(self.build_tasks)
        adapter = _find_adapter_task(self.build_tasks)
        if facade is None or adapter is None:
            pytest.skip("facade or adapter task not present")
        depends_on = facade.get("depends_on") or []
        adapter_id = adapter.get("task_id")
        assert adapter_id in depends_on, (
            f"Facade module must declare dependency on adapter task '{adapter_id}'. "
            f"Got depends_on={depends_on!r}"
        )

    # --- Page bundle ---

    def test_page_bundle_task_exists(self) -> None:
        page_tasks = _get_tasks_by_type(self.build_tasks, "page_bundle")
        assert page_tasks, "Missing page_bundle task"

    def test_page_bundle_initial_agent_is_appschemaagent(self) -> None:
        page_tasks = _get_tasks_by_type(self.build_tasks, "page_bundle")
        if not page_tasks:
            pytest.skip("page_bundle task not present")
        for t in page_tasks:
            assert t.get("initial_agent") == "AppSchemaAgent", (
                f"page_bundle task initial_agent={t.get('initial_agent')!r}, expected 'AppSchemaAgent'"
            )

    def test_page_bundle_does_not_bind_to_wallet_module_directly(self) -> None:
        """Page task initial_message must not instruct direct binding to /api/modules/wallet/."""
        page_tasks = _get_tasks_by_type(self.build_tasks, "page_bundle")
        if not page_tasks:
            pytest.skip("page_bundle task not present")
        for t in page_tasks:
            msg = str(t.get("initial_message") or "")
            # Check that if wallet API paths are mentioned, they are for the facade, not the hosted pack
            import re
            direct_hosted_refs = re.findall(r"/api/modules/wallet/", msg)
            assert not direct_hosted_refs, (
                f"Page bundle initial_message references /api/modules/wallet/ directly. "
                "Pages must bind through the facade module, not the hosted pack id. "
                f"Got: {msg[:200]!r}"
            )

    # --- Dependency ordering ---

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

