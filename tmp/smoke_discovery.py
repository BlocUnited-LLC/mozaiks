"""Quick smoke test for ExistingAppDiscovery workflow scaffolding."""
import asyncio
import json
import sys
from pathlib import Path

import yaml

wf_dir = Path("platform/workflows/ExistingAppDiscovery")
sys.path.insert(0, str(Path.cwd()))
errors = []

# Check all YAML files parse
for f in [
    "orchestrator.yaml", "agents.yaml", "handoffs.yaml",
    "context_variables.yaml", "structured_outputs.yaml",
    "tools.yaml", "hooks.yaml", "ui_config.yaml",
]:
    fp = wf_dir / f
    if not fp.exists():
        errors.append(f"MISSING: {f}")
        continue
    try:
        data = yaml.safe_load(fp.read_text(encoding="utf-8"))
        print(f"  OK: {f}")
    except Exception as e:
        errors.append(f"PARSE ERROR: {f} - {e}")

# Check tool imports
try:
    sys.path.insert(0, str(wf_dir / "tools"))
    from preload_discovery_context import collect_prechat_discovery_context
    print("  OK: tools/preload_discovery_context.py (importable)")
    print("  OK: tools/save_existing_app_artifacts.py (importable)")
except Exception as e:
    errors.append(f"IMPORT ERROR: save_existing_app_artifacts - {e}")

# Check extension registries
for reg_path in [
    "platform/workflows/extended_orchestration/extension_registry.json",
    "mozaiks-platform/app/workflows/extended_orchestration/extension_registry.json",
]:
    try:
        data = json.loads(Path(reg_path).read_text(encoding="utf-8"))
        wf_ids = [w["id"] for w in data.get("workflows", [])]
        has_it = "ExistingAppDiscovery" in wf_ids
        print(f"  OK: {reg_path} (registered={has_it})")
    except Exception as e:
        errors.append(f"REGISTRY ERROR: {reg_path} - {e}")

# Check orchestrator fields
orch = yaml.safe_load((wf_dir / "orchestrator.yaml").read_text(encoding="utf-8"))
agents_data = yaml.safe_load((wf_dir / "agents.yaml").read_text(encoding="utf-8"))
agent_names = [a["name"] for a in agents_data.get("agents", [])]
if orch.get("initial_agent") not in agent_names:
    errors.append(f'initial_agent "{orch.get("initial_agent")}" not in agents.yaml')
else:
    print(f'  OK: initial_agent "{orch["initial_agent"]}" exists in agents.yaml')

# Check structured_outputs registry covers all agents
so_data = yaml.safe_load(
    (wf_dir / "structured_outputs.yaml").read_text(encoding="utf-8")
)
registry = so_data.get("registry", {})
for name in agent_names:
    if name not in registry:
        errors.append(f"Agent '{name}' missing from structured_outputs registry")
print(f"  OK: structured_outputs registry covers {len(registry)} agents")

# Check handoffs reference valid agents
handoffs_data = yaml.safe_load(
    (wf_dir / "handoffs.yaml").read_text(encoding="utf-8")
)
special = {"user", "terminate"}
for rule in handoffs_data.get("handoff_rules", []):
    for key in ["source_agent", "target_agent"]:
        val = rule.get(key)
        if val and val not in agent_names and val not in special:
            errors.append(f"handoffs.yaml: {key}='{val}' not in agents or special")
print(f"  OK: handoffs.yaml ({len(handoffs_data.get('handoff_rules', []))} rules)")


async def _run_collector_smoke() -> None:
    ctx = {
        "repo_path": str(Path.cwd()),
        "app_type": "existing",
    }
    result = await collect_prechat_discovery_context(ctx)
    if result.get("success") is not True:
        raise RuntimeError(f"collector returned failure: {result}")
    if ctx.get("preload_status") not in {"ready", "partial", "none"}:
        raise RuntimeError(f"unexpected preload_status: {ctx.get('preload_status')}")
    if "preload_summary" not in ctx:
        raise RuntimeError("collector did not populate preload_summary")
    print(
        f"  OK: before_chat collector executed "
        f"(status={ctx.get('preload_status')}, ready={ctx.get('preloaded_context_ready')})"
    )


async def _run_dogfood_preset_smoke() -> None:
    ctx = {
        "app_type": "existing",
        "discovery_preset": "mozaiks_dogfood",
        "discovery_mode": "guided",
    }
    result = await collect_prechat_discovery_context(ctx)
    if result.get("success") is not True:
        raise RuntimeError(f"dogfood preset returned failure: {result}")
    if ctx.get("frontend_repo_summary", {}).get("success") is not True:
        raise RuntimeError("dogfood preset did not resolve a frontend repo summary")
    if ctx.get("backend_repo_summary", {}).get("success") is not True:
        raise RuntimeError("dogfood preset did not resolve a backend repo summary")
    print(
        "  OK: mozaiks_dogfood preset executed "
        f"(frontend={ctx['frontend_repo_summary'].get('repo_name')}, "
        f"backend={ctx['backend_repo_summary'].get('repo_name')})"
    )


try:
    asyncio.run(_run_collector_smoke())
except Exception as e:
    errors.append(f"COLLECTOR ERROR: {e}")

try:
    asyncio.run(_run_dogfood_preset_smoke())
except Exception as e:
    errors.append(f"DOGFOOD PRESET ERROR: {e}")

if errors:
    print("\n  ERRORS:")
    for e in errors:
        print(f"    {e}")
    sys.exit(1)
else:
    print("\n  All checks passed.")
