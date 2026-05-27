from __future__ import annotations

import importlib.util
import json
from importlib import import_module
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import assemble_app_tasks as assemble_module

hook_quality_module = import_module(
    "factory_app.workflows.AppGenerator.tools.hook_app_ui_quality_gate"
)


def _load_ui_quality_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "tools"
        / "ui_quality.py"
    )
    spec = importlib.util.spec_from_file_location(
        "tests.appgenerator_ui_quality_direct", file_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ui_quality_module = _load_ui_quality_module()


def _read_yaml(relative_path: str):
    workspace = Path(__file__).resolve().parents[1]
    return yaml.safe_load((workspace / relative_path).read_text(encoding="utf-8"))


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


def test_review_ui_quality_passes_without_warnings() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": [],
            "app_schema_ready": True,
            "app_manifest": {"app_name": "Support Operations"},
            "app_pages": [{"name": "Tickets", "route": "/tickets", "page_type": "record_list", "sections": []}],
        }
    )

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "passed"
    assert context.data["app_ui_quality_status"] == "passed"
    assert context.data["app_ui_quality_revision_request"] is None


def test_review_ui_quality_requires_persisted_schema_before_passing() -> None:
    context = _Context({"app_ui_quality_warnings": []})

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "needs_revision"
    assert "did not persist the app schema" in result["revision_request"]
    assert "did not persist app_manifest" in result["revision_request"]
    assert "did not persist any declarative pages" in result["revision_request"]


def test_review_ui_quality_routes_warnings_to_schema_revision() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": [
                "Dashboard has 8 top-level sections; keep collection pages scan-first."
            ],
            "app_ui_quality_revision_count": 0,
        }
    )

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "needs_revision"
    assert result["revision_count"] == 1
    assert context.data["app_ui_quality_status"] == "needs_revision"
    assert "Dashboard has 8 top-level sections" in context.data[
        "app_ui_quality_revision_request"
    ]


def test_review_ui_quality_routes_lane_mismatch_warnings_to_schema_revision() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": [
                "Notifications: build plan marked /notifications as custom_react_page, but schema emitted a declarative page. Honor the planned UI lane or revise the upstream plan before assembly."
            ],
            "app_ui_quality_revision_count": 0,
        }
    )

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "needs_revision"
    assert "Honor the planned UI lane" in context.data["app_ui_quality_revision_request"]


def test_review_ui_quality_audits_persisted_page_schemas() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": [],
            "app_schema_ready": True,
            "app_manifest": {"app_name": "Operations", "pages": ["Operations"]},
            "app_pages": [
                {
                    "name": "Operations Dashboard",
                    "route": "/operations",
                    "title": "Operations Dashboard",
                    "page_type": "analytics_dashboard",
                    "sections": [
                        {
                            "id": "summary-a",
                            "primitive": "SummaryStrip",
                            "config": {"items": [{"label": "Total", "value": 1}]},
                        },
                        {
                            "id": "summary-b",
                            "primitive": "SummaryStrip",
                            "config": {"items": [{"label": "Open", "value": 1}]},
                        },
                    ],
                }
            ],
        }
    )

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "needs_revision"
    assert any("dashboard-style page naming" in warning for warning in result["warnings"])
    assert any("uses 2 SummaryStrip sections" in warning for warning in result["warnings"])


def test_review_ui_quality_audits_custom_route_react() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": [],
            "app_schema_ready": True,
            "app_manifest": {"app_name": "Deal Room", "pages": [], "custom_routes": ["deal-room"]},
            "app_pages": [],
            "app_custom_route_bundle": {
                "route_manifest": [{"id": "deal-room"}],
                "page_files": [
                    {
                        "path": "ui/pages/custom/DealRoom.jsx",
                        "content": (
                            "import { Card } from '@mozaiks/chat-ui/ui';\n"
                            "export default function DealRoom(){ return <Card>Placeholder</Card>; }\n"
                        ),
                    }
                ],
            },
        }
    )

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "needs_revision"
    assert any("imports non-canonical component primitives: Card" in warning for warning in result["warnings"])
    assert any("placeholder/internal copy" in warning for warning in result["warnings"])


