"""Safe diagnostics for real Factory failure shapes; no model or database calls."""

import hashlib
import json
import logging

import pytest

from factory_app.workflows.AppGenerator.tools import app_validation


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _report(caplog):
    line = next(record.message for record in caplog.records if record.message.startswith("APP_GENERATOR_VALIDATION "))
    return json.loads(line.removeprefix("APP_GENERATOR_VALIDATION "))


@pytest.mark.parametrize(
    ("error", "reason", "location"),
    [
        ("property 'image_url': request schemas (input_schema) cannot be nullable", "nullable_request_property", "input_schema"),
        ("read 'get_items' does not admit the observer default {id: string}", "missing_read_fixture", "module_contract.fixtures_yaml"),
    ],
)
def test_replays_task_rejection_and_dependency_without_logging_payload(caplog, error, reason, location):
    secret = "sk-private-example-do-not-log"
    context = {
        "chat_id": "synthetic-chat",
        "app_task_batch_results": {"_failed": {
            "inventory_contract": {
                "failure_kind": "output_rejected", "worker_agent": "ConfigMiddlewareAgent",
                "attempts": 2, "error": error + " " + secret,
                "rejected_output": {"prompt": secret},
            },
            "notes_page": {
                "failure_kind": "dependency_blocked", "worker_agent": "PageAgent",
                "attempts": 0, "blocked_by": ["inventory_contract"], "error": secret,
            },
        }},
    }
    with caplog.at_level(logging.WARNING, logger=app_validation.__name__):
        app_validation._record_validation_outcome(
            context, files={}, acceptance={"status": "failed", "validation_evidence": {"failed": ["module_wiring", secret]}},
            validation={"validation_status": "failed"}, passed=False,
        )

    report = _report(caplog)
    assert report["stage"] == "app_validation"
    assert report["outcome"] == "failed"
    assert report["chat_id_hash"] == _digest("synthetic-chat")
    assert report["failed_task_count"] == 2
    assert report["failed_gates"] == ["module_wiring"]
    assert report["validation_status"] == "failed"
    first, blocked = report["tasks"]
    assert first == {
        "task_id_hash": _digest("inventory_contract"), "worker_id_hash": _digest("ConfigMiddlewareAgent"),
        "failure_kind": "output_rejected", "attempts": 2, "reason": reason, "location": location,
    }
    assert blocked["blocked_by_hashes"] == [_digest("inventory_contract")]
    assert blocked["reason"] == "dependency_failed"
    assert context["app_validation_ends_run"] is True
    assert secret not in caplog.text
    assert "inventory_contract" not in caplog.text


def test_provider_task_failure_logs_status_only_and_preserves_recovery(caplog):
    secret = "API-KEY-PRIVATE-EXAMPLE"
    context = {"app_task_batch_results": {"_failed": {"plan_task": {
        "failure_kind": "execution_failed", "worker_agent": "Planner", "attempts": 1,
        "error": "google.genai.errors.ClientError: 400 Bad Request; " + secret,
    }}}}
    acceptance = {"status": "failed", "task_recovery_request": {"request_id": "request-1"}}
    with caplog.at_level(logging.WARNING, logger=app_validation.__name__):
        app_validation._record_validation_outcome(context, files={}, acceptance=acceptance, validation=None, passed=False)
    report = _report(caplog)
    assert report["outcome"] == "recovery_requested"
    assert report["tasks"][0]["provider_status"] == 400
    assert report["tasks"][0]["reason"] == "unclassified"
    assert context["app_validation_ends_run"] is False
    assert secret not in caplog.text


@pytest.mark.parametrize(
    ("error", "status"),
    [
        ("HTTP request timed out after 500 ms; ClientError: 429 Too Many Requests", 429),
        ("HTTP request timed out after 500 ms", None),
        ("HTTP 500 ms timeout", None),
        ("HTTP status 503; request size 400", 503),
    ],
)
def test_provider_status_requires_an_explicit_status_label(error, status):
    diagnostic = app_validation._task_failure_diagnostic("task", {"error": error})
    assert diagnostic.get("provider_status") == status


@pytest.mark.parametrize("validation,expected", [(None, None), ({}, None),
                                                ({"infrastructure_failure": True}, True),
                                                ({"infrastructure_failure": False}, False)])
