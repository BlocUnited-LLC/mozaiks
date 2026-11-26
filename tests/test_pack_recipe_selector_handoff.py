"""Exercise app recipes on the selector's real AG2 request, review and planner boundaries."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from ag2 import MemoryStream

from factory_app.workflows._shared.pack_selection import selected_pack_rows
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import _selected_capability_packs
from factory_app.workflows.AppGenerator.tools.hook_managed_capabilities_context import (
    inject_managed_capabilities_context,
)
from factory_app.workflows.DesignDocs.tools import (
    hook_selected_pack_design_context,
    save_design_doc,
)
from mozaiks_cli.commands.init import create_scaffold
from mozaiksai.core import observability
from mozaiksai.core.workflow import llm_config
from mozaiksai.core.workflow.agents import factory
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs import structured
from mozaiksai.core.workflow.workflow_manager import workflow_manager
from mozaiksai.resources import resolve_factory_app_root
from tests import test_designdocs_monetization_inventory as inventory
from tests import test_designdocs_selection_handoff as handoff
from tests.test_factory_pack_selection import _enable_managed

persistence = inventory.persistence
selector = handoff.selector


@pytest.mark.parametrize("mode,expected", [
    ("revision", "directly supplies the newly requested behavior"),
    ("genesis", "Use app_recipes when their stated behavior matches"),
])
def test_revision_catalog_matches_offerings_to_new_behavior(monkeypatch, mode, expected):
    hook = hook_selected_pack_design_context
    available = {"messaging": {"capabilities": [{"capability_id": "messaging.inbox"}]}}
    monkeypatch.setattr(hook, "available_pack_rows", lambda _: available)
    agent = SimpleNamespace(name="DesignPackSelectionAgent", context_variables={"build_mode": mode}, system_message="")
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    hook.inject_pack_selection_context(agent, [])
    assert expected in agent.system_message
    [offering] = json.loads(next(line for line in agent.system_message.splitlines() if line.startswith("[{")))
    assert offering["pack_id"] == "messaging" and offering["capabilities"] == available["messaging"]["capabilities"]


@pytest.mark.parametrize("workspace", ["factory", "new", "operator"])
def test_first_run_billing_offering_respects_workspace_registry(tmp_path, monkeypatch, workspace):
    from factory_app.workflows._shared.pack_selection import available_pack_rows, pin_pack_choices

    root = resolve_factory_app_root() if workspace == "factory" else tmp_path
    if workspace == "operator":
        (root / "build_context").mkdir()
    if workspace != "factory":
        create_scaffold(target_dir=root, preset="chat", app_name="Studio test")
    monkeypatch.delenv("MOZAIKS_BUILD_CONTEXT_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(root / "app"))
    assert ("mozaikspay" in available_pack_rows({}, pack_ids={"mozaikspay"})) == (workspace != "operator")
    assert pin_pack_choices({}, []) == []
    assert not (root / "app/modules/billing_portal").exists()


@pytest.mark.parametrize(("monetized", "subscription", "brownfield", "mode", "recommended"), [
    (True, True, None, "initial", True),
    (True, False, None, "initial", False),
    (False, True, None, "initial", False),
    (True, True, "adopt_existing", "initial", False),
    (True, True, None, "revision", False),
])
def test_only_a_new_subscription_contract_gets_the_billing_recipe(
    monkeypatch, monetized, subscription, brownfield, mode, recommended,
):
    hook = hook_selected_pack_design_context
    monkeypatch.setattr(hook, "available_pack_rows", lambda _: {"mozaikspay": {}})
    context = inventory._context(monetized=monetized, subscription=subscription, brownfield=brownfield)
    context.set("build_mode", mode)
    agent = SimpleNamespace(name="DesignPackSelectionAgent", context_variables=context, system_message="")
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    hook.inject_pack_selection_context(agent, [])
    offer, = json.loads(agent.system_message.splitlines()[-1])
    assert ("saas" in offer.get("app_recipes", {})) == recommended


@pytest.mark.parametrize("domains", [None, {"invalid": {"pack_recipe": {"pack_id": "social"}}}])
def test_incomplete_recipe_cannot_hide_a_verified_offering(monkeypatch, domains):
    hook = hook_selected_pack_design_context
    monkeypatch.setattr(hook, "available_pack_rows", lambda _: {"social": {"capability_source": "generated_module"}})
    monkeypatch.setattr(hook, "load_catalog", lambda _: {"domains": domains})
    agent = SimpleNamespace(name="DesignPackSelectionAgent", context_variables={}, system_message="")
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    hook.inject_pack_selection_context(agent, [])
    rows = json.loads(agent.system_message.splitlines()[-1])
    assert [row["pack_id"] for row in rows] == ["social"]
    assert "app_recipes" not in rows[0]


@pytest.mark.parametrize(("overview", "domain", "monetized", "managed", "expected"), [
    pytest.param("A team inbox for direct and group conversations.", "messaging_communication",
                 False, False, ["messaging"], id="messaging"),
    pytest.param("An online store with products and checkout.", "ecommerce",
                 False, False, ["commerce"], id="store"),
    pytest.param("A community forum for posts and reactions.", "social_community",
                 False, False, ["social"], id="community"),
    pytest.param("Paid SaaS with operator-enabled MozaiksPay for subscriptions.", "saas",
                 True, True, ["mozaikspay"], id="paid-selected"),
    pytest.param("Sell monthly memberships to the recipe archive.", "saas",
                 True, True, ["mozaikspay"], id="paid-without-provider-name"),
    pytest.param("Free SaaS for organizing a film crew.", "saas", False, True, [], id="free-saas"),
    pytest.param("Paid SaaS subscriptions using MozaiksPay.", "saas", True, False, [], id="unavailable-billing"),
    pytest.param("Paid SaaS using the owner's existing provider.", "saas", True, True, [], id="paid-unselected"),
    pytest.param("", None, False, True, [], id="blank"),
])
def test_recipe_reaches_registered_selector_and_reviewed_app_plan(
    selector, persistence, monkeypatch, tmp_path, overview, domain, monetized, managed, expected,
):
    handler, initial = selector
    if managed:
        _enable_managed(tmp_path, monkeypatch)
        # The target workflow owns the trusted registry of operator-enabled offerings.
        assert not workflow_manager.reload_workflow("AppGenerator").get("error")
    concept = inventory._context(monetized=monetized, subscription=monetized).snapshot()
    bridge = factory.ContextVariablesBridge(
        {**concept, **initial.snapshot(), "concept_blueprint": concept["concept_blueprint"],
         "concept_overview": overview, "monetization_enabled": monetized},
        authority_policy=initial._authority_policy,
    )
    bridge._bind_run(("DesignDocs", "host", "chat"), initial._authority_policy)
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        system = "\n".join(message["content"] for message in body["messages"] if message["role"] == "system")
        assert "[AVAILABLE PACK CATALOG]" in system
        assert not overview or overview in system
        section = system.split("[AVAILABLE PACK CATALOG]", 1)[1]
        rows = json.loads(next(line for line in section.splitlines() if line.startswith("[{")))
        ids = {row["pack_id"] for row in rows}
        assert ("mozaikspay" in ids) == managed
        if not monetized:
            assert all("saas" not in row.get("app_recipes", {}) for row in rows)
        recommendations = {row["pack_id"]: row["app_recipes"][domain] for row in rows
                           if domain in row.get("app_recipes", {})}
        if domain == "saas" and monetized and managed:
            assert "Propose MozaiksPay as the default" in recommendations["mozaikspay"]
            assert "explicit alternative" in recommendations["mozaikspay"]
        # Replay the owner's alternate-provider response as well as the default proposal.
        chosen = [] if "owner's existing provider" in overview else list(recommendations)
        return httpx.Response(200, json={
            "id": "offline-recipe", "object": "chat.completion", "created": 1, "model": "offline",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps({"pack_ids": chosen})},
                         "finish_reason": "stop"}],
        })

    async def model_config(*_args, **_kwargs):
        return None, {"config_list": [{"model": "offline", "api_type": "openai", "api_key": "test",
                                       "base_url": "https://provider.invalid/v1"}],
                      "streaming": False, "max_completion_tokens": 128, "max_retries": 0}

    def offline_usage(**_kwargs):
        raise RuntimeError("Usage collection disabled for the offline provider")

    monkeypatch.setattr(llm_config, "get_llm_config", model_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", model_config)
    monkeypatch.setattr(observability, "build_ag2_usage_middleware", offline_usage)
    convert = factory.llm_config_to_ag2_config

    async def select():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda value: convert(value).copy(http_client=client))
            agent = (await factory.create_agents("DesignDocs", context_variables=bridge))["DesignPackSelectionAgent"]
            reply = await agent.ask("Use the approved app requirements.", stream=MemoryStream())
            selection = await reply.content(retries=0)
            payload = selection.model_dump() if hasattr(selection, "model_dump") else json.loads(selection)
            await handoff.dispatch(handler, bridge, payload, 1)

    asyncio.run(select())
    assert len(requests) == 1
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert bridge.get("pack_selection_outcome") == "selected"
    pins = detach(bridge.get("pack_selection"))
    assert [item["pack_id"] for item in pins] == expected
    assert all(set(item) == {"pack_id", "version", "digest"} for item in pins)

    handler._persist_context_variables.assert_awaited_once()
    saved = handler._persist_context_variables.await_args.kwargs["context_variables"]
    assert saved["pack_selection"] == pins
    context = factory.ContextVariablesBridge(saved)
    cards = []

    async def approve(_tool, payload, **_kwargs):
        cards.append(payload)
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", approve)
    result = inventory._save(context, inventory._bundle(pricing=monetized and not expected))
    assert result["outcome"] == "saved", result
    summary = persistence[2].await_args.kwargs["summary_payload"]
    assert summary["access_policy_review"]["pack_identities"] == pins
    if expected:
        assert all(any(f"Selected pack {pack_id} version" in text for text in cards[0]["checkpoints"])
                   for pack_id in expected)

    context.set("workflow_name", "AppGenerator")
    planner = SimpleNamespace(name="AppPlanAgent", context_variables=context, system_message="")
    planner.update_system_message = lambda message: setattr(planner, "system_message", message)
    inject_managed_capabilities_context(planner, [])
    assert [row["id"] for row in selected_pack_rows(context)] == expected
    assert "Authorized reusable packs: " + json.dumps(pins, separators=(",", ":")) in planner.system_message

    if monetized and managed:
        plan = inventory._monetized_plan(context, subscription=True)
        if expected:
            pricing = next(page for page in plan["pages"] if page["route"] == "/pricing")
            pricing["name"] = "Pricing"
            review = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
            assert review["outcome"] == "ready", review
        else:
            # An operator-enabled provider was offered but the approved design declined it.
            with pytest.raises(ValueError, match="renew DesignDocs review"):
                _selected_capability_packs(plan, context)
