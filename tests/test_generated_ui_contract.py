from __future__ import annotations

from factory_app.workflows.generated_ui_contract import (
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


def test_generated_ui_contract_accepts_clean_page_schema() -> None:
    warnings = audit_page_schemas(
        [
            {
                "name": "Tickets",
                "route": "/tickets",
                "title": "Tickets",
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


def test_generated_ui_contract_blocks_noisy_page_schema() -> None:
    warnings = audit_page_schemas(
        [
            {
                "name": "Operations Dashboard",
                "route": "/operations",
                "title": "Operations Dashboard",
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
