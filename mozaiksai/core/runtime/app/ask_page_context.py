# ==============================================================================
# FILE: mozaiksai/core/runtime/app/ask_page_context.py
# DESCRIPTION: Resolve page-declared ask-context actions into prompt lines.
#              Pages declare read-only module actions (meta.ask_context) whose
#              results ground ask-mode answers in live app data.
# ==============================================================================
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("ask_page_context")

# Bound the prompt cost of page-declared context: at most this many actions
# resolve per exchange, and each rendered payload is truncated.
MAX_DECLARED_ACTIONS = 3
MAX_RENDERED_CHARS = 1600



def _coerce_params(params: Any) -> dict[str, Any]:
    """Accept both declaration shapes: a plain mapping or [{key, value}] pairs."""
    if isinstance(params, dict):
        return dict(params)
    if isinstance(params, (list, tuple)):
        coerced: dict[str, Any] = {}
        for item in params:
            if isinstance(item, dict) and "key" in item:
                key = str(item.get("key") or "").strip()
                if key:
                    coerced[key] = item.get("value")
        return coerced
    return {}


def normalize_ask_context_declarations(raw: Any) -> list[dict[str, Any]]:
    """Validate a page's declared ask-context actions, dropping malformed entries."""
    if not isinstance(raw, (list, tuple)):
        return []
    declarations: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        module = str(entry.get("module") or "").strip()
        action = str(entry.get("action") or "").strip()
        if not module or not action:
            continue
        label = str(entry.get("label") or "").strip() or f"{module}.{action}"
        declarations.append(
            {
                "module": module,
                "action": action,
                "params": _coerce_params(entry.get("params")),
                "label": label,
            }
        )
        if len(declarations) >= MAX_DECLARED_ACTIONS:
            if len(raw) > MAX_DECLARED_ACTIONS:
                logger.debug(
                    "ASK_PAGE_CONTEXT: %d declared actions exceed the limit of %d — extra entries ignored",
                    len(raw),
                    MAX_DECLARED_ACTIONS,
                )
            break
    return declarations


def _render_result(data: Any) -> str:
    try:
        rendered = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        rendered = str(data)
    if len(rendered) > MAX_RENDERED_CHARS:
        rendered = rendered[:MAX_RENDERED_CHARS] + " …(truncated)"
    return rendered


async def resolve_page_ask_context(
    declarations: list[dict[str, Any]],
    *,
    app: Any,
    app_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Dispatch page-declared read-only module actions into ask-context lines.

    Fail-closed eligibility: an action resolves only when its module opts in
    with ``ask_context_safe: true`` in ``module.yaml``. Eligibility is a
    separate declaration from ``api_surface`` on purpose — opting an action
    into ask context must never widen its external HTTP exposure.

    Dispatch runs in enforce mode with an empty permission grant, so any
    action declaring required permissions is denied by the module executor,
    and the requested scope passes through the host ``module_scope`` resolver
    exactly as HTTP and workflow dispatch do. A failing entry is skipped — it
    never breaks the ask exchange.
    """
    if not declarations:
        return {}
    eligibility = getattr(getattr(app, "state", None), "module_ask_context_actions", None)
    if not isinstance(eligibility, dict):
        logger.debug("ASK_PAGE_CONTEXT: ask-context eligibility map unavailable — skipping")
        return {}

    from mozaiksai.core.runtime.composition import (
        ModuleActionDispatchRequest,
        ModuleDispatchAuthority,
        ModuleDispatchScope,
        dispatch_module_action,
        get_platform_hooks,
    )

    context: dict[str, Any] = {}
    for declaration in declarations:
        module = declaration["module"]
        action = declaration["action"]
        module_eligibility = eligibility.get(module)
        eligible = (
            bool(module_eligibility.get(action)) if isinstance(module_eligibility, dict) else False
        )
        if not eligible:
            logger.debug(
                "ASK_PAGE_CONTEXT: %s.%s skipped — module.yaml does not declare ask_context_safe",
                module,
                action,
            )
            continue
        try:
            scope = await get_platform_hooks().call_module_scope(
                principal=None,
                module_name=module,
                action_name=action,
                requested_scope={"app_id": app_id, "user_id": user_id},
                params=declaration["params"],
                default_permissions=[],
                fail_closed=True,
            )
            # A resolver may narrow tenant/workspace membership; it may never
            # turn an ask exchange into a different actor or app.
            if scope.get("app_id") != app_id or scope.get("user_id") != user_id:
                logger.debug(
                    "ASK_PAGE_CONTEXT: %s.%s skipped — scope resolver changed the actor or app",
                    module,
                    action,
                )
                continue
            result = await dispatch_module_action(
                ModuleActionDispatchRequest(
                    module=module,
                    action=action,
                    params=declaration["params"],
                    scope=ModuleDispatchScope(
                        app_id=app_id,
                        user_id=user_id,
                        tenant_id=scope.get("tenant_id"),
                        workspace_id=scope.get("workspace_id"),
                    ),
                    # Permissions stay empty by design: a scope resolver may
                    # narrow this dispatch, never widen it. Only actions that
                    # require no permissions can ground an ask answer.
                    authority=ModuleDispatchAuthority(
                        kind="authenticated_user",
                        permission_mode="enforce",
                        reason="page_declared_ask_context",
                        actor_id=user_id,
                        permissions=(),
                    ),
                ),
                app=app,
            )
        except Exception as dispatch_err:
            logger.debug(
                "ASK_PAGE_CONTEXT: %s.%s dispatch failed: %s", module, action, dispatch_err
            )
            continue
        if not getattr(result, "success", False):
            logger.debug(
                "ASK_PAGE_CONTEXT: %s.%s did not resolve (%s)",
                module,
                action,
                getattr(result, "error_code", None),
            )
            continue
        context[declaration["label"]] = _render_result(getattr(result, "data", None))
    return context
