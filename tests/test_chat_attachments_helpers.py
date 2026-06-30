"""
Chat attachments pure helper unit tests.

Covers:
  _parse_allowed_workflows:
    - None → empty set
    - empty string → empty set
    - single workflow → {workflow}
    - comma-separated list → set of workflows
    - entries with whitespace → stripped
    - trailing/leading commas → empty entries excluded

  _normalize_intent:
    - "context" → "context"
    - "bundle" → "bundle"
    - "deliverable" → "deliverable"
    - uppercase → lowercased
    - whitespace stripped
    - unknown intent → ValueError
    - None → "context" (default)
    - empty string → "context" (default stripped to "context"? No: "" → "") → ValueError

  _max_bytes_from_env:
    - env var not set → default_bytes returned
    - env var set to valid int → returned
    - env var set to zero or negative → default returned
    - env var set to non-int string → default returned
    - env var set to whitespace → default returned
"""
from __future__ import annotations

import pytest

from mozaiksai.core.chat_attachments.attachments import (
    _allowed_mime_types_from_env,
    _max_bytes_from_env,
    _normalize_intent,
    _parse_allowed_workflows,
    handle_chat_upload,
)

# ---------------------------------------------------------------------------
# 1. _parse_allowed_workflows
# ---------------------------------------------------------------------------

class TestParseAllowedWorkflows:
    def test_none_returns_empty_set(self):
        assert _parse_allowed_workflows(None) == set()  # type: ignore[arg-type]

    def test_empty_string_returns_empty_set(self):
        assert _parse_allowed_workflows("") == set()

    def test_single_workflow(self):
        result = _parse_allowed_workflows("AppGenerator")
        assert result == {"AppGenerator"}

    def test_comma_separated_list(self):
        result = _parse_allowed_workflows("AppGenerator,AgentGenerator,ExistingApp")
        assert result == {"AppGenerator", "AgentGenerator", "ExistingApp"}

    def test_whitespace_stripped_from_entries(self):
        result = _parse_allowed_workflows("  AppGenerator  ,  AgentGenerator  ")
        assert result == {"AppGenerator", "AgentGenerator"}

    def test_empty_entries_excluded(self):
        result = _parse_allowed_workflows(",AppGenerator,")
        assert result == {"AppGenerator"}

    def test_all_whitespace_entries_excluded(self):
        result = _parse_allowed_workflows("  ,  ,AppGenerator")
        assert result == {"AppGenerator"}


# ---------------------------------------------------------------------------
# 2. _normalize_intent
# ---------------------------------------------------------------------------

class TestNormalizeIntent:
    def test_context_returned(self):
        assert _normalize_intent("context") == "context"

    def test_bundle_returned(self):
        assert _normalize_intent("bundle") == "bundle"

    def test_deliverable_returned(self):
        assert _normalize_intent("deliverable") == "deliverable"

    def test_uppercase_lowercased(self):
        assert _normalize_intent("Context") == "context"
        assert _normalize_intent("BUNDLE") == "bundle"

    def test_whitespace_stripped(self):
        assert _normalize_intent("  context  ") == "context"

    def test_none_defaults_to_context(self):
        assert _normalize_intent(None) == "context"

    def test_unknown_intent_raises(self):
        with pytest.raises(ValueError, match="intent must be one of"):
            _normalize_intent("attachment")

    def test_empty_string_defaults_to_context(self):
        # "" → (None-like falsy) → "context" default applied
        assert _normalize_intent("") == "context"


# ---------------------------------------------------------------------------
# 3. _max_bytes_from_env
# ---------------------------------------------------------------------------

class TestMaxBytesFromEnv:
    def test_env_not_set_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("TEST_MAX_BYTES", raising=False)
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_valid_int(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "2048")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 2048

    def test_env_set_to_zero_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "0")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_negative_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "-100")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_non_int_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "ten_megabytes")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_whitespace_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "   ")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_float_string_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        # int("3.5") raises ValueError → default
        monkeypatch.setenv("TEST_MAX_BYTES", "3.5")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024


# ---------------------------------------------------------------------------
# 4. _allowed_mime_types_from_env
# ---------------------------------------------------------------------------

class TestAllowedMimeTypesFromEnv:
    def test_env_not_set_returns_defaults(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("UPLOAD_ALLOWED_MIME_TYPES", raising=False)
        result = _allowed_mime_types_from_env()
        assert "application/json" in result
        assert "image/png" in result
        assert "application/pdf" in result
        assert "text/plain" in result

    def test_env_empty_returns_defaults(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "")
        result = _allowed_mime_types_from_env()
        assert "application/json" in result

    def test_env_custom_list(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "text/plain,application/json")
        result = _allowed_mime_types_from_env()
        assert result == frozenset({"text/plain", "application/json"})
        assert "image/png" not in result

    def test_env_wildcard_disables_enforcement(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "*")
        result = _allowed_mime_types_from_env()
        assert "*" in result

    def test_env_values_lowercased(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "Text/Plain, APPLICATION/JSON")
        result = _allowed_mime_types_from_env()
        assert "text/plain" in result
        assert "application/json" in result

    def test_env_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "  text/plain  ,  image/png  ")
        result = _allowed_mime_types_from_env()
        assert "text/plain" in result
        assert "image/png" in result


