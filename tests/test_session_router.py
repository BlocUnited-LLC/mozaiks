from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.import_utils import import_module_directly

_schema = import_module_directly("mozaiksai.core.workflow.pack.schema")
_session_model = import_module_directly("mozaiksai.core.session.model")
_session_persist = import_module_directly("mozaiksai.core.session.persistence")
_session_router = import_module_directly("mozaiksai.core.session.router")
_control_plane = import_module_directly("mozaiksai.control_plane")
_data_models = import_module_directly("mozaiksai.core.data.models")

parse_global_pack_graph = _schema.parse_global_pack_graph
TriggerInput = _session_model.TriggerInput
SessionRouter = _session_router.SessionRouter
SessionStateStore = _session_persist.SessionStateStore
WorkflowStatus = _data_models.WorkflowStatus
get_refinement_trigger_route_resolver = _control_plane.get_refinement_trigger_route_resolver
get_orchestration_control_harness = _control_plane.get_orchestration_control_harness


class _FakeChangeClassifier:
    def __init__(
        self,
        *,
        change_class: str,
        rationale: str,
        confidence: float = 0.91,
        signals: list[str] | None = None,
    ) -> None:
        self._result = SimpleNamespace(
            change_class=change_class,
            rationale=rationale,
            confidence=confidence,
            signals=list(signals or []),
        )

    async def classify(self, **kwargs):  # noqa: ANN003
        return self._result


class _MemoryCollection:
    def __init__(self) -> None:
        self._docs = {}

    async def find_one(self, query, projection=None, sort=None):  # noqa: ANN001
        for doc in self._docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    async def update_one(self, filter_query, update, upsert=False):  # noqa: ANN001
        doc_id = filter_query.get("_id")
        if not doc_id:
            for existing_id, existing_doc in self._docs.items():
                if all(existing_doc.get(k) == v for k, v in filter_query.items()):
                    doc_id = existing_id
                    break
        if not doc_id:
            if not upsert:
                return
            doc_id = f"doc_{len(self._docs) + 1}"

        base = dict(self._docs.get(doc_id, {"_id": doc_id}))
        for key, value in (update.get("$set") or {}).items():
            base[key] = value
        self._docs[doc_id] = base


class _FakePersistence:
    def __init__(self) -> None:
        self._default = _MemoryCollection()
        self._named = {}

    async def _coll(self, name=None):  # noqa: ANN001
        if not name:
            return self._default
        if name not in self._named:
            self._named[name] = _MemoryCollection()
        return self._named[name]


def _make_pack():
    return parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {"id": "ValueEngine"},
                {"id": "DesignDocs", "dependencies": ["ValueEngine"]},
                {"id": "AgentGenerator", "dependencies": ["DesignDocs"]},
                {"id": "AppGenerator", "dependencies": ["DesignDocs", "AgentGenerator"]},
            ],
            "transitions": [],
            "workflow_sequences": [],
        }
    )


