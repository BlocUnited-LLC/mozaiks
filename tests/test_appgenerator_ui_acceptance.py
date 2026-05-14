from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_acceptance_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "scripts" / "generated_ui_acceptance.py"
    spec = importlib.util.spec_from_file_location(
        "tests.generated_ui_acceptance_direct",
        file_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ui_acceptance = _load_acceptance_module()


def test_parse_playwright_json_report_returns_structured_findings() -> None:
    report = {
        "suites": [
            {
                "specs": [
                    {
                        "title": "generated page renders",
                        "file": "generated-app.generic.acceptance.spec.js",
                        "tests": [
                            {
                                "projectName": "generated-mobile",
                                "results": [
                                    {
                                        "status": "failed",
                                        "errors": [
                                            {
                                                "message": "Expected main heading to be visible",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }

    findings = ui_acceptance.parse_playwright_json_report(report)

    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert findings[0]["category"] == "render"
    assert "generated page renders" in findings[0]["message"]
    assert "main heading" in findings[0]["message"]
    assert "generated-mobile" in findings[0]["suggested_fix"]


def test_parse_playwright_json_report_normalizes_runner_errors() -> None:
    report = {
        "config": {"env": {"SHOULD_NOT_LEAK": "secret"}},
        "suites": [],
        "errors": [
            {
                "message": "Error: Process from config.webServer was not able to start. Exit code: 1\nstack details",
                "stack": "large stack",
            }
        ],
    }

    findings = ui_acceptance.parse_playwright_json_report(report)

    assert len(findings) == 1
    assert findings[0]["category"] == "runner"
    assert findings[0]["message"] == "Error: Process from config.webServer was not able to start. Exit code: 1"
    assert "SHOULD_NOT_LEAK" not in str(findings)


def test_review_ui_acceptance_passes_when_no_findings() -> None:
    result = ui_acceptance.review_ui_acceptance_findings([])

    assert result["status"] == "passed"
    assert result["revision_request"] is None


def test_review_ui_acceptance_routes_findings_to_revision() -> None:
    result = ui_acceptance.review_ui_acceptance_findings(
        [
            {
                "route": "/tickets",
                "category": "layout",
                "message": "Horizontal overflow on mobile.",
                "suggested_fix": "Reduce table width or use ResourceTable responsive behavior.",
            }
        ],
        prior_revision_count=0,
    )

    assert result["status"] == "needs_revision"
    assert result["revision_count"] == 1
    assert "Horizontal overflow" in result["revision_request"]
    assert "Reduce table width" in result["revision_request"]


def test_review_ui_acceptance_blocks_after_budget() -> None:
    result = ui_acceptance.review_ui_acceptance_findings(
        ["Page throws a browser error."],
        prior_revision_count=1,
        max_revision_attempts=1,
    )

    assert result["status"] == "blocked"
    assert "operator review" in result["revision_request"]


def test_review_ui_acceptance_can_require_runner_execution() -> None:
    result = ui_acceptance.review_ui_acceptance_findings(
        [],
        require_acceptance_run=True,
        acceptance_ran=False,
    )

    assert result["status"] == "needs_revision"
    assert "did not run" in result["revision_request"]
