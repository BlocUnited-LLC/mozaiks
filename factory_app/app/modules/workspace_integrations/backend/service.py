from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .policy import derive_status
from .schemas import (
    CATALOG_BY_ID,
    INTEGRATIONS_CATALOG,
    build_declaration_document,
    build_declaration_response,
    build_integration_response,
)

if TYPE_CHECKING:
    from mozaiksai.core.runtime.composition.module_context import ModuleContext

    from .repo import IntegrationDeclarationsRepo, WorkspaceIntegrationsRepo


class WorkspaceIntegrationsService:
    def __init__(
        self,
        repo: WorkspaceIntegrationsRepo | None = None,
        declarations_repo: IntegrationDeclarationsRepo | None = None,
    ) -> None:
        if repo is None:
            from .repo import WorkspaceIntegrationsRepo
            repo = WorkspaceIntegrationsRepo()
        if declarations_repo is None:
            from .repo import IntegrationDeclarationsRepo
            declarations_repo = IntegrationDeclarationsRepo()
        self.repo = repo
        self.declarations_repo = declarations_repo

    async def list_integrations(
        self,
        ctx: ModuleContext,
        *,
        category: str | None = None,
    ) -> dict[str, Any]:
        notes_list = await self.repo.get_all_notes(ctx)
        notes_by_id = {n["integration_id"]: n.get("note") for n in notes_list}

        catalog = INTEGRATIONS_CATALOG
        if category:
            catalog = [e for e in catalog if e["category"] == category]

        integrations = []
        counts: dict[str, int] = {"configured": 0, "partial": 0, "missing": 0, "unknown": 0}

        for spec in catalog:
            status, missing = derive_status(spec["required_secrets"])
            counts[status] = counts.get(status, 0) + 1
            integrations.append(
                build_integration_response(
                    spec,
                    status=status,
                    missing_secrets=missing,
                    note=notes_by_id.get(spec["id"]),
                )
            )

        return {
            "integrations": integrations,
            "summary": {
                "configured": counts["configured"],
                "partial": counts["partial"],
                "missing": counts["missing"] + counts["unknown"],
                "total": len(integrations),
            },
        }

    async def get_integration(
        self,
        ctx: ModuleContext,
        *,
        integration_id: str,
    ) -> dict[str, Any]:
        spec = CATALOG_BY_ID.get(integration_id)
        if not spec:
            return {"integration": None}

        note_doc = await self.repo.get_note(ctx, integration_id)
        status, missing = derive_status(spec["required_secrets"])

        return {
            "integration": build_integration_response(
                spec,
                status=status,
                missing_secrets=missing,
                note=note_doc.get("note") if note_doc else None,
            )
        }

    async def set_integration_note(
        self,
        ctx: ModuleContext,
        *,
        integration_id: str,
        note: str,
        user_id: str,
    ) -> dict[str, Any]:
        if integration_id not in CATALOG_BY_ID:
            raise ValueError(f"Unknown integration: {integration_id}")

        await self.repo.upsert_note(
            ctx,
            integration_id=integration_id,
            note=note.strip(),
            updated_by=user_id,
        )
        return {"success": True, "integration_id": integration_id}

    async def declare_app_integration_needs(
        self,
        *,
        app_id: str,
        needs: list[dict[str, Any]],
        declared_at: str,
    ) -> dict[str, Any]:
        """Persist integration declarations from a factory build session.

        Called by save_integration_manifest factory tool after IntegrationReadinessAgent
        resolves what the app needs. Declarations are keyed by (app_id, service) so
        re-running a build overwrites the previous state cleanly.
        """
        if not app_id or not needs:
            return {"saved": 0, "app_id": app_id}

        docs = [
            build_declaration_document(
                app_id=app_id,
                service=str(n.get("service") or "").strip(),
                catalog_id=n.get("catalog_id"),
                display_name=n.get("display_name"),
                kind=str(n.get("kind") or "api_key"),
                purpose=n.get("purpose"),
                required_at=str(n.get("required_at") or "runtime"),
                optional=bool(n.get("optional", False)),
                workspace_status=n.get("workspace_status"),
                connector_status=str(n.get("connector_status") or "not_configured"),
                declared_at=declared_at,
            )
            for n in needs
            if n.get("service")
        ]

        saved = await self.declarations_repo.upsert_declarations(app_id=app_id, declarations=docs)
        return {"saved": len(saved), "app_id": app_id}

    async def save_workspace_connector(
        self,
        *,
        workspace_id: str,
        service: str,
        secret_value: str,
        display_name: str | None = None,
        user_id: str | None = None,
        public_config: dict[str, Any] | None = None,
        required_fields: list[dict[str, Any]] | None = None,
        ttl_days: int = 30,
    ) -> dict[str, Any]:
        from mozaiksai.core.data.persistence import ConnectorStore
        from mozaiksai.core.workflow.generator_support.connector_service import save_connector
        return await save_connector(
            scope=ConnectorStore.SCOPE_WORKSPACE,
            scope_id=str(workspace_id),
            service=service,
            secret_value=secret_value,
            display_name=display_name,
            user_id=user_id,
            public_config=public_config,
            required_fields=required_fields,
            ttl_days=ttl_days,
        )

    async def list_workspace_connectors(
        self,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        from mozaiksai.core.data.persistence import ConnectorStore
        from mozaiksai.core.workflow.generator_support.connector_service import list_connectors
        connectors = await list_connectors(scope=ConnectorStore.SCOPE_WORKSPACE, scope_id=str(workspace_id))
        return {"connectors": connectors, "total": len(connectors)}

    async def delete_workspace_connector(
        self,
        *,
        workspace_id: str,
        service: str,
    ) -> dict[str, Any]:
        from mozaiksai.core.data.persistence import ConnectorStore
        from mozaiksai.core.workflow.generator_support.connector_service import delete_connector
        return await delete_connector(
            scope=ConnectorStore.SCOPE_WORKSPACE,
            scope_id=str(workspace_id),
            service=service,
        )

    async def list_app_integration_needs(
        self,
        *,
        app_id: str,
    ) -> dict[str, Any]:
        """Return all integration declarations for an app with live workspace status overlay."""
        docs = await self.declarations_repo.get_for_app(app_id=app_id)

        # Overlay current workspace catalog status for catalog-matched entries.
        results = []
        for doc in docs:
            response = build_declaration_response(doc)
            catalog_id = doc.get("catalog_id")
            if catalog_id and catalog_id in CATALOG_BY_ID:
                live_status, _ = derive_status(CATALOG_BY_ID[catalog_id]["required_secrets"])
                response["workspace_status"] = live_status
                if live_status == "missing":
                    response["setup_url"] = f"/integrations/{catalog_id}"
                else:
                    response.pop("setup_url", None)
            results.append(response)

        required = [r for r in results if not r.get("optional")]
        blocking = [r for r in required if r.get("workspace_status") in {"missing", "partial"} or r.get("connector_status") == "not_configured"]
        return {
            "app_id": app_id,
            "declarations": results,
            "summary": {
                "total": len(results),
                "required": len(required),
                "blocking": len(blocking),
            },
        }
