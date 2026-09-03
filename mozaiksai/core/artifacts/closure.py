"""Cold verification of run-bound build authority against persisted records.

Self-digests on receipts and bindings prove body integrity only — they are
never authorization. Authorization requires resolving the exact persisted
records the claim references and requiring agreement on every canonical
dimension: app scope, family/key vocabulary, run/build binding, lifecycle
state, logical AppContextVersion identity, the AppContextVersion-to-
BuildRecord cross-reference, and the bundle digest. Any missing or
disagreeing value fails closed; a real but unrelated CURRENT record is
never sufficient, and records without a persisted run binding can never
satisfy a run-bound claim.
"""
from __future__ import annotations

from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.artifacts.build_receipt import (
    BuildFailureReceipt,
    BuildRevisionCandidateReceipt,
    BuildSuccessReceipt,
)
from mozaiksai.core.artifacts.run_build_binding import RunBuildBinding

logger = get_core_logger("build_closure_verification")


def _refuse(reason: str, *args: Any) -> bool:
    logger.error("BUILD_CLOSURE_VERIFICATION_FAILED: " + reason, *args)
    return False


def _resolve_store(store: Any | None) -> Any:
    if store is not None:
        return store
    from mozaiksai.core.artifacts import get_artifact_store

    return get_artifact_store()


async def verify_run_build_binding_closure(
    binding: RunBuildBinding,
    *,
    workflow_run_id: str,
    app_id: str,
    workflow_name: str,
    store: Any | None = None,
) -> bool:
    """Cold-verify that a binding is the exact current run's build relation.

    The binding must name the exact caller-supplied run/app/workflow, and the
    persisted BuildRecord it references must exist under that app scope and
    carry the identical run/build binding, family, key, and version stamped
    at creation. When the binding carries a bundle digest, that digest must
    be present in the persisted record manifest. Anything else fails closed:
    a binding for another run, another app, an unbound record, or a record
    whose stored identity disagrees never authorizes a build-specific claim.
    """
    expected_run = str(workflow_run_id or "").strip()
    if not expected_run or binding.workflow_run_id != expected_run:
        return _refuse(
            "binding run %r does not match current run %r",
            binding.workflow_run_id,
            expected_run or "<missing>",
        )
    if binding.app_id != str(app_id) or binding.workflow_name != str(workflow_name):
        return _refuse(
            "binding scope %s/%s does not match current %s/%s",
            binding.app_id,
            binding.workflow_name,
            app_id,
            workflow_name,
        )

    resolved_store = _resolve_store(store)
    record = await resolved_store.get_build_record(
        app_id=str(app_id), build_record_id=binding.build_record_id
    )
    if record is None:
        return _refuse(
            "build record %s not found for app %s", binding.build_record_id, app_id
        )

    record_run_id = str(getattr(record, "workflow_run_id", "") or "").strip()
    record_build_id = str(getattr(record, "build_id", "") or "").strip()
    if not record_run_id or record_run_id != binding.workflow_run_id:
        return _refuse(
            "record %s run binding %r does not match binding run %r",
            record.id,
            record_run_id or "<unbound>",
            binding.workflow_run_id,
        )
    if not record_build_id or record_build_id != binding.build_id:
        return _refuse(
            "record %s build binding %r does not match binding build %r",
            record.id,
            record_build_id or "<unbound>",
            binding.build_id,
        )
    if record.build_family != binding.build_family or record.build_key != binding.build_key:
        return _refuse(
            "record %s family/key %s/%s does not match binding %s/%s",
            record.id,
            record.build_family,
            record.build_key,
            binding.build_family,
            binding.build_key,
        )
    if int(record.version_number) != int(binding.version_number):
        return _refuse(
            "record %s version %s does not match binding version %s",
            record.id,
            record.version_number,
            binding.version_number,
        )
    if binding.bundle_digest is not None:
        manifest_digests = {
            str(getattr(entry, "sha256", "") or "") for entry in record.files_manifest
        }
        manifest_digests.discard("")
        if binding.bundle_digest not in manifest_digests:
            return _refuse(
                "binding bundle digest %s absent from record %s manifest",
                binding.bundle_digest,
                record.id,
            )
    return True


