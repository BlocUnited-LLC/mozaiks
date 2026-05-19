from __future__ import annotations

import pytest

from factory_app.workflows._shared.generated_ui_contract import (
    VALID_PAGE_TYPES,
    audit_generated_react_files,
    audit_page_schemas,
)


def test_generated_ui_contract_accepts_clean_react_surface() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/review/ChangeReviewPanel.jsx",
                "content": (
                    "import { Button, InlineEmptyState, Panel, StatusPill } from '@mozaiks/chat-ui/ui';\n"
                    "export default function ChangeReviewPanel(){\n"
                    "  return <Panel title=\"Review\" action={<StatusPill label=\"Ready\" tone=\"primary\" />}>\n"
                    "    <InlineEmptyState title=\"No changes\" />\n"
                    "    <Button label=\"Approve\" />\n"
                    "  </Panel>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated workflow-local React",
        require_jsx=True,
    )

    assert warnings == []


def test_generated_ui_contract_blocks_removed_primitives_and_hardcoded_style() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/review/ReviewDashboard.js",
                "content": (
                    "import { Badge, Card, Metric } from '@mozaiks/chat-ui/ui';\n"
                    "export default function ReviewDashboard(){\n"
                    "  return <Card style={{ color: '#fff', fontFamily: 'Rajdhani' }}>\n"
                    "    <Badge label=\"Todo\" /><Metric label=\"A\" value=\"1\" />\n"
                    "  </Card>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated workflow-local React",
        require_jsx=True,
    )

    assert any("must use .jsx" in warning for warning in warnings)
    assert any("imports non-canonical component primitives: Badge, Card" in warning for warning in warnings)
    assert any("renders non-canonical component primitive <Badge>" in warning for warning in warnings)
    assert any("hardcodes color values" in warning for warning in warnings)
    assert any("literal brand fonts" in warning for warning in warnings)
    assert any("dashboard-style naming" in warning for warning in warnings)


def test_generated_ui_contract_blocks_direct_font_family_declarations() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/ReportsPage.jsx",
                "content": (
                    "export default function ReportsPage(){\n"
                    "  return <main className=\"font-heading\" style={{ fontFamily: 'Inter, sans-serif' }}>Reports</main>;\n"
                    "}\n"
                ),
            },
            {
                "filename": "ui/pages/custom/ReportSummary.jsx",
                "content": (
                    "export default function ReportSummary(){\n"
                    "  return <style>{`.report-title { font-family: Inter, sans-serif; }`}</style>;\n"
                    "}\n"
                ),
            },
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    font_warnings = [warning for warning in warnings if "hardcodes font-family styling" in warning]
    assert len(font_warnings) == 2


def test_generated_ui_contract_blocks_inline_hex_color() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/BrandPreview.jsx",
                "content": (
                    "export default function BrandPreview(){\n"
                    "  return <main style={{ color: '#28c7ff' }}>Preview</main>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    assert any("hardcodes color values" in warning for warning in warnings)


def test_generated_ui_contract_blocks_background_hex_color() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/BrandPreview.jsx",
                "content": (
                    "export default function BrandPreview(){\n"
                    "  return <main style={{ backgroundColor: '#111827' }}>Preview</main>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    assert any("hardcodes color values" in warning for warning in warnings)


def test_generated_ui_contract_blocks_rgb_and_hsl_colors() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/RgbPreview.jsx",
                "content": (
                    "export default function RgbPreview(){\n"
                    "  return <main style={{ color: 'rgb(40, 199, 255)' }}>Preview</main>;\n"
                    "}\n"
                ),
            },
            {
                "filename": "ui/pages/custom/HslPreview.jsx",
                "content": (
                    "export default function HslPreview(){\n"
                    "  return <main style={{ color: 'hsl(191, 100%, 58%)' }}>Preview</main>;\n"
                    "}\n"
                ),
            },
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    color_warnings = [warning for warning in warnings if "hardcodes color values" in warning]
    assert len(color_warnings) == 2


def test_generated_ui_contract_blocks_local_palette_or_theme_objects() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/PalettePreview.jsx",
                "content": (
                    "const palette = { primary: '#28c7ff' };\n"
                    "export default function PalettePreview(){\n"
                    "  return <main className=\"bg-background text-foreground\">Preview</main>;\n"
                    "}\n"
                ),
            },
            {
                "filename": "ui/pages/custom/ThemePreview.jsx",
                "content": (
                    "const theme = { accent: 'hsl(191, 100%, 58%)' };\n"
                    "export default function ThemePreview(){\n"
                    "  return <main className=\"bg-background text-foreground\">Preview</main>;\n"
                    "}\n"
                ),
            },
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    assert len([warning for warning in warnings if "page-local palette/theme object" in warning]) == 2
    assert len([warning for warning in warnings if "hardcodes color values" in warning]) == 2


