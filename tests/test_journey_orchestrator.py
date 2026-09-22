from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest
import yaml

from tests.import_utils import import_module_directly


@pytest.fixture(autouse=True)
def generic_platform_hooks(monkeypatch):
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry

    monkeypatch.setattr(PlatformHookRegistry, "_instance", PlatformHookRegistry())

_session_model = import_module_directly("mozaiksai.core.session.model")
_session_router = import_module_directly("mozaiksai.core.session.router")
_workflow_manager = import_module_directly("mozaiksai.core.workflow.workflow_manager")
_session_pkg = types.ModuleType("mozaiksai.core.session")
_session_pkg.TriggerInput = _session_model.TriggerInput
_session_pkg.get_session_router = _session_router.get_session_router

# Save originals before patching so they can be restored after this module's
# collection-time setup is complete.  The fake transport with __path__ = []
# would prevent `mozaiksai.core.transport.rate_limit` from being found in
# later test files collected in the same session.
_orig_session = sys.modules.get("mozaiksai.core.session")
_orig_transport = sys.modules.get("mozaiksai.core.transport")
_orig_session_registry = sys.modules.get("mozaiksai.core.transport.session_registry")

sys.modules["mozaiksai.core.session"] = _session_pkg
_transport_pkg = types.ModuleType("mozaiksai.core.transport")
_transport_pkg.__path__ = []
_session_registry_mod = types.ModuleType("mozaiksai.core.transport.session_registry")
_session_registry_mod.session_registry = types.SimpleNamespace(
    add_workflow=lambda **kwargs: None,
    complete_workflow=lambda ws_id, chat_id: None,
)
sys.modules["mozaiksai.core.transport"] = _transport_pkg
sys.modules["mozaiksai.core.transport.session_registry"] = _session_registry_mod
_journey_mod = import_module_directly("mozaiksai.core.workflow.pack.journey_orchestrator")

# Restore originals so subsequent test files can import the real transport.
def _restore(key, original):
    if original is None:
        sys.modules.pop(key, None)
    else:
        sys.modules[key] = original

_restore("mozaiksai.core.session", _orig_session)
_restore("mozaiksai.core.transport", _orig_transport)
_restore("mozaiksai.core.transport.session_registry", _orig_session_registry)
del _restore, _orig_session, _orig_transport, _orig_session_registry

JourneyOrchestrator = _journey_mod.JourneyOrchestrator
JourneyAdvanceDecision = _session_model.JourneyAdvanceDecision
RoutingDecision = _session_model.RoutingDecision


@pytest.mark.asyncio
async def test_missing_transport_connection_is_logged(monkeypatch, caplog):
    orchestrator = JourneyOrchestrator()

    async def missing_transport(_chat_id):
        return None, None

    monkeypatch.setattr(orchestrator, "_get_transport_conn", missing_transport)
    with caplog.at_level(logging.WARNING):
        await orchestrator._handle_run_complete_inner(
            {
                "chat_id": "chat_missing_transport",
                "workflow_name": "ValueEngine",
                "app_id": "app_1",
                "user_id": "user_1",
            },
            "chat_missing_transport",
        )

    assert "chat_missing_transport" in caplog.text
    assert "missing transport or connection" in caplog.text


_MISSING = object()


def _matches(doc, key, expected):  # noqa: ANN001
    """Mongo semantics for the queries the orchestrator issues: dotted paths and field presence."""
    value = doc
    for part in key.split("."):
        value = value.get(part, _MISSING) if isinstance(value, dict) else _MISSING
    if isinstance(expected, dict) and "$exists" in expected:
        return (value is not _MISSING) is bool(expected["$exists"])
    return value is not _MISSING and value == expected