@pytest.mark.asyncio
async def test_route_trigger_reroutes_to_first_unmet_dependency(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = _make_pack()
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    decision = await router.route_trigger(
        TriggerInput(
            app_id="app_1",
            user_id="user_1",
            trigger_source="transition",
            workflow_id="AppGenerator",
        )
    )

    assert decision.workflow_id == "ValueEngine"
    assert decision.requested_workflow_id == "AppGenerator"
    assert decision.rerouted_by_dependency is True
    assert decision.unmet_dependency is not None
    assert decision.unmet_dependency.workflow_id == "ValueEngine"


@pytest.mark.asyncio
async def test_route_trigger_keeps_requested_workflow_when_dependencies_met(monkeypatch):
    persistence = _FakePersistence()
    sessions = await persistence._coll()
    sessions._docs["c1"] = {
        "_id": "c1",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "ValueEngine",
        "status": int(WorkflowStatus.COMPLETED),
    }
    sessions._docs["c2"] = {
        "_id": "c2",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "DesignDocs",
        "status": int(WorkflowStatus.COMPLETED),
    }
    sessions._docs["c3"] = {
        "_id": "c3",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "AgentGenerator",
        "status": int(WorkflowStatus.COMPLETED),
    }

    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = _make_pack()
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    decision = await router.route_trigger(
        TriggerInput(
            app_id="app_1",
            user_id="user_1",
            trigger_source="transition",
            workflow_id="AppGenerator",
        )
    )

    assert decision.workflow_id == "AppGenerator"
    assert decision.rerouted_by_dependency is False
    assert decision.unmet_dependency is None


@pytest.mark.asyncio
async def test_route_trigger_refinement_uses_injected_trigger_route_resolver(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    resolver = get_refinement_trigger_route_resolver()
    monkeypatch.setattr(
        resolver,
        "_classifier",
        _FakeChangeClassifier(
            change_class="patch",
            rationale="Scoped patch to the workflow bundle.",
            signals=["bug_fix", "local_change"],
        ),
    )
    router = SessionRouter(
        persistence=persistence,
        store=store,
        trigger_route_resolver=resolver,
    )
    pack = _make_pack()
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    decision = await router.route_trigger(
        TriggerInput(
            app_id="app_1",
            user_id="user_1",
            trigger_source="refinement",
            workflow_id=None,
            trigger_payload={
                "refinement_request": {
                    "declared_change_class": "patch",
                    "artifact_kind": "workflow_bundle",
                    "artifact_key": "workflow_bundle",
                    "artifact_version_id": "v1",
                    "raw_user_request": "Update workflow naming",
                    "source_surface": "studio_create",
                }
            },
            context_variables={"screen": "studio-create"},
        )
    )

    # workflow_bundle owner is AgentGenerator, but dependency reroute applies
    assert decision.requested_workflow_id == "AgentGenerator"
    assert decision.workflow_id == "ValueEngine"
    assert decision.rerouted_by_dependency is True
    assert decision.lifecycle_state == _session_model.SessionLifecycle.ACTIVE
    assert decision.context_seed.get("change_class") == "patch"
    assert decision.context_seed.get("artifact_kind") == "workflow_bundle"
    assert decision.context_seed.get("artifact_version_id") == "v1"
    assert decision.context_seed.get("change_intent", {}).get("change_class") == "patch"
    assert decision.context_seed.get("change_intent", {}).get("source") == "llm"
    assert decision.context_seed.get("impact_set", {}).get("restart_from") == "AgentGenerator"


@pytest.mark.asyncio
async def test_route_trigger_refinement_classifier_uses_authoritative_llm_result(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    resolver = get_refinement_trigger_route_resolver()
    monkeypatch.setattr(
        resolver,
        "_classifier",
        _FakeChangeClassifier(
            change_class="core",
            rationale="The request changes the product identity and must reopen ValueEngine.",
            signals=["concept_shift", "business_model_change"],
        ),
    )
    router = SessionRouter(
        persistence=persistence,
        store=store,
        trigger_route_resolver=resolver,
    )
    pack = _make_pack()
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    decision = await router.route_trigger(
        TriggerInput(
            app_id="app_1",
            user_id="user_1",
            trigger_source="refinement",
            workflow_id=None,
            trigger_payload={
                "refinement_request": {
                    "declared_change_class": "patch",
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "v9",
                    "raw_user_request": "Actually pivot this into a blockchain marketplace instead of an internal ops tool.",
                }
            },
        )
    )

    assert decision.requested_workflow_id == "ValueEngine"
    assert decision.workflow_id == "ValueEngine"
    assert decision.is_full_restart is True
    assert decision.lifecycle_state == _session_model.SessionLifecycle.STALE
    assert decision.context_seed.get("change_class") == "core"
    assert decision.context_seed.get("change_intent", {}).get("source") == "llm"
    assert "concept_shift" in decision.context_seed.get("change_intent", {}).get("signals", [])


@pytest.mark.asyncio
async def test_orchestration_control_harness_delegates_refinement_into_session_router(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    harness = get_orchestration_control_harness()
    monkeypatch.setattr(
        harness._refinement_resolver,
        "_classifier",
        _FakeChangeClassifier(
            change_class="feature",
            rationale="Adding a workflow stays within the current concept.",
            signals=["workflow_expansion", "new_capability"],
        ),
    )
    router = SessionRouter(
        persistence=persistence,
        store=store,
        trigger_route_resolver=harness,
    )
    pack = _make_pack()
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    decision = await router.route_trigger(
        TriggerInput(
            app_id="app_1",
            user_id="user_1",
            trigger_source="refinement",
            workflow_id=None,
            trigger_payload={
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "v11",
                    "raw_user_request": "Add a workflow that handles premium escalations.",
                }
            },
        )
    )

    assert decision.requested_workflow_id == "AppGenerator"
    assert decision.context_seed.get("change_class") == "feature"
    assert decision.context_seed.get("change_intent", {}).get("source") == "llm"


@pytest.mark.asyncio
async def test_session_store_normalizes_legacy_refining_state_to_active():
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    session_coll = await persistence._coll("SessionRouterState")
    session_coll._docs["session_router::app_1::user_1"] = {
        "_id": "session_router::app_1::user_1",
        "app_id": "app_1",
        "user_id": "user_1",
        "lifecycle_state": "refining",
    }

    state = await store.load(app_id="app_1", user_id="user_1")

    assert state is not None
    assert state.lifecycle_state == _session_model.SessionLifecycle.ACTIVE


@pytest.mark.asyncio
async def test_resolve_transition_persists_pending_transition(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "AppGenerator"}],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "user_choice",
                    "ui": {"component": "LauncherScreen", "mode": "screen"},
                    "options": [
                        {"id": "continue", "route_to": "details"},
                    ],
                },
                {
                    "id": "details",
                    "transition_type": "confirm",
                    "ui": {"component": "ConfirmScreen", "mode": "screen"},
                    "confirm_route": "AppGenerator",
                },
            ],
            "workflow_sequences": [{"id": "build", "steps": [{"workflows": ["ValueEngine"]}, {"workflows": ["AppGenerator"]}]}],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    resolution = await router.resolve_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="entry",
        option_id="continue",
        context_seed={"app_type": "greenfield_app"},
    )

    assert resolution.resolution_type == "transition"
    assert resolution.target_id == "details"
    assert resolution.route_type == "transition"
    assert resolution.option_id == "continue"

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.lifecycle_state == _session_model.SessionLifecycle.AWAITING_TRANSITION
    assert state.pending_transition_id == "details"
    assert state.journey_key is None
    assert state.journey_total_steps == 0


