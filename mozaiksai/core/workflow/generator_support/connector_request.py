"""Shared connector request helpers for generator workflows.

Generator agents can discover integration needs while planning or executing
decomposed work. These helpers keep the UX agentic: check app-scoped
connectors first, ask inline only for unresolved required setup, then persist
through the platform connector store so the app-scoped integrations surface
reflects the result.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from logs.logging_config import get_workflow_logger
from mozaiksai.core.data.persistence import ConnectorStore
from mozaiksai.core.workflow.generator_support.connector_service import (
    get_connector_inventory,
    save_connector,
    save_connector_draft,
)
from mozaiksai.core.workflow.generator_support.connector_setup import (
    SECRET_FIELD_TYPES,
    normalize_required_fields,
    normalize_setup_lane,
    normalize_setup_lanes,
    safe_managed_default,
    setup_lane_ids,
)
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool

_normalize_required_fields = normalize_required_fields
_normalize_setup_lane = normalize_setup_lane
_safe_managed_default = safe_managed_default
_setup_lane_ids = setup_lane_ids


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _context_set(context_variables: Any, key: str, value: Any) -> None:
    if context_variables is None:
        return
    setter = getattr(context_variables, "set", None)
    if callable(setter):
        try:
            setter(key, value)
            return
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value
    elif isinstance(context_variables, dict):
        context_variables[key] = value


def normalize_service(service: Any) -> str:
    return str(service or "").strip().lower().replace(" ", "_")


def display_service(service: str) -> str:
    normalized = normalize_service(service)
    return normalized.replace("_", " ").title()


def _append_need(
    needs: list[dict[str, Any]],
    *,
    service: Any,
    display_name: Any = None,
    kind: str = "api_key",
    provider: Any = None,
    purpose: Any = None,
    required_at: str = "runtime",
    required_fields: list[dict[str, Any]] | None = None,
    required_by: dict[str, Any] | None = None,
    optional: bool = False,
    preferred_setup_lane: Any = None,
    allowed_setup_lanes: Any = None,
    setup_lanes: Any = None,
    managed_default: Any = None,
) -> None:
    normalized = normalize_service(service)
    if not normalized:
        return
    required_fields = _normalize_required_fields(
        {
            "kind": kind,
            "required_fields": required_fields,
        }
    )
    need = {
        "service": normalized,
        "integration_id": normalized,
        "provider": normalize_service(provider) or normalized,
        "display_name": str(display_name or display_service(normalized)),
        "kind": str(kind or "api_key"),
        "purpose": str(purpose or "Required by generated app integration."),
        "required_fields": required_fields,
        "required_at": str(required_at or "runtime"),
        "required_by": dict(required_by or {}),
        "optional": bool(optional),
    }
    if preferred_setup_lane is not None:
        need["preferred_setup_lane"] = preferred_setup_lane
    raw_lanes = allowed_setup_lanes if allowed_setup_lanes is not None else setup_lanes
    if raw_lanes is not None:
        need["allowed_setup_lanes"] = raw_lanes
    safe_managed_default = _safe_managed_default(managed_default)
    if safe_managed_default:
        need["managed_default"] = safe_managed_default
    lanes = normalize_setup_lanes(need)
    need["preferred_setup_lane"] = lanes["preferred_setup_lane"]
    need["allowed_setup_lanes"] = lanes["allowed_setup_lanes"]
    needs.append(need)


def _split_required_fields(fields: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    secret_fields: list[dict[str, Any]] = []
    non_secret_fields: list[dict[str, Any]] = []
    for field in fields:
        if str(field.get("type") or "").lower() in SECRET_FIELD_TYPES:
            secret_fields.append(field)
            continue
        if field.get("frontend_safe") is not False:
            non_secret_fields.append(field)
    return secret_fields, non_secret_fields


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return list(_iter_dicts(value))


def _extract_needs_from_plan(app_build_plan: Any) -> list[dict[str, Any]]:
    needs: list[dict[str, Any]] = []
    if not isinstance(app_build_plan, dict):
        return needs

    for integration in _iter_dicts(app_build_plan.get("external_integrations")):
        service = integration.get("service") or integration.get("name")
        _append_need(
            needs,
            service=service,
            display_name=integration.get("display_name") or integration.get("name"),
            kind=integration.get("kind") or "api_key",
            provider=integration.get("provider"),
            purpose=integration.get("purpose"),
            required_at=integration.get("required_at") or "runtime",
            required_fields=integration.get("required_fields") if isinstance(integration.get("required_fields"), list) else None,
            optional=bool(integration.get("optional", False)),
            required_by={"source": "app_build_plan.external_integrations"},
            preferred_setup_lane=integration.get("preferred_setup_lane"),
            allowed_setup_lanes=integration.get("allowed_setup_lanes"),
            setup_lanes=integration.get("setup_lanes"),
            managed_default=integration.get("managed_default"),
        )

    for pack in _iter_dicts(app_build_plan.get("capability_packs")):
        pack_id = pack.get("capability_pack_id") or pack.get("id") or pack.get("pack_id")
        for requirement in pack.get("required_integrations") or []:
            if isinstance(requirement, dict):
                required_by = {
                    "source": "capability_pack",
                    "capability_pack_id": pack_id,
                }
                required_by.update(_dict_or_empty(requirement.get("required_by")))
                _append_need(
                    needs,
                    service=requirement.get("service") or requirement.get("name"),
                    display_name=requirement.get("display_name") or requirement.get("name"),
                    kind=requirement.get("kind") or "api_key",
                    provider=requirement.get("provider"),
                    purpose=requirement.get("purpose") or f"Required by capability pack {pack_id}.",
                    required_at=requirement.get("required_at") or "runtime",
                    required_fields=(
                        requirement.get("required_fields")
                        if isinstance(requirement.get("required_fields"), list)
                        else None
                    ),
                    optional=bool(requirement.get("optional", False)),
                    required_by=required_by,
                    preferred_setup_lane=requirement.get("preferred_setup_lane"),
                    allowed_setup_lanes=requirement.get("allowed_setup_lanes"),
                    setup_lanes=requirement.get("setup_lanes"),
                    managed_default=requirement.get("managed_default"),
                )
                continue
            _append_need(
                needs,
                service=requirement,
                purpose=f"Required by capability pack {pack_id}.",
                required_at="runtime",
                required_by={"source": "capability_pack", "capability_pack_id": pack_id},
            )

    for task in _iter_dicts(app_build_plan.get("build_tasks")):
        task_id = task.get("task_id")
        for need in _iter_dicts(task.get("integration_needs")):
            required_by = {
                "source": "build_task",
                "task_id": task_id,
            }
            required_by.update(_dict_or_empty(need.get("required_by")))
            _append_need(
                needs,
                service=need.get("service") or need.get("name"),
                display_name=need.get("display_name") or need.get("name"),
                kind=need.get("kind") or "api_key",
                provider=need.get("provider"),
                purpose=need.get("purpose") or need.get("reason"),
                required_at=need.get("required_at") or "runtime",
                required_fields=need.get("required_fields") if isinstance(need.get("required_fields"), list) else None,
                optional=bool(need.get("optional", False)),
                required_by=required_by,
                preferred_setup_lane=need.get("preferred_setup_lane"),
                allowed_setup_lanes=need.get("allowed_setup_lanes"),
                setup_lanes=need.get("setup_lanes"),
                managed_default=need.get("managed_default"),
            )
    return needs


def _extract_needs_from_nested(value: Any, *, source: str) -> list[dict[str, Any]]:
    needs: list[dict[str, Any]] = []

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            raw_needs = node.get("integration_needs") or node.get("connector_needs")
            for need in _iter_dicts(raw_needs):
                required_by = {
                    "source": source,
                    "path": path,
                }
                required_by.update(_dict_or_empty(need.get("required_by")))
                _append_need(
                    needs,
                    service=need.get("service") or need.get("name"),
                    display_name=need.get("display_name") or need.get("name"),
                    kind=need.get("kind") or "api_key",
                    provider=need.get("provider"),
                    purpose=need.get("purpose") or need.get("reason"),
                    required_at=need.get("required_at") or "runtime",
                    required_fields=need.get("required_fields") if isinstance(need.get("required_fields"), list) else None,
                    optional=bool(need.get("optional", False)),
                    required_by=required_by,
                    preferred_setup_lane=need.get("preferred_setup_lane"),
                    allowed_setup_lanes=need.get("allowed_setup_lanes"),
                    setup_lanes=need.get("setup_lanes"),
                    managed_default=need.get("managed_default"),
                )
            for key, child in node.items():
                if key in {"integration_needs", "connector_needs"}:
                    continue
                visit(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for idx, child in enumerate(node):
                visit(child, f"{path}[{idx}]")

    visit(value, "")
    return needs


def dedupe_integration_needs(needs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_service: dict[str, dict[str, Any]] = {}
    for need in needs:
        if not isinstance(need, dict):
            continue
        service = normalize_service(need.get("service") or need.get("name"))
        if not service:
            continue
        existing = by_service.get(service)
        if existing is None:
            lanes = normalize_setup_lanes(need)
            safe_managed_default = _safe_managed_default(need.get("managed_default"))
            normalized_need = {
                **need,
                "service": service,
                "integration_id": need.get("integration_id") or service,
                "provider": need.get("provider") or service,
                "display_name": need.get("display_name") or display_service(service),
                "required_fields": _normalize_required_fields(need),
                "preferred_setup_lane": lanes["preferred_setup_lane"],
                "allowed_setup_lanes": lanes["allowed_setup_lanes"],
                "required_by": [need.get("required_by")] if isinstance(need.get("required_by"), dict) else [],
                "optional": bool(need.get("optional", False)),
            }
            if safe_managed_default:
                normalized_need["managed_default"] = safe_managed_default
            else:
                normalized_need.pop("managed_default", None)
            by_service[service] = normalized_need
            continue
        existing["optional"] = bool(existing.get("optional")) and bool(need.get("optional", False))
        if not existing.get("purpose") and need.get("purpose"):
            existing["purpose"] = need.get("purpose")
        next_required_fields = _normalize_required_fields(need) if need.get("required_fields") else []
        if next_required_fields:
            existing_fields = _dict_list(existing.get("required_fields"))
            existing_names = {str(field.get("name") or "") for field in existing_fields if isinstance(field, dict)}
            next_names = {str(field.get("name") or "") for field in next_required_fields if isinstance(field, dict)}
            if not existing_fields or next_names - existing_names or len(next_required_fields) > len(existing_fields):
                existing["required_fields"] = next_required_fields
        if need.get("provider") and existing.get("provider") in {None, "", service}:
            existing["provider"] = need.get("provider")
        if need.get("display_name") and existing.get("display_name") in {None, "", display_service(service)}:
            existing["display_name"] = need.get("display_name")
        existing_lanes = _setup_lane_ids(existing.get("allowed_setup_lanes"))
        next_lanes = _setup_lane_ids(need.get("allowed_setup_lanes")) or _setup_lane_ids(need.get("setup_lanes"))
        safe_managed_default = _safe_managed_default(need.get("managed_default"))
        if safe_managed_default:
            existing["managed_default"] = safe_managed_default
            if "managed" not in next_lanes:
                next_lanes.insert(0, "managed")
        for lane in next_lanes:
            if lane not in existing_lanes:
                existing_lanes.append(lane)
        if existing_lanes:
            existing["allowed_setup_lanes"] = existing_lanes
        preferred = _normalize_setup_lane(need.get("preferred_setup_lane"))
        if preferred and preferred in existing_lanes:
            existing["preferred_setup_lane"] = preferred
        else:
            lanes = normalize_setup_lanes(existing)
            existing["preferred_setup_lane"] = lanes["preferred_setup_lane"]
            existing["allowed_setup_lanes"] = lanes["allowed_setup_lanes"]
        if isinstance(need.get("required_by"), dict):
            existing.setdefault("required_by", []).append(need["required_by"])
        current_required_at = str(existing.get("required_at") or "runtime")
        next_required_at = str(need.get("required_at") or "runtime")
        priority = {"build_time": 0, "validation_time": 1, "runtime": 2, "deployment": 3}
        if priority.get(next_required_at, 9) < priority.get(current_required_at, 9):
            existing["required_at"] = next_required_at
    return sorted(by_service.values(), key=lambda item: (str(item.get("display_name", "")).lower(), item["service"]))


def collect_integration_needs(context_variables: Any = None) -> list[dict[str, Any]]:
    """Collect declared and discovered integration needs from workflow context."""

    collected: list[dict[str, Any]] = []
    collected.extend(_extract_needs_from_plan(_context_get(context_variables, "app_build_plan")))
    collected.extend(_extract_needs_from_nested(_context_get(context_variables, "current_build_task"), source="current_build_task"))
    collected.extend(_extract_needs_from_nested(_context_get(context_variables, "app_task_batch_results"), source="app_task_batch_results"))
    collected.extend(_extract_needs_from_nested(_context_get(context_variables, "integration_needs"), source="context.integration_needs"))
    return dedupe_integration_needs(collected)


async def record_integration_need(
    *,
    service: str,
    display_name: str | None = None,
    kind: str = "api_key",
    provider: str | None = None,
    purpose: str | None = None,
    required_at: str = "runtime",
    required_fields: list[dict[str, Any]] | None = None,
    required_by: dict[str, Any] | None = None,
    optional: bool = False,
    preferred_setup_lane: Any = None,
    allowed_setup_lanes: Any = None,
    setup_lanes: Any = None,
    managed_default: Any = None,
    context_variables: Any = None,
) -> dict[str, Any]:
    """Record a discovered integration need in context for later readiness checks."""

    existing = _context_get(context_variables, "integration_needs", [])
    needs = list(existing) if isinstance(existing, list) else []
    _append_need(
        needs,
        service=service,
        display_name=display_name,
        kind=kind,
        provider=provider,
        purpose=purpose,
        required_at=required_at,
        required_fields=required_fields,
        required_by=required_by,
        optional=optional,
        preferred_setup_lane=preferred_setup_lane,
        allowed_setup_lanes=allowed_setup_lanes,
        setup_lanes=setup_lanes,
        managed_default=managed_default,
    )
    deduped = dedupe_integration_needs(needs)
    _context_set(context_variables, "integration_needs", deduped)
    return {"status": "recorded", "integration_needs": deduped}


def build_integration_request_payload(
    *,
    services: list[dict[str, Any]],
    agent_message_id: str,
    context_variables: Any = None,
) -> list[dict[str, Any]]:
    """Build frontend-safe integration.required payload items.

    Secret fields are described by name/type only. Secret values are never
    included in this payload and should only travel back through the correlated
    tool_call_response.
    """

    workflow_name = _context_get(context_variables, "workflow_name", "unknown")
    run_id = _context_get(context_variables, "run_id") or _context_get(context_variables, "chat_id")
    step_id = _context_get(context_variables, "current_build_task_id") or _context_get(context_variables, "step_id")
    requests: list[dict[str, Any]] = []
    for idx, raw in enumerate(services):
        service = normalize_service(raw.get("service") or raw.get("name"))
        if not service:
            continue
        fields = _normalize_required_fields(raw)
        secret_fields, non_secret_fields = _split_required_fields(fields)
        setup_lanes = normalize_setup_lanes(raw)
        managed_default = _safe_managed_default(raw.get("managed_default"))
        requests.append(
            {
                "event_type": "integration.required",
                "integration_id": str(raw.get("integration_id") or service),
                "provider": str(raw.get("provider") or service),
                "display_name": str(raw.get("display_name") or raw.get("name") or display_service(service)),
                "purpose": str(raw.get("purpose") or raw.get("description") or "Required by generated app integration."),
                "required_fields": fields,
                "secret_fields": secret_fields,
                "non_secret_fields": non_secret_fields,
                "preferred_setup_lane": setup_lanes["preferred_setup_lane"],
                "allowed_setup_lanes": setup_lanes["allowed_setup_lanes"],
                **({"managed_default": managed_default} if managed_default else {}),
                "permissions_required": list(raw.get("permissions_required") or ["integrations.manage"]),
                "resume": {
                    "workflow_name": workflow_name,
                    "run_id": run_id,
                    "step_id": step_id,
                    "tool_call_id": f"{agent_message_id}:{service}:{idx}",
                },
            }
        )
    return requests


def _extract_submitted_values(entry: dict[str, Any]) -> dict[str, Any]:
    for key in ("fields", "values", "config", "field_values"):
        value = entry.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _extract_secret_value(entry: dict[str, Any], fields: list[dict[str, Any]]) -> str:
    raw_key = entry.get("apiKey") or entry.get("api_key")
    if isinstance(raw_key, str) and raw_key.strip():
        return raw_key.strip()
    submitted = _extract_submitted_values(entry)
    for field in fields:
        if str(field.get("type") or "").lower() not in SECRET_FIELD_TYPES:
            continue
        value = submitted.get(field.get("name"))  # type: ignore[arg-type]
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_frontend_safe_config(entry: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    submitted = _extract_submitted_values(entry)
    safe_config: dict[str, Any] = {}
    for field in fields:
        name = str(field.get("name") or "").strip()
        if not name or name not in submitted:
            continue
        field_type = str(field.get("type") or "").lower()
        if field_type in SECRET_FIELD_TYPES or field.get("frontend_safe") is False:
            continue
        value = submitted.get(name)
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe_config[name] = value
    return safe_config


async def request_connector_bundle(
    *,
    services: list[dict[str, Any]],
    agent_message: str | None = None,
    description: str | None = None,
    context_variables: Any = None,
) -> dict[str, Any]:
    if not services:
        return {"status": "no_services", "services": [], "collected": [], "missing_required": []}

    workflow_name = _context_get(context_variables, "workflow_name", "AppGenerator")
    chat_id = _context_get(context_variables, "chat_id")
    app_id = _context_get(context_variables, "app_id")
    user_id = _context_get(context_variables, "user_id")
    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id, app_id=app_id)

    agent_message_id = f"connector_bundle_{uuid.uuid4().hex[:10]}"
    normalized_services: list[dict[str, Any]] = []
    for idx, raw in enumerate(services):
        service = normalize_service(raw.get("service") or raw.get("name"))
        if not service:
            continue
        display_name = str(raw.get("display_name") or raw.get("name") or display_service(service))
        required_fields = _normalize_required_fields(raw)
        secret_fields, non_secret_fields = _split_required_fields(required_fields)
        secret_label = secret_fields[0].get("label") if secret_fields else "Secret"
        setup_lanes = normalize_setup_lanes(raw)
        managed_default = _safe_managed_default(raw.get("managed_default"))
        normalized_services.append(
            {
                "service": service,
                "integration_id": str(raw.get("integration_id") or service),
                "provider": str(raw.get("provider") or service),
                "display_name": display_name,
                "required": not bool(raw.get("optional", False)),
                "mask_input": True,
                "required_fields": required_fields,
                "secret_fields": secret_fields,
                "non_secret_fields": non_secret_fields,
                "preferred_setup_lane": setup_lanes["preferred_setup_lane"],
                "allowed_setup_lanes": setup_lanes["allowed_setup_lanes"],
                **({"managed_default": managed_default} if managed_default else {}),
                "placeholder": raw.get("placeholder") or f"Enter {display_name} {secret_label}...",
                "description": raw.get("purpose") or raw.get("description") or f"Setup values for {display_name}",
                "label": raw.get("label") or f"{display_name} {secret_label}",
                "agent_message_id": f"{agent_message_id}:{service}:{idx}",
            }
        )

    integration_requests = build_integration_request_payload(
        services=normalized_services,
        agent_message_id=agent_message_id,
        context_variables=context_variables,
    )
    integrations_url = f"/apps/{app_id}/integrations" if app_id else "/apps/{appId}/integrations"
    payload = {
        "event_type": "integration.required",
        "agent_message_id": agent_message_id,
        "agent_message": agent_message or "Configure the missing integrations so the build can continue.",
        "description": description or "Setup values are saved to the app connector store when vault storage is available.",
        "integration_requests": integration_requests,
        "permissions_required": ["integrations.manage"],
        "integrations_url": integrations_url,
        "services": [
            {
                "service": svc["service"],
                "integration_id": svc["integration_id"],
                "provider": svc["provider"],
                "service_display_name": svc["display_name"],
                "required": svc["required"],
                "maskInput": svc["mask_input"],
                "placeholder": svc["placeholder"],
                "description": svc["description"],
                "label": svc["label"],
                "required_fields": svc["required_fields"],
                "secret_fields": svc["secret_fields"],
                "non_secret_fields": svc["non_secret_fields"],
                "preferred_setup_lane": svc["preferred_setup_lane"],
                "allowed_setup_lanes": svc["allowed_setup_lanes"],
                **({"managed_default": svc["managed_default"]} if isinstance(svc.get("managed_default"), dict) else {}),
                "agent_message_id": svc["agent_message_id"],
            }
            for svc in normalized_services
        ],
    }

    try:
        response = await use_ui_tool(
            "AgentAPIKeysBundleInput",
            payload,
            chat_id=chat_id,
            workflow_name=str(workflow_name),
        )
    except UIToolError as exc:
        return {"status": "error", "message": f"UI interaction failed: {exc}", "services": []}
    except Exception as exc:  # pragma: no cover - defensive guard
        wf_logger.error("Connector bundle UI interaction failed: %s", exc)
        return {"status": "error", "message": "UI interaction failure", "services": []}

    status = (response or {}).get("status") or (response or {}).get("data", {}).get("status")
    data_block = response.get("data") if isinstance(response, dict) else {}
    submitted = data_block.get("services") if isinstance(data_block, dict) else None
    submitted_lookup: dict[str, dict[str, Any]] = {}
    if isinstance(submitted, list):
        for item in submitted:
            service = normalize_service(item.get("service"))
            if service:
                submitted_lookup[service] = item

    sanitized: list[dict[str, Any]] = []
    collected: list[str] = []
    missing_required: list[str] = []
    if status in {"cancelled", "canceled"}:
        missing_required = [svc["service"] for svc in normalized_services if svc["required"]]
        return {
            "status": "cancelled",
            "services": normalized_services,
            "collected": [],
            "missing_required": missing_required,
        }

    for svc in normalized_services:
        entry = submitted_lookup.get(svc["service"], {})
        trimmed_key = _extract_secret_value(entry, svc["required_fields"])
        public_config = _extract_frontend_safe_config(entry, svc["required_fields"])
        selected_setup_lane = _normalize_setup_lane(entry.get("selected_setup_lane"))
        if selected_setup_lane:
            public_config["selected_setup_lane"] = selected_setup_lane
        has_key = bool(trimmed_key)
        needs_secret = any(str(field.get("type") or "").lower() in SECRET_FIELD_TYPES for field in svc["required_fields"])
        configured = has_key or (not needs_secret and bool(public_config))
        key_length = len(trimmed_key) if has_key else 0
        metadata_saved = False
        connector_stored = False
        metadata_error = None
        connector_record = None

        if configured and app_id:
            try:
                metadata_result = await save_connector_draft(
                    scope=ConnectorStore.SCOPE_APP,
                    scope_id=str(app_id),
                    service=svc["service"],
                    provider=svc.get("provider"),
                    integration_id=svc.get("integration_id"),
                    display_name=svc["display_name"],
                    key_length=key_length,
                    workflow_name=workflow_name,
                    chat_id=chat_id,
                    agent_message_id=svc["agent_message_id"],
                    ui_event_id=response.get("ui_event_id"),
                    public_config=public_config,
                    required_fields=svc["required_fields"],
                    user_id=str(user_id) if user_id else None,
                    status_reason="Credential requested inline by a generator workflow.",
                    logger=wf_logger,
                )
                metadata_saved = bool(metadata_result.get("saved"))
                connector_record = metadata_result.get("connector")
            except Exception as exc:
                metadata_error = str(exc)
            if has_key:
                try:
                    stored = await save_connector(
                        scope=ConnectorStore.SCOPE_APP,
                        scope_id=str(app_id),
                        user_id=str(user_id) if user_id else "unknown",
                        service=svc["service"],
                        secret_value=trimmed_key,
                        display_name=svc["display_name"],
                        ttl_days=30,
                        public_config=public_config,
                        required_fields=svc["required_fields"],
                        provider=svc.get("provider"),
                        integration_id=svc.get("integration_id"),
                        logger=wf_logger,
                    )
                    connector_stored = bool(stored.get("success"))
                    connector_record = stored.get("connector") or connector_record
                except Exception as exc:
                    wf_logger.warning("Connector storage failed for %s: %s", svc["service"], exc)

        if configured:
            collected.append(svc["service"])
        elif svc["required"]:
            missing_required.append(svc["service"])

        sanitized.append(
            {
                "service": svc["service"],
                "display_name": svc["display_name"],
                "required": svc["required"],
                "status": "success" if configured else ("missing" if svc["required"] else "skipped"),
                "has_key": has_key,
                "configured": configured,
                "selected_setup_lane": selected_setup_lane or svc.get("preferred_setup_lane"),
                "key_length": key_length,
                "public_config": public_config,
                "metadata_saved": metadata_saved,
                "connector_stored": connector_stored,
                **({"metadata_error": metadata_error} if metadata_error else {}),
                **({"connector": connector_record} if connector_record else {}),
            }
        )
        if isinstance(entry, dict) and "apiKey" in entry:
            entry["apiKey"] = None

    overall_status = "success" if not missing_required else ("partial" if collected else "no_keys")
    return {
        "status": overall_status,
        "services": sanitized,
        "collected": collected,
        "missing_required": missing_required,
        "submitted_at": data_block.get("submissionTime") if isinstance(data_block, dict) else None,
        "ui_event_id": response.get("ui_event_id"),
    }


def _request_result_ready_services(request_result: dict[str, Any] | None) -> set[str]:
    ready_services: set[str] = set()
    for entry in (request_result or {}).get("services") or []:
        if not isinstance(entry, dict):
            continue
        service = normalize_service(entry.get("service"))
        if not service or entry.get("status") != "success":
            continue
        stored_secret = bool(entry.get("connector_stored"))
        stored_metadata_only = bool(entry.get("metadata_saved")) and not bool(entry.get("has_key"))
        if stored_secret or stored_metadata_only:
            ready_services.add(service)
    return ready_services


def _merge_inventory_ready_services(
    inventory: dict[str, Any],
    ready_services: set[str],
) -> dict[str, Any]:
    if not ready_services:
        return inventory

    merged = dict(inventory or {})
    required = [
        normalize_service(service)
        for service in (merged.get("required_services") or [])
        if normalize_service(service)
    ]
    required_set = set(required)
    ready = {
        normalize_service(service)
        for service in (merged.get("ready_services") or [])
        if normalize_service(service)
    }
    ready.update(ready_services)

    known = {
        normalize_service(service)
        for service in (merged.get("known_services") or [])
        if normalize_service(service)
    }
    known.update(ready)

    unresolved = required_set - ready
    merged["ready_services"] = sorted(ready)
    merged["known_services"] = sorted(known)
    merged["missing_required_services"] = sorted(unresolved)
    merged["known_but_unready_required_services"] = sorted(unresolved & known)
    merged["entirely_missing_required_services"] = sorted(unresolved - known)

    status_buckets = dict(merged.get("status_buckets") or {})
    active = {
        normalize_service(service)
        for service in (status_buckets.get("active") or [])
        if normalize_service(service)
    }
    active.update(ready)
    status_buckets["active"] = sorted(active)

    metadata_only = {
        normalize_service(service)
        for service in (status_buckets.get("metadata_only") or [])
        if normalize_service(service)
    }
    status_buckets["metadata_only"] = sorted(metadata_only - ready)
    merged["status_buckets"] = status_buckets
    return merged


async def collect_missing_connector_needs(
    *,
    context_variables: Any = None,
    required_at: list[str] | None = None,
    prompt: bool = True,
) -> dict[str, Any]:
    """Aggregate connector needs, prompt for unresolved required services, persist status."""

    needs = collect_integration_needs(context_variables)
    required_at_set = {str(item) for item in (required_at or ["build_time", "validation_time", "runtime"])}
    blocking_needs = [
        need
        for need in needs
        if not bool(need.get("optional", False)) and str(need.get("required_at") or "runtime") in required_at_set
    ]
    app_id = _context_get(context_variables, "app_id")
    required_services = [need["service"] for need in blocking_needs]
    inventory = (
        await get_connector_inventory(scope=ConnectorStore.SCOPE_APP, scope_id=str(app_id), required_services=required_services)
        if app_id
        else {
            "required_services": required_services,
            "ready_services": [],
            "missing_required_services": required_services,
            "connectors": [],
        }
    )
    ready = set(inventory.get("ready_services") or [])
    missing = [need for need in blocking_needs if need["service"] not in ready]

    request_result = None
    if prompt and missing:
        request_result = await request_connector_bundle(
            services=missing,
            agent_message="I need a few integration credentials to finish validating this app.",
            description="These were discovered while building the app. Saved credentials will appear in the app-scoped integrations surface.",
            context_variables=context_variables,
        )
        inventory = (
            await get_connector_inventory(scope=ConnectorStore.SCOPE_APP, scope_id=str(app_id), required_services=required_services)
            if app_id
            else inventory
        )
        inventory = _merge_inventory_ready_services(inventory, _request_result_ready_services(request_result))

    unresolved = list(inventory.get("missing_required_services") or [])
    if not unresolved:
        status = "ready"
    elif prompt and request_result is not None:
        status = "blocked"
    else:
        status = "needs_configuration"
    result = {
        "status": status,
        "checked_at": datetime.now(UTC).isoformat(),
        "integration_needs": needs,
        "blocking_needs": blocking_needs,
        "connector_inventory": inventory,
        "request_result": request_result,
        "unresolved_required_services": unresolved,
    }
    _context_set(context_variables, "integration_needs", needs)
    _context_set(context_variables, "integration_readiness_status", status)
    _context_set(context_variables, "integration_readiness_result", result)
    _context_set(context_variables, "required_connector_services", inventory.get("required_services", []))
    _context_set(context_variables, "ready_connector_services", inventory.get("ready_services", []))
    _context_set(context_variables, "missing_connector_services", unresolved)
    _context_set(context_variables, "connector_inventory", inventory)
    return result


__all__ = [
    "build_integration_request_payload",
    "collect_integration_needs",
    "collect_missing_connector_needs",
    "dedupe_integration_needs",
    "display_service",
    "normalize_service",
    "normalize_setup_lanes",
    "record_integration_need",
    "request_connector_bundle",
]
