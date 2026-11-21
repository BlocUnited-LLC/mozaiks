from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from logs import logging_config
from logs.logging_config import (
    PrettyConsoleFormatter,
    ProductionJSONFormatter,
    SafeRotatingFileHandler,
)


def test_safe_rotating_file_handler_writes_when_rollover_is_locked(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "mozaiks.log"
    handler = SafeRotatingFileHandler(
        log_path,
        maxBytes=1,
        backupCount=1,
        encoding="utf-8",
        rollover_retry_seconds=5.0,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    def _raise_permission_error(*_args, **_kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(handler, "rotate", _raise_permission_error)

    record = logging.LogRecord(
        name="tests.logging",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello world",
        args=(),
        exc_info=None,
    )

    try:
        handler.emit(record)
    finally:
        handler.close()

    assert "hello world" in log_path.read_text(encoding="utf-8")


def test_agent_transcript_logging_is_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("MOZAIKS_AGENT_TRANSCRIPT_LOGGING_ENABLED", raising=False)
    assert logging_config.agent_transcript_logging_enabled() is False

    monkeypatch.setenv("MOZAIKS_AGENT_TRANSCRIPT_LOGGING_ENABLED", "true")
    assert logging_config.agent_transcript_logging_enabled() is True


def test_context_logger_exception_keeps_traceback_and_context(caplog) -> None:
    base = logging.getLogger("tests.context_exception")
    logger = logging_config.ContextLogger(base, {"app_id": "app-test"})
    with caplog.at_level(logging.ERROR, logger=base.name):
        try:
            raise ValueError("index rejected")
        except ValueError:
            logger.exception("Job %s failed", "job-test", job_id="job-test")
    record = caplog.records[-1]
    assert record.getMessage() == "Job job-test failed"
    assert record.exc_info and record.exc_info[0] is ValueError
    assert record.app_id == "app-test"
    assert record.job_id == "job-test"


def test_context_logger_exception_honors_disabled_traceback(caplog) -> None:
    base = logging.getLogger("tests.context_no_exception")
    logger = logging_config.ContextLogger(base, {})
    with caplog.at_level(logging.ERROR, logger=base.name):
        logger.exception("Failure already handled", exc_info=False)
    assert caplog.records[-1].getMessage() == "Failure already handled"
    assert not caplog.records[-1].exc_info


def test_agent_conversation_filter_keeps_summary_and_gates_full_prompt_records() -> None:
    summary_record = logging.LogRecord(
        name="mozaiks.workflow.agent_messages",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="AGENT_CONTEXT_READY",
        args=(),
        exc_info=None,
    )
    summary_record.agent_transcript_scope = "summary"

    full_record = logging.LogRecord(
        name="mozaiks.workflow.agent_messages",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="AGENT_SYSTEM_PROMPT",
        args=(),
        exc_info=None,
    )
    full_record.agent_transcript_scope = "full"

    safe_filter = logging_config.AgentConversationFilter(allow_full_transcripts=False)
    full_filter = logging_config.AgentConversationFilter(allow_full_transcripts=True)

    assert safe_filter.filter(summary_record) is True
    assert safe_filter.filter(full_record) is False
    assert full_filter.filter(full_record) is True


def test_pretty_formatter_redacts_quoted_modern_api_keys() -> None:
    key = "sk-proj-test-redaction-key"
    record = logging.LogRecord(
        name="tests.logging",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg="LLM config: %s",
        args=({"api_key": key},),
        exc_info=None,
    )

    rendered = PrettyConsoleFormatter(no_color=True).format(record)

    assert key not in rendered
    assert "***REDACTED***" in rendered


@pytest.mark.parametrize("formatter", [PrettyConsoleFormatter(no_color=True), ProductionJSONFormatter()])
@pytest.mark.parametrize("message", [
    'WebSocket /stream?access_token=synthetic-credential&cursor=visible 403',
    'GET /stream?%61ccess%5Ftoken=synthetic-credential&cursor=visible HTTP/1.1',
    'GET /stream?cursor=visible&TOKEN=synthetic-credential HTTP/1.1',
    'Sec-WebSocket-Protocol: mozaiks.bearer.v1, synthetic-credential',
    'sec-websocket-protocol: other, mozaiks.bearer.v1, synthetic-credential',
    'sec-websocket-protocol: synthetic-credential',
    '{"access_token": "synthetic-credential", "cursor": "visible"}',
])
def test_formatters_redact_transport_credentials_and_preserve_context(formatter, message):
    record = logging.LogRecord("uvicorn.error", logging.DEBUG, __file__, 1, "%s", (message,), None)
    rendered = formatter.format(record)
    assert "synthetic-credential" not in rendered
    assert "***REDACTED***" in rendered
    assert "uvicorn.error" in rendered
    if "visible" in message:
        assert "visible" in rendered
    if isinstance(formatter, ProductionJSONFormatter):
        assert json.loads(rendered)["logger"] == "uvicorn.error"


@pytest.mark.parametrize("formatter", [PrettyConsoleFormatter(no_color=True), ProductionJSONFormatter()])
def test_formatters_redact_credentials_in_exception_output(formatter):
    try:
        raise ValueError("rejected /stream?access_token=synthetic-credential")
    except ValueError:
        record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "Handshake failed", (), sys.exc_info())
    rendered = formatter.format(record)
    assert "synthetic-credential" not in rendered
    assert "ValueError" in rendered and "Handshake failed" in rendered
    assert "***REDACTED***" in rendered


def test_json_formatter_handles_opaque_websocket_extra_without_raw_error_output(capsys):
    class Connection:
        def __repr__(self):
            raise AssertionError("Opaque extras must not invoke user representation")

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(ProductionJSONFormatter())
    record = logging.LogRecord("websockets.client", logging.DEBUG, __file__, 1,
                               "Sec-WebSocket-Protocol: %s", ("synthetic-credential",), None)
    record.websocket = Connection()
    try:
        handler.handle(record)
    finally:
        handler.close()
    assert capsys.readouterr().err == ""
    rendered = json.loads(output.getvalue())
    assert rendered["extra"]["websocket"] == "<Connection>"
    assert "synthetic-credential" not in rendered["msg"]
    assert "***REDACTED***" in rendered["msg"]


# ---------------------------------------------------------------------------
# Audit fallback: the rendered output must keep the sanitized evidence (#1003)
# ---------------------------------------------------------------------------

_DRIVER_SECRET = "SECRET-IN-DRIVER-ERROR"
_DIGEST = "a" * 64


class _FailingCollection:
    async def insert_one(self, document):
        raise RuntimeError(f"insert failed: payload={_DRIVER_SECRET}")


class _FakeMongoClient:
    """Subscriptable stand-in: client[database][collection] -> collection."""

    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, _database):
        return {"audit_log": self._collection}

    def __class_getitem__(cls, _item):
        return cls


def _fallback_message(rendered: str) -> str:
    """The human-readable message of the first rendered fallback line.

    JSON output also carries the whole audit document (unchanged by design), so
    checks about what the readable line includes look at ``msg`` only.
    """

    import json

    first = rendered.strip().splitlines()[0]
    if first.startswith("{"):
        return json.loads(first)["msg"]
    return first.split("  (audit_logger.py")[0]


def _rejection_shaped_record(**overrides):
    """An AuditRecord shaped like a structured-output rejection record."""

    from mozaiksai.core.audit.audit_logger import AuditEventKind, AuditRecord

    fields = {
        "kind": AuditEventKind.MODULE_ACTION,
        "actor_id": "user-7",
        "app_id": "app-1",
        "chat_id": "chat-9",
        "tenant_id": "tenant-3",
        "workspace_id": "ws-4",
        "resource": "contacts",
        "action": "create_contact",
        "inputs_hash": _DIGEST,
        "outcome": "fail",
        "error": "schema_violation",
        "extra": {
            "source": "unit",
            "trace_id": "trace-1",
            "model_name": "ContactOut",
            "failure_fields": ["payload.<redacted-field>:int_parsing", "name:string_type"],
        },
    }
    fields.update(overrides)
    return AuditRecord(**fields)


def _render_audit_fallback(
    monkeypatch, formatter, *, collection_available: bool, record=None
) -> str:
    """Run the real AuditLogger and return what a handler with ``formatter`` writes."""

    import asyncio
    import io

    from mozaiksai.core import core_config
    from mozaiksai.core.audit.audit_logger import AuditLogger

    client = _FakeMongoClient(_FailingCollection()) if collection_available else None
    monkeypatch.setattr(core_config, "get_mongo_client", lambda: client)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    audit_logger = logging_config.get_core_logger("audit")
    previous_level = audit_logger.level
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(handler)
    try:
        asyncio.run(AuditLogger().log(record or _rejection_shaped_record()))
    finally:
        audit_logger.removeHandler(handler)
        audit_logger.setLevel(previous_level)
    return stream.getvalue()


@pytest.mark.parametrize("collection_available", [False, True], ids=["no-collection", "insert-failure"])
@pytest.mark.parametrize(
    "make_formatter",
    [lambda: PrettyConsoleFormatter(no_color=True), logging_config.ProductionJSONFormatter],
    ids=["pretty", "json"],
)
def test_audit_fallback_keeps_sanitized_evidence_in_rendered_output(
    monkeypatch, make_formatter, collection_available
) -> None:
    rendered = _render_audit_fallback(
        monkeypatch, make_formatter(), collection_available=collection_available
    )

    for evidence in (
        "module.action",  # audit kind
        "user-7",  # initiating actor
        "app-1",
        "chat-9",
        "tenant-3",
        "ws-4",
        "contacts",  # workflow
        "create_contact",  # agent
        _DIGEST,  # digest of the input
        "schema_violation",  # failure category
        "payload.<redacted-field>:int_parsing",  # safe schema path
        "name:string_type",
        "unit",  # source
        "trace-1",
        "ContactOut",
    ):
        assert evidence in rendered, f"{evidence!r} missing from the rendered fallback:\n{rendered}"


@pytest.mark.parametrize(
    "make_formatter",
    [lambda: PrettyConsoleFormatter(no_color=True), logging_config.ProductionJSONFormatter],
    ids=["pretty", "json"],
)
def test_audit_insert_failure_does_not_render_raw_driver_exception_text(
    monkeypatch, make_formatter
) -> None:
    rendered = _render_audit_fallback(monkeypatch, make_formatter(), collection_available=True)

    assert "AUDIT_WRITE_FAIL" in rendered
    assert _DRIVER_SECRET not in rendered


@pytest.mark.parametrize("collection_available", [False, True], ids=["no-collection", "insert-failure"])
@pytest.mark.parametrize(
    "make_formatter",
    [lambda: PrettyConsoleFormatter(no_color=True), logging_config.ProductionJSONFormatter],
    ids=["pretty", "json"],
)
def test_audit_fallback_leaves_out_values_that_are_not_safe_tokens(
    monkeypatch, make_formatter, collection_available
) -> None:
    secret = "sk-live-secret-in-free-text"
    record = _rejection_shaped_record(
        chat_id="chat-9" + chr(10) + "FORGED-LINE",
        error="free text with spaces " + secret,
        extra={
            "source": "unit",
            "dispatch": {"params": secret},  # arbitrary content under a key the fallback never reads
            "failure_fields": ["ok:int_type", "bad value with spaces " + secret],
        },
    )

    rendered = _render_audit_fallback(
        monkeypatch, make_formatter(), collection_available=collection_available, record=record
    )

    message = _fallback_message(rendered)
    assert "ok:int_type" in message
    assert "FORGED-LINE" not in message
    assert "free text" not in message
    assert secret not in message
    # The readable line stays a single line; nothing forged can start a new one.
    assert chr(10) not in message


def test_audit_fallback_renders_nothing_extra_for_a_minimal_record(monkeypatch) -> None:
    from mozaiksai.core.audit.audit_logger import AuditEventKind, AuditRecord

    record = AuditRecord(kind=AuditEventKind.ADMIN_ACCESS, actor_id="admin-1", resource="/admin", action="GET")

    rendered = _render_audit_fallback(
        monkeypatch, PrettyConsoleFormatter(no_color=True), collection_available=False, record=record
    )

    assert "AUDIT kind=admin.access actor=admin-1 resource=/admin action=GET outcome=ok" in rendered
    for absent in ("digest=", "error=", "paths=", "tenant=", "chat_id="):
        assert absent not in rendered
