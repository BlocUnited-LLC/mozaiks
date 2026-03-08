"""Diagnostic checks for first-time setup and local dev readiness."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mozaiksai.cli.generators.realm import generate_realm_dict
from mozaiksai.cli.generators.theme import render_template
from mozaiksai.cli.paths import find_project_root


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    fix: str | None = None


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _parse_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _check_http(url: str, timeout: float) -> tuple[bool, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            code = response.getcode()
            body = response.read(200).decode("utf-8", errors="ignore").strip()
            return 200 <= code < 300, f"HTTP {code} {body}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason)
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)


def _compare_realm(root: Path, app_json: dict[str, Any]) -> CheckResult:
    realm_path = root / "infra" / "keycloak" / "realm-export.json"
    if not realm_path.is_file():
        return CheckResult(
            "Generated realm file",
            "fail",
            f"Missing {realm_path.relative_to(root)}",
            "Run: python -m mozaiksai.cli generate --realm",
        )

    expected = generate_realm_dict(app_json)
    try:
        actual = _load_json(realm_path)
    except Exception as exc:
        return CheckResult("Generated realm file", "fail", str(exc))

    if actual == expected:
        return CheckResult("Generated realm file", "pass", "realm-export.json matches app/app.json")
    return CheckResult(
        "Generated realm file",
        "fail",
        "realm-export.json is out of sync with app/app.json",
        "Run: python -m mozaiksai.cli generate --realm",
    )


def _compare_theme(root: Path, app_json: dict[str, Any]) -> CheckResult:
    brand_path = root / "app" / "brand" / "public" / "brand.json"
    if not brand_path.is_file():
        return CheckResult("Generated Keycloak theme", "fail", "Missing app/brand/public/brand.json")

    theme_name = (
        app_json.get("auth", {})
        .get("keycloak", {})
        .get("themeName", "mozaiks")
    )
    theme_dir = root / "infra" / "keycloak" / "themes" / theme_name / "login"
    base_theme_dir = root / "infra" / "keycloak" / "themes" / "mozaiks" / "login"

    css_output = theme_dir / "resources" / "css" / "login.css"
    css_template = theme_dir / "resources" / "css" / "login.css.tmpl"
    if not css_template.is_file():
        fallback = base_theme_dir / "resources" / "css" / "login.css.tmpl"
        if fallback.is_file():
            css_template = fallback
        else:
            return CheckResult(
                "Generated Keycloak theme",
                "fail",
                f"Template not found for theme '{theme_name}'",
                "Create a template or set auth.keycloak.themeName to an existing theme.",
            )

    if not css_output.is_file():
        return CheckResult(
            "Generated Keycloak theme",
            "fail",
            f"Missing {css_output.relative_to(root)}",
            "Run: python -m mozaiksai.cli generate --theme",
        )

    try:
        brand = _load_json(brand_path)
        template = css_template.read_text(encoding="utf-8")
        expected = render_template(template, brand)
        current = css_output.read_text(encoding="utf-8")
    except Exception as exc:
        return CheckResult("Generated Keycloak theme", "fail", str(exc))

    if current == expected:
        return CheckResult("Generated Keycloak theme", "pass", f"{theme_name} theme is up-to-date")
    return CheckResult(
        "Generated Keycloak theme",
        "fail",
        f"{theme_name} theme CSS is out of date",
        "Run: python -m mozaiksai.cli generate --theme",
    )


def _format_result(result: CheckResult) -> str:
    prefix = {
        "pass": "[PASS]",
        "warn": "[WARN]",
        "fail": "[FAIL]",
    }.get(result.status, "[INFO]")

    line = f"{prefix} {result.name}: {result.detail}"
    if result.fix:
        line = f"{line}\n       Fix: {result.fix}"
    return line


def run(
    *,
    root: Path | None = None,
    strict: bool = False,
    timeout_seconds: float = 2.0,
    skip_network: bool = False,
) -> int:
    """Run setup diagnostics and print actionable remediation."""
    root = root or find_project_root()
    results: list[CheckResult] = []

    app_json_path = root / "app" / "app.json"
    env_path = root / ".env"
    brand_path = root / "app" / "brand" / "public" / "brand.json"

    if app_json_path.is_file():
        try:
            app_json = _load_json(app_json_path)
            results.append(CheckResult("app/app.json", "pass", "Found and parsed"))
        except Exception as exc:
            app_json = {}
            results.append(CheckResult("app/app.json", "fail", str(exc)))
    else:
        app_json = {}
        results.append(
            CheckResult("app/app.json", "fail", "Missing app/app.json", "Run: python -m mozaiksai.cli init")
        )

    if brand_path.is_file():
        results.append(CheckResult("brand.json", "pass", "Found app/brand/public/brand.json"))
    else:
        results.append(CheckResult("brand.json", "fail", "Missing app/brand/public/brand.json"))

    if env_path.is_file():
        env = _parse_env(env_path)
        results.append(CheckResult(".env", "pass", "Found .env"))
    else:
        env = {}
        results.append(CheckResult(".env", "fail", "Missing .env", "Copy .env.example to .env"))

    if env:
        key = env.get("OPENAI_API_KEY", "")
        if not key or key == "sk-...":
            results.append(
                CheckResult(
                    "OPENAI_API_KEY",
                    "fail",
                    "OPENAI_API_KEY is unset or placeholder",
                    "Set OPENAI_API_KEY in .env",
                )
            )
        else:
            results.append(CheckResult("OPENAI_API_KEY", "pass", "Configured"))

        auth_enabled = env.get("AUTH_ENABLED", "true").lower()
        if auth_enabled not in ("true", "false"):
            results.append(CheckResult("AUTH_ENABLED", "warn", f"Unexpected value '{auth_enabled}'"))
        elif auth_enabled == "true":
            results.append(CheckResult("AUTH_ENABLED", "pass", "Auth enabled (recommended)"))
        else:
            results.append(CheckResult("AUTH_ENABLED", "warn", "Auth disabled (temporary fallback mode)"))

    if app_json:
        for key in ("appName", "appId", "apiUrl", "wsUrl"):
            value = app_json.get(key)
            if isinstance(value, str) and value.strip():
                results.append(CheckResult(f"app.json:{key}", "pass", value.strip()))
            else:
                results.append(CheckResult(f"app.json:{key}", "fail", "Missing or empty"))

        results.append(_compare_realm(root, app_json))
        results.append(_compare_theme(root, app_json))

    if shutil.which("docker"):
        results.append(CheckResult("docker", "pass", "Docker executable found on PATH"))
    else:
        results.append(CheckResult("docker", "warn", "Docker executable not found on PATH"))

    if not skip_network:
        backend_ok, backend_detail = _check_http("http://localhost:8000/api/health", timeout_seconds)
        if backend_ok:
            results.append(CheckResult("backend health", "pass", backend_detail))
        else:
            results.append(
                CheckResult(
                    "backend health",
                    "warn",
                    backend_detail,
                    "If backend is not running yet, start it and re-run doctor.",
                )
            )

        keycloak_ok, keycloak_detail = _check_http("http://localhost:8080/health/ready", timeout_seconds)
        if keycloak_ok:
            results.append(CheckResult("keycloak health", "pass", keycloak_detail))
        else:
            results.append(
                CheckResult(
                    "keycloak health",
                    "warn",
                    keycloak_detail,
                    "Start keycloak with docker compose if AUTH_ENABLED=true.",
                )
            )

    pass_count = sum(r.status == "pass" for r in results)
    warn_count = sum(r.status == "warn" for r in results)
    fail_count = sum(r.status == "fail" for r in results)

    print("Mozaiks Doctor")
    print("==============")
    for result in results:
        print(_format_result(result))
    print("")
    print(f"Summary: {pass_count} pass, {warn_count} warn, {fail_count} fail")

    if fail_count > 0:
        return 1
    if strict and warn_count > 0:
        return 1
    return 0
