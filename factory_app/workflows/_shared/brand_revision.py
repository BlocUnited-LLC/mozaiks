"""Validate an app chat branding change and assign its child bundle to the owner's build."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.media_draft import register_media_draft
from factory_app.workflows._shared.platform.build_target import require_build_binding
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    BuildRecordStatus,
    BuildRecordValidationStatus,
)
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.runtime.app.theme_contract import APP_THEME_PATH
from mozaiksai.core.validation.compilation_inputs import resolve_app_compilation_inputs
from mozaiksai.core.validation.generated_app import validate_generated_app_candidate


async def _release_assigned_draft(child: BuildRecord, guard: Callable[[], None]) -> None:
    """Only a winning review assignment enables Studio acceptance of the checked bytes."""
    if child.validation_status == BuildRecordValidationStatus.PASSED:
        return
    if child.validation_status != BuildRecordValidationStatus.PENDING:
        raise ValueError("The assigned brand draft did not pass validation")
    guard()
    saved = await get_artifact_store().set_validation_status_for_build_record(
        app_id=child.app_id, build_record_id=child.id, validation_status=BuildRecordValidationStatus.PASSED,
    )
    if not saved:
        raise ValueError("The assigned brand draft could not be released for review")


async def stage_brand_revision(
    context: Any,
    *,
    run: tuple[str, str, str, str],
    workflow_sequence: str,
    selection: dict[str, str],
    apply: Callable[[dict[str, str | bytes]], Awaitable[dict[str, Any]]],
    guard: Callable[[], None],
    theme_capture: BuildRecord | None = None,
) -> BuildRecord:
    workflow, host, chat, user = run
    binding = require_build_binding(context)
    guard()
    if binding.phase != "refinement" or binding.target_app_id == host:
        raise ValueError("A brand change requires a registered app refinement")
    registry = AppRegistryService()
    record = (await registry.get_app_record(owner_user_id=user, build_registry_id=binding.build_registry_id)).get("app")
    current = (record or {}).get("current_build_run") or {}
    if (not record or record.get("chat_app_id") != host or record.get("app_id") != binding.target_app_id
            or record.get("owner_user_id") != user
            or current.get("build_id") != binding.build_id or current.get("active_chat_id") != chat
            or current.get("active_workflow_id") != workflow or current.get("phase") != binding.phase):
        raise ValueError("The registered brand refinement is no longer current")

    store = get_artifact_store()
    change_id = context.get("change_request_id")
    change = await store.get_change_request(app_id=binding.target_app_id, change_request_id=change_id)
    if (not change or not change_id or change.id != change_id or change.created_by_user_id != user
            or change.app_id != binding.target_app_id or change.build_family != "app_bundle" or change.build_key != "app_bundle"
            or not change.build_record_id or change.build_record_id != context.get("artifact_version_id")
            or change.router_decision.get("workflow_sequence") != workflow_sequence):
        raise ValueError("The change does not have an owned source refinement")
    parent = await store.get_build_record(app_id=binding.target_app_id, build_record_id=change.build_record_id)
    if (not parent or parent.app_id != binding.target_app_id or parent.id != change.build_record_id
            or parent.build_family != change.build_family or parent.build_key != change.build_key
            or parent.commit_metadata.author_user_id != user
            or parent.commit_metadata.metadata.get("build_registry_id") != binding.build_registry_id
            or parent.lifecycle_status not in {BuildRecordStatus.DRAFT, BuildRecordStatus.CURRENT, BuildRecordStatus.STALE}):
        raise ValueError("The selected source app bundle is unavailable")
    selection = {**selection, "change_request_id": change_id}
    input_versions = dict(parent.canonical_inputs_version)
    captured_theme = None
    if theme_capture is not None:
        captured = await store.get_build_record(app_id=binding.target_app_id, build_record_id=theme_capture.id)
        if (not captured or captured.id != theme_capture.id or captured.app_id != binding.target_app_id
                or captured.build_family != "theme_capture" or captured.build_key != "theme_capture"
                or captured.source_workflow != workflow or captured.source_chat_id != chat
                or captured.commit_metadata.author_user_id != user or selection.get("theme_capture_id") != captured.id
                or captured.lifecycle_status not in {BuildRecordStatus.DRAFT, BuildRecordStatus.CURRENT}):
            raise ValueError("The captured theme does not belong to this workflow chat")
        captured_theme = captured.commit_metadata.metadata.get("summary_payload", {}).get("theme_config")
        if not isinstance(captured_theme, dict) or not captured_theme:
            raise ValueError("The selected theme artifact has no theme configuration")
        input_versions["theme_capture"] = captured.id

    assigned_id = current.get("artifact_version_id")
    if assigned_id and not (theme_capture and assigned_id == theme_capture.id):
        saved = await store.get_build_record(app_id=binding.target_app_id, build_record_id=assigned_id)
        if (not saved or saved.id != assigned_id or saved.parent_build_record_id != parent.id
                or saved.app_id != parent.app_id or saved.build_family != parent.build_family or saved.build_key != parent.build_key
                or saved.source_chat_id != chat or saved.source_workflow != workflow or saved.commit_metadata.author_user_id != user
                or saved.lifecycle_status not in {BuildRecordStatus.DRAFT, BuildRecordStatus.CURRENT}
                or saved.canonical_inputs_version != input_versions
                or any(saved.commit_metadata.metadata.get(k) != v for k, v in binding.model_dump().items())
                or any(saved.commit_metadata.metadata.get("media_overlay", {}).get(k) != v for k, v in selection.items())):
            raise ValueError("The refinement already has a different draft")
        await _release_assigned_draft(saved, guard)
        return saved

    files, diagnostics = await read_artifact_bundle(parent, include_binary=True)
    if any(item.get("blocking") for item in diagnostics):
        raise ValueError("The source app bundle could not be verified")
    if APP_THEME_PATH in files:
        source_theme = files[APP_THEME_PATH]
        if not isinstance(source_theme, str) or not isinstance(json.loads(source_theme), dict):
            raise ValueError("The source app theme is invalid")
    details = await apply(files)
    if captured_theme is not None and json.loads(files[APP_THEME_PATH]) != captured_theme:
        raise ValueError("The proposed theme must match the saved theme artifact")
    compilation = await resolve_app_compilation_inputs(
        app_id=parent.app_id, build_record_id=parent.id, owner_user_id=user,
        build_registry_id=binding.build_registry_id, artifact_store=store,
    )
    validation = await validate_generated_app_candidate(
        files=files, app_id=parent.app_id, validation_strategy=parent.app_validation_strategy,
        capability_packs=parent.commit_metadata.metadata.get("capability_packs") or [], approved_compilation=compilation,
    )
    acceptance = validation.get("app_bundle_acceptance_result") or {}
    build = validation.get("app_validation_result") or {}
    if (validation.get("validation_status") != "passed" or acceptance.get("passed") is not True
            or acceptance.get("status") != "passed" or build.get("validation_status") != "passed"):
        failed_gates = sorted({str(test["gate"]) for test in acceptance.get("failed_tests", []) if test.get("gate")})
        raise ValueError(
            f"Brand revision validation {validation.get('validation_status')}, "
            f"acceptance {acceptance.get('status')}, build {build.get('validation_status')}, "
            f"failed gates {', '.join(failed_gates) or 'none recorded'}"
        )
    child, path = await register_media_draft(
        parent=parent, files=files, overlay={**details, **selection}, validation=validation, chat_id=chat, user_id=user,
        workflow_name=workflow, binding=binding, approved_compilation=compilation, scope_guard=guard,
        pending_review_assignment=True, canonical_inputs_version=input_versions,
    )
    guard()
    review = await registry.update_build_status(
        owner_user_id=user, build_registry_id=binding.build_registry_id, status="review", bundle_path=path,
        artifact_version_id=child.id, workflow_sequence=workflow_sequence, active_chat_id=chat, active_workflow_id=workflow,
        expected_build_id=binding.build_id, expected_active_chat_id=chat, expected_lifecycle_state="building",
        expected_artifact_version_id=assigned_id,
    )
    if not review.get("success"):
        await store.reject_artifact_version(
            app_id=child.app_id, artifact_version_id=child.id, reason="Another build claimed the brand review.",
        )
        raise ValueError("The brand refinement changed while validating the draft")
    await _release_assigned_draft(child, guard)
    return child