def test_review_ui_quality_blocks_custom_route_theme_contract_violations() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": [],
            "app_schema_ready": True,
            "app_manifest": {
                "app_name": "Operations Studio",
                "default_route": "/operations",
                "pages": [],
                "custom_routes": ["operations"],
            },
            "app_pages": [],
            "app_custom_route_bundle": {
                "route_manifest": [
                    {
                        "id": "operations",
                        "label": "Operations",
                        "path": "/operations",
                        "component": "OperationsPage",
                        "requiresAuth": True,
                        "meta": {"title": "Operations"},
                        "purpose": "Interactive operations page.",
                    }
                ],
                "page_files": [
                    {
                        "route_id": "operations",
                        "path": "ui/pages/custom/OperationsPage.jsx",
                        "component_name": "OperationsPage",
                        "registry_key": "OperationsPage",
                        "purpose": "Interactive operations page.",
                        "content": (
                            "const palette = { primary: '#28c7ff' };\n"
                            "function StatusPill({ status }){ return <span>{status}</span>; }\n"
                            "export default function OperationsPage(){\n"
                            "  return <main style={{ color: 'hsl(191, 100%, 58%)', fontFamily: 'Inter' }}>\n"
                            "    <button className=\"rounded-xl bg-primary px-4 py-2\">Run</button>\n"
                            "  </main>;\n"
                            "}\n"
                        ),
                    },
                    {
                        "route_id": "operations",
                        "path": "public/fonts/Brand.woff2",
                        "component_name": "OperationsFont",
                        "registry_key": "OperationsFont",
                        "purpose": "Invalid font artifact.",
                        "content": "<binary>",
                    },
                ],
            },
        }
    )

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "needs_revision"
    assert any("page-local palette/theme object" in warning for warning in result["warnings"])
    assert any("hardcodes color values" in warning for warning in result["warnings"])
    assert any("hardcodes font-family styling" in warning for warning in result["warnings"])
    assert any("defines local visual primitive clones" in warning for warning in result["warnings"])
    assert any("raw primary-styled <button>" in warning for warning in result["warnings"])
    assert any("local font outside brand/fonts" in warning for warning in result["warnings"])


def test_review_ui_quality_requires_custom_route_component_registration() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": [],
            "app_schema_ready": True,
            "app_manifest": {
                "app_name": "Deal Room",
                "default_route": "/deal-room",
                "pages": [],
                "custom_routes": ["deal-room"],
            },
            "app_pages": [],
            "app_custom_route_bundle": {
                "route_manifest": [
                    {
                        "id": "deal-room",
                        "label": "Deal Room",
                        "path": "/deal-room",
                        "component": "InvestorDealRoomPage",
                        "requiresAuth": True,
                        "meta": {"title": "Deal Room"},
                        "purpose": "Live collaboration view that cannot be represented with page primitives.",
                    }
                ],
                "page_files": [
                    {
                        "route_id": "deal-room",
                        "path": "ui/pages/custom/InvestorDealRoom.jsx",
                        "component_name": "InvestorDealRoom",
                        "registry_key": "InvestorDealRoom",
                        "purpose": "Custom deal-room page.",
                        "content": "export default function InvestorDealRoom(){ return <main>Deal Room</main>; }\n",
                    }
                ],
            },
        }
    )

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "needs_revision"
    assert any(
        "registry_key 'InvestorDealRoom' must match route_manifest component 'InvestorDealRoomPage'"
        in warning
        for warning in result["warnings"]
    )
    assert any(
        "declares component 'InvestorDealRoomPage' but no page_files entry registers that key"
        in warning
        for warning in result["warnings"]
    )


