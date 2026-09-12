"""Generated-app build identity resolution.

A factory build session executes under the Studio workspace's runtime
``app_id``; the app being generated has its own registry identity, reserved by
ValueEngine's ``create_app_record`` lifecycle tool and carried through the
build journey as the declared ``generated_app_id`` context variable. Runtime
identity is immutable during a run (see
``mozaiksai.core.workflow.context.authority.require_unchanged_runtime_authority``),
so the generated app's identity must never be written into ``app_id``.

Artifact-store records, generated staging paths, app-registry updates, and
app-context registration are scoped by the generated app identity. Chat and
session persistence stay scoped by the executing runtime ``app_id``.

When no registry identity exists (registry-less OSS runs where
``create_app_record`` could not reserve one), artifacts scope under the
executing ``app_id`` — the same degraded behavior those runs had before this
contract existed.
"""

from __future__ import annotations

from typing import Any


def _context_get(context_variables: Any | None, key: str) -> Any:
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key)
        except Exception:
            pass
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    return None


def resolve_generated_app_id(context_variables: Any | None) -> str | None:
    """Return the generated app's identity for artifact/registry scoping.

    Prefers the declared ``generated_app_id`` context variable; falls back to
    the executing ``app_id`` when no registry identity was reserved.
    """
    generated = str(_context_get(context_variables, "generated_app_id") or "").strip()
    if generated:
        return generated
    executing = str(_context_get(context_variables, "app_id") or "").strip()
    return executing or None


async def read_generated_app_id_for_chat(*, app_id: str, chat_id: str) -> str | None:
    """Resolve the generated app identity from a persisted chat session.

    Used by run-level lifecycle hooks (``on_complete``) that receive only
    runtime identity kwargs and no live context object. Falls back to the
    executing ``app_id`` when the chat carries no ``generated_app_id``.
    """
    resolved_app_id = str(app_id or "").strip()
    resolved_chat_id = str(chat_id or "").strip()
    if not resolved_app_id or not resolved_chat_id:
        return resolved_app_id or None
    try:
        from mozaiksai.core.core_config import get_mongo_client
        from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
        from mozaiksai.core.multitenant import build_app_scope_filter

        client = get_mongo_client()
        coll = client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]
        doc = await coll.find_one(
            {"_id": resolved_chat_id, **build_app_scope_filter(resolved_app_id)},
            {"context_variables.generated_app_id": 1, "generated_app_id": 1},
        )
        if isinstance(doc, dict):
            top_level = str(doc.get("generated_app_id") or "").strip()
            if top_level:
                return top_level
            ctx = doc.get("context_variables")
            if isinstance(ctx, dict):
                nested = str(ctx.get("generated_app_id") or "").strip()
                if nested:
                    return nested
    except Exception:
        pass
    return resolved_app_id


__all__ = ["read_generated_app_id_for_chat", "resolve_generated_app_id"]