# ---------------------------------------------------------------------------
# 5. handle_chat_upload — MIME type enforcement
# ---------------------------------------------------------------------------

class _FakeFileObj:
    """Minimal UploadFile stub for testing handle_chat_upload."""
    def __init__(self, content_type: str, data: bytes = b"data", filename: str = "test.txt"):
        self.content_type = content_type
        self.filename = filename
        self._data = data
        self._read = False

    async def read(self, size: int = -1) -> bytes:
        if self._read:
            return b""
        self._read = True
        return self._data[:size] if size > 0 else self._data


class _FakeChatColl:
    def __init__(self, session_exists: bool = True, user_id: str = "user_1"):
        self._user_id = user_id
        self._session_exists = session_exists
        self.updates: list[tuple] = []

    async def find_one(self, query, projection=None):  # noqa: ANN001
        if not self._session_exists:
            return None
        return {"_id": query.get("_id"), "workflow_name": None, "user_id": self._user_id}

    async def update_one(self, query, update, **kwargs):  # noqa: ANN001
        self.updates.append((query, update))


@pytest.mark.asyncio
async def test_handle_chat_upload_rejects_disallowed_mime_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "text/plain,image/png")
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(tmp_path))
    coll = _FakeChatColl(user_id="u1")
    file_obj = _FakeFileObj(content_type="application/x-executable")

    with pytest.raises(ValueError, match="File type not permitted"):
        await handle_chat_upload(
            chat_coll=coll,
            file_obj=file_obj,
            app_id="app_1",
            user_id="u1",
            chat_id="chat_1",
        )


@pytest.mark.asyncio
async def test_handle_chat_upload_accepts_allowed_mime_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "text/plain,image/png")
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(tmp_path))
    coll = _FakeChatColl(user_id="u1")
    file_obj = _FakeFileObj(content_type="text/plain", data=b"hello")

    result = await handle_chat_upload(
        chat_coll=coll,
        file_obj=file_obj,
        app_id="app_1",
        user_id="u1",
        chat_id="chat_1",
    )
    assert result.bytes_written == 5


@pytest.mark.asyncio
async def test_handle_chat_upload_wildcard_bypasses_mime_enforcement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "*")
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(tmp_path))
    coll = _FakeChatColl(user_id="u1")
    file_obj = _FakeFileObj(content_type="application/x-executable", data=b"\x7fELF")

    result = await handle_chat_upload(
        chat_coll=coll,
        file_obj=file_obj,
        app_id="app_1",
        user_id="u1",
        chat_id="chat_1",
    )
    assert result.bytes_written == 4


@pytest.mark.asyncio
async def test_handle_chat_upload_strips_mime_parameters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """content_type with charset parameter should still match the base type."""
    monkeypatch.setenv("UPLOAD_ALLOWED_MIME_TYPES", "text/plain")
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(tmp_path))
    coll = _FakeChatColl(user_id="u1")
    file_obj = _FakeFileObj(content_type="text/plain; charset=utf-8", data=b"hello")

    result = await handle_chat_upload(
        chat_coll=coll,
        file_obj=file_obj,
        app_id="app_1",
        user_id="u1",
        chat_id="chat_1",
    )
    assert result.bytes_written == 5


# ---------------------------------------------------------------------------
# 6. handle_chat_upload — upload path containment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_chat_upload_rejects_traversal_in_app_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Path traversal sequences in app_id must be rejected before disk access."""
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(upload_root))
    monkeypatch.delenv("UPLOAD_ALLOWED_MIME_TYPES", raising=False)
    coll = _FakeChatColl(user_id="u1")
    file_obj = _FakeFileObj(content_type="text/plain", data=b"exploit")

    with pytest.raises(ValueError, match="outside the permitted upload directory"):
        await handle_chat_upload(
            chat_coll=coll,
            file_obj=file_obj,
            app_id="../../etc",
            user_id="u1",
            chat_id="chat_1",
        )


@pytest.mark.asyncio
async def test_handle_chat_upload_rejects_traversal_in_chat_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Path traversal sequences in chat_id must be rejected before disk access."""
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(upload_root))
    monkeypatch.delenv("UPLOAD_ALLOWED_MIME_TYPES", raising=False)
    coll = _FakeChatColl(user_id="u1")
    file_obj = _FakeFileObj(content_type="text/plain", data=b"exploit")

    with pytest.raises(ValueError, match="outside the permitted upload directory"):
        await handle_chat_upload(
            chat_coll=coll,
            file_obj=file_obj,
            app_id="app_1",
            user_id="u1",
            chat_id="../../../tmp",
        )


@pytest.mark.asyncio
async def test_handle_chat_upload_stored_path_inside_upload_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Verify stored_path of a legitimate upload stays within upload_root."""
    from pathlib import Path as P

    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(upload_root))
    monkeypatch.delenv("UPLOAD_ALLOWED_MIME_TYPES", raising=False)
    coll = _FakeChatColl(user_id="u1")
    file_obj = _FakeFileObj(content_type="text/plain", data=b"safe content")

    result = await handle_chat_upload(
        chat_coll=coll,
        file_obj=file_obj,
        app_id="safe_app",
        user_id="u1",
        chat_id="safe_chat",
    )
    stored = P(result.stored_path).resolve()
    assert stored.is_relative_to(upload_root.resolve())
