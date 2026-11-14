"""Deterministic fake task outputs exercise normal collection, registry, assembly and shell owners."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import _apply_app_config_contracts
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_admin_panel_pages,
    scan_generated_bundle,
)
from factory_app.workflows.AppGenerator.tools.save_admin_registry import save_admin_registry
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from mozaiksai.hosts import shell_config
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]


def test_portal_children_are_removed_from_explicit_global_navigation():
    stale = {"id": "usage", "path": "/admin/usage", "label": "Usage"}
    result = {
        "header": {"pages": [stale], "actions": [stale]},
        "profile": {"menu": [stale]},
        "footer": {"links": [{"href": "/admin/usage", "label": "Usage"}]},
        "mobile": {"bottomBar": {"items": [stale]}},
    }
    shell_config._apply_dynamic_shell_navigation(
        result,
        pages=[{"path": "/admin/usage", "component": "AdminPortal", "meta": {"adminPage": "usage"}}],
        navigation={"items": [stale]},
        shortcuts={},
    )
    assert "/admin/usage" not in json.dumps(result)


def test_theme_identity_fallback_retains_explicit_brand_and_input():
    from factory_app.workflows.AppGenerator.tools.save_app_schema import resolve_app_theme_config

    approved = {"identity": {"name": "Approved"}, "theme": {"primary": "teal"}}
    assert resolve_app_theme_config(approved, None, app_name="Fallback") == approved
    assert approved["identity"] == {"name": "Approved"}
    assert resolve_app_theme_config(None, None, app_name="New App") == {"identity": {"name": "New App"}}


def registry(*ids):
    return {
        "schema_version": "mozaiks.admin.registry.v1",
        "pages": [
            {
                "id": page_id,
                "label": page_id.title(),
                "path": "/admin" if page_id == "overview" else "/admin/" + page_id,
                "order": index,
            }
            for index, page_id in enumerate(ids)
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["saas", "workflow"])
async def test_completed_typed_batch_passes_registry_assembly_scanner_and_runtime_shell(
    monkeypatch, tmp_path, kind
):
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
    from tests import test_appgenerator_materializing_task_authority as workflow
    from tests import test_appplan_materialization_acceptance as saas

    module = saas if kind == "saas" else workflow
    original = module.assemble_app_tasks
    workflow_root = ROOT / "factory_app/workflows/AppGenerator"
    rules = yaml.safe_load((workflow_root / "transition_graph.yaml").read_text())[
        "transition_rules"
    ]
    agents = yaml.safe_load((workflow_root / "agents.yaml").read_text(encoding="utf-8"))["agents"]
    graph = compile_transition_rules_to_graph(
        rules,
        initial_agent_name="AppPlanAgent",
        agent_id_by_name={agent["name"]: agent["name"] for agent in agents},
        max_turns=30,
    )
    declared = registry("overview", "usage" if kind == "saas" else "operations")

    async def assemble(*, context_variables):
        status = context_variables.get("app_task_batch_status")
        assert status == "completed"
        assert (
            resolve_next_agent(
                graph,
                current_agent_name="AppPlanAgent",
                context_variables={"app_task_batch_status": status, "app_plan_outcome": "ready"},
            )
            == "AdminRegistryAgent"
        )
        context_variables.set("platform_user_email", "owner@example.test")
        save_admin_registry(
            admin_registry=declared, code_files=[], context_variables=context_variables
        )
        assert (
            resolve_next_agent(graph, current_agent_name="AdminRegistryAgent", context_variables={})
            == "AssemblyAgent"
        )
        return await original(context_variables=context_variables)

    monkeypatch.setattr(module, "assemble_app_tasks", assemble)
    if kind == "saas":
        files, app_root, _, context, _ = await saas._materialize_plan_bundle(tmp_path=tmp_path)
    else:
        from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import save_auth_scaffold

        files, values, _ = await workflow.materialize_fixture(monkeypatch)
        context = ContextVariablesBridge(values)
        scaffold = await save_auth_scaffold(context_variables=context)
        files.update({item["filename"]: item["content"] for item in scaffold["code_files"]})
        app_root = tmp_path / "app"
        # This legacy fixture aggregates two artifact planes. Host workflows beside app/.
        for name, content in files.items():
            target = (tmp_path / name) if name.startswith("workflows/") else (app_root / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    app_files = {name: content for name, content in files.items() if not name.startswith("workflows/")}
    manifest = json.loads(files["app.json"])
    assert json.loads(files["brand/theme_config.json"])["identity"]["name"] == (manifest.get("appName") or manifest["appId"])
    assert manifest["admins"] == ["owner@example.test"]
    ai = json.loads(files["config/ai.json"])
    assert (manifest.get("appName") or manifest["appId"]) in ai["ask"]["ask_mode_prompt"]
    assert ai["workflows"]["entry_point"] == (None if kind == "saas" else "DocumentAnalysis")
    assert (
        scan_generated_bundle(
            app_files,
            context_variables=context,
            capability_packs=context.get("app_build_plan")["capability_packs"],
        )
        == []
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_PROVIDER", "none")
    monkeypatch.setenv("AUTH_ANON_ACCESS", "local")
    reset_auth_adapter()
    monkeypatch.setattr(shell_config, "resolve_app_root", lambda: app_root)
    monkeypatch.setattr(shell_config, "resolve_active_app_root", lambda: app_root)
    shell = await shell_config.build_shell_config()
    assert shell["assistant_available"] is True
    assert [item for item in shell["profile"]["menu"] if item.get("id") == "admin-portal"] == [
        {
            "id": "admin-portal",
            "label": "Admin",
            "action": "navigate",
            "path": "/admin",
            "requiresRole": "admin",
        }
    ]
    assert all(
        not page["meta"]["navigation"]["include"]
        for page in shell["pages"]
        if page.get("component") == "AdminPortal"
    )
    assert len([page for page in shell["pages"] if page.get("component") == "AdminPortal"]) == 2
    reset_auth_adapter()


@pytest.mark.parametrize(
    "mirror", [None, "pages: [{id: stale, label: Stale, path: /admin/stale}]", "pages: []"]
)
def test_typed_registry_replaces_stale_and_contradictory_mirrors(mirror):
    context = ContextVariablesBridge(
        {
            "code_files": [
                {"filename": "admin/admin_registry.yaml", "content": "pages: []"},
                {"filename": "keep.txt", "content": "kept"},
            ]
        }
    )
    save_admin_registry(
        admin_registry=registry("overview", "usage"),
        code_files=[]
        if mirror is None
        else [{"filename": "admin/admin_registry.yaml", "content": mirror}],
        context_variables=context,
    )
    files = {item["filename"]: item["content"] for item in context.get("code_files")}
    assert files["keep.txt"] == "kept"
    assert [page["id"] for page in yaml.safe_load(files["admin/admin_registry.yaml"])["pages"]] == [
        "overview",
        "usage",
    ]
    assert [page["id"] for page in context.get("admin_registry")["pages"]] == ["overview", "usage"]


def test_registry_page_references_close_against_published_enabled_pages():
    files = {"modules/tasks/contracts/admin.yaml": "panels: [{id: tasks, page: usage}]"}
    assert _scan_admin_panel_pages(files)
    files["admin/admin_registry.yaml"] = yaml.safe_dump(registry("overview"))
    assert _scan_admin_panel_pages(files)
    files["admin/admin_registry.yaml"] = yaml.safe_dump(registry("overview", "usage"))
    assert _scan_admin_panel_pages(files) == []


def test_refinement_keeps_verified_baseline_admins_and_explicit_assistant_configuration():
    context = factory_context({"platform_user_email": "editor@example.test"})
    context["run_build_binding"]["phase"] = "refinement"
    baseline = {
        "app.json": json.dumps({"appName": "Original", "admins": ["owner@example.test"]}),
        "config/ai.json": '{"ask":{"ask_mode_prompt":"App-specific guidance"}}',
    }
    result = _apply_app_config_contracts(
        [
            {
                "filename": "app.json",
                "content": json.dumps({"appName": "Original", "admins": ["model@example.test"]}),
            }
        ],
        app_id="generated-app",
        app_build_plan={},
        context_variables=context,
        baseline_files=baseline,
    )
    files = {item["filename"]: item["content"] for item in result}
    assert json.loads(files["app.json"])["admins"] == ["owner@example.test"]
    assert files["config/ai.json"] == baseline["config/ai.json"]


@pytest.mark.asyncio
async def test_profile_identity_lookup_uses_the_shared_collection_and_exact_scope(monkeypatch):
    from mozaiksai.core.profile import identity

    collection = AsyncMock()
    collection.find_one.return_value = {"email": "owner@example.test"}
    monkeypatch.setattr(
        identity, "get_mongo_client", lambda: {"mozaiksai": {"UserProfiles": collection}}
    )
    assert await identity.read_account_profile_identity(app_id="factory", user_id="owner") == {
        "email": "owner@example.test"
    }
    collection.find_one.assert_awaited_once_with(
        {"_id": "factory:owner", "app_id": "factory", "user_id": "owner"}, {"_id": 0, "email": 1}
    )
