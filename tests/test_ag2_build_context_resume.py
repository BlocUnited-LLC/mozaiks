"""Protected projections survive the actual AG2 channel serialization boundary."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
import yaml
from ag2 import Agent
from ag2.knowledge import MemoryKnowledgeStore
from ag2.network import Hub
from ag2.network.hub.layout import channel_metadata_path

from mozaiksai.core.adapters.ag2_network_runner import (
    AG2NetworkRunner,
    AG2NetworkRunnerRequest,
    _require_current_build_context,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.session.build_context import load_trusted_build_context
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import build_context_authority_policy


@pytest.fixture
def registered_contracts(tmp_path, monkeypatch):
    factory = Path(__file__).resolve().parents[1] / "factory_app"
    source = factory / "build_context" / "mozaikspay"
    target = tmp_path / "build_context" / "mozaikspay"
    target.mkdir(parents=True)
    shutil.copyfile(source / "context.yaml", target / "context.yaml")
    registry = yaml.safe_load((source / "context.yaml").read_text(encoding="utf-8"))
    for asset in registry["assets"]:
        if asset["kind"] == "contract":
            shutil.copyfile(source / asset["path"], target / asset["path"])
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(target.parent))
    definitions = yaml.safe_load(
        (factory / "workflows" / "AppGenerator" / "context_variables.yaml").read_text(encoding="utf-8")
    )["definitions"]
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=definitions)
    return target, policy


def _provider_contract(contracts):
    return next(contract for contract in contracts if contract["contract_id"] == "mozaikspay_provider_api")


class _BridgeAgent(Agent):
    def __init__(self, name, bridge):
        super().__init__(name, prompt="Deterministic protected-context resume probe")
        self._mozaiks_context_bridge = bridge
        self.calls = 0

    async def ask(self, *msg: Any, **kwargs: Any) -> Any:
        self.calls += 1
        contracts = self._mozaiks_context_bridge.get("operator_contracts")
        assert isinstance(contracts, tuple)
        assert isinstance(contracts[0], MappingProxyType)
        return SimpleNamespace(body="Review the build." if self.name == "Planner" else "Build complete.")


def _request(policy, store, *, resume=False):
    bridge = ContextVariablesBridge(load_trusted_build_context(policy), authority_policy=policy)
    planner = _BridgeAgent("Planner", bridge)
    worker = _BridgeAgent("Worker", bridge)
    request = AG2NetworkRunnerRequest(
        workflow_name="AppGenerator", app_id="projection-app", chat_id="projection-chat",
        agents={"Planner": planner, "Worker": worker},
        initial_agent_name="Planner", initial_message="Approved" if resume else "Start",
        # Match orchestration's detached initial snapshot while the real agents
        # receive the bridge, which AG2 hydrates before each turn.
        context_variables=bridge.snapshot(), context_authority_policy=policy,
        knowledge_store=store, resume_existing_only=resume, close_timeout_seconds=3.0,
        transition_rules=[
            {"source_agent": "Planner", "target_agent": "user", "transition_type": "after_turn"},
            {"source_agent": "user", "target_agent": "Worker", "transition_type": "after_turn"},
            {"source_agent": "Worker", "target_agent": "terminate", "transition_type": "after_turn"},
        ],
    )
    return request, worker


async def _paused_channel(policy):
    # This is AG2's real string-backed store, not a context dict fake: Hub writes
    # channel metadata/WAL as JSON and hydrates them when a new Hub opens it.
    store = MemoryKnowledgeStore()
    request, worker = _request(policy, store)
    paused = await AG2NetworkRunner().run(request)
    assert paused.status is RunStatus.PAUSED, paused.error
    assert paused.live_run is not None
    assert request.agents["Planner"].calls == 1
    assert worker.calls == 0
    stored = await store.read(channel_metadata_path(paused.channel_id))
    assert isinstance(stored, str)
    persisted = json.loads(stored)["knobs"]["context_vars"]
    current = load_trusted_build_context(policy)
    saved_errors = _provider_contract(persisted["operator_contracts"])["error_handling"]["http_errors"]
    current_errors = _provider_contract(current["operator_contracts"])["error_handling"]["http_errors"]
    assert set(saved_errors) == {"400", "401", "403", "404"}
    assert set(current_errors) == {400, 401, 403, 404}
    # This exact shipped contract differs only by the JSON key representation.
    assert persisted["operator_contracts"] != current["operator_contracts"]
    assert persisted["operator_contracts"] == json.loads(json.dumps(current["operator_contracts"]))
    return store, paused, worker


def test_frozen_bridge_accepts_current_protected_build_context(registered_contracts):
    _, policy = registered_contracts
    bridge = ContextVariablesBridge(load_trusted_build_context(policy), authority_policy=policy)
    assert isinstance(bridge.get("operator_contracts"), tuple)
    assert isinstance(bridge.get("operator_contracts")[0], MappingProxyType)
    _require_current_build_context(saved=bridge, policy=policy)


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_mode", ["live", "durable"])
async def test_unchanged_live_operator_contract_resumes_after_channel_round_trip(registered_contracts, resume_mode):
    _, policy = registered_contracts
    store, paused, worker = await _paused_channel(policy)
    try:
        if resume_mode == "durable":
            await paused.live_run.close()
            reloaded = await Hub.open(store, ttl_sweep_interval=0, expectation_sweep_interval=0)
            try:
                restored = reloaded.adapter_state(paused.channel_id).context_vars
                bridge = ContextVariablesBridge(restored, authority_policy=policy)
                _require_current_build_context(saved=bridge.snapshot(), policy=policy)
            finally:
                await reloaded.close()
            request, worker = _request(policy, store, resume=True)
            resumed = await AG2NetworkRunner().run(request)
        else:
            resumed = await paused.live_run.continue_with_user_message("Approved")
        assert resumed.status is RunStatus.COMPLETED, resumed.error
        assert resumed.channel_id == paused.channel_id
        assert worker.calls == 1
    finally:
        await paused.live_run.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_mode", ["live", "durable"])
@pytest.mark.parametrize("mutation", [
    "nested_value", "sequence_order", "removed_contract", "scalar_type", "key_collision",
])
async def test_changed_operator_contract_still_rejects_resume(registered_contracts, resume_mode, mutation):
    target, policy = registered_contracts
    store, paused, worker = await _paused_channel(policy)
    contract_path = target / "provider_api_contract.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    if mutation == "nested_value":
        contract["error_handling"]["http_errors"][401] = "Changed authorization requirement."
    elif mutation == "sequence_order":
        contract["endpoints"].reverse()
    elif mutation == "scalar_type":
        # Python equates True with 1; their JSON types carry different meaning.
        assert contract["auth"]["headers"][0]["required"] is True
        contract["auth"]["headers"][0]["required"] = 1
    elif mutation == "key_collision":
        # A lossy key conversion would discard the changed int-keyed value and
        # retain the old string-keyed value, falsely admitting stale authority.
        errors = contract["error_handling"]["http_errors"]
        errors["401"] = errors[401]
        errors[401] = "Changed authorization requirement hidden by key collision."
    else:
        registry_path = target / "context.yaml"
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        registry["assets"] = [asset for asset in registry["assets"] if asset["path"] != contract_path.name]
        registry_path.write_text(yaml.safe_dump(registry), encoding="utf-8")
    contract_path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    try:
        if resume_mode == "durable":
            await paused.live_run.close()
            request, worker = _request(policy, store, resume=True)
            resumed = await AG2NetworkRunner().run(request)
        else:
            resumed = await paused.live_run.continue_with_user_message("Approved")
        assert resumed.status is RunStatus.FAILED
        assert resumed.error == "ag2_network_stale_build_context"
        assert worker.calls == 0
    finally:
        await paused.live_run.close()
