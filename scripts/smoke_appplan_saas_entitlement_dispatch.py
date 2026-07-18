"""
Manual smoke script: AppPlanAgent self-hosted SaaS entitlement_dispatch planning.

Usage:
    cd mozaiks
    python scripts/smoke_appplan_saas_entitlement_dispatch.py
    python scripts/smoke_appplan_saas_entitlement_dispatch.py --save-fixture
    python scripts/smoke_appplan_saas_entitlement_dispatch.py --model gpt-4o

What it does:
    1. Builds AppPlanAgent system prompt from agents.yaml.
    2. Injects [FILE CONTRACTS CONTEXT] via hook_file_contract_context.
    3. Injects [SUBSCRIPTION CONTRACT CONTEXT] via subscription_contract_context.
    4. Calls OpenAI with an analytics SaaS build request.
    5. Parses the AppBuildPlan JSON output.
    6. Validates with app_build_plan.py.
    7. Checks plan shape:
       - subscription_config task present (config/subscriptions.yaml)
       - entitlement_dispatch module_contract task present
       - entitlement_dispatch business_services task present
       - No mozaikspay managed_capability pack
       - reports module has entitlement_gate on export_report
    8. Reports results. Exits 0 on success, 1 on failure.

--save-fixture  Write output to tests/fixtures/appplan_saas_entitlement_dispatch_output.json
                for use by the pytest fixture-replay test.

Environment:
    OPENAI_API_KEY     Required.
    APPPLAN_SMOKE_MODEL  Override model (default: gpt-4o).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Ensure the repo root is on sys.path so factory_app and mozaiksai are importable
# when the script is run directly (outside a pip-installed environment).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml

_AGENTS_YAML = _REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
_TOOLS_DIR = _REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools"
_SHARED_DIR = _REPO_ROOT / "factory_app" / "workflows" / "_shared"
_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures"

# ---------------------------------------------------------------------------
# Subscription contract fixture — simulates SubscriptionContractDesigner output
# ---------------------------------------------------------------------------

_SUBSCRIPTION_CONTRACT: dict[str, Any] = {
    "contract_required": True,
    "rationale": "Analytics SaaS with free/pro tiers — pro users pay for report export capability",
    "app_name": "Analytics SaaS",
    "subscription_config_file": {
        "schema_version": "mozaiks.subscriptions.v1",
        "label": "Analytics SaaS Plans",
        "default_plan_id": "free",
        "assignment_store": {
            "data_alias": "billing.subscriptions",
            "user_id_field": "user_id",
            "active_statuses": ["active"],
        },
        "plans": [
            {
                "plan_id": "free",
                "label": "Free",
                "capabilities": ["reports.view"],
            },
            {
                "plan_id": "pro",
                "label": "Pro",
                "capabilities": ["reports.view", "reports.export"],
            },
        ],
    },
    "module_contract_updates": [
        {
            "module_id": "reports",
            "action_entitlement_updates": [
                {
                    "action_id": "export_report",
                    "entitlement_gate": "reports.export",
                }
            ],
        }
    ],
}

_RUNTIME_CAPABILITIES = [
    "module_execution",
    "event_dispatch",
    "admin_shell",
    "page_primitives",
    "websocket_transport",
    "notifications",
]

_USER_REQUEST = (
    "Build an analytics SaaS app where users can view reports on the free plan "
    "and export reports as CSV on the pro plan. Wire entitlement gating so the "
    "export action requires the pro subscription. Use the provided subscription "
    "contract. No third-party payment provider is needed — use self-hosted "
    "subscription assignment."
)


# ---------------------------------------------------------------------------
# Minimal fake agent for hook application
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Minimal agent stub for applying hooks without the full AG2 stack."""

    def __init__(self, name: str, context_variables: dict[str, Any]) -> None:
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables

    def update_system_message(self, message: str) -> None:
        self.system_message = message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_agent_system_prompt(agent_name: str) -> str:
    """Assemble agent system prompt from agents.yaml prompt_sections."""
    with open(_AGENTS_YAML, encoding="utf-8") as f:
        agents = yaml.safe_load(f)

    agents = agents.get("agents") if isinstance(agents, dict) else agents
    if not isinstance(agents, list):
        raise ValueError(f"agents.yaml must contain an agents list: {type(agents)}")

    for agent in agents:
        if not isinstance(agent, dict) or agent.get("name") != agent_name:
            continue
        sections = agent.get("prompt_sections") or []
        parts: list[str] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "").strip()
            content = str(section.get("content") or "").strip()
            if heading and content:
                parts.append(f"{heading}\n{content}")
            elif content:
                parts.append(content)
        return "\n\n".join(parts)

    raise ValueError(f"Agent '{agent_name}' not found in {_AGENTS_YAML}")