@pytest.mark.asyncio
async def test_resolve_transition_routes_to_workflow_and_binds_session(monkeypatch):
    persistence = _FakePersistence()
    sessions = await persistence._coll()
    sessions._docs["c1"] = {
        "_id": "c1",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "ValueEngine",
        "status": int(WorkflowStatus.COMPLETED),
    }
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {"id": "ValueEngine"},
                {"id": "DesignDocs", "dependencies": ["ValueEngine"]},
            ],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "user_choice",
                    "ui": {"component": "LauncherScreen", "mode": "screen"},
                    "options": [
                        {"id": "docs", "route_to": "DesignDocs"},
                    ],
                }
            ],
            "workflow_sequences": [{"id": "build", "steps": [{"workflows": ["ValueEngine"]}, {"workflows": ["DesignDocs"]}]}],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    resolution = await router.resolve_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="entry",
        option_id="docs",
        context_seed={"app_type": "greenfield_app"},
    )

    assert resolution.resolution_type == "workflow"
    assert resolution.route_type == "workflow"
    assert resolution.option_id == "docs"
    assert resolution.routing_decision is not None
    assert resolution.routing_decision.workflow_id == "DesignDocs"

    await router.bind_workflow_session(
        app_id="app_1",
        user_id="user_1",
        workflow_id="DesignDocs",
        chat_id="chat_design_1",
        journey_id="build",
    )

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.lifecycle_state == _session_model.SessionLifecycle.ACTIVE
    assert state.current_workflow_id == "DesignDocs"
    assert state.current_chat_id == "chat_design_1"
    assert state.pending_transition_id is None