class _MemoryCollection:
    def __init__(self) -> None:
        self._docs = {}
        self.queries = []

    async def find_one(self, query, projection=None, sort=None):  # noqa: ANN001
        self.queries.append(dict(query))
        matches = [doc for doc in self._docs.values() if all(_matches(doc, k, v) for k, v in query.items())]
        for key, direction in reversed(list(sort or [])):
            matches.sort(key=lambda doc: doc.get(key) or 0, reverse=direction < 0)
        return dict(matches[0]) if matches else None


class _FakePersistenceManager:
    def __init__(self) -> None:
        self._coll_ref = _MemoryCollection()
        self._created = 0

    async def _coll(self):
        return self._coll_ref

    async def create_chat_session(self, chat_id, app_id, workflow_name, user_id, extra_fields=None):  # noqa: ANN001
        self._created += 1
        doc = {
            "_id": chat_id,
            "app_id": app_id,
            "workflow_name": workflow_name,
            "user_id": user_id,
            "status": 0,
            "created_at": self._created,
        }
        if isinstance(extra_fields, dict):
            doc.update(extra_fields)
        self._coll_ref._docs[chat_id] = doc

    async def persist_server_owned_session_fields(self, *, chat_id, fields=None, **_):  # noqa: ANN001, ANN003
        self._coll_ref._docs[chat_id].update(dict(fields or {}))


class _FakeTransport:
    def __init__(self, persistence):
        self._pm = persistence
        self.connections = {}
        self._background_tasks = {}
        self.sent_events = []

    def _get_or_create_persistence_manager(self):
        return self._pm

    async def send_event_to_ui(self, event, chat_id):  # noqa: ANN001
        self.sent_events.append((chat_id, event))

    async def _flush_pre_connection_buffers(self, chat_id):  # noqa: ANN001
        return None

    async def _run_workflow_background(self, **kwargs):  # noqa: ANN003
        return kwargs


class _FakeSessionRouter:
    def __init__(self, *, next_workflows=None, next_transition_id=None) -> None:  # noqa: ANN001
        self.annotated = []
        self.bound = []
        self.next_workflows = list(next_workflows or ["DesignDocs"])
        self.next_transition_id = next_transition_id

    async def advance_journey_after_run_complete(self, **kwargs):  # noqa: ANN003
        return JourneyAdvanceDecision(
            journey_instance_id="journey_run_1",
            journey_key="build",
            current_group_index=0,
            journey_total_steps=2,
            next_group_index=1,
            next_workflows=[] if self.next_transition_id else self.next_workflows,
            next_transition_id=self.next_transition_id,
            completed=False,
        )

    async def route_trigger(self, trigger):  # noqa: ANN001
        return RoutingDecision(
            workflow_id=trigger.workflow_id,
            requested_workflow_id=trigger.workflow_id,
        )

    async def annotate_workflow_chat(self, **kwargs):  # noqa: ANN003
        self.annotated.append(kwargs)

    async def bind_workflow_session(self, **kwargs):  # noqa: ANN003
        self.bound.append(kwargs)


@pytest.mark.asyncio
async def test_journey_orchestrator_uses_session_router_metadata(monkeypatch):
    persistence = _FakePersistenceManager()
    transport = _FakeTransport(persistence)
    transport.connections["chat_source"] = {
        "websocket": object(),
        "ws_id": 77,
        "workflow_name": "ValueEngine",
        "app_id": "app_1",
        "user_id": "user_1",
    }

    fake_router = _FakeSessionRouter()
    orchestrator = JourneyOrchestrator()

    async def _fake_get_transport_conn(chat_id):  # noqa: ANN001
        return transport.connections.get(chat_id), transport

    monkeypatch.setattr(orchestrator, "_get_transport_conn", _fake_get_transport_conn)
    async def router_for_chat(**kwargs):
        return fake_router

    monkeypatch.setattr(_journey_mod, "get_session_router_for_chat", router_for_chat)
    monkeypatch.setattr(_journey_mod.session_registry, "complete_workflow", lambda ws_id, chat_id: None)
    monkeypatch.setattr(_journey_mod.session_registry, "add_workflow", lambda **kwargs: None)

    await orchestrator.handle_run_complete(
        {
            "chat_id": "chat_source",
            "workflow_name": "ValueEngine",
            "app_id": "app_1",
            "user_id": "user_1",
            "status": 1,
        }
    )

    created = next(iter(persistence._coll_ref._docs.values()))
    assert created["session_router_session_id"] == "session_router::app_1::user_1"
    assert created["journey_instance_id"] == "journey_run_1"
    assert created["journey_key"] == "build"
    assert created["journey_position"] == 1
    assert "journey_step_index" not in created

    assert fake_router.annotated
    assert fake_router.bound
    assert transport.sent_events[-1][1]["data"]["journey_id"] == "journey_run_1"


