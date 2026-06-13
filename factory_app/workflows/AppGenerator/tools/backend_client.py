"""
AppGenerator Backend Client.

Extends the core BackendClient with methods specific to the AppGenerator workflow:
- App specifications
- Deployment/Export operations
- Template generation
- App Scaffolding
- Database Provisioning
"""

import base64
import os
import zipfile
from typing import Any

from logs.logging_config import get_workflow_logger
from mozaiksai.core.workflow.generator_support.backend_client import BackendClient

logger = get_workflow_logger("app_gen_backend_client")

class AppGeneratorBackendClient(BackendClient):
    """
    Backend client specialized for AppGenerator workflow operations.
    """

    # ------------------------------------------------------------------
    # App Generation & Specs
    # ------------------------------------------------------------------

    async def get_app_spec(self, app_id: str) -> dict[str, Any]:
        """GET /api/apps/{appId}/appgen/spec"""
        return await self.get(f"/api/apps/{app_id}/appgen/spec", error_msg="Failed to get app spec")

    async def get_supported_stacks(self) -> dict[str, Any]:
        """GET /api/appgen/supported-stacks"""
        return await self.get("/api/appgen/supported-stacks", error_msg="Failed to get supported stacks")

    # ------------------------------------------------------------------
    # Deployment & Repo Operations
    # ------------------------------------------------------------------

    async def get_repo_manifest(self, app_id: str, repo_url: str, user_id: str | None = None) -> dict[str, Any]:
        """POST /api/apps/{appId}/deploy/repo/manifest"""
        payload = {
            "repoUrl": repo_url,
            "userId": user_id
        }
        return await self.post(f"/api/apps/{app_id}/deploy/repo/manifest", json=payload, error_msg="Failed to get repo manifest")

    async def initial_export(self, app_id: str, bundle_path: str, repo_name: str | None, commit_message: str | None, user_id: str | None) -> dict[str, Any]:
        """POST /api/apps/{appId}/deploy/repo/initial-export"""
        
        files_payload = []
        
        if not os.path.exists(bundle_path):
             raise FileNotFoundError(f"Bundle not found: {bundle_path}")

        try:
            with zipfile.ZipFile(bundle_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    if file_info.is_dir():
                        continue
                    with zip_ref.open(file_info) as f:
                        content = f.read()
                        files_payload.append({
                            "path": file_info.filename,
                            "operation": "add",
                            "contentBase64": base64.b64encode(content).decode('utf-8')
                        })
        except Exception as e:
            raise RuntimeError(f"Failed to process bundle for export: {e}") from e

        payload = {
            "repoUrl": None, # Optional if createRepo=true
            "userId": user_id,
            "createRepo": True,
            "repoName": repo_name,
            "files": files_payload,
            "commitMessage": commit_message or "Initial export from MozaiksAI"
        }

        return await self.post(f"/api/apps/{app_id}/deploy/repo/initial-export", json=payload, error_msg="Failed to export app")

    async def create_pull_request(self, app_id: str, repo_url: str, base_commit_sha: str, branch_name: str, title: str, body: str, changes: list[dict], conflicts: list[dict], patch_id: str | None, user_id: str | None) -> dict[str, Any]:
        """POST /api/apps/{appId}/deploy/repo/pull-requests"""
        
        payload = {
            "repoUrl": repo_url,
            "baseCommitSha": base_commit_sha,
            "branchName": branch_name,
            "title": title,
            "body": body,
            "changes": changes,
            "patchId": patch_id,
            "userId": user_id
        }
        return await self.post(f"/api/apps/{app_id}/deploy/repo/pull-requests", json=payload, error_msg="Failed to create PR")

    async def generate_template(
        self,
        app_id: str,
        tech_stack: dict[str, Any],
        include_workflow: bool = True,
        include_dockerfiles: bool = True,
        deployment_profile: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/apps/{appId}/deploy/templates/generate"""
        payload = {
            "userId": user_id,
            "techStack": tech_stack,
            "includeWorkflow": include_workflow,
            "includeDockerfiles": include_dockerfiles,
            "deploymentProfile": deployment_profile,
            "outputFormat": "files"
        }
        return await self.post(f"/api/apps/{app_id}/deploy/templates/generate", json=payload, error_msg="Failed to generate templates")

    async def generate_scaffold(
        self,
        app_id: str,
        dependencies: dict[str, list[str]],
        tech_stack_override: dict[str, Any] | None = None,
        include_dockerfiles: bool = True,
        include_workflow: bool = True,
        include_compose: bool = False,
        deployment_profile: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/apps/{appId}/deploy/scaffold"""
        payload = {
            "userId": user_id,
            "dependencies": dependencies,
            "includeDockerfiles": include_dockerfiles,
            "includeWorkflow": include_workflow,
            "includeCompose": include_compose,
            "deploymentProfile": deployment_profile,
            "includeBoilerplate": True,
            "includeInitFiles": True,
            "techStackOverride": tech_stack_override
        }
        return await self.post(f"/api/apps/{app_id}/deploy/scaffold", json=payload, error_msg="Failed to generate scaffold")

    async def get_database_status(self, app_id: str, user_id: str | None = None) -> dict[str, Any]:
        """GET /api/apps/{appId}/database/status"""
        params = {"userId": user_id or "mozaiksai"}
        return await self.get(f"/api/apps/{app_id}/database/status", params=params, error_msg="Failed to get database status")

    async def get_artifact_schema(self, app_id: str, artifact_version_id: str) -> dict[str, Any]:
        """
        GET /api/apps/{appId}/database/schema/{artifactVersionId}

        Returns the recorded data contract/schema for the given artifact version.
        Canonical generated bundles stage data/contract.json and
        optional data/migrations/*.json; these endpoints are not the
        current source of truth until platform database adapter support lands.
        Response: {"schema": <dict | null>}
        Returns {"schema": null} when no schema is recorded for that version.
        """
        return await self.get(
            f"/api/apps/{app_id}/database/schema/{artifact_version_id}",
            error_msg="Failed to fetch artifact schema",
        )

    async def apply_schema_migration(
        self,
        app_id: str,
        migration: dict[str, Any],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /api/apps/{appId}/database/migrate

        Apply the safe ops in a migration document produced by schema_migration.py.
        The backend applies only additive ops (new collections, new fields, new indexes)
        and records the migration_id in the app's migration history.

        Payload: {"userId": str, "migration": <migration dict>}
        Response: {"success": bool, "migration_id": str, "applied_ops": [...], "skipped_ops": [...]}
        """
        payload = {
            "userId": user_id or "mozaiksai",
            "migration": migration,
        }
        return await self.post(
            f"/api/apps/{app_id}/database/migrate",
            json=payload,
            error_msg="Failed to apply schema migration",
        )

# Singleton instance
app_gen_backend_client = AppGeneratorBackendClient()



