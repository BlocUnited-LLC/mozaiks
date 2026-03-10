from __future__ import annotations

from tests.import_utils import import_module_directly

_resume = import_module_directly("mozaiksai.core.workflow.pack.resume_contract")
MFJ_RESUME_PENDING_KEY = _resume.MFJ_RESUME_PENDING_KEY
MFJ_RESUME_TARGET_KEY = _resume.MFJ_RESUME_TARGET_KEY
MFJ_RESUME_ENTRY_KEY = _resume.MFJ_RESUME_ENTRY_KEY
MFJ_RESUME_NONCE_KEY = _resume.MFJ_RESUME_NONCE_KEY
MFJ_RESUME_CONSUMED_NONCE_KEY = _resume.MFJ_RESUME_CONSUMED_NONCE_KEY
MFJ_RESUME_TRIGGER_ID_KEY = _resume.MFJ_RESUME_TRIGGER_ID_KEY
MFJ_RESUME_CYCLE_KEY = _resume.MFJ_RESUME_CYCLE_KEY
MFJ_RESUME_INJECT_AS_KEY = _resume.MFJ_RESUME_INJECT_AS_KEY
MFJ_RESUME_SUCCEEDED_COUNT_KEY = _resume.MFJ_RESUME_SUCCEEDED_COUNT_KEY
MFJ_RESUME_FAILED_COUNT_KEY = _resume.MFJ_RESUME_FAILED_COUNT_KEY
MFJ_RESUME_TS_KEY = _resume.MFJ_RESUME_TS_KEY
build_resume_context_payload = _resume.build_resume_context_payload
is_runtime_resume_key = _resume.is_runtime_resume_key
mark_resume_consumed = _resume.mark_resume_consumed


class _AG2LikeContext:
    def __init__(self) -> None:
        self.data = {}

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value


def test_build_resume_context_payload_shape() -> None:
    payload = build_resume_context_payload(
        trigger_id="planning",
        cycle=2,
        inject_as="mfj_planning_outputs",
        resume_entry_agent="ResumeRouterAgent",
        resume_target_agent="HostAgent",
        resume_nonce="abc123",
        succeeded_count=4,
        failed_count=1,
    )
    assert payload[MFJ_RESUME_PENDING_KEY] is True
    assert payload[MFJ_RESUME_TARGET_KEY] == "HostAgent"
    assert payload[MFJ_RESUME_ENTRY_KEY] == "ResumeRouterAgent"
    assert payload[MFJ_RESUME_NONCE_KEY] == "abc123"
    assert payload[MFJ_RESUME_TRIGGER_ID_KEY] == "planning"
    assert payload[MFJ_RESUME_CYCLE_KEY] == 2
    assert payload[MFJ_RESUME_INJECT_AS_KEY] == "mfj_planning_outputs"
    assert payload[MFJ_RESUME_SUCCEEDED_COUNT_KEY] == 4
    assert payload[MFJ_RESUME_FAILED_COUNT_KEY] == 1
    assert isinstance(payload[MFJ_RESUME_TS_KEY], str) and payload[MFJ_RESUME_TS_KEY]


def test_mark_resume_consumed_on_dict_context() -> None:
    context = {
        MFJ_RESUME_PENDING_KEY: True,
        MFJ_RESUME_NONCE_KEY: "nonce-1",
    }
    updates = mark_resume_consumed(context)
    assert updates[MFJ_RESUME_PENDING_KEY] is False
    assert updates[MFJ_RESUME_CONSUMED_NONCE_KEY] == "nonce-1"
    assert context[MFJ_RESUME_PENDING_KEY] is False
    assert context[MFJ_RESUME_CONSUMED_NONCE_KEY] == "nonce-1"


def test_mark_resume_consumed_on_ag2_like_context() -> None:
    context = _AG2LikeContext()
    context.set(MFJ_RESUME_PENDING_KEY, True)
    context.set(MFJ_RESUME_NONCE_KEY, "nonce-2")
    updates = mark_resume_consumed(context)
    assert updates[MFJ_RESUME_PENDING_KEY] is False
    assert updates[MFJ_RESUME_CONSUMED_NONCE_KEY] == "nonce-2"
    assert context.get(MFJ_RESUME_PENDING_KEY) is False
    assert context.get(MFJ_RESUME_CONSUMED_NONCE_KEY) == "nonce-2"


def test_is_runtime_resume_key() -> None:
    assert is_runtime_resume_key("_mfj_resume_pending") is True
    assert is_runtime_resume_key("mfj_planning_outputs") is True
    assert is_runtime_resume_key("project_name") is False
