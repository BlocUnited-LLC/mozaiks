from __future__ import annotations

import hashlib
import json
import logging
import uuid
import zipfile

logger = logging.getLogger(__name__)
from pathlib import Path, PurePosixPath
from typing import Any

from mozaiksai.control_plane.app_validation import run_current_app_source_validation
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    CodingWorkerResult,
    ControlPlaneToolCall,
    ControlPlaneToolContext,
    FileUpdate,
)
from mozaiksai.control_plane.executor import ControlPlaneToolExecutor
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    get_artifact_store,
)
from mozaiksai.core.artifacts.content_store import get_artifact_content_store

_ELIGIBLE_CHANGE_CLASSES = {"patch"}
_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "workflow_bundle", "theme_config"}
_VALIDATION_STRATEGIES = {"skip", "local", "e2b"}
_CHECKPOINT_EVENT = "coding_requested"
_MODEL_VALIDATION_COMMAND_MAX_LENGTH = 240

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
    ) -> None:
        self._agent_runner = agent_runner or AG2StructuredAgentRunner(agent_factory=agent_factory)
        self._config_loader = config_loader
        self._pack_loader = pack_loader
        self._tool_executor = tool_executor or ControlPlaneToolExecutor(pack_loader=pack_loader)
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
                metadata={"artifact_kind": request.artifact_kind, "change_class": request.change_class},
            )

        try:
            llm_config = self._load_config().resolve_capability_llm_config("coding") or {}
            system_prompt = self._load_system_prompt()
            control_plane_context = await self._load_control_plane_context(request)
            user_prompt = self._build_user_prompt(request=request, control_plane_context=control_plane_context)
            plan = await self._agent_runner.run(
                agent_name="CodingWorker",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                llm_config=llm_config,
                response_schema=CodingWorkerPlan,
            )
        except Exception as exc:
            return CodingWorkerResult(
                eligible=True,
                status="failed",
                error=str(exc),
                metadata={"artifact_kind": request.artifact_kind, "change_class": request.change_class},
            )

        try:
            resolved_plan = self._normalize_plan(plan)
            applied_files = self._resolve_updated_files(request=request, plan=resolved_plan)
        except Exception as exc:
            return CodingWorkerResult(
                eligible=True,
                status="failed",
                error=str(exc),
                metadata={"artifact_kind": request.artifact_kind, "change_class": request.change_class},
            )

        resolved_strategy = self._resolve_validation_strategy(
            request.validation_strategy or resolved_plan.validation_strategy or "skip"
        )
        resolved_plan = resolved_plan.model_copy(
            update={
                "validation_strategy": resolved_strategy,
                "start_preview": bool(request.start_preview or resolved_plan.start_preview),
            }
        )
        merged_files = dict(request.files)
        merged_files.update(applied_files)

        # Resolve aliased artifact kinds so validation and persistence use the
        # backing store kind (e.g. theme_config → app_bundle).
        resolved_artifact_kind = _ARTIFACT_KIND_ALIASES.get(request.artifact_kind, request.artifact_kind)

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
            "artifact_kind": request.artifact_kind,
            "change_class": request.change_class,
            "tool_context_loaded": bool(control_plane_context),
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

    def _load_pack(self) -> LoadedControlPlanePack:
        pack = self._pack_loader()
        return pack if isinstance(pack, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(pack)

    def _load_system_prompt(self) -> str:
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.prompt_id:
            raise RuntimeError(
                f"Selected refinement harness does not declare a '{_CHECKPOINT_EVENT}' checkpoint with prompt_id"
            )
        prompt = pack.prompt_by_id(checkpoint.prompt_id)
        if prompt is None:
            raise RuntimeError(f"Coding prompt '{checkpoint.prompt_id}' was not found in prompts.yaml")
        return prompt.content

    async def _load_control_plane_context(self, request: CodingWorkerRequest) -> dict[str, Any]:
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.tool_ids:
            return {}

        context = ControlPlaneToolContext(
            checkpoint=_CHECKPOINT_EVENT,
            app_id=request.app_id,
            user_id=request.user_id,
            artifact_kind=request.artifact_kind,
            artifact_key=request.artifact_key,
            artifact_version_id=request.artifact_version_id,
            requested_workflow_id=request.requested_workflow_id,
            source_surface=request.source_surface,
            raw_user_request=request.raw_user_request,
            extra=dict(request.metadata or {}),
        )
        results: dict[str, Any] = {}
        for tool_id in checkpoint.tool_ids:
            result = await self._tool_executor.execute_tool(
                ControlPlaneToolCall(tool_id=tool_id, target=_CHECKPOINT_EVENT),
                context=context,
            )
            if result.success:
                results[tool_id] = result.output
            else:
                results[tool_id] = {"error": result.error or "tool_execution_failed"}
        return results

    @staticmethod
    def _normalize_plan(plan: CodingWorkerPlan) -> CodingWorkerPlan:
        owned_paths = []
        seen_owned: set[str] = set()
        for raw_path in plan.owned_paths:
            safe = ScopedRefinementCodingWorker._safe_relpath(raw_path)
            if safe and safe not in seen_owned:
                owned_paths.append(safe)
                seen_owned.add(safe)

        seen_updated: set[str] = set()
        normalized_files: list[FileUpdate] = []
        for file_update in plan.updated_files or []:
            safe = ScopedRefinementCodingWorker._safe_relpath(file_update.path)
            if not safe or safe in seen_updated:
                continue
            normalized_files.append(FileUpdate(path=safe, content=str(file_update.content)))
            seen_updated.add(safe)
            if safe not in seen_owned:
                owned_paths.append(safe)
                seen_owned.add(safe)

        commands = []
        for raw_command in plan.validation_commands or []:
            command = str(raw_command or "").strip()
            if command and ScopedRefinementCodingWorker._safe_model_validation_command_hint(command):
                commands.append(command)
            elif command:
                logger.warning(
                    "CODING_WORKER: discarded unsafe model validation_command hint: %r",
                    command,
                )

        return plan.model_copy(
            update={
                "owned_paths": owned_paths,
                "updated_files": normalized_files,
                "validation_commands": commands,
            }
        )

    @staticmethod
    def _resolve_updated_files(*, request: CodingWorkerRequest, plan: CodingWorkerPlan) -> dict[str, str]:
        if not plan.updated_files:
            raise ValueError("coding worker returned no updated_files for the scoped refinement")

        files_as_dict = {fu.path: fu.content for fu in plan.updated_files}
        allowed_paths = set(request.files.keys())
        invalid_paths = [path for path in files_as_dict if path not in allowed_paths]
        if invalid_paths:
            raise ValueError(
                "coding worker attempted to edit paths outside the explicit scoped files: "
                + ", ".join(sorted(invalid_paths))
            )

        if plan.owned_paths:
            outside_owned = [path for path in files_as_dict if path not in set(plan.owned_paths)]
            if outside_owned:
                raise ValueError(
                    "coding worker returned updated_files outside the declared owned_paths: "
                    + ", ".join(sorted(outside_owned))
                )

        return {path: str(content) for path, content in files_as_dict.items()}

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
        if str(request.artifact_kind or "").strip() not in _ELIGIBLE_ARTIFACT_KINDS:
            return False, "coding worker only supports app_bundle or workflow_bundle artifacts"
        if not str(request.artifact_version_id or "").strip():
            return False, "coding worker requires artifact_version_id for scoped refinement"
        if not isinstance(request.files, dict) or not request.files:
            return False, "coding worker requires explicit scoped files in v1"
        return True, None

    @staticmethod
    def _safe_relpath(raw: Any) -> str | None:
        if not isinstance(raw, str):
            return None
        normalized = raw.replace("\\", "/").strip()
        if not normalized or normalized.startswith("/"):
            return None
        posix_path = PurePosixPath(normalized)
        if posix_path.is_absolute() or any(part == ".." for part in posix_path.parts):
            return None
        return str(posix_path)

    @staticmethod
    def _build_user_prompt(
        *,
        request: CodingWorkerRequest,
        control_plane_context: dict[str, Any],
    ) -> str:
        payload = {
            "artifact_kind": request.artifact_kind,
            "artifact_key": request.artifact_key,
            "artifact_version_id": request.artifact_version_id,
            "requested_workflow_id": request.requested_workflow_id,
            "change_class": request.change_class,
            "source_surface": request.source_surface,
            "request": request.raw_user_request,
            "file_paths": sorted(request.files.keys()),
            "input_files": request.files,
            "validation_strategy_hint": request.validation_strategy or "auto",
            "start_preview_requested": bool(request.start_preview),
            "context_seed": request.context_seed,
            "metadata": request.metadata,
            "refinement_context": control_plane_context,
        }
        lines = [
            "Plan a scoped coding refinement for this Mozaiks artifact request.",
            "Return JSON only.",
            "payload_json:",
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            "",
            "Return a JSON object with this exact shape:",
            (
                '{"summary":"...","owned_paths":["..."],'
                '"updated_files":[{"path":"relative/path","content":"full file content"}],'
                '"validation_strategy":"skip|local|e2b",'
                '"validation_commands":["..."],"start_preview":false,'
                '"needs_human_review":false,"rationale":"..."}'
            ),
        ]
        return "\n".join(lines)

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
            payload = result.model_dump(mode="json")
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
        artifact_key = str(request.artifact_key or resolved_artifact_kind or "artifact").strip() or "artifact"
        bundle_token = uuid.uuid4().hex[:12]
        # Use the resolved (aliased) kind for file system layout so theme patches
        # land alongside app_bundle artifacts, not in a separate tree.
        bundle_root = self._output_root / request.app_id / resolved_artifact_kind / artifact_key / bundle_token
        workspace_dir = bundle_root / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        written_paths: list[str] = []
        for raw_path, content in merged_files.items():
            safe = self._safe_relpath(raw_path)
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
        artifact_version = await artifact_store.create_artifact_version(
            app_id=request.app_id,
            artifact_kind=resolved_artifact_kind,
            artifact_key=artifact_key,
            parent_version_id=request.artifact_version_id,
            source_workflow=request.requested_workflow_id or "control_plane_coding",
            source_chat_id=None,
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
            validation_status=validation_status,
            files_manifest=[
                {
                    "path": f"{artifact_key}/{zip_path.name}",
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
            "artifact_version_id": artifact_version.id,
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
    def _safe_model_validation_command_hint(command: str) -> bool:
        if not command or "\x00" in command:
            return False
        if len(command) > _MODEL_VALIDATION_COMMAND_MAX_LENGTH:
            return False
        return not any(char in command for char in "\r\n")

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
