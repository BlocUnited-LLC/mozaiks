from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def _complete_report(*, status="passed"):
    return {
        "suites": [{"specs": [{
            "title": "required page", "file": "generated-app.generic.acceptance.spec.js",
            "tests": [{"projectName": "generated-desktop", "expectedStatus": "passed",
                       "status": "expected" if status == "passed" else "skipped",
                       "results": [{"status": status}]}],
        }]}],
        "errors": [],
        "stats": {"expected": int(status == "passed"), "skipped": int(status == "skipped"),
                  "unexpected": 0, "flaky": 0},
    }


@pytest.mark.parametrize("stdout", [
    "", "{}", "not a report", '{"suites": [', "[]",
    json.dumps({"suites": [], "errors": [], "stats": {"expected": 0, "skipped": 0, "unexpected": 0, "flaky": 0}}),
    json.dumps(_complete_report(status="skipped")),
    json.dumps({**_complete_report(), "stats": {"expected": 2, "skipped": 0, "unexpected": 0, "flaky": 0}}),
    json.dumps({**_complete_report(), "suites": [{"specs": [{"tests": []}]}]}),
])
def test_runner_refuses_incomplete_or_skipped_report(monkeypatch, tmp_path, stdout):
    monkeypatch.setattr(ui_acceptance.shutil, "which", lambda _name: "npx")
    monkeypatch.setattr(ui_acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=stdout, stderr="",
    ))
    result = ui_acceptance.run_playwright_acceptance(app_root=tmp_path, repo_root=tmp_path)
    assert result["success"] is False
    assert result["validation_run"]["status"] == "failed"
    assert result["repair_decision"]["disposition"] != "accept"


def test_runner_accepts_complete_report_and_labels_mocked_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(ui_acceptance.shutil, "which", lambda _name: "npx")
    monkeypatch.setattr(ui_acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps(_complete_report()), stderr="",
    ))
    result = ui_acceptance.run_playwright_acceptance(app_root=tmp_path, repo_root=tmp_path)
    assert result["success"] is True
    assert result["evidence_mode"] == "mocked_rendering_smoke"
    assert result["coverage"] == {"executed": 1, "passed": 1, "skipped": 0, "failed": 0}


@pytest.fixture
def mixed_profile(tmp_path, monkeypatch):
    """Authored admission-shape fixture for gate tests; no generation claim."""
    app = tmp_path / "app"
    (app / "ui/pages/custom").mkdir(parents=True)
    (app / "app.json").write_text(json.dumps({"appId": "gate-test"}))
    (app / "ui/pages/tasks.yaml").write_text(
        "schema_version: mozaiks.app_page.v1\nname: tasks\nroute: /tasks\ntitle: Tasks\n"
        "page_type: record_list\nlayout: full-width\nsections:\n"
        "  - id: heading\n    primitive: PageHeader\n    config:\n      title: Tasks\n",
    )
    (app / "ui/route_manifest.json").write_text(json.dumps({"pages": [{"path": "/board", "component": "TaskBoard"}]}))
    (app / "ui/index.js").write_text(
        "import { registerComponent } from '@mozaiks/chat-ui/registry';\n"
        "import TaskBoard from './pages/custom/TaskBoard.jsx';\nregisterComponent('TaskBoard', TaskBoard);\n",
    )
    (app / "ui/pages/custom/TaskBoard.jsx").write_text("export default function TaskBoard() { return <main>Board</main>; }\n")
    monkeypatch.setattr(ui_acceptance, "_source_commit", lambda _: "a" * 40)
    value = {
        "candidate": {"app_id": "gate-test", "revision": "accepted-r1", "source_sha": "a" * 40,
                      "bundle_sha256": ui_acceptance.bundle_digest(app)},
        "routes": [
            {"path": "/tasks", "component": "SchemaPage", "schema": "tasks", "kind": "declarative", "heading": "Tasks"},
            {"path": "/board", "component": "TaskBoard", "kind": "custom", "heading": "Board"},
            {"path": "/me", "component": "ProfilePage", "kind": "extension", "excluded_reason": "Account regression is a separate profile."},
        ],
        "cases": [{"id": f"route:{route}", "route": route, "project": project}
                  for route in ("/tasks", "/board") for project in ("generated-desktop", "generated-mobile")],
    }
    return app, value


