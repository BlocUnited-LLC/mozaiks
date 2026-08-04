from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.import_utils import import_module_directly

_schema = import_module_directly("mozaiksai.core.workflow.pack.schema")
_session_model = import_module_directly("mozaiksai.core.session.model")
_session_persist = import_module_directly("mozaiksai.core.session.persistence")
_session_router = import_module_directly("mozaiksai.core.session.router")
_session_launcher = import_module_directly("mozaiksai.core.session.launcher")
_data_models = import_module_directly("mozaiksai.core.data.models")
_workflow_manager = import_module_directly("mozaiksai.core.workflow.workflow_manager")

parse_global_pack_graph = _schema.parse_global_pack_graph
SessionRouter = _session_router.SessionRouter
SessionStateStore = _session_persist.SessionStateStore
WorkflowStatus = _data_models.WorkflowStatus


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

    async def insert_one(self, doc):  # noqa: ANN001
        self._docs[doc["_id"]] = dict(doc)


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


class _ChatSessionPersistenceAdapter:
    def __init__(self, persistence: _FakePersistence) -> None:
        self._persistence = persistence

    async def create_chat_session(
        self,
        chat_id: str,
        app_id: str | None = None,
        workflow_name: str = "",
        user_id: str = "",
        *,
        extra_fields=None,
    ) -> None:
        coll = await self._persistence._coll()
        now = datetime.now(UTC)
        doc = {
            "_id": chat_id,
            "chat_id": chat_id,
            "app_id": app_id,
            "workflow_name": workflow_name,
            "user_id": user_id,
            "status": int(WorkflowStatus.IN_PROGRESS),
            "created_at": now,
            "last_updated_at": now,
            "last_sequence": 0,
            "last_artifact": None,
            "messages": [],
        }
        if isinstance(extra_fields, dict):
            doc.update(extra_fields)
        coll._docs[chat_id] = doc


def test_validate_context_for_workflow_keeps_refinement_launch_keys_for_appgenerator() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager.UnifiedWorkflowManager._instance = None
    _workflow_manager.initialize_workflows(base_path=str(workflows_root))

    validated = _session_launcher.validate_context_for_workflow(
        "AppGenerator",
        {
            "artifact_kind": "app_bundle",
            "artifact_version_id": "av_123",
            "refinement_request_meta": {"artifact_key": "app_bundle"},
            "screen": "studio-create",
        },
    )

    assert validated == {
        "artifact_kind": "app_bundle",
        "artifact_version_id": "av_123",
        "refinement_request_meta": {"artifact_key": "app_bundle"},
        "screen": "studio-create",
    }


@pytest.mark.asyncio
async def test_apply_launch_context_provider_invokes_env_provider(monkeypatch) -> None:
    provider_module = types.ModuleType("_test_launch_context_provider")

    def _merge(  # noqa: ANN001
        context_variables,
        *,
        workflow_id,
        journey_id,
        trigger_source,
        trigger_payload,
        **_,
    ):
        return {
            **context_variables,
            "provider_workflow": workflow_id,
            "provider_journey": journey_id,
            "provider_trigger_source": trigger_source,
            "provider_payload": trigger_payload,
        }

    provider_module.merge = _merge
    monkeypatch.setitem(sys.modules, "_test_launch_context_provider", provider_module)
    monkeypatch.setenv(
        "MOZAIKS_LAUNCH_CONTEXT_PROVIDER",
        "_test_launch_context_provider:merge",
    )

    provided = await _session_launcher.apply_launch_context_provider(
        workflow_id="AppGenerator",
        context_variables={"concept_overview": "paid marketplace"},
        app_id="app_1",
        user_id="user_1",
        journey_id="build",
        trigger_source="transition",
        trigger_payload={"source": "ui"},
    )

    assert provided == {
        "concept_overview": "paid marketplace",
        "provider_workflow": "AppGenerator",
        "provider_journey": "build",
        "provider_trigger_source": "transition",
        "provider_payload": {"source": "ui"},
    }


