"""DesignDocs must approve monetization pages before the planner freezes scope."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_coverage,
)
from factory_app.workflows.DesignDocs.tools import save_design_doc
from mozaiksai.core.session.build_context import discover_pack_descriptors
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome
from tests.test_app_plan_review import _plan
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]


def _bundle(*, pricing: bool, subscription: bool = False) -> dict:
    pages = [{
        "name": "Reports", "route": "/reports", "layout": "full-width",
        "intent": "Review reports", "sections": [{
            "id": "reports", "primitive": "DataTable", "intent": "Read reports",
        }],
    }]
    surfaces = [{
        "surface_id": "reports", "label": "Reports", "surface_kind": "module", "owner": "app",
        "primary_entities": ["Report"], "owned_pages": ["Reports"],
        "notes": None,
    }]
    if pricing:
        pages.append({
            "name": "Subscriptions", "route": "/pricing", "layout": "full-width",
            "intent": "Compare plans and manage subscription", "sections": [{
                "id": "plans", "primitive": "SurfaceCard",
                "intent": "Compare available plans and select a subscription",
            }],
        })
        surfaces.append({
            "surface_id": "pricing", "label": "Subscriptions", "surface_kind": "ui_only", "owner": "app",
            "primary_entities": [], "owned_pages": ["Subscriptions"], "notes": None,
        })
    if subscription:
        for name in ("Billing", "Usage"):
            pages.append({
                "name": name, "route": f"/{name.lower()}", "layout": "full-width",
                "intent": f"Manage {name.lower()}", "sections": [{
                    "id": name.lower(), "primitive": "SurfaceCard", "intent": f"Show {name.lower()} status",
                }],
            })
        surfaces[-1].update(
            surface_id="billing_portal", surface_kind="module",
            owned_pages=["Subscriptions", "Billing", "Usage"],
        )
    return {
        "agent_message": "Design the approved reports and subscription surfaces.",
        "frontend_markdown": "# Frontend\nApproved reports and subscription experience.",
        "backend_markdown": "# Backend\nReports module and managed subscription integration.",
        "database_markdown": "# Database\nReport persistence.",
        "experience_spec": {
            "navigation_model": "Top-level routes", "brand_direction": "Professional",
            "pages": pages,
        },
        "surface_map": {"surfaces": surfaces},
        "data_contract": {
            "version": "1", "app_id": None, "artifact_version_id": None,
            "surfaces": [{"surface_id": "reports", "surface_kind": "module", "collections": []}],
            "policies": {"default_scope_field": "app_id", "allow_destructive_migrations": False},
        },
    }


def _context(
    *, monetized: bool, brownfield: str | None = None, subscription: bool = False,
) -> ContextVariablesBridge:
    return ContextVariablesBridge({
        "run_build_binding": {
            "build_registry_id": "pricing_registry", "target_app_id": "reports-app",
            "build_id": "pricing_build", "phase": "genesis",
        },
        "chat_id": "pricing_chat", "user_id": "owner", "build_mode": "initial",
        "monetization_enabled": monetized, "brownfield_build_path": brownfield,
        "concept_blueprint": {
            "agentic_capabilities": [],
            "monetization_intent": {
                "monetized": monetized, "subscription_contract_likely": subscription,
                "money_flow_summary": "Recurring subscription" if subscription else "No subscription",
            },
        },
        "design_docs_save_outcome": "blocked", "design_docs_save_attempts": 0,
        "design_docs_save_feedback": "", "app_plan_attempts": 0,
        "app_plan_outcome": "blocked",
    })


def _save(context: ContextVariablesBridge, bundle: dict) -> dict:
    tools = yaml.safe_load(
        (ROOT / "factory_app/workflows/DesignDocs/tools.yaml").read_text(encoding="utf-8")
    )["tools"]
    entry = next(tool for tool in tools if tool["function"] == "save_design_docs_bundle")
    wrapped = wrap_tool_outcome(
        save_design_doc.save_design_docs_bundle, ToolOutcomeSpec.model_validate(entry["outcome"]),
    )
    return asyncio.run(wrapped(context_variables=StructuredOutputOverlay(context, bundle)))


@pytest.fixture
def persistence(monkeypatch):
    """Only replace storage; validation, context writes, and outcome routing stay real."""
    store = Mock()
    store.mark_design_doc_status = AsyncMock()
    store.upsert_design_doc = AsyncMock()
    store.save_data_contract = AsyncMock()
    store_factory = Mock(return_value=store)
    summary = AsyncMock()
    monkeypatch.setattr(save_design_doc, "AG2PersistenceManager", Mock())
    monkeypatch.setattr(save_design_doc, "BuilderArtifactStore", store_factory)
    monkeypatch.setattr(save_design_doc, "persist_summary_artifact", summary)
    return store, store_factory, summary


def test_design_prompt_requires_pricing_using_projected_monetization_signals():
    workflow = ROOT / "factory_app/workflows/DesignDocs"
    context = yaml.safe_load((workflow / "context_variables.yaml").read_text(encoding="utf-8"))
    agent = yaml.safe_load((workflow / "agents.yaml").read_text(encoding="utf-8"))["agents"][0]
    variables = context["agents"]["DesignDocsAgent"]["variables"]
    assert {"monetization_enabled", "concept_blueprint"} <= set(variables)
    assert context["definitions"]["monetization_enabled"]["source"]["default"] is False
    sections = {section["id"]: section["content"] for section in agent["prompt_sections"]}
    guidance = sections["monetization_surfaces"]
    assert "monetization_enabled: true" in guidance
    assert "Pricing at /pricing in experience_spec.pages" in guidance
    assert "concept_blueprint.monetization_intent" in guidance
    assert "monetization_enabled is false, do not add" in guidance
    assert "exactly one primary owner in surface_map" in guidance


def test_monetized_approved_inventory_persists_pricing_through_real_bridge(persistence):
    store, _, summary = persistence
    context = _context(monetized=True)
    bundle = _bundle(pricing=True)

    assert isinstance(context.get("concept_blueprint"), MappingProxyType)
    overlay = StructuredOutputOverlay(context, bundle)
    assert isinstance(overlay.get("concept_blueprint"), MappingProxyType)
    # Structured output is a detached transient payload; durable bridge reads freeze.
    assert isinstance(overlay.get("structured_output"), dict)

    result = _save(context, bundle)

    assert result["outcome"] == "saved", result
    assert result["page_count"] == 2
    assert isinstance(context.get("experience_spec"), MappingProxyType)
    assert detach(context.get("experience_spec"))["pages"] == bundle["experience_spec"]["pages"]
    assert ("Subscriptions", "/pricing") in {
        (page["name"], page["route"]) for page in context.get("experience_spec")["pages"]
    }
    ui_document = next(
        call.kwargs for call in store.upsert_design_doc.await_args_list
        if call.kwargs["kind"] == "ui_schema"
    )
    assert ui_document["extra_fields"]["experience_spec"] == detach(context.get("experience_spec"))
    assert summary.await_args.kwargs["summary_payload"]["experience_spec"] == detach(context.get("experience_spec"))


def test_missing_pricing_is_refused_before_storage_and_retry_saves_it(persistence):
    _, store_factory, _ = persistence
    context = _context(monetized=True)

    refused = _save(context, _bundle(pricing=False))

    assert refused["outcome"] == "revise", refused
    assert refused.get("outcome_error") is None
    assert "/pricing" in refused["error"]
    assert "/pricing" in context.get("design_docs_save_feedback")
    assert context.get("design_docs_save_attempts") == 1
    assert context.get("design_docs_save_outcome") == "revise"
    assert context.get("experience_spec") is None
    store_factory.assert_not_called()

    accepted = _save(context, _bundle(pricing=True))

    assert accepted["outcome"] == "saved", accepted
    assert context.get("design_docs_save_attempts") == 2
    assert context.get("design_docs_save_outcome") == "saved"
    assert any(page["route"] == "/pricing" for page in context.get("experience_spec")["pages"])


@pytest.mark.parametrize("owners", [0, 2])
def test_pricing_requires_one_surface_owner_by_page_name(persistence, owners):
    _, store_factory, _ = persistence
    context = _context(monetized=True)
    bundle = _bundle(pricing=True)
    bundle["surface_map"]["surfaces"][1]["owned_pages"] = []
    for index in range(owners):
        bundle["surface_map"]["surfaces"][index]["owned_pages"].append("Subscriptions")

    result = _save(context, bundle)

    assert result["outcome"] == "revise", result
    assert "Subscriptions" in result["error"]
    assert "owner" in result["error"].lower()
    store_factory.assert_not_called()


def test_free_app_saves_unchanged_inventory_without_pricing(persistence):
    context = _context(monetized=False)
    bundle = _bundle(pricing=False)
    expected = deepcopy(bundle["experience_spec"])

    result = _save(context, bundle)

    assert result["outcome"] == "saved", result
    assert detach(context.get("experience_spec")) == expected
    assert all(page["route"] != "/pricing" for page in context.get("experience_spec")["pages"])
    assert detach(context.get("design_surface_map")) == bundle["surface_map"]


def test_free_app_cannot_acquire_a_pricing_page(persistence):
    _, store_factory, _ = persistence
    context = _context(monetized=False)

    result = _save(context, _bundle(pricing=True))

    assert result["outcome"] == "revise", result
    assert "/pricing" in result["error"]
    assert context.get("experience_spec") is None
    store_factory.assert_not_called()


@pytest.mark.parametrize("monetized,pricing", [(True, False), (False, True)])
def test_existing_app_inventory_is_preserved_for_brownfield(persistence, monetized, pricing):
    context = _context(monetized=monetized, brownfield="extend")
    bundle = _bundle(pricing=pricing)

    result = _save(context, bundle)

    assert result["outcome"] == "saved", result
    assert detach(context.get("experience_spec")) == bundle["experience_spec"]


def test_refinement_preserves_existing_inventory_without_synthesizing_pages(persistence):
    context = _context(monetized=True)
    binding = detach(context.get("run_build_binding"))
    binding["phase"] = "refinement"
    context.set("run_build_binding", binding)
    bundle = _bundle(pricing=False)

    result = _save(context, bundle)

    assert result["outcome"] == "saved", result
    assert detach(context.get("experience_spec")) == bundle["experience_spec"]


@pytest.mark.parametrize("missing", ["/billing", "/usage"])
def test_subscription_design_must_include_managed_facade_pages(persistence, missing):
    _, store_factory, _ = persistence
    context = _context(monetized=True, subscription=True)
    bundle = _bundle(pricing=True, subscription=True)
    bundle["experience_spec"]["pages"] = [
        page for page in bundle["experience_spec"]["pages"] if page["route"] != missing
    ]

    result = _save(context, bundle)

    assert result["outcome"] == "revise", result
    assert missing in result["error"]
    store_factory.assert_not_called()


def _monetized_plan(context: ContextVariablesBridge, *, subscription: bool = False) -> dict:
    plan = _plan()
    plan["revenue_model"] = "subscription"
    plan["monetization_provider"] = "mozaiks_pay"
    plan["pages"].append({
        "name": "Subscriptions", "route": "/pricing", "purpose": "Compare subscription plans",
        "primary_entities": [], "primary_actions": [], "ui_layout": "full-width",
        "ui_surface": "declarative_page", "page_type_hint": "landing", "sections_hint": [],
    })
    next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")["owned_paths"].append(
        "ui/pages/pricing.yaml"
    )
    plan["build_tasks"].append({
        "task_id": "subscription.config", "task_type": "subscription_config",
        "capability_pack_id": None, "surface_id": "subscription_contract", "surface_kind": "app_policy",
        "execution_target": "AppGenerator", "initial_agent": "ConfigMiddlewareAgent",
        "description": "Serialize approved subscriptions",
        "initial_message": "Emit config/subscriptions.yaml from the subscription contract.",
        "owned_paths": ["config/subscriptions.yaml"], "depends_on": [],
    })
    plan["generation_order"].append("subscription.config")
    if subscription:
        pack_root = ROOT / "factory_app/build_context/mozaikspay"
        config = yaml.safe_load((pack_root / "context.yaml").read_text(encoding="utf-8"))
        descriptor = discover_pack_descriptors(pack_root, config)[0]
        context.set("capability_packs", [descriptor])
        plan["capability_packs"].append({
            "capability_pack_id": "mozaikspay", "surface_id": "mozaikspay_managed",
            "surface_kind": "external_integration", "capability_source": "managed_capability",
            "pack_type": "mozaikspay", "label": "MozaiksPay", "summary": "Managed subscription provider",
            "implementation_mode": "external_integration", "primary_entities": [],
        })
        plan["capability_packs"].append({
            "capability_pack_id": "billing_portal", "surface_id": "billing_portal",
            "surface_kind": "module", "capability_source": "generated_module",
            "pack_type": "custom_domain", "label": "Billing Portal", "summary": "Managed billing facade",
            "implementation_mode": "hybrid", "primary_entities": [],
        })
        for name in ("Billing", "Usage"):
            plan["pages"].append({
                "name": name, "route": f"/{name.lower()}", "purpose": f"Manage {name.lower()}",
                "primary_entities": [], "primary_actions": [], "ui_layout": "full-width",
                "ui_surface": "declarative_page", "page_type_hint": "analytics_dashboard", "sections_hint": [],
            })
            next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")["owned_paths"].append(
                f"ui/pages/{name.lower()}.yaml"
            )
        for task_type, agent, filenames in (
            ("module_contract", "ConfigMiddlewareAgent", ["module.yaml"]),
            ("data_models", "ModelAgent", ["backend/schemas.py"]),
            ("business_services", "ServiceAgent", ["backend/handler.py", "backend/service.py"]),
        ):
            task_id = f"billing_portal.{task_type}"
            plan["build_tasks"].append({
                "task_id": task_id, "task_type": task_type, "capability_pack_id": "billing_portal",
                "surface_id": "billing_portal", "surface_kind": "module",
                "execution_target": "AppGenerator", "initial_agent": agent,
                "description": "Materialize approved billing facade", "initial_message": "Use the managed pack facade.",
                "owned_paths": [f"modules/billing_portal/{filename}" for filename in filenames], "depends_on": [],
            })
            plan["generation_order"].append(task_id)
    context.set("subscription_contract", {
        "contract_required": True,
        "subscription_config_file": {"plans": [{"plan_id": "free"}, {"plan_id": "pro"}]},
    })
    return plan


def test_saved_pricing_inventory_passes_planner_coverage_and_full_review(persistence):
    _load_models()
    context = _context(monetized=True, subscription=True)
    models, _ = load_workflow_structured_outputs("DesignDocs")
    bundle = models["DesignDocsBundle"].model_validate(_bundle(pricing=True, subscription=True))
    assert _save(context, bundle.model_dump(mode="json"))["outcome"] == "saved"
    plan = _monetized_plan(context, subscription=True)

    validate_plan_coverage(plan, context)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    assert {(page["name"], page["route"]) for page in cached["pages"]} == {
        ("Reports", "/reports"), ("Subscriptions", "/pricing"),
        ("Billing", "/billing"), ("Usage", "/usage"),
    }
    assert any(task["task_type"] == "subscription_config" for task in cached["build_tasks"])
    validate_plan_coverage(cached, context)


@pytest.mark.parametrize("invented", ["billing", "usage"])
def test_subscription_contract_cannot_expand_the_approved_inventory(persistence, invented):
    context = _context(monetized=True)
    assert _save(context, _bundle(pricing=True))["outcome"] == "saved"
    plan = _monetized_plan(context)
    plan["pages"].append({"name": invented.title(), "route": f"/{invented}"})
    next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")["owned_paths"].append(
        f"ui/pages/{invented}.yaml"
    )

    with pytest.raises(ValueError, match="approved name/route inventory"):
        validate_plan_coverage(plan, context)