@pytest.mark.asyncio
async def test_journey_handoff_failure_is_visible_without_exposing_exception(monkeypatch):
    orchestrator = JourneyOrchestrator()
    transport = _FakeTransport(_FakePersistenceManager())

    async def fail_handoff(*_args):
        raise ValueError("private launch details")

    async def connected(_chat_id):
        return {"websocket": object()}, transport

    monkeypatch.setattr(orchestrator, "_handle_run_complete_inner", fail_handoff)
    monkeypatch.setattr(orchestrator, "_get_transport_conn", connected)
    await orchestrator.handle_run_complete({"chat_id": "source_chat", "status": "completed"})

    assert len(transport.sent_events) == 1
    chat_id, event = transport.sent_events[0]
    assert chat_id == "source_chat"
    assert event["type"] == "chat.error"
    assert event["data"]["error_code"] == "JOURNEY_ADVANCE_FAILED"
    assert "not complete" in event["data"]["message"]
    assert "private launch details" not in str(event)


@pytest.mark.asyncio
async def test_journey_orchestrator_ignores_failed_run_complete(monkeypatch):
    orchestrator = JourneyOrchestrator()

    async def _unexpected_transport_lookup(chat_id):  # noqa: ANN001
        raise AssertionError(f"failed run should not advance journey for {chat_id}")

    monkeypatch.setattr(orchestrator, "_get_transport_conn", _unexpected_transport_lookup)

    await orchestrator.handle_run_complete(
        {
            "chat_id": "chat_source",
            "workflow_name": "ValueEngine",
            "app_id": "app_1",
            "user_id": "user_1",
            "status": "failed",
        }
    )


@pytest.mark.asyncio
async def test_journey_orchestrator_ignores_run_complete_without_success_status(monkeypatch):
    orchestrator = JourneyOrchestrator()

    async def _unexpected_transport_lookup(chat_id):  # noqa: ANN001
        raise AssertionError(f"ambiguous run should not advance journey for {chat_id}")

    monkeypatch.setattr(orchestrator, "_get_transport_conn", _unexpected_transport_lookup)

    await orchestrator.handle_run_complete(
        {
            "chat_id": "chat_source",
            "workflow_name": "ValueEngine",
            "app_id": "app_1",
            "user_id": "user_1",
            "run_completed": True,
        }
    )