@pytest.mark.asyncio
async def test_prepare_routed_workflow_launch_applies_context_provider_before_validation(
    monkeypatch,
) -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager.UnifiedWorkflowManager._instance = None
    _workflow_manager.initialize_workflows(base_path=str(workflows_root))

    provider_module = types.ModuleType("_test_appgenerator_context_provider")

    def _merge(  # noqa: ANN001
        context_variables,
        *,
        workflow_id,
        journey_id,
        trigger_source,
        trigger_payload,
        **_,
    ):
        assert workflow_id == "AppGenerator"
        return {
            **context_variables,
            "capability_packs": [{"id": "paid_downloads"}],
            "provider_backed_capabilities": [
                {"intent_id": "monetization", "pack_id": "paid_downloads"}
            ],
            "not_declared_for_appgenerator": "drop me",
        }

    provider_module.merge = _merge
    monkeypatch.setitem(sys.modules, "_test_appgenerator_context_provider", provider_module)
    monkeypatch.setenv(
        "MOZAIKS_LAUNCH_CONTEXT_PROVIDER",
        "_test_appgenerator_context_provider:merge",
    )

    class _Router:
        async def route_trigger(self, trigger):  # noqa: ANN001
            return _session_model.RoutingDecision(
                workflow_id="AppGenerator",
                requested_workflow_id=trigger.workflow_id,
                journey_id=trigger.journey_id,
            )

    launch = await _session_launcher.prepare_routed_workflow_launch(
        workflow_id="AppGenerator",
        app_id="app_1",
        user_id="user_1",
        trigger_source="transition",
        context_variables={"concept_overview": "paid downloads"},
        trigger_payload={"source": "ui"},
        journey_id="build",
        session_router=_Router(),
    )

    assert launch.validated_context["concept_overview"] == "paid downloads"
    assert launch.validated_context["capability_packs"] == [{"id": "paid_downloads"}]
    assert launch.validated_context["provider_backed_capabilities"] == [
        {"intent_id": "monetization", "pack_id": "paid_downloads"}
    ]
    assert "not_declared_for_appgenerator" not in launch.validated_context


@pytest.mark.parametrize(
    ("workflow_id", "context", "expected"),
    [
        (
            "AgentGenerator",
            {
                "build_mode": "revision",
                "revision_scope": "patch",
                "change_request_id": "cr_patch_1",
                "revision_id": "rev_patch_1",
                "artifact_kind": "workflow_bundle",
                "artifact_version_id": "wv_123",
                "sequence_status": "completed",
                "revision_origin_workflow": "AppGenerator",
                "refinement_request": "Rename the workflow title",
                "refinement_request_meta": {"artifact_key": "workflow_bundle"},
                "screen": "studio-create",
                "change_intent": {"change_class": "patch"},
                "impact_set": {"restart_from": "AgentGenerator"},
            },
            {
                "build_mode": "revision",
                "revision_scope": "patch",
                "change_request_id": "cr_patch_1",
                "revision_id": "rev_patch_1",
                "artifact_kind": "workflow_bundle",
                "artifact_version_id": "wv_123",
                "sequence_status": "completed",
                "revision_origin_workflow": "AppGenerator",
                "refinement_request": "Rename the workflow title",
                "refinement_request_meta": {"artifact_key": "workflow_bundle"},
                "screen": "studio-create",
                "change_intent": {"change_class": "patch"},
                "impact_set": {"restart_from": "AgentGenerator"},
            },
        ),
        (
            "DesignDocs",
            {
                "build_mode": "revision",
                "revision_scope": "design",
                "change_request_id": "cr_design_1",
                "revision_id": "rev_design_1",
                "artifact_kind": "app_bundle",
                "artifact_version_id": "av_456",
                "sequence_status": "in_progress",
                "revision_origin_workflow": "AppGenerator",
                "refinement_request": "Refresh the dashboard information architecture",
                "refinement_request_meta": {"artifact_key": "app_bundle"},
                "screen": "studio-create",
                "change_intent": {"change_class": "design"},
                "impact_set": {"restart_from": "DesignDocs"},
            },
            {
                "build_mode": "revision",
                "revision_scope": "design",
                "change_request_id": "cr_design_1",
                "revision_id": "rev_design_1",
                "artifact_kind": "app_bundle",
                "artifact_version_id": "av_456",
                "sequence_status": "in_progress",
                "revision_origin_workflow": "AppGenerator",
                "refinement_request": "Refresh the dashboard information architecture",
                "refinement_request_meta": {"artifact_key": "app_bundle"},
                "screen": "studio-create",
                "change_intent": {"change_class": "design"},
                "impact_set": {"restart_from": "DesignDocs"},
            },
        ),
        (
            "ValueEngine",
            {
                "build_mode": "revision",
                "revision_scope": "core",
                "change_request_id": "cr_core_1",
                "revision_id": "rev_core_1",
                "artifact_kind": "app_bundle",
                "artifact_version_id": "av_789",
                "sequence_status": "completed",
                "revision_origin_workflow": "AppGenerator",
                "refinement_request": "Turn this into a blockchain marketplace",
                "refinement_request_meta": {"artifact_key": "app_bundle"},
                "screen": "studio-create",
                "change_intent": {"change_class": "core"},
                "impact_set": {"restart_from": "ValueEngine"},
            },
            {
                "build_mode": "revision",
                "revision_scope": "core",
                "change_request_id": "cr_core_1",
                "revision_id": "rev_core_1",
                "artifact_kind": "app_bundle",
                "artifact_version_id": "av_789",
                "sequence_status": "completed",
                "revision_origin_workflow": "AppGenerator",
                "refinement_request": "Turn this into a blockchain marketplace",
                "refinement_request_meta": {"artifact_key": "app_bundle"},
                "screen": "studio-create",
                "change_intent": {"change_class": "core"},
                "impact_set": {"restart_from": "ValueEngine"},
            },
        ),
    ],
)
def test_validate_context_for_workflow_keeps_refinement_launch_keys_for_builder_workflows(
    workflow_id: str,
    context: dict,
    expected: dict,
) -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager.UnifiedWorkflowManager._instance = None
    _workflow_manager.initialize_workflows(base_path=str(workflows_root))

    validated = _session_launcher.validate_context_for_workflow(workflow_id, context)

    assert validated == expected


