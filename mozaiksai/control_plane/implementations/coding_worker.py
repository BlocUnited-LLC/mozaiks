from __future__ import annotations

import hashlib
import logging
import uuid
import zipfile

logger = logging.getLogger(__name__)
from pathlib import Path
from typing import Any, cast

from mozaiksai.control_plane.app_validation import run_current_app_source_validation
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    CodingWorkerResult,
    FileUpdate,
    StagedPatchProposal,
    safe_artifact_relpath,
)
from mozaiksai.control_plane.implementations.structured_coding_provider import (
    StructuredOutputCodingProvider,
)
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.ports import CodingExecutionProvider
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    get_artifact_store,
)
from mozaiksai.core.artifacts.content_store import get_artifact_content_store

_ELIGIBLE_CHANGE_CLASSES = {"patch"}
_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "workflow_bundle", "theme_config"}
# Deliberately excludes "e2b": this worker validates via local subprocesses
# (run_current_app_source_validation) and has no sandbox execution path, so a
# plan claiming e2b would stamp a strategy onto build records that never ran.
# Sandbox-backed worker validation is an AG2 SandboxCodeTool/SandboxPort
# integration tracked in docs/architecture/workflows/ag2-update-watchpoints.md.
_VALIDATION_STRATEGIES = {"skip", "local"}