def test_review_ui_quality_requires_page_file_for_each_custom_route() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": [],
            "app_schema_ready": True,
            "app_manifest": {
                "app_name": "Deal Room",
                "default_route": "/deal-room",
                "pages": [],
                "custom_routes": ["deal-room"],
            },
            "app_pages": [],
            "app_custom_route_bundle": {
                "route_manifest": [
                    {
                        "id": "deal-room",
                        "label": "Deal Room",
                        "path": "/deal-room",
                        "component": "InvestorDealRoomPage",
                        "requiresAuth": True,
                        "meta": {"title": "Deal Room"},
                        "purpose": "Live collaboration view that cannot be represented with page primitives.",
                    }
                ],
                "page_files": [],
            },
        }
    )

    result = ui_quality_module.review_ui_quality(context_variables=context)

    assert result["status"] == "needs_revision"
    assert any("page_files must be a non-empty list" in warning for warning in result["warnings"])
    assert any(
        "declares component 'InvestorDealRoomPage' but no page_files entry registers that key"
        in warning
        for warning in result["warnings"]
    )


def test_review_ui_quality_blocks_after_revision_budget() -> None:
    context = _Context(
        {
            "app_ui_quality_warnings": ["Page uses nested cards."],
            "app_ui_quality_revision_count": 2,
        }
    )

    result = ui_quality_module.review_ui_quality(
        max_revision_attempts=2,
        context_variables=context,
    )

    assert result["status"] == "blocked"
    assert result["revision_count"] == 2
    assert context.data["app_ui_quality_status"] == "blocked"
    assert "operator review" in context.data["app_ui_quality_revision_request"]


@pytest.mark.asyncio
async def test_assemble_app_tasks_requires_passed_ui_quality_gate() -> None:
    context = _Context(
        {
            "app_id": "ops-portal",
            "app_ui_quality_status": "needs_revision",
            "app_ui_quality_warnings": ["Dashboard repeats low-value metrics."],
            "_mfj_resume_inject_as": "mfj_app_task_results",
            "mfj_app_task_results": {},
        }
    )

    with pytest.raises(ValueError, match="app_ui_quality_status must be 'passed'"):
        await assemble_module.assemble_app_tasks(context_variables=context)


@pytest.mark.asyncio
async def test_assemble_app_tasks_prefers_persisted_schema_artifacts(tmp_path: Path) -> None:
    app_dir = tmp_path / "generated" / "apps" / "support" / "build-1" / "app"
    (app_dir / "ui" / "pages").mkdir(parents=True)
    (app_dir / "app.json").write_text('{"appName":"Support"}\n', encoding="utf-8")
    (app_dir / "ui" / "pages" / "Tickets.yaml").write_text(
        "name: Tickets\nroute: /tickets\n",
        encoding="utf-8",
    )

    context = _Context(
        {
            "app_id": "support",
            "app_ui_quality_status": "passed",
            "app_schema_ready": True,
            "generated_app_dir": str(app_dir),
            "_mfj_resume_inject_as": "mfj_app_task_results",
            "mfj_app_task_results": {},
        }
    )

    result = await assemble_module.assemble_app_tasks(context_variables=context)

    filenames = {item["filename"] for item in result["code_files"]}
    assert filenames == {"app.json", "ui/pages/Tickets.yaml"}
    assert context.data["assembled_source"] == "schema_artifacts"
    assert context.data["generated_files"]["app.json"] == '{"appName":"Support"}\n'


