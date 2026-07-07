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
    assert schemas.THREAD_TYPE_DM == "dm"
    assert schemas.THREAD_TYPE_GROUP == "group"
    assert schemas.THREAD_TYPES == {"dm", "group"}


def test_thread_statuses_complete():
    assert schemas.THREAD_STATUSES == {"open", "closed", "archived"}


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
        "thread_id", "title", "thread_type", "participant_ids",
        "context_id", "status", "created_by", "created_at",
        "updated_at", "last_message_at", "last_message",
    }
    assert required == set(schemas.Thread.__annotations__.keys())


def test_message_typeddict_has_required_fields():
    required = {
        "message_id", "thread_id", "sender_id", "body",
        "message_type", "created_at", "edited_at", "is_deleted",
    }
    assert required == set(schemas.Message.__annotations__.keys())


def test_read_state_typeddict_has_required_fields():
    required = {"thread_id", "user_id", "last_read_message_id", "read_at"}
    assert required == set(schemas.ReadState.__annotations__.keys())
