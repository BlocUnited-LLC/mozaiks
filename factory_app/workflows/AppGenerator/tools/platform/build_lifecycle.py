"""AppGenerator build lifecycle hooks.

Extends the shared platform build lifecycle with app_bundle artifact persistence.
Each workflow that produces a canonical artifact family overrides emit_build_completed
here to also call persist_summary_artifact() for its owned family.
"""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.platform.build_lifecycle import (  # noqa: F401
    build_export_download_url,
    emit_build_failed,
    emit_build_started,
    get_build_artifacts,
    runtime_public_base_url,
)
from factory_app.workflows._shared.platform.build_lifecycle import (
    emit_build_completed as _shared_emit_build_completed,
)


async def _read_build_mode(*, app_id: str, chat_id: str) -> str | None:
    """Read build_mode from the persisted chat session context variables."""
    try:
        from mozaiksai.core.core_config import get_mongo_client
        from mozaiksai.core.data.persistence.namespaces import (
            SYSTEM_DATABASE,
            RuntimeCollections,
        )
        from mozaiksai.core.multitenant import build_app_scope_filter

        client = get_mongo_client()
        coll = client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]
        doc = await coll.find_one(
            {"_id": str(chat_id), **build_app_scope_filter(str(app_id))},
            {"context_variables.build_mode": 1, "build_mode": 1},
        )
        if isinstance(doc, dict):
            raw_top_level = doc.get("build_mode")
            if isinstance(raw_top_level, str) and raw_top_level.strip():
                return raw_top_level.strip().lower()
            ctx = doc.get("context_variables")
            if isinstance(ctx, dict):
                raw = ctx.get("build_mode")
                if isinstance(raw, str):
                    return raw.strip().lower() or None
    except Exception:
        pass
    return None


async def _persist_app_bundle_artifact(
    *,
    app_id: str,
    chat_id: str | None,
    user_id: str | None,
    workflow_name: str,
    build_mode: str | None,
    artifact_store: Any | None = None,
) -> None:
    """Persist a versioned app_bundle summary artifact after AppGenerator completes.

    Pass ``artifact_store`` to direct artifact writes to a specific store instance
    without touching process-level state.
    """
    from mozaiksai.core.artifacts.summary_artifacts import persist_summary_artifact

    resolved_chat_id = (chat_id or "").strip() or None
    await persist_summary_artifact(
        app_id=app_id,
        artifact_kind="app_bundle",
        artifact_key="app_bundle",
        summary_payload={
            "source_workflow": workflow_name,
            "source_chat_id": resolved_chat_id,
        },
        source_workflow=workflow_name,
        source_chat_id=resolved_chat_id,
        author_user_id=(user_id or "").strip() or None,
        revision_mode=build_mode == "revision",
        input_artifact_kinds=(
            "design_docs",
            "subscription_contract",
            "workflow_bundle",
            "theme_capture",
        ),
        artifact_store=artifact_store,
    )


def _context_value(context_variables: Any, key: str) -> Any:
    if context_variables is None:
        return None
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except TypeError:
            return getter(key, None)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    return None


async def _verify_success_receipt_lineage(receipt: Any, *, app_id: str) -> bool:
    """Cold-verify a success receipt against the persisted build lineage.

    The receipt's references must resolve in the artifact store with the
    identities and lifecycle the receipt claims: the app-bundle BuildRecord
    (CURRENT for greenfield promotion, DRAFT only for revision records that
    carry parent lineage), the CURRENT AppContextVersion record, and the
    bundle digest when the record carries one. Any mismatch fails closed.
    """
    from logs.logging_config import get_core_logger
    from mozaiksai.core.artifacts import get_artifact_store

    log = get_core_logger("appgenerator_build_lifecycle")
    store = get_artifact_store()

    record = await store.get_build_record(
        app_id=app_id, build_record_id=receipt.build_record_id
    )
    if record is None:
        log.error(
            "BUILD_RECEIPT_VERIFICATION_FAILED: build record %s not found for app %s",
            receipt.build_record_id, app_id,
        )
        return False
    if record.build_family != "app_bundle":
        log.error(
            "BUILD_RECEIPT_VERIFICATION_FAILED: record %s family=%s is not app_bundle",
            record.id, record.build_family,
        )
        return False
    lifecycle = str(getattr(record.lifecycle_status, "value", record.lifecycle_status))
    if lifecycle != receipt.build_record_lifecycle:
        log.error(
            "BUILD_RECEIPT_VERIFICATION_FAILED: record %s lifecycle=%s receipt claims %s",
            record.id, lifecycle, receipt.build_record_lifecycle,
        )
        return False
    if lifecycle == "draft" and not record.parent_build_record_id:
        # A draft without parent lineage is an unaccepted greenfield record:
        # success requires CURRENT acceptance under the temporary authority.
        log.error(
            "BUILD_RECEIPT_VERIFICATION_FAILED: record %s is an unaccepted greenfield draft",
            record.id,
        )
        return False
    if receipt.bundle_digest:
        manifest_digests = {
            str(getattr(entry, "sha256", "") or "") for entry in record.files_manifest
        }
        if receipt.bundle_digest not in manifest_digests:
            log.error(
                "BUILD_RECEIPT_VERIFICATION_FAILED: bundle digest %s absent from record %s manifest",
                receipt.bundle_digest, record.id,
            )
            return False

    context_record = await store.get_build_record(
        app_id=app_id, build_record_id=receipt.app_context_record_id
    )
    if context_record is None:
        log.error(
            "BUILD_RECEIPT_VERIFICATION_FAILED: AppContextVersion record %s not found",
            receipt.app_context_record_id,
        )
        return False
    context_lifecycle = str(
        getattr(context_record.lifecycle_status, "value", context_record.lifecycle_status)
    )
    if context_lifecycle != "current":
        log.error(
            "BUILD_RECEIPT_VERIFICATION_FAILED: AppContextVersion record %s lifecycle=%s is not current",
            context_record.id, context_lifecycle,
        )
        return False
    return True


