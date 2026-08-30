"""Materialize canonical app config contracts from AppGenerator context."""

from __future__ import annotations

import json
from typing import Any

import yaml

from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.workflow.generator_support.connector_request import (
    collect_integration_needs,
)
from mozaiksai.core.workflow.generator_support.connector_setup import (
    normalize_setup_lanes,
    safe_managed_default,
)


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
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            result[str(key)] = _safe_dict(item)
        elif isinstance(item, list):
            result[str(key)] = [
                _safe_dict(entry) if isinstance(entry, dict) else _safe_scalar(entry)
                for entry in item
            ]
        else:
            result[str(key)] = _safe_scalar(item)
    return result


def _subscriptions_config_file(context_variables: Any) -> dict[str, Any] | None:
    """Return subscription_config_file from context, trying both the live contract and artifact."""
    for key in ("subscription_contract", "subscription_contract_artifact"):
        raw = _context_get(context_variables, key)
        if isinstance(raw, dict):
            cfg = raw.get("subscription_config_file")
            if isinstance(cfg, dict):
                return cfg
    return None


def _materialize_subscriptions_yaml(*, context_variables: Any) -> str | None:
    """Emit config/subscriptions.yaml from SubscriptionContractDesigner output.

    Returns None when the context contains no subscriptions contract, which
    signals that config/subscriptions.yaml should not be written (non-SaaS app).
    The runtime wires NoOpEntitlementAdapter when the file is absent, so all
    entitlement checks pass by default — self-hosted and non-SaaS apps are
    unaffected.
    """
    cfg = _subscriptions_config_file(context_variables)
    if cfg is None:
        return None

    # Validate and serialize through the canonical runtime contract rather than
    # reconstructing selected fields. This preserves every supported v1/v2
    # field and fails closed when a future field or schema version is not yet
    # understood instead of silently dropping it from the generated bundle.
    doc = SubscriptionsConfig.model_validate(cfg).model_dump(
        mode="json",
        exclude_unset=True,
    )

    return str(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True))


def _materialize_integrations_yaml(*, app_id: str, context_variables: Any) -> str:
    requirements: list[dict[str, Any]] = []
    for need in collect_integration_needs(context_variables):
        lanes = normalize_setup_lanes(need)
        requirement: dict[str, Any] = {
            "integration_id": str(need.get("integration_id") or need.get("service")),
            "service": str(need.get("service") or ""),
            "provider": str(need.get("provider") or need.get("service") or ""),
            "display_name": str(need.get("display_name") or need.get("service") or ""),
            "kind": str(need.get("kind") or "api_key"),
            "purpose": str(need.get("purpose") or "Required by generated app integration."),
            "required_at": str(need.get("required_at") or "runtime"),
            "optional": bool(need.get("optional", False)),
            "preferred_setup_lane": lanes["preferred_setup_lane"],
            "allowed_setup_lanes": lanes["allowed_setup_lanes"],
            "required_fields": need.get("required_fields") or [],
            "required_by": need.get("required_by") or [],
        }
        managed_default = need.get("managed_default")
        safe_default = safe_managed_default(managed_default)
        if safe_default:
            requirement["managed_default"] = _safe_dict(safe_default)
        requirements.append(requirement)

    payload = {
        "schema_version": "mozaiks.integrations.v1",
        "app_id": app_id,
        "requirements": requirements,
    }
    return str(yaml.safe_dump(payload, sort_keys=False, default_flow_style=False))


def _first_dict(items: Any) -> dict[str, Any]:
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                return item
    if isinstance(items, dict):
        return items
    return {}


def _materialize_targets_json(*, app_id: str, app_build_plan: Any, context_variables: Any) -> str:
    plan = app_build_plan if isinstance(app_build_plan, dict) else {}
    target = _first_dict(plan.get("deployment_targets"))
    deployment_profile = (
        target.get("deployment_profile")
        or plan.get("deployment_profile")
        or _context_get(context_variables, "deployment_profile")
        or "self_hosted_container"
    )

    runtime = _safe_dict(target.get("runtime"))
    if not runtime:
        runtime = {
            "kind": "web",
            "health_path": "/health",
            "container_port": 8000,
        }

    allowed_lanes = target.get("allowed_lanes")
    deployment: dict[str, Any] = {
        "profile": str(deployment_profile),
        "preferred_lane": str(target.get("preferred_lane") or "self_hosted"),
        "allowed_lanes": (
            [_safe_scalar(item) for item in allowed_lanes]
            if isinstance(allowed_lanes, list)
            else ["self_hosted"]
        ),
    }
    if target.get("target_id"):
        deployment["target_id"] = str(target["target_id"])
    artifact_outputs = target.get("artifact_outputs")
    if isinstance(artifact_outputs, list):
        deployment["artifact_outputs"] = [_safe_scalar(item) for item in artifact_outputs]

    environment = _safe_dict(target.get("environment"))
    if environment:
        deployment["environment"] = environment

    checks = target.get("checks")
    if isinstance(checks, list):
        deployment["checks"] = [_safe_dict(item) if isinstance(item, dict) else _safe_scalar(item) for item in checks]

    domains = _safe_dict(target.get("domains"))
    if not domains:
        domains = {
            "custom_domain_supported": False,
            "managed_dns_preferred": False,
        }

    payload = {
        "schema_version": "mozaiks.targets.v1",
        "app_id": app_id,
        "runtime": runtime,
        "deployment": deployment,
        "domains": domains,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def materialize_app_config_contracts(
    *,
    app_id: str,
    app_build_plan: Any,
    context_variables: Any,
) -> list[dict[str, str]]:
    """Return generated app config files owned by AppGenerator assembly.

    These files are declarative app-bundle facts. They intentionally contain no
    raw credentials, cloud account ids, provider execution state, or hosted-only
    policy defaults.
    """

    files = [
        {
            "filename": "config/integrations.yaml",
            "content": _materialize_integrations_yaml(
                app_id=app_id,
                context_variables=context_variables,
            ),
        },
        {
            "filename": "config/targets.json",
            "content": _materialize_targets_json(
                app_id=app_id,
                app_build_plan=app_build_plan,
                context_variables=context_variables,
            ),
        },
    ]

    subscriptions_content = _materialize_subscriptions_yaml(context_variables=context_variables)
    if subscriptions_content is not None:
        files.append({"filename": "config/subscriptions.yaml", "content": subscriptions_content})

    return files


__all__ = ["materialize_app_config_contracts"]
