"""Regression tests for the generated-app identity migration (PR #507 follow-up).

The runtime rejects lifecycle changes to execution identity
(`require_unchanged_runtime_authority`), so the factory build pipeline must
carry the generated app's identity in the declared ``generated_app_id``
context variable instead of rebinding ``app_id`` — and that identity must
survive mid-journey transition hops the same way workflow-to-workflow journey
hops inherit context.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.import_utils import import_module_directly

_schema = import_module_directly("mozaiksai.core.workflow.pack.schema")
_session_model = import_module_directly("mozaiksai.core.session.model")
_session_persist = import_module_directly("mozaiksai.core.session.persistence")
_session_router = import_module_directly("mozaiksai.core.session.router")
_data_models = import_module_directly("mozaiksai.core.data.models")

parse_global_pack_graph = _schema.parse_global_pack_graph
SessionRouter = _session_router.SessionRouter
SessionStateStore = _session_persist.SessionStateStore
WorkflowStatus = _data_models.WorkflowStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_WORKFLOWS = [
    "ValueEngine",
    "ThemeCapture",
    "DesignDocs",
    "SubscriptionContractDesigner",
    "AgentGenerator",
    "AppGenerator",
    "SecurityReadiness",
    "AppReview",
]


def _load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


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


def _journey_pack():
    return parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "AppGenerator"}],
            "transitions": [
                {
                    "id": "mode_selector",
                    "transition_type": "user_choice",
                    "ui": {"component": "LauncherScreen", "mode": "screen"},
                    "options": [{"id": "go", "route_to": "AppGenerator"}],
                }
            ],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"workflows": ["ValueEngine"]},
                        {"transition": "mode_selector"},
                        {"workflows": ["AppGenerator"]},
                    ],
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_journey_transition_hop_carries_scalar_context(monkeypatch):
    """Identity/mode context survives a mid-journey transition hop.

    advance -> stores scalar carry context on the session state;
    resolve_transition -> merges it under caller context;
    bind_workflow_session -> clears it.
    """
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store)
    pack = _journey_pack()
    monkeypatch.setattr(_session_router, "load_global_pack_graph", lambda: pack)

    class _StubArtifactStore:
        async def get_current_build_record_refs(self, *, app_id):  # noqa: ANN001
            return {}

    monkeypatch.setattr(
        "mozaiksai.core.artifacts.store.get_artifact_store",
        lambda: _StubArtifactStore(),
    )

    await router.bind_workflow_session(
        app_id="app_1",
        user_id="user_1",
        workflow_id="ValueEngine",
        chat_id="chat_ve",
        journey_id="build",
        journey_position=0,
    )
    state = await store.load(app_id="app_1", user_id="user_1")
    state.journey_instance_id = "ji_1"
    state.journey_key = "build"
    state.journey_position = 0
    await store.upsert(state)

    sessions = await persistence._coll()
    sessions._docs["chat_ve"] = {
        "_id": "chat_ve",
        "app_id": "app_1",
        "user_id": "user_1",
        "workflow_name": "ValueEngine",
        "status": int(WorkflowStatus.COMPLETED),
        "session_router_session_id": state.session_id,
        "journey_instance_id": "ji_1",
        "journey_position": 0,
    }

    advance = await router.advance_journey_after_run_complete(
        app_id="app_1",
        user_id="user_1",
        workflow_id="ValueEngine",
        chat_id="chat_ve",
        carry_context={
            "generated_app_id": "draft-build-abc123",
            "build_registry_id": "appreg_1",
            "design_docs_hitl": False,
            "value_manifest": {"not": "a scalar"},
            "huge": "x" * 5000,
        },
    )
    assert advance is not None
    assert advance.next_transition_id == "mode_selector"

    state = await store.load(app_id="app_1", user_id="user_1")
    assert state.pending_transition_id == "mode_selector"
    assert state.pending_transition_context == {
        "generated_app_id": "draft-build-abc123",
        "build_registry_id": "appreg_1",
        "design_docs_hitl": False,
    }

    resolution = await router.resolve_transition(
        app_id="app_1",
        user_id="user_1",
        transition_id="mode_selector",
        option_id="go",
        context_seed={"design_docs_hitl": True},
    )
    assert resolution.resolution_type == "workflow"
    assert resolution.context_seed["generated_app_id"] == "draft-build-abc123"
    assert resolution.context_seed["build_registry_id"] == "appreg_1"
    # Caller context wins over carried context.
    assert resolution.context_seed["design_docs_hitl"] is True

    await router.bind_workflow_session(
        app_id="app_1",
        user_id="user_1",
        workflow_id="AppGenerator",
        chat_id="chat_ag",
        journey_id="build",
    )
    state = await store.load(app_id="app_1", user_id="user_1")
    assert state.pending_transition_context == {}


@pytest.mark.asyncio
async def test_create_app_record_reserves_generated_app_id_without_touching_app_id(monkeypatch):
    module = _load_module_from_path(
        "ve_create_app_record",
        REPO_ROOT / "factory_app" / "workflows" / "ValueEngine" / "tools" / "create_app_record.py",
    )

    captured: dict = {}

    async def _fake_create(payload):  # noqa: ANN001
        captured.update(payload)
        return {
            "success": True,
            "app": {
                "build_registry_id": "appreg_test_1",
                "app_id": payload["app_id"],
            },
        }

    monkeypatch.setattr(module, "_create_studio_app", _fake_create)

    context = {
        "app_id": "mozaiks-factory",
        "chat_id": "chat_1",
        "user_id": "user_1",
    }
    result = await module.create_app_record(context_variables=context)

    assert result["success"] is True
    assert result["build_registry_id"] == "appreg_test_1"
    generated = result["generated_app_id"]
    assert generated.startswith("draft-build-")
    # The executing runtime identity is immutable during a run; the hook must
    # never rebind it (require_unchanged_runtime_authority would fail the run).
    assert context["app_id"] == "mozaiks-factory"
    assert context["generated_app_id"] == generated
    assert captured["chat_app_id"] == "mozaiks-factory"


def test_all_build_workflows_declare_generated_app_id():
    """Launch validation drops undeclared context keys, so every workflow in
    the build journey must declare generated_app_id for it to survive."""
    for workflow in BUILD_WORKFLOWS:
        path = REPO_ROOT / "factory_app" / "workflows" / workflow / "context_variables.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        definitions = payload.get("definitions") or {}
        assert "generated_app_id" in definitions, (
            f"{workflow} must declare generated_app_id in context_variables.yaml"
        )


def test_launch_validation_drops_unwritable_declared_keys_instead_of_failing():
    """Journey-inherited context that the target workflow's declared authority
    refuses for this writer is dropped with a warning — it must not abort the
    launch (a raised error here killed the ValueEngine→ThemeCapture journey
    advance in the live Genesis run: both workflows declare their own
    interview_complete routing flag)."""
    _launcher = import_module_directly("mozaiksai.core.session.launcher")
    _wm = import_module_directly("mozaiksai.core.workflow.workflow_manager")

    definitions = {
        "interview_complete": {
            "type": "boolean",
            "source": {
                "type": "state",
                "default": False,
                "triggers": [{"type": "agent_text", "value": "$1", "match": {"regex": "NEXT"}}],
            },
        },
        "app_type": {"type": "string", "source": {"type": "state", "default": None}},
    }
    original = _wm.workflow_manager.get_config
    _wm.workflow_manager.get_config = lambda wf: {"context_variables": {"definitions": definitions}}
    try:
        validated = _launcher.validate_context_for_workflow(
            "ThemeCaptureLike",
            {"interview_complete": True, "app_type": "greenfield_app", "undeclared": "x"},
        )
    finally:
        _wm.workflow_manager.get_config = original

    # The closed-writer routing flag and the undeclared key are dropped; the
    # ordinary state key survives.
    assert validated == {"app_type": "greenfield_app"}


def test_query_template_fallback_chain_resolution():
    """data_reference query templates support {{a||b}} fallback chains, so
    artifact hydration scopes by generated_app_id in build journeys and falls
    back to the executing app_id in revision re-entry."""
    _variables = import_module_directly("mozaiksai.core.workflow.context.variables")
    resolve = _variables._resolve_template_value

    ctx = {"generated_app_id": "draft-build-9"}
    assert resolve("{{generated_app_id||runtime.app_id}}", ctx, "mozaiks-factory") == "draft-build-9"
    assert resolve("{{generated_app_id||runtime.app_id}}", {}, "mozaiks-factory") == "mozaiks-factory"
    assert resolve("{{generated_app_id||runtime.app_id}}", {"generated_app_id": ""}, "app-x") == "app-x"
    # Plain expressions keep their existing semantics.
    assert resolve("{{runtime.app_id}}", ctx, "mozaiks-factory") == "mozaiks-factory"
    assert resolve("{{generated_app_id}}", ctx, "mozaiks-factory") == "draft-build-9"
    assert resolve("literal", ctx, "mozaiks-factory") == "literal"


def test_resolve_generated_app_id_precedence():
    module = _load_module_from_path(
        "shared_build_identity",
        REPO_ROOT / "factory_app" / "workflows" / "_shared" / "build_identity.py",
    )
    assert (
        module.resolve_generated_app_id(
            {"generated_app_id": "draft-build-1", "app_id": "mozaiks-factory"}
        )
        == "draft-build-1"
    )
    assert module.resolve_generated_app_id({"app_id": "mozaiks-factory"}) == "mozaiks-factory"
    assert module.resolve_generated_app_id({}) is None
    assert module.resolve_generated_app_id(None) is None
    ns = SimpleNamespace(data={"generated_app_id": "draft-build-2"})
    assert module.resolve_generated_app_id(ns) == "draft-build-2"
