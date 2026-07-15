"""Unit tests for the messaging build_context schemas.py.

These tests are self-contained — schemas.py has no mozaiksai runtime imports.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCHEMAS_PATH = (
    Path(__file__).parent.parent
    / "factory_app"
    / "build_context"
    / "messaging"
    / "templates"
    / "modules"
    / "messages"
    / "backend"
    / "schemas.py"
)


def _load_schemas():
    spec = importlib.util.spec_from_file_location("msg_schemas", _SCHEMAS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


schemas = _load_schemas()


# ---------------------------------------------------------------------------
# coerce_limit
# ---------------------------------------------------------------------------


def test_coerce_limit_clamps_to_maximum():
    assert schemas.coerce_limit(9999, maximum=100) == 100


def test_coerce_limit_clamps_to_minimum():
    assert schemas.coerce_limit(0) == 1
    assert schemas.coerce_limit(-5) == 1


def test_coerce_limit_falls_back_to_default_on_non_int():
    assert schemas.coerce_limit("abc", default=20) == 20
    assert schemas.coerce_limit(None, default=15) == 15


def test_coerce_limit_valid_value():
    assert schemas.coerce_limit(42) == 42


# ---------------------------------------------------------------------------
# timestamp_now
# ---------------------------------------------------------------------------


def test_timestamp_now_returns_iso_string():
    ts = schemas.timestamp_now()
    assert isinstance(ts, str)
    assert "T" in ts  # ISO 8601 format
    assert "+" in ts or "Z" in ts  # timezone-aware


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_thread_types_complete():
    assert schemas.THREAD_TYPES == {"direct", "group", "support"}
    assert schemas.normalize_thread_type("direct") == "direct"
    assert schemas.normalize_thread_type("support") == "support"
    assert schemas.normalize_thread_type("unexpected") == "group"


def test_thread_scope_types_complete():
    assert schemas.THREAD_SCOPE_TYPES == {"app", "workspace"}
    assert schemas.normalize_scope_type("workspace") == "workspace"
    assert schemas.normalize_scope_type("unexpected") == "app"


def test_thread_statuses_complete():
    assert schemas.THREAD_STATUSES == {"open", "resolved", "archived"}
    assert schemas.normalize_status("resolved") == "resolved"
    assert schemas.normalize_status("closed") == "open"


def test_max_message_length_is_reasonable():
    assert schemas.MAX_MESSAGE_LENGTH >= 1000
    assert schemas.MAX_MESSAGE_LENGTH <= 10_000


def test_message_preview_length_is_subset_of_max():
    assert schemas.MESSAGE_PREVIEW_LENGTH < schemas.MAX_MESSAGE_LENGTH


# ---------------------------------------------------------------------------
# TypedDict shapes (structural)
# ---------------------------------------------------------------------------


def test_thread_typeddict_has_required_fields():
    required = {
        "thread_id", "scope_type", "scope_id", "subject_app_id",
        "title", "thread_type", "participant_ids", "status",
        "created_by", "created_at", "updated_at", "last_message_at",
        "last_message", "related_type", "related_id", "metadata",
    }
    assert required == set(schemas.ThreadRecord.__annotations__.keys())


def test_message_typeddict_has_required_fields():
    required = {
        "message_id", "thread_id", "sender_id", "sender_role", "body",
        "message_type", "created_at", "edited_at", "is_deleted", "metadata",
    }
    assert required == set(schemas.MessageRecord.__annotations__.keys())


def test_build_thread_record_persists_scope_and_subject_app_id():
    record = schemas.build_thread_record(
        created_by="user_1",
        scope_type="workspace",
        scope_id="workspace_1",
        subject_app_id="app_1",
        participant_ids=["user_2"],
    )

    assert record["scope_type"] == "workspace"
    assert record["scope_id"] == "workspace_1"
    assert record["subject_app_id"] == "app_1"
    assert record["participant_ids"] == ["user_1", "user_2"]
