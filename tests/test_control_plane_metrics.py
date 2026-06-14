"""Unit tests for mozaiksai.control_plane.metrics.

Tests ControlPlaneBuildTimer, log_build_outcome, and check_token_usage
without any network calls, AG2 dependencies, or live LLMs.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from mozaiksai.control_plane.metrics import (
    ControlPlaneBuildTimer,
    check_token_usage,
    log_build_outcome,
    _token_anomaly_threshold,
)


# ---------------------------------------------------------------------------
# _token_anomaly_threshold
# ---------------------------------------------------------------------------


class TestTokenAnomalyThreshold:
    def test_returns_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", raising=False)
        assert _token_anomaly_threshold() == 50_000

    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", "100000")
        assert _token_anomaly_threshold() == 100_000

    def test_clamps_to_zero_when_negative(self, monkeypatch):
        monkeypatch.setenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", "-1")
        assert _token_anomaly_threshold() == 0

    def test_falls_back_to_default_on_invalid_value(self, monkeypatch):
        monkeypatch.setenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", "not-a-number")
        assert _token_anomaly_threshold() == 50_000


# ---------------------------------------------------------------------------
# ControlPlaneBuildTimer
# ---------------------------------------------------------------------------


class TestControlPlaneBuildTimer:
    def test_logs_start_at_debug_level(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="mozaiksai.control_plane.metrics"):
            with ControlPlaneBuildTimer("test_stage", request_id="req-1", app_id="app-1"):
                pass
        start_records = [r for r in caplog.records if "cp_stage_start" in r.getMessage()]
        assert start_records, "Expected cp_stage_start debug log"

    def test_logs_end_at_info_level(self, caplog):
        with caplog.at_level(logging.INFO, logger="mozaiksai.control_plane.metrics"):
            with ControlPlaneBuildTimer("test_stage", request_id="req-1"):
                pass
        end_records = [r for r in caplog.records if "cp_stage_end" in r.getMessage()]
        assert end_records, "Expected cp_stage_end info log"

    def test_end_record_contains_duration(self, caplog):
        with caplog.at_level(logging.INFO, logger="mozaiksai.control_plane.metrics"):
            with ControlPlaneBuildTimer("test_stage"):
                pass
        end_records = [r for r in caplog.records if "cp_stage_end" in r.getMessage()]
        assert end_records
        record = end_records[0]
        assert record.__dict__.get("cp_duration_ms") is not None
        assert isinstance(record.__dict__["cp_duration_ms"], int)
        assert record.__dict__["cp_duration_ms"] >= 0

    def test_end_record_contains_stage_name(self, caplog):
        with caplog.at_level(logging.INFO, logger="mozaiksai.control_plane.metrics"):
            with ControlPlaneBuildTimer("route_refinement"):
                pass
        end_records = [r for r in caplog.records if "cp_stage_end" in r.getMessage()]
        assert end_records
        assert end_records[0].__dict__["cp_stage"] == "route_refinement"

    def test_end_record_outcome_is_ok(self, caplog):
        with caplog.at_level(logging.INFO, logger="mozaiksai.control_plane.metrics"):
            with ControlPlaneBuildTimer("test_stage"):
                pass
        end_records = [r for r in caplog.records if "cp_stage_end" in r.getMessage()]
        assert end_records
        assert end_records[0].__dict__["cp_outcome"] == "ok"

    def test_exception_logs_warning_and_reraises(self, caplog):
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            with pytest.raises(ValueError, match="boom"):
                with ControlPlaneBuildTimer("failing_stage", request_id="req-err"):
                    raise ValueError("boom")
        error_records = [r for r in caplog.records if "cp_stage_error" in r.getMessage()]
        assert error_records, "Expected cp_stage_error warning on exception"
        record = error_records[0]
        assert record.__dict__["cp_outcome"] == "error"
        assert "boom" in record.__dict__["cp_error"]

    def test_exception_record_contains_duration(self, caplog):
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            with pytest.raises(RuntimeError):
                with ControlPlaneBuildTimer("failing_stage"):
                    raise RuntimeError("fail")
        error_records = [r for r in caplog.records if "cp_stage_error" in r.getMessage()]
        assert error_records
        assert error_records[0].__dict__["cp_duration_ms"] >= 0

    def test_optional_request_id_and_app_id_attached(self, caplog):
        with caplog.at_level(logging.INFO, logger="mozaiksai.control_plane.metrics"):
            with ControlPlaneBuildTimer("s", request_id="req-xyz", app_id="app-abc"):
                pass
        end_records = [r for r in caplog.records if "cp_stage_end" in r.getMessage()]
        assert end_records
        record = end_records[0]
        assert record.__dict__["cp_request_id"] == "req-xyz"
        assert record.__dict__["cp_app_id"] == "app-abc"

    def test_works_without_optional_fields(self, caplog):
        with caplog.at_level(logging.INFO, logger="mozaiksai.control_plane.metrics"):
            with ControlPlaneBuildTimer("bare_stage"):
                pass
        end_records = [r for r in caplog.records if "cp_stage_end" in r.getMessage()]
        assert end_records


# ---------------------------------------------------------------------------
# log_build_outcome
# ---------------------------------------------------------------------------


class TestLogBuildOutcome:
    def test_ok_outcome_logs_at_info(self, caplog):
        with caplog.at_level(logging.INFO, logger="mozaiksai.control_plane.metrics"):
            log_build_outcome(
                outcome="ok",
                request_id="req-1",
                app_id="app-1",
                change_class="patch",
                workflow_sequence="app_revision",
                duration_ms=500,
            )
        info_records = [r for r in caplog.records if "cp_build_outcome" in r.getMessage()]
        assert info_records
        record = info_records[0]
        assert record.__dict__["cp_outcome"] == "ok"
        assert record.__dict__["cp_change_class"] == "patch"
        assert record.__dict__["cp_workflow_sequence"] == "app_revision"

    def test_error_outcome_logs_at_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            log_build_outcome(
                outcome="error",
                request_id="req-2",
                app_id="app-2",
                error="LLM returned invalid JSON",
            )
        warn_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "cp_build_outcome" in r.getMessage()
        ]
        assert warn_records
        record = warn_records[0]
        assert record.__dict__["cp_outcome"] == "error"
        assert record.__dict__["cp_error"] == "LLM returned invalid JSON"

    def test_skipped_outcome_logs_at_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="mozaiksai.control_plane.metrics"):
            log_build_outcome(outcome="skipped", request_id="req-3")
        debug_records = [r for r in caplog.records if "cp_build_outcome" in r.getMessage()]
        assert debug_records

    def test_extra_fields_merged(self, caplog):
        with caplog.at_level(logging.INFO, logger="mozaiksai.control_plane.metrics"):
            log_build_outcome(
                outcome="ok",
                extra={"custom_field": "custom_value"},
            )
        info_records = [r for r in caplog.records if "cp_build_outcome" in r.getMessage()]
        assert info_records
        assert info_records[0].__dict__["custom_field"] == "custom_value"


# ---------------------------------------------------------------------------
# check_token_usage
# ---------------------------------------------------------------------------


class TestCheckTokenUsage:
    def test_no_warning_when_under_threshold(self, caplog, monkeypatch):
        monkeypatch.setenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", "1000")
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            check_token_usage(stage="coding_worker", token_count=500)
        anomaly_records = [r for r in caplog.records if "cp_token_anomaly" in r.getMessage()]
        assert not anomaly_records

    def test_warning_when_over_threshold(self, caplog, monkeypatch):
        monkeypatch.setenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", "1000")
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            check_token_usage(stage="coding_worker", token_count=1500)
        anomaly_records = [r for r in caplog.records if "cp_token_anomaly" in r.getMessage()]
        assert anomaly_records
        record = anomaly_records[0]
        assert record.__dict__["cp_token_count"] == 1500
        assert record.__dict__["cp_threshold"] == 1000

    def test_no_warning_when_token_count_none(self, caplog):
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            check_token_usage(stage="coding_worker", token_count=None)
        anomaly_records = [r for r in caplog.records if "cp_token_anomaly" in r.getMessage()]
        assert not anomaly_records

    def test_no_warning_when_threshold_zero(self, caplog, monkeypatch):
        monkeypatch.setenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", "0")
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            check_token_usage(stage="coding_worker", token_count=999_999)
        anomaly_records = [r for r in caplog.records if "cp_token_anomaly" in r.getMessage()]
        assert not anomaly_records

    def test_explicit_threshold_override(self, caplog, monkeypatch):
        monkeypatch.delenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", raising=False)
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            check_token_usage(
                stage="coding_worker",
                token_count=200,
                threshold=100,
            )
        anomaly_records = [r for r in caplog.records if "cp_token_anomaly" in r.getMessage()]
        assert anomaly_records
        assert anomaly_records[0].__dict__["cp_threshold"] == 100

    def test_structured_fields_present(self, caplog, monkeypatch):
        monkeypatch.setenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", "50")
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            check_token_usage(
                stage="route_refinement",
                token_count=100,
                request_id="req-anomaly",
                app_id="app-xyz",
            )
        anomaly_records = [r for r in caplog.records if "cp_token_anomaly" in r.getMessage()]
        assert anomaly_records
        record = anomaly_records[0]
        assert record.__dict__["cp_stage"] == "route_refinement"
        assert record.__dict__["cp_request_id"] == "req-anomaly"
        assert record.__dict__["cp_app_id"] == "app-xyz"
        assert record.__dict__["cp_outcome"] == "anomaly"

    def test_equal_to_threshold_does_not_warn(self, caplog, monkeypatch):
        monkeypatch.setenv("CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD", "1000")
        with caplog.at_level(logging.WARNING, logger="mozaiksai.control_plane.metrics"):
            check_token_usage(stage="test", token_count=1000)
        anomaly_records = [r for r in caplog.records if "cp_token_anomaly" in r.getMessage()]
        assert not anomaly_records, "Threshold is > not >= — equal count should not warn"