async def emit_build_completed(
    *,
    app_id: str,
    execution_id: str | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
    workflow_name: str,
    workflow_run_id: str | None = None,
    context_variables: Any = None,
    **kwargs: Any,
) -> str | None:
    """Emit lifecycle claims only from this run's immutable terminal receipt.

    generate_and_download issues exactly one run-bound terminal receipt after
    the complete persistence closure (bundle acceptance, BuildRecord,
    required AppContextVersion lineage, promotion) or a run-bound failure
    receipt on terminal build failure. The chat-scoped ``download_status``
    marker is a UI projection only — it never authorizes completion, so a
    prior run's stale state in the same chat can never complete this run.

    The hook fires on every completed workflow turn: no receipt, a receipt
    from a different run/app/workflow, an altered receipt, or a receipt whose
    lineage does not cold-verify all claim nothing (fail closed).
    """
    from logs.logging_config import get_core_logger
    from mozaiksai.core.artifacts.build_receipt import (
        TERMINAL_RECEIPT_CONTEXT_KEY,
        BuildFailureReceipt,
        ReceiptValidationError,
        parse_terminal_receipt,
    )

    log = get_core_logger("appgenerator_build_lifecycle")

    raw_receipt = _context_value(context_variables, TERMINAL_RECEIPT_CONTEXT_KEY)
    if raw_receipt is None:
        # Intermediate turn, cancelled download, or a run whose terminal tool
        # never ran: claim nothing, fabricate nothing.
        return None
    try:
        receipt = parse_terminal_receipt(raw_receipt)
    except ReceiptValidationError as exc:
        log.error("BUILD_RECEIPT_INVALID chat=%s: %s", chat_id, exc)
        return None

    completing_run = str(workflow_run_id or "").strip()
    if (
        not completing_run
        or receipt.workflow_run_id != completing_run
        or receipt.app_id != str(app_id)
        or receipt.workflow_name != str(workflow_name)
        or receipt.scope != "server"
    ):
        # A receipt for Run A cannot complete Run B even in the same chat.
        log.info(
            "BUILD_RECEIPT_RUN_MISMATCH chat=%s: receipt run=%s app=%s workflow=%s; "
            "completing run=%s app=%s workflow=%s — no lifecycle claim",
            chat_id, receipt.workflow_run_id, receipt.app_id, receipt.workflow_name,
            completing_run or "<missing>", app_id, workflow_name,
        )
        return None

    if isinstance(receipt, BuildFailureReceipt):
        return await emit_build_failed(
            app_id=app_id,
            execution_id=execution_id,
            chat_id=chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            workflow_run_id=receipt.workflow_run_id,
            build_id=receipt.build_id,
            error=f"terminal build failure: {receipt.error_code}",
            **kwargs,
        )

    if not await _verify_success_receipt_lineage(receipt, app_id=str(app_id)):
        return None

    outbox_event_id = await _shared_emit_build_completed(
        app_id=app_id,
        execution_id=execution_id,
        chat_id=chat_id,
        user_id=user_id,
        workflow_name=workflow_name,
        workflow_run_id=receipt.workflow_run_id,
        build_id=receipt.build_id,
        context_variables=context_variables,
        **kwargs,
    )

    try:
        build_mode = await _read_build_mode(app_id=app_id, chat_id=chat_id or "")
        await _persist_app_bundle_artifact(
            app_id=app_id,
            chat_id=chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            build_mode=build_mode,
        )
    except Exception as exc:
        from logs.logging_config import get_core_logger
        get_core_logger("appgenerator_build_lifecycle").warning(
            "[AppGenerator] app_bundle artifact persistence failed: %s", exc
        )

    return outbox_event_id


__all__ = [
    "emit_build_started",
    "emit_build_completed",
    "emit_build_failed",
    "get_build_artifacts",
    "runtime_public_base_url",
    "build_export_download_url",
]

