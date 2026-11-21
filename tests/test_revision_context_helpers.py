"""
Pure helper unit tests for:
  mozaiksai/control_plane/revision_context.py

Covers helpers that do not require DB/IO:

  _load_pack:
    - callable returning LoadedControlPlanePack → returned as-is
    - callable returning a dict → model_validate wraps it

  _session_summary:
    - None state → {"present": False}
    - session state with required fields → "present" is True
    - session_id included
    - lifecycle_state .value extracted
    - sequence_status .value extracted
    - artifact_version_refs coerced to dict
    - stale_layers coerced to dict
    - revision_history tail-3 returned
    - empty revision_history → empty list
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mozaiksai.control_plane.revision_context import (
    _load_pack,
    _session_summary,
)
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.control_plane.tools.get_revision_context import get_revision_context
from mozaiksai.core.artifacts.models import BuildRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_pack_dict() -> dict:
    """Minimal valid dict that satisfies LoadedControlPlanePack.model_validate."""
    # Read a real pack from the factory path to get a valid dict
    from mozaiksai.control_plane.loader import load_refinement_harness
    pack = load_refinement_harness()
    return pack.model_dump(mode="python")


# ---------------------------------------------------------------------------
# 1. _load_pack
# ---------------------------------------------------------------------------

class TestLoadPack:
    def test_callable_returning_instance_returned_unchanged(self):
        from mozaiksai.control_plane.loader import load_refinement_harness
        pack = load_refinement_harness()
        def loader():
            return pack
        result = _load_pack(loader)
        assert result is pack

    def test_callable_returning_dict_wraps_with_model_validate(self):
        from mozaiksai.control_plane.loader import load_refinement_harness
        pack = load_refinement_harness()
        pack_dict = pack.model_dump(mode="python")
        def loader():
            return pack_dict
        result = _load_pack(loader)
        assert isinstance(result, LoadedControlPlanePack)


# ---------------------------------------------------------------------------
# 2. _session_summary
# ---------------------------------------------------------------------------

class _FakeEnum:
    def __init__(self, val: str):
        self.value = val


def _fake_state(**kwargs) -> SimpleNamespace:
    defaults = {
        "session_id": "sess-abc",
        "lifecycle_state": _FakeEnum("active"),
        "sequence_status": _FakeEnum("in_progress"),
        "current_workflow_id": "AppGenerator",
        "current_chat_id": "chat-1",
        "journey_key": "onboarding",
        "journey_position": 2,
        "journey_total_steps": 5,
        "active_revision_id": "rev-001",
        "active_change_request_id": "cr-001",
        "current_revision_scope": "feature_addition",
        "revision_origin_workflow": "AppGenerator",
        "restart_from_workflow": None,
        "artifact_version_refs": {"app_bundle": "v-1"},
        "stale_layers": {"ui": True},
        "revision_history": [],
        "updated_at": datetime(2026, 6, 12, 10, 0, 0, tzinfo=UTC),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestSessionSummary:
    def test_none_state_returns_not_present(self):
        result = _session_summary(None)
        assert result == {"present": False}

    def test_state_present_true(self):
        result = _session_summary(_fake_state())
        assert result["present"] is True

    def test_session_id_included(self):
        result = _session_summary(_fake_state(session_id="sess-xyz"))
        assert result["session_id"] == "sess-xyz"

    def test_lifecycle_state_value_extracted(self):
        result = _session_summary(_fake_state(lifecycle_state=_FakeEnum("active")))
        assert result["lifecycle_state"] == "active"

    def test_sequence_status_value_extracted(self):
        result = _session_summary(_fake_state(sequence_status=_FakeEnum("in_progress")))
        assert result["sequence_status"] == "in_progress"

    def test_artifact_version_refs_coerced_to_dict(self):
        refs = {"app_bundle": "v-abc"}
        result = _session_summary(_fake_state(artifact_version_refs=refs))
        assert result["artifact_version_refs"] == {"app_bundle": "v-abc"}

    def test_stale_layers_coerced_to_dict(self):
        result = _session_summary(_fake_state(stale_layers={"ui": True}))
        assert result["stale_layers"] == {"ui": True}

    def test_empty_revision_history_returns_empty_list(self):
        result = _session_summary(_fake_state(revision_history=[]))
        assert result["recent_revision_history"] == []

    def test_revision_history_tail_3_returned(self):
        def _entry(i):
            return SimpleNamespace(
                revision_id=f"rev-{i}",
                change_request_id=f"cr-{i}",
                scope="feature_addition",
                origin_workflow="AppGenerator",
                target_workflow="AppGenerator",
                from_version_refs={},
                to_version_refs={},
                timestamp=datetime(2026, 6, 12, tzinfo=UTC),
            )
        history = [_entry(i) for i in range(5)]
        result = _session_summary(_fake_state(revision_history=history))
        assert len(result["recent_revision_history"]) == 3
        # Should be the last 3 entries
        assert result["recent_revision_history"][0]["revision_id"] == "rev-2"

    def test_none_artifact_version_refs_returns_empty_dict(self):
        result = _session_summary(_fake_state(artifact_version_refs=None))
        assert result["artifact_version_refs"] == {}

    def test_current_workflow_id_present(self):
        result = _session_summary(_fake_state(current_workflow_id="MyWorkflow"))
        assert result["current_workflow_id"] == "MyWorkflow"


def _revision_record() -> BuildRecord:
    return BuildRecord(
        id="pinned-build",
        app_id="target-app",
        build_family="app_bundle",
        build_key="app_bundle",
        version_number=3,
        parent_build_record_id="parent-build",
        lineage_root_id="first-build",
        canonical_inputs_version={"design_docs": "approved-design"},
        lifecycle_status="current",
        validation_status="passed",
        commit_metadata={"metadata": {"summary_payload": {"nested": ["large-plan" * 50_000]}}},
    )


def _revision_context(**updates):
    return {
        "checkpoint": "coding_requested",
        "app_id": "execution-app",
        "target_app_id": "target-app",
        "user_id": "owner",
        "build_family": "app_bundle",
        "build_key": "app_bundle",
        "build_record_id": "pinned-build",
        "extra": {"change_request_id": "historical-change"},
        **updates,
    }


@pytest.mark.asyncio
async def test_coding_revision_context_reads_only_pinned_lineage_without_planning_payloads():
    record = _revision_record()
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=record))
    sessions = SimpleNamespace(load=AsyncMock(side_effect=AssertionError("coding must not load session history")))
    pack_loader = Mock(side_effect=AssertionError("coding must not load routing or tracked families"))

    result = await get_revision_context(
        context=_revision_context(), artifact_store=store, session_store=sessions, pack_loader=pack_loader,
    )

    store.get_build_record.assert_awaited_once_with(app_id="target-app", build_record_id="pinned-build")
    sessions.load.assert_not_awaited()
    pack_loader.assert_not_called()
    current = result["current_build_record"]
    assert result["app_id"] == "target-app"
    assert current["build_record_id"] == "pinned-build"
    assert current["parent_build_record_id"] == "parent-build"
    assert current["lineage_root_id"] == "first-build"
    assert current["canonical_inputs_version"] == {"design_docs": "approved-design"}
    assert (current["lifecycle_status"], current["validation_status"]) == ("current", "passed")
    assert "summary_payload" not in current
    assert "input_artifacts" not in current
    assert not {"tracked_build_records", "session", "routing", "active_change_request", "recent_change_requests"} & result.keys()
    assert len(json.dumps(result, default=str).encode("utf-8")) < 2_000
    assert record.commit_metadata.metadata["summary_payload"]["nested"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["missing", "app_id", "id", "build_family", "build_key"])
async def test_coding_revision_context_never_substitutes_a_missing_or_foreign_pin(mismatch):
    record = None if mismatch == "missing" else _revision_record().model_copy(update={mismatch: "other"})
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=record))
    result = await get_revision_context(context=_revision_context(), artifact_store=store)

    assert result["current_build_record"] == {
        "present": False, "build_family": "app_bundle", "build_key": "app_bundle",
        "build_record_id": "pinned-build", "source": "request_version_missing",
    }
    store.get_build_record.assert_awaited_once_with(app_id="target-app", build_record_id="pinned-build")


@pytest.mark.asyncio
async def test_coding_revision_context_can_resolve_pin_without_optional_family_selectors():
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=_revision_record()))
    result = await get_revision_context(
        context=_revision_context(build_family=None, build_key=None), artifact_store=store,
    )

    assert result["current_build_record"]["present"] is True
    assert result["current_build_record"]["build_family"] == "app_bundle"
    assert result["current_build_record"]["build_key"] == "app_bundle"


@pytest.mark.asyncio
async def test_coding_revision_context_requires_a_pin_instead_of_loading_latest():
    result = await get_revision_context(context=_revision_context(build_record_id=None), artifact_store=object())

    assert result["current_build_record"]["present"] is False
    assert result["current_build_record"]["source"] == "not_found"


@pytest.mark.asyncio
async def test_non_coding_revision_context_keeps_persisted_planning_payloads_and_input_artifacts():
    from mozaiksai.control_plane.loader import load_refinement_harness

    record = _revision_record()
    design = record.model_copy(update={"id": "approved-design", "build_family": "design_docs", "canonical_inputs_version": {}})
    records = {record.id: record, design.id: design}
    store = SimpleNamespace(
        get_build_record=AsyncMock(side_effect=lambda **kw: records.get(kw["build_record_id"])),
        list_build_records=AsyncMock(return_value=[]),
        list_change_requests=AsyncMock(return_value=[]),
    )
    sessions = SimpleNamespace(load=AsyncMock(return_value=None))
    result = await get_revision_context(
        context=_revision_context(checkpoint="request_submitted", extra={}),
        artifact_store=store, session_store=sessions, pack_loader=load_refinement_harness,
    )

    assert result["current_build_record"]["summary_payload"] == record.commit_metadata.metadata["summary_payload"]
    assert result["current_build_record"]["input_artifacts"]["design_docs"]["build_record_id"] == design.id
    assert result["tracked_build_records"]
    assert "routing" in result and "session" in result and "recent_change_requests" in result
    sessions.load.assert_awaited_once_with(app_id="execution-app", user_id="owner", target_app_id="target-app")