@pytest.mark.asyncio
async def test_resolve_transition_option_sequence_override_rebinds_journey(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {"id": "ValueEngine"},
                {"id": "ExistingAppDiscovery"},
            ],
            "transitions": [
                {
                    "id": "app_type_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "AppTypeSelector", "mode": "screen"},
                    "options": [
                        {
                            "id": "greenfield_app",
                            "route_to": "ValueEngine",
                            "sequence": "build",
                            "context_variables": {"app_type": "greenfield_app"},
                        },
                        {
                            "id": "brownfield_app",
                            "route_to": "ExistingAppDiscovery",
                            "sequence": "brownfield_app_adoption",
                            "context_variables": {"app_type": "brownfield_app"},
                        },
                    ],
                }
            ],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"transition": "app_type_selector"},
                        {"workflows": ["ValueEngine"]},
                    ],
                },
                {
                    "id": "brownfield_app_adoption",
                    "steps": [
                        {"transition": "app_type_selector"},
                        {"workflows": ["ExistingAppDiscovery"]},
                    ],
                },
            ],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    resolution = await router.resolve_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="app_type_selector",
        option_id="brownfield_app",
        journey_id="build",
        context_seed={},
    )

    assert resolution.route_type == "workflow"
    assert resolution.routing_decision is not None
    assert resolution.routing_decision.workflow_id == "ExistingAppDiscovery"
    assert resolution.routing_decision.journey_id == "brownfield_app_adoption"

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.journey_key == "brownfield_app_adoption"
    assert state.journey_position == 1


@pytest.mark.asyncio
async def test_bind_workflow_session_rejects_explicit_journey_mismatch(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "ExistingAppDiscovery"}],
            "transitions": [],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [{"workflows": ["ValueEngine"]}],
                }
            ],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    with pytest.raises(ValueError, match="not part of journey"):
        await router.bind_workflow_session(
            app_id="app_1",
            user_id="user_1",
            workflow_id="ExistingAppDiscovery",
            chat_id="chat_existing_1",
            journey_id="build",
        )


@pytest.mark.asyncio
async def test_resolve_transition_merges_option_context_variables(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "LauncherScreen", "mode": "screen"},
                    "options": [
                        {"id": "brownfield_app", "route_to": "ValueEngine", "context_variables": {"app_type": "brownfield_app"}},
                    ],
                }
            ],
            "workflow_sequences": [],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    resolution = await router.resolve_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="entry",
        option_id="brownfield_app",
        context_seed={"source": "route"},
    )

    assert resolution.resolution_type == "workflow"
    assert resolution.target_id == "ValueEngine"
    assert resolution.context_seed == {"source": "route", "app_type": "brownfield_app"}


@pytest.mark.asyncio
async def test_resolve_transition_rejects_invalid_route(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "user_choice",
                    "ui": {"component": "LauncherScreen", "mode": "screen"},
                    "options": [{"id": "go", "route_to": "ValueEngine"}],
                }
            ],
            "workflow_sequences": [],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    with pytest.raises(ValueError):
        await router.resolve_transition(
            app_id="app_1",
            user_id="user_1",
            transition_id="entry",
            option_id="missing",
        )


@pytest.mark.asyncio
async def test_resolve_condition_transition_uses_context_key(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "AppGenerator"}, {"id": "DesignDocs"}],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "condition",
                    "context_key": "app_type",
                    "routes": [
                        {
                            "match": "brownfield_app",
                            "route_to": "DesignDocs",
                        },
                        {
                            "match": "greenfield_app",
                            "route_to": "AppGenerator",
                        },
                    ],
                    "default_route": "AppGenerator",
                }
            ],
            "workflow_sequences": [],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    resolution = await router.resolve_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="entry",
        context_seed={"app_type": "brownfield_app"},
    )

    assert resolution.resolution_type == "workflow"
    assert resolution.target_id == "DesignDocs"
    assert resolution.context_seed["app_type"] == "brownfield_app"


@pytest.mark.asyncio
async def test_resolve_silent_transition_without_route_to(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "silent",
                    "route_to": "details",
                },
                {
                    "id": "details",
                    "transition_type": "confirm",
                    "ui": {"component": "ConfirmScreen", "mode": "screen"},
                    "confirm_route": "ValueEngine",
                },
            ],
            "workflow_sequences": [],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    resolution = await router.resolve_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="entry",
        context_seed={"app_type": "greenfield_app"},
    )

    assert resolution.resolution_type == "transition"
    assert resolution.target_id == "details"
    assert resolution.context_seed["app_type"] == "greenfield_app"

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.lifecycle_state == _session_model.SessionLifecycle.AWAITING_TRANSITION
    assert state.pending_transition_id == "details"


