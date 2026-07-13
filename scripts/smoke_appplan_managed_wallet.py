"""
Manual smoke script: AppPlanAgent managed-wallet planning.

Usage:
    cd mozaiks
    python scripts/smoke_appplan_managed_wallet.py
    python scripts/smoke_appplan_managed_wallet.py --save-fixture
    python scripts/smoke_appplan_managed_wallet.py --model gpt-4o

What it does:
    1. Builds AppPlanAgent system prompt from agents.yaml.
    2. Injects [FILE CONTRACTS CONTEXT] via hook_file_contract_context.
    3. Injects [MANAGED CAPABILITIES CONTEXT] via hook_managed_capabilities_context.
    4. Calls OpenAI with a creator-dashboard user request.
    5. Parses the AppBuildPlan JSON output.
    6. Validates with app_build_plan.py.
    7. Checks plan shape: managed_capability entry, adapter task, facade module, page binding.
    8. Reports results. Exits 0 on success, 1 on failure.

--save-fixture  Write output to tests/fixtures/appplan_managed_wallet_output.json
                for use by the pytest fixture-replay test.

Environment:
    OPENAI_API_KEY     Required.
    APPPLAN_SMOKE_MODEL  Override model (default: gpt-5-nano).
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

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_YAML = _REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
_TOOLS_DIR = _REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools"
_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures"

# ---------------------------------------------------------------------------
# Managed wallet context
# ---------------------------------------------------------------------------

_RUNTIME_CAPABILITIES = [
    "module_execution",
    "event_dispatch",
    "admin_shell",
    "page_primitives",
    "websocket_transport",
    "notifications",
]

_AVAILABLE_MANAGED_CAPABILITIES = [
    {
        "id": "wallet",
        "label": "Wallet",
        "capability_source": "managed_capability",
        "status": "active",
        "capabilities": [
            {"capability_id": "wallet.view"},
            {"capability_id": "wallet.payout"},
            {"capability_id": "wallet.connect_provider"},
        ],
    }
]

_USER_REQUEST = (
    "Build a creator dashboard app where creators can view their wallet balance, "
    "connect MozaiksPay, request payouts, and see recent payout activity. "
    "Use the managed wallet capability if available."
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
    """Apply file_contract and managed_capabilities hooks to the fake agent."""
    # File contracts context hook
    fc_hook = _load_module(
        _TOOLS_DIR / "hook_file_contract_context.py",
        f"smoke_hook_file_contract.{id(agent)}",
    )
    fc_hook.inject_cookie_cutter_contracts_context(agent, [])

    # Managed capabilities context hook
    hc_hook = _load_module(
        _TOOLS_DIR / "hook_managed_capabilities_context.py",
        f"smoke_hook_managed_capabilities.{id(agent)}",
    )
    hc_hook.inject_managed_capabilities_context(agent, [])


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from a string that may be wrapped in markdown code blocks."""
    stripped = text.strip()

    # Direct JSON object
    if stripped.startswith("{"):
        return json.loads(stripped)

    # Markdown code blocks
    for pattern in (r"```json\s*([\s\S]+?)\s*```", r"```\s*([\s\S]+?)\s*```"):
        m = re.search(pattern, stripped)
        if m:
            candidate = m.group(1).strip()
            if candidate.startswith("{"):
                return json.loads(candidate)

    # Find outermost { ... }
    start = stripped.find("{")
    end = stripped.rfind("}")
    if 0 <= start < end:
        return json.loads(stripped[start : end + 1])

    raise ValueError(f"Could not extract JSON from LLM response:\n{text[:600]}")


def _call_openai(system_message: str, user_message: str, model: str) -> str:
    """Call OpenAI chat completions API."""
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
        "timeout": 120,
    }
    if not model.startswith("gpt-5"):
        request["temperature"] = 0.1
    response = client.chat.completions.create(**request)
    return response.choices[0].message.content


