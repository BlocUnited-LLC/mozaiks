from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml
from mozaiksai.core.workflow.workflow_ui_catalog import get_workflow_shipped_component_names


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def _read_yaml(relative_path: str):
    return yaml.safe_load(_read(relative_path))


def _workflow_manifest_paths() -> list[Path]:
    from tests.conftest import _resolve_active_app_root
    workspace = _workspace()
    roots = ["factory_app/workflows"]
    app_root = _resolve_active_app_root()
    if app_root is not None:
        roots.insert(0, str(app_root / "workflows"))
    paths: list[Path] = []
    for root in roots:
        workflow_root = workspace / root if not (Path(root).is_absolute()) else Path(root)
        if not workflow_root.exists():
            continue
        paths.extend(sorted(workflow_root.glob("*/tools.yaml")))
    return paths


def _parse_index_exports(index_content: str) -> tuple[set[str], list[str]]:
    exported_names: set[str] = set()
    module_paths: list[str] = []

    for match in re.finditer(r"export\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", index_content):
        spec, module_path = match.groups()
        module_paths.append(module_path)
        for item in spec.split(","):
            token = item.strip()
            if not token:
                continue
            if token.startswith("default as "):
                exported_names.add(token.replace("default as ", "", 1).strip())
                continue
            if " as " in token:
                exported_names.add(token.split(" as ", 1)[1].strip())
                continue
            exported_names.add(token)
    return exported_names, module_paths


