"""A fresh ThemeCapture retry restores only its completed, approved build input."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from mozaiksai.core.artifacts.models import ArtifactLifecycleStatus, BuildRecord


@pytest.fixture
def recovery(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/ThemeCapture/tools/preload_theme_capture_context.py"
    spec = importlib.util.spec_from_file_location("theme_concept_recovery_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    binding = {"build_registry_id": "appreg_owned", "target_app_id": "target_app",
               "build_id": "build_current", "phase": "genesis"}
    context = {"app_id": "host_app", "user_id": "owner", "chat_id": "new_retry",
               "app_type": "greenfield_app", "run_build_binding": copy.deepcopy(binding),
               "value_manifest": None, "theme_save_attempts": 0, "interview_attempts": 0}
    summary = {"app_id": "target_app", "app_name": "Tool Library", "status": "approved",
               "review_id": "concept_review_exact", "concept_overview": "Shared tool lending",
               "value_proposition": "Borrow instead of buying", "target_user": "Local members",
               "brand_intent": {"appearance": "light", "primary_color": "teal"},
               "approved_scope": ["browse", "request", "review", "return"]}
    record = BuildRecord.model_validate({
        "_id": "av_" + "1" * 24, "app_id": "target_app", "build_family": "concept", "build_key": "concept",
        "version_number": 4, "lineage_root_id": "av_" + "0" * 24,
        "source_workflow": "ValueEngine", "source_chat_id": "approved_source",
        "lifecycle_status": "current", "validation_status": "skipped",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": summary}},
    })
    source = {"_id": "approved_source", "app_id": "host_app", "user_id": "owner",
              "workflow_name": "ValueEngine", "status": 1, "concept_review_outcome": "approved",
              "run_build_binding": copy.deepcopy(binding), "value_manifest": copy.deepcopy(summary),
              "interview_attempts": 9, "theme_save_attempts": 8, "structured_output": {"untrusted": True}}
    state = SimpleNamespace(record=record, source=source, context=context, reads=[], queries=[])

    class Store:
        async def list_build_records(self, **kwargs):
            state.reads.append(kwargs)
            return [state.record] if state.record is not None else []

    class Collection:
        async def find_one(self, query, projection):
            state.queries.append((query, projection))
            return copy.deepcopy(state.source)

    class Persistence:
        async def _coll(self):
            return Collection()

    monkeypatch.setattr(module, "get_artifact_store", Store)
    monkeypatch.setattr(module, "AG2PersistenceManager", Persistence)
    return module, state


@pytest.mark.asyncio
async def test_matching_genesis_retry_restores_approved_concept_only(recovery):
    module, state = recovery
    result = await module.collect_prechat_theme_context(state.context)
    assert result["success"] is True
    assert state.context["value_manifest"] == state.source["value_manifest"]
    assert state.context["value_manifest"] is not state.record.commit_metadata.metadata["summary_payload"]
    assert state.context["app_name"] == "Tool Library"
    assert state.context["concept_overview"] == "Shared tool lending"
    assert state.context["theme_save_attempts"] == state.context["interview_attempts"] == 0
    assert "structured_output" not in state.context
    assert state.reads == [{"app_id": "target_app", "build_family": "concept", "build_key": "concept",
                            "lifecycle_status": ArtifactLifecycleStatus.CURRENT, "limit": 1}]
    query, projection = state.queries[0]
    assert query == {"_id": "approved_source", "app_id": "host_app", "user_id": "owner",
                     "workflow_name": "ValueEngine", "status": 1, "concept_review_outcome": "approved",
                     **{f"run_build_binding.{key}": value for key, value in state.context["run_build_binding"].items()}}
    assert "structured_output" not in projection and "interview_attempts" not in projection


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "changes_requested", "cancelled"])
async def test_current_nonapproved_concept_never_searches_for_older_approval(recovery, status):
    module, state = recovery
    state.record.commit_metadata.metadata["summary_payload"]["status"] = status
    before = copy.deepcopy(state.context)
    with pytest.raises(ValueError, match="owned approval"):
        await module._restore_approved_genesis_concept(state.context)
    assert state.context == before and len(state.reads) == 1 and not state.queries


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("app_id", "foreign_app"), ("source_chat_id", "foreign_source"),
    ("source_workflow", "OtherWorkflow"), ("lifecycle_status", ArtifactLifecycleStatus.SUPERSEDED),
])
async def test_mismatched_artifact_is_never_projected(recovery, field, value):
    module, state = recovery
    setattr(state.record, field, value)
    before = copy.deepcopy(state.context)
    with pytest.raises(ValueError):
        await module._restore_approved_genesis_concept(state.context)
    assert state.context == before


@pytest.mark.asyncio
async def test_foreign_artifact_author_is_rejected_before_source_lookup(recovery):
    module, state = recovery
    state.record.commit_metadata.author_user_id = "another_owner"
    with pytest.raises(ValueError, match="owned approval"):
        await module._restore_approved_genesis_concept(state.context)
    assert state.context["value_manifest"] is None and not state.queries


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("_id", "foreign_source"), ("app_id", "other_host"), ("user_id", "another_owner"),
    ("workflow_name", "ThemeCapture"), ("status", 0), ("status", 2),
    ("concept_review_outcome", "changes_requested"), ("value_manifest", {"status": "approved"}),
])
async def test_source_must_be_completed_owner_approval_matching_exact_summary(recovery, field, value):
    module, state = recovery
    state.source[field] = value
    before = copy.deepcopy(state.context)
    with pytest.raises(ValueError, match="source does not match"):
        await module._restore_approved_genesis_concept(state.context)
    assert state.context == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["build_registry_id", "target_app_id", "build_id", "phase"])
async def test_entire_source_binding_must_match(recovery, field):
    module, state = recovery
    state.source["run_build_binding"][field] = "different"
    with pytest.raises(ValueError, match="source does not match"):
        await module._restore_approved_genesis_concept(state.context)
    assert state.context["value_manifest"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["existing_concept", "refinement", "brownfield", "unbound"])
async def test_existing_and_non_genesis_inputs_are_preserved_without_lookup(recovery, kind):
    module, state = recovery
    if kind == "existing_concept":
        state.context["value_manifest"] = {"already": "provided"}
    elif kind == "refinement":
        state.context["run_build_binding"]["phase"] = "refinement"
        state.context["parent_theme_config"] = {"theme": {"appearance": "dark"}}
    elif kind == "brownfield":
        state.context["app_type"] = "existing_app"
        state.context["frontend_repo_summary"] = {"repo_name": "existing"}
    else:
        del state.context["run_build_binding"]
    before = copy.deepcopy(state.context)
    await module._restore_approved_genesis_concept(state.context)
    assert state.context == before and not state.reads and not state.queries


@pytest.mark.asyncio
async def test_absent_current_concept_preserves_existing_theme_intake(recovery):
    module, state = recovery
    state.record = None
    before = copy.deepcopy(state.context)
    await module._restore_approved_genesis_concept(state.context)
    assert state.context == before and not state.queries


@pytest.mark.asyncio
async def test_explicit_theme_input_fields_survive_missing_manifest_recovery(recovery):
    module, state = recovery
    state.context.update(app_name="Explicit name", parent_theme_config={"theme": {"appearance": "light"}})
    await module._restore_approved_genesis_concept(state.context)
    assert state.context["app_name"] == "Explicit name"
    assert state.context["parent_theme_config"] == {"theme": {"appearance": "light"}}

@pytest.mark.asyncio
async def test_approved_concept_recovery_uses_runtime_frozen_reads_and_before_chat_writer(recovery):
    from types import MappingProxyType

    from mozaiksai.core.workflow.context.adapter import (
        _RuntimeContextVariables,
        create_context_container,
    )
    from mozaiksai.core.workflow.context.authority import (
        RUNTIME_SYSTEM_WRITER,
        build_context_authority_policy,
    )
    from mozaiksai.core.workflow.context.frozen import detach
    from mozaiksai.core.workflow.context.variables import (
        _load_workflow_plan,
        _task_batch_context_keys,
    )
    from mozaiksai.core.workflow.workflow_manager import get_workflow_manager

    module, state = recovery
    plan, _ = _load_workflow_plan("ThemeCapture")
    config = get_workflow_manager().get_config("ThemeCapture") or {}
    context = create_context_container(copy.deepcopy(state.context))
    context._mozaiks_context_authority_policy = build_context_authority_policy(
        workflow_name="ThemeCapture", definitions=plan.definitions or {},
        transition_rules=(config.get("transition_graph") or {}).get("transition_rules") or [],
        task_batch_context_keys=_task_batch_context_keys("ThemeCapture"),
    )
    context._mozaiks_context_writer_id = RUNTIME_SYSTEM_WRITER
    assert isinstance(context, _RuntimeContextVariables)
    assert isinstance(context.get("run_build_binding"), MappingProxyType)

    result = await module.collect_prechat_theme_context(context)

    assert result["success"] is True
    assert isinstance(context.get("value_manifest"), MappingProxyType)
    assert isinstance(context.get("value_manifest")["approved_scope"], tuple)
    assert detach(context.get("value_manifest")) == state.source["value_manifest"]
    assert context.get("app_name") == "Tool Library"
    assert context.get("concept_overview") == "Shared tool lending"
    assert context.get("theme_save_attempts") == context.get("interview_attempts") == 0
    assert context.get("structured_output") is None
    assert len(state.reads) == len(state.queries) == 1
