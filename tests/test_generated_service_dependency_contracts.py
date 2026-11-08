from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_file_contracts_body,
)
from mozaiksai.core.runtime.app.module_loader import ModuleLoader, ModuleLoadError

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("agent_name", ["AppPlanAgent", "ConfigMiddlewareAgent", "ServiceAgent"])
def test_dependency_and_mutation_order_contract_reaches_existing_consumers(agent_name):
    contracts = yaml.safe_load(
        (ROOT / "factory_app/build_context/AppGenerator/file_contracts.yaml").read_text(encoding="utf-8")
    )
    agent = SimpleNamespace(name=agent_name, context_variables={})
    projected = _build_file_contracts_body(agent, contracts)
    for obligation in (
        "modules.<module_id>.backend.service",
        "Do not derive sibling paths from __package__",
        "retain the original ctx",
        "action event declarations rather than fabricating authority",
        "check that conditional claim before creating or changing dependent records",
        "authoritative claimed values, not an earlier mutable snapshot",
        "A unique result index alone does not fence concurrent edits or cancellation",
        "an exception cleanup block is not recovery after process interruption",
        "ModuleLoader requires event.producer to match the declaring module",
        "Never copy foreign event contracts, retag their producer, or replace ctx",
        "without inventing ctx methods or silently changing the approved mutation order",
    ):
        assert obligation in projected


def test_service_prompt_preserves_dependency_and_approved_lifecycle_ownership():
    agents = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8"))
    agent = next(agent for agent in agents["agents"] if agent["name"] == "ServiceAgent")
    text = "\n".join(section["content"] for section in agent["prompt_sections"])
    assert "injected FILE CONTRACTS CONTEXT for approved sibling service imports and mutation ordering" in text
    assert "existing signature, preserving the original ctx" in text
    assert "authoritative claimed record" in text
    assert "report the owning contract gap" in text


def test_contract_prompt_preserves_foreign_event_ownership_and_approved_lifecycle():
    agents = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8"))
    agent = next(agent for agent in agents["agents"] if agent["name"] == "ConfigMiddlewareAgent")
    text = "\n".join(section["content"] for section in agent["prompt_sections"])
    assert "Each `events_yaml` producer must equal its declaring module" in text
    assert "Do not copy a dependency's event contracts, retag their producer, or replace ctx" in text
    assert "do not invent a ctx API or reorder the approved mutations" in text


def test_design_and_plan_preserve_multi_collection_invariant_ownership():
    design = yaml.safe_load((ROOT / "factory_app/workflows/DesignDocs/agents.yaml").read_text(encoding="utf-8"))
    design_text = yaml.safe_dump(design, width=10000)
    for obligation in (
        "One module may own multiple collections",
        "does not create a multi-document transaction",
        "supported persistence operations",
        "already approved decomposition must return through design review",
    ):
        assert obligation in design_text
    agents = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8"))
    planner = next(agent for agent in agents["agents"] if agent["name"] == "AppPlanAgent")
    text = "\n".join(section["content"] for section in planner["prompt_sections"])
    assert "do not split it into one module per entity" in text
    assert "report the design contract gap for review" in text
    assert "silently merging owners, or weakening the business rules, roles or pages" in text


@pytest.fixture
def isolated_module_imports():
    """Restore the app import aliases and synthetic packages touched by ModuleLoader."""
    def owned(name):
        return (
            name in {"modules", "services"}
            or name.startswith(("modules.", "services.", "mozaiks_runtime_module_"))
        )

    saved_modules = {name: module for name, module in sys.modules.items() if owned(name)}
    saved_path = sys.path[:]
    try:
        yield
    finally:
        for name in tuple(sys.modules):
            if owned(name):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def _write_test_module(root: Path, name: str, service: str) -> None:
    module_root = root / "modules" / name
    backend = module_root / "backend"
    backend.mkdir(parents=True)
    manifest = {
        "schema_version": "mozaiks.module.v1",
        "module": {
            "id": name, "display_name": name, "version": "1.0.0",
            "description": "Test-owned service dependency fixture",
            "handler": "backend.handler:Handler",
        },
        "actions": [{
            "id": "inspect", "description": "Inspect the unchanged context",
            "handler_method": "inspect", "input_schema": {"type": "object"},
            "output_schema": {"type": "object"}, "permissions": [], "emits": [],
        }],
    }
    (module_root / "module.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (backend / "service.py").write_text(service, encoding="utf-8")
    (backend / "handler.py").write_text(
        "from . import service\nclass Handler:\n"
        "    async def inspect(self, ctx, **params):\n"
        "        return await service.inspect(ctx)\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_canonical_module_alias_resolves_dependency_from_isolated_handler(tmp_path, isolated_module_imports):
    """Exercise hand-authored test fixtures, never a live generated candidate."""
    (tmp_path / "app.json").write_text('{"appName":"Dependency test"}', encoding="utf-8")
    _write_test_module(tmp_path, "inventory", "async def inspect(ctx):\n    return ctx\n")
    _write_test_module(
        tmp_path, "consumer",
        "from modules.inventory.backend import service as inventory_service\n"
        "async def inspect(ctx):\n"
        "    return {'context': await inventory_service.inspect(ctx), 'package': __package__}\n",
    )
    loader = ModuleLoader(str(tmp_path))
    loader.load("inventory")
    consumer = loader.load("consumer")
    context = SimpleNamespace(module_id="consumer", permissions=["records.inspect"])
    result = await consumer.handler.inspect(context)
    assert result["package"] == "mozaiks_runtime_module_consumer.backend"
    assert result["context"] is context
    assert context.module_id == "consumer"
    assert context.permissions == ["records.inspect"]


def test_module_loader_rejects_foreign_event_producer(tmp_path, isolated_module_imports):
    _write_test_module(tmp_path, "consumer", "async def inspect(ctx):\n    return {}\n")
    contracts = tmp_path / "modules/consumer/contracts"
    contracts.mkdir()
    (contracts / "events.yaml").write_text(yaml.safe_dump({
        "schema_version": "mozaiks.events.v1",
        "events": [{"type": "domain.inventory.changed", "producer": "inventory",
                    "version": 1, "payload_schema": {"type": "object"}}],
    }), encoding="utf-8")
    with pytest.raises(ModuleLoadError, match="producer 'inventory' must match module id 'consumer'"):
        ModuleLoader(str(tmp_path)).load("consumer")
