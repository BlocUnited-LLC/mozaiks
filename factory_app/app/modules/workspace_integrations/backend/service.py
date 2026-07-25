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

    @staticmethod
    def _declaration_from_need(
        *,
        app_id: str,
        need: dict[str, Any],
        declared_at: str,
    ) -> dict[str, Any] | None:
        service = str(need.get("service") or "").strip()
        if not app_id or not service:
            return None
        catalog_id = need.get("catalog_id") or (service if service in CATALOG_BY_ID else None)
        catalog_spec = CATALOG_BY_ID.get(str(catalog_id)) if catalog_id else None
        workspace_status = need.get("workspace_status")
        if workspace_status is None and catalog_spec:
            workspace_status, _ = derive_status(catalog_spec["required_secrets"])
        required_fields = need.get("required_fields") if isinstance(need.get("required_fields"), list) else []
        allowed_setup_lanes = need.get("allowed_setup_lanes")
        if not isinstance(allowed_setup_lanes, list) and catalog_spec:
            allowed_setup_lanes = catalog_spec.get("allowed_setup_lanes")
        return build_declaration_document(
            app_id=app_id,
            service=service,
            catalog_id=str(catalog_id) if catalog_id else None,
            display_name=need.get("display_name") or (catalog_spec or {}).get("name") or service,
            kind=str(need.get("kind") or "api_key"),
            purpose=need.get("purpose"),
            required_at=str(need.get("required_at") or "runtime"),
            optional=bool(need.get("optional", False)),
            workspace_status=workspace_status,
            connector_status=str(need.get("connector_status") or "not_configured"),
            defaulted=bool(need.get("defaulted", False)),
            removable=bool(need.get("removable", False)),
            source=str(need.get("source") or "build"),
            required_fields=required_fields,
            preferred_setup_lane=need.get("preferred_setup_lane") or (catalog_spec or {}).get("preferred_setup_lane"),
            allowed_setup_lanes=allowed_setup_lanes,
            managed_default=need.get("managed_default") or (catalog_spec or {}).get("managed_default"),
            declared_at=declared_at,
        )

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
        catalog_ids = [entry["id"] for entry in catalog]
        try:
            usage_counts = await self.declarations_repo.get_catalog_usage_counts(catalog_ids=catalog_ids)
        except Exception:
            usage_counts = {}

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
            integrations[-1]["app_usage_count"] = int(usage_counts.get(spec["id"], 0))

        return {
            "integrations": integrations,
            "summary": {
                "configured": counts["configured"],
                "partial": counts["partial"],
                "missing": counts["missing"] + counts["unknown"],
                "used": sum(1 for count in usage_counts.values() if count > 0),
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
            doc
            for n in needs
            if isinstance(n, dict)
            for doc in [self._declaration_from_need(app_id=app_id, need=n, declared_at=declared_at)]
            if doc is not None
        ]

        saved = await self.declarations_repo.upsert_declarations(app_id=app_id, declarations=docs)
        return {"saved": len(saved), "app_id": app_id}

    async def upsert_app_integration_need(
        self,
        *,
        app_id: str,
        need: dict[str, Any],
        declared_at: str,
    ) -> dict[str, Any]:
        """Create or update one app integration declaration."""
        doc = self._declaration_from_need(app_id=app_id, need=need, declared_at=declared_at)
        if doc is None:
            raise ValueError("app_id and need.service are required")
        saved = await self.declarations_repo.upsert_declarations(app_id=app_id, declarations=[doc])
        declaration = build_declaration_response(saved[0]) if saved else None
        return {"saved": len(saved), "app_id": app_id, "declaration": declaration}

    async def delete_app_integration_need(
        self,
        *,
        app_id: str,
        service: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove an app integration declaration while preserving default-removal intent."""
        deleted = await self.declarations_repo.soft_delete_declaration(
            app_id=app_id,
            service=service,
            removed_by=user_id,
        )
        return {"deleted": deleted, "app_id": app_id, "service": service}

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

    async def check_workspace_connector_health(
        self,
        *,
        workspace_id: str,
        service: str,
    ) -> dict[str, Any]:
        from mozaiksai.core.data.persistence import ConnectorStore
        from mozaiksai.core.workflow.generator_support.connector_health import run_connector_health_check
        return await run_connector_health_check(
            scope=ConnectorStore.SCOPE_WORKSPACE,
            scope_id=str(workspace_id),
            service=service,
            checked_by="manual",
        )

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
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Return all integration declarations for an app with live workspace status overlay."""
        docs = await self.declarations_repo.get_for_app(app_id=app_id)
        connector_by_service: dict[str, dict[str, Any]] = {}
        if workspace_id:
            try:
                connector_payload = await self.list_workspace_connectors(workspace_id=workspace_id)
                connector_by_service = {
                    str(connector.get("service") or ""): connector
                    for connector in connector_payload.get("connectors", [])
                    if isinstance(connector, dict) and connector.get("service")
                }
            except Exception:
                connector_by_service = {}

        # Overlay current workspace catalog status for catalog-matched entries.
        results = []
        for doc in docs:
            if doc.get("removed"):
                continue
            response = build_declaration_response(doc)
            connector = connector_by_service.get(str(response.get("service") or ""))
            if connector:
                response["connector_status"] = "ready" if connector.get("ready") else "partial"
                response["workspace_connector_status"] = response["connector_status"]
            catalog_id = doc.get("catalog_id")
            if catalog_id and catalog_id in CATALOG_BY_ID:
                live_status, _ = derive_status(CATALOG_BY_ID[catalog_id]["required_secrets"])
                if connector and connector.get("ready"):
                    live_status = "configured"
                response["workspace_status"] = live_status
                if live_status == "missing":
                    response["setup_url"] = f"/integrations/{catalog_id}"
                else:
                    response.pop("setup_url", None)
            results.append(response)

        required = [r for r in results if not r.get("optional")]

        def _is_blocking_requirement(item: dict[str, Any]) -> bool:
            if item.get("catalog_id"):
                return item.get("workspace_status") in {"missing", "partial"}
            return item.get("connector_status") in {"not_configured", "partial"}

        blocking = [r for r in required if _is_blocking_requirement(r)]
        return {
            "app_id": app_id,
            "declarations": results,
            "summary": {
                "total": len(results),
                "required": len(required),
                "blocking": len(blocking),
            },
        }
