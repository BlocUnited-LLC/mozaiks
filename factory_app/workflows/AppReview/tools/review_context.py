from __future__ import annotations

from typing import Any

_VALIDATION_STATUSES = {"passed", "failed", "skipped", "pending"}
_LIFECYCLE_STATES = {
    "draft",
    "building",
    "review",
    "configuring",
    "deploying",
    "active",
    "needs_revision",
    "archived",
}


def _context_get(context_variables: Any | None, key: str) -> Any | None:
    if context_variables is None:
        return None
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key)
        except Exception:
            return None
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    return None


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
        except Exception:
            return


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _status(value: Any) -> str | None:
    normalized = _text(value)
    if normalized is None:
        return None
    lowered = normalized.lower()
    return lowered if lowered in _VALIDATION_STATUSES else normalized


def _lifecycle_state(value: Any) -> str:
    normalized = _text(value)
    if normalized is None:
        return "review"
    lowered = normalized.lower()
    return lowered if lowered in _LIFECYCLE_STATES else normalized


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "passed"}:
            return True
        if lowered in {"0", "false", "no", "failed"}:
            return False
    return None


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def build_review_summary_payload(context_variables: Any | None) -> dict[str, Any]:
    """Build the canonical AppReview summary payload from workflow context."""

    artifact_kind = _text(_context_get(context_variables, "artifact_kind")) or "app_bundle"
    artifact_key = _text(_context_get(context_variables, "artifact_key")) or artifact_kind
    lifecycle_state = _lifecycle_state(_context_get(context_variables, "lifecycle_state"))
    app_validation_status = _status(_context_get(context_variables, "app_validation_status"))
    app_bundle_acceptance_status = _status(
        _context_get(context_variables, "app_bundle_acceptance_status")
    )
    integration_tests_passed = _bool_or_none(
        _context_get(context_variables, "integration_tests_passed")
    )

    build_registry_id = _text(_context_get(context_variables, "build_registry_id"))
    bundle_path = _text(_context_get(context_variables, "bundle_path"))
    artifact_version_id = _text(_context_get(context_variables, "artifact_version_id"))

    promotion_blockers: list[str] = []
    revision_blockers: list[str] = []

    if not build_registry_id:
        promotion_blockers.append("missing_build_registry_id")
    if lifecycle_state != "review":
        promotion_blockers.append("lifecycle_not_review")
    if app_validation_status is None:
        promotion_blockers.append("missing_app_validation_status")
    elif app_validation_status == "failed":
        promotion_blockers.append("app_validation_failed")
    if app_bundle_acceptance_status is None:
        promotion_blockers.append("missing_app_bundle_acceptance_status")
    elif app_bundle_acceptance_status == "failed":
        promotion_blockers.append("app_bundle_acceptance_failed")
    elif app_bundle_acceptance_status != "passed":
        promotion_blockers.append("app_bundle_acceptance_not_passed")
    if integration_tests_passed is None:
        promotion_blockers.append("missing_integration_test_status")
    elif integration_tests_passed is False:
        promotion_blockers.append("integration_tests_failed")

    if lifecycle_state == "review" and not bundle_path:
        revision_blockers.append("missing_review_bundle_path")

    can_promote = not promotion_blockers
    can_revise = not revision_blockers

    return {
        "app_id": _text(_context_get(context_variables, "app_id")),
        "build_id": _text(_context_get(context_variables, "build_id")),
        "build_registry_id": build_registry_id,
        "artifact_kind": artifact_kind,
        "artifact_key": artifact_key,
        "artifact_version_id": artifact_version_id,
        "bundle_path": bundle_path,
        "lifecycle_state": lifecycle_state,
        "app_validation_status": app_validation_status,
        "app_validation_strategy_used": _text(
            _context_get(context_variables, "app_validation_strategy_used")
        ),
        "app_validation_preview_url": _text(
            _context_get(context_variables, "app_validation_preview_url")
        ),
        "app_bundle_acceptance_status": app_bundle_acceptance_status,
        "integration_tests_passed": integration_tests_passed,
        "promotion_blockers": promotion_blockers,
        "revision_blockers": revision_blockers,
        "review_ready": can_promote and can_revise,
        "can_promote": can_promote,
        "can_revise": can_revise,
    }


def build_refinement_request_payload(
    context_variables: Any | None,
    revision_request: str,
) -> dict[str, Any]:
    """Build the nested refinement_request payload used by Studio triggers."""

    summary = build_review_summary_payload(context_variables)
    extra = _compact(
        {
            "lifecycle_state": summary["lifecycle_state"],
            "bundle_path": summary["bundle_path"],
            "build_registry_id": summary["build_registry_id"],
            "build_id": summary["build_id"],
            "app_validation_status": summary["app_validation_status"],
            "app_validation_strategy_used": summary["app_validation_strategy_used"],
            "integration_tests_passed": summary["integration_tests_passed"],
        }
    )
    return _compact(
        {
            "raw_user_request": revision_request,
            "artifact_kind": summary["artifact_kind"],
            "artifact_key": summary["artifact_key"],
            "artifact_version_id": summary["artifact_version_id"],
            "source_surface": "app_review",
            "extra": extra,
        }
    )


def build_revision_event_payload(
    context_variables: Any | None,
    revision_request: str,
) -> dict[str, Any]:
    """Build the websocket event emitted after an AppReview revision request."""

    refinement_request = build_refinement_request_payload(context_variables, revision_request)
    return {
        "kind": "chat.revision_requested",
        "refinement_request": revision_request,
        "artifact_kind": refinement_request.get("artifact_kind"),
        "artifact_key": refinement_request.get("artifact_key"),
        "artifact_version_id": refinement_request.get("artifact_version_id"),
        "source_surface": refinement_request.get("source_surface"),
        "extra": refinement_request.get("extra", {}),
    }


def mark_review_revision_submitted(
    context_variables: Any | None,
    refinement_request: dict[str, Any],
) -> None:
    _context_set(context_variables, "revision_submitted", True)
    _context_set(context_variables, "refinement_request", refinement_request["raw_user_request"])
    _context_set(context_variables, "refinement_request_meta", refinement_request)
    _context_set(context_variables, "review_complete", True)


def mark_review_promoted(context_variables: Any | None) -> None:
    _context_set(context_variables, "lifecycle_state", "active")
    _context_set(context_variables, "review_complete", True)
