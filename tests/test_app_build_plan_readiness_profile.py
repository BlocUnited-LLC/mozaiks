from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan


class _Ctx:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = dict(data or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


def _minimal_plan(**overrides: Any) -> dict[str, Any]:
    plan = {
        "agent_message": "Build a host-operator platform that hosts apps, manages launch gates, and records evidence.",
        "app_kind": "saas",
        "pages": [
            {
                "name": "Dashboard",
                "route": "/dashboard",
                "purpose": "Operator dashboard",
            }
        ],
        "entities": [],
        "roles": [],
        "auth_strategy": "basic-login",
        "service_scope": ["hosting", "billing", "domains"],
        "frontend_scope": [],
        "theme_preferences": None,
        "brand_intent": None,
        "capability_packs": [],
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": [],
        "generation_order": ["app-schema-bundle"],
    }
    plan.update(overrides)
    return plan


def test_app_build_plan_infers_host_operator_readiness_profile() -> None:
    ctx = _Ctx()

    app_build_plan(AppBuildPlan=_minimal_plan(), context_variables=ctx)

    assert ctx.data["app_build_plan"]["readiness_profile"] == "host_operator_platform"


def test_app_build_plan_preserves_explicit_readiness_profile() -> None:
    ctx = _Ctx()

    app_build_plan(
        AppBuildPlan=_minimal_plan(readiness_profile="saas_app"),
        context_variables=ctx,
    )

    assert ctx.data["app_build_plan"]["readiness_profile"] == "saas_app"