def _resolve_export_target(index_file: Path, module_path: str) -> Path | None:
    target = (index_file.parent / module_path).resolve()
    if target.suffix:
        return target if target.exists() else None

    candidates = [
        Path(f"{target}.js"),
        Path(f"{target}.jsx"),
        target / "index.js",
        target / "index.jsx",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def test_repo_owned_interactive_ui_tools_use_canonical_helper_import() -> None:
    files = [
        "factory_app/workflows/AppGenerator/tools/generate_and_download.py",
        "factory_app/workflows/AgentGenerator/tools/generate_and_download.py",
        "factory_app/workflows/AgentGenerator/tools/request_api_key.py",
        "factory_app/workflows/WorkflowPrimitiveAcceptance/tools/request_acceptance_approval.py",
    ]

    for relative_path in files:
        content = _read(relative_path)
        assert "from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool" in content
        assert "from app.modules.ui_tools import use_ui_tool, UIToolError" not in content
        assert "from mozaiksai.core.workflow.outputs.ui_tools import UIToolError, use_ui_tool" not in content


def test_agent_generator_runtime_helpers_are_yaml_first() -> None:
    generate_download = _read("factory_app/workflows/AgentGenerator/tools/generate_and_download.py")
    export_helper = _read("factory_app/workflows/AgentGenerator/tools/export_agent_workflow.py")

    assert "tools.json" not in generate_download
    assert "agents.json" not in generate_download
    assert "tools.yaml" in generate_download

    assert '"/agents.yaml"' in export_helper
    assert '"/tools.yaml"' in export_helper
    assert '"/agents.json"' not in export_helper
    assert '"/tools.json"' not in export_helper


def test_repo_workflow_tools_do_not_import_global_shared_workflow_bucket() -> None:
    from tests.conftest import _resolve_active_app_root
    app_root = _resolve_active_app_root()
    if app_root is None:
        import pytest
        pytest.skip("No active app workspace configured.")
    workflow_root = app_root / "workflows"
    assert not (workflow_root / "_shared").exists()

    offenders = []
    for path in workflow_root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "workflows._shared" in content or "app.workflows._shared" in content:
            offenders.append(str(path.relative_to(app_root)))

    assert offenders == []


def test_request_api_key_exposes_current_runtime_contract() -> None:
    source = _read("factory_app/workflows/AgentGenerator/tools/request_api_key.py")
    module = ast.parse(source)
    function_def = next(
        node for node in module.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "request_api_key"
    )
    arg_names = [arg.arg for arg in function_def.args.args]

    assert "display_name" in arg_names
    assert "store_connector" in arg_names
    assert "return_for_e2b" in arg_names
    assert "service_display_name" not in arg_names


def test_generator_prompts_treat_connector_state_as_platform_owned() -> None:
    agent_generator = _read("factory_app/workflows/AgentGenerator/agents.yaml")
    design_docs = _read("factory_app/workflows/DesignDocs/agents.yaml")
    app_generator = _read("factory_app/workflows/AppGenerator/agents.yaml")

    assert "platform connector flow" in agent_generator
    assert "Do not create workflow collections for API keys" in agent_generator
    assert "workspace integrations/admin surface" in agent_generator
    assert "some integrations may already be ready from the workspace integrations surface" in agent_generator
    assert "missing dependency" in agent_generator
    assert "must not be modeled as app/business collections inside `database_intent_bundle`" in design_docs
    assert "Connector credentials, API-key metadata, and workspace integration records are platform-owned integration state." in app_generator
    assert "app connector inventory as the source of truth" in app_generator


def test_app_generator_page_contract_stays_declarative() -> None:
    content = _read("factory_app/workflows/AppGenerator/agents.yaml")
    agents = _read_yaml("factory_app/workflows/AppGenerator/agents.yaml")
    handoffs = _read_yaml("factory_app/workflows/AppGenerator/handoffs.yaml")

    agent_names = {agent["name"] for agent in agents["agents"]}
    expected_agents = {
        "InterviewAgent",
        "AppPlanAgent",
        "AppSchemaAgent",
        "AppUIQualityAgent",
        "AdminRegistryAgent",
        "AssemblyAgent",
        "DatabaseAgent",
        "ConfigMiddlewareAgent",
        "ModuleContractQualityAgent",
        "ModelAgent",
        "AppValidationAgent",
        "IntegrationTestAgent",
        "DownloadAgent",
        "ServiceAgent",
        "FrontendStubAgent",
        "ControllerAgent",
    }
    connected_agents = {
        rule[side]
        for rule in handoffs["handoff_rules"]
        for side in ("source_agent", "target_agent")
        if rule[side] not in {"user", "terminate"}
    }

    assert "Keep persistent app pages declarative." in content
    assert "Only emit custom full-page React when a true primitive gap remains" in content
    assert "The default owner of persistent pages is `AppSchemaAgent`." in content
    assert "Do NOT plan a second raw-frontend lane inside AppGenerator." in content
    assert "Persistent page ownership is exclusive to `AppSchemaAgent`." in content
    assert "persistent app pages still belong in `app.json` + `ui/pages/*.yaml`" in content
    assert "ui/index.js" in content
    assert "theme_config_patch" in content
    assert "shell_config" in content
    assert "config/shell.json" in content
    assert "brand/theme_config.json" in content
    assert agent_names == expected_agents
    assert connected_agents <= expected_agents


def test_ui_docs_define_page_customization_boundary() -> None:
    surface_contract = _read("docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md")
    assembly_contract = _read("docs/architecture/builder/appgenerator-output-assembly-contract.md")

    assert "Persistent app UI" in surface_contract
    assert "Bounded custom UI" in surface_contract
    assert "default to declarative page schemas" in surface_contract
    assert "custom_route_bundle" in surface_contract
    assert "workflow UI, transition UI, and bounded custom UI" in surface_contract
    assert "theme_config_patch" in assembly_contract
    assert "Theme vs Shell Ownership" in assembly_contract
    assert "config/shell.json" in assembly_contract


def test_architecture_index_references_appgenerator_output_contract() -> None:
    contract = _read("docs/architecture/builder/appgenerator-output-assembly-contract.md")

    assert "save_app_schema" in contract
    assert "generate_and_download" in contract
    assert "Raw Frontend Path Removed" in contract
    assert "secondary raw frontend page/component generation lane" in contract
    assert "config/shell.json" in contract


def test_generated_workflow_ui_contract_is_co_located_with_workflow_pack() -> None:
    converter = _read("factory_app/workflows/AgentGenerator/tools/workflow_converter.py")
    registry = _read("chat-ui/src/@chat-workflows/index.js")
    app_vite = _read("web_shell/vite.config.js")
    embed_vite = _read("chat-ui/vite.embed.config.js")
    router = _read("chat-ui/src/core/WorkflowUIRouter.js")
    tailwind = _read("chat-ui/tailwind.config.js")

    assert "ChatUI/src/workflows" not in converter
    assert 'rel_path.startswith("ui/")' in converter
    assert '"path": "ui/index.js"' in converter

    assert "@chat-workflows-root/*/ui/index.{js,jsx}" in registry
    assert "@chat-workflows-root-secondary" not in registry
    assert "mozaiks-platform/app/workflows" not in registry
    assert "const namespacedComponentName = `${workflowName}:${componentName}`;" in registry
    assert "'@chat-workflows-root': platformWorkflowRoot" in app_vite
    assert "@chat-workflows-root-secondary" not in app_vite
    assert "'@chat-workflows-root': fileURLToPath(new URL('./src/workflows_stub', import.meta.url))" in embed_vite
    assert "../mozaiks-platform/" not in tailwind
    assert "`@chat-workflows/${workflow}/components/index.js`" not in router
    assert "workflow && component ? `${workflow}:${component}` : null" in router
    assert "workflow && toolName ? `${workflow}:${toolName}` : null" in router


def test_repo_owned_workflow_ui_surfaces_use_shared_bridges() -> None:
    style_files = [
        "factory_app/workflows/AgentGenerator/ui/AgentAPIKeysBundleInput.js",
        "factory_app/workflows/AppGenerator/ui/AppWorkbench.js",
        "factory_app/workflows/ValueEngine/ui/ValueEngine/components/ConceptBlueprint.js",
    ]
    runtime_files = [
        "factory_app/workflows/AgentGenerator/ui/ActionPlan.js",
        "factory_app/workflows/AgentGenerator/ui/AgentAPIKeysBundleInput.js",
    ]

    for relative_path in style_files:
        content = _read(relative_path)
        assert "workflowSurfaceStyles.js" in content
        assert "artifactDesignSystem" not in content

    for relative_path in runtime_files:
        content = _read(relative_path)
        assert "workflowSurfaceRuntime.js" in content
        assert "core/toolsLogger" not in content


def test_repo_owned_workflow_ui_barrels_register_top_level_surfaces() -> None:
    agent_index = _read("factory_app/workflows/AgentGenerator/ui/index.js")
    app_index = _read("factory_app/workflows/AppGenerator/ui/index.js")
    value_index = _read("factory_app/workflows/ValueEngine/ui/index.js")
    app_workbench = _read("factory_app/workflows/AppGenerator/ui/AppWorkbench.js")
    export_actions = _read("factory_app/workflows/AppGenerator/ui/ExportActions.js")

    assert "AgentAPIKeysBundleInput" in agent_index
    assert "ActionPlan" in agent_index
    assert "AppWorkbench" in app_index
    assert "ConceptBlueprint" in value_index

    assert "import { useAppValidationWorkbench } from './useAppValidationWorkbench';" in app_workbench
    assert "theme_config.json" not in app_workbench
    assert "@mozaiks/chat-ui/core/ui/DownloadCenter.js" in export_actions


def test_repo_owned_one_way_ui_emitters_use_canonical_surface_helper() -> None:
    files = [
        "factory_app/workflows/AgentGenerator/tools/mermaid_sequence_diagram.py",
        "factory_app/workflows/ValueEngine/tools/manifest.py",
        "factory_app/workflows/ExistingAppDiscovery/tools/save_existing_app_artifacts.py",
        "factory_app/workflows/WorkflowPrimitiveAcceptance/tools/show_acceptance_diagram.py",
    ]

    for relative_path in files:
        content = _read(relative_path)
        assert "from mozaiksai.core.workflow.ui_tools import emit_ui_surface" in content
        assert "send_ui_tool_event(" not in content


def test_ui_system_spec_documents_interactive_vs_one_way_producer_contracts() -> None:
    content = _read("docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md")

    assert "response-bearing workflow UI uses" in content
    assert "fire-and-forget workflow UI uses" in content
    assert "use_ui_tool(" in content
    assert "emit_ui_surface(" in content


def test_workflow_manifests_use_explicit_ui_surface_types() -> None:
    files = [
        "factory_app/workflows/AgentGenerator/tools.yaml",
        "factory_app/workflows/AppGenerator/tools.yaml",
        "factory_app/workflows/DesignDocs/tools.yaml",
        "factory_app/workflows/RuntimeToolCallSmoke/tools.yaml",
        "factory_app/workflows/ValueEngine/tools.yaml",
        "factory_app/workflows/WorkflowPrimitiveAcceptance/tools.yaml",
    ]

    for relative_path in files:
        manifest = yaml.safe_load(_read(relative_path)) or {}
        for section_name in ("tools", "lifecycle_tools"):
            for entry in manifest.get(section_name) or []:
                tool_type = entry.get("tool_type")
                ui = entry.get("ui")
                if tool_type == "Agent_Tool":
                    assert ui is None, f"{relative_path}:{entry.get('function')} should not declare ui"
                if tool_type in {"UI_Tool", "UI_Surface"}:
                    assert isinstance(ui, dict), f"{relative_path}:{entry.get('function')} is missing ui metadata"
                    assert ui.get("component")
                    assert ui.get("mode")
                    assert ui.get("workflow_primitive")
                    assert ui.get("realization")
                    assert ui.get("workflow_primitive") != "composer_reply"
                if tool_type == "UI_Tool":
                    ui_contract = entry.get("ui_contract")
                    assert isinstance(ui_contract, dict), f"{relative_path}:{entry.get('function')} is missing ui_contract"
                    assert ui_contract.get("surface_kind") == "agent_tool"
                    assert isinstance(ui_contract.get("payload_schema"), dict)
                    assert isinstance(ui_contract.get("actions_schema"), list)


def test_workflow_primitive_acceptance_exports_expected_ui_surfaces() -> None:
    manifest = _read_yaml("factory_app/workflows/WorkflowPrimitiveAcceptance/tools.yaml")
    response_fixture = _read_yaml("factory_app/workflows/WorkflowPrimitiveAcceptance/smoke_responses.json")
    shipped_components = set(get_workflow_shipped_component_names())

    assert [tool["ui"]["component"] for tool in manifest["tools"]] == [
        "ApprovalCard",
        "DiagramViewer",
    ]
    assert [tool["ui"]["workflow_primitive"] for tool in manifest["tools"]] == [
        "approval_card",
        "diagram_viewer",
    ]
    assert [tool["ui"]["realization"] for tool in manifest["tools"]] == [
        "shipped_component",
        "shipped_component",
    ]
    assert set(tool["ui"]["component"] for tool in manifest["tools"]) <= shipped_components
    assert response_fixture["tool_responses"]["ApprovalCard"]["approved"] is True


def test_agent_generator_smoke_fixture_covers_real_ag2_workflow_ui_contract() -> None:
    manifest = _read_yaml("factory_app/workflows/AgentGenerator/tools.yaml")
    response_fixture = _read_yaml("factory_app/workflows/AgentGenerator/smoke_responses.json")
    smoke_prompt = _read("factory_app/workflows/AgentGenerator/smoke_prompt.txt").strip()
    agent_index = _read("factory_app/workflows/AgentGenerator/ui/index.js")
    exported_names, _ = _parse_index_exports(agent_index)

    manifest_components = {
        tool["ui"]["component"]
        for section_name in ("tools", "lifecycle_tools")
        for tool in manifest.get(section_name) or []
        if isinstance(tool.get("ui"), dict) and tool["ui"].get("component")
    }
    scripted_components = {name for name in response_fixture["tool_responses"] if name != "*"}
    manifest_realizations = {
        tool["ui"]["component"]: tool["ui"].get("realization")
        for section_name in ("tools", "lifecycle_tools")
        for tool in manifest.get(section_name) or []
        if isinstance(tool.get("ui"), dict) and tool["ui"].get("component")
    }
    assistant_reply_rules = response_fixture["assistant_reply_rules"]

    assert response_fixture["default_input_reply"] == (
        "Use the current assumptions: manual chat startup, urgency levels low/medium/high/critical, no assets, no integrations, and proceed with the workflow."
    )
    assert len(response_fixture["input_replies"]) >= 3
    assert "*" in response_fixture["tool_responses"]
    assert response_fixture["tool_responses"]["*"]["approved"] is True
    assert response_fixture["tool_responses"]["DownloadCenter"]["action"] == "download_complete"
    assert response_fixture["tool_responses"]["AgentAPIKeysBundleInput"]["action"] == "cancel"
    assert "ActionPlan" not in response_fixture["tool_responses"]
    assert any(rule["contains"] == "final tweaks" for rule in assistant_reply_rules)
    assert any("Proceed with implementation." in rule["reply"] for rule in assistant_reply_rules)
    assert "internal helpdesk lead" in smoke_prompt
    assert "classify urgency" in smoke_prompt
    assert "ask for human approval before closing" in smoke_prompt
    assert "Do not use external APIs or third-party integrations." in smoke_prompt
    assert {"DownloadCenter", "DiagramViewer", "AgentAPIKeysBundleInput"} <= manifest_components
    assert manifest_realizations["DownloadCenter"] == "shipped_component"
    assert manifest_realizations["DiagramViewer"] == "shipped_component"
    assert manifest_realizations["AgentAPIKeysBundleInput"] == "workflow_wrapper"
    assert (scripted_components - manifest_components) <= exported_names


def test_agent_generator_review_handoff_uses_user_text_state_triggers() -> None:
    handoffs = _read_yaml("factory_app/workflows/AgentGenerator/handoffs.yaml")
    context_vars = _read_yaml("factory_app/workflows/AgentGenerator/context_variables.yaml")

    review_handoffs = {
        (rule["source_agent"], rule["target_agent"]): rule
        for rule in handoffs["handoff_rules"]
        if rule["source_agent"] == "user" and rule["target_agent"] in {"ContextVariablesAgent", "PatternAgent"}
    }
    review_defs = context_vars["definitions"]

    assert review_handoffs[("user", "ContextVariablesAgent")]["condition_type"] == "expression"
    assert review_handoffs[("user", "ContextVariablesAgent")]["condition_scope"] == "pre"
    assert review_handoffs[("user", "ContextVariablesAgent")]["condition"] == "${workflow_review_approved} == True"
    assert review_handoffs[("user", "PatternAgent")]["condition_type"] == "expression"
    assert review_handoffs[("user", "PatternAgent")]["condition_scope"] == "pre"
    assert review_handoffs[("user", "PatternAgent")]["condition"] == "${workflow_review_revision_requested} == True"

    approved_trigger = review_defs["workflow_review_approved"]["source"]["triggers"][0]
    revision_trigger = review_defs["workflow_review_revision_requested"]["source"]["triggers"][0]

    assert approved_trigger["type"] == "user_text"
    assert "regex" in approved_trigger["match"]
    assert revision_trigger["type"] == "user_text"
    assert "regex" in revision_trigger["match"]


def test_ui_manifest_components_are_exported_by_resolvable_workflow_barrels() -> None:
    shipped_components = set(get_workflow_shipped_component_names())

    for manifest_path in _workflow_manifest_paths():
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        required_components: set[str] = set()
        for section_name in ("tools", "lifecycle_tools"):
            for entry in manifest.get(section_name) or []:
                if entry.get("tool_type") not in {"UI_Tool", "UI_Surface"}:
                    continue
                ui = entry.get("ui") or {}
                component = ui.get("component")
                if component:
                    required_components.add(str(component))

        if not required_components:
            continue

        index_file = manifest_path.parent / "ui/index.js"
        if not index_file.exists():
            assert required_components <= shipped_components, (
                f"{manifest_path} declares non-shipped UI components but {index_file} is missing"
            )
            continue

        index_content = index_file.read_text(encoding="utf-8")
        exported_names, module_paths = _parse_index_exports(index_content)
        missing_exports = sorted((required_components - exported_names) - shipped_components)
        assert not missing_exports, (
            f"{index_file} missing exports for manifest UI components: {missing_exports}"
        )

        unresolved_modules = [
            module_path for module_path in module_paths if _resolve_export_target(index_file, module_path) is None
        ]
        assert not unresolved_modules, (
            f"{index_file} has unresolved export targets: {unresolved_modules}"
        )


def test_core_ui_index_exports_shipped_workflow_components() -> None:
    content = _read("chat-ui/src/core/ui/index.js")

    for component_name in get_workflow_shipped_component_names():
        assert component_name in content


def test_workflow_ui_components_use_payload_prop_contract() -> None:
    files = [
        "factory_app/workflows/ExistingAppDiscovery/ui/DiscoveryBriefCard.jsx",
        "factory_app/workflows/AppGenerator/ui/AppWorkbench.js",
        "factory_app/workflows/AgentGenerator/ui/ActionPlan.js",
    ]
    for relative_path in files:
        content = _read(relative_path)
        assert "({ data" not in content
        assert "payload" in content


def test_transition_shell_screens_stay_workflow_agnostic() -> None:
    launcher = _read("chat-ui/src/ui/screens/LauncherScreen.jsx")
    confirm = _read("chat-ui/src/ui/screens/ConfirmScreen.jsx")

    # Generic shell components should not embed product-specific journey copy.
    assert "Start from a rough idea and let Mozaiks" not in launcher
    assert "BUILD WITH MOZAIKS" not in launcher
    assert "NO-CODE AUTOMATION" not in launcher
    assert "Failed to start workflow" not in confirm

    assert "transition?.ui?.props" in launcher
    assert "transition?.ui?.props" in confirm


def test_frontend_prompts_enforce_theme_shell_ownership_boundaries() -> None:
    app_generator = _read("factory_app/workflows/AppGenerator/agents.yaml")
    design_docs = _read("factory_app/workflows/DesignDocs/agents.yaml")
    agent_generator = _read("factory_app/workflows/AgentGenerator/agents.yaml")

    assert "theme_config_patch` owns visual tokens only" in app_generator
    assert "shell_config` owns shell content/behavior only" in app_generator
    assert "asset_manifest` owns reusable media inventory metadata" in app_generator
    assert "custom_route_bundle" in app_generator
    assert "use `PageFrame` from `@mozaiks/chat-ui`" in app_generator
    assert "use `useChatUI()` / shipped adapters instead of hardcoded API base URLs" in app_generator
    assert "Do NOT put raw spacing, padding, width, or density tokens in `shell_config`" in app_generator
    assert "Do NOT put header actions, profile menu items, or footer links in `theme_config_patch`" in app_generator

    assert "Do NOT define header/profile/notification/footer content objects in ui_schema" in design_docs
    assert "Do NOT encode raw visual token values" in design_docs
    assert "asset_manifest.json" in design_docs
    assert "custom_route_bundle" in design_docs
    assert "generic account/profile/preferences as host-owned platform primitives" in design_docs

    assert "Typography must come from semantic theme tokens" in agent_generator
    assert "never hardcode `font-family` names in component code" in agent_generator
    assert "prefer a file-backed transition component exported from `extended_orchestration/ui/index.js`" in agent_generator
    assert "Use `ui.props` only for lightweight built-in fallback tuning" in agent_generator


def test_agent_generator_primitive_reference_matches_runtime_contract() -> None:
    content = _read("factory_app/workflows/AgentGenerator/agents.yaml")

    assert "composer_reply" in content
    assert "workflow_primitive" in content
    assert "Do NOT emit workflow-local UI requirements for framework-owned shell status surfaces" in content

    # Canonical runtime-aligned props
    assert "`DataTable`  — `id`, `columns[]`, `data[]`" in content
    assert "`Form`       — `id`, `fields[]`, `layout`, `columns`, `submit_label`" in content
    assert "`Grid`       — `columns`, `gap`, `children`" in content
    assert "`Panel`      — `title`, `subtitle`, `children`, `actions[]`" in content
    assert "`SummaryStrip` — `items[]`" in content
    assert "`Metric`     — `label`, `value`, `detail`" in content
    assert "`Empty`      — `title`, `message`, `action`, `icon`" in content

    # Stale guidance that drifts from shipped primitive contracts
    assert "`rows[]`" not in content
    assert "`onRowClick`" not in content
    assert "`submitLabel`" not in content
    assert "`renderItem`" not in content


def test_form_primitive_uses_static_tailwind_grid_column_classes() -> None:
    content = _read("chat-ui/src/ui/primitives/Form.jsx")
    assert "GRID_COL_CLASS" in content
    assert "grid-cols-${columns}" not in content


def test_extended_orchestration_transition_components_are_file_backed() -> None:
    index_content = _read("factory_app/workflows/extended_orchestration/ui/index.js")
    registry = _read_yaml("factory_app/workflows/extended_orchestration/extension_registry.json")

    assert "CodingJourneySelector" in index_content
    assert "AppTypeSelector" in index_content
    assert "DatabaseSetupSelector" in index_content

    transition_components = {
        entry["ui"]["component"]
        for entry in registry.get("transitions", [])
        if isinstance(entry, dict) and isinstance(entry.get("ui"), dict) and entry["ui"].get("component")
    }
    assert {
        "CodingJourneySelector",
        "AppTypeSelector",
        "DatabaseSetupSelector",
    }.issubset(transition_components)

    # Transition routing stays semantic; visual copy/images live in the React stubs.
    for entry in registry.get("transitions", []):
        if not isinstance(entry, dict):
            continue
        ui = entry.get("ui")
        if not isinstance(ui, dict):
            continue
        assert "props" not in ui


def test_platform_ui_fonts_flow_through_semantic_theme_tokens() -> None:
    from tests.conftest import active_app_root
    app_root = active_app_root()
    typography_path = app_root / "ui" / "theme" / "typography.js"
    app_card_path = app_root / "ui" / "components" / "AppCard.jsx"
    dashboard_path = app_root / "ui" / "pages" / "custom" / "Dashboard.jsx"
    if not (typography_path.exists() and app_card_path.exists() and dashboard_path.exists()):
        pytest.skip("Product theme token fixtures are not present in the active app workspace")
    typography = typography_path.read_text(encoding="utf-8")
    app_card = app_card_path.read_text(encoding="utf-8")
    dashboard = dashboard_path.read_text(encoding="utf-8")

    assert "var(--font-body" in typography
    assert "var(--font-heading" in typography

    # Dashboard/App cards must consume shared semantic font stacks.
    assert "THEME_BODY_FONT_STACK" in app_card
    assert "THEME_HEADING_FONT_STACK" in app_card
    assert "THEME_BODY_FONT_STACK" in dashboard
    assert "THEME_HEADING_FONT_STACK" in dashboard

    # Do not regress to literal brand font names in component code.
    literal_font_names = ("Rajdhani", "Orbitron", "Fagrak")
    ui_root = app_root / "ui"
    for path in sorted(ui_root.rglob("*.jsx")):
        source = path.read_text(encoding="utf-8")
        for font_name in literal_font_names:
            assert font_name not in source, f"{path} should use semantic font tokens, found literal '{font_name}'"