@pytest.mark.parametrize(
    "workflow_id",
    ["ValueEngine", "ThemeCapture", "DesignDocs", "AgentGenerator", "AppGenerator"],
)
def test_validate_context_for_workflow_keeps_builder_options_for_build_sequence(
    workflow_id: str,
) -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager.UnifiedWorkflowManager._instance = None
    _workflow_manager.initialize_workflows(base_path=str(workflows_root))

    builder_options = {
        "provider_backed_capabilities": [
            {"intent_id": "monetization", "surfaces": ["checkout", "billing"]}
        ]
    }
    validated = _session_launcher.validate_context_for_workflow(
        workflow_id,
        {
            "builder_options": builder_options,
            "not_a_declared_context_key": "drop me",
        },
    )

    assert validated == {"builder_options": builder_options}


@pytest.mark.asyncio
async def test_launch_routed_workflow_creates_chat_and_binds_session(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "DesignDocs"}],
            "transitions": [],
            "workflow_sequences": [
                {"id": "build", "steps": [{"workflows": ["ValueEngine"]}, {"workflows": ["DesignDocs"]}]}
            ],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)
    monkeypatch.setattr(_session_launcher, "_PERSISTENCE_MANAGER", _ChatSessionPersistenceAdapter(persistence))

    launch = await _session_launcher.launch_routed_workflow(
        workflow_id="ValueEngine",
        app_id="app_1",
        user_id="user_1",
        context_variables={"unused": "value"},
        session_router=router,
    )

    sessions = await persistence._coll()
    assert launch.workflow_id == "ValueEngine"
    assert launch.chat_id in sessions._docs
    assert sessions._docs[launch.chat_id]["workflow_name"] == "ValueEngine"

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.current_chat_id == launch.chat_id
    assert state.current_workflow_id == "ValueEngine"
    assert state.journey_key == "build"


