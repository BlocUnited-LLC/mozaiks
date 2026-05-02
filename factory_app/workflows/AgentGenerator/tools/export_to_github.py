"""
export_to_github - Deploys generated code bundles to GitHub via Mozaiks Backend.

This tool:
1) Reads the bundle ZIP file and converts it to a file-change payload
2) Calls the Mozaiks Backend initial export endpoint (repo + commit)
3) Configures repository secrets (best-effort)
4) Polls deployment status (GitHub Actions) until completion
5) Returns repo URL + deployment info
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from mozaiksai.core.workflow.generator_support.app_code_versions import (
    build_snapshot_document,
    extract_files_from_zip_bundle,
)
from mozaiksai.core.workflow.generator_support.backend_client import BackendClient
from logs.logging_config import get_workflow_logger

try:
    from logs.tools_logs import get_tool_logger as _get_tool_logger, log_tool_event as _log_tool_event  # type: ignore
except Exception:  # pragma: no cover
    _get_tool_logger = None  # type: ignore
    _log_tool_event = None  # type: ignore


class ExportResult(BaseModel):
    success: bool
    repo_url: Optional[str] = None
    repo_full_name: Optional[str] = None
    base_commit_sha: Optional[str] = None
    job_id: Optional[str] = None  # GitHub Actions workflowRun.id (when available)
    workflow_run_url: Optional[str] = None
    deployment_url: Optional[str] = None
    error: Optional[str] = None


class ExportToGitHubTool:
    """Tool for exporting generated bundles to GitHub via the Mozaiks deployment pipeline."""

    def __init__(self) -> None:
        self.backend_client = BackendClient()
        self.internal_api_key = self.backend_client.api_key

        # Deployment status polling (GitHub Actions)
        try:
            self.deploy_status_timeout_s = int(os.getenv("MOZAIKS_DEPLOY_STATUS_TIMEOUT_SECONDS", "600"))
        except Exception:
            self.deploy_status_timeout_s = 600
        try:
            self.poll_interval_s = float(os.getenv("MOZAIKS_DEPLOY_STATUS_POLL_INTERVAL_SECONDS", "30"))
        except Exception:
            self.poll_interval_s = 30.0
        if self.poll_interval_s <= 0:
            self.poll_interval_s = 30.0
        if self.deploy_status_timeout_s <= 0:
            self.deploy_status_timeout_s = 600

        # Derived attempts (clamped)
        self.max_poll_attempts = max(1, int(self.deploy_status_timeout_s / self.poll_interval_s))

        # Optional size limit (bytes). Default: 25MB.
        try:
            self.max_bundle_bytes = int(os.getenv("MOZAIKS_DEPLOY_MAX_BUNDLE_BYTES", str(25 * 1024 * 1024)))
        except Exception:
            self.max_bundle_bytes = 25 * 1024 * 1024

    async def execute(
        self,
        *,
        app_id: Optional[str],
        bundle_path: str,
        repo_name: Optional[str] = None,
        commit_message: Optional[str] = None,
        user_id: Optional[str] = None,
        workflow_type: Optional[str] = None,
        context_variables: Optional[Any] = None,
    ) -> ExportResult:
        """Export a code bundle ZIP to GitHub via Mozaiks Backend."""

        chat_id = None
        workflow_name = "AgentGenerator"
        if context_variables is not None and hasattr(context_variables, "get"):
            try:
                chat_id = context_variables.get("chat_id")
                workflow_name = context_variables.get("workflow_name") or workflow_name
                app_id = app_id or context_variables.get("app_id")
                user_id = user_id or context_variables.get("user_id")
                workflow_type = workflow_type or context_variables.get("workflow_type")
            except Exception:
                pass

        wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id, app_id=app_id)
        tlog = None
        if _get_tool_logger:
            try:
                tlog = _get_tool_logger(
                    tool_name="ExportToGitHub",
                    chat_id=chat_id,
                    app_id=app_id,
                    workflow_name=workflow_name,
                )
            except Exception:
                tlog = None

        if not app_id or not str(app_id).strip():
            return ExportResult(success=False, error="app_id is required")

        bundle_file = Path(bundle_path)
        if not bundle_file.exists():
            return ExportResult(success=False, error=f"Bundle file not found: {bundle_path}")
        if bundle_file.suffix.lower() != ".zip":
            return ExportResult(success=False, error=f"Bundle must be a .zip file, got: {bundle_file.suffix}")

        try:
            bundle_size = bundle_file.stat().st_size
        except Exception:
            bundle_size = None

        if (
            isinstance(bundle_size, int)
            and isinstance(self.max_bundle_bytes, int)
            and self.max_bundle_bytes > 0
            and bundle_size > self.max_bundle_bytes
        ):
            return ExportResult(
                success=False,
                error=f"Bundle too large ({bundle_size} bytes). Limit is {self.max_bundle_bytes} bytes.",
            )

        # Backend tool endpoints require S2S auth.
        if not self.internal_api_key:
            error_msg = "INTERNAL_API_KEY is required for Mozaiks Backend repo export endpoints"
            await _emit_deployment_event(chat_id=chat_id, status="failed", data={"app_id": app_id, "error": error_msg})
            return ExportResult(success=False, error=error_msg)

        if not user_id or not str(user_id).strip():
            error_msg = "user_id is required for deployment"
            await _emit_deployment_event(chat_id=chat_id, status="failed", data={"app_id": app_id, "error": error_msg})
            return ExportResult(success=False, error=error_msg)

        await _emit_deployment_event(
            chat_id=chat_id,
            status="started",
            data={
                "app_id": app_id,
                "message": "Starting deployment to GitHub...",
            },
        )

        if tlog and _log_tool_event:
            _log_tool_event(tlog, action="start", status="ok", bundle_bytes=bundle_size)

        try:
            # Convert bundle zip into the backend "files" payload (path + base64 content).
            bundle_files = extract_files_from_zip_bundle(str(bundle_file))
            snapshot_doc = build_snapshot_document(
                app_id=str(app_id),
                session_id=chat_id,
                workflow_type=str(workflow_type or workflow_name or "").strip() or "agent-generator",
                source="generated",
                files=bundle_files,
                structured_outputs=None,
            )
            files_payload: List[Dict[str, Any]] = [
                {"path": f.get("path"), "operation": "add", "contentBase64": f.get("contentBase64")}
                for f in (snapshot_doc.get("files") or [])
                if isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("contentBase64"), str)
            ]

            if not files_payload:
                error_msg = "No files found to export (bundle may be empty or filtered)."
                await _emit_deployment_event(chat_id=chat_id, status="failed", data={"app_id": app_id, "error": error_msg})
                return ExportResult(success=False, error=error_msg)

            if tlog and _log_tool_event:
                _log_tool_event(tlog, action="bundle_extracted", status="ok", file_count=len(files_payload))

            # 1) Initial export (repo + commit)
            export_res = await self.initial_export(
                app_id=str(app_id),
                files=files_payload,
                repo_name=repo_name,
                commit_message=commit_message,
                user_id=str(user_id).strip(),
            )
            if not isinstance(export_res, dict) or export_res.get("success") is not True:
                error_msg = (export_res or {}).get("error") or (export_res or {}).get("message") or "Initial export failed"
                await _emit_deployment_event(chat_id=chat_id, status="failed", data={"app_id": app_id, "error": error_msg})
                return ExportResult(success=False, error=str(error_msg))

            repo_url = export_res.get("repoUrl") or export_res.get("repo_url")
            repo_full_name = export_res.get("repoFullName") or export_res.get("repo_full_name")
            base_commit_sha = export_res.get("baseCommitSha") or export_res.get("base_commit_sha")

            await _emit_deployment_event(
                chat_id=chat_id,
                status="progress",
                data={
                    "app_id": app_id,
                    "repo_url": repo_url,
                    "repo_full_name": repo_full_name,
                    "status": "exported",
                    "message": "Repo exported. Configuring secrets and waiting for deployment...",
                },
            )

            # 2) Set repo secrets (best-effort; log but do not fail hard).
            if isinstance(repo_full_name, str) and repo_full_name.strip():
                try:
                    secrets_res = await self.set_repository_secrets(
                        app_id=str(app_id),
                        repo_full_name=str(repo_full_name).strip(),
                        user_id=str(user_id).strip(),
                        include_database_uri=True,
                        include_app_api_key=False,
                    )
                    if tlog and _log_tool_event:
                        _log_tool_event(tlog, action="repo_secrets", status="ok", repo_full_name=repo_full_name, result=secrets_res)
                except Exception as sec_exc:
                    wf_logger.warning(f"[EXPORT] Failed to set repository secrets: {sec_exc}")
                    if tlog and _log_tool_event:
                        _log_tool_event(tlog, action="repo_secrets", status="error", repo_full_name=repo_full_name, error=str(sec_exc))

            # 3) Poll deployment status (GitHub Actions).
            deployment_status: Dict[str, Any] = {}
            if isinstance(repo_full_name, str) and repo_full_name.strip():
                deployment_status = await self._poll_deployment_status(
                    app_id=str(app_id),
                    repo_full_name=str(repo_full_name).strip(),
                    chat_id=chat_id,
                )

            status = str(deployment_status.get("status") or "").lower()
            workflow_run = deployment_status.get("workflowRun") if isinstance(deployment_status.get("workflowRun"), dict) else {}
            conclusion = str((workflow_run or {}).get("conclusion") or "").lower()

            deployment_url = None
            if isinstance(deployment_status.get("deploymentUrls"), dict):
                deployment_url = deployment_status["deploymentUrls"].get("preview")

            workflow_run_url = (workflow_run or {}).get("htmlUrl")
            workflow_run_id = (workflow_run or {}).get("id")

            if status == "completed" and conclusion == "success":
                await _emit_deployment_event(
                    chat_id=chat_id,
                    status="completed",
                    data={
                        "app_id": app_id,
                        "repo_url": repo_url,
                        "repo_full_name": repo_full_name,
                        "deployment_url": deployment_url,
                        "message": "Deployment completed.",
                    },
                )
                if tlog and _log_tool_event:
                    _log_tool_event(
                        tlog,
                        action="completed",
                        status="ok",
                        repo_url=repo_url,
                        repo_full_name=repo_full_name,
                        workflow_run_id=workflow_run_id,
                        deployment_url=deployment_url,
                    )
                return ExportResult(
                    success=True,
                    repo_url=str(repo_url) if isinstance(repo_url, str) else None,
                    repo_full_name=str(repo_full_name) if isinstance(repo_full_name, str) else None,
                    base_commit_sha=str(base_commit_sha) if isinstance(base_commit_sha, str) else None,
                    job_id=str(workflow_run_id) if workflow_run_id is not None else None,
                    workflow_run_url=str(workflow_run_url) if isinstance(workflow_run_url, str) else None,
                    deployment_url=str(deployment_url) if isinstance(deployment_url, str) else None,
                )

            # If we cannot poll status (e.g., no repoFullName), still return the repo URL as success.
            if not deployment_status:
                return ExportResult(
                    success=True,
                    repo_url=str(repo_url) if isinstance(repo_url, str) else None,
                    repo_full_name=str(repo_full_name) if isinstance(repo_full_name, str) else None,
                    base_commit_sha=str(base_commit_sha) if isinstance(base_commit_sha, str) else None,
                    error=None,
                )

            error_msg = f"Deployment failed: status={status or 'unknown'} conclusion={conclusion or 'unknown'}"
            await _emit_deployment_event(chat_id=chat_id, status="failed", data={"app_id": app_id, "error": error_msg})
            if tlog and _log_tool_event:
                _log_tool_event(tlog, action="failed", status="error", repo_full_name=repo_full_name, error=error_msg)
            return ExportResult(
                success=False,
                repo_url=str(repo_url) if isinstance(repo_url, str) else None,
                repo_full_name=str(repo_full_name) if isinstance(repo_full_name, str) else None,
                base_commit_sha=str(base_commit_sha) if isinstance(base_commit_sha, str) else None,
                job_id=str(workflow_run_id) if workflow_run_id is not None else None,
                workflow_run_url=str(workflow_run_url) if isinstance(workflow_run_url, str) else None,
                deployment_url=str(deployment_url) if isinstance(deployment_url, str) else None,
                error=error_msg,
            )

        except RuntimeError as e:
            error_msg = f"Request error: {e}"
            wf_logger.error(error_msg)
            await _emit_deployment_event(chat_id=chat_id, status="failed", data={"app_id": app_id, "error": error_msg})
            return ExportResult(success=False, error=error_msg)
        except Exception as e:  # pragma: no cover
            error_msg = f"Unexpected error: {e}"
            wf_logger.exception(error_msg)
            await _emit_deployment_event(chat_id=chat_id, status="failed", data={"app_id": app_id, "error": error_msg})
            return ExportResult(success=False, error=error_msg)

    def _format_backend_path(self, template: str, *, app_id: str) -> str:
        t = str(template or "").strip() or ""
        if not t:
            return ""
        return (
            t.replace("{app_id}", app_id)
            .replace("{{app_id}}", app_id)
            .replace("{appId}", app_id)
            .replace("{{appId}}", app_id)
        )

    async def get_repo_manifest(
        self,
        *,
        app_id: str,
        repo_url: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch repo file manifest (sha256 by path) for conflict detection.

        Expected backend response:
        {
          "repoUrl": "...",
          "baseCommitSha": "...",
          "files": [{"path":"src/App.jsx","sha256":"..."}, ...]
        }
        """
        if not app_id or not str(app_id).strip():
            raise ValueError("app_id is required")
        if not repo_url or not str(repo_url).strip():
            raise ValueError("repo_url is required")
        if not self.internal_api_key:
            raise ValueError("INTERNAL_API_KEY is required for repo manifest endpoint")
        if self.internal_api_key and (not user_id or not str(user_id).strip()):
            raise ValueError("user_id is required when INTERNAL_API_KEY is configured")

        path_template = os.getenv(
            "MOZAIKS_BACKEND_REPO_MANIFEST_PATH",
            "/api/apps/{app_id}/deploy/repo/manifest",
        )
        endpoint = self._format_backend_path(path_template, app_id=str(app_id).strip())
        payload: Dict[str, Any] = {"repoUrl": str(repo_url).strip(), "userId": str(user_id).strip() if user_id else None}

        data = await self.backend_client.post(endpoint, json=payload, error_msg="Failed to get repo manifest")
        return data if isinstance(data, dict) else {"_raw": data}

    async def create_pull_request(
        self,
        *,
        app_id: str,
        repo_url: str,
        base_commit_sha: str,
        branch_name: str,
        title: str,
        body: str,
        changes: List[Dict[str, Any]],
        patch_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Request backend to create a branch + PR for an update patchset.

        Backend owns GitHub credentials; MozaiksCore never pushes directly.
        """
        if not app_id or not str(app_id).strip():
            raise ValueError("app_id is required")
        if not repo_url or not str(repo_url).strip():
            raise ValueError("repo_url is required")
        if not base_commit_sha or not str(base_commit_sha).strip():
            raise ValueError("base_commit_sha is required")
        if not branch_name or not str(branch_name).strip():
            raise ValueError("branch_name is required")
        if not self.internal_api_key:
            raise ValueError("INTERNAL_API_KEY is required for create PR endpoint")
        if self.internal_api_key and (not user_id or not str(user_id).strip()):
            raise ValueError("user_id is required when INTERNAL_API_KEY is configured")

        path_template = os.getenv(
            "MOZAIKS_BACKEND_CREATE_PR_PATH",
            "/api/apps/{app_id}/deploy/repo/pull-requests",
        )
        endpoint = self._format_backend_path(path_template, app_id=str(app_id).strip())

        backend_changes: List[Dict[str, Any]] = []
        for c in changes if isinstance(changes, list) else []:
            if not isinstance(c, dict):
                continue
            path = c.get("path")
            op = c.get("operation") or c.get("op")
            if not isinstance(path, str) or not path.strip():
                continue
            if not isinstance(op, str) or not op.strip():
                continue
            item: Dict[str, Any] = {"path": path.strip(), "operation": op.strip()}
            if op.strip() in {"add", "modify"} and isinstance(c.get("contentBase64"), str):
                item["contentBase64"] = c.get("contentBase64")
            backend_changes.append(item)

        payload: Dict[str, Any] = {
            "repoUrl": str(repo_url).strip(),
            "userId": str(user_id).strip() if user_id else None,
            "baseCommitSha": str(base_commit_sha).strip(),
            "branchName": str(branch_name).strip(),
            "title": str(title or "").strip() or "Mozaiks update",
            "body": str(body or "").strip(),
            "changes": backend_changes,
        }
        if patch_id and str(patch_id).strip():
            payload["patchId"] = str(patch_id).strip()

        data = await self.backend_client.post(endpoint, json=payload, error_msg="Failed to create PR")
        return data if isinstance(data, dict) else {"_raw": data}

    async def initial_export(
        self,
        *,
        app_id: str,
        files: List[Dict[str, Any]],
        user_id: str,
        repo_name: Optional[str] = None,
        commit_message: Optional[str] = None,
        create_repo: bool = True,
    ) -> Dict[str, Any]:
        """POST /api/apps/{appId}/deploy/repo/initial-export"""
        path_template = os.getenv(
            "MOZAIKS_BACKEND_INITIAL_EXPORT_PATH",
            "/api/apps/{app_id}/deploy/repo/initial-export",
        )
        endpoint = self._format_backend_path(path_template, app_id=str(app_id).strip())
        payload: Dict[str, Any] = {
            "userId": str(user_id).strip(),
            "createRepo": bool(create_repo),
            "files": files if isinstance(files, list) else [],
            "commitMessage": str(commit_message or "").strip() or "Initial export from MozaiksCore",
        }
        if repo_name and str(repo_name).strip():
            payload["repoName"] = str(repo_name).strip()

        data = await self.backend_client.post(endpoint, json=payload, error_msg="Initial export failed")
        return data if isinstance(data, dict) else {"_raw": data}

    async def set_repository_secrets(
        self,
        *,
        app_id: str,
        repo_full_name: str,
        user_id: str,
        include_database_uri: bool = True,
        include_app_api_key: bool = False,
    ) -> Dict[str, Any]:
        """POST /api/apps/{appId}/deploy/repo/secrets"""
        path_template = os.getenv(
            "MOZAIKS_BACKEND_REPO_SECRETS_PATH",
            "/api/apps/{app_id}/deploy/repo/secrets",
        )
        endpoint = self._format_backend_path(path_template, app_id=str(app_id).strip())
        payload: Dict[str, Any] = {
            "userId": str(user_id).strip(),
            "repoFullName": str(repo_full_name).strip(),
            "includeDatabaseUri": bool(include_database_uri),
            "includeAppApiKey": bool(include_app_api_key),
        }
        data = await self.backend_client.post(endpoint, json=payload, error_msg="Failed to set repository secrets")
        return data if isinstance(data, dict) else {"_raw": data}

    async def get_deploy_status(self, *, app_id: str, repo_full_name: str) -> Dict[str, Any]:
        """GET /api/apps/{appId}/deploy/status?repoFullName=org/repo"""
        path_template = os.getenv(
            "MOZAIKS_BACKEND_DEPLOY_STATUS_PATH",
            "/api/apps/{app_id}/deploy/status",
        )
        endpoint = self._format_backend_path(path_template, app_id=str(app_id).strip())
        params = {"repoFullName": str(repo_full_name).strip()}
        data = await self.backend_client.get(endpoint, params=params, error_msg="Failed to get deploy status")
        return data if isinstance(data, dict) else {"_raw": data}

    async def _poll_deployment_status(self, *, app_id: str, repo_full_name: str, chat_id: Optional[str]) -> Dict[str, Any]:
        last_status: Optional[str] = None
        for attempt in range(self.max_poll_attempts):
            status_data = await self.get_deploy_status(app_id=app_id, repo_full_name=repo_full_name)
            status = str(status_data.get("status") or "").lower()
            if status and status != last_status:
                last_status = status
                await _emit_deployment_event(
                    chat_id=chat_id,
                    status="progress",
                    data={
                        "app_id": app_id,
                        "repo_full_name": repo_full_name,
                        "status": status,
                        "attempt": attempt + 1,
                        "max_attempts": self.max_poll_attempts,
                        "message": f"Deployment status: {status}",
                    },
                )

            if status == "completed":
                return status_data
            await asyncio.sleep(self.poll_interval_s)

        return {
            "success": False,
            "status": "completed",
            "workflowRun": {"conclusion": "failure"},
            "error": f"Deployment polling timed out after {int(self.max_poll_attempts * self.poll_interval_s)} seconds",
        }


export_to_github_tool = ExportToGitHubTool()


async def export_to_github(
    bundle_path: str,
    app_id: Optional[str] = None,
    repo_name: Optional[str] = None,
    commit_message: Optional[str] = None,
    user_id: Optional[str] = None,
    workflow_type: Optional[str] = None,
    context_variables: Optional[Any] = None,
) -> dict:
    """Convenience wrapper returning a dict compatible with tool outputs."""

    result = await export_to_github_tool.execute(
        app_id=app_id,
        bundle_path=bundle_path,
        repo_name=repo_name,
        commit_message=commit_message,
        user_id=user_id,
        workflow_type=workflow_type,
        context_variables=context_variables,
    )
    return result.model_dump()


async def _emit_deployment_event(
    *,
    chat_id: Optional[str],
    status: str,
    data: dict,
) -> None:
    if not chat_id:
        return
    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        await transport.send_event_to_ui(
            {"type": f"chat.deployment_{status}", "data": {"timestamp": datetime.now(timezone.utc).isoformat(), **data}},
            chat_id,
        )
    except Exception:
        return