@pytest.mark.asyncio
async def test_journey_orchestrator_ignores_explicit_incomplete_run(monkeypatch):
    orchestrator = JourneyOrchestrator()

    async def _unexpected_transport_lookup(chat_id):  # noqa: ANN001
        raise AssertionError(f"incomplete run should not advance journey for {chat_id}")

    monkeypatch.setattr(orchestrator, "_get_transport_conn", _unexpected_transport_lookup)

    await orchestrator.handle_run_complete(
        {
            "chat_id": "chat_source",
            "workflow_name": "ValueEngine",
            "app_id": "app_1",
            "user_id": "user_1",
            "status": "completed",
            "run_completed": False,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("spoof_provider", [False, True])
async def test_journey_orchestrator_inherits_context_and_applies_launch_provider(monkeypatch, tmp_path, spoof_provider):
    from mozaiksai.core.session.build_context import merge_build_context

    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
    _workflow_manager.UnifiedWorkflowManager._instance = None
    _workflow_manager.initialize_workflows(base_path=str(workflows_root))

    context_root = tmp_path / "build_context" / "Payments"
    context_root.mkdir(parents=True)
    (context_root / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "payments",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "values": {"provider_backed_capabilities": [
            {"intent_id": "monetization", "pack_id": "paid_downloads"},
        ]},
        "projections": {"context_variables": {
            "provider_backed_capabilities": {"from": "provider_backed_capabilities"},
        }},
    }), encoding="utf-8")
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(context_root.parent))

    provider_module = types.ModuleType("_test_journey_launch_context_provider")

    def _merge(  # noqa: ANN001
        context_variables,
        *,
        workflow_id,
        journey_id,
        trigger_source,
        trigger_payload,
        **_,
    ):
        if workflow_id != "AppGenerator":
            return dict(context_variables)
        projected = merge_build_context(workflow_id=workflow_id, context_variables=context_variables)
        if spoof_provider:
            projected["provider_backed_capabilities"] = [{"pack_id": "unapproved_provider"}]
        return {**projected, "not_declared_for_appgenerator": "drop me"}

    provider_module.merge = _merge
    monkeypatch.setitem(sys.modules, "_test_journey_launch_context_provider", provider_module)
    monkeypatch.setenv(
        "MOZAIKS_LAUNCH_CONTEXT_PROVIDER",
        "_test_journey_launch_context_provider:merge",
    )

    persistence = _FakePersistenceManager()
    persistence._coll_ref._docs["chat_source"] = {
        "_id": "chat_source",
        "app_id": "app_1",
        "workflow_name": "AgentGenerator",
        "user_id": "user_1",
        "builder_options": {
            "provider_backed_capabilities": [
                {"intent_id": "monetization", "surfaces": ["checkout", "billing"]}
            ]
        },
        "unused_context": "drop me",
        "interview_complete": True,
        "app_id_was_not_a_launch_input": "other-app",
    }
    transport = _FakeTransport(persistence)
    transport.connections["chat_source"] = {
        "websocket": object(),
        "ws_id": 77,
        "workflow_name": "AgentGenerator",
        "app_id": "app_1",
        "user_id": "user_1",
    }

    fake_router = _FakeSessionRouter(next_workflows=["AppGenerator"])
    orchestrator = JourneyOrchestrator()

    async def _fake_get_transport_conn(chat_id):  # noqa: ANN001
        return transport.connections.get(chat_id), transport

    monkeypatch.setattr(orchestrator, "_get_transport_conn", _fake_get_transport_conn)
    async def router_for_chat(**kwargs):
        return fake_router

    monkeypatch.setattr(_journey_mod, "get_session_router_for_chat", router_for_chat)
    monkeypatch.setattr(_journey_mod.session_registry, "complete_workflow", lambda ws_id, chat_id: None)
    monkeypatch.setattr(_journey_mod.session_registry, "add_workflow", lambda **kwargs: None)

    await orchestrator.handle_run_complete(
        {
            "chat_id": "chat_source",
            "workflow_name": "AgentGenerator",
            "app_id": "app_1",
            "user_id": "user_1",
            "status": 1,
        }
    )

    if spoof_provider:
        assert not any(
            doc.get("workflow_name") == "AppGenerator"
            for doc in persistence._coll_ref._docs.values()
        )
        assert not fake_router.annotated
        assert transport.sent_events[-1][1]["type"] == "chat.error"
        assert transport.sent_events[-1][1]["data"]["error_code"] == "JOURNEY_ADVANCE_FAILED"
        return

    created = next(
        doc
        for doc in persistence._coll_ref._docs.values()
        if doc.get("workflow_name") == "AppGenerator"
    )
    assert created["builder_options"]["provider_backed_capabilities"][0]["intent_id"] == "monetization"
    assert created["provider_backed_capabilities"] == [
        {"intent_id": "monetization", "pack_id": "paid_downloads"}
    ]
    assert "unused_context" not in created
    assert "not_declared_for_appgenerator" not in created


