from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


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
    assert findings[0].severity == "error"
    assert findings[0].code == "render"
    assert findings[0].gate_id == "generated_ui_browser"
    assert "generated page renders" in findings[0].message
    assert "main heading" in findings[0].message
    assert "generated-mobile" in findings[0].suggested_fix


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
    assert findings[0].code == "runner"
    assert findings[0].message == "Error: Process from config.webServer was not able to start. Exit code: 1"
    assert "SHOULD_NOT_LEAK" not in str(findings)


def test_playwright_runner_returns_only_canonical_acceptance_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ui_acceptance.shutil, "which", lambda _name: "npx")
    monkeypatch.setattr(
        ui_acceptance.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout='{"suites": [], "errors": [{"message": "Browser failed"}]}',
            stderr="",
        ),
    )

    result = ui_acceptance.run_playwright_acceptance(
        app_root=tmp_path,
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert result["success"] is False
    assert result["validation_run"]["status"] == "failed"
    assert result["repair_decision"]["disposition"] == "repair"
    assert result["validation_run"]["gate_results"][0]["gate_id"] == "generated_ui_browser"
    assert "status" not in result
    assert "revision_count" not in result
    assert "revision_request" not in result

