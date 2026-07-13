from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FACTORY_WORKFLOWS = REPO_ROOT / "factory_app" / "workflows"
FACTORY_BUILD_CONTEXT = REPO_ROOT / "factory_app" / "build_context"

EXPECTED_APPGENERATOR_CATALOGS = {
    "capability_directory.yaml",
    "domain_catalogs.yaml",
    "capability_routing.yaml",
    "file_contracts.yaml",
    "module_archetypes.yaml",
    "shell_presets.yaml",
    "workflow_archetypes.yaml",
}

EXPECTED_AGENTGENERATOR_PATTERNBOOKS = {
    "ag2_network_patterns.yaml",
}

EXPECTED_WEBAPP_BUILDER_CATALOGS = {
    "language_profile.yaml",
}

EXPECTED_INTEGRATIONS_CATALOGS = {
    "catalog.yaml",
}

LEGACY_WORKFLOW_ROOT_CATALOGS = [
    *(f"factory_app/workflows/AppGenerator/{name}" for name in EXPECTED_APPGENERATOR_CATALOGS),
    *(f"factory_app/workflows/AppGenerator/tools/{name}" for name in EXPECTED_APPGENERATOR_CATALOGS),
    "factory_app/workflows/_shared/ag2_network_patterns.yaml",
    "factory_app/workflows/_shared/factory_catalogs.py",
    "factory_app/workflows/_shared/pattern_catalog.py",
    "factory_app/workflows/_shared/catalogs",
    "factory_app/build_context/workflows",
    "factory_app/build_context/AgentGenerator/patternbook",
    "factory_app/workflows/AgentGenerator/patternbook",
    "factory_app/workflows/AppGenerator/patternbook",
]


def test_appgenerator_catalog_yamls_exist_in_factory_build_context() -> None:
    root = FACTORY_BUILD_CONTEXT / "AppGenerator"
    for name in EXPECTED_APPGENERATOR_CATALOGS:
        path = root / name
        assert path.exists(), f"Missing AppGenerator factory catalog: {path}"
        assert path.suffix == ".yaml"


def test_factory_build_context_uses_named_context_roots() -> None:
    assert (FACTORY_BUILD_CONTEXT / "AppGenerator" / "context.yaml").is_file()
    assert (FACTORY_BUILD_CONTEXT / "AgentGenerator" / "context.yaml").is_file()
    assert not (FACTORY_BUILD_CONTEXT / "workflows").exists()
    assert not (FACTORY_BUILD_CONTEXT / "build_context.yaml").exists()
    assert not any(FACTORY_BUILD_CONTEXT.glob("*/manifest.yaml"))
    for context_root in FACTORY_BUILD_CONTEXT.iterdir():
        if not context_root.is_dir():
            continue
        assert (context_root / "context.yaml").is_file()
        assert not (context_root / "packs").exists()
        assert not (context_root / "contracts").exists()
        assert not (context_root / "catalogs").exists()
        allowed = {"context.yaml", "contract.yaml", "templates"}
        if context_root.name == "AppGenerator":
            allowed |= EXPECTED_APPGENERATOR_CATALOGS
        if context_root.name == "AgentGenerator":
            allowed |= EXPECTED_AGENTGENERATOR_PATTERNBOOKS
        if context_root.name == "webapp_builder":
            allowed |= EXPECTED_WEBAPP_BUILDER_CATALOGS
        if context_root.name == "integrations":
            allowed |= EXPECTED_INTEGRATIONS_CATALOGS
        actual = {item.name for item in context_root.iterdir()}
        assert actual <= allowed, f"{context_root} has non-canonical build-context entries: {sorted(actual - allowed)}"


def test_context_yaml_is_structural_not_semantic_guidance() -> None:
    semantic_context_fields = {"description", "purpose", "templates"}
    semantic_projection_fields = {"purpose", "description"}

    for context_path in FACTORY_BUILD_CONTEXT.glob("*/context.yaml"):
        data = yaml.safe_load(context_path.read_text(encoding="utf-8")) or {}
        assert not (semantic_context_fields & set(data)), (
            f"{context_path} has semantic or derived fields in context.yaml: "
            f"{sorted(semantic_context_fields & set(data))}"
        )
        for asset in data.get("assets") or []:
            for projection in asset.get("projections") or []:
                assert not (semantic_projection_fields & set(projection)), (
                    f"{context_path} asset projection has semantic fields: "
                    f"{sorted(semantic_projection_fields & set(projection))}"
                )


