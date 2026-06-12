from __future__ import annotations

import logging

from logs.logging_config import SafeRotatingFileHandler


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