def test_theme_handoff_drops_source_progress_but_preserves_launch_inputs():
    source = {
        "_id": "source-chat", "app_id": "factory", "user_id": "alice",
        "workflow_name": "ValueEngine", "interview_complete": True,
        "concept_review_outcome": "approved", "app_name": "Client Ledger",
        "builder_options": {"monetization_enabled": False},
        "run_build_binding": {"target_app_id": "tracker"},
    }
    projected = _journey_mod._project_launch_context(source, "ThemeCapture")
    assert projected == {
        "app_name": "Client Ledger", "builder_options": {"monetization_enabled": False},
    }
    from mozaiksai.core.session.launcher import validate_context_for_workflow
    from mozaiksai.core.workflow.context.authority import ContextAuthorityError

    assert validate_context_for_workflow("ThemeCapture", projected) == projected
    with pytest.raises(ContextAuthorityError):
        validate_context_for_workflow("ThemeCapture", {"interview_outcome": "ready"})


def test_handoff_cannot_silently_drop_context_for_an_unloaded_workflow():
    with pytest.raises(ValueError, match="not loaded"):
        _journey_mod._project_launch_context({"app_name": "Client Ledger"}, "UnregisteredWorkflow")


@pytest.mark.asyncio
@pytest.mark.parametrize("transition_type", ["chat_session", "silent", "user_choice"])
async def test_journey_transition_projects_only_declared_chat_session_inputs(monkeypatch, transition_type):
    from mozaiksai.core.workflow.pack.schema import WorkflowTransition
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    declaration = {"id": "review_step", "transition_type": transition_type}
    if transition_type == "user_choice":
        declaration.update(ui={"component": "ReviewChoice"}, options=[{"id": "review", "route_to": "ReviewScreen"}])
    else:
        declaration["route_to"] = "ReviewScreen"
    pack = types.SimpleNamespace(transitions=[WorkflowTransition.model_validate(declaration)])
    monkeypatch.setattr(_journey_mod, "load_global_pack_graph", lambda: pack)
    config = {"context_variables": {"definitions": {
        "allowed": {"type": "object", "source": {"type": "state"}},
        "app_id": {"type": "string", "source": {"type": "state"}},
        "calculated": {"type": "boolean", "source": {"type": "computed"}},
        "protected": {
            "type": "string", "source": {"type": "state"},
            "authority_class": "closed_writer_quality_state", "writer_ids": ["deterministic_tool"],
        },
    }}}
    monkeypatch.setattr(workflow_manager, "get_config", lambda name: config if name == "ReviewScreen" else None)
    persistence = _FakePersistenceManager()
    persistence._coll_ref._docs["chat_source"] = {
        "_id": "chat_source", "app_id": "app_1", "user_id": "user_1", "workflow_name": "SourceWorkflow",
        "allowed": {"evidence": "declared input"}, "calculated": True,
        "protected": "not router writable", "undeclared": "never forward",
    }
    transport = _FakeTransport(persistence)
    fake_router = _FakeSessionRouter(next_transition_id="review_step")

    async def router_for_chat(**kwargs):
        return fake_router

    async def connected(chat_id):
        return {"websocket": object(), "ws_id": 77}, transport

    monkeypatch.setattr(_journey_mod, "get_session_router_for_chat", router_for_chat)
    orchestrator = JourneyOrchestrator()
    monkeypatch.setattr(orchestrator, "_get_transport_conn", connected)
    await orchestrator.handle_run_complete({
        "chat_id": "chat_source", "app_id": "app_1", "user_id": "user_1",
        "workflow_name": "SourceWorkflow", "status": "completed",
    })

    assert len(transport.sent_events) == 1
    chat_id, event = transport.sent_events[0]
    assert chat_id == "chat_source"
    assert event["type"] == "chat.transition_requested"
    expected = {"allowed": {"evidence": "declared input"}} if transition_type == "chat_session" else {}
    assert event["data"]["context_variables"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_pack", "missing_transition", "unloaded_target"])
async def test_unresolved_chat_session_transition_reports_handoff_failure(monkeypatch, fault):
    from mozaiksai.core.workflow.pack.schema import WorkflowTransition
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    transition = WorkflowTransition(id="review_step", transition_type="chat_session", route_to="UnloadedReview")
    pack = types.SimpleNamespace(transitions=[] if fault == "missing_transition" else [transition])
    monkeypatch.setattr(_journey_mod, "load_global_pack_graph", lambda: None if fault == "missing_pack" else pack)
    monkeypatch.setattr(workflow_manager, "get_config", lambda name: None)
    persistence = _FakePersistenceManager()
    persistence._coll_ref._docs["chat_source"] = {
        "_id": "chat_source", "app_id": "app_1", "user_id": "user_1", "workflow_name": "SourceWorkflow",
    }
    transport = _FakeTransport(persistence)
    fake_router = _FakeSessionRouter(next_transition_id="review_step")

    async def router_for_chat(**kwargs):
        return fake_router

    async def connected(chat_id):
        return {"websocket": object(), "ws_id": 77}, transport

    monkeypatch.setattr(_journey_mod, "get_session_router_for_chat", router_for_chat)
    orchestrator = JourneyOrchestrator()
    monkeypatch.setattr(orchestrator, "_get_transport_conn", connected)
    await orchestrator.handle_run_complete({
        "chat_id": "chat_source", "app_id": "app_1", "user_id": "user_1",
        "workflow_name": "SourceWorkflow", "status": "completed",
    })

    assert len(transport.sent_events) == 1
    event = transport.sent_events[0][1]
    assert event["type"] == "chat.error"
    assert event["data"]["error_code"] == "JOURNEY_ADVANCE_FAILED"



_CURRENT_BINDING = {
    "build_registry_id": "appreg_current",
    "target_app_id": "tracker",
    "build_id": "build_current",
    "phase": "genesis",
}
_PRIOR_BINDING = {**_CURRENT_BINDING, "build_registry_id": "appreg_prior", "build_id": "build_prior"}
_TARGET_SCOPE = "session_router::app_1::user_1::tracker"
_UNBOUND_SCOPE = "session_router::app_1::user_1"


def _theme_child(chat_id, *, status, binding, scope=_TARGET_SCOPE, created_at=0):  # noqa: ANN001
    doc = {
        "_id": chat_id, "app_id": "app_1", "user_id": "user_1", "workflow_name": "ThemeCapture",
        "session_router_session_id": scope, "journey_instance_id": "journey_run_1",
        "journey_key": "build", "journey_position": 1, "status": status, "created_at": created_at,
    }
    if binding is not None:
        doc["run_build_binding"] = dict(binding)
    return doc


async def _complete_value_engine(monkeypatch, *, source_binding, children, binding_error=None):  # noqa: ANN001
    """Run the ValueEngine -> ThemeCapture handoff against persisted sibling chats."""
    from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    monkeypatch.setattr(
        workflow_manager, "get_config",
        lambda name: {"context_variables": {"definitions": {}}} if name == "ThemeCapture" else None,
    )
    persistence = _FakePersistenceManager()
    source = {"_id": "chat_source", "app_id": "app_1", "user_id": "user_1", "workflow_name": "ValueEngine", "status": 1}
    if source_binding is not None:
        source["run_build_binding"] = dict(source_binding)
    persistence._coll_ref._docs["chat_source"] = source
    for child in children:
        persistence._coll_ref._docs[child["_id"]] = child

    binding_requests = []

    async def chat_session_fields(**kwargs):  # noqa: ANN003
        binding_requests.append(kwargs)
        if binding_error is not None:
            raise binding_error
        return {"run_build_binding": dict(source_binding)} if source_binding is not None else {}

    get_platform_hooks().register_bundle({"chat_session_fields": chat_session_fields}, source="test")

    transport = _FakeTransport(persistence)
    transport.connections["chat_source"] = {
        "websocket": object(), "ws_id": 77, "workflow_name": "ValueEngine", "app_id": "app_1", "user_id": "user_1",
    }
    fake_router = _FakeSessionRouter(next_workflows=["ThemeCapture"])
    orchestrator = JourneyOrchestrator()

    async def connected(chat_id):  # noqa: ANN001
        return transport.connections.get(chat_id), transport

    async def router_for_chat(**kwargs):  # noqa: ANN003
        return fake_router

    activated = []
    monkeypatch.setattr(orchestrator, "_get_transport_conn", connected)
    monkeypatch.setattr(_journey_mod, "get_session_router_for_chat", router_for_chat)
    monkeypatch.setattr(_journey_mod.session_registry, "complete_workflow", lambda ws_id, chat_id: None)
    monkeypatch.setattr(_journey_mod.session_registry, "add_workflow", lambda **kwargs: activated.append(kwargs))

    await orchestrator.handle_run_complete({
        "chat_id": "chat_source", "workflow_name": "ValueEngine", "app_id": "app_1", "user_id": "user_1",
        "status": "completed", "run_completed": True,
    })
    return types.SimpleNamespace(
        persistence=persistence, transport=transport, activated=activated, binding_requests=binding_requests,
        theme_chats=[doc for doc in persistence._coll_ref._docs.values() if doc["workflow_name"] == "ThemeCapture"],
        switched_to=next(
            (event["data"]["to_chat_id"] for _cid, event in transport.sent_events if event["type"] == "chat.context_switched"),
            None,
        ),
        errors=[event for _cid, event in transport.sent_events if event["type"] == "chat.error"],
    )


@pytest.mark.asyncio
async def test_new_build_creates_its_own_child_instead_of_a_prior_builds_completed_sibling(monkeypatch):
    result = await _complete_value_engine(
        monkeypatch,
        source_binding=_CURRENT_BINDING,
        children=[_theme_child("theme_prior_build", status=1, binding=_PRIOR_BINDING)],
    )

    assert result.errors == []
    assert result.switched_to not in (None, "theme_prior_build")
    assert [call["chat_id"] for call in result.activated] == [result.switched_to]
    created = result.persistence._coll_ref._docs[result.switched_to]
    assert created["run_build_binding"] == _CURRENT_BINDING
    assert created["journey_instance_id"] == "journey_run_1"
    assert created["journey_position"] == 1
    assert [call["source_chat_id"] for call in result.binding_requests] == ["chat_source"]
    assert result.persistence._coll_ref._docs["theme_prior_build"]["status"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", [1, 2])
async def test_terminal_child_of_the_same_build_is_never_selected(monkeypatch, terminal_status):
    result = await _complete_value_engine(
        monkeypatch,
        source_binding=_CURRENT_BINDING,
        children=[_theme_child("theme_terminal", status=terminal_status, binding=_CURRENT_BINDING)],
    )

    assert result.errors == []
    assert result.switched_to not in (None, "theme_terminal")
    assert [call["chat_id"] for call in result.activated] == [result.switched_to]
    assert result.persistence._coll_ref._docs[result.switched_to]["run_build_binding"] == _CURRENT_BINDING
    assert len(result.theme_chats) == 2


@pytest.mark.asyncio
async def test_in_progress_child_of_the_same_build_is_reused(monkeypatch):
    result = await _complete_value_engine(
        monkeypatch,
        source_binding=_CURRENT_BINDING,
        children=[
            _theme_child("theme_prior_build", status=0, binding=_PRIOR_BINDING),
            _theme_child("theme_in_progress", status=0, binding=_CURRENT_BINDING),
        ],
    )

    assert result.errors == []
    assert result.switched_to == "theme_in_progress"
    assert [call["chat_id"] for call in result.activated] == ["theme_in_progress"]
    assert result.binding_requests == []
    assert len(result.theme_chats) == 2


@pytest.mark.asyncio
async def test_newest_in_progress_child_of_the_same_build_wins(monkeypatch):
    result = await _complete_value_engine(
        monkeypatch,
        source_binding=_CURRENT_BINDING,
        children=[
            _theme_child("theme_newest", status=0, binding=_CURRENT_BINDING, created_at=2),
            _theme_child("theme_oldest", status=0, binding=_CURRENT_BINDING, created_at=1),
        ],
    )

    assert result.switched_to == "theme_newest"
    assert result.binding_requests == []
    assert len(result.theme_chats) == 2


@pytest.mark.asyncio
async def test_unbound_source_never_adopts_a_bound_child(monkeypatch):
    """A host that permits unbound sessions gets an unbound child, never the bound sibling."""
    result = await _complete_value_engine(
        monkeypatch,
        source_binding=None,
        children=[_theme_child("theme_bound", status=0, binding=_CURRENT_BINDING, scope=_UNBOUND_SCOPE)],
    )

    assert result.errors == []
    assert result.switched_to not in (None, "theme_bound")
    created = result.persistence._coll_ref._docs[result.switched_to]
    assert "run_build_binding" not in created
    assert created["session_router_session_id"] == _UNBOUND_SCOPE
    reuse_query = next(q for q in result.persistence._coll_ref.queries if q.get("workflow_name") == "ThemeCapture")
    assert reuse_query["run_build_binding"] == {"$exists": False}
    assert reuse_query["status"] == 0


@pytest.mark.asyncio
async def test_unbound_source_fails_closed_when_the_host_refuses_to_bind(monkeypatch):
    """A factory host rejects a child for an unbound source; nothing is created or activated."""
    result = await _complete_value_engine(
        monkeypatch,
        source_binding=None,
        children=[_theme_child("theme_bound", status=0, binding=_CURRENT_BINDING, scope=_UNBOUND_SCOPE)],
        binding_error=ValueError("Source session has no build binding"),
    )

    assert result.switched_to is None
    assert result.activated == []
    assert result.theme_chats == [result.persistence._coll_ref._docs["theme_bound"]]
    assert [call["source_chat_id"] for call in result.binding_requests] == ["chat_source"]
    assert [event["data"]["error_code"] for event in result.errors] == ["JOURNEY_ADVANCE_FAILED"]
    assert "Source session" not in str(result.errors)


@pytest.mark.asyncio
async def test_unbound_source_reuses_its_own_unbound_in_progress_child(monkeypatch):
    result = await _complete_value_engine(
        monkeypatch,
        source_binding=None,
        children=[_theme_child("theme_unbound", status=0, binding=None, scope=_UNBOUND_SCOPE)],
    )

    assert result.errors == []
    assert result.switched_to == "theme_unbound"
    assert len(result.theme_chats) == 1


def test_next_chat_reuse_filter_pins_every_binding_field_and_in_progress_status():
    from mozaiksai.core.session.build_binding import RunBuildBinding

    scope = _journey_mod._next_chat_reuse_filter(RunBuildBinding.model_validate(_CURRENT_BINDING))
    assert scope == {
        "status": 0,
        "run_build_binding.build_registry_id": "appreg_current",
        "run_build_binding.target_app_id": "tracker",
        "run_build_binding.build_id": "build_current",
        "run_build_binding.phase": "genesis",
    }
    assert _journey_mod._next_chat_reuse_filter(None) == {"status": 0, "run_build_binding": {"$exists": False}}