# theme_config files live inside the app_bundle workspace. We alias the artifact
# kind so the coding worker can locate and patch these files without requiring a
# separate theme_config artifact store entry. Workspace scope tools fall back to
# the app_bundle artifact when a theme_config entry does not yet exist.
_ARTIFACT_KIND_ALIASES: dict[str, str] = {
    "theme_config": "app_bundle",
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
        source_validation_runner: Any = run_current_app_source_validation,
        artifact_store: Any = None,
        output_root: Any = None,
        provider: CodingExecutionProvider | None = None,
    ) -> None:
        self._provider: CodingExecutionProvider = provider or StructuredOutputCodingProvider(
            agent_factory=agent_factory,
            agent_runner=agent_runner,
            config_loader=config_loader,
            pack_loader=pack_loader,
            tool_executor=tool_executor,
        )
        self._config_loader = config_loader
        self._source_validation_runner = source_validation_runner
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

        proposal = await self._provider.execute(request)
        if proposal.status != "completed":
            return CodingWorkerResult(
                eligible=True,
                status="failed",
                provider=proposal.provider_id,
                error=proposal.error or "coding provider failed without an error message",
                metadata={"build_family": request.build_family, "change_class": request.change_class},
            )

        resolved_strategy = self._resolve_validation_strategy(
            request.validation_strategy or proposal.validation_strategy_hint or "skip"
        )
        try:
            resolved_plan = self._plan_from_proposal(
                request=request,
                proposal=proposal,
                resolved_strategy=resolved_strategy,
            )
            applied_files = {change.path: change.content for change in proposal.changed_files}
        except Exception as exc:
            return CodingWorkerResult(
                eligible=True,
                status="failed",
                provider=proposal.provider_id,
                error=str(exc),
                metadata={"build_family": request.build_family, "change_class": request.change_class},
            )
        merged_files = dict(request.files)
        merged_files.update(applied_files)

        # Resolve aliased artifact kinds so validation and persistence use the
        # backing store kind (e.g. theme_config → app_bundle).
        resolved_artifact_kind = _ARTIFACT_KIND_ALIASES.get(request.build_family, request.build_family)

        validation_result = None
        status = "planned"
        if resolved_artifact_kind == "app_bundle" and merged_files:
            validation_result = await self._run_source_validation(
                request=request,
                plan=resolved_plan,
                merged_files=merged_files,
                validation_strategy=resolved_strategy,
            )
            validation_status = str((validation_result or {}).get("validation_status") or "").strip().lower()
            if validation_status in {"passed", "skipped", "warning"}:
                status = "validated"
            elif validation_status == "failed":
                status = "failed"
            else:
                status = "planned"

        metadata = {
            "build_family": request.build_family,
            "change_class": request.change_class,
            "tool_context_loaded": proposal.tool_context_loaded,
            "applied_paths": sorted(applied_files.keys()),
            "applied_file_count": len(applied_files),
            "selected_file_paths": list((request.metadata or {}).get("selected_file_paths") or []),
        }
        if validation_result is not None:
            validation_status = str((validation_result or {}).get("validation_status") or "").strip().lower()
            metadata["validation_status"] = validation_status
            metadata["source_validation_status"] = validation_status
            metadata["source_validation_execution_mode"] = validation_result.get("execution_mode")
        if isinstance((request.metadata or {}).get("scope_proposal"), dict):
            metadata["scope_proposal"] = dict(request.metadata["scope_proposal"])
        persistence_error: str | None = None
        if status in {"validated", "failed"} and validation_result is not None:
            try:
                metadata.update(
                    await self._persist_refinement_artifact(
                        request=request,
                        resolved_artifact_kind=resolved_artifact_kind,
                        applied_files=applied_files,
                        merged_files=merged_files,
                        plan=resolved_plan,
                        validation_result=validation_result or {},
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
        return CodingWorkerPlan(
            summary=proposal.summary,
            owned_paths=list(proposal.owned_paths),
            updated_files=[
                FileUpdate(path=change.path, content=change.content) for change in proposal.changed_files
            ],
            validation_strategy=cast(Any, resolved_strategy),
            validation_commands=list(proposal.validation_commands),
            start_preview=bool(request.start_preview or proposal.start_preview),
            needs_human_review=proposal.needs_human_review,
            rationale=proposal.rationale,
        )

    @staticmethod
    def _resolve_validation_strategy(raw: str) -> str:
        normalized = str(raw or "").strip().lower() or "skip"
        return normalized if normalized in _VALIDATION_STRATEGIES else "skip"

    @staticmethod
    def _check_eligibility(request: CodingWorkerRequest) -> tuple[bool, str | None]:
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

    async def _run_source_validation(
        self,
        *,
        request: CodingWorkerRequest,
        plan: CodingWorkerPlan,
        merged_files: dict[str, str],
        validation_strategy: str,
    ) -> dict[str, Any]:
        options = self._source_validation_options(
            request=request,
            plan=plan,
            validation_strategy=validation_strategy,
        )
        result = await self._source_validation_runner(
            app_id=request.app_id,
            artifact_store=self._artifact_store,
            overlay_files=merged_files,
            allowed_kinds=options["allowed_kinds"],
            include_install=options["include_install"],
            max_commands=options["max_commands"],
            timeout_seconds=options["timeout_seconds"],
            confirm_execution=options["confirm_execution"],
            copy_workspace=True,
        )
        if hasattr(result, "model_dump"):
            payload = cast(dict[str, Any], result.model_dump(mode="json"))
        elif isinstance(result, dict):
            payload = dict(result)
        else:
            payload = {"validation_status": "failed", "error": str(result)}
        payload["validation_strategy"] = validation_strategy
        payload["requested_validation_commands"] = list(plan.validation_commands or [])
        payload["confirm_execution"] = options["confirm_execution"]
        return payload

    @staticmethod
    def _source_validation_options(
        *,
        request: CodingWorkerRequest,
        plan: CodingWorkerPlan,
        validation_strategy: str,
    ) -> dict[str, Any]:
        metadata = dict(request.metadata or {})
        context_seed = dict(request.context_seed or {})
        raw_kinds = metadata.get("validation_allowed_kinds") or metadata.get("allowed_validation_kinds")
        if raw_kinds is None:
            raw_kinds = context_seed.get("validation_allowed_kinds") or context_seed.get("allowed_validation_kinds")
        allowed_kinds = ScopedRefinementCodingWorker._string_list(raw_kinds)
        include_install = bool(metadata.get("validation_include_install") or context_seed.get("validation_include_install"))
        max_commands = ScopedRefinementCodingWorker._bounded_int(
            metadata.get("validation_max_commands") or context_seed.get("validation_max_commands"),
            default=4,
            minimum=1,
            maximum=12,
        )
        timeout_seconds = ScopedRefinementCodingWorker._bounded_int(
            metadata.get("validation_timeout_seconds") or context_seed.get("validation_timeout_seconds"),
            default=120,
            minimum=5,
            maximum=900,
        )
        confirm_execution = validation_strategy != "skip"
        if plan.start_preview:
            confirm_execution = True
        return {
            "allowed_kinds": allowed_kinds or None,
            "include_install": include_install,
            "max_commands": max_commands,
            "timeout_seconds": timeout_seconds,
            "confirm_execution": confirm_execution,
        }

    async def _persist_refinement_artifact(
        self,
        *,
        request: CodingWorkerRequest,
        resolved_artifact_kind: str,
        applied_files: dict[str, str],
        merged_files: dict[str, str],
        plan: CodingWorkerPlan,
        validation_result: dict[str, Any],
    ) -> dict[str, Any]:
        build_key = str(request.build_key or resolved_artifact_kind or "artifact").strip() or "artifact"
        bundle_token = uuid.uuid4().hex[:12]
        # Use the resolved (aliased) kind for file system layout so theme patches
        # land alongside app_bundle artifacts, not in a separate tree.
        bundle_root = self._output_root / request.app_id / resolved_artifact_kind / build_key / bundle_token
        workspace_dir = bundle_root / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        written_paths: list[str] = []
        for raw_path, content in merged_files.items():
            safe = safe_artifact_relpath(raw_path)
            if not safe:
                continue
            out_path = workspace_dir / safe
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(str(content), encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(
                    f"STAGING_WRITE_FAILED: could not write '{safe}' to staging workspace "
                    f"at {workspace_dir} — {exc}"
                ) from exc
            written_paths.append(safe)

        zip_path = bundle_root / "artifact.zip"
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
            "artifact_path": str(zip_path.resolve()),
            "workspace_dir": str(workspace_dir.resolve()),
            "bundle_mode": "staged_refinement_bundle",
            "applied_paths": sorted(applied_files.keys()),
            "validation_strategy": plan.validation_strategy,
            "validation_status": "",  # filled after status is resolved below
            "source_validation_status": str((validation_result or {}).get("validation_status") or "").strip().lower(),
            "source_validation_result": validation_result,
            "validation_result": validation_result,
            "source_surface": request.source_surface,
        }
        content_store = get_artifact_content_store()
        if content_store.backend_name != "local":
            try:
                content_ref = await content_store.put_bundle(
                    zip_bytes,
                    app_id=request.app_id,
                    artifact_version_id=f"pending_{zip_sha[:16]}",
                )
                commit_content_metadata["content_ref"] = content_ref
                commit_content_metadata["content_backend"] = content_store.backend_name
            except Exception as cs_exc:
                logger.warning(
                    "CONTENT_STORE_PUT_BUNDLE_FAILED app=%s: %s — using local path only",
                    request.app_id,
                    cs_exc,
                )

        artifact_store = self._artifact_store or get_artifact_store()
        validation_status = self._artifact_validation_status(validation_result)
        commit_content_metadata["validation_status"] = validation_status.value
        artifact_version = await artifact_store.create_build_record(
            app_id=request.app_id,
            build_family=resolved_artifact_kind,
            build_key=build_key,
            parent_build_record_id=request.build_record_id,
            source_workflow=request.requested_workflow_id or "control_plane_coding",
            source_chat_id=None,
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
            validation_status=validation_status,
            files_manifest=[
                {
                    "path": f"{build_key}/{zip_path.name}",
                    "sha256": zip_sha,
                    "size_bytes": zip_path.stat().st_size,
                    "content_type": "application/zip",
                }
            ],
            commit_metadata={
                "message": plan.summary,
                "source_workflow": request.requested_workflow_id or "control_plane_coding",
                "metadata": commit_content_metadata,
            },
        )
        return {
            "build_record_id": artifact_version.id,
            "artifact_path": str(zip_path.resolve()),
            "workspace_dir": str(workspace_dir.resolve()),
            "bundle_mode": "staged_refinement_bundle",
            "validation_status": validation_status.value,
            "source_validation_status": commit_content_metadata["source_validation_status"],
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
        for key in ("command_results", "fallback_checks"):
            values = validation_result.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "failed":
                    return str(item.get("reason") or f"{key} failed")
        errors = validation_result.get("errors")
        if isinstance(errors, list) and errors:
            return str(errors[0])
        return None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = str(item or "").strip().lower()
            if text and text not in result:
                result.append(text)
        return result

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
