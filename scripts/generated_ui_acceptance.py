"""Production-readiness helper for generated UI browser acceptance.

This script keeps Playwright outside the live AppGenerator agent loop. It can run
the generic generated-app browser smoke, parse Playwright JSON output, and emit
structured findings that a human or later promotion gate can inspect.
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
) -> dict[str, str]:
    out = {
        "severity": severity,
        "route": route or "",
        "category": category,
        "message": message,
        "source": source,
        "suggested_fix": suggested_fix
        or "Revise the generated page schema or bounded custom React for the affected route.",
    }
    return {key: value for key, value in out.items() if value}


def normalize_ui_acceptance_findings(value: Any) -> list[dict[str, str]]:
    """Normalize arbitrary finding payloads into structured dictionaries."""

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
        findings: list[dict[str, str]] = []
        for item in value:
            findings.extend(normalize_ui_acceptance_findings(item))
        return findings
    return [_finding(message=str(value))]


def parse_playwright_json_report(report: dict[str, Any]) -> list[dict[str, str]]:
    """Convert Playwright JSON reporter output into UI acceptance findings."""

    findings: list[dict[str, str]] = []

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


def review_ui_acceptance_findings(
    findings: Any,
    *,
    acceptance_ran: bool = True,
    require_acceptance_run: bool = False,
    prior_revision_count: int = 0,
    max_revision_attempts: int = 1,
) -> dict[str, Any]:
    """Return a bounded production-readiness status for browser findings."""

    normalized = normalize_ui_acceptance_findings(findings)
    if require_acceptance_run and not acceptance_ran:
        normalized.append(
            _finding(
                message="Browser UI acceptance did not run before final delivery.",
                category="acceptance",
                suggested_fix="Run the generated UI Playwright acceptance smoke before promotion.",
            )
        )

    if not normalized:
        status = "passed"
        revision_request = None
        revision_count = prior_revision_count
    elif prior_revision_count < max(0, max_revision_attempts):
        status = "needs_revision"
        revision_count = prior_revision_count + 1
        revision_request = _format_revision_request(normalized)
    else:
        status = "blocked"
        revision_count = prior_revision_count
        revision_request = (
            "Browser UI acceptance findings remain after the automated revision budget. "
            "User/operator review is required:\n- "
            + _format_findings(normalized)
        )

    return {
        "status": status,
        "findings": normalized,
        "revision_count": revision_count,
        "max_revision_attempts": max(0, max_revision_attempts),
        "revision_request": revision_request,
    }


def _format_findings(findings: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for finding in findings:
        route = f" route={finding['route']}" if finding.get("route") else ""
        suggestion = finding.get("suggested_fix") or "Revise the generated UI."
        lines.append(
            f"{finding.get('severity', 'error')} {finding.get('category', 'render')}{route}: "
            f"{finding.get('message', '')} Suggested fix: {suggestion}"
        )
    return "\n- ".join(lines)


def _format_revision_request(findings: list[dict[str, str]]) -> str:
    return (
        "Revise the generated UI based on browser acceptance findings. "
        "Do not add new decorative sections; fix the concrete rendered issue:\n- "
        + _format_findings(findings)
    )


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
    review = review_ui_acceptance_findings(findings, acceptance_ran=True)
    result = {
        "success": completed.returncode == 0 and review["status"] == "passed",
        "returncode": completed.returncode,
        "status": review["status"],
        "findings": review["findings"],
        "revision_request": review["revision_request"],
    }
    if not result["success"] and not review["findings"]:
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
    "review_ui_acceptance_findings",
    "run_playwright_acceptance",
]
