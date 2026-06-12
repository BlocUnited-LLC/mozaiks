from __future__ import annotations

import importlib
import logging
import os
from datetime import UTC, datetime
from typing import Any

from mozaiksai.core.transport.session_registry import session_registry

logger = logging.getLogger("simple_transport.general_mode")


def _load_general_agent_service():
    """Load the pluggable general-mode capability executor."""
    module_path = os.getenv("MOZAIKS_GENERAL_AGENT_MODULE", "mozaiksai.core.capabilities.simple_llm")
    factory_name = os.getenv("MOZAIKS_GENERAL_AGENT_FACTORY", "get_general_capability_service")
    try:
        module = importlib.import_module(module_path)
        factory = getattr(module, factory_name, None)
        if callable(factory):
            return factory()
        logger.debug(
            "General agent factory not callable",
            extra={"module": module_path, "factory": factory_name},
        )
    except Exception as exc:
        logger.debug(
            "General agent service unavailable",
            extra={"module": module_path, "factory": factory_name, "error": str(exc)},
        )
    return None


class GeneralModeMixin:
    """Mixin providing general-mode routing and persistence helpers."""

    async def _ensure_general_chat_context(
        self,
        *,
        chat_id: str,
        force_new: bool = False,
        requested_general_chat_id: str | None = None,
    ) -> dict[str, Any]:
        """Return or create the general chat context for a websocket connection."""

        conn = self.connections.get(chat_id)
        if not conn:
            raise RuntimeError(f"No active connection metadata for chat {chat_id}")

        if not force_new:
            existing_ctx = conn.get("general_session")
            if isinstance(existing_ctx, dict) and existing_ctx.get("chat_id"):
                return existing_ctx

        app_id = conn.get("app_id")
        user_id = conn.get("user_id") or "anonymous"
        if not app_id:
            raise RuntimeError("Cannot create general chat without app context")

        pm = self._get_or_create_persistence_manager()
        if requested_general_chat_id and not force_new:
            transcript = await pm.fetch_general_chat_transcript(
                general_chat_id=str(requested_general_chat_id),
                app_id=str(app_id),
                limit=1,
            )
            if transcript and str(transcript.get("user_id") or "") == str(user_id):
                general_ctx = {
                    "chat_id": transcript.get("chat_id"),
                    "label": transcript.get("label"),
                    "sequence": transcript.get("sequence"),
                    "app_id": str(app_id),
                    "user_id": str(user_id),
                    "created_at": (
                        transcript.get("created_at").isoformat()
                        if hasattr(transcript.get("created_at"), "isoformat")
                        else transcript.get("created_at")
                    ),
                }
                conn["general_session"] = general_ctx
                return general_ctx

        session_info = await pm.create_general_chat_session(
            app_id=str(app_id),
            user_id=str(user_id),
        )

        general_ctx = {
            "chat_id": session_info.get("chat_id"),
            "label": session_info.get("label"),
            "sequence": session_info.get("sequence"),
            "app_id": str(app_id),
            "user_id": str(user_id),
            "created_at": datetime.now(UTC).isoformat(),
        }
        conn["general_session"] = general_ctx
        return general_ctx

    async def _handle_general_agent_exchange(
        self,
        *,
        chat_id: str,
        ws_id: int | None,
        user_message: str,
        ui_context: dict[str, Any] | None = None,
    ) -> None:
        """Route a general-mode utterance to the configured capability executor."""

        conn = self.connections.get(chat_id) or {}
        app_id = conn.get("app_id")
        user_id = conn.get("user_id")
        if not app_id:
            raise RuntimeError("Cannot route general-mode message without app context")

        requested_general_chat_id = None
        if isinstance(ui_context, dict):
            requested_general_chat_id = ui_context.get("general_chat_id") or ui_context.get("requested_general_chat_id")
        general_ctx = await self._ensure_general_chat_context(
            chat_id=chat_id,
            requested_general_chat_id=requested_general_chat_id,
        )
        general_chat_id = general_ctx.get("chat_id")
        general_label = general_ctx.get("label")
        if not general_chat_id:
            raise RuntimeError("Failed to resolve general chat identifier")

        workflows_payload: list[dict[str, Any]] = []
        if ws_id:
            contexts = session_registry.get_all_workflows(ws_id)
            for ctx in contexts:
                if hasattr(ctx, "to_dict"):
                    workflows_payload.append(ctx.to_dict())
                else:
                    workflows_payload.append(
                        {
                            "chat_id": getattr(ctx, "chat_id", None),
                            "workflow_name": getattr(ctx, "workflow_name", None),
                            "status": getattr(ctx, "status", None),
                            "artifact_id": getattr(ctx, "artifact_id", None),
                            "app_id": getattr(ctx, "app_id", None),
                            "user_id": getattr(ctx, "user_id", None),
                        }
                    )

        metadata_base = {
            "source": "general_agent",
            "ui_context": ui_context or {},
            "workflows": workflows_payload,
            "general_chat_id": general_chat_id,
            "general_chat_label": general_label,
        }

        await self._persist_general_message(
            general_chat_id=str(general_chat_id),
            app_id=str(app_id),
            role="user",
            content=user_message,
            user_id=str(user_id) if user_id else None,
            metadata=metadata_base,
        )

        await self.send_event_to_ui(
            {
                "kind": "text",
                "agent": "user",
                "content": user_message,
                "chat_id": chat_id,
                "metadata": metadata_base,
            },
            chat_id,
        )

        service = _load_general_agent_service()
        if service is None:
            await self.send_chat_message(
                "General mode is not configured for this runtime.",
                agent_name="System",
                chat_id=chat_id,
                metadata=metadata_base,
            )
            return

        response = await service.generate_response(
            prompt=user_message,
            workflows=workflows_payload,
            app_id=str(app_id),
            user_id=str(user_id) if user_id else None,
            ui_context=ui_context,
        )

        assistant_metadata = {
            "source": "general_agent",
            "workflows": workflows_payload,
            "ui_context": ui_context or {},
            "general_chat_id": general_chat_id,
            "general_chat_label": general_label,
        }

        await self._persist_general_message(
            general_chat_id=str(general_chat_id),
            app_id=str(app_id),
            role="assistant",
            content=response.get("content", ""),
            user_id=str(user_id) if user_id else None,
            metadata=assistant_metadata,
        )

        await self.send_chat_message(
            response.get("content", ""),
            agent_name="Assistant",
            chat_id=chat_id,
            metadata=assistant_metadata,
        )

        usage = response.get("usage") or {}
        try:
            from mozaiksai.core.tokens.manager import TokenManager

            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            await TokenManager.emit_usage_delta(
                chat_id=str(general_chat_id),
                app_id=str(app_id),
                user_id=str(user_id) if user_id else "anonymous",
                workflow_name="GeneralCapability",
                agent_name="assistant",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=int(usage.get("total_tokens") or (prompt_tokens + completion_tokens)),
                cached=False,
                duration_sec=0.0,
            )
        except Exception as metrics_err:
            logger.debug("Failed to emit general-mode usage delta: %s", metrics_err)

    async def _persist_general_message(
        self,
        *,
        general_chat_id: str,
        app_id: str,
        role: str,
        content: str,
        user_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pm = self._get_or_create_persistence_manager()
        try:
            await pm.append_general_message(
                general_chat_id=general_chat_id,
                app_id=app_id,
                role=role,
                content=content,
                user_id=user_id,
                metadata=metadata,
            )
        except Exception as persist_err:
            logger.debug(
                "Failed to persist general agent message for %s: %s",
                general_chat_id,
                persist_err,
            )