def _load_validation_tool():
    return _load_module(
        _TOOLS_DIR / "app_build_plan.py",
        f"smoke_app_build_plan.{id(object())}",
    )


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
    """Return list of drift/shape violations. Empty means clean."""
    violations: list[str] = []
    capability_packs = plan.get("capability_packs") or []
    build_tasks = plan.get("build_tasks") or []

    # 1. Must have wallet as managed_capability
    wallet_pack = next(
        (p for p in capability_packs if isinstance(p, dict) and p.get("capability_pack_id") == "wallet"),
        None,
    )
    if wallet_pack is None:
        violations.append("MISSING: wallet capability_pack entry")
    else:
        if wallet_pack.get("capability_source") != "managed_capability":
            violations.append(
                f"DRIFT: wallet capability_source={wallet_pack.get('capability_source')!r}, expected managed_capability"
            )
        if wallet_pack.get("implementation_mode") not in ("external_integration", None):
            if wallet_pack.get("implementation_mode") != "external_integration":
                violations.append(
                    f"DRIFT: wallet implementation_mode={wallet_pack.get('implementation_mode')!r}, expected external_integration"
                )

    # 2. Must NOT have module_contract task for wallet
    for t in build_tasks:
        if isinstance(t, dict) and t.get("task_type") == "module_contract" and t.get("capability_pack_id") == "wallet":
            violations.append(f"DRIFT: module_contract task exists for wallet (managed_capability): {t.get('task_id')}")

    # 3. Must have api_surface adapter task targeting wallet_client.py
    adapter_task: dict[str, Any] | None = next(
        (
            t for t in build_tasks
            if isinstance(t, dict)
            and t.get("task_type") == "api_surface"
            and any("wallet_client.py" in str(p) for p in (t.get("owned_paths") or []))
        ),
        None,
    )
    if adapter_task is None:
        violations.append("MISSING: api_surface adapter task with backend/integrations/wallet_client.py")

    # 4. Must have a module_contract task for an app-owned facade module (not wallet)
    facade_tasks = [
        t for t in build_tasks
        if isinstance(t, dict)
        and t.get("task_type") == "module_contract"
        and t.get("capability_pack_id") != "wallet"
    ]
    if not facade_tasks:
        violations.append(
            "MISSING: module_contract task for app-owned facade module "
            "(e.g. wallet_dashboard, creator_earnings - not wallet itself)"
        )

    # 5. Must have page_bundle task
    page_tasks = [t for t in build_tasks if isinstance(t, dict) and t.get("task_type") == "page_bundle"]
    if not page_tasks:
        violations.append("MISSING: page_bundle task")

    # 6. No owned_paths under modules/wallet/
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        for path in (task.get("owned_paths") or []):
            if str(path).startswith("modules/wallet/"):
                violations.append(f"DRIFT: owned_path under modules/wallet/: {path} (task={task.get('task_id')})")

    # 7. No capability_packs in any owned_path
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        for path in (task.get("owned_paths") or []):
            if "capability_packs" in str(path):
                violations.append(f"DRIFT: owned_path contains capability_packs: {path}")

    return violations


# ---------------------------------------------------------------------------
# Main smoke run
# ---------------------------------------------------------------------------


def run(*, save_fixture: bool = False, model: str = "gpt-5-nano") -> int:
    print("=" * 70)
    print("AppPlanAgent Managed-Wallet Planning Smoke Test")
    print("=" * 70)

    # Step 1: Build system prompt
    print("\n[1/6] Building AppPlanAgent system prompt from agents.yaml...")
    base_prompt = _build_agent_system_prompt("AppPlanAgent")
    print(f"      Base prompt: {len(base_prompt):,} chars, {base_prompt.count(chr(10)) + 1} lines")

    # Step 2: Apply hooks
    print("[2/6] Applying hooks (file_contracts + managed_capabilities)...")
    agent = _FakeAgent(
        name="AppPlanAgent",
        context_variables={
            "runtime_capabilities": _RUNTIME_CAPABILITIES,
            "capability_packs": _AVAILABLE_MANAGED_CAPABILITIES,
            "available_managed_capabilities": _AVAILABLE_MANAGED_CAPABILITIES,
            "pack_sources": [],
        },
    )
    agent.system_message = base_prompt
    _apply_hooks(agent)
    system_message = agent.system_message
    print(f"      Final prompt: {len(system_message):,} chars")

    if "[FILE CONTRACTS CONTEXT]" not in system_message:
        print("      ERROR: [FILE CONTRACTS CONTEXT] not injected")
        return 1
    if "[MANAGED CAPABILITIES CONTEXT]" not in system_message:
        print("      ERROR: [MANAGED CAPABILITIES CONTEXT] not injected")
        return 1
    print("      [FILE CONTRACTS CONTEXT] injected")
    print("      [MANAGED CAPABILITIES CONTEXT] injected")

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
        validation_mod = _load_validation_tool()
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
                f" | agent={task.get('initial_agent')} | paths={paths}"
            )

    violations = _check_plan_shape(plan)
    if violations:
        print(f"\n  VIOLATIONS ({len(violations)}):")
        for v in violations:
            print(f"    FAIL: {v}")
        return 1

    print("\n  All shape checks passed")

    # Optionally save fixture
    if save_fixture:
        _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        fixture_path = _FIXTURES_DIR / "appplan_managed_wallet_output.json"
        fixture_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
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
        description="AppPlanAgent managed-wallet planning smoke test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--save-fixture",
        action="store_true",
        help="Save plan output to tests/fixtures/appplan_managed_wallet_output.json for CI replay",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("APPPLAN_SMOKE_MODEL", "gpt-5-nano"),
        help="OpenAI model to use (default: gpt-5-nano or APPPLAN_SMOKE_MODEL env var)",
    )
    args = parser.parse_args()
    return run(save_fixture=args.save_fixture, model=args.model)


if __name__ == "__main__":
    sys.exit(main())
