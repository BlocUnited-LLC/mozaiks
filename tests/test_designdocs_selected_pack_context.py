"""Selected pack guidance reaches DesignDocs through the real prompt middleware."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import yaml

from mozaiksai.core.session.build_context import load_trusted_build_context
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from mozaiksai.core.workflow.execution.middleware import MozaiksPromptMiddleware, _resolve_import

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "factory_app/workflows/DesignDocs"
PACK = ROOT / "factory_app/build_context/content"
MANAGED_PACK = ROOT / "factory_app/build_context/mozaikspay"
HEADER = "[SELECTED PACK DESIGN CONTRACTS]"


def _prompt_runner(context):
    config = yaml.safe_load((WORKFLOW / "middleware.yaml").read_text(encoding="utf-8"))
    [entry] = [row for row in config["prompt_middleware"] if row["function"] == "inject_selected_pack_design_context"]
    hook, _ = _resolve_import("DesignDocs", entry["filename"], entry["function"], WORKFLOW)
    assert hook is not None
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))
    view = load_context_variables_config(definitions).agents["DesignDocsAgent"]
    return MozaiksPromptMiddleware(
        None, None, middleware_functions=[hook], agent_name="DesignDocsAgent",
        base_system_message="Design the approved app.", context_bridge=context,
        context_variables=view.variables,
    )


def _prompt(runner):
    return asyncio.run(runner._run_prompt_middleware(SimpleNamespace(variables={}, prompt=[])))


def test_trusted_content_contract_reaches_the_real_design_prompt_without_template_code(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    shutil.copytree(PACK, root / "content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    trusted = load_trusted_build_context(policy, build_context_root=root)
    trusted["operator_contracts"].append({"contract_id": "unselected", "runtime_boundaries": [{"rule": "UNSELECTED SECRET"}]})
    bridge = ContextVariablesBridge(trusted)
    before = bridge.snapshot()

    prompt = _prompt(_prompt_runner(bridge))

    assert HEADER in prompt
    assert "content.entries.read" in prompt
    assert "content.entries.manage" in prompt
    assert "source_capability_packs" in prompt
    for action in ("list_entries", "get_entry", "create_entry", "update_entry", "archive_entry"):
        assert f"content.{action}" in prompt
    assert "content.manage" in prompt
    for rule in trusted["operator_contracts"][0]["runtime_boundaries"]:
        assert rule["rule"] in prompt
    assert "UNSELECTED SECRET" not in prompt
    assert str(root) not in prompt
    assert "class ContentHandler" not in prompt
    assert bridge.snapshot() == before


def test_unverified_or_unselected_descriptors_do_not_supply_prompt_contracts(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    shutil.copytree(PACK, root / "content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    trusted = load_trusted_build_context(policy, build_context_root=root)
    trusted["capability_packs"][0]["version"] = "999.0.0"
    bridge = ContextVariablesBridge(trusted)
    assert HEADER not in _prompt(_prompt_runner(bridge))

    # A bare generated_module ID describes ordinary authored work, not a pack.
    bridge = ContextVariablesBridge({**trusted, "capability_packs": [{"id": "content", "capability_source": "generated_module"}]})
    assert HEADER not in _prompt(_prompt_runner(bridge))

    # DesignDocs must not infer a current selection from an old build plan.
    bridge = ContextVariablesBridge({
        "capability_packs": [], "app_build_plan": {"capability_packs": load_trusted_build_context(policy, build_context_root=root)["capability_packs"]},
    })
    assert HEADER not in _prompt(_prompt_runner(bridge))


def test_recomputed_prompt_drops_pack_guidance_when_selection_disappears(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    shutil.copytree(PACK, root / "content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    bridge = ContextVariablesBridge(load_trusted_build_context(policy, build_context_root=root))
    runner = _prompt_runner(bridge)
    assert HEADER in _prompt(runner)
    bridge.set("capability_packs", [])
    assert HEADER not in _prompt(runner)


def test_managed_facade_and_local_pack_keep_their_distinct_design_boundaries(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    for pack in (PACK, MANAGED_PACK):
        shutil.copytree(pack, root / pack.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    bridge = ContextVariablesBridge(load_trusted_build_context(policy, build_context_root=root))

    prompt = _prompt(_prompt_runner(bridge))

    assert "Pack: content (source: generated_module)" in prompt
    assert "Pack: mozaikspay (source: managed_capability)" in prompt
    assert "billing_portal" in prompt
    assert "start_subscription_checkout" in prompt
    assert "subscription_management" in prompt
    assert "content.create_entry" in prompt
    assert "a pack does not prescribe the app's layout or visual identity" in prompt
    assert str(root) not in prompt


def test_same_pack_preserves_distinct_approved_experience_inputs(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    shutil.copytree(PACK, root / "content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    trusted = load_trusted_build_context(policy, build_context_root=root)
    briefs = [
        "A quiet, image-led architecture portfolio with a generous grid.",
        "A dense, high-contrast investigative newsroom with a compact index.",
    ]
    prompts = [_prompt(_prompt_runner(ContextVariablesBridge({**trusted, "concept_overview": brief}))) for brief in briefs]

    for prompt, brief, other in zip(prompts, briefs, reversed(briefs), strict=True):
        assert brief in prompt
        assert other not in prompt
        assert "content.list_entries" in prompt
        assert "do not invent roles or grants" in prompt


def test_operator_selection_resolves_the_shipped_pack_before_prompting(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    selection = root / "selection"
    selection.mkdir(parents=True)
    (selection / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "selection",
        "applies_to_workflows": ["DesignDocs"],
        "assets": [],
        "values": {"operator_capabilities": ["content"]},
        "projections": {"context_variables": {"operator_capabilities": {"from": "operator_capabilities"}}},
    }), encoding="utf-8")
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(ROOT / "factory_app"))
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    trusted = load_trusted_build_context(policy, build_context_root=root)

    assert [pack["id"] for pack in trusted["capability_packs"]] == ["content"]
    prompt = _prompt(_prompt_runner(ContextVariablesBridge(trusted)))
    assert "Pack: content (source: generated_module)" in prompt
    assert "content.list_entries" in prompt
    assert "Pack: mozaikspay" not in prompt


def test_invalid_template_module_is_reported_without_exposing_its_payload(tmp_path, monkeypatch, caplog):
    root = tmp_path / "build_context"
    copied = root / "content"
    shutil.copytree(PACK, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    manifest = copied / "templates/modules/content/module.yaml"
    value = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    value["actions"] = "private-invalid-payload"
    manifest.write_text(yaml.safe_dump(value), encoding="utf-8")
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    bridge = ContextVariablesBridge(load_trusted_build_context(policy, build_context_root=root))

    prompt = _prompt(_prompt_runner(bridge))

    assert "Pack module contract unavailable (content)" in prompt
    assert "do not invent its actions" in prompt
    assert "private-invalid-payload" not in prompt
    assert "private-invalid-payload" not in caplog.text


def test_selected_pack_guidance_composes_with_all_declared_design_middleware(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    shutil.copytree(PACK, root / "content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    config = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=config["definitions"])
    context = {
        **load_trusted_build_context(policy, build_context_root=root),
        "concept_blueprint": {"capability_pack_hints": ["ai_review_pack"]},
        "brownfield_build_path": "full_migration",
        "design_docs_save_outcome": "revise",
        "design_docs_save_feedback": "Use the approved editor role.",
    }
    bridge = ContextVariablesBridge(context)
    middleware = yaml.safe_load((WORKFLOW / "middleware.yaml").read_text(encoding="utf-8"))["prompt_middleware"]
    functions = [
        _resolve_import("DesignDocs", entry["filename"], entry["function"], WORKFLOW)[0]
        for entry in middleware if entry["agent"] == "DesignDocsAgent"
    ]
    assert all(functions)
    view = load_context_variables_config(config).agents["DesignDocsAgent"]
    runner = MozaiksPromptMiddleware(
        None, None, middleware_functions=functions, agent_name="DesignDocsAgent",
        base_system_message="Design the approved app.", context_bridge=bridge, context_variables=view.variables,
    )

    prompt = _prompt(runner)

    assert prompt.startswith("[REPAIR REQUIRED]")
    assert "Use the approved editor role." in prompt
    assert "[AI PACK SURFACE DECLARATIONS]" in prompt
    assert "[EXISTING APP ENHANCEMENT]" in prompt
    assert "[UI SURFACE TAXONOMY]" in prompt
    assert prompt.count(HEADER) == 1
    assert "content.list_entries" in prompt
    assert bridge.snapshot() == context
