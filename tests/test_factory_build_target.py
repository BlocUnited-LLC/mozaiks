"""Factory target identity must not become execution or caller authority."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.platform.build_target import require_build_binding
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.session.build_binding import BuildTargetReference, RunBuildBinding
from mozaiksai.core.session.persistence import SessionStateStore
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)


def binding(**changes):
    return RunBuildBinding(
        **{
            "build_registry_id": "appreg_tracker",
            "target_app_id": "customer_tracker",
            "build_id": "build_genesis",
            "phase": "genesis",
            **changes,
        }
    )


def registry():
    repo = AsyncMock()
    repo.get_by_build_registry_id.return_value = {
        "app_id": "customer_tracker", "chat_app_id": "factory",
        "build_registry_id": "appreg_tracker", "active_chat_id": "first_chat",
    }
    repo.get_owned_chat_binding.return_value = binding().model_dump()
    return AppRegistryService(repo), repo


@pytest.mark.parametrize("value", ["", "../other", "a/b", "a.b", "a b", 123])
def test_target_identity_is_a_bounded_canonical_id(value):
    with pytest.raises(ValidationError):
        binding(target_app_id=value)


@pytest.mark.asyncio
async def test_resume_revalidates_owner_and_host():
    service, repo = registry()
    result = await service.resolve_build_binding(
        app_id="factory", owner_user_id="owner", chat_id="first_chat",
        workflow_name="ValueEngine", persisted_binding=binding().model_dump(), resume=True,
    )
    assert result == binding()
    repo.get_by_build_registry_id.assert_awaited_once_with(
        build_registry_id="appreg_tracker", owner_user_id="owner"
    )
    repo.upsert_app_record.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [None, {"app_id": "customer_tracker", "chat_app_id": "other_host"}])
async def test_foreign_or_missing_registry_fails_closed(record):
    service, repo = registry()
    repo.get_by_build_registry_id.return_value = record
    with pytest.raises(ValueError, match="not available"):
        await service.resolve_build_binding(
            app_id="factory", owner_user_id="other_owner", chat_id="next_chat",
            workflow_name="AppGenerator", build_registry_id="appreg_tracker",
        )
    repo.upsert_app_record.assert_not_called()


@pytest.mark.asyncio
async def test_source_chat_is_scoped_to_authenticated_owner_and_host():
    service, repo = registry()
    result = await service.resolve_build_binding(
        app_id="factory", owner_user_id="owner", chat_id="next_chat",
        workflow_name="DesignDocs", source_chat_id="first_chat",
    )
    assert result == binding()
    repo.get_owned_chat_binding.assert_awaited_once_with(
        app_id="factory", owner_user_id="owner", chat_id="first_chat"
    )


@pytest.mark.asyncio
async def test_superseded_session_is_rejected_before_execution():
    service, repo = registry()
    repo.get_by_build_registry_id.return_value["current_build_run"] = {"build_id": "newer_build"}
    with pytest.raises(ValueError, match="superseded"):
        await service.resolve_build_binding(
            app_id="factory", owner_user_id="owner", chat_id="first_chat",
            workflow_name="AppGenerator", persisted_binding=binding().model_dump(), resume=True,
        )
    repo.update_lifecycle_state.assert_not_awaited()


def _draft_with_prior_build(repo, prior_build_id="build_prepared"):
    """A registered draft that already carried a build and has no active chat.

    This is what a re-run looks like: the target is prepared, its previous
    build id is still on the record, and no chat is attached yet.
    """
    repo.get_by_build_registry_id.return_value = {
        "app_id": "customer_tracker", "chat_app_id": "factory",
        "build_registry_id": "appreg_tracker", "active_chat_id": None,
        "lifecycle_state": "draft",
        "current_build_run": {"build_id": prior_build_id, "phase": "genesis"},
    }

    async def _persist(**kwargs):
        # Echo the write, as the real repo does, so the caller can read back
        # what it just stored rather than the copy it read beforehand.
        return {
            **repo.get_by_build_registry_id.return_value,
            "lifecycle_state": kwargs["lifecycle_state"],
            "active_chat_id": kwargs["active_chat_id"],
            "current_build_run": kwargs["current_build_run"],
        }

    repo.update_lifecycle_state.side_effect = _persist
    return repo


@pytest.mark.asyncio
async def test_starting_a_draft_that_already_carried_a_build_is_not_superseded():
    """The live failure: a re-run against a prepared target was rejected.

    OSS 859d6c59, chat 741a31e4, requested release-greenfield-value-target.
    The start returned HTTP 500 "The selected build session has been
    superseded" before any plan review. This branch mints a build id and
    persists it, then the supersession check read the pre-update record and
    compared the new build against the one it had just replaced.
    """
    service, repo = registry()
    _draft_with_prior_build(repo)

    result = await service.resolve_build_binding(
        app_id="factory", owner_user_id="owner", chat_id="fresh_chat",
        workflow_name="ValueEngine", build_registry_id="appreg_tracker",
        allow_create=True,
    )

    assert result.target_app_id == "customer_tracker"
    assert result.build_id != "build_prepared", "a new chat starts a new build run"
    persisted = repo.update_lifecycle_state.await_args.kwargs["current_build_run"]
    assert persisted["build_id"] == result.build_id, "the binding must be the one stored"


@pytest.mark.asyncio
async def test_a_concurrent_writer_still_stops_the_start():
    """The race the supersession check exists for is caught by the conditional
    write, so relaxing the stale comparison does not open it."""
    service, repo = registry()
    _draft_with_prior_build(repo)
    repo.update_lifecycle_state.side_effect = None
    repo.update_lifecycle_state.return_value = None  # expected_lifecycle_state no longer matched

    with pytest.raises(ValueError, match="changed before its build could start"):
        await service.resolve_build_binding(
            app_id="factory", owner_user_id="owner", chat_id="fresh_chat",
            workflow_name="ValueEngine", build_registry_id="appreg_tracker",
            allow_create=True,
        )


@pytest.mark.asyncio
async def test_a_draft_start_is_refused_without_create_authority():
    """Reading back the write must not become a way to create one."""
    service, repo = registry()
    _draft_with_prior_build(repo)

    with pytest.raises(ValueError, match="no resumable build session"):
        await service.resolve_build_binding(
            app_id="factory", owner_user_id="owner", chat_id="fresh_chat",
            workflow_name="AppGenerator", build_registry_id="appreg_tracker",
        )
    repo.update_lifecycle_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_cannot_switch_targets_using_another_registry_selector():
    service, repo = registry()
    with pytest.raises(ValueError, match="does not match"):
        await service.resolve_build_binding(
            app_id="factory", owner_user_id="owner", chat_id="next_chat",
            workflow_name="AppGenerator", source_chat_id="first_chat",
            build_registry_id="appreg_other",
        )
    repo.get_by_build_registry_id.assert_not_called()


@pytest.mark.asyncio
async def test_refinement_keeps_target_but_allocates_new_build():
    service, _ = registry()
    result = await service.resolve_build_binding(
        app_id="factory", owner_user_id="owner", chat_id="revision_chat",
        workflow_name="AppGenerator", source_chat_id="first_chat", refinement=True,
    )
    assert result.target_app_id == binding().target_app_id
    assert result.build_registry_id == binding().build_registry_id
    assert result.build_id != binding().build_id
    assert result.phase == "refinement"


@pytest.mark.asyncio
async def test_registration_failure_is_not_suppressed():
    service, _ = registry()
    service.create_app_record = AsyncMock(side_effect=RuntimeError("database unavailable"))
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.resolve_build_binding(
            app_id="factory", owner_user_id="owner", chat_id="new_chat",
            workflow_name="ValueEngine", allow_create=True,
        )


@pytest.mark.asyncio
async def test_resume_cannot_register_a_missing_binding():
    service, repo = registry()
    with pytest.raises(ValueError, match="no registered target"):
        await service.resolve_build_binding(
            app_id="factory", owner_user_id="owner", chat_id="old_chat",
            workflow_name="ValueEngine", resume=True, allow_create=True,
        )
    repo.upsert_app_record.assert_not_called()


@pytest.mark.parametrize("writer", ["caller_input", "transition_router", "lifecycle_tool", "tool_writeback", "persisted_replay"])
def test_binding_is_not_writable_by_workflow_actors(writer):
    policy = build_context_authority_policy(
        workflow_name="ValueEngine",
        definitions={"run_build_binding": {"type": "object", "source": {"type": "runtime", "required": True}}},
    )
    context = create_context_container({"run_build_binding": binding().model_dump(), "app_id": "factory"}, authority_policy=policy)
    assert require_build_binding(context) == binding()
    with pytest.raises(ContextAuthorityError):
        policy.require_can_write("run_build_binding", writer_id=writer)
    assert context.get("app_id") == "factory"


def test_same_owner_targets_have_different_router_state_keys():
    keys = {
        SessionStateStore.session_id_for_scope("factory", owner, target)
        for owner in ("alice", "bob") for target in (None, "tracker", "shop")
    }
    assert len(keys) == 6


@pytest.mark.asyncio
async def test_required_session_hook_failure_propagates():
    hooks = PlatformHookRegistry()
    hooks.register_bundle({"chat_session_fields": AsyncMock(side_effect=RuntimeError("registration failed"))}, source="test")
    with pytest.raises(RuntimeError, match="registration failed"):
        await hooks.call_chat_session_fields("factory", "owner", "ValueEngine", "chat")


@pytest.mark.asyncio
async def test_operator_hook_cannot_replace_factory_binding():
    hooks = PlatformHookRegistry()
    hooks.register_bundle({"chat_session_fields": lambda **kw: {"run_build_binding": binding().model_dump()}}, source="factory")
    hooks.register_bundle({"chat_session_fields": lambda **kw: {"run_build_binding": binding(target_app_id="other").model_dump()}}, source="operator")
    with pytest.raises(ValueError, match="Conflicting session field"):
        await hooks.call_chat_session_fields("factory", "owner", "ValueEngine", "chat")


@pytest.mark.parametrize("key", ["build_registry_id", "source_chat_id"])
@pytest.mark.parametrize("value", [{"$ne": None}, ["appreg_tracker"], "", "../target", 42])
@pytest.mark.asyncio
async def test_untrusted_selectors_are_rejected_before_database_lookup(key, value):
    service, repo = registry()
    with pytest.raises(ValidationError):
        await service.resolve_build_binding(
            app_id="factory", owner_user_id="owner", chat_id="chat",
            workflow_name="AppGenerator", **{key: value},
        )
    repo.get_owned_chat_binding.assert_not_called()
    repo.get_by_build_registry_id.assert_not_called()


def test_reference_cannot_carry_authoritative_binding():
    with pytest.raises(ValidationError):
        BuildTargetReference(target_app_id="stolen", build_id="forged")


@pytest.mark.asyncio
async def test_resume_hook_cannot_install_a_new_field():
    hooks = PlatformHookRegistry()
    hooks.register_bundle({"chat_session_fields": lambda **kw: {"new_field": "x"}}, source="operator")
    with pytest.raises(ValueError, match="Resume cannot install"):
        await hooks.call_chat_session_fields("factory", "owner", "ValueEngine", "chat", phase="resume")


@pytest.mark.asyncio
async def test_runtime_binding_precedes_artifact_hydration(monkeypatch):
    from mozaiksai.core.workflow.context import variables
    from mozaiksai.core.workflow.context.schema import ContextVariablesPlan

    plan = ContextVariablesPlan.model_validate({"definitions": {
        "concept": {"type": "object", "source": {"type": "data_reference"}},
        "run_build_binding": {"type": "object", "source": {"type": "runtime", "required": True}},
    }})
    monkeypatch.setattr(variables, "_load_workflow_plan", lambda _: (plan, {}))
    monkeypatch.setattr(variables, "_task_batch_context_keys", lambda _: set())

    async def load_artifact(*args, context, app_id, **kwargs):
        assert app_id == "factory"
        assert require_build_binding(context) == binding()
        query = variables._materialize_query_template(
            {"app_id": "{{run_build_binding.target_app_id}}"}, app_id=app_id, context=context,
        )
        assert query == {"app_id": "customer_tracker"}
        return {"app_id": "customer_tracker"}

    loader = AsyncMock(side_effect=load_artifact)
    monkeypatch.setattr(variables, "_load_data_reference_value", loader)
    context = await variables._load_context_async(
        "BindingSmoke", "factory", runtime_context={"run_build_binding": binding().model_dump()},
    )
    assert context.get("app_id") == "factory"
    assert context.get("concept")["app_id"] == "customer_tracker"
    loader.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_binding_stops_before_artifact_lookup(monkeypatch):
    from mozaiksai.core.workflow.context import variables
    from mozaiksai.core.workflow.context.schema import ContextVariablesPlan

    plan = ContextVariablesPlan.model_validate({"definitions": {
        "concept": {"type": "object", "source": {"type": "data_reference"}},
        "run_build_binding": {"type": "object", "source": {"type": "runtime", "required": True}},
    }})
    monkeypatch.setattr(variables, "_load_workflow_plan", lambda _: (plan, {}))
    monkeypatch.setattr(variables, "_task_batch_context_keys", lambda _: set())
    loader = AsyncMock()
    monkeypatch.setattr(variables, "_load_data_reference_value", loader)
    with pytest.raises(ValueError, match="Required runtime context"):
        await variables._load_context_async("BindingSmoke", "factory")
    loader.assert_not_awaited()