def _apply_hooks(agent: _FakeAgent) -> None:
    """Apply file_contract and subscription_contract hooks to the fake agent."""
    fc_hook = _load_module(
        _TOOLS_DIR / "hook_file_contract_context.py",
        f"smoke_hook_file_contract.{id(agent)}",
    )
    fc_hook.inject_cookie_cutter_contracts_context(agent, [])

    sub_hook = _load_module(
        _SHARED_DIR / "subscription_contract_context.py",
        f"smoke_hook_subscription_contract.{id(agent)}",
    )
    sub_hook.inject_subscription_contract_context(agent, [])


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from a string that may be wrapped in markdown code blocks."""
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    for pattern in (r"```json\s*([\s\S]+?)\s*```", r"```\s*([\s\S]+?)\s*```"):
        m = re.search(pattern, stripped)
        if m:
            candidate = m.group(1).strip()
            if candidate.startswith("{"):
                return json.loads(candidate)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if 0 <= start < end:
        return json.loads(stripped[start : end + 1])
    raise ValueError(f"Could not extract JSON from LLM response:\n{text[:600]}")


def _call_openai(system_message: str, user_message: str, model: str) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise ImportError("openai package required: pip install openai") from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable not set")

    client = OpenAI(api_key=api_key)
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "timeout": 180,
    }
    if not model.startswith("gpt-5"):
        request["temperature"] = 0.1
    response = client.chat.completions.create(**request)
    return response.choices[0].message.content


class _Context:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


def _validate_plan(plan: dict[str, Any], validation_mod) -> tuple[dict[str, Any], str]:
    ctx = _Context()
    result = validation_mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
    return ctx.data, str(result)


def _check_plan_shape(plan: dict[str, Any]) -> list[str]:
    """Return list of shape violations. Empty means clean."""
    violations: list[str] = []
    capability_packs = plan.get("capability_packs") or []
    build_tasks = plan.get("build_tasks") or []

    # 1. No mozaikspay managed pack — this is self-hosted
    mozaikspay_pack = next(
        (p for p in capability_packs if isinstance(p, dict) and p.get("capability_pack_id") == "mozaikspay"),
        None,
    )
    if mozaikspay_pack and mozaikspay_pack.get("capability_source") == "managed_capability":
        violations.append(
            "DRIFT: mozaikspay managed_capability pack present — this smoke test "
            "covers self-hosted SaaS, which should not use the mozaikspay pack."
        )

    # 2. subscription_config task must be present
    sub_config_tasks = [
        t for t in build_tasks
        if isinstance(t, dict) and t.get("task_type") == "subscription_config"
    ]
    if not sub_config_tasks:
        violations.append("MISSING: subscription_config task (config/subscriptions.yaml)")
    else:
        for t in sub_config_tasks:
            paths = t.get("owned_paths") or []
            if not any("subscriptions.yaml" in str(p) for p in paths):
                violations.append(
                    f"DRIFT: subscription_config task '{t.get('task_id')}' "
                    f"owned_paths do not include config/subscriptions.yaml: {paths}"
                )

    # 3. entitlement_dispatch module_contract task must be present
    dispatch_contract_tasks = [
        t for t in build_tasks
        if isinstance(t, dict)
        and t.get("task_type") == "module_contract"
        and t.get("capability_pack_id") == "entitlement_dispatch"
    ]
    if not dispatch_contract_tasks:
        violations.append(
            "MISSING: module_contract task with capability_pack_id='entitlement_dispatch'. "
            "AppPlanAgent must plan this when assignment_store is declared without mozaikspay."
        )
    else:
        for t in dispatch_contract_tasks:
            paths = t.get("owned_paths") or []
            if not any("entitlement_dispatch/module.yaml" in str(p) for p in paths):
                violations.append(
                    f"DRIFT: entitlement_dispatch module_contract task '{t.get('task_id')}' "
                    f"owned_paths do not include modules/entitlement_dispatch/module.yaml: {paths}"
                )

    # 4. entitlement_dispatch business_services task must be present
    dispatch_service_tasks = [
        t for t in build_tasks
        if isinstance(t, dict)
        and t.get("task_type") == "business_services"
        and t.get("capability_pack_id") == "entitlement_dispatch"
    ]
    if not dispatch_service_tasks:
        violations.append(
            "MISSING: business_services task with capability_pack_id='entitlement_dispatch'."
        )

    # 5. page_bundle task must be present
    page_tasks = [t for t in build_tasks if isinstance(t, dict) and t.get("task_type") == "page_bundle"]
    if not page_tasks:
        violations.append("MISSING: page_bundle task")

    # 6. No owned_paths under modules/entitlement_dispatch/ outside of the planned tasks
    dispatch_task_ids = {t.get("task_id") for t in dispatch_contract_tasks + dispatch_service_tasks}
    for task in build_tasks:
        if not isinstance(task, dict) or task.get("task_id") in dispatch_task_ids:
            continue
        for path in (task.get("owned_paths") or []):
            if "modules/entitlement_dispatch/" in str(path):
                violations.append(
                    f"DRIFT: task '{task.get('task_id')}' owns entitlement_dispatch path "
                    f"outside the designated dispatch tasks: {path}"
                )

    return violations


# ---------------------------------------------------------------------------
# Main smoke run
# ---------------------------------------------------------------------------


def run(*, save_fixture: bool = False, model: str = "gpt-4o") -> int:
    print("=" * 70)
    print("AppPlanAgent Self-Hosted SaaS Entitlement Dispatch Smoke Test")
    print("=" * 70)

    # Step 1: Build system prompt
    print("\n[1/6] Building AppPlanAgent system prompt from agents.yaml...")
    base_prompt = _build_agent_system_prompt("AppPlanAgent")
    print(f"      Base prompt: {len(base_prompt):,} chars, {base_prompt.count(chr(10)) + 1} lines")

    # Step 2: Apply hooks
    print("[2/6] Applying hooks (file_contracts + subscription_contract)...")
    agent = _FakeAgent(
        name="AppPlanAgent",
        context_variables={
            "subscription_contract": _SUBSCRIPTION_CONTRACT,
            "runtime_capabilities": _RUNTIME_CAPABILITIES,
            "capability_packs": [],
            "available_managed_capabilities": [],
        },
    )
    agent.system_message = base_prompt
    _apply_hooks(agent)
    system_message = agent.system_message
    print(f"      Final prompt: {len(system_message):,} chars")

    if "[FILE CONTRACTS CONTEXT]" not in system_message:
        print("      ERROR: [FILE CONTRACTS CONTEXT] not injected")
        return 1
    if "[SUBSCRIPTION CONTRACT CONTEXT]" not in system_message:
        print("      ERROR: [SUBSCRIPTION CONTRACT CONTEXT] not injected")
        return 1
    if "assignment_store" not in system_message:
        print("      ERROR: assignment_store not present in injected subscription context")
        return 1
    if "entitlement_dispatch" not in system_message:
        print("      ERROR: entitlement_dispatch not mentioned in injected system message")
        return 1
    print("      [FILE CONTRACTS CONTEXT] injected")
    print("      [SUBSCRIPTION CONTRACT CONTEXT] injected (with assignment_store + entitlement_dispatch rule)")

    # Step 3: Call LLM
    print(f"\n[3/6] Calling OpenAI ({model})...")
    print(f"      User request: {_USER_REQUEST[:80]}...")
    try:
        response_text = _call_openai(system_message, _USER_REQUEST, model)
    except Exception as exc:
        print(f"      ERROR: LLM call failed - {exc}")
        return 1
    print(f"      Response: {len(response_text):,} chars")

    # Step 4: Parse JSON
    print("\n[4/6] Parsing AppBuildPlan JSON...")
    try:
        raw = _extract_json(response_text)
    except Exception as exc:
        print(f"      ERROR: JSON parse failed - {exc}")
        print(f"      Raw response (first 800 chars):\n{response_text[:800]}")
        return 1

    plan = raw.get("AppBuildPlan") or raw
    capability_packs = plan.get("capability_packs") or []
    build_tasks = plan.get("build_tasks") or []
    print(f"      Capability packs: {len(capability_packs)}")
    print(f"      Build tasks: {len(build_tasks)}")
    print(f"      Pages: {len(plan.get('pages') or [])}")
    print(f"      Generation order phases: {len(plan.get('generation_order') or [])}")

    # Step 5: Validate with app_build_plan.py
    print("\n[5/6] Validating plan with app_build_plan.py...")
    try:
        validation_mod = _load_module(
            _TOOLS_DIR / "app_build_plan.py",
            f"smoke_app_build_plan.{id(object())}",
        )
        ctx_data, result_msg = _validate_plan(plan, validation_mod)
        if ctx_data.get("app_plan_ready") is not True:
            print(f"      ERROR: app_plan_ready={ctx_data.get('app_plan_ready')!r}, expected True")
            return 1
        plan = ctx_data.get("app_build_plan") or plan
        capability_packs = plan.get("capability_packs") or []
        build_tasks = plan.get("build_tasks") or []
        print(f"      {result_msg}")
        print("      Validation: PASSED")
    except Exception as exc:
        print(f"      Validation: FAILED - {exc}")
        return 1

    # Step 6: Shape checks
    print("\n[6/6] Checking plan shape...")
    print("  Capability packs:")
    for pack in capability_packs:
        if isinstance(pack, dict):
            print(
                f"    [{pack.get('capability_source', 'generated_module')}] "
                f"{pack.get('capability_pack_id')} ({pack.get('surface_kind', '?')})"
            )

    print("  Build tasks:")
    for task in build_tasks:
        if isinstance(task, dict):
            paths = task.get("owned_paths") or []
            print(
                f"    [{task.get('task_type')}] {task.get('task_id')}"
                f" | agent={task.get('initial_agent')} | cap={task.get('capability_pack_id')}"
                f" | paths={paths}"
            )

    violations = _check_plan_shape(plan)
    if violations:
        print(f"\n  VIOLATIONS ({len(violations)}):")
        for v in violations:
            print(f"    FAIL: {v}")
        return 1

    print("\n  All shape checks passed")

    if save_fixture:
        _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        fixture_path = _FIXTURES_DIR / "appplan_saas_entitlement_dispatch_output.json"
        # Save the post-validation plan so capability_pack_id values are normalized.
        fixture_payload = {"AppBuildPlan": plan}
        fixture_path.write_text(json.dumps(fixture_payload, indent=2), encoding="utf-8")
        print(f"\n  Fixture saved: {fixture_path}")
        print("  Commit this file for CI fixture-replay tests.")

    print("\n" + "=" * 70)
    print("SMOKE TEST PASSED")
    print("=" * 70)
    return 0


def main() -> int:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_REPO_ROOT / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="AppPlanAgent self-hosted SaaS entitlement_dispatch smoke test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--save-fixture",
        action="store_true",
        help="Save plan output to tests/fixtures/appplan_saas_entitlement_dispatch_output.json",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("APPPLAN_SMOKE_MODEL", "gpt-4o"),
        help="OpenAI model to use (default: gpt-4o or APPPLAN_SMOKE_MODEL env var)",
    )
    args = parser.parse_args()
    return run(save_fixture=args.save_fixture, model=args.model)


if __name__ == "__main__":
    sys.exit(main())
