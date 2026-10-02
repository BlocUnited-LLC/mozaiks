from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan


def test_structured_outputs_use_one_closed_auth_strategy_vocabulary() -> None:
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
    models = yaml.safe_load(path.read_text(encoding="utf-8"))["models"]
    assert models["AppAuthStrategy"]["values"] == [
        "public", "basic-login", "role-based", "third-party",
    ]
    for model in ("AppBuildPlan", "AppManifest"):
        assert models[model]["fields"]["auth_strategy"]["variants"] == [
            "AppAuthStrategy", "null",
        ]


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


@pytest.mark.parametrize("strategy", ["public", "basic-login", "role-based", "third-party", None])
def test_app_build_plan_accepts_canonical_auth_strategy(strategy: str | None) -> None:
    ctx = _Ctx()
    roles = ["operator"] if strategy == "role-based" else []
    app_build_plan(
        AppBuildPlan=_minimal_plan(auth_strategy=strategy, roles=roles),
        context_variables=ctx,
    )
    assert ctx.data["app_plan_ready"] is True
    assert ctx.data["app_build_plan"]["auth_strategy"] == strategy


@pytest.mark.parametrize("strategy", ["none", "oidc", "required", "passport-session", "", " public "])
def test_app_build_plan_rejects_unknown_auth_strategy(strategy: str) -> None:
    ctx = _Ctx()
    with pytest.raises(ValueError, match="AppBuildPlan.auth_strategy must be"):
        app_build_plan(AppBuildPlan=_minimal_plan(auth_strategy=strategy), context_variables=ctx)
    assert ctx.data["app_plan_ready"] is False


@pytest.mark.parametrize("strategy", ["public", None])
def test_app_build_plan_rejects_roles_without_auth(strategy: str | None) -> None:
    ctx = _Ctx()
    with pytest.raises(ValueError, match="cannot be public or null when roles are declared"):
        app_build_plan(
            AppBuildPlan=_minimal_plan(auth_strategy=strategy, roles=["operator"]),
            context_variables=ctx,
        )
    assert ctx.data["app_plan_ready"] is False


@pytest.mark.parametrize("strategy", ["basic-login", "role-based", "third-party"])
def test_app_build_plan_accepts_roles_with_auth(strategy: str) -> None:
    ctx = _Ctx()
    app_build_plan(
        AppBuildPlan=_minimal_plan(auth_strategy=strategy, roles=["operator"]),
        context_variables=ctx,
    )
    assert ctx.data["app_plan_ready"] is True
    assert ctx.data["app_build_plan"]["roles"] == ["operator"]


@pytest.mark.parametrize("roles", [None, []])
def test_app_build_plan_rejects_role_based_without_roles(roles: list[str] | None) -> None:
    ctx = _Ctx()
    plan = _minimal_plan(auth_strategy="role-based", roles=roles)
    if roles is None:
        plan.pop("roles")
    with pytest.raises(ValueError, match="requires at least one role for role-based auth"):
        app_build_plan(AppBuildPlan=plan, context_variables=ctx)
    assert ctx.data["app_plan_ready"] is False

