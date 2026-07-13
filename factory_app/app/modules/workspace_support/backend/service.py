from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class WorkspaceSupportService:
    async def create_support_request(
        self,
        ctx,
        *,
        message: str,
        page_url: str | None = None,
        page_title: str | None = None,
        severity: str = "low",
    ) -> dict:
        now = datetime.now(tz=UTC)
        request_id = f"sr_{int(now.timestamp())}"
        created_at = now.isoformat()

        doc = {
            "request_id": request_id,
            "app_id": ctx.app_id,
            "user_id": ctx.user_id,
            "message": message,
            "page_url": page_url,
            "page_title": page_title,
            "severity": severity,
            "status": "open",
            "created_at": created_at,
            "resolved_at": None,
            "notes": None,
        }

        try:
            await ctx.persistence.collection("workspace_support", "requests").insert_one(doc)
        except Exception:
            logger.warning(
                "workspace_support: persistence unavailable, support request %s not stored",
                request_id,
            )

        try:
            await ctx.emit(
                "domain.workspace_support.request_created",
                {
                    "request_id": request_id,
                    "app_id": ctx.app_id,
                    "severity": severity,
                    "message": message,
                    "page_url": page_url,
                    "page_title": page_title,
                },
            )
        except Exception:
            logger.warning(
                "workspace_support: event emission failed for request %s",
                request_id,
            )

        return {"request_id": request_id, "status": "open", "created_at": created_at}

    async def list_support_requests(
        self,
        ctx,
        *,
        status: str = "open",
        limit: int = 50,
    ) -> dict:
        query: dict = {"app_id": ctx.app_id}
        if status != "all":
            query["status"] = status

        try:
            cursor = ctx.persistence.collection("workspace_support", "requests").find(
                query,
                limit=limit,
                sort=[("created_at", -1)],
            )
            requests = [doc async for doc in cursor]
        except Exception:
            logger.warning("workspace_support: persistence unavailable, returning empty list")
            requests = []

        return {"requests": requests, "total": len(requests)}

    async def submit_session_feedback(
        self,
        ctx,
        *,
        session_id: str | None = None,
        workflow_name: str | None = None,
        rating: int = 1,
    ) -> dict:
        now = datetime.now(tz=UTC)
        feedback_id = f"fb_{int(now.timestamp())}"
        created_at = now.isoformat()

        doc = {
            "feedback_id": feedback_id,
            "app_id": ctx.app_id,
            "user_id": ctx.user_id,
            "session_id": session_id,
            "workflow_name": workflow_name,
            "rating": rating,
            "created_at": created_at,
        }

        try:
            await ctx.persistence.collection("workspace_support", "feedback").insert_one(doc)
        except Exception:
            logger.warning(
                "workspace_support: persistence unavailable, feedback %s not stored",
                feedback_id,
            )

        if rating == 0:
            try:
                await ctx.emit(
                    "domain.workspace_support.negative_feedback",
                    {
                        "feedback_id": feedback_id,
                        "app_id": ctx.app_id,
                        "session_id": session_id,
                        "workflow_name": workflow_name,
                    },
                )
            except Exception:
                logger.warning(
                    "workspace_support: event emission failed for feedback %s",
                    feedback_id,
                )

        return {"feedback_id": feedback_id, "rating": rating, "created_at": created_at}