def _route_report(profile, run_id="fresh-run"):
    report = {"suites": [{"specs": []}], "errors": [],
              "stats": {"expected": len(profile["cases"]), "unexpected": 0, "skipped": 0, "flaky": 0}}
    for case in profile["cases"]:
        report["suites"][0]["specs"].append({"title": case["id"], "tests": [{
            "projectName": case["project"], "expectedStatus": "passed", "status": "expected", "results": [{"status": "passed"}],
            "annotations": [
                {"type": "mozaiks.case_id", "description": case["id"]},
                {"type": "mozaiks.route", "description": case["route"]},
                {"type": "mozaiks.run_id", "description": run_id},
                {"type": "mozaiks.candidate", "description": json.dumps(profile["candidate"])},
            ],
        }]})
    return report


def test_real_host_profile_accepts_complete_declared_mixed_routes(mixed_profile, tmp_path):
    app, value = mixed_profile
    profile = ui_acceptance.validate_real_host_profile(value, app_root=app, repo_root=tmp_path)
    coverage, findings = ui_acceptance._real_host_coverage(_route_report(value), profile, "fresh-run")
    assert findings == []
    assert coverage["required_cases"] == coverage["reported_cases"] == 4
    assert coverage["required_routes"] == coverage["reported_routes"] == ["/board", "/tasks"]
    assert coverage["excluded_routes"] == [{"path": "/me", "reason": "Account regression is a separate profile."}]


@pytest.mark.parametrize("change", ["missing_custom", "wrong_component", "wrong_schema", "excluded_custom",
                                        "missing_mobile", "unknown_route", "duplicate_case", "duplicate_route",
                                        "wrong_app", "wrong_source", "wrong_bundle", "unknown_field", "missing_heading"])
def test_profile_rejects_missing_route_registration_or_identity(mixed_profile, tmp_path, change):
    app, value = mixed_profile
    if change == "missing_custom":
        value["routes"].pop(1)
    elif change == "wrong_component":
        value["routes"][1]["component"] = "InventedBoard"
    elif change == "wrong_schema":
        value["routes"][0]["schema"] = "missing"
    elif change == "excluded_custom":
        value["routes"][1]["excluded_reason"] = "Do not test board"
        value["cases"] = value["cases"][:2]
    elif change == "missing_mobile":
        value["cases"].pop()
    elif change == "unknown_route":
        value["cases"].append({"id": "unknown", "route": "/not-declared", "project": "generated-desktop"})
    elif change == "duplicate_case":
        value["cases"].append(value["cases"][0])
    elif change == "duplicate_route":
        value["routes"].append(value["routes"][0])
    elif change == "unknown_field":
        value["ignore_failures"] = True
    elif change == "missing_heading":
        value["routes"][1].pop("heading")
    else:
        key = {"wrong_app": "app_id", "wrong_source": "source_sha", "wrong_bundle": "bundle_sha256"}[change]
        value["candidate"][key] = "b" * (40 if key == "source_sha" else 64)
    with pytest.raises(ValueError):
        ui_acceptance.validate_real_host_profile(value, app_root=app, repo_root=tmp_path)


@pytest.mark.parametrize("missing_file", ["ui/index.js", "ui/pages/custom/TaskBoard.jsx", "ui/pages/tasks.yaml"])
def test_profile_uses_existing_registration_and_schema_owners(mixed_profile, tmp_path, missing_file):
    app, value = mixed_profile
    (app / missing_file).unlink()
    value["candidate"]["bundle_sha256"] = ui_acceptance.bundle_digest(app)
    with pytest.raises(ValueError):
        ui_acceptance.validate_real_host_profile(value, app_root=app, repo_root=tmp_path)


