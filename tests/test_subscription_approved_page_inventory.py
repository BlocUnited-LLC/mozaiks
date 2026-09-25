"""Subscription requirements enrich approved pages without adding new routes."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.SubscriptionContractDesigner.tools import (
    save_subscription_contract as module,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from tests.factory_context import factory_context

WORKFLOW = Path(__file__).resolve().parents[1] / "factory_app/workflows/SubscriptionContractDesigner"


def _context(route: str, *, phase: str = "genesis", **overrides: object) -> StructuredOutputOverlay:
    data = factory_context({
        "app_id": "pricing-test",
        "chat_id": "pricing-chat",
        "experience_spec": {"pages": [
            {"name": "Dashboard", "route": "/dashboard"},
            {"name": "Pricing", "route": "/pricing"},
        ]},
        **overrides,
    })
    data["run_build_binding"]["phase"] = phase
    output = {
        "contract_required": True,
        "app_name": "Task Tracker",
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Task Tracker plans",
            "default_plan_id": "free",
            "plans": [{"plan_id": "free", "label": "Free", "capabilities": []}],
        },
        "page_surface_requirements": [{
            "page_id": route.removeprefix("/"),
            "route": route,
            "purpose": "Show subscription plans and usage.",
            "required_runtime_endpoints": ["/api/modules/billing_portal/list_plans"],
        }],
    }
    return StructuredOutputOverlay(ContextVariablesBridge(data), output)


@pytest.fixture
def side_effects(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    review = AsyncMock(return_value={"approved": True})
    persist = AsyncMock(return_value=SimpleNamespace(id="subscription-version"))
    monkeypatch.setattr(module, "use_ui_tool", review)
    monkeypatch.setattr(module, "persist_summary_artifact", persist)
    return review, persist


@pytest.mark.asyncio
async def test_approved_pricing_requirement_is_reviewed_and_persisted(side_effects) -> None:
    context = _context("/pricing")
    assert isinstance(context.get("experience_spec"), MappingProxyType)
    assert isinstance(context.get("experience_spec")["pages"][0], MappingProxyType)

    result = await module.save_subscription_contract(context)

    assert result["success"] is True
    assert result["review_status"] == "confirmed"
    review, persist = side_effects
    review.assert_awaited_once()
    persist.assert_awaited_once()
    saved = persist.await_args.kwargs["summary_payload"]
    assert saved["page_surface_requirements"][0]["route"] == "/pricing"
    assert context.get("subscription_contract")["page_surface_requirements"][0]["route"] == "/pricing"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/billing", "/usage"])
async def test_unapproved_subscription_page_returns_for_revision_before_review(route, side_effects) -> None:
    context = _context(route, subscription_contract={"stale": True}, subscription_contract_files=["stale"])

    result = await module.save_subscription_contract(context)

    assert result["success"] is False
    assert result["review_status"] == "changes_requested"
    assert route in result["requested_changes"]
    assert "/pricing" in result["requested_changes"]
    assert "experience_spec.pages" in result["requested_changes"]
    assert context.get("subscription_contract") is None
    assert not context.get("subscription_contract_files")
    assert context.get("subscription_contract_review_response")["source"] == "approved_page_inventory"
    review, persist = side_effects
    review.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"brownfield_build_path": "full_migration"},
    {"experience_spec": None},
    {"experience_spec": {"pages": []}},
])
async def test_page_guard_requires_a_greenfield_approved_inventory(overrides, side_effects) -> None:
    result = await module.save_subscription_contract(_context("/billing", **overrides))

    assert result["success"] is True
    review, persist = side_effects
    review.assert_awaited_once()
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_refinement_is_not_limited_to_the_genesis_page_inventory(side_effects) -> None:
    result = await module.save_subscription_contract(_context("/billing", phase="refinement"))

    assert result["success"] is True
    review, persist = side_effects
    review.assert_awaited_once()
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["revision_mode"] is True


@pytest.mark.asyncio
async def test_malformed_page_metadata_returns_validation_feedback(side_effects) -> None:
    context = _context("/pricing", experience_spec={"pages": [{"name": "Missing route"}]})

    result = await module.save_subscription_contract(context)

    assert result["success"] is False
    assert result["review_status"] == "changes_requested"
    assert "invalid_subscription_contract" in result["error"]
    assert context.get("subscription_contract_review_response")["source"] == "contract_validation"
    review, persist = side_effects
    review.assert_not_awaited()
    persist.assert_not_awaited()


def test_subscription_prompt_uses_the_existing_public_catalog_action() -> None:
    prompt = (WORKFLOW / "agents.yaml").read_text(encoding="utf-8")
    assert "get_plans" not in prompt
    assert "billing_portal" in prompt and "public_readonly list_plans" in prompt
    assert "experience_spec.pages" in prompt


def test_subscription_page_schema_declares_an_approved_route() -> None:
    schema = yaml.safe_load((WORKFLOW / "structured_outputs.yaml").read_text(encoding="utf-8"))
    description = schema["models"]["PageSurfaceRequirement"]["fields"]["route"]["description"]
    assert "experience_spec.pages" in description
    assert "approved" in description.lower()