def test_context_yaml_uses_explicit_assets_not_implicit_lanes() -> None:
    forbidden_top_level = {"catalogs", "contract"}
    allowed_asset_kinds = {"catalog", "contract", "templates"}

    for context_path in FACTORY_BUILD_CONTEXT.glob("*/context.yaml"):
        data = yaml.safe_load(context_path.read_text(encoding="utf-8")) or {}
        assert not (forbidden_top_level & set(data)), (
            f"{context_path} uses implicit build-context lanes: "
            f"{sorted(forbidden_top_level & set(data))}"
        )
        assets = data.get("assets")
        assert isinstance(assets, list) and assets, f"{context_path} must declare assets"
        for asset in assets:
            assert isinstance(asset, dict), f"{context_path} asset must be a mapping"
            assert {"path", "kind"} <= set(asset), f"{context_path} asset must declare path and kind"
            assert asset["kind"] in allowed_asset_kinds, (
                f"{context_path} asset kind is not canonical: {asset['kind']!r}"
            )
            asset_path = (context_path.parent / asset["path"]).resolve()
            assert asset_path.exists(), f"{context_path} asset path does not exist: {asset_path}"


def test_build_pack_contracts_are_typed_rule_contracts() -> None:
    forbidden_top_level = {"purpose", "description", "generation_rules", "recommended_facades"}
    required_top_level = {
        "contract_id",
        "contract_type",
        "selection_rules",
        "required_outputs",
        "forbidden_outputs",
        "runtime_boundaries",
        "facades",
    }

    for contract_path in FACTORY_BUILD_CONTEXT.glob("*/contract.yaml"):
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
        assert not (forbidden_top_level & set(data)), (
            f"{contract_path} uses prose-oriented legacy fields: "
            f"{sorted(forbidden_top_level & set(data))}"
        )
        assert required_top_level <= set(data), (
            f"{contract_path} is missing typed contract fields: "
            f"{sorted(required_top_level - set(data))}"
        )


def test_catalog_asset_projections_use_canonical_fields() -> None:
    deprecated_fields = {
        "context_type",
        "source_path",
        "target_agents",
        "projection_mode",
        "output_marker",
        "selector",
        "match_field",
    }
    required_fields = {"id", "records", "recipients", "render", "marker"}

    for context_path in FACTORY_BUILD_CONTEXT.glob("*/context.yaml"):
        data = yaml.safe_load(context_path.read_text(encoding="utf-8")) or {}
        projections = data.get("projections") or {}
        assert "prompts" not in projections, f"{context_path} must not use projections.prompts"
        assert "prompt_inputs" not in projections, f"{context_path} prompt projections belong on assets"
        for asset in data.get("assets") or []:
            if asset.get("kind") != "catalog":
                continue
            for prompt_input in asset.get("projections") or []:
                assert not (deprecated_fields & set(prompt_input)), (
                    f"{context_path} asset projection uses deprecated fields: "
                    f"{sorted(deprecated_fields & set(prompt_input))}"
                )
                assert required_fields <= set(prompt_input), (
                    f"{context_path} asset projection is missing required fields: "
                    f"{sorted(required_fields - set(prompt_input))}"
                )


def test_agentgenerator_patternbooks_exist_in_factory_build_context() -> None:
    root = FACTORY_BUILD_CONTEXT / "AgentGenerator"
    for name in EXPECTED_AGENTGENERATOR_PATTERNBOOKS:
        path = root / name
        assert path.exists(), f"Missing AgentGenerator patternbook: {path}"
        assert path.suffix == ".yaml"


def test_prompt_catalogs_are_not_in_workflow_runtime_roots() -> None:
    for relative in LEGACY_WORKFLOW_ROOT_CATALOGS:
        assert not (REPO_ROOT / relative).exists(), (
            f"Prompt catalog must live under factory_app/build_context, not {relative}"
        )


def test_factory_catalog_resolver_is_shared_hook_utility() -> None:
    assert (FACTORY_WORKFLOWS / "_shared" / "hook_utils.py").is_file()
    assert not (FACTORY_WORKFLOWS / "_shared" / "catalogs").exists()
    assert (FACTORY_WORKFLOWS / "AgentGenerator" / "tools" / "ag2_patterns.py").is_file()


def test_appgenerator_hook_tools_use_factory_catalog_resolver() -> None:
    tools_dir = FACTORY_WORKFLOWS / "AppGenerator" / "tools"
    catalog_hooks = {
        "hook_capability_routing_context.py",
        "hook_domain_catalog_context.py",
        "hook_file_contract_context.py",
        "hook_shell_preset_context.py",
    }
    for name in catalog_hooks:
        content = (tools_dir / name).read_text(encoding="utf-8")
        assert "_shared.hook_utils" in content, f"{name} must load catalogs through shared hook utilities"
        assert "workflow_context_path" in content, f"{name} must resolve factory build-context paths deterministically"
        assert "_shared.catalogs" not in content, f"{name} must not use removed _shared.catalogs"


def test_ai_pack_workflow_hook_uses_current_startup_contract() -> None:
    content = (
        FACTORY_WORKFLOWS
        / "AppGenerator"
        / "tools"
        / "hook_ai_pack_workflow_context.py"
    ).read_text(encoding="utf-8")
    assert "workflow_startup_mode=BackendOnly" in content
    assert re.search(r"(?<!workflow_)startup_mode=BackendOnly", content) is None