@pytest.mark.parametrize("change", ["unvisited_custom", "wrong_candidate", "stale_run", "wrong_route", "duplicate_case", "missing_annotation"])
def test_real_host_report_rejects_omission_or_mismatched_evidence(mixed_profile, tmp_path, change):
    app, value = mixed_profile
    profile = ui_acceptance.validate_real_host_profile(value, app_root=app, repo_root=tmp_path)
    report = _route_report(value)
    specs = report["suites"][0]["specs"]
    if change == "unvisited_custom":
        del specs[2:]
        report["stats"]["expected"] = 2
    elif change == "duplicate_case":
        specs[3] = deepcopy(specs[2])
    else:
        annotations = specs[0]["tests"][0]["annotations"]
        if change == "wrong_candidate":
            annotations[-1]["description"] = json.dumps({**value["candidate"], "revision": "other"})
        elif change == "stale_run":
            annotations[2]["description"] = "old-run"
        elif change == "wrong_route":
            annotations[1]["description"] = "/other"
        else:
            annotations.pop(0)
    _, findings = ui_acceptance._real_host_coverage(report, profile, "fresh-run")
    assert findings


@pytest.mark.parametrize("change", ["none", "profile_changed", "bundle_changed", "source_changed", "missing_custom"])
def test_real_host_runner_binds_local_inputs_but_never_attests_external_host(
    mixed_profile, tmp_path, monkeypatch, change,
):
    app, value = mixed_profile
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(value))
    monkeypatch.setattr(ui_acceptance.shutil, "which", lambda _: "npx")

    def browser(command, **kwargs):
        env = kwargs["env"]
        assert env["MOZAIKS_GENERATED_UI_PROFILE_SHA256"] == ui_acceptance.hashlib.sha256(profile_path.read_bytes()).hexdigest()
        assert command[-1] == "--retries=0"
        report = _route_report(value, env["MOZAIKS_GENERATED_UI_RUN_ID"])
        if change == "profile_changed":
            altered = deepcopy(value)
            altered["routes"][1]["heading"] = "Old board"
            profile_path.write_text(json.dumps(altered))
        elif change == "bundle_changed":
            (app / "ui/pages/custom/TaskBoard.jsx").write_text("export default () => null;")
        elif change == "source_changed":
            monkeypatch.setattr(ui_acceptance, "_source_commit", lambda _: "b" * 40)
        elif change == "missing_custom":
            del report["suites"][0]["specs"][2:]
            report["stats"]["expected"] = 2
        return SimpleNamespace(returncode=0, stdout=json.dumps(report), stderr="")

    monkeypatch.setattr(ui_acceptance.subprocess, "run", browser)
    result = ui_acceptance.run_playwright_acceptance(
        app_root=app, repo_root=Path(__file__).resolve().parents[1], mode="real_host",
        profile_path=profile_path, base_url="http://127.0.0.1:4317",
    )
    assert result["runtime_identity"] == "not_evaluated"
    assert result["success"] is False
    assert result["validation_run"]["status"] == "failed"
    assert result["route_probe_passed"] is (change == "none")
    assert result["route_coverage"]["reported_cases"] == (2 if change == "missing_custom" else 4)


@pytest.mark.parametrize("change", ["status_object", "result_object", "project_object", "errors_object", "retry_failure"])
def test_malformed_nested_or_failed_attempt_report_cannot_accept(monkeypatch, tmp_path, change):
    report = _complete_report()
    test = report["suites"][0]["specs"][0]["tests"][0]
    if change == "status_object":
        test["status"] = {}
    elif change == "result_object":
        test["results"][0]["status"] = {}
    elif change == "project_object":
        test["projectName"] = {}
    elif change == "errors_object":
        test["results"][0]["errors"] = [{} , "bad error"]
    else:
        test["results"].insert(0, {"status": "failed"})
    monkeypatch.setattr(ui_acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps(report), stderr="",
    ))
    result = ui_acceptance.run_playwright_acceptance(app_root=tmp_path, repo_root=tmp_path)
    assert result["success"] is False
    assert result["validation_run"]["status"] == "failed"


def test_timeout_is_a_failure_with_no_clean_exit(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise ui_acceptance.subprocess.TimeoutExpired("playwright", 180)
    monkeypatch.setattr(ui_acceptance.subprocess, "run", timeout)
    result = ui_acceptance.run_playwright_acceptance(app_root=tmp_path, repo_root=tmp_path)
    assert result["success"] is False
    assert result["returncode"] is None


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

