from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    get_artifact_store,
)
from mozaiksai.core.artifacts.content_store import get_artifact_content_store
from mozaiksai.core.capabilities import get_general_capability_service
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    CodingWorkerResult,
    ControlPlaneToolCall,
    ControlPlaneToolContext,
)
from mozaiksai.control_plane.executor import ControlPlaneToolExecutor
from mozaiksai.control_plane.loader import load_selected_control_plane_pack
from mozaiksai.control_plane.schema import LoadedControlPlanePack

from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_build

_ELIGIBLE_CHANGE_CLASSES = {"patch"}
_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "workflow_bundle", "theme_config"}
_VALIDATION_STRATEGIES = {"skip", "local", "e2b"}
_CHECKPOINT_EVENT = "coding_requested"

# theme_config files live inside the app_bundle workspace. We alias the artifact
# kind so the coding worker can locate and patch these files without requiring a
# separate theme_config artifact store entry. Workspace scope tools fall back to
# the app_bundle artifact when a theme_config entry does not yet exist.
_ARTIFACT_KIND_ALIASES: dict[str, str] = {
    "theme_config": "app_bundle",
}


class ScopedRefinementCodingWorker:
    """First-party control-plane coding worker for narrow refinement loops.

    This worker is intentionally conservative in v1. It is only eligible for
    scoped patch-style refinements and operates on explicit file payloads.
    """

    def __init__(
        self,
        *,
        capability_service: Any = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_control_plane_pack,
        tool_executor: Any = None,
        validation_runner: Any = validate_app_build,
        artifact_store: Any = None,
        output_root: Any = None,
    ) -> None:
        self._service = capability_service or get_general_capability_service()
        self._config_loader = config_loader
        self._pack_loader = pack_loader
        self._tool_executor = tool_executor or ControlPlaneToolExecutor(pack_loader=pack_loader)
        self._validation_runner = validation_runner
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
            llm_config = self._load_config().resolve_capability_llm_config("coding")
            system_prompt = self._load_system_prompt()
            control_plane_context = await self._load_control_plane_context(request)
            user_prompt = self._build_user_prompt(request=request, control_plane_context=control_plane_context)
            response = await self._service.generate_json_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                app_id=request.app_id,
                user_id=request.user_id,
                ui_context={"surface": request.source_surface or "coding_worker"},
                llm_config=llm_config,
                temperature=self._resolve_temperature(llm_config),
            )
            plan = CodingWorkerPlan.model_validate(response.get("parsed") or {})
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
            validation_result = await self._validation_runner(
                files=merged_files,
                commands=list(resolved_plan.validation_commands or []),
                start_dev_server=bool(resolved_plan.start_preview),
                validation_strategy=resolved_strategy,
                context_variables=None,
            )
            validation_status = str((validation_result or {}).get("validation_status") or "").strip().lower()
            if validation_status in {"passed", "skipped"}:
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
        if isinstance((request.metadata or {}).get("scope_proposal"), dict):
            metadata["scope_proposal"] = dict(request.metadata["scope_proposal"])
        if status == "validated":
            try:
                metadata.update(
                    await self._persist_validated_artifact(
                        request=request,
                        resolved_artifact_kind=resolved_artifact_kind,
                        applied_files=applied_files,
                        merged_files=merged_files,
                        plan=resolved_plan,
                        validation_result=validation_result or {},
                    )
                )
            except Exception as exc:
                metadata["artifact_persistence_error"] = str(exc)

        return CodingWorkerResult(
            eligible=True,
            status=status,
            plan=resolved_plan,
            applied_files=applied_files,
            validation_result=validation_result,
            metadata=metadata,
            error=(validation_result or {}).get("errors", [None])[0] if status == "failed" else None,
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
                f"Selected control-plane profile does not declare a '{_CHECKPOINT_EVENT}' checkpoint with prompt_id"
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

        updated_files: dict[str, str] = {}
        for raw_path, content in (plan.updated_files or {}).items():
            safe = ScopedRefinementCodingWorker._safe_relpath(raw_path)
            if not safe:
                continue
            updated_files[safe] = str(content)
            if safe not in seen_owned:
                owned_paths.append(safe)
                seen_owned.add(safe)

        commands = []
        for raw_command in plan.validation_commands or []:
            command = str(raw_command or "").strip()
            if command:
                commands.append(command)

        return plan.model_copy(
            update={
                "owned_paths": owned_paths,
                "updated_files": updated_files,
                "validation_commands": commands,
            }
        )

    @staticmethod
    def _resolve_updated_files(*, request: CodingWorkerRequest, plan: CodingWorkerPlan) -> dict[str, str]:
        if not isinstance(plan.updated_files, dict) or not plan.updated_files:
            raise ValueError("coding worker returned no updated_files for the scoped refinement")

        allowed_paths = {str(path): str(content) for path, content in request.files.items()}
        invalid_paths = [path for path in plan.updated_files if path not in allowed_paths]
        if invalid_paths:
            raise ValueError(
                "coding worker attempted to edit paths outside the explicit scoped files: "
                + ", ".join(sorted(invalid_paths))
            )

        if plan.owned_paths:
            outside_owned = [path for path in plan.updated_files if path not in set(plan.owned_paths)]
            if outside_owned:
                raise ValueError(
                    "coding worker returned updated_files outside the declared owned_paths: "
                    + ", ".join(sorted(outside_owned))
                )

        return {path: str(content) for path, content in plan.updated_files.items()}

    @staticmethod
    def _resolve_temperature(llm_config: Optional[dict[str, Any]]) -> Optional[float]:
        if not isinstance(llm_config, dict):
            return None
        value = llm_config.get("temperature")
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _resolve_validation_strategy(raw: str) -> str:
        normalized = str(raw or "").strip().lower() or "skip"
        return normalized if normalized in _VALIDATION_STRATEGIES else "skip"

    @staticmethod
    def _check_eligibility(request: CodingWorkerRequest) -> tuple[bool, Optional[str]]:
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
    def _safe_relpath(raw: Any) -> Optional[str]:
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
            "control_plane_context": control_plane_context,
        }
        lines = [
            "Plan a scoped coding refinement for this Mozaiks artifact request.",
            "Return JSON only.",
            "payload_json:",
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            "",
            "Return a JSON object with this exact shape:",
            (
                '{"summary":"...","owned_paths":["..."],"updated_files":{"path":"full file content"},'
                '"validation_strategy":"skip|local|e2b",'
                '"validation_commands":["..."],"start_preview":false,'
                '"needs_human_review":false,"rationale":"..."}'
            ),
        ]
        return "\n".join(lines)

    async def _persist_validated_artifact(
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
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(str(content), encoding="utf-8")
            written_paths.append(safe)

        zip_path = bundle_root / "artifact.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for rel_path in sorted(written_paths):
                zipf.write(workspace_dir / rel_path, arcname=rel_path)

        zip_bytes = zip_path.read_bytes()
        zip_sha = hashlib.sha256(zip_bytes).hexdigest()

        # Persist to content store if a non-local backend is configured.
        commit_content_metadata: dict[str, Any] = {
            "artifact_path": str(zip_path.resolve()),
            "workspace_dir": str(workspace_dir.resolve()),
            "bundle_mode": "workspace_snapshot",
            "applied_paths": sorted(applied_files.keys()),
            "validation_strategy": plan.validation_strategy,
            "validation_status": "",  # placeholder; set after status resolved below
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
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "Content store put_bundle failed for app %s; falling back to local path: %s",
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
            "bundle_mode": "workspace_snapshot",
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


_coding_worker: Optional[ScopedRefinementCodingWorker] = None


def get_coding_worker() -> ScopedRefinementCodingWorker:
    global _coding_worker
    if _coding_worker is None:
        _coding_worker = ScopedRefinementCodingWorker()
    return _coding_worker