@pytest.mark.asyncio
async def test_bind_workflow_session_infers_journey_and_stamps_chat_doc(monkeypatch):
    persistence = _FakePersistence()
    sessions = await persistence._coll()
    sessions._docs["chat_value_1"] = {
        "_id": "chat_value_1",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "ValueEngine",
        "status": int(WorkflowStatus.IN_PROGRESS),
    }
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "DesignDocs"}],
            "transitions": [],
            "workflow_sequences": [{"id": "build", "steps": [{"workflows": ["ValueEngine"]}, {"workflows": ["DesignDocs"]}]}],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    await router.bind_workflow_session(
        app_id="app_1",
        user_id="user_1",
        workflow_id="ValueEngine",
        chat_id="chat_value_1",
    )

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.journey_key == "build"
    assert state.journey_position == 0
    assert state.journey_total_steps == 2
    assert state.journey_instance_id is not None

    chat_doc = sessions._docs["chat_value_1"]
    assert chat_doc["session_router_session_id"] == SessionStateStore.session_id_for_scope("app_1", "user_1")
    assert chat_doc["journey_key"] == "build"
    assert chat_doc["journey_position"] == 0
    assert chat_doc["journey_instance_id"] == state.journey_instance_id


@pytest.mark.asyncio
async def test_bind_workflow_session_infers_position_after_entry_transition(monkeypatch):
    persistence = _FakePersistence()
    sessions = await persistence._coll()
    sessions._docs["chat_value_1"] = {
        "_id": "chat_value_1",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "ValueEngine",
        "status": int(WorkflowStatus.IN_PROGRESS),
    }
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "DesignDocs"}],
            "transitions": [
                {
                    "id": "app_type_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "AppTypeSelector", "mode": "screen"},
                    "options": [{"id": "greenfield_app", "route_to": "ValueEngine", "context_variables": {"app_type": "greenfield_app"}}],
                }
            ],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"transition": "app_type_selector"},
                        {"workflows": ["ValueEngine"]},
                        {"workflows": ["DesignDocs"]},
                    ],
                }
            ],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    await router.bind_workflow_session(
        app_id="app_1",
        user_id="user_1",
        workflow_id="ValueEngine",
        chat_id="chat_value_1",
    )

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.journey_key == "build"
    assert state.journey_position == 1

    chat_doc = sessions._docs["chat_value_1"]
    assert chat_doc["journey_position"] == 1


@pytest.mark.asyncio
async def test_advance_journey_after_run_complete_waits_for_parallel_group(monkeypatch):
    persistence = _FakePersistence()
    sessions = await persistence._coll()
    sessions._docs["chat_value"] = {
        "_id": "chat_value",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "ValueEngine",
        "status": int(WorkflowStatus.COMPLETED),
    }
    sessions._docs["chat_design"] = {
        "_id": "chat_design",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "DesignDocs",
        "status": int(WorkflowStatus.IN_PROGRESS),
    }
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "DesignDocs"}, {"id": "AgentGenerator"}],
            "transitions": [],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"workflows": ["ValueEngine", "DesignDocs"]},
                        {"workflows": ["AgentGenerator"]},
                    ],
                }
            ],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    await router.bind_workflow_session(
        app_id="app_1",
        user_id="user_1",
        workflow_id="ValueEngine",
        chat_id="chat_value",
        journey_id="build",
        journey_position=0,
    )
    await router.annotate_workflow_chat(
        app_id="app_1",
        user_id="user_1",
        workflow_id="DesignDocs",
        chat_id="chat_design",
        journey_id="build",
        journey_position=0,
    )

    blocked = await router.advance_journey_after_run_complete(
        app_id="app_1",
        user_id="user_1",
        workflow_id="ValueEngine",
        chat_id="chat_value",
    )
    assert blocked is None

    sessions._docs["chat_design"]["status"] = int(WorkflowStatus.COMPLETED)
    advanced = await router.advance_journey_after_run_complete(
        app_id="app_1",
        user_id="user_1",
        workflow_id="DesignDocs",
        chat_id="chat_design",
    )

    assert advanced is not None
    assert advanced.completed is False
    assert advanced.journey_key == "build"
    assert advanced.next_group_index == 1
    assert advanced.next_workflows == ["AgentGenerator"]

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.journey_position == 1
    assert state.current_chat_id is None
    assert state.lifecycle_state == _session_model.SessionLifecycle.ACTIVE


