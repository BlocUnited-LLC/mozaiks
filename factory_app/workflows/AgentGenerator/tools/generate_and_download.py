"""
generate_and_download tool — assembles workflow bundle files from workflow_bundle_results,
zips them, and presents the inline download UI.

Replaces the old persistence-gather + converter approach with a direct read from
the task batch output stored in context_variables["workflow_bundle_results"].
"""

import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Annotated, Any

from factory_app.workflows._shared.workflow_integration import (
    apply_workflow_integration_context,
    extract_workflow_integration_metadata_from_bundle_entries,
)
from logs.logging_config import get_workflow_logger
from mozaiksai.core.workflow.generator_support.agent_endpoints import (
    resolve_agent_api_url,
    resolve_agent_websocket_url,
)
from mozaiksai.core.workflow.generator_support.workflow_artifacts import record_workflow_artifacts
from mozaiksai.core.workflow.generator_support.workflow_exports import record_workflow_export
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool

from .export_agent_workflow import export_agent_workflow_to_github
from .workflow_converter import promote_generated_workflow
from .workflow_quality_gate import (
    prepare_workflow_bundle_repair,
    run_workflow_bundle_quality_gate,
)

try:
    from logs.tools_logs import get_tool_logger as _get_tool_logger  # type: ignore
    from logs.tools_logs import log_tool_event as _log_tool_event
except Exception:
    _get_tool_logger = None  # type: ignore
    _log_tool_event = None  # type: ignore

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _resolve_generated_artifacts_root() -> Path:
    raw = os.getenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", "generated").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


