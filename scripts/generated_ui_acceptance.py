"""Production-readiness helper for generated UI browser acceptance.

This script runs the generic generated-app browser smoke through the canonical
validation registry and acceptance controller.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mozaiksai.core.validation import (
    AcceptanceController,
    ValidationGate,
    ValidationIssue,
    ValidationRegistry,
)

_GATE_ID = "generated_ui_browser"


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _finding(
    *,
    message: str,
    severity: str = "error",
    route: str | None = None,
    category: str = "render",
    source: str = "playwright",
    suggested_fix: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        gate_id=_GATE_ID,
        code=category,
        severity=severity,
        route=route,
        message=message,
        source=source,
        suggested_fix=suggested_fix
        or "Revise the generated page schema or bounded custom React for the affected route.",
        repair_owner="AppSchemaAgent",
    )


def normalize_ui_acceptance_findings(value: Any) -> list[ValidationIssue]:
    """Normalize arbitrary finding payloads into canonical validation issues."""

    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [_finding(message=text)] if text else []
    if isinstance(value, dict):
        message = _string(value.get("message") or value.get("error") or value.get("title"))
        if not message:
            return []
        return [
            _finding(
                message=message,
                severity=_string(value.get("severity")) or "error",
                route=_string(value.get("route")) or None,
                category=_string(value.get("category")) or "render",
                source=_string(value.get("source")) or "playwright",
                suggested_fix=_string(value.get("suggested_fix")) or None,
            )
        ]
    if isinstance(value, Iterable):
        findings: list[ValidationIssue] = []
        for item in value:
            findings.extend(normalize_ui_acceptance_findings(item))
        return findings
    return [_finding(message=str(value))]


def parse_playwright_json_report(report: dict[str, Any]) -> list[ValidationIssue]:
    """Convert Playwright JSON reporter output into UI acceptance findings."""

    findings: list[ValidationIssue] = []

    for error in report.get("errors") or []:
        if not isinstance(error, dict):
            continue
        message = _string(error.get("message") or error.get("stack"))
        if not message:
            continue
        findings.append(
            _finding(
                message=message.splitlines()[0],
                category="runner",
                source="playwright",
                suggested_fix="Fix the generated UI test environment or rendered app startup failure.",
            )
        )

    def walk_suite(suite: dict[str, Any]) -> None:
        for child in suite.get("suites") or []:
            if isinstance(child, dict):
                walk_suite(child)

        for spec in suite.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            title = _string(spec.get("title")) or "Playwright assertion failed"
            file_name = _string(spec.get("file"))
            for test in spec.get("tests") or []:
                if not isinstance(test, dict):
                    continue
                project = _string(test.get("projectName"))
                for result in test.get("results") or []:
                    if not isinstance(result, dict):
                        continue
                    status = _string(result.get("status"))
                    if status in {"passed", "skipped"}:
                        continue
                    errors = result.get("errors") or []
                    if not errors:
                        findings.append(
                            _finding(
                                message=f"{title} failed with status {status}.",
                                category="render",
                                source="playwright",
                                suggested_fix=f"Open {file_name} ({project}) and fix the rendered UI failure.",
                            )
                        )
                        continue
                    for error in errors:
                        if not isinstance(error, dict):
                            continue
                        message = _string(error.get("message") or error.get("stack"))
                        if not message:
                            continue
                        findings.append(
                            _finding(
                                message=f"{title}: {message}",
                                category="render",
                                source="playwright",
                                suggested_fix=f"Fix the generated UI so the browser assertion passes in {project or 'Playwright'}.",
                            )
                        )

    for suite in report.get("suites") or []:
        if isinstance(suite, dict):
            walk_suite(suite)

    return findings


def _extract_json_report(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _compact_runner_output(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return "Playwright acceptance failed."
    return "\n".join(lines[:12])[:1200]


def run_playwright_acceptance(
    *,
    app_root: Path,
    repo_root: Path,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    web_shell = repo_root / "web_shell"
    env = os.environ.copy()
    env["MOZAIKS_GENERATED_UI_APP_ROOT"] = str(app_root.resolve())
    npx = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    command = [
        npx,
        "playwright",
        "test",
        "-c",
        "playwright.generated-ui.config.js",
        "playwright/generated-ui/generated-app.generic.acceptance.spec.js",
        "--reporter=json",
    ]
    completed = subprocess.run(
        command,
        cwd=str(web_shell),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    report = _extract_json_report(completed.stdout)
    findings = parse_playwright_json_report(report)
    if completed.returncode != 0 and not findings:
        findings = [
            _finding(
                message=_compact_runner_output(completed.stderr or completed.stdout),
                category="runner",
                suggested_fix="Inspect Playwright runner output and fix the generated app or test environment.",
            )
        ]
    registry = ValidationRegistry()
    registry.register(
        ValidationGate(
            gate_id=_GATE_ID,
            description="Generated app browser rendering acceptance.",
            handler=lambda _context: findings,
        )
    )
    acceptance = AcceptanceController(registry).run(context={})
    result = {
        "success": completed.returncode == 0 and acceptance.accepted,
        "returncode": completed.returncode,
        **acceptance.model_dump(mode="json"),
    }
    if not result["success"] and not findings:
        result["runner_error"] = (completed.stderr or completed.stdout or "").strip()[-2000:]
    return result


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir() and (parent / "web_shell").is_dir():
            return parent
    return here.parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run generated UI browser acceptance.")
    parser.add_argument("--app-root", required=True, help="Generated app root containing app.json and ui/pages/*.yaml.")
    parser.add_argument("--output", default="", help="Optional JSON output path for structured findings.")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args(argv)

    result = run_playwright_acceptance(
        app_root=Path(args.app_root),
        repo_root=_repo_root(),
        timeout_seconds=args.timeout_seconds,
    )
    rendered = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "normalize_ui_acceptance_findings",
    "parse_playwright_json_report",
    "run_playwright_acceptance",
]