@pytest.mark.asyncio
async def test_launch_routed_workflow_binds_brownfield_app_adoption_journey(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "ExistingAppDiscovery"}],
            "transitions": [
                {
                    "id": "app_type_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "AppTypeSelector", "mode": "screen"},
                    "options": [
                        {"id": "greenfield_app", "route_to": "ValueEngine", "context_variables": {"app_type": "greenfield_app"}},
                        {
                            "id": "brownfield_app",
                            "route_to": "brownfield_path_selector",
                            "context_variables": {"app_type": "brownfield_app"},
                        },
                    ],
                },
                {
                    "id": "brownfield_path_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "BrownfieldPathSelector", "mode": "screen"},
                    "options": [
                        {
                            "id": "light_integration",
                            "route_to": "brownfield_repo_input",
                            "context_variables": {"brownfield_build_path": "light_integration"},
                        },
                        {
                            "id": "full_migration",
                            "route_to": "brownfield_repo_input",
                            "context_variables": {"brownfield_build_path": "full_migration"},
                        },
                    ],
                },
                {
                    "id": "brownfield_repo_input",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "BrownfieldRepoInput", "mode": "screen"},
                    "options": [
                        {
                            "id": "start_discovery",
                            "route_to": "ExistingAppDiscovery",
                            "context_variables": {"github_repo": "BlocUnited-LLC/mozaiks-app"},
                        },
                    ],
                },
            ],
            "workflow_sequences": [
                {"id": "build", "steps": [{"transition": "app_type_selector"}, {"workflows": ["ValueEngine"]}]},
                {
                    "id": "brownfield_app_adoption",
                    "steps": [
                        {"transition": "app_type_selector"},
                        {"transition": "brownfield_path_selector"},
                        {"transition": "brownfield_repo_input"},
                        {"workflows": ["ExistingAppDiscovery"]},
                    ],
                },
            ],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)
    monkeypatch.setattr(_session_launcher, "_PERSISTENCE_MANAGER", _ChatSessionPersistenceAdapter(persistence))

    launch = await _session_launcher.launch_routed_workflow(
        workflow_id="ExistingAppDiscovery",
        app_id="app_1",
        user_id="user_1",
        context_variables={
            "app_type": "brownfield_app",
            "brownfield_build_path": "light_integration",
            "github_repo": "BlocUnited-LLC/mozaiks-app",
        },
        journey_id="brownfield_app_adoption",
        session_router=router,
    )

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.current_chat_id == launch.chat_id
    assert state.current_workflow_id == "ExistingAppDiscovery"
    assert state.journey_key == "brownfield_app_adoption"
    assert state.journey_position == 3
    assert state.journey_total_steps == 4


@pytest.mark.asyncio
async def test_launch_transition_returns_next_transition_payload(monkeypatch):
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "DesignDocs"}],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "user_choice",
                    "ui": {"component": "LauncherScreen", "mode": "screen"},
                    "options": [{"id": "continue", "route_to": "details"}],
                },
                {
                    "id": "details",
                    "transition_type": "confirm",
                    "ui": {"component": "ConfirmScreen", "mode": "screen"},
                    "confirm_route": "DesignDocs",
                },
            ],
            "workflow_sequences": [],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)
    monkeypatch.setattr(_session_launcher, "load_global_pack_graph", lambda: pack)

    launch = await _session_launcher.launch_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="entry",
        option_id="continue",
        context_variables={"source": "route"},
        session_router=router,
    )

    assert launch.resolution_type == "transition"
    assert launch.next_transition_id == "details"
    assert launch.transition is not None
    assert launch.transition.id == "details"
    assert launch.context_variables == {"source": "route"}


