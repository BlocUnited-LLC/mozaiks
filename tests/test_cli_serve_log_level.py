"""Tests for mozaiks serve — LOG_LEVEL normalization.

uvicorn requires a lowercase log level string.  The conventional env-var value
is uppercase ("INFO", "WARNING"), so the serve command must normalize it before
passing to uvicorn.run().  A missing or empty LOG_LEVEL must fall back to "info".
"""
from __future__ import annotations

import sys
from argparse import Namespace
from types import ModuleType
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs) -> Namespace:
    defaults = {
        "workspace": ".",
        "host": "platform",
        "port": 8000,
        "listen": "0.0.0.0",
        "reload": False,
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


def _fake_app_root(tmp_path):
    """Create a minimal app bundle so _resolve_app_root succeeds."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    return tmp_path


def _run_serve_with_captured_log_level(tmp_path, monkeypatch, log_level_env: str | None) -> str | None:
    """Run mozaiks serve with a fake uvicorn and return the log_level passed to uvicorn.run."""
    if log_level_env is None:
        monkeypatch.delenv("LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("LOG_LEVEL", log_level_env)

    captured: dict = {}

    # Build a fake uvicorn module that captures the log_level argument.
    fake_uvicorn = ModuleType("uvicorn")

    def _fake_run(app_module, **kwargs):
        captured["log_level"] = kwargs.get("log_level")

    fake_uvicorn.run = _fake_run  # type: ignore[attr-defined]

    # Patch the lazy import of uvicorn inside serve.run() by pre-inserting
    # the fake module into sys.modules before the function runs.
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    with (
        patch("mozaiks_cli.commands.serve._resolve_app_root", return_value=tmp_path / "app"),
        patch("mozaiks_cli.commands.sync_agent_guidance.auto_sync_agent_guidance"),
    ):
        # Re-import so the module-level ``import uvicorn`` path picks up the fake.
        # The lazy import inside run() uses ``import uvicorn`` which hits sys.modules.
        from mozaiks_cli.commands.serve import run
        run(_make_args(workspace=str(tmp_path)))

    return captured.get("log_level")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_serve_normalizes_uppercase_log_level(monkeypatch, tmp_path) -> None:
    """LOG_LEVEL=INFO must be lowercased to 'info' before calling uvicorn.run."""
    _fake_app_root(tmp_path)
    result = _run_serve_with_captured_log_level(tmp_path, monkeypatch, "INFO")
    assert result == "info", f"Expected 'info', got {result!r}"


def test_serve_defaults_to_info_when_log_level_unset(monkeypatch, tmp_path) -> None:
    """When LOG_LEVEL is not set, uvicorn must receive 'info'."""
    _fake_app_root(tmp_path)
    result = _run_serve_with_captured_log_level(tmp_path, monkeypatch, None)
    assert result == "info", f"Expected 'info', got {result!r}"


def test_serve_preserves_lowercase_log_level(monkeypatch, tmp_path) -> None:
    """An already-lowercase LOG_LEVEL must pass through unchanged."""
    _fake_app_root(tmp_path)
    result = _run_serve_with_captured_log_level(tmp_path, monkeypatch, "debug")
    assert result == "debug", f"Expected 'debug', got {result!r}"
