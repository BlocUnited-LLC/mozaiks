"""Production-readiness helper for generated UI browser acceptance.

Browser evidence is interpreted through the canonical validation registry and
acceptance controller. Externally started real hosts remain identity-unverified.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit
from urllib.request import urlopen
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.validation import (
    AcceptanceController,
    ValidationGate,
    ValidationIssue,
    ValidationRegistry,
)

_GATE_ID = "generated_ui_browser"
_PROJECTS = ("generated-desktop", "generated-mobile")
_HARNESS_FAILURES = frozenset({"preflight", "identity", "report_integrity", "coverage", "runner"})


class _Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    app_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _Route(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(pattern=r"^/[^?#]*$")
    component: str = Field(min_length=1)
    schema_name: str | None = Field(default=None, alias="schema")
    kind: Literal["declarative", "custom", "auth", "workflow", "extension"]
    visit_path: str | None = None
    heading: str | None = None
    excluded_reason: str | None = None


class _Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    route: str
    project: Literal["generated-desktop", "generated-mobile"]


class _Profile(BaseModel):
    """Reviewed test inventory, not a generated-app or runtime contract."""
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate: _Candidate
    routes: list[_Route] = Field(min_length=1)
    cases: list[_Case] = Field(min_length=1)


def _app_files(app_root: Path) -> dict[str, bytes]:
    root = app_root.resolve(strict=True)
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {"__pycache__", ".git", ".venv", "node_modules", ".pytest_cache"} for part in relative.parts):
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError("App bundle contains a path outside its root.")
        if path.name == ".env" or path.name.startswith(".env."):
            raise ValueError("App bundle must not contain environment credential files.")
        if path.is_file():
            files[relative.as_posix()] = path.read_bytes()
    if "app.json" not in files:
        raise ValueError("App bundle has no app.json.")
    return files


def bundle_digest(app_root: Path) -> str:
    """Hash fixed admitted bytes, including assets, without executing them."""
    manifest = {name: hashlib.sha256(value).hexdigest() for name, value in _app_files(app_root).items()}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _source_commit(repo_root: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repo_root, text=True).strip()
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Real-host acceptance requires a frozen clean source checkout.")
    return git("rev-parse", "HEAD")


def _frontend_files(dist_path: Path) -> dict[str, str]:
    root = dist_path.resolve(strict=True)
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.resolve().is_relative_to(root):
            raise ValueError("Frontend build contains a path outside its root.")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "index.html" not in files:
        raise ValueError("Owned frontend build has no index.html.")
    return files


@dataclass(frozen=True)
class OwnedBrowserLaunch:
    """In-process binding from the existing local acceptance owner, never a CLI receipt.

    The trusted Python driver owns both Popen handles and their explicit environments.
    Browser annotations and serialized caller hashes cannot construct this binding.
    This proves a local controlled launch, not isolation from a hostile machine owner.
    """

    app_root: Path
    repo_root: Path
    candidate: _Candidate
    base_url: str
    api_url: str
    backend: Any
    frontend: Any
    dist_path: Path
    build_files: dict[str, str]

    @classmethod
    def from_driver(cls, *, app_root: Path, repo_root: Path, candidate: dict,
                    state: dict, backend: Any, runtime_env: dict, preview: dict):
        app_root, repo_root = app_root.resolve(strict=True), repo_root.resolve(strict=True)
        identity = _Candidate.model_validate(candidate)
        if (state.get("source_sha") != identity.source_sha
                or state.get("bundle_sha256") != identity.bundle_sha256
                or state.get("app_id") != identity.app_id):
            raise ValueError("Backend launch inputs differ from the admitted candidate.")
        frontend_env = preview["env"]
        dist_path = preview["dist_path"].resolve(strict=True)
        backend_args, frontend_args = backend.args, preview["process"].args
        launcher = repo_root / "examples/canonical-apps/community/scripts/live_environment.py"
        if (not isinstance(backend_args, list) or len(backend_args) < 3
                or Path(backend_args[1]).resolve() != launcher or backend_args[2] != "_host"
                or not isinstance(frontend_args, list) or "preview" not in frontend_args
                or "--strictPort" not in frontend_args or "--outDir" not in frontend_args
                or frontend_args.index("--outDir") + 1 >= len(frontend_args)
                or Path(frontend_args[frontend_args.index("--outDir") + 1]).resolve() != dist_path):
            raise ValueError("Browser acceptance requires the existing owner's backend and built preview processes.")
        for child_env in (runtime_env, frontend_env):
            if Path(child_env.get("PLATFORM_PATH", "")).resolve() != app_root:
                raise ValueError("Owned launch uses a different app root.")
        if (Path(runtime_env.get("PYTHONPATH", "")).resolve() != repo_root
                or runtime_env.get("PYTHON_DOTENV_DISABLED") != "1"
                or runtime_env.get("AUTH_ENABLED") != "true"
                or frontend_env.get("VITE_API_URL") != state["api_url"]
                or frontend_env.get("MOZAIKS_BACKEND_URL") != state["api_url"]
                or runtime_env.get("AUTH_ISSUER") != frontend_env.get("VITE_OIDC_AUTHORITY")):
            raise ValueError("Owned frontend/backend source or authentication binding differs.")
        for name, arguments, option in (("api_url", backend_args, "--api-port"),
                                         ("base_url", frontend_args, "--port")):
            url = urlsplit(state[name])
            if (url.scheme != "http" or url.hostname != "127.0.0.1"
                    or url.username or url.password or url.path or url.query or url.fragment
                    or option not in arguments or arguments.index(option) + 1 >= len(arguments)
                    or str(url.port) != arguments[arguments.index(option) + 1]):
                raise ValueError("Owned process origin differs from its launch arguments.")
        if preview["bundle_sha256"] != identity.bundle_sha256:
            raise ValueError("Frontend was built from different candidate bytes.")
        binding = cls(app_root, repo_root, identity, state["base_url"], state["api_url"],
                      backend, preview["process"], dist_path, _frontend_files(dist_path))
        binding.verify(app_root=app_root, repo_root=repo_root, candidate=identity,
                       base_url=state["base_url"])
        return binding

    def verify(self, *, app_root: Path, repo_root: Path, candidate: _Candidate,
               base_url: str) -> dict[str, Any]:
        if (app_root.resolve() != self.app_root or repo_root.resolve() != self.repo_root
                or candidate != self.candidate or base_url != self.base_url):
            raise ValueError("Running launch does not belong to this candidate/profile.")
        if self.backend.poll() is not None or self.frontend.poll() is not None:
            raise ValueError("Owned backend or frontend process is no longer running.")
        if (_source_commit(repo_root) != candidate.source_sha
                or bundle_digest(app_root) != candidate.bundle_sha256
                or _frontend_files(self.dist_path) != self.build_files):
            raise ValueError("Owned launch source, candidate or frontend build changed.")
        with urlopen(self.api_url + "/api/health/ready", timeout=10) as response:
            if response.status != 200:
                raise ValueError("Owned backend is not ready.")
        for relative, expected in self.build_files.items():
            with urlopen(self.base_url + "/" + quote(relative), timeout=10) as response:
                if hashlib.sha256(response.read()).hexdigest() != expected:
                    raise ValueError("Running frontend serves bytes different from its owned build.")
        return {"source_sha": candidate.source_sha, "bundle_sha256": candidate.bundle_sha256,
                "revision": candidate.revision, "backend_pid": self.backend.pid,
                "frontend_pid": self.frontend.pid, "base_url": self.base_url,
                "api_url": self.api_url, "frontend_sha256": hashlib.sha256(
                    json.dumps(self.build_files, sort_keys=True).encode()).hexdigest()}


def validate_real_host_profile(value: Any, *, app_root: Path, repo_root: Path) -> _Profile:
    """Close reviewed route expectations against the existing app contract owners."""
    from factory_app.workflows._shared.generated_ui_contract import audit_app_ui_bundle_integrity
    from mozaiksai.core.runtime.app.auth_contract import APP_AUTH_COMPONENTS
    from mozaiksai.core.runtime.app.page_schema import (
        PageSchemaValidationError,
        load_app_page_schemas,
    )

    profile = _Profile.model_validate(value)
    files = _app_files(app_root)
    app = json.loads(files["app.json"])
    if not isinstance(app, dict):
        raise ValueError("App manifest must be an object.")
    if profile.candidate.app_id != (app.get("appId") or app.get("app_id")):
        raise ValueError("Acceptance app identity differs from app.json.")
    if profile.candidate.source_sha != _source_commit(repo_root):
        raise ValueError("Acceptance source identity differs from the frozen checkout.")
    if profile.candidate.bundle_sha256 != bundle_digest(app_root):
        raise ValueError("Acceptance bundle identity differs from admitted app bytes.")
    code_files = [{"filename": name, "content": data.decode("utf-8")} for name, data in files.items()
                  if Path(name).suffix in {".json", ".yaml", ".yml", ".js", ".jsx"}]
    diagnostics = audit_app_ui_bundle_integrity(code_files, source_label="Browser acceptance candidate")
    if diagnostics:
        raise ValueError("; ".join(diagnostics))
    declared: dict[str, tuple[str, str | None, str]] = {}
    try:
        pages = load_app_page_schemas(app_root)
    except PageSchemaValidationError as exc:
        raise ValueError("Candidate has an invalid declarative page schema.") from exc
    for name, page in pages.items():
        declared[page.route] = ("SchemaPage", name, "declarative")
    manifest = json.loads(files.get("ui/route_manifest.json", b'{"pages": []}'))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("pages", []), list):
        raise ValueError("Route manifest must contain a pages list.")
    for page in manifest.get("pages", []):
        if not isinstance(page, dict) or not isinstance(page.get("path"), str) or not page.get("component"):
            raise ValueError("Route manifest has an invalid route declaration.")
        component = page["component"]
        entry = (component, page.get("schema"), "auth" if component in APP_AUTH_COMPONENTS else "custom")
        if page["path"] in declared and declared[page["path"]] != entry:
            raise ValueError("Route manifest conflicts with a declarative page.")
        declared[page["path"]] = entry
    routes = {route.path: route for route in profile.routes}
    if len(routes) != len(profile.routes):
        raise ValueError("Acceptance route inventory contains duplicate paths.")
    for path, (component, schema, kind) in declared.items():
        route = routes.get(path)
        if route is None or (route.component, route.schema_name, route.kind) != (component, schema, kind):
            raise ValueError(f"Missing or mismatched declared route: {path}.")
    cases = {(case.id, case.project): case for case in profile.cases}
    if len(cases) != len(profile.cases):
        raise ValueError("Acceptance case inventory contains duplicate IDs within a project.")
    for route in profile.routes:
        if route.kind in {"declarative", "custom"} and (route.path not in declared or route.excluded_reason is not None):
            raise ValueError("Application page routes must be declared and required.")
        if route.excluded_reason is not None:
            if not route.excluded_reason.strip() or any(case.route == route.path for case in profile.cases):
                raise ValueError("Excluded routes require a prior reason and cannot have required cases.")
        else:
            if not route.heading or not route.heading.strip():
                raise ValueError(f"Required route lacks a reviewed heading: {route.path}.")
            if any(not any(case.route == route.path and case.project == project for case in profile.cases) for project in _PROJECTS):
                raise ValueError(f"Required route lacks a case in each browser project: {route.path}.")
    if any(case.route not in routes for case in profile.cases):
        raise ValueError("Required case references an unknown route.")
    return profile


def _real_host_coverage(report: dict[str, Any], profile: _Profile, run_id: str) -> tuple[dict[str, Any], list[ValidationIssue]]:
    expected = {(case.id, case.project): case.route for case in profile.cases}
    seen: dict[tuple[str, str], str] = {}
    findings = []
    for _spec, test in _report_tests(report):
        annotations = test.get("annotations", [])
        if not isinstance(annotations, list):
            findings.append(_finding(message="Invalid acceptance annotations.", category="report_integrity"))
            annotations = []
        values: dict[str, str] = {}
        for annotation in annotations:
            if isinstance(annotation, dict) and isinstance(annotation.get("type"), str) and annotation["type"].startswith("mozaiks."):
                name = annotation["type"]
                if name in values:
                    findings.append(_finding(message="Duplicate acceptance annotation.", category="coverage"))
                description = annotation.get("description", "")
                if not isinstance(description, str):
                    findings.append(_finding(message="Invalid acceptance annotation value.", category="report_integrity"))
                    description = ""
                values[name] = description
        key = (values.get("mozaiks.case_id", ""), test.get("projectName", ""))
        route = values.get("mozaiks.route", "")
        try:
            identity_matches = json.loads(values.get("mozaiks.candidate", "")) == profile.candidate.model_dump()
        except (ValueError, TypeError):
            identity_matches = False
        if not identity_matches or values.get("mozaiks.run_id") != run_id:
            findings.append(_finding(message="Browser result has missing or mismatched candidate/run identity.", category="identity"))
        if key not in expected or route != expected[key] or key in seen:
            findings.append(_finding(message="Unexpected, mismatched or duplicate required browser case.", category="coverage"))
        else:
            seen[key] = route
    missing = sorted(set(expected) - set(seen))
    if missing:
        findings.append(_finding(message=f"Required browser cases never executed: {missing}.", category="coverage"))
    return {
        "required_cases": len(expected), "reported_cases": len(seen),
        "required_routes": sorted(set(expected.values())), "reported_routes": sorted(set(seen.values())),
        "missing_cases": [{"id": key[0], "project": key[1]} for key in missing],
        "excluded_routes": [{"path": route.path, "reason": route.excluded_reason}
                            for route in profile.routes if route.excluded_reason is not None],
    }, findings


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
        suggested_fix=suggested_fix or (
            "Correct the acceptance environment or evidence before testing the same candidate again."
            if category in _HARNESS_FAILURES
            else "Revise the generated page schema or bounded custom React for the affected route."
        ),
        repair_owner=None if category in _HARNESS_FAILURES else "AppSchemaAgent",
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
                if test.get("expectedStatus", "passed") != "passed":
                    # Unsupported required outcomes are coverage findings, not UI repairs.
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
        # A fragment of a truncated or mixed report is not acceptance evidence.
        return {}


def _report_tests(report: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Validate reporter structure before interpreting the absence of failures."""
    tests: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def walk(suites: Any) -> None:
        if not isinstance(suites, list):
            raise ValueError("Playwright suites must be a list.")
        for suite in suites:
            if not isinstance(suite, dict):
                raise ValueError("Playwright suite must be an object.")
            walk(suite.get("suites", []))
            specs = suite.get("specs", [])
            if not isinstance(specs, list):
                raise ValueError("Playwright specs must be a list.")
            for spec in specs:
                if not isinstance(spec, dict) or not isinstance(spec.get("tests"), list) or not spec["tests"]:
                    raise ValueError("Playwright spec has no test records.")
                for test in spec["tests"]:
                    if not isinstance(test, dict) or test.get("status") not in ("expected", "unexpected", "flaky", "skipped"):
                        raise ValueError("Playwright test outcome is missing or invalid.")
                    if test.get("projectName") not in _PROJECTS:
                        raise ValueError("Playwright project identity is missing or invalid.")
                    if test.get("expectedStatus") not in ("passed", "failed", "timedOut", "skipped", "interrupted"):
                        raise ValueError("Playwright expected test status is missing or invalid.")
                    results = test.get("results")
                    if not isinstance(results, list) or not results or any(
                        not isinstance(result, dict) or result.get("status") not in (
                            "passed", "failed", "timedOut", "skipped", "interrupted",
                        ) for result in results
                    ):
                        raise ValueError("Playwright test has missing or invalid execution results.")
                    if any(not isinstance(result.get("errors", []), list) or any(
                        not isinstance(error, dict) for error in result.get("errors", [])
                    ) for result in results):
                        raise ValueError("Playwright execution errors must be a list.")
                    tests.append((spec, test))

    walk(report.get("suites"))
    errors = report.get("errors")
    if not isinstance(errors, list) or any(not isinstance(error, dict) for error in errors):
        raise ValueError("Playwright runner error inventory is missing or invalid.")
    stats = report.get("stats")
    if not isinstance(stats, dict) or any(
        type(stats.get(key)) is not int or stats[key] < 0
        for key in ("expected", "unexpected", "flaky", "skipped")
    ):
        raise ValueError("Playwright execution totals are missing or invalid.")
    for status in ("expected", "unexpected", "flaky", "skipped"):
        if stats[status] != sum(test["status"] == status for _, test in tests):
            raise ValueError("Playwright execution totals disagree with case records.")
    return tests