def test_generated_ui_contract_blocks_font_files_outside_brand_fonts() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "public/fonts/Brand.woff2",
                "content": "<binary>",
            },
            {
                "filename": "brand/fonts/Brand.woff2",
                "content": "<binary>",
            },
        ],
        source_label="generated app assets",
        require_jsx=False,
    )

    assert warnings == [
        "public/fonts/Brand.woff2 places a local font outside brand/fonts; generated font binaries must live under app/brand/fonts and be referenced through /fonts/."
    ]


def test_generated_ui_contract_blocks_local_visual_primitive_clones() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/AuditLog.jsx",
                "content": (
                    "function StatusPill({ status }){ return <span>{status}</span>; }\n"
                    "function MetricTile({ value }){ return <div>{value}</div>; }\n"
                    "function StatCard({ value }){ return <div>{value}</div>; }\n"
                    "export default function AuditLog(){\n"
                    "  return <section>\n"
                    "    <button className=\"rounded-xl bg-primary px-4 py-2\">Save</button>\n"
                    "    <div className=\"rounded-2xl border border-border bg-card p-4\" />\n"
                    "    <div className=\"rounded-2xl border border-border bg-card p-4\" />\n"
                    "    <div className=\"rounded-2xl border border-border bg-card p-4\" />\n"
                    "  </section>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    assert any("defines local visual primitive clones" in warning for warning in warnings)
    assert any("raw primary-styled <button>" in warning for warning in warnings)
    assert any("local card/panel shells" in warning for warning in warnings)


def test_generated_ui_contract_allows_thin_wrappers_over_shared_primitives() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/NotificationsPage.jsx",
                "content": (
                    "import { Button, ResourceList, StatusPill, SurfaceCard } from '@mozaiks/chat-ui/ui';\n"
                    "function NotificationRow({ item }){\n"
                    "  return <SurfaceCard>\n"
                    "    <StatusPill tone={item.active ? 'success' : 'default'}>{item.active ? 'Active' : 'Muted'}</StatusPill>\n"
                    "    <Button variant=\"secondary\">Review</Button>\n"
                    "  </SurfaceCard>;\n"
                    "}\n"
                    "export default function NotificationsPage(){\n"
                    "  return <ResourceList items={[]} columns={[]} renderMobileItem={(item) => <NotificationRow item={item} />} />;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    assert warnings == []


def test_generated_ui_contract_allows_semantic_tokens_and_fonts() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/AccountInsights.jsx",
                "content": (
                    "import { Button, Panel, StatusPill, SurfaceCard } from '@mozaiks/chat-ui/ui';\n"
                    "export default function AccountInsights(){\n"
                    "  return <div className=\"bg-background text-foreground\">\n"
                    "    <Panel title=\"Insights\" className=\"bg-card text-muted-foreground font-sans\">\n"
                    "      <SurfaceCard className=\"bg-card text-foreground font-heading\">\n"
                    "        <StatusPill tone=\"primary\">Healthy</StatusPill>\n"
                    "        <Button variant=\"secondary\">View Details</Button>\n"
                    "      </SurfaceCard>\n"
                    "    </Panel>\n"
                    "  </div>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    assert warnings == []


def test_generated_ui_contract_ignores_docs_and_tests_fixtures() -> None:
    warnings = audit_generated_react_files(
        [
            {
                "filename": "docs/examples/BadExample.jsx",
                "content": (
                    "export default function BadExample(){\n"
                    "  return <button className=\"bg-primary\" style={{ color: '#ffffff', fontFamily: 'ExampleFont' }}>Run</button>;\n"
                    "}\n"
                ),
            },
            {
                "filename": "tests/fixtures/BadFixture.jsx",
                "content": (
                    "function StatusPill(){ return <span />; }\n"
                    "export default function BadFixture(){\n"
                    "  return <div className=\"rounded-2xl border border-border bg-card p-4\" />;\n"
                    "}\n"
                ),
            },
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    assert warnings == []


def test_admin_console_page_using_page_frame_is_flagged() -> None:
    """admin/pages/ files must use WorkspaceLayout, not PageFrame."""
    warnings = audit_generated_react_files(
        [
            {
                "filename": "admin/pages/WorkspaceHostingPage.jsx",
                "content": (
                    "import { PageFrame } from '@mozaiks/chat-ui';\n"
                    "export default function WorkspaceHostingPage() {\n"
                    "  return <PageFrame name='workspace-hosting' layout='full-width'>\n"
                    "    <div>Hosting</div>\n"
                    "  </PageFrame>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated admin console React",
        require_jsx=True,
    )

    assert any("workspace/app console page" in w and "WorkspaceLayout" in w for w in warnings), (
        f"Expected layout shell warning for admin/pages/ using PageFrame, got: {warnings}"
    )


def test_admin_console_page_using_workspace_layout_is_clean() -> None:
    """admin/pages/ files using WorkspaceLayout must not trigger the layout warning."""
    warnings = audit_generated_react_files(
        [
            {
                "filename": "admin/pages/WorkspaceHostingPage.jsx",
                "content": (
                    "import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace';\n"
                    "export default function WorkspaceHostingPage() {\n"
                    "  return <WorkspaceLayout>\n"
                    "    <div>Hosting</div>\n"
                    "  </WorkspaceLayout>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated admin console React",
        require_jsx=True,
    )

    assert not any("workspace/app console page" in w for w in warnings), (
        f"Unexpected layout shell warning for correct admin/pages/ usage: {warnings}"
    )


def test_custom_route_using_workspace_layout_is_flagged() -> None:
    """ui/pages/custom/ files must use PageFrame, not WorkspaceLayout."""
    warnings = audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/SettingsPage.jsx",
                "content": (
                    "import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace';\n"
                    "export default function SettingsPage() {\n"
                    "  return <WorkspaceLayout><div>Settings</div></WorkspaceLayout>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="generated custom route React",
        require_jsx=True,
    )

    assert any("user-facing custom route" in w and "PageFrame" in w for w in warnings), (
        f"Expected layout shell warning for ui/pages/custom/ using WorkspaceLayout, got: {warnings}"
    )


def test_generated_ui_contract_accepts_clean_page_schema() -> None:
    warnings = audit_page_schemas(
        [
            {
                "name": "Tickets",
                "route": "/tickets",
                "title": "Tickets",
                "page_type": "record_list",
                "extensions": None,
                "sections": [
                    {
                        "id": "tickets-header",
                        "primitive": "PageHeader",
                        "config": {
                            "title": "Tickets",
                            "subtitle": "Review support requests and route the next response.",
                        },
                    },
                    {
                        "id": "tickets-table",
                        "primitive": "ResourceTable",
                        "config": {
                            "columns": [
                                {"key": "subject", "label": "Subject"},
                                {"key": "status", "label": "Status", "type": "status"},
                            ],
                            "data": [],
                            "empty": {"title": "No tickets"},
                        },
                    },
                ],
            }
        ]
    )

    assert warnings == []


def test_generated_ui_contract_accepts_extensions_slots() -> None:
    warnings = audit_page_schemas(
        [
            {
                "name": "Projects",
                "route": "/projects",
                "title": "Projects",
                "page_type": "gallery",
                "extensions": [
                    {"slot": "empty_state", "component": "ProjectsEmptyState"},
                    {"slot": "hero", "component": "ProjectsHero"},
                ],
                "sections": [],
            }
        ]
    )
    assert warnings == []


def test_generated_ui_contract_blocks_missing_page_type() -> None:
    warnings = audit_page_schemas(
        [{"name": "Items", "route": "/items", "title": "Items", "sections": []}]
    )
    assert any("missing required 'page_type'" in warning for warning in warnings)


def test_generated_ui_contract_blocks_invalid_page_type() -> None:
    warnings = audit_page_schemas(
        [{"name": "Items", "route": "/items", "title": "Items", "page_type": "crud_list", "sections": []}]
    )
    assert any("invalid page_type 'crud_list'" in warning for warning in warnings)


def test_generated_ui_contract_blocks_invalid_extension_slot() -> None:
    warnings = audit_page_schemas(
        [
            {
                "name": "Items",
                "route": "/items",
                "title": "Items",
                "page_type": "record_list",
                "extensions": [{"slot": "footer", "component": "CustomFooter"}],
                "sections": [],
            }
        ]
    )
    assert any("invalid slot 'footer'" in warning for warning in warnings)


def test_generated_ui_contract_blocks_extension_missing_component() -> None:
    warnings = audit_page_schemas(
        [
            {
                "name": "Items",
                "route": "/items",
                "title": "Items",
                "page_type": "record_list",
                "extensions": [{"slot": "header", "component": ""}],
                "sections": [],
            }
        ]
    )
    assert any("missing required 'component'" in warning for warning in warnings)


def test_generated_ui_contract_blocks_noisy_page_schema() -> None:
    warnings = audit_page_schemas(
        [
            {
                "name": "Operations Dashboard",
                "route": "/operations",
                "title": "Operations Dashboard",
                "page_type": "analytics_dashboard",
                "extensions": None,
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
                    {
                        "id": "card",
                        "primitive": "Panel",
                        "config": {
                            "title": "Placeholder posture",
                            "children": [
                                {
                                    "id": "nested",
                                    "primitive": "SurfaceCard",
                                    "config": {"title": "Nested"},
                                }
                            ],
                        },
                    },
                    {
                        "id": "legacy",
                        "primitive": "Card",
                        "config": {"title": "Old card"},
                    },
                ],
            }
        ]
    )

    assert any("dashboard-style page naming" in warning for warning in warnings)
    assert any("placeholder/internal copy" in warning for warning in warnings)
    assert any("uses removed primitive 'Card'" in warning for warning in warnings)
    assert any("nests SurfaceCard inside Panel" in warning for warning in warnings)
    assert any("uses 2 SummaryStrip sections" in warning for warning in warnings)


@pytest.mark.parametrize("page_type", sorted(VALID_PAGE_TYPES))
def test_generated_ui_contract_accepts_minimal_schema_for_each_page_type(
    page_type: str,
) -> None:
    """Every value in VALID_PAGE_TYPES must pass audit_page_schemas with an empty
    sections list — confirming the quality gate accepts the type, not just that
    the enum is defined."""
    warnings = audit_page_schemas(
        [
            {
                "name": "Test",
                "route": "/test",
                "title": "Test",
                "page_type": page_type,
                "extensions": None,
                "sections": [],
            }
        ]
    )
    assert warnings == [], (
        f"page_type '{page_type}' produced unexpected quality gate warnings: {warnings}"
    )
