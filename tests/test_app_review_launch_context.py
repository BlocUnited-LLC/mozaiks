"""New review sessions hydrate saved build facts before the first model turn."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows._shared.platform import build_target
from factory_app.workflows.AppReview.tools import review_context
from mozaiksai.core.artifacts import ArtifactLifecycleStatus
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.session import launcher
from mozaiksai.core.session.build_binding import RunBuildBinding


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
    review_launch.registry.get_app_record.assert_not_awaited()
    review_launch.store.get_build_record.assert_not_awaited()
