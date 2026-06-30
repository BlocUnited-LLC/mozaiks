"""Live smoke: AppPlanAgent + selected MozaiksPay build context.

This is a single-agent live smoke, not a full workflow run. It verifies that:
- the real AppPlanAgent prompt can run against a real LLM,
- selected build_context/mozaikspay projects capability_packs,
- app_build_plan deterministically expands the required MozaiksPay adapter,
  facade modules, and pages even if the LLM omits some details.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from mozaiksai.core.session.build_context import merge_build_context

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_YAML = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
TOOLS_DIR = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools"
MOZAIKSPAY_CONTEXT_ROOT = REPO_ROOT / "factory_app" / "build_context"


def _load_dotenv() -> None:
    for path in (REPO_ROOT / ".env", REPO_ROOT.parent / "mozaiks-app" / ".env"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_agent_system_prompt(agent_name: str) -> str:
    raw = yaml.safe_load(AGENTS_YAML.read_text(encoding="utf-8")) or {}
    agents = raw.get("agents") if isinstance(raw, dict) else raw
    if not isinstance(agents, list):
        raise ValueError("agents.yaml must contain an agents list")
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("name") != agent_name:
            continue
        parts: list[str] = []
        for section in agent.get("prompt_sections") or []:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "").strip()
            content = str(section.get("content") or "").strip()
            if heading and content:
                parts.append(f"{heading}\n{content}")
            elif content:
                parts.append(content)
        return "\n\n".join(parts)
    raise ValueError(f"Agent not found: {agent_name}")


class _FakeAgent:
    def __init__(self, context_variables: dict[str, Any]) -> None:
        self.name = "AppPlanAgent"
        self.system_message = _build_agent_system_prompt("AppPlanAgent")
        self.context_variables = context_variables

    def update_system_message(self, message: str) -> None:
        self.system_message = message


class _Context:
    def __init__(self, **data: Any) -> None:
        self.data = dict(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


def _apply_prompt_hooks(agent: _FakeAgent) -> None:
    file_contracts = _load_module(
        TOOLS_DIR / "hook_file_contract_context.py",
        f"smoke_mozaikspay_file_contracts.{id(agent)}",
    )
    managed = _load_module(
        TOOLS_DIR / "hook_managed_capabilities_context.py",
        f"smoke_mozaikspay_managed.{id(agent)}",
    )
    file_contracts.inject_cookie_cutter_contracts_context(agent, [])
    managed.inject_managed_capabilities_context(agent, [])


def _call_openai(system_message: str, user_message: str, model: str) -> str:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
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
    return response.choices[0].message.content or ""


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    for pattern in (r"```json\s*([\s\S]+?)\s*```", r"```\s*([\s\S]+?)\s*```"):
        match = re.search(pattern, stripped)
        if match:
            return json.loads(match.group(1).strip())
    start = stripped.find("{")
    end = stripped.rfind("}")
    if 0 <= start < end:
        return json.loads(stripped[start : end + 1])
    raise ValueError(f"Could not extract JSON from response: {text[:500]}")


def _validate_with_app_build_plan(plan: dict[str, Any], projected: dict[str, Any]) -> dict[str, Any]:
    tool = _load_module(
        TOOLS_DIR / "app_build_plan.py",
        f"smoke_mozaikspay_app_build_plan.{id(plan)}",
    )
    ctx = _Context(**projected)
    tool.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
    return ctx.data["app_build_plan"]


def _managed_template_paths(plan: dict[str, Any]) -> set[str]:
    resolver = _load_module(
        TOOLS_DIR / "resolve_managed_capability_templates.py",
        f"smoke_mozaikspay_template_resolver.{id(plan)}",
    )
    return {
        file.get("filename")
        for file in resolver.resolve_managed_capability_templates(plan.get("capability_packs") or [])
        if isinstance(file, dict) and file.get("filename")
    }


def _assert_mozaikspay_shape(plan: dict[str, Any]) -> None:
    pack_ids = {pack.get("capability_pack_id") for pack in plan.get("capability_packs") or []}
    routes = {page.get("route") for page in plan.get("pages") or []}
    task_paths = {
        path
        for task in plan.get("build_tasks") or []
        for path in task.get("owned_paths") or []
    }
    paths = task_paths | _managed_template_paths(plan)
    required_paths = {
        "services/integrations/mozaikspay_client.py",
        "modules/billing_portal/module.yaml",
    }
    missing = []
    if not {"mozaikspay", "billing_portal"}.issubset(pack_ids):
        missing.append("required capability packs")
    if not required_paths.issubset(paths):
        missing.append(f"required paths: {sorted(required_paths.difference(paths))}")
    if not {"/billing", "/usage"}.issubset(routes):
        missing.append("required routes")
    if any(str(path).startswith("modules/mozaikspay/") for path in paths):
        missing.append("forbidden modules/mozaikspay output")
    if missing:
        raise AssertionError(f"MozaiksPay live smoke shape failed: {missing}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live AppPlanAgent MozaiksPay smoke.")
    parser.add_argument("--model", default=os.getenv("APPPLAN_SMOKE_MODEL", "gpt-5-nano"))
    args = parser.parse_args()

    _load_dotenv()
    projected = merge_build_context(
        build_context_root=MOZAIKSPAY_CONTEXT_ROOT,
        workflow_id="AppGenerator",
        context_variables={},
    )
    agent = _FakeAgent(projected)
    _apply_prompt_hooks(agent)
    response = _call_openai(
        agent.system_message,
        (
            "Build a SaaS creator app. It needs subscriptions, checkout, usage "
            "status, a billing portal, and wallet/account connection through the "
            "selected MozaiksPay managed payments capability. Return only the AppBuildPlan JSON."
        ),
        args.model,
    )
    raw_plan = _extract_json(response)
    normalized = _validate_with_app_build_plan(raw_plan, projected)
    _assert_mozaikspay_shape(normalized)
    print("MozaiksPay live AppPlanAgent smoke: PASS")
    print(
        json.dumps(
            {
                "model": args.model,
                "packs": [p.get("capability_pack_id") for p in normalized.get("capability_packs") or []],
                "task_count": len(normalized.get("build_tasks") or []),
                "routes": [p.get("route") for p in normalized.get("pages") or []],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
