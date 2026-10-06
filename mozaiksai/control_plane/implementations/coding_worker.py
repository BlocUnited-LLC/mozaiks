from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
import zipfile
from copy import deepcopy

logger = logging.getLogger(__name__)
from pathlib import Path
from typing import Any, cast

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    CodingWorkerResult,
    FileUpdate,
    StagedPatchProposal,
)
from mozaiksai.control_plane.implementations.acp_coding_provider import (
    ACPCodingProvider,
    acp_available,
)
from mozaiksai.control_plane.implementations.coding_provider_selection import (
    ACP_FALLBACK_STATUSES,
    select_coding_provider,
)
from mozaiksai.control_plane.implementations.structured_coding_provider import (
    StructuredOutputCodingProvider,
)
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.ports import CodingExecutionProvider
from mozaiksai.control_plane.workspace import (
    harvest_coding_workspace,
    materialize_coding_workspace,
)
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    get_artifact_store,
)
from mozaiksai.core.artifacts.content_store import get_artifact_content_store
from mozaiksai.core.artifacts.models import (
    canonical_bundle_archive_path,
    resolve_canonical_bundle_entry,
)
from mozaiksai.core.validation.generated_app import validate_generated_app_candidate
from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
    APP_VALIDATION_STRATEGIES,
    resolve_app_validation_strategy,
)

_ELIGIBLE_CHANGE_CLASSES = {"patch"}
_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "workflow_bundle", "theme_capture"}


def resolve_coding_validation_strategy(raw: str | None) -> str:
    """Resolve operator/request policy before any coding provider executes."""
    normalized = str(raw or "").strip().lower() or None
    if normalized is not None and normalized not in APP_VALIDATION_STRATEGIES:
        raise ValueError(f"Unsupported coding validation strategy: {normalized}")
    return resolve_app_validation_strategy(requested=normalized)[0]

# Theme files live inside the app_bundle workspace. We alias the theme_capture artifact
# kind so the coding worker can locate and patch these files without requiring a
# separate staged theme workspace. Workspace scope tools fall back to the app_bundle
# artifact while theme_capture remains the persisted semantic input record.
_ARTIFACT_KIND_ALIASES: dict[str, str] = {
    "theme_capture": "app_bundle",
}