@pytest.mark.asyncio
async def test_launch_transition_starts_workflow_chat(monkeypatch):
    persistence = _FakePersistence()
    sessions = await persistence._coll()
    sessions._docs["value_complete"] = {
        "_id": "value_complete",
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
                        {
                            "id": "docs",
                            "route_to": "DesignDocs",
                            "context_variables": {"brownfield_build_path": "light_integration"},
                        }
                    ],
                }
            ],
            "workflow_sequences": [
                {"id": "build", "steps": [{"workflows": ["ValueEngine"]}, {"workflows": ["DesignDocs"]}]}
            ],
        }
    )
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)
    monkeypatch.setattr(_session_launcher, "_PERSISTENCE_MANAGER", _ChatSessionPersistenceAdapter(persistence))

    launch = await _session_launcher.launch_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="entry",
        option_id="docs",
        session_router=router,
    )

    assert launch.resolution_type == "workflow"
    assert launch.workflow_launch is not None
    assert launch.workflow_launch.workflow_id == "DesignDocs"
    assert launch.workflow_launch.chat_id in sessions._docs
    assert launch.context_variables["brownfield_build_path"] == "light_integration"
    assert sessions._docs[launch.workflow_launch.chat_id]["brownfield_build_path"] == "light_integration"

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state is not None
    assert state.current_chat_id == launch.workflow_launch.chat_id
    assert state.current_workflow_id == "DesignDocs"


@pytest.mark.asyncio
async def test_emit_workflow_launch_navigation_sends_chat_navigate_envelope(monkeypatch):
    captured = []

    class _FakeTransport:
        async def send_event_to_ui(self, event, chat_id=None):  # noqa: ANN001
            captured.append((event, chat_id))

    class _FakeTransportFactory:
        @classmethod
        async def get_instance(cls):
            return _FakeTransport()

    monkeypatch.setitem(
        sys.modules,
        "mozaiksai.core.transport.simple_transport",
        SimpleNamespace(SimpleTransport=_FakeTransportFactory),
    )

    launched = _session_launcher.WorkflowLaunchResult(
        chat_id="chat_launched",
        app_id="app_1",
        user_id="user_1",
        workflow_id="ValueEngine",
        requested_workflow_id="ValueEngine",
        websocket_url="/ws/ValueEngine/app_1/chat_launched/user_1",
        trigger_source="transition",
        routing_explanation="ok",
        rerouted_by_dependency=False,
        validated_context={},
        trigger_meta={},
        journey_id=None,
        routing_decision=SimpleNamespace(explanation="ok", rerouted_by_dependency=False),
    )

    sent = await _session_launcher.emit_workflow_launch_navigation(
        source_chat_id="chat_source",
        workflow_launch=launched,
    )

    assert sent is True
    assert captured == [
        (
            {
                "type": "chat.navigate",
                "data": {
                    "chat_id": "chat_launched",
                    "workflow_name": "ValueEngine",
                    "requested_workflow_name": "ValueEngine",
                    "app_id": "app_1",
                    "websocket_url": "/ws/ValueEngine/app_1/chat_launched/user_1",
                },
                "timestamp": captured[0][0]["timestamp"],
            },
            "chat_source",
        )
    ]


# ---------------------------------------------------------------------------
# validate_context_for_workflow — size limit tests
# ---------------------------------------------------------------------------

def test_validate_context_too_many_keys_truncates_at_limit() -> None:
    """context_variables with more than _CONTEXT_MAX_KEYS entries are silently capped."""
    limit = _session_launcher._CONTEXT_MAX_KEYS
    # All keys are undeclared so they pass the declared-key filter (declared_keys is empty when
    # the workflow isn't found). Use a non-existent workflow so no key filter applies.
    context = {f"key_{i}": "value" for i in range(limit + 10)}
    validated = _session_launcher.validate_context_for_workflow("unknown_wf", context)
    assert len(validated) <= limit


def test_validate_context_large_string_value_is_dropped() -> None:
    """Values exceeding _CONTEXT_MAX_VALUE_BYTES are silently dropped."""
    max_bytes = _session_launcher._CONTEXT_MAX_VALUE_BYTES
    oversized = "x" * (max_bytes + 1)
    context = {"small": "ok", "big": oversized}
    validated = _session_launcher.validate_context_for_workflow("unknown_wf", context)
    assert "small" in validated
    assert "big" not in validated


def test_validate_context_value_at_exact_limit_is_accepted() -> None:
    """Values exactly at the byte limit are accepted (boundary condition)."""
    max_bytes = _session_launcher._CONTEXT_MAX_VALUE_BYTES
    at_limit = "x" * max_bytes
    context = {"key": at_limit}
    validated = _session_launcher.validate_context_for_workflow("unknown_wf", context)
    assert "key" in validated