def _safe_path_segment(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return text or fallback


def _resolve_workflow_output_root(*, app_id: Any, build_id: Any) -> Path:
    return (
        _resolve_generated_artifacts_root()
        / "workflows"
        / _safe_path_segment(app_id, fallback="local-app")
        / _safe_path_segment(build_id, fallback="local-build")
    )


def _to_pascal_case(name: str) -> str:
    """Convert a workflow name to PascalCase."""
    if not name:
        return name
    if ' ' not in name and '-' not in name and '_' not in name and name[0].isupper():
        return name
    words = name.replace('_', ' ').replace('-', ' ').split()
    return ''.join(word.title() for word in words if word)


def _format_bytes(num: int) -> str:
    try:
        value: float = float(num)
        for unit in ['bytes', 'KB', 'MB', 'GB', 'TB']:
            if value < 1024 or unit == 'TB':
                return f"{int(value)} bytes" if unit == 'bytes' else f"{value:.1f} {unit}"
            value /= 1024.0
    except Exception:
        return f"{num} bytes"
    return f"{num} bytes"


def _write_bundle_to_disk(
    workflow_name: str,
    files: list[dict[str, Any]],
    base_dir: Path,
) -> tuple[Path, list[str]]:
    """Write WorkflowBundleBuilderOutput files to disk under base_dir/workflow_name/.

    Returns (workflow_dir, list_of_relative_filenames_written).
    """
    workflow_dir = base_dir / workflow_name
    workflow_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    for file_entry in files:
        filename = (file_entry.get("filename") or "").strip()
        content = file_entry.get("content") or ""
        if not filename:
            continue
        target = workflow_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append(filename)

    return workflow_dir, created


def _build_pack_zip(
    *,
    bundle_dirs: list[tuple],  # list of (wf_name: str, wf_dir: Path)
    output_path: Path,
) -> Path:
    """Zip one or more workflow bundle directories into a single archive."""
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for wf_name, wf_dir in bundle_dirs:
            for file_path in wf_dir.rglob("*"):
                if file_path.is_file():
                    arcname = f"{wf_name}/{file_path.relative_to(wf_dir)}"
                    zf.write(file_path, arcname=arcname)
    return output_path


def _promote_workflow_to_app_workspace(workflow_dir: Path, wf_name: str) -> None:
    """Copy a generated workflow into the active app workspace's workflows/ directory.

    Reads MOZAIKS_APP_WORKSPACE_PATH to locate the target. No-op when unset.
    Logs a warning on failure but never raises.
    """
    workspace = os.getenv("MOZAIKS_APP_WORKSPACE_PATH", "").strip()
    if not workspace:
        _logger.debug("[%s] MOZAIKS_APP_WORKSPACE_PATH not set — skipping workflow promotion", wf_name)
        return
    target_root = Path(workspace) / "workflows"
    try:
        result = promote_generated_workflow(workflow_dir, target_root)
        _logger.info("[%s] Promoted generated workflow to app workspace: %s", wf_name, result["target_dir"])
    except Exception as exc:
        _logger.warning("[%s] Failed to promote generated workflow to app workspace (%s): %s", wf_name, target_root, exc)


async def _register_workflow_bundle_artifact_version(
    *,
    app_id: str,
    user_id: str | None,
    workflow_name: str,
    chat_id: str | None,
    bundle_name: str,
    zip_path: Path | None,
    context_variables: Any | None,
    workflow_integration_metadata: dict[str, Any] | None = None,
) -> Any:
    try:
        import hashlib

        from mozaiksai.core.artifacts import (
            ArtifactLifecycleStatus,
            ArtifactValidationStatus,
            get_artifact_store,
            resolve_latest_artifact_version_refs,
        )
    except Exception as exc:
        raise RuntimeError(f"Artifact store dependencies unavailable: {exc}") from exc

    files_manifest = []
    if zip_path is not None and zip_path.exists():
        raw = zip_path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        files_manifest.append(
            {
                "path": f"{bundle_name}/{zip_path.name}",
                "sha256": sha,
                "size_bytes": zip_path.stat().st_size,
                "content_type": "application/zip",
            }
        )

    parent_version_id = None
    if context_variables and hasattr(context_variables, "get"):
        try:
            parent_version_id = context_variables.get("artifact_version_id")
        except Exception:
            pass

    artifact_store = get_artifact_store()
    canonical_inputs_version = await resolve_latest_artifact_version_refs(
        app_id=str(app_id),
        artifact_kinds=("concept", "design_docs"),
        artifact_store=artifact_store,
    )
    artifact_version = await artifact_store.create_build_record(
        app_id=str(app_id),
        build_family="workflow_bundle",
        build_key=bundle_name,
        parent_build_record_id=str(parent_version_id) if parent_version_id else None,
        canonical_inputs_version=canonical_inputs_version,
        files_manifest=files_manifest,
        source_workflow=workflow_name,
        source_chat_id=chat_id,
        lifecycle_status=(
            ArtifactLifecycleStatus.DRAFT if parent_version_id else ArtifactLifecycleStatus.CURRENT
        ),
        validation_status=ArtifactValidationStatus.PENDING,
        commit_metadata={
            "message": f"{workflow_name}: {bundle_name}",
            "author_user_id": user_id,
            "source_workflow": workflow_name,
            "source_chat_id": chat_id,
            "metadata": {
                "artifact_path": str(zip_path) if zip_path else None,
                "workflow_name": bundle_name,
                "workflow_integration_metadata": workflow_integration_metadata,
            },
        },
    )
    if context_variables and hasattr(context_variables, "set"):
        try:
            context_variables.set("artifact_version_id", artifact_version.id)
        except Exception:
            pass
    return artifact_version


async def _record_context_and_artifacts(
    *,
    app_id: str | None,
    user_id: str | None,
    chat_id: str | None,
    pack_name: str,
    bundle_entries: list[dict[str, Any]],
    zip_path: Path | None,
    context_variables: Any | None,
) -> None:
    """Record workflow export context and artifacts for downstream chaining."""
    if not app_id:
        raise ValueError("app_id is required to record workflow artifacts")

    # Synthesize minimal workflow config from bundle entries.
    workflow_names = [e.get("workflow_name", "") for e in bundle_entries if e.get("workflow_name")]
    workflow_integration_metadata = extract_workflow_integration_metadata_from_bundle_entries(
        bundle_entries,
        bundle_name=pack_name,
    )
    primary_workflow = (
        workflow_integration_metadata.get("primary_workflow")
        if isinstance(workflow_integration_metadata, dict)
        else None
    )

    websocket_url = resolve_agent_websocket_url(str(app_id))
    api_url = resolve_agent_api_url(str(app_id))
    capability_id = (
        str(primary_workflow.get("capability_id"))
        if isinstance(primary_workflow, dict) and primary_workflow.get("capability_id")
        else (pack_name.lower().replace("_", "-").replace(" ", "-") if pack_name else None)
    )
    generated_workflow_name = (
        str(primary_workflow.get("workflow_name"))
        if isinstance(primary_workflow, dict) and primary_workflow.get("workflow_name")
        else pack_name
    )
    generated_workflow_startup_mode = (
        primary_workflow.get("startup_mode")
        if isinstance(primary_workflow, dict)
        else None
    )
    generated_workflow_trigger_events = (
        primary_workflow.get("trigger_events") or []
        if isinstance(primary_workflow, dict)
        else []
    )
    if workflow_integration_metadata:
        apply_workflow_integration_context(context_variables, workflow_integration_metadata)

    # The canonical BuildRecord is the required persistence boundary. Register
    # it before writing optional export/diagnostic projections so a required
    # failure cannot leave those secondary records looking authoritative.
    await _register_workflow_bundle_artifact_version(
        app_id=str(app_id),
        user_id=user_id,
        workflow_name="AgentGenerator",
        chat_id=chat_id,
        bundle_name=pack_name,
        zip_path=zip_path,
        context_variables=context_variables,
        workflow_integration_metadata=workflow_integration_metadata,
    )

    try:
        await record_workflow_export(
            app_id=str(app_id),
            user_id=user_id,
            workflow_type="agent-generator",
            repo_url=None,
            job_id=None,
            meta={
                "bundle_workflow_name": pack_name,
                "workflow_name": generated_workflow_name,
                "capability_id": capability_id,
                "workflow_names": workflow_names,
                "workflow_integration_metadata": workflow_integration_metadata,
            },
            extra_fields={
                "bundle_workflow_name": pack_name,
                "workflow_name": generated_workflow_name,
                "capability_id": capability_id,
                "workflow_names": workflow_names,
                "workflow_startup_mode": generated_workflow_startup_mode,
                "workflow_trigger_events": generated_workflow_trigger_events,
                "workflow_integration_metadata": workflow_integration_metadata,
                "agent_websocket_url": websocket_url,
                "agent_api_url": api_url,
            },
        )
    except Exception as exc:
        _logger.warning("Optional workflow export persistence failed: %s", exc)

    try:
        await record_workflow_artifacts(
            app_id=str(app_id),
            user_id=user_id,
            workflow_type="agent-generator",
            workflow_name=pack_name,
            artifacts={
                "bundle_name": pack_name,
                "workflow_names": workflow_names,
                "bundle_entries": bundle_entries,
                "workflow_integration_metadata": workflow_integration_metadata,
            },
        )
    except Exception as exc:
        _logger.warning("Optional workflow artifact projection failed: %s", exc)

    if context_variables and hasattr(context_variables, "set"):
        context_variables.set("agent_websocket_url", websocket_url)
        context_variables.set("agent_api_url", api_url)
        context_variables.set("generated_workflow_name", generated_workflow_name)
        context_variables.set("generated_workflow_capability_id", capability_id)
        context_variables.set("generated_workflow_startup_mode", generated_workflow_startup_mode)
        context_variables.set("generated_workflow_trigger_events", generated_workflow_trigger_events)


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------


async def generate_and_download(
    DownloadRequest: Annotated[dict[str, Any], "Download configuration"],
    agent_message: Annotated[str, "Concise message (≤140 chars) shown to user in download UI"],
    context_variables: Annotated[Any | None, "Injected runtime context."] = None,
) -> dict[str, Any]:
    """
    Assemble all workflow bundle files from workflow_bundle_results, zip them,
    and present the inline download UI.

    Reads pre-built CodeFile lists directly from the task batch output in
    context_variables["workflow_bundle_results"] — no converter or persistence
    assembly needed.
    """
    confirmation_only = DownloadRequest.get("confirmation_only", True)
    storage_backend = DownloadRequest.get("storage_backend", "none")

    chat_id: str | None = None
    app_id: str | None = None
    workflow_name: str | None = None
    user_id: str | None = None
    build_id: str | None = None

    if context_variables and hasattr(context_variables, "get"):
        chat_id = context_variables.get("chat_id")
        app_id = context_variables.get("app_id")
        workflow_name = context_variables.get("workflow_name")
        user_id = context_variables.get("user_id")
        build_id = context_variables.get("build_id")

    wf_logger = get_workflow_logger(workflow_name=(workflow_name or "AgentGenerator"), chat_id=chat_id, app_id=app_id)
    tlog = None
    if _get_tool_logger:  # type: ignore[truthy-function]
        try:
            tlog = _get_tool_logger(
                tool_name="GenerateAndDownload",
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=(workflow_name or "AgentGenerator"),
            )
            if _log_tool_event:  # type: ignore[truthy-function]
                _log_tool_event(tlog, action="start", status="ok")
        except Exception:
            tlog = None

    wf_logger.info("🏗️ generate_and_download start (confirmation_only=%s) chat=%s", confirmation_only, chat_id)

    if not chat_id or not app_id:
        return {"status": "error", "message": "chat_id and app_id are required"}

    # ------------------------------------------------------------------
    # Read workflow_bundle_results from task batch output
    # ------------------------------------------------------------------
    workflow_bundle_results: dict[str, Any] | None = None
    pack_name: str | None = None

    if context_variables and hasattr(context_variables, "get"):
        workflow_bundle_results = context_variables.get("workflow_bundle_results")
        pack_name = context_variables.get("pack_name")

    if not isinstance(workflow_bundle_results, dict) or not workflow_bundle_results:
        return {
            "status": "error",
            "message": "workflow_bundle_results is empty — task batch did not complete successfully",
        }

    # Collect per-workflow entries (skip internal _meta key)
    bundle_entries: list[dict[str, Any]] = [
        v for k, v in workflow_bundle_results.items()
        if not k.startswith("_") and isinstance(v, dict)
    ]

    if not bundle_entries:
        return {"status": "error", "message": "No workflow bundles found in workflow_bundle_results"}

    quality_gate = run_workflow_bundle_quality_gate(
        bundle_entries=bundle_entries,
        context_variables=context_variables,
    )
    if not quality_gate.get("passed"):
        repair_result = prepare_workflow_bundle_repair(
            quality_gate=quality_gate,
            bundle_entries=bundle_entries,
            context_variables=context_variables,
        )
        wf_logger.warning(
            "Workflow bundle quality gate failed with %d issue(s).",
            len(quality_gate.get("errors") or []),
        )
        return {
            "status": "blocked",
            "message": (
                "Workflow bundle quality gate failed; repair has been scheduled."
                if repair_result.get("status") == "needs_revision"
                else "Workflow bundle quality gate failed; download was not created."
            ),
            "workflow_bundle_quality_gate": quality_gate,
            "workflow_bundle_repair": repair_result,
            "validation_errors": quality_gate.get("errors") or [],
        }
    if context_variables is not None and hasattr(context_variables, "set"):
        try:
            context_variables.set("workflow_bundle_repair_status", "passed")
            context_variables.set("workflow_bundle_repair_active", False)
        except Exception:
            pass

    # Derive the top-level bundle name (pack name or single workflow name)
    if pack_name and isinstance(pack_name, str) and pack_name.strip():
        bundle_name = _to_pascal_case(pack_name.strip())
    else:
        bundle_name = _to_pascal_case(
            (bundle_entries[0].get("workflow_name") or "GeneratedWorkflow").strip()
        )

    wf_logger = get_workflow_logger(workflow_name=bundle_name, chat_id=chat_id, app_id=app_id)
    wf_logger.info("📦 Bundle name: %s  workflows: %d", bundle_name, len(bundle_entries))

    agent_message_text = agent_message or (
        "Would you like to download the generated workflow bundle now?"
        if confirmation_only else
        "Preparing your workflow files. Use the download panel when ready."
    )
    agent_message_id = f"msg_{uuid.uuid4().hex[:10]}"
    print(agent_message_text)

    # ------------------------------------------------------------------
    # Resolve output directory
    # ------------------------------------------------------------------
    base_generated = _resolve_workflow_output_root(
        app_id=app_id,
        build_id=build_id or chat_id,
    )
    base_generated.mkdir(parents=True, exist_ok=True)

    ui_files: list[dict[str, Any]] = []
    zip_path_obj: Path | None = None

    def _create_bundle_files() -> tuple[Path | None, list[dict[str, Any]]]:
        """Write all bundle files to disk and create the zip. Returns (zip_path, ui_files)."""
        bundle_dirs: list[tuple] = []
        for entry in bundle_entries:
            wf_name = entry.get("workflow_name") or ""
            files = entry.get("files") or []
            if not wf_name or not isinstance(files, list):
                wf_logger.warning("Skipping bundle entry with missing workflow_name or files: %r", entry.get("_task_id"))
                continue
            wf_dir, created = _write_bundle_to_disk(wf_name, files, base_generated)
            wf_logger.info("✅ Wrote %d files for workflow %s to %s", len(created), wf_name, wf_dir)
            bundle_dirs.append((wf_name, wf_dir))

        if not bundle_dirs:
            return None, []

        zip_output = base_generated / f"{bundle_name}.zip"
        _build_pack_zip(bundle_dirs=bundle_dirs, output_path=zip_output)
        zip_size = zip_output.stat().st_size
        wf_logger.info("📦 Created zip archive: %s (%s)", zip_output.name, _format_bytes(zip_size))

        return zip_output, [
            {
                "name": f"{bundle_name}.zip",
                "size": _format_bytes(zip_size),
                "size_bytes": zip_size,
                "path": str(zip_output.resolve()),
                "id": "file-zip-bundle",
                "type": "zip",
            }
        ]

    if not confirmation_only:
        # Immediate mode: create files now, then show download UI
        wf_logger.info("📦 Creating workflow files immediately (confirmation_only=False)...")
        zip_path_obj, ui_files = _create_bundle_files()
        if not ui_files:
            return {"status": "error", "message": "Failed to create workflow bundle files"}

        # Promote first workflow to app workspace (best-effort)
        first_wf_name = bundle_entries[0].get("workflow_name", "") if bundle_entries else ""
        if first_wf_name:
            first_wf_dir = base_generated / first_wf_name
            _promote_workflow_to_app_workspace(first_wf_dir, first_wf_name)

        await _record_context_and_artifacts(
            app_id=app_id,
            user_id=user_id,
            chat_id=chat_id,
            pack_name=bundle_name,
            bundle_entries=bundle_entries,
            zip_path=zip_path_obj,
            context_variables=context_variables,
        )
    else:
        # Confirmation mode: show UI first, create files after user confirms
        wf_logger.info("❓ Asking user confirmation before creating files...")

    ui_payload = {
        "downloadType": "single",
        "files": ui_files,
        "agent_message": agent_message_text,
        "description": agent_message_text,
        "title": "Workflow Bundle Ready" if confirmation_only else "Generated Workflow Files",
        "workflow_name": bundle_name,
        "agent_message_id": agent_message_id,
        "stage": "confirm" if (confirmation_only and not ui_files) else "files_ready",
    }

    try:
        if tlog and _log_tool_event:  # type: ignore[truthy-function]
            _log_tool_event(tlog, action="emit_ui", status="start", agent_message_id=agent_message_id)
        response = await use_ui_tool(
            tool_id="DownloadCenter",
            payload=ui_payload,
            chat_id=chat_id,
            workflow_name=bundle_name,
        )
        wf_logger.info("📥 Download UI completed with status: %s", response.get("status", "unknown"))
        if tlog and _log_tool_event:  # type: ignore[truthy-function]
            _log_tool_event(tlog, action="emit_ui", status="done", result_status=response.get("status", "unknown"))
    except UIToolError:
        raise
    except Exception as e:
        wf_logger.error("❌ UI interaction failed: %s", e, exc_info=True)
        raise UIToolError("Failed during file download UI interaction") from e

    if response.get("status") == "cancelled":
        wf_logger.info("❌ User declined download")
        return {
            "status": "cancelled",
            "ui_response": response,
            "agent_message_id": agent_message_id,
            "ui_files": [],
            "message": "User declined download",
        }

    # If confirmation mode, now create the files (user confirmed)
    if confirmation_only:
        wf_logger.info("✅ User confirmed — creating files now...")
        zip_path_obj, ui_files = _create_bundle_files()
        if not ui_files:
            return {"status": "error", "message": "Failed to create workflow bundle files"}

        first_wf_name = bundle_entries[0].get("workflow_name", "") if bundle_entries else ""
        if first_wf_name:
            first_wf_dir = base_generated / first_wf_name
            _promote_workflow_to_app_workspace(first_wf_dir, first_wf_name)

        await _record_context_and_artifacts(
            app_id=app_id,
            user_id=user_id,
            chat_id=chat_id,
            pack_name=bundle_name,
            bundle_entries=bundle_entries,
            zip_path=zip_path_obj,
            context_variables=context_variables,
        )

        if isinstance(response, dict):
            response.setdefault("data", {})
            if isinstance(response["data"], dict):
                response["data"]["files"] = ui_files
                response["data"]["fileCount"] = len(ui_files)
            response.setdefault("agentContext", {})
            if isinstance(response.get("agentContext"), dict):
                response["agentContext"]["files_created"] = True
                response["agentContext"]["file_count"] = len(ui_files)

    # Set download_complete when user accepted
    if context_variables and response.get("download_accepted"):
        try:
            context_variables.set("download_complete", True)
            wf_logger.info("✅ Set download_complete=True")
        except Exception:
            pass

    # Optional storage backend
    if storage_backend != "none" and ui_files:
        try:
            storage_result = await _handle_storage_action(
                storage_backend=storage_backend,
                response=response,
                zip_path=zip_path_obj,
                context_variables=context_variables,
                workflow_name=bundle_name,
            )
            if storage_result:
                response = {**response, "storage": storage_result}
        except Exception as se:
            wf_logger.warning("⚠️ Storage action failed: %s", se)

    # Optional GitHub export
    deployment_result: dict[str, Any] | None = None
    try:
        action = None
        if isinstance(response, dict):
            action = response.get("action")
            if not action and isinstance(response.get("data"), dict):
                action = response["data"].get("action")

        if action == "export_to_github":
            zip_bundle_path = None
            for f in ui_files:
                if isinstance(f, dict) and f.get("type") == "zip" and f.get("path"):
                    zip_bundle_path = f.get("path")
                    break

            repo_name = None
            commit_message = "Initial code generation from Mozaiks AI"
            if isinstance(response, dict):
                repo_name = response.get("repo_name") or response.get("repoName")
                commit_message = response.get("commit_message") or response.get("commitMessage") or commit_message
                if isinstance(response.get("data"), dict):
                    repo_name = response["data"].get("repo_name") or response["data"].get("repoName") or repo_name
                    commit_message = (
                        response["data"].get("commit_message")
                        or response["data"].get("commitMessage")
                        or commit_message
                    )

            if not zip_bundle_path:
                deployment_result = {"success": False, "error": "ZIP bundle path not available for export."}
            else:
                wf_logger.info("🚀 Export to GitHub requested (repo=%s)", repo_name)
                deployment_result = await export_agent_workflow_to_github(
                    bundle_path=str(zip_bundle_path),
                    app_id=app_id,
                    repo_name=repo_name,
                    commit_message=commit_message,
                    user_id=user_id,
                    context_variables=context_variables,
                )
                if context_variables and isinstance(deployment_result, dict) and deployment_result.get("success"):
                    try:
                        if deployment_result.get("repo_url"):
                            context_variables.set("github_repo_url", deployment_result["repo_url"])
                        if deployment_result.get("job_id"):
                            context_variables.set("github_deploy_job_id", deployment_result["job_id"])
                    except Exception:
                        pass
    except Exception as deploy_err:
        wf_logger.warning("⚠️ GitHub export flow failed: %s", deploy_err)

    return {
        "status": "success",
        "ui_response": response,
        "agent_message_id": agent_message_id,
        "ui_files": ui_files,
        **({"deployment": deployment_result} if deployment_result is not None else {}),
    }


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


async def _handle_storage_action(
    storage_backend: str,
    response: dict[str, Any],
    zip_path: Path | None,
    context_variables: Any | None = None,
    workflow_name: str | None = None,
) -> dict[str, Any] | None:
    if storage_backend == "local":
        return await _store_files_local(
            response=response,
            zip_path=zip_path,
            context_variables=context_variables,
            workflow_name=workflow_name,
        )
    if storage_backend == "none":
        return None
    return {"status": "error", "message": f"Unsupported storage backend: {storage_backend}"}


async def _store_files_local(
    response: dict[str, Any],
    zip_path: Path | None,
    context_variables: Any | None = None,
    workflow_name: str | None = None,
) -> dict[str, Any]:
    selected_path = None
    if isinstance(response, dict):
        selected_path = response.get("selectedPath") or (
            response.get("data", {}).get("selectedPath") if isinstance(response.get("data"), dict) else None
        )
    if not selected_path:
        return {"status": "skipped", "message": "No selectedPath provided by UI"}
    if not zip_path or not zip_path.exists():
        return {"status": "skipped", "message": "No zip bundle to copy"}

    wf_logger = get_workflow_logger(workflow_name=(workflow_name or "generator"), chat_id=None, app_id=None)
    try:
        dest = Path(selected_path)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / zip_path.name
        shutil.copy2(zip_path, target)
        wf_logger.info("📋 Copied %s -> %s", zip_path.name, target)

        if context_variables:
            try:
                downloads = context_variables.get("file_downloads", []) or []
                rec = {
                    "type": "local_copy",
                    "files": [str(target)],
                    "file_count": 1,
                    "dest_path": str(dest),
                    "copied_at": str(time.time()),
                }
                downloads.append(rec)
                context_variables.set("file_downloads", downloads)
                context_variables.set("last_download", rec)
            except Exception:
                pass

        return {
            "status": "success",
            "message": f"Copied bundle to {selected_path}",
            "copied_files": [str(target)],
            "dest_path": str(dest),
        }
    except Exception as e:
        wf_logger.error("❌ Local storage failed: %s", e)
        return {"status": "error", "message": f"Failed to copy files: {e}"}