def _report_coverage(report: dict[str, Any]) -> tuple[dict[str, int], list[ValidationIssue]]:
    coverage = {"executed": 0, "passed": 0, "skipped": 0, "failed": 0}
    render_failures = 0
    try:
        tests = _report_tests(report)
    except ValueError as exc:
        return coverage, [_finding(message=str(exc), category="report_integrity")]
    for _spec, test in tests:
        results = test["results"]
        if test["status"] == "skipped":
            coverage["skipped"] += 1
        else:
            coverage["executed"] += 1
            passed = test.get("expectedStatus") == "passed" and test["status"] == "expected" and all(
                result["status"] == "passed" and not result.get("errors") for result in results
            )
            coverage["passed" if passed else "failed"] += 1
            if not passed and test["expectedStatus"] == "passed":
                render_failures += 1
    findings = []
    if report["errors"]:
        findings.append(_finding(message="Playwright reported runner errors.", category="runner"))
    if not coverage["executed"]:
        findings.append(_finding(message="No required browser cases executed.", category="coverage"))
    if coverage["skipped"]:
        findings.append(_finding(message="Required browser cases were skipped.", category="coverage"))
    if any(test["expectedStatus"] != "passed" for _, test in tests):
        findings.append(_finding(message="Required browser cases must expect a passing outcome.", category="coverage"))
    if render_failures:
        findings.append(_finding(message="Browser cases did not pass without failed attempts.", category="render"))
    return coverage, findings


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
    mode: str = "mocked_rendering_smoke",
    profile_path: Path | None = None,
    base_url: str | None = None,
    storage_state: Path | None = None,
    spec_path: str | None = None,
    owned_launch: OwnedBrowserLaunch | None = None,
    evidence_dir: Path | None = None,
) -> dict[str, Any]:
    web_shell = repo_root / "web_shell"
    env = os.environ.copy()
    env["MOZAIKS_GENERATED_UI_APP_ROOT"] = str(app_root.resolve())
    env["MOZAIKS_GENERATED_UI_MODE"] = mode
    profile = None
    run_id = str(uuid4())
    metadata: dict[str, Any] = {"evidence_mode": mode, "run_id": run_id}
    spec = "playwright/generated-ui/generated-app.generic.acceptance.spec.js"
    try:
        if mode == "real_host":
            if profile_path is None or not base_url:
                raise ValueError("Real-host mode requires a reviewed profile and explicit loopback base URL.")
            url = urlsplit(base_url)
            if url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1"} or (
                url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}
            ):
                raise ValueError("Real-host acceptance requires a credential-free loopback HTTP origin.")
            profile_bytes = profile_path.read_bytes()
            profile = validate_real_host_profile(json.loads(profile_bytes), app_root=app_root, repo_root=repo_root)
            if owned_launch is not None:
                metadata["launch"] = owned_launch.verify(
                    app_root=app_root, repo_root=repo_root, candidate=profile.candidate, base_url=base_url,
                )
            spec = spec_path or "playwright/generated-ui/generated-app.real-host.acceptance.spec.js"
            spec_file = (web_shell / spec).resolve(strict=True)
            if not spec_file.is_relative_to((web_shell / "playwright/generated-ui").resolve()) or not spec.endswith(".spec.js"):
                raise ValueError("Acceptance spec must be a trusted generated-UI spec in this checkout.")
            env.update({
                "MOZAIKS_GENERATED_UI_PROFILE": str(profile_path.resolve()),
                "MOZAIKS_GENERATED_UI_PROFILE_SHA256": hashlib.sha256(profile_bytes).hexdigest(),
                "MOZAIKS_GENERATED_UI_RUN_ID": run_id,
                "MOZAIKS_GENERATED_UI_BASE_URL": base_url,
                "MOZAIKS_GENERATED_UI_SPEC": spec,
            })
            if storage_state is not None:
                env["MOZAIKS_GENERATED_UI_STORAGE_STATE"] = str(storage_state.resolve(strict=True))
            else:
                env.pop("MOZAIKS_GENERATED_UI_STORAGE_STATE", None)
            metadata.update(candidate=profile.candidate.model_dump(),
                            profile_sha256=hashlib.sha256(profile_bytes).hexdigest(), spec=spec)
        elif mode != "mocked_rendering_smoke" or any(value is not None for value in (profile_path, base_url, storage_state, spec_path, owned_launch)):
            raise ValueError("Unknown acceptance mode or real-host arguments supplied to mocked mode.")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        return _acceptance_result([_finding(message=str(exc), category="preflight")], returncode=None, **metadata)
    npx = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    command = [
        npx,
        "playwright",
        "test",
        "-c",
        "playwright.generated-ui.config.js",
        spec,
        "--reporter=json",
        "--retries=0",
    ]
    if evidence_dir is not None:
        evidence_dir = evidence_dir.resolve()
        evidence_dir.mkdir(parents=True, exist_ok=False)
        command.extend(["--output", str(evidence_dir / "artifacts")])
        metadata["evidence_dir"] = str(evidence_dir)
    try:
        completed = subprocess.run(command, cwd=str(web_shell), env=env, text=True,
                                   capture_output=True, timeout=timeout_seconds, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if evidence_dir is not None:
            # Timeout output can be bytes even when subprocess text mode is used.
            for name in ("stdout", "stderr"):
                output = getattr(exc, name, None) or ""
                (evidence_dir / f"playwright.{name}").write_bytes(
                    output if isinstance(output, bytes) else output.encode("utf-8"))
        return _acceptance_result([_finding(message=f"Browser runner did not complete: {type(exc).__name__}.",
                                            category="runner")], returncode=None, **metadata)
    if evidence_dir is not None:
        (evidence_dir / "playwright.stdout").write_text(completed.stdout, encoding="utf-8")
        (evidence_dir / "playwright.stderr").write_text(completed.stderr, encoding="utf-8")
    report = _extract_json_report(completed.stdout)
    coverage, coverage_findings = _report_coverage(report)
    findings = list(coverage_findings)
    if not any(item.code == "report_integrity" for item in findings):
        findings.extend(parse_playwright_json_report(report))
        if profile is not None:
            route_coverage, route_findings = _real_host_coverage(report, profile, run_id)
            metadata["route_coverage"] = route_coverage
            findings.extend(route_findings)
    if profile is not None:
        try:
            if profile_path.read_bytes() != profile_bytes:
                raise ValueError("Acceptance profile changed during execution.")
            validate_real_host_profile(json.loads(profile_bytes), app_root=app_root, repo_root=repo_root)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            findings.append(_finding(message=str(exc), category="identity"))
        metadata["route_probe_passed"] = completed.returncode == 0 and not findings
        metadata["runtime_identity"] = "not_evaluated"
        if owned_launch is None:
            findings.append(_finding(
                message="Running host/build identity is not bound to this candidate. External-host route probes cannot accept a candidate.",
                category="identity",
                suggested_fix="Integrate the existing task-owned host/build driver with admitted candidate provenance before acceptance.",
            ))
        else:
            try:
                metadata["launch"] = owned_launch.verify(
                    app_root=app_root, repo_root=repo_root, candidate=profile.candidate, base_url=base_url,
                )
                metadata["runtime_identity"] = "verified_owned_launch"
            except (OSError, ValueError, subprocess.CalledProcessError) as exc:
                findings.append(_finding(message=str(exc), category="identity"))
    if completed.returncode != 0 and not findings:
        findings = [
            _finding(
                message=_compact_runner_output(completed.stderr or completed.stdout),
                category="runner",
                suggested_fix="Inspect Playwright runner output and fix the generated app or test environment.",
            )
        ]
    return _acceptance_result(findings, returncode=completed.returncode, coverage=coverage, **metadata)


def _acceptance_result(findings: list[ValidationIssue], *, returncode: int | None, **metadata: Any) -> dict[str, Any]:
    registry = ValidationRegistry()
    registry.register(
        ValidationGate(
            gate_id=_GATE_ID,
            description="Generated app browser report and declared coverage acceptance.",
            handler=lambda _context: findings,
        )
    )
    # Missing proof and harness failures cannot be repaired by regenerating UI.
    max_attempts = 0 if any(issue.code in _HARNESS_FAILURES and issue.severity == "error" for issue in findings) else 1
    acceptance = AcceptanceController(registry).run(context={}, max_attempts=max_attempts)
    return {
        "success": returncode == 0 and acceptance.accepted,
        "returncode": returncode,
        **metadata,
        **acceptance.model_dump(mode="json"),
    }


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
    parser.add_argument("--mode", choices=("mocked_rendering_smoke", "real_host"), default="mocked_rendering_smoke")
    parser.add_argument("--profile", type=Path, help="Reviewed route/case inventory and candidate identity for real-host mode.")
    parser.add_argument("--base-url", help="Already-running local frontend origin, backed by the real app host.")
    parser.add_argument("--storage-state", type=Path, help="Legitimate Playwright-supported auth state (not sessionStorage); never copied into the receipt.")
    parser.add_argument("--spec", help="Trusted app-specific spec under web_shell/playwright/generated-ui; defaults to route probes.")
    args = parser.parse_args(argv)

    result = run_playwright_acceptance(
        app_root=Path(args.app_root),
        repo_root=_repo_root(),
        timeout_seconds=args.timeout_seconds,
        mode=args.mode, profile_path=args.profile, base_url=args.base_url,
        storage_state=args.storage_state, spec_path=args.spec,
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
    "validate_real_host_profile",
    "bundle_digest",
]