async def verify_terminal_receipt_closure(
    receipt: BuildSuccessReceipt | BuildRevisionCandidateReceipt,
    *,
    app_id: str,
    store: Any | None = None,
) -> bool:
    """Cold-verify a lineage receipt against the exact persisted closure.

    The referenced records must be the exact records persisted for this run
    and build, on every canonical dimension: server-owned app scope,
    family/key vocabulary, run/build binding, lifecycle state, logical
    AppContextVersion identity, the AppContextVersion-to-BuildRecord
    cross-reference, and the required bundle digest. Failure receipts carry
    no lineage and are never closure-verifiable.
    """
    if isinstance(receipt, BuildFailureReceipt):
        return _refuse("failure receipts carry no lineage closure to verify")

    resolved_store = _resolve_store(store)
    record = await resolved_store.get_build_record(
        app_id=str(app_id), build_record_id=receipt.build_record_id
    )
    if record is None:
        return _refuse(
            "build record %s not found for app %s", receipt.build_record_id, app_id
        )
    if record.build_family != "app_bundle" or record.build_key != "app_bundle":
        return _refuse(
            "record %s family/key %s/%s is not the canonical app_bundle vocabulary",
            record.id,
            record.build_family,
            record.build_key,
        )

    # Exact run/build binding: the record must have been created FOR this
    # run and build. Records without a persisted binding can never satisfy a
    # lineage receipt — there is no fallback for unbound records.
    record_run_id = str(getattr(record, "workflow_run_id", "") or "").strip()
    record_build_id = str(getattr(record, "build_id", "") or "").strip()
    if not record_run_id or record_run_id != receipt.workflow_run_id:
        return _refuse(
            "record %s run binding %r does not match receipt run %r",
            record.id,
            record_run_id or "<unbound>",
            receipt.workflow_run_id,
        )
    if not record_build_id or record_build_id != receipt.build_id:
        return _refuse(
            "record %s build binding %r does not match receipt build %r",
            record.id,
            record_build_id or "<unbound>",
            receipt.build_id,
        )

    lifecycle = str(getattr(record.lifecycle_status, "value", record.lifecycle_status))
    if isinstance(receipt, BuildRevisionCandidateReceipt):
        if lifecycle != "draft" or not record.parent_build_record_id:
            return _refuse(
                "revision candidate record %s must be a draft with parent lineage (lifecycle=%s)",
                record.id,
                lifecycle,
            )
    else:
        if lifecycle != "current":
            return _refuse(
                "success record %s lifecycle=%s is not CURRENT", record.id, lifecycle
            )

    # Required bundle digest agreement with the persisted manifest authority.
    manifest_digests = {
        str(getattr(entry, "sha256", "") or "") for entry in record.files_manifest
    }
    manifest_digests.discard("")
    if receipt.bundle_digest not in manifest_digests:
        return _refuse(
            "bundle digest %s absent from record %s manifest",
            receipt.bundle_digest,
            record.id,
        )

    context_record = await resolved_store.get_build_record(
        app_id=str(app_id), build_record_id=receipt.app_context_record_id
    )
    if context_record is None:
        return _refuse(
            "AppContextVersion record %s not found", receipt.app_context_record_id
        )
    if (
        context_record.build_family != "app_context_version"
        or context_record.build_key != "app_context_version"
    ):
        return _refuse(
            "record %s family/key %s/%s is not the canonical app_context_version vocabulary",
            context_record.id,
            context_record.build_family,
            context_record.build_key,
        )
    context_lifecycle = str(
        getattr(context_record.lifecycle_status, "value", context_record.lifecycle_status)
    )
    if context_lifecycle != "current":
        return _refuse(
            "AppContextVersion record %s lifecycle=%s is not current",
            context_record.id,
            context_lifecycle,
        )
    context_run_id = str(getattr(context_record, "workflow_run_id", "") or "").strip()
    context_build_id = str(getattr(context_record, "build_id", "") or "").strip()
    if not context_run_id or context_run_id != receipt.workflow_run_id:
        return _refuse(
            "AppContextVersion record %s run binding %r does not match receipt run %r",
            context_record.id,
            context_run_id or "<unbound>",
            receipt.workflow_run_id,
        )
    if not context_build_id or context_build_id != receipt.build_id:
        return _refuse(
            "AppContextVersion record %s build binding %r does not match receipt build %r",
            context_record.id,
            context_build_id or "<unbound>",
            receipt.build_id,
        )

    # Logical AppContextVersion identity and the exact cross-reference back to
    # the receipt's BuildRecord, read from the persisted summary payload.
    summary = getattr(getattr(context_record, "commit_metadata", None), "metadata", None)
    payload = summary.get("summary_payload") if isinstance(summary, dict) else None
    if not isinstance(payload, dict):
        return _refuse(
            "AppContextVersion record %s carries no persisted summary payload",
            context_record.id,
        )
    logical_id = str(payload.get("context_version_id") or "").strip()
    if not logical_id or logical_id != receipt.app_context_version_id:
        return _refuse(
            "AppContextVersion record %s logical id %r does not match receipt %r",
            context_record.id,
            logical_id or "<missing>",
            receipt.app_context_version_id,
        )
    payload_app_id = str(payload.get("app_id") or "").strip()
    if payload_app_id != str(app_id):
        return _refuse(
            "AppContextVersion record %s app scope %r does not match %r",
            context_record.id,
            payload_app_id,
            app_id,
        )
    bundle_refs = [
        ref
        for ref in (payload.get("artifact_refs") or [])
        if isinstance(ref, dict) and ref.get("artifact_kind") == "app_bundle"
    ]
    if not any(
        str(ref.get("artifact_version_id") or "").strip() == receipt.build_record_id
        for ref in bundle_refs
    ):
        return _refuse(
            "AppContextVersion record %s does not cross-reference BuildRecord %s",
            context_record.id,
            receipt.build_record_id,
        )
    return True


__all__ = [
    "verify_run_build_binding_closure",
    "verify_terminal_receipt_closure",
]