def test_appgenerator_ui_quality_handoffs_and_tools_are_canonical() -> None:
    handoffs = _read_yaml("factory_app/workflows/AppGenerator/handoffs.yaml")
    tools = _read_yaml("factory_app/workflows/AppGenerator/tools.yaml")
    hooks = _read_yaml("factory_app/workflows/AppGenerator/hooks.yaml")
    agents = (
        Path(__file__).resolve().parents[1]
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "agents.yaml"
    ).read_text(encoding="utf-8")

    handoff_pairs = {
        (rule["source_agent"], rule["target_agent"]): rule
        for rule in handoffs["handoff_rules"]
    }
    tool_entries = {
        (entry["agent"], entry["function"]): entry
        for entry in tools["tools"]
    }

    assert ("AppSchemaAgent", "AppUIQualityAgent") in handoff_pairs
    assert handoff_pairs[("AppUIQualityAgent", "AppSchemaAgent")]["condition"] == (
        '${app_ui_quality_status} == "needs_revision"'
    )
    assert handoff_pairs[("AppUIQualityAgent", "AdminRegistryAgent")]["condition"] == (
        '${app_ui_quality_status} == "passed"'
    )
    assert ("AdminRegistryAgent", "AssemblyAgent") in handoff_pairs
    assert ("AssemblyAgent", "IntegrationReadinessAgent") in handoff_pairs
    assert ("IntegrationReadinessAgent", "DownloadAgent") in handoff_pairs
    assert ("IntegrationReadinessAgent", "AppValidationAgent") in handoff_pairs
    assert handoff_pairs[("AppUIQualityAgent", "user")]["condition"] == (
        '${app_ui_quality_status} == "blocked"'
    )

    assert ("AppUIQualityAgent", "review_ui_quality") in tool_entries
    assert (
        tool_entries[("AppUIQualityAgent", "review_ui_quality")]["auto_tool_call"]
        is True
    )
    assert ("AppUIQualityAgent", "review_ui_acceptance") not in tool_entries

    assert any(
        entry["hook_agent"] == "AppUIQualityAgent"
        and entry["function"] == "run_app_ui_quality_gate"
        for entry in hooks["hooks"]
    )

    assert "- name: AppUIQualityAgent" in agents
    assert "app_ui_quality_revision_request" in agents
    assert "app_ui_quality_status == \"passed\"" in agents
    assert "import shared UI primitives from `@mozaiks/chat-ui/ui`" in agents
    assert "never define local visual clones" in agents
    assert "semantic variants" in agents
    assert "app/brand/theme_config.json" in agents
    assert "app/config/shell.json" in agents
    assert "page-local brand palettes" in agents
    assert "raw primary-styled `<button" in agents


def test_app_ui_quality_hook_persists_previous_schema_before_handoff(
    monkeypatch, tmp_path: Path
) -> None:
    generated_root = tmp_path / "generated"
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(generated_root))

    context = _Context({"app_id": "support-ops", "build_id": "build-1"})

    class _Agent:
        name = "AppUIQualityAgent"

        def __init__(self) -> None:
            self.context_variables = context
            self.system_message = "Quality agent"

        def update_system_message(self, message: str) -> None:
            self.system_message = message

    messages = [
        {
            "name": "AppSchemaAgent",
            "content": json.dumps(
                {
                    "manifest": {
                        "app_name": "Support Operations",
                        "version": "1.0.0",
                        "default_route": "/tickets",
                        "pages": ["Tickets"],
                        "custom_routes": [],
                    },
                    "pages": [
                        {
                            "name": "Tickets",
                            "route": "/tickets",
                            "title": "Tickets",
                            "page_type": "record_list",
                            "layout": "full-width",
                            "sections": [
                                {
                                    "id": "tickets-header",
                                    "primitive": "PageHeader",
                                    "config": {"title": "Tickets"},
                                },
                                {
                                    "id": "tickets-table",
                                    "primitive": "ResourceTable",
                                    "config": {
                                        "columns": [
                                            {"key": "subject", "label": "Ticket"}
                                        ],
                                        "data": [],
                                    },
                                },
                            ],
                        }
                    ],
                    "custom_route_bundle": None,
                    "theme_config_patch": None,
                    "shell_config": None,
                    "asset_manifest": None,
                }
            ),
        }
    ]

    hook_quality_module.run_app_ui_quality_gate(_Agent(), messages)

    assert context.data["app_schema_ready"] is True
    assert context.data["app_ui_quality_status"] == "passed"
    assert context.data["app_pages"][0]["route"] == "/tickets"
