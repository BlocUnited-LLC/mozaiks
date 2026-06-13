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

from datetime import UTC, datetime
from types import SimpleNamespace

from mozaiksai.control_plane.revision_context import (
    _load_pack,
    _session_summary,
)
from mozaiksai.control_plane.schema import LoadedControlPlanePack

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_pack_dict() -> dict:
    """Minimal valid dict that satisfies LoadedControlPlanePack.model_validate."""
    # Read a real pack from the factory path to get a valid dict
    from mozaiksai.control_plane.loader import load_control_plane_pack
    pack = load_control_plane_pack()
    return pack.model_dump(mode="python")


# ---------------------------------------------------------------------------
# 1. _load_pack
# ---------------------------------------------------------------------------

class TestLoadPack:
    def test_callable_returning_instance_returned_unchanged(self):
        from mozaiksai.control_plane.loader import load_control_plane_pack
        pack = load_control_plane_pack()
        def loader():
            return pack
        result = _load_pack(loader)
        assert result is pack

    def test_callable_returning_dict_wraps_with_model_validate(self):
        from mozaiksai.control_plane.loader import load_control_plane_pack
        pack = load_control_plane_pack()
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
