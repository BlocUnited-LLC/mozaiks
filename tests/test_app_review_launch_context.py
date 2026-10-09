"""New review sessions hydrate saved build facts before the first model turn."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from ag2 import Agent

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.platform import build_target
from factory_app.workflows.AppReview.tools import review_context
from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.artifacts import ArtifactLifecycleStatus
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.session import launcher
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import build_context_authority_policy


@pytest.fixture
def review_launch(monkeypatch):
    binding = RunBuildBinding(
        build_registry_id="registry", target_app_id="target", build_id="build", phase="genesis",
    )
    record = {
        "app_id": "target", "chat_app_id": "factory", "lifecycle_state": "review",
        "current_build_run": {"build_id": "build", "phase": "genesis", "artifact_version_id": "saved"},
    }
    version = SimpleNamespace(
        id="saved", app_id="target", build_family="app_bundle", build_key="app_bundle",
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        app_validation_status="passed", app_validation_strategy="e2b",
        commit_metadata=SimpleNamespace(author_user_id="owner", metadata={
            **binding.model_dump(), "workspace_dir": "/saved/app",
            "app_bundle_acceptance": {"status": "passed", "passed": True},
        }),
    )
    registry = SimpleNamespace(
        resolve_build_binding=AsyncMock(return_value=binding),
        get_app_record=AsyncMock(return_value={"app": record}),
    )
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=version))
    monkeypatch.setattr(build_target, "AppRegistryService", lambda: registry)
    monkeypatch.setattr(review_context, "get_artifact_store", lambda: store)
    hooks = PlatformHookRegistry()
    hooks._chat_session_fields_hooks = [build_target.bind_factory_session]
    monkeypatch.setattr(launcher, "get_platform_hooks", lambda: hooks)
    return SimpleNamespace(binding=binding, record=record, version=version, registry=registry, store=store, hooks=hooks)


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "transition", "chat"])
async def test_new_review_hydrates_from_owned_build_without_a_handoff(review_launch, trigger):
    persistence = SimpleNamespace(create_chat_session=AsyncMock(), persist_server_owned_session_fields=AsyncMock())
    await launcher.create_routed_chat_session(
        workflow_id="AppReview", app_id="factory", user_id="owner", chat_id="new-review",
        context_variables={"artifact_version_id": "untrusted-stale", "bundle_path": "/untrusted"},
        trigger_meta={"trigger_source": trigger}, build_registry_id="registry",
        source_chat_id="old-review", persistence_manager=persistence,
    )
    fields = persistence.create_chat_session.await_args.kwargs["extra_fields"]
    assert fields["artifact_version_id"] == "saved"
    assert fields["bundle_path"] == "/saved/app"
    assert fields["app_validation_status"] == "passed"
    assert fields["app_validation_strategy_used"] == "e2b"
    assert fields["app_bundle_acceptance_status"] == "passed"
    assert fields["integration_tests_passed"] is True
    assert fields["lifecycle_state"] == "review"
    assert "run_build_binding" not in fields
    assert persistence.persist_server_owned_session_fields.await_args.kwargs["fields"] == {
        "run_build_binding": review_launch.binding.model_dump(),
    }
    review_launch.registry.get_app_record.assert_awaited_once_with(
        owner_user_id="owner", build_registry_id="registry",
    )
    review_launch.store.get_build_record.assert_awaited_once_with(app_id="target", build_record_id="saved")
    assert review_launch.registry.resolve_build_binding.await_args.kwargs["allow_current_build"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [
    "missing_registry", "host", "target", "superseded", "phase", "missing_id", "missing_artifact",
    "artifact_id", "artifact_app", "family", "archived", "author", "metadata",
])
async def test_review_launch_refuses_unowned_stale_or_missing_build_evidence(review_launch, damage):
    fixture = review_launch
    if damage == "missing_registry":
        fixture.registry.get_app_record.return_value = {"app": None}
    elif damage in {"host", "target"}:
        fixture.record["chat_app_id" if damage == "host" else "app_id"] = "other"
    elif damage in {"superseded", "phase", "missing_id"}:
        key = {"superseded": "build_id", "phase": "phase", "missing_id": "artifact_version_id"}[damage]
        fixture.record["current_build_run"][key] = None if damage == "missing_id" else "other"
    elif damage == "missing_artifact":
        fixture.store.get_build_record.return_value = None
    elif damage in {"artifact_id", "artifact_app", "family"}:
        setattr(fixture.version, {"artifact_id": "id", "artifact_app": "app_id", "family": "build_family"}[damage], "other")
    elif damage == "archived":
        fixture.version.lifecycle_status = ArtifactLifecycleStatus.ARCHIVED
    elif damage == "author":
        fixture.version.commit_metadata.author_user_id = "other"
    else:
        fixture.version.commit_metadata.metadata["build_id"] = "other"
    with pytest.raises(ValueError):
        await fixture.hooks.call_chat_session_fields(
            app_id="factory", user_id="owner", workflow_name="AppReview", chat_id="new-review",
            build_registry_id="registry",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(("workflow", "phase"), [("AppReview", "resume"), ("AppGenerator", "prepare")])
async def test_resume_and_other_workflows_keep_the_existing_binding_contract(review_launch, workflow, phase):
    fields = {"run_build_binding": review_launch.binding.model_dump()}
    result = await review_launch.hooks.call_chat_session_fields(
        app_id="factory", user_id="owner", workflow_name=workflow, chat_id="existing",
        phase=phase, session_fields=fields,
    )
    assert result == fields
    assert review_launch.registry.resolve_build_binding.await_args.kwargs["allow_current_build"] is False
    review_launch.registry.get_app_record.assert_not_awaited()
    review_launch.store.get_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_hydrated_review_user_reply_keeps_saved_artifact_in_revision_event(review_launch):
    persistence = SimpleNamespace(create_chat_session=AsyncMock(), persist_server_owned_session_fields=AsyncMock())
    await launcher.create_routed_chat_session(
        workflow_id="AppReview", app_id="factory", user_id="owner", chat_id="new-review",
        context_variables={}, trigger_meta={"trigger_source": "manual"},
        build_registry_id="registry", persistence_manager=persistence,
    )
    persisted = {
        **persistence.create_chat_session.await_args.kwargs["extra_fields"],
        **persistence.persist_server_owned_session_fields.await_args.kwargs["fields"],
        "chat_id": "new-review", "app_id": "factory", "user_id": "owner", "review_complete": False,
    }
    root = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppReview"
    rules = yaml.safe_load((root / "transition_graph.yaml").read_text(encoding="utf-8"))["transition_rules"]
    definitions = yaml.safe_load((root / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="AppReview", definitions=definitions, transition_rules=rules)
    bridge = ContextVariablesBridge(persisted, authority_policy=policy)
    events = []

    class ReviewAgent(Agent):
        def __init__(self):
            super().__init__("ReviewAgent", prompt="Deterministic saved-review routing test")
            self._mozaiks_context_bridge = bridge

        async def ask(self, *messages, **kwargs):
            events.append(review_context.build_revision_event_payload(bridge, "Make the timer teal."))
            return SimpleNamespace(body="Your saved draft is ready for review.")

    result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="AppReview", app_id="factory", chat_id="new-review",
        agents={"ReviewAgent": ReviewAgent()}, transition_rules=rules, context_authority_policy=policy,
        context_variables=bridge.snapshot(), initial_agent_name="ReviewAgent", initial_message="Review the draft.",
        idle_timeout_seconds=3.0,
    ))
    assert result.status is RunStatus.PAUSED, result.error
    try:
        continued = await result.live_run.continue_with_user_message("Make the timer teal.")
        assert continued.status is RunStatus.PAUSED, continued.error
        assert len(events) == 2
        assert events[-1]["artifact_version_id"] == "saved"
        assert events[-1]["extra"]["build_registry_id"] == "registry"
        assert events[-1]["extra"]["build_id"] == "build"
    finally:
        await result.live_run.close()


@pytest.fixture
def current_review(review_launch, monkeypatch):
    fixture = review_launch
    fixture.record.update(build_registry_id="registry", active_chat_id=None)
    repo = SimpleNamespace(
        get_by_build_registry_id=AsyncMock(return_value=fixture.record),
        get_owned_chat_binding=AsyncMock(return_value=fixture.binding.model_copy(update={"build_id": "older"}).model_dump()),
        update_lifecycle_state=AsyncMock(), upsert_app_record=AsyncMock(),
    )
    service = AppRegistryService(repo)
    monkeypatch.setattr(build_target, "AppRegistryService", lambda: service)
    fixture.repo = repo
    return fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["review", "needs_revision", "active"])
@pytest.mark.parametrize("phase", ["genesis", "refinement"])
async def test_current_saved_build_reopens_without_a_chat_or_new_build(current_review, state, phase):
    fixture = current_review
    fixture.binding = fixture.binding.model_copy(update={"phase": phase})
    fixture.version.commit_metadata.metadata["phase"] = phase
    fixture.record["current_build_run"]["phase"] = phase
    fixture.record["lifecycle_state"] = state
    before = deepcopy(fixture.record)
    persistence = SimpleNamespace(create_chat_session=AsyncMock(), persist_server_owned_session_fields=AsyncMock())
    await launcher.create_routed_chat_session(
        workflow_id="AppReview", app_id="factory", user_id="owner", chat_id="reopened-review",
        context_variables={}, trigger_meta={"trigger_source": "manual"},
        build_registry_id="registry", persistence_manager=persistence,
    )
    assert persistence.persist_server_owned_session_fields.await_args.kwargs["fields"] == {
        "run_build_binding": fixture.binding.model_dump(),
    }
    assert persistence.create_chat_session.await_args.kwargs["extra_fields"]["artifact_version_id"] == "saved"
    assert fixture.record == before
    fixture.repo.update_lifecycle_state.assert_not_awaited()
    fixture.repo.upsert_app_record.assert_not_awaited()
    fixture.repo.get_owned_chat_binding.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "foreign_host", "building", "draft", "artifact", "build_id", "phase", "race"])
async def test_current_build_reopen_fails_before_session_creation(current_review, damage):
    fixture = current_review
    if damage == "missing":
        fixture.repo.get_by_build_registry_id.return_value = None
    elif damage == "foreign_host":
        fixture.record["chat_app_id"] = "other"
    elif damage in {"building", "draft"}:
        fixture.record["lifecycle_state"] = damage
    elif damage in {"artifact", "build_id", "phase"}:
        fixture.record["current_build_run"].pop("artifact_version_id" if damage == "artifact" else damage)
    else:
        newer = deepcopy(fixture.record)
        newer["current_build_run"]["build_id"] = "newer"
        fixture.repo.get_by_build_registry_id.side_effect = [fixture.record, newer]
    persistence = SimpleNamespace(create_chat_session=AsyncMock(), persist_server_owned_session_fields=AsyncMock())
    with pytest.raises(ValueError):
        await launcher.create_routed_chat_session(
            workflow_id="AppReview", app_id="factory", user_id="owner", chat_id="reopened-review",
            context_variables={}, trigger_meta={"trigger_source": "manual"},
            build_registry_id="registry", persistence_manager=persistence,
        )
    persistence.create_chat_session.assert_not_awaited()
    fixture.repo.update_lifecycle_state.assert_not_awaited()
    fixture.repo.upsert_app_record.assert_not_awaited()
    assert fixture.repo.get_by_build_registry_id.await_args.kwargs["owner_user_id"] == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["old_source", "resume_old", "resume_missing", "other_workflow"])
async def test_current_build_policy_does_not_replace_explicit_or_resumed_bindings(current_review, mode):
    fixture = current_review
    extra = {}
    if mode == "old_source":
        extra["source_chat_id"] = "old-review"
    elif mode == "resume_old":
        extra.update(phase="resume", session_fields={
            "run_build_binding": fixture.binding.model_copy(update={"build_id": "older"}).model_dump(),
        })
    elif mode == "resume_missing":
        extra["phase"] = "resume"
    with pytest.raises(ValueError):
        await fixture.hooks.call_chat_session_fields(
            app_id="factory", user_id="owner", chat_id="reopened-review", build_registry_id="registry",
            workflow_name="AppGenerator" if mode == "other_workflow" else "AppReview", **extra,
        )
    fixture.repo.update_lifecycle_state.assert_not_awaited()
    fixture.repo.upsert_app_record.assert_not_awaited()