@pytest.mark.asyncio
async def test_advance_journey_after_run_complete_can_pause_on_transition(monkeypatch):
    persistence = _FakePersistence()
    sessions = await persistence._coll()
    sessions._docs["chat_theme"] = {
        "_id": "chat_theme",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "ThemeCapture",
        "status": int(WorkflowStatus.COMPLETED),
    }
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ThemeCapture"}, {"id": "DesignDocs"}],
            "transitions": [
                {
                    "id": "coding_journey_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "CodingJourneySelector", "mode": "screen"},
                    "options": [{"id": "guided", "route_to": "DesignDocs", "context_variables": {"design_docs_hitl": True}}],
                }
            ],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"workflows": ["ThemeCapture"]},
                        {"transition": "coding_journey_selector"},
                        {"workflows": ["DesignDocs"]},
                    ],
                }
            ],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    await router.bind_workflow_session(
        app_id="app_1",
        user_id="user_1",
        workflow_id="ThemeCapture",
        chat_id="chat_theme",
        journey_id="build",
        journey_position=0,
    )

    advanced = await router.advance_journey_after_run_complete(
        app_id="app_1",
        user_id="user_1",
        workflow_id="ThemeCapture",
        chat_id="chat_theme",
    )

    assert advanced is not None
    assert advanced.next_transition_id == "coding_journey_selector"
    assert advanced.next_workflows == []

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.lifecycle_state == _session_model.SessionLifecycle.AWAITING_TRANSITION
    assert state.pending_transition_id == "coding_journey_selector"
    assert state.journey_position == 1


@pytest.mark.asyncio
async def test_resolve_resume_prefers_session_state_chat(monkeypatch):
    persistence = _FakePersistence()
    sessions = await persistence._coll()
    sessions._docs["chat_agent_1"] = {
        "_id": "chat_agent_1",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "AgentGenerator",
        "status": int(WorkflowStatus.IN_PROGRESS),
        "journey_key": "build",
        "journey_instance_id": "journey_run_1",
        "journey_position": 2,
        "journey_total_steps": 3,
    }
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "AgentGenerator"}],
            "transitions": [],
            "workflow_sequences": [{"id": "build", "steps": [{"workflows": ["AgentGenerator"]}]}],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    await router.bind_workflow_session(
        app_id="app_1",
        user_id="user_1",
        workflow_id="AgentGenerator",
        chat_id="chat_agent_1",
        journey_id="build",
        journey_position=2,
    )

    resolution = await router.resolve_resume(
        app_id="app_1",
        user_id="user_1",
        requested_workflow_id="AgentGenerator",
    )

    assert resolution["found"] is True
    assert resolution["resolved_from"] == "session_state"
    assert resolution["chat_id"] == "chat_agent_1"
    assert resolution["session_state"]["current_chat_id"] == "chat_agent_1"
    assert resolution["session_state"]["journey_position"] == 2
    assert resolution["session_state"]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_mark_and_resolve_approval_updates_session_state(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: _make_pack())

    awaiting = await router.mark_awaiting_approval(
        app_id="app_1",
        user_id="user_1",
        approval_id="approve_123",
        workflow_id="DesignDocs",
        chat_id="chat_design_1",
    )

    assert awaiting["lifecycle_state"] == "awaiting_approval"
    assert awaiting["pending_approval_id"] == "approve_123"
    assert awaiting["current_chat_id"] == "chat_design_1"

    resolved = await router.resolve_approval(
        app_id="app_1",
        user_id="user_1",
        approval_id="approve_123",
        approved=True,
    )

    assert resolved["approved"] is True
    assert resolved["lifecycle_state"] == "active"
    assert resolved["pending_approval_id"] is None
    assert "approved" in (resolved["last_route_explanation"] or "")