def test_unknown_infrastructure_status_is_not_reported_as_false(caplog, validation, expected):
    with caplog.at_level(logging.WARNING, logger=app_validation.__name__):
        app_validation._record_validation_outcome(
            {}, files={}, acceptance={"status": "failed"}, validation=validation, passed=False,
        )
    assert _report(caplog)["infrastructure_failure"] is expected


def test_pending_acceptance_reports_only_known_skipped_gates(caplog):
    acceptance = {"status": "pending", "validation_evidence": {
        "failed": [], "skipped": ["app_runtime_smoke", "synthetic-private-gate"],
    }}
    with caplog.at_level(logging.WARNING, logger=app_validation.__name__):
        app_validation._record_validation_outcome({}, files={}, acceptance=acceptance, validation=None, passed=False)
    report = _report(caplog)
    assert report["acceptance_status"] == "pending"
    assert report["skipped_gates"] == ["app_runtime_smoke"]
    assert "synthetic-private-gate" not in caplog.text


def test_saved_two_attempt_failure_reports_both_reasons_without_candidates(caplog):
    secret = "synthetic-private-candidate"
    task_id = "inventory_contract"
    context = {"app_task_batch_results": {
        "_failed": {task_id: {
            "failure_kind": "output_rejected", "attempts": 2, "worker_agent": "ConfigMiddlewareAgent",
            "error": "read 'get_items' does not admit the observer default", "rejected_output": secret,
        }},
        "_meta": {"failure_history": {task_id: [
            {"failure_kind": "output_rejected", "attempts": 1, "worker_agent": "ConfigMiddlewareAgent",
             "error": "request schemas (input_schema) cannot be nullable", "rejected_output": secret},
            {"failure_kind": "output_rejected", "attempts": 2, "error": "does not admit the observer default"},
        ]}},
    }}
    with caplog.at_level(logging.WARNING, logger=app_validation.__name__):
        app_validation._record_validation_outcome(
            context, files={}, acceptance={"status": "failed"}, validation=None, passed=False,
        )
    task = _report(caplog)["tasks"][0]
    assert task["reason"] == "missing_read_fixture"
    assert len(task["previous_attempts"]) == 1
    assert task["previous_attempts"][0]["attempts"] == 1
    assert task["previous_attempts"][0]["reason"] == "nullable_request_property"
    assert secret not in caplog.text


@pytest.mark.parametrize("logger_also_fails", [False, True])
def test_diagnostic_failure_never_changes_validation(monkeypatch, caplog, logger_also_fails):
    def broken(*args, **kwargs):
        raise RuntimeError("secret diagnostic payload")

    monkeypatch.setattr(app_validation, "_log_validation_outcome", broken)
    if logger_also_fails:
        monkeypatch.setattr(app_validation.logger, "warning", broken)
    context = {}
    with caplog.at_level(logging.WARNING, logger=app_validation.__name__):
        app_validation._record_validation_outcome(
            context, files={}, acceptance={"status": "failed"}, validation=None, passed=False,
        )
    assert context["app_validation_ends_run"] is True
    assert context["app_build_failure_message"].startswith("The app build cannot continue")
    assert "secret diagnostic payload" not in caplog.text
    if not logger_also_fails:
        assert "APP_GENERATOR_VALIDATION diagnostic_unavailable" in caplog.text


def test_passed_validation_reports_success_without_exposing_untrusted_fields(caplog):
    context = {"chat_id": "person@example.test", "app_task_batch_results": {"_failed": {
        "sk-sensitive": {"failure_kind": ["unexpected"], "attempts": True, "worker_agent": "private@example.test",
                         "error": "Authorization: Bearer SECRET"},
    }}}
    with caplog.at_level(logging.INFO, logger=app_validation.__name__):
        app_validation._record_validation_outcome(context, files={}, acceptance={"status": "success"}, validation=None, passed=True)
    report = _report(caplog)
    assert report["outcome"] == "passed"
    assert report["tasks"][0]["failure_kind"] == "unknown"
    assert report["tasks"][0]["attempts"] is None
    for forbidden in ("person@example.test", "sk-sensitive", "private@example.test", "SECRET"):
        assert forbidden not in caplog.text
