"""Trusted named registries survive the real chat-to-Factory bootstrap path."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import validate_plan_origins
from mozaiksai.core.adapters.ag2_network_runner import _authorized_context_updates
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.session import build_context, launcher
from mozaiksai.core.session.model import RoutingDecision
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context import variables
from mozaiksai.core.workflow.context.authority import (
    AGENT_TEXT_WRITER,
    PERSISTED_REPLAY_WRITER,
    ContextAuthorityClass,
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.execution.run_bootstrap import merge_persisted_extra_context
from mozaiksai.core.workflow.workflow_manager import get_workflow_manager, initialize_workflows
from tests.test_persistence_initial_messages import AG2PersistenceManager, _FakeCollection


def _write_registry(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


@pytest.fixture
def trusted_launch(tmp_path, monkeypatch):
    previous_root = str(get_workflow_manager().workflows_base_path)
    initialize_workflows(str(Path(__file__).resolve().parents[1] / "factory_app" / "workflows"))
    root = tmp_path / "build_context"
    registry_path = root / "Payments" / "context.yaml"
    config = {
        "context_id": "managed_payments",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "pack": {
            "id": "mozaikspay",
            "version": "1.0.0",
            "status": "active",
            "capability_source": "managed_capability",
        },
        "values": {
            "operator_capabilities": ["mozaikspay"],
            "capability_registry": {
                "mozaikspay": {"capability_source": "managed_capability"},
            },
        },
        "projections": {
            "context_variables": {
                "operator_capabilities": {"from": "operator_capabilities"},
                "capability_registry": {"from": "capability_registry"},
                "capability_packs": {"from": "capability_packs"},
            },
        },
    }
    _write_registry(registry_path, config)
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    monkeypatch.setenv("MOZAIKS_LAUNCH_CONTEXT_PROVIDER", "mozaiksai.core.session.build_context:merge_build_context")
    monkeypatch.setenv("CONTEXT_INCLUDE_SCHEMA", "false")
    hooks = PlatformHookRegistry()
    monkeypatch.setattr(launcher, "get_platform_hooks", lambda: hooks)
    # Artifact reads are outside this handoff; retain the real workflow declarations.
    monkeypatch.setattr(variables, "_load_data_reference_value", AsyncMock(return_value=None))
    plan, _ = variables._load_workflow_plan("AppGenerator")
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=plan.definitions)
    coll = _FakeCollection({})
    manager = AG2PersistenceManager()
    monkeypatch.setattr(manager, "_coll", AsyncMock(return_value=coll))
    router = SimpleNamespace(
        route_trigger=AsyncMock(return_value=RoutingDecision(
            workflow_id="AppGenerator", requested_workflow_id="AppGenerator",
        )),
        bind_workflow_session=AsyncMock(),
    )
    try:
        yield SimpleNamespace(
            root=root, path=registry_path, config=config, policy=policy,
            coll=coll, manager=manager, router=router,
        )
    finally:
        initialize_workflows(previous_root)


async def _prepare(fixture, **kwargs):
    return await launcher.prepare_routed_workflow_launch(
        workflow_id="AppGenerator", app_id="app-handoff", user_id="user-handoff",
        session_router=fixture.router, **kwargs,
    )


async def _create(fixture, launch, **kwargs):
    return await launcher.create_routed_chat_session(
        workflow_id=launch.workflow_id, app_id=launch.app_id, user_id=launch.user_id,
        context_variables=launch.validated_context, trigger_meta=launch.trigger_meta,
        chat_id=launch.chat_id, session_fields=kwargs.pop("session_fields", launch.session_fields),
        persistence_manager=fixture.manager, **kwargs,
    )


async def _fetch(fixture):
    return await fixture.manager.fetch_chat_session_extra_context(
        chat_id=fixture.coll.doc["_id"], app_id="app-handoff",
        user_id="user-handoff", workflow_name="AppGenerator",
    )


async def _bootstrap(extra):
    context = await variables._load_context_async(
        "AppGenerator", "app-handoff", runtime_context={"run_build_binding": {}},
    )
    merge_persisted_extra_context(context, extra)
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("pack_source", ["workspace", "installed"])
async def test_trusted_projection_survives_chat_persistence_replay_and_factory_bootstrap(trusted_launch, pack_source):
    if pack_source == "installed":
        # The observed launch selected an installed pack but supplied only a
        # descriptive registry. Resolve its real descriptor before persistence.
        trusted_launch.config.pop("pack")
        trusted_launch.config["projections"]["context_variables"].pop("capability_packs")
        _write_registry(trusted_launch.path, trusted_launch.config)
    launch = await _prepare(trusted_launch, context_variables={
        "concept_overview": "A paid task app", "screen": "studio-create",
    })
    assert launch.validated_context["operator_capabilities"] == ["mozaikspay"]
    descriptor = launch.validated_context["capability_packs"][0]
    assert descriptor["id"] == "mozaikspay"
    assert descriptor["capability_source"] == "managed_capability"
    expected_pack_root = (
        trusted_launch.path.parent.resolve() if pack_source == "workspace"
        else Path(__file__).resolve().parents[1] / "factory_app/build_context/mozaikspay"
    )
    assert descriptor["pack_source_path"] == str(expected_pack_root)

    await _create(trusted_launch, launch)
    for key in ("operator_capabilities", "capability_registry", "capability_packs"):
        assert trusted_launch.coll.doc[key] == launch.validated_context[key]
    replayed = await _fetch(trusted_launch)
    context = await _bootstrap(replayed)
    for key in ("operator_capabilities", "capability_registry", "capability_packs"):
        assert replayed[key] == launch.validated_context[key]
        assert context.snapshot()[key] == launch.validated_context[key]
        authority = context._mozaiks_context_authority_policy.variables[key]
        assert authority.authority_class is ContextAuthorityClass.TOOL_ONLY_INFORMATION
        assert authority.persisted is False
        assert authority.model_visible is False
    assert context.get("screen") == "studio-create"
    assert context.get("concept_overview") is None  # data_reference remains non-replayable
    validate_plan_origins({
        "capability_packs": [{
            "capability_pack_id": "mozaikspay", "surface_id": "mozaikspay_managed",
            "surface_kind": "external_integration", "capability_source": "managed_capability",
            "pack_type": "billing_pack",
        }],
        "build_tasks": [],
    }, context.snapshot())


@pytest.mark.asyncio
async def test_workflow_applicability_controls_launch_and_rehydration(trusted_launch):
    trusted_launch.config["applies_to_workflows"] = ["ValueEngine"]
    _write_registry(trusted_launch.path, trusted_launch.config)
    launch = await _prepare(trusted_launch)
    assert not trusted_launch.policy.build_context_keys.intersection(launch.validated_context)
    await _create(trusted_launch, launch)
    trusted_launch.coll.doc["operator_capabilities"] = ["historical_provider"]
    replayed = await _fetch(trusted_launch)
    assert not trusted_launch.policy.build_context_keys.intersection(replayed)
    context = await _bootstrap(replayed)
    assert context.get("operator_capabilities") is None
    assert context.get("capability_packs") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "rule"),
    [
        ("operator_capabilities", {"value": {"wrong": "object"}}),
        ("capability_registry", {"value": "wrong scalar"}),
        ("undeclared_provider", {"value": ["injected"]}),
        ("capability_registry", {"from_trigger": "provider", "default": {}}),
        ("capability_registry", {"from_context": "provider", "default": {}}),
    ],
)
async def test_malformed_or_untrusted_projections_fail_before_chat_creation(trusted_launch, key, rule):
    trusted_launch.config["projections"]["context_variables"][key] = rule
    _write_registry(trusted_launch.path, trusted_launch.config)
    with pytest.raises((build_context.BuildContextError, ContextAuthorityError)):
        await _prepare(trusted_launch, trigger_payload={"provider": {"injected": True}})
    assert trusted_launch.coll.doc == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["operator_capabilities", "capability_registry", "capability_packs"])
async def test_caller_cannot_override_protected_launch_projection(trusted_launch, key):
    with pytest.raises(ContextAuthorityError, match=key):
        await _prepare(trusted_launch, context_variables={key: ["attacker"]})
    assert trusted_launch.coll.doc == {}


@pytest.mark.asyncio
async def test_custom_launch_provider_cannot_override_trusted_registry(trusted_launch, monkeypatch):
    monkeypatch.setattr(launcher, "apply_launch_context_provider", AsyncMock(return_value={
        "capability_packs": [{"id": "attacker", "capability_source": "managed_capability"}],
    }))
    with pytest.raises((ContextAuthorityError, build_context.BuildContextError)):
        await _prepare(trusted_launch)
    assert trusted_launch.coll.doc == {}


@pytest.mark.asyncio
async def test_session_fields_cannot_replace_approved_projection(trusted_launch):
    launch = await _prepare(trusted_launch)
    with pytest.raises((ContextAuthorityError, build_context.BuildContextError)):
        await _create(trusted_launch, launch, session_fields={"operator_capabilities": ["attacker"]})
    assert trusted_launch.coll.doc == {}


@pytest.mark.asyncio
async def test_bridge_agent_and_nested_readbacks_cannot_mutate_protected_values(trusted_launch):
    context = await _bootstrap({})
    original = context.snapshot()
    bridge = ContextVariablesBridge(context.snapshot(), authority_policy=context._mozaiks_context_authority_policy)
    with pytest.raises(ContextAuthorityError, match="capability_packs"):
        bridge["capability_packs"] = [{"id": "attacker"}]
    with pytest.raises(ContextAuthorityError, match="capability_registry"):
        bridge.pop("capability_registry")
    with pytest.raises(ContextAuthorityError, match="operator_capabilities"):
        _authorized_context_updates(
            {"operator_capabilities": ["attacker"]}, writer_id=AGENT_TEXT_WRITER,
            context_authority_policy=context._mozaiks_context_authority_policy,
        )
    with pytest.raises(TypeError):
        context.get("capability_registry")["mozaikspay"]["capability_source"] = "attacker"
    detached = context.snapshot()
    detached["capability_packs"][0]["id"] = "attacker"
    assert context.snapshot() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("registry_available", [True, False])
async def test_historical_context_is_revalidated_against_current_registry(trusted_launch, registry_available):
    # Old chats have no trusted-origin evidence. A stored claim cannot establish it.
    trusted_launch.coll.doc = {
        "_id": "historical-chat", "app_id": "app-handoff", "user_id": "user-handoff",
        "workflow_name": "AppGenerator", "screen": "studio-create",
        "operator_capabilities": ["historical_provider"],
        "capability_packs": [{"id": "historical_provider", "capability_source": "managed_capability"}],
        "capability_registry": {"historical_provider": {"capability_source": "managed_capability"}},
        "unknown_launch_payload": "must not replay",
    }
    if not registry_available:
        trusted_launch.config["applies_to_workflows"] = []
        _write_registry(trusted_launch.path, trusted_launch.config)
    replayed = await _fetch(trusted_launch)
    assert "unknown_launch_payload" not in replayed
    context = await _bootstrap(replayed)
    assert context.get("screen") == "studio-create"
    if registry_available:
        assert context.snapshot()["operator_capabilities"] == ["mozaikspay"]
        assert context.snapshot()["capability_packs"][0]["id"] == "mozaikspay"
    else:
        assert not trusted_launch.policy.build_context_keys.intersection(replayed)
        assert context.get("capability_packs") is None
        assert context.get("operator_capabilities") is None


@pytest.mark.asyncio
async def test_bootstrap_revalidates_registry_changes_after_chat_replay(trusted_launch):
    launch = await _prepare(trusted_launch)
    await _create(trusted_launch, launch)
    replayed = await _fetch(trusted_launch)
    assert replayed["capability_packs"][0]["version"] == "1.0.0"
    trusted_launch.config["pack"]["version"] = "2.0.0"
    _write_registry(trusted_launch.path, trusted_launch.config)
    context = await _bootstrap(replayed)
    assert context.snapshot()["capability_packs"][0]["version"] == "2.0.0"
    # Bootstrap also scrubs a removed projection, even over an existing runtime snapshot.
    trusted_launch.config["applies_to_workflows"] = []
    _write_registry(trusted_launch.path, trusted_launch.config)
    merge_persisted_extra_context(context, replayed)
    assert context.get("capability_packs") is None


@pytest.mark.asyncio
async def test_generic_tool_only_context_is_not_replayed(trusted_launch):
    plan, _ = variables._load_workflow_plan("AppGenerator")
    generic_keys = {
        key for key, definition in plan.definitions.items()
        if definition.source.type in {"config", "file", "external", "data_reference", "data_entity"}
    }
    assert generic_keys
    injected = {key: "historical_untrusted_value" for key in generic_keys}
    injected.update(deepcopy(trusted_launch.config["values"]))
    assert trusted_launch.policy.filter_for_replay(injected, writer_id=PERSISTED_REPLAY_WRITER) == {}
    context = await _bootstrap(injected)
    for key in generic_keys:
        assert context.get(key) != "historical_untrusted_value"
    assert context.snapshot()["operator_capabilities"] == ["mozaikspay"]