class ScopedRefinementCodingWorker:
    """First-party refinement coding worker for narrow refinement loops.

    This worker is intentionally conservative in v1. It is only eligible for
    scoped patch-style refinements and operates on explicit file payloads.

    The worker owns eligibility, validation, artifact persistence, and the
    checkpoint result shape. Producing the staged file changes is delegated to
    a :class:`~mozaiksai.control_plane.ports.CodingExecutionProvider`; the
    default provider is the single-shot structured-output provider.
    """

    def __init__(
        self,
        *,
        agent_factory: Any = None,
        agent_runner: AG2StructuredAgentRunner | None = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_refinement_harness,
        tool_executor: Any = None,
        candidate_validation_runner: Any = validate_generated_app_candidate,
        artifact_store: Any = None,
        output_root: Any = None,
        provider: CodingExecutionProvider | None = None,
        acp_provider: CodingExecutionProvider | None = None,
    ) -> None:
        self._structured_provider: CodingExecutionProvider = provider or StructuredOutputCodingProvider(
            agent_factory=agent_factory,
            agent_runner=agent_runner,
            config_loader=config_loader,
            pack_loader=pack_loader,
            tool_executor=tool_executor,
        )
        self._acp_provider: CodingExecutionProvider = acp_provider or ACPCodingProvider(
            config_loader=config_loader,
        )
        self._config_loader = config_loader
        self._candidate_validation_runner = candidate_validation_runner
        self._artifact_store = artifact_store
        self._output_root = Path(output_root) if output_root is not None else Path("generated_refinements")

    def enabled(self) -> bool:
        config = self._load_config()
        return bool(config.enabled and config.coding_enabled())

    async def execute(self, request: CodingWorkerRequest) -> CodingWorkerResult:
        eligible, blocked_reason = self._check_eligibility(request)
        if not eligible:
            return CodingWorkerResult(
                eligible=False,
                status="ineligible",
                blocked_reason=blocked_reason,
                metadata={"build_family": request.build_family, "change_class": request.change_class},
            )

        selection = select_coding_provider(request, self._load_config(), acp_importable=acp_available())
        provider_attempts: list[dict[str, str]] = []

        if selection.provider == "acp":
            proposal = await self._acp_provider.execute(request)
            provider_attempts.append(
                {"provider": proposal.provider_id, "status": proposal.status, "reason": selection.reason}
            )
            if proposal.status in ACP_FALLBACK_STATUSES:
                logger.warning(
                    "ACP_CODING_ATTEMPT_FELL_BACK app=%s status=%s: %s",
                    request.app_id,
                    proposal.status,
                    proposal.error,
                )
                proposal = await self._structured_provider.execute(request)
                provider_attempts.append(
                    {"provider": proposal.provider_id, "status": proposal.status, "reason": "acp_fallback"}
                )
        else:
            proposal = await self._structured_provider.execute(request)
            provider_attempts.append(
                {"provider": proposal.provider_id, "status": proposal.status, "reason": selection.reason}
            )

        return await self.finalize_proposal(request, proposal, provider_attempts=provider_attempts)

    async def finalize_proposal(
        self,
        request: CodingWorkerRequest,
        proposal: StagedPatchProposal,
        *,
        provider_attempts: list[dict[str, str]] | None = None,
    ) -> CodingWorkerResult:
        """Validate and persist scoped output without making another model call."""
        provider_attempts = list(provider_attempts or [])
        if proposal.status != "completed":
            return CodingWorkerResult(
                eligible=True,
                status="failed",
                provider=proposal.provider_id,
                error=proposal.error or "coding provider failed without an error message",
                metadata={
                    "build_family": request.build_family,
                    "change_class": request.change_class,
                    "coding_provider_attempts": provider_attempts,
                },
            )

        try:
            resolved_strategy = resolve_coding_validation_strategy(
                request.validation_strategy
            )
            resolved_plan = self._plan_from_proposal(
                request=request,
                proposal=proposal,
                resolved_strategy=resolved_strategy,
            )
            applied_files = {change.path: change.content for change in resolved_plan.updated_files}
        except Exception as exc:
            return CodingWorkerResult(
                eligible=True,
                status="failed",
                provider=proposal.provider_id,
                error=str(exc),
                metadata={
                    "build_family": request.build_family,
                    "change_class": request.change_class,
                    "coding_provider_attempts": provider_attempts,
                },
            )
        merged_files = dict(request.baseline_files if request.baseline_files is not None else request.files)
        merged_files.update(applied_files)

        # Resolve aliased artifact kinds so validation and persistence use the
        # backing store kind (e.g. theme_capture → app_bundle).
        resolved_artifact_kind = _ARTIFACT_KIND_ALIASES.get(request.build_family, request.build_family)

        validation_result = None
        capability_packs: list[dict[str, Any]] = []
        status = "planned"
        if resolved_artifact_kind == "app_bundle" and merged_files:
            try:
                capability_packs = await self._parent_capability_packs(request)
                validation_result = await self._run_candidate_validation(
                    request=request,
                    merged_files=merged_files,
                    validation_strategy=resolved_strategy,
                    capability_packs=capability_packs,
                )
            except Exception as exc:
                return CodingWorkerResult(
                    eligible=True, status="failed", provider=proposal.provider_id,
                    error=f"CANDIDATE_VALIDATION_FAILED: {exc}",
                    metadata={"coding_provider_attempts": provider_attempts},
                )
            validation_status = str((validation_result or {}).get("validation_status") or "").strip().lower()
            if validation_status == "passed":
                status = "validated"
            elif validation_status == "failed":
                status = "failed"
            else:
                status = "planned"

        provider_execution: dict[str, Any] = {
            "provider_id": proposal.provider_id,
            "provider_model": proposal.provider_model,
            "usage": proposal.usage,
            "attempts": provider_attempts,
            "events": [event.model_dump(mode="json") for event in proposal.provider_events],
        }
        metadata = {
            "build_family": request.build_family,
            "change_class": request.change_class,
            "coding_provider": provider_execution,
            "coding_provider_attempts": provider_attempts,
            "tool_context_loaded": proposal.tool_context_loaded,
            "applied_paths": sorted(applied_files.keys()),
            "applied_file_count": len(applied_files),
            "selected_file_paths": list((request.metadata or {}).get("selected_file_paths") or []),
        }
        if validation_result is not None:
            validation_status = str((validation_result or {}).get("validation_status") or "").strip().lower()
            metadata["validation_status"] = validation_status
            metadata["app_validation_status"] = validation_result.get("app_validation_result", {}).get("validation_status")
        if isinstance((request.metadata or {}).get("scope_proposal"), dict):
            metadata["scope_proposal"] = dict(request.metadata["scope_proposal"])
        persistence_error: str | None = None
        if validation_result is not None:
            try:
                metadata.update(
                    await self._persist_refinement_artifact(
                        request=request,
                        resolved_artifact_kind=resolved_artifact_kind,
                        applied_files=applied_files,
                        merged_files=merged_files,
                        plan=resolved_plan,
                        validation_result=validation_result or {},
                        provider_execution=provider_execution,
                        capability_packs=capability_packs,
                    )
                )
            except Exception as exc:
                persistence_error = f"ARTIFACT_PERSISTENCE_FAILED: {exc}"
                metadata["artifact_persistence_error"] = persistence_error
                logger.error(
                    "CODING_WORKER_PERSISTENCE_FAILED app=%s: %s",
                    request.app_id,
                    exc,
                    exc_info=True,
                )
                status = "failed"

        return CodingWorkerResult(
            eligible=True,
            status=status,  # type: ignore[arg-type]
            provider=proposal.provider_id,
            plan=resolved_plan,
            applied_files=applied_files,
            validation_result=validation_result,
            metadata=metadata,
            error=persistence_error
            or (self._validation_error(validation_result) if status == "failed" else None),
        )

    def _load_config(self) -> ControlPlaneConfig:
        config = self._config_loader()
        return config if isinstance(config, ControlPlaneConfig) else ControlPlaneConfig.model_validate(config)

    @staticmethod
    def _plan_from_proposal(
        *,
        request: CodingWorkerRequest,
        proposal: StagedPatchProposal,
        resolved_strategy: str,
    ) -> CodingWorkerPlan:
        """Reconstruct the checkpoint-facing plan from a provider proposal."""
        if any(change.op != "update" or change.content is None for change in proposal.changed_files):
            raise ValueError("Generated-app coding worker only supports updates to selected files")
        changed_paths = [change.path for change in proposal.changed_files]
        if not changed_paths:
            raise ValueError("Coding provider returned no file changes")
        if len(set(changed_paths)) != len(changed_paths):
            raise ValueError("Coding provider returned duplicate file changes")
        allowed_paths = set(request.files)
        outside_scope = sorted(set(changed_paths) - allowed_paths)
        if outside_scope:
            raise ValueError("Coding provider returned changes outside the approved file scope: " + ", ".join(outside_scope))
        unowned = sorted(set(changed_paths) - set(proposal.owned_paths))
        if unowned:
            raise ValueError("Coding provider returned changes outside its declared owned_paths: " + ", ".join(unowned))
        baseline = request.baseline_files if request.baseline_files is not None else request.files
        changes = [
            change for change in proposal.changed_files
            if change.path not in baseline or change.content != baseline[change.path]
        ]
        if not changes:
            raise ValueError("Coding provider returned no effective file changes")
        return CodingWorkerPlan(
            summary=proposal.summary,
            owned_paths=[path for path in proposal.owned_paths if path in allowed_paths],
            updated_files=[
                FileUpdate(path=change.path, content=cast(str, change.content)) for change in changes
            ],
            validation_strategy=cast(Any, resolved_strategy),
            validation_commands=list(proposal.validation_commands),
            start_preview=bool(request.start_preview or proposal.start_preview),
            needs_human_review=proposal.needs_human_review,
            rationale=proposal.rationale,
        )

    @staticmethod
    def _check_eligibility(request: CodingWorkerRequest) -> tuple[bool, str | None]:
        if request.read_only_files:
            return False, "generated-app coding worker cannot accept repository inspection files"
        try:
            resolve_coding_validation_strategy(request.validation_strategy)
        except ValueError as exc:
            return False, str(exc)
        if not str(request.app_id or "").strip():
            return False, "app_id is required"
        if str(request.change_class or "").strip().lower() not in _ELIGIBLE_CHANGE_CLASSES:
            return False, "coding worker only supports patch refinements in v1"
        if str(request.build_family or "").strip() not in _ELIGIBLE_ARTIFACT_KINDS:
            return False, "coding worker only supports app_bundle or workflow_bundle artifacts"
        if not str(request.build_record_id or "").strip():
            return False, "coding worker requires build_record_id for scoped refinement"
        if not isinstance(request.files, dict) or not request.files:
            return False, "coding worker requires explicit scoped files in v1"
        return True, None

    async def _parent_capability_packs(self, request: CodingWorkerRequest) -> list[dict[str, Any]]:
        """Only the saved parent may authorize pack-owned candidate outputs."""
        parent_id = request.build_record_id
        if parent_id is None or not parent_id.strip():
            raise ValueError("Refinement parent build record ID is required")
        artifact_store = self._artifact_store or get_artifact_store()
        parent = await artifact_store.get_build_record(
            app_id=request.artifact_app_id, build_record_id=parent_id,
        )
        if parent is None:
            raise ValueError("Refinement parent build record was not found for the target app")
        packs = parent.commit_metadata.metadata.get("capability_packs", [])
        if not isinstance(packs, list) or any(not isinstance(pack, dict) for pack in packs):
            raise ValueError("Refinement parent capability_packs must be a list of selected pack descriptors")
        return deepcopy(packs)

    async def _run_candidate_validation(
        self,
        *,
        request: CodingWorkerRequest,
        merged_files: dict[str, str],
        validation_strategy: str,
        capability_packs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Reject unsafe/secret-sensitive files before the validator can execute
        # the complete candidate. Provider context and commands are not policy.
        self._output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="validation-", dir=self._output_root.resolve()) as root:
            materialize_coding_workspace(merged_files, workspace_root=Path(root))
            result = await self._candidate_validation_runner(
                files=dict(merged_files),
                app_id=request.artifact_app_id,
                validation_strategy=validation_strategy,
                capability_packs=deepcopy(capability_packs),
                timeout_seconds=self._bounded_int(
                    request.metadata.get("validation_timeout_seconds"), default=120, minimum=5, maximum=900,
                ),
            )
        if not isinstance(result, dict):
            raise ValueError("Candidate validator returned no acceptance/build evidence")
        payload = dict(result)
        acceptance = payload.get("app_bundle_acceptance_result")
        build = payload.get("app_validation_result")
        if not isinstance(acceptance, dict) or not isinstance(build, dict):
            raise ValueError("Candidate validator returned incomplete acceptance/build evidence")
        if payload.get("validation_status") == "passed" and not (
            acceptance.get("passed") is True and build.get("validation_status") == "passed"
        ):
            raise ValueError("Candidate validator passed without completed acceptance and build checks")
        return payload

    async def _persist_refinement_artifact(
        self,
        *,
        request: CodingWorkerRequest,
        resolved_artifact_kind: str,
        applied_files: dict[str, str],
        merged_files: dict[str, str],
        plan: CodingWorkerPlan,
        validation_result: dict[str, Any],
        capability_packs: list[dict[str, Any]],
        provider_execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        build_key = str(request.build_key or resolved_artifact_kind or "artifact").strip() or "artifact"
        bundle_token = uuid.uuid4().hex[:12]
        # Use the resolved (aliased) kind for file system layout so theme patches
        # land alongside app_bundle artifacts, not in a separate tree.
        bundle_root = self._output_root / request.artifact_app_id / resolved_artifact_kind / build_key / bundle_token
        workspace_dir = bundle_root / "workspace"

        try:
            staged_workspace = materialize_coding_workspace(merged_files, workspace_root=workspace_dir)
        except OSError as exc:
            raise RuntimeError(
                f"STAGING_WRITE_FAILED: could not write staging workspace at {workspace_dir} — {exc}"
            ) from exc
        harvest = harvest_coding_workspace(staged_workspace)
        if not harvest.clean:
            details = "; ".join(f"{violation.path} ({violation.kind})" for violation in harvest.violations)
            raise RuntimeError(
                f"STAGING_VERIFICATION_FAILED: workspace harvest found scope violations: {details}"
            )
        written_paths = sorted(staged_workspace.editable_manifest)

        bundle_name = f"refinement_{bundle_token}"
        zip_path = bundle_root / f"{bundle_name}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for rel_path in sorted(written_paths):
                    zipf.write(workspace_dir / rel_path, arcname=rel_path)
        except OSError as exc:
            raise RuntimeError(
                f"ARTIFACT_ZIP_FAILED: could not create artifact bundle at {zip_path} — {exc}"
            ) from exc

        zip_bytes = zip_path.read_bytes()
        zip_sha = hashlib.sha256(zip_bytes).hexdigest()

        # Persist to content store if a non-local backend is configured.
        commit_content_metadata: dict[str, Any] = {
            **(request.run_build_binding.model_dump() if request.run_build_binding else {}),
            "artifact_path": str(zip_path.resolve()),
            "bundle_name": bundle_name,
            "workspace_dir": str(workspace_dir.resolve()),
            "bundle_mode": "staged_refinement_bundle",
            "applied_paths": sorted(applied_files.keys()),
            "validation_strategy": plan.validation_strategy,
            "validation_status": "",  # filled after status is resolved below
            "validation_result": validation_result,
            "app_bundle_acceptance": validation_result["app_bundle_acceptance_result"],
            "validation_evidence": validation_result["app_bundle_acceptance_result"].get("validation_evidence"),
            "app_validation_result": validation_result["app_validation_result"],
            "source_surface": request.source_surface,
            "staged_file_sha256": dict(staged_workspace.editable_manifest),
            "coding_provider": provider_execution,
            "capability_packs": deepcopy(capability_packs),
        }
        content_store = get_artifact_content_store()
        if content_store.backend_name != "local":
            try:
                content_ref = await content_store.put_bundle(
                    zip_bytes,
                    app_id=request.artifact_app_id,
                    artifact_version_id=f"pending_{zip_sha[:16]}",
                )
                if not content_ref:
                    raise ValueError("Configured content store returned no bundle reference")
                commit_content_metadata["content_ref"] = content_ref
                commit_content_metadata["content_backend"] = content_store.backend_name
            except Exception as cs_exc:
                raise RuntimeError(
                    f"CONTENT_STORE_PUT_BUNDLE_FAILED: {cs_exc}"
                ) from cs_exc

        artifact_store = self._artifact_store or get_artifact_store()
        validation_status = self._artifact_validation_status(validation_result)
        build_result = validation_result["app_validation_result"]
        commit_content_metadata["validation_status"] = validation_status.value
        artifact_version = await artifact_store.create_build_record(
            app_id=request.artifact_app_id,
            build_family=resolved_artifact_kind,
            build_key="app_bundle",
            parent_build_record_id=request.build_record_id,
            source_workflow=request.requested_workflow_id or "control_plane_coding",
            source_chat_id=None,
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
            validation_status=validation_status,
            app_validation_status=build_result.get("validation_status"),
            app_validation_strategy=build_result.get("validation_strategy"),
            sandbox_session_id=build_result.get("sandbox_session_id"),
            sandbox_provider=build_result.get("sandbox_provider"),
            files_manifest=[
                {
                    "path": canonical_bundle_archive_path(bundle_name),
                    "sha256": zip_sha,
                    "size_bytes": zip_path.stat().st_size,
                    "content_type": "application/zip",
                }
            ],
            commit_metadata={
                "message": plan.summary,
                "author_user_id": request.user_id,
                "source_workflow": request.requested_workflow_id or "control_plane_coding",
                "metadata": commit_content_metadata,
            },
        )
        if resolve_canonical_bundle_entry(artifact_version).sha256 != zip_sha:
            raise RuntimeError("Saved candidate archive identity differs from the validated bundle")
        return {
            "build_record_id": artifact_version.id,
            "artifact_path": str(zip_path.resolve()),
            "workspace_dir": str(workspace_dir.resolve()),
            "bundle_mode": "staged_refinement_bundle",
            "validation_status": validation_status.value,
            "app_validation_status": build_result.get("validation_status"),
        }

    @staticmethod
    def _artifact_validation_status(validation_result: dict[str, Any]) -> ArtifactValidationStatus:
        status = str((validation_result or {}).get("validation_status") or "").strip().lower()
        if status == "passed":
            return ArtifactValidationStatus.PASSED
        if status == "skipped":
            return ArtifactValidationStatus.SKIPPED
        if status == "failed":
            return ArtifactValidationStatus.FAILED
        return ArtifactValidationStatus.PENDING

    @staticmethod
    def _validation_error(validation_result: dict[str, Any] | None) -> str | None:
        if not isinstance(validation_result, dict):
            return None
        if validation_result.get("error"):
            return str(validation_result["error"])
        errors = validation_result.get("errors")
        if isinstance(errors, list) and errors:
            return str(errors[0])
        return None

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(number, maximum))


_coding_worker: ScopedRefinementCodingWorker | None = None


def get_coding_worker() -> ScopedRefinementCodingWorker:
    global _coding_worker
    if _coding_worker is None:
        _coding_worker = ScopedRefinementCodingWorker()
    return _coding_worker
