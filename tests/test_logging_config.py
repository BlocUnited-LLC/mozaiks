from __future__ import annotations

import logging

from logs import logging_config
from logs.logging_config import PrettyConsoleFormatter, SafeRotatingFileHandler


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
    key = "sk-proj-abcdefghijklmno123456789"
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
