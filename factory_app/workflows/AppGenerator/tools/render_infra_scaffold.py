"""Render infra scaffold and auth adapter templates into code files.

InfraScaffoldAgent calls this tool to materialise provider-neutral deployment
and auth files from the build-context templates stored under:
  - factory_app/build_context/infra/           → Dockerfile, readiness.yml, deploy.yml, provision.sh
  - factory_app/build_context/webapp_builder/  → config/auth.yaml, ui/auth/authAdapter.js
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Locate the factory_app build_context root relative to this file.
_THIS_DIR = Path(__file__).resolve().parent
_FACTORY_APP_DIR = _THIS_DIR.parent.parent.parent  # factory_app/
_BUILD_CONTEXT_DIR = _FACTORY_APP_DIR / "build_context"

_INFRA_TEMPLATES_DIR = _BUILD_CONTEXT_DIR / "infra" / "templates"
_WEBAPP_BUILDER_TEMPLATES_DIR = _BUILD_CONTEXT_DIR / "webapp_builder" / "templates"

# Infra template files -> output path in the generated bundle.
# Workflow templates live under templates/workflows/ and are emitted into the
# GitHub workflow directory for app operators to customize.
_INFRA_TEMPLATES: list[tuple[Path, str]] = [
    (_INFRA_TEMPLATES_DIR / "Dockerfile", "Dockerfile"),
    (_INFRA_TEMPLATES_DIR / "workflows" / "readiness.yml", ".github/workflows/readiness.yml"),
    (_INFRA_TEMPLATES_DIR / "workflows" / "deploy.yml", ".github/workflows/deploy.yml"),
    (_INFRA_TEMPLATES_DIR / "scripts" / "provision.sh", "scripts/provision.sh"),
]

_AUTH_ADAPTER_TEMPLATE = _WEBAPP_BUILDER_TEMPLATES_DIR / "ui" / "auth" / "authAdapter.js"
_AUTH_ADAPTER_OUTPUT = "ui/auth/authAdapter.js"
_AUTH_CONFIG_TEMPLATE = _WEBAPP_BUILDER_TEMPLATES_DIR / "config" / "auth.yaml"
_AUTH_CONFIG_OUTPUT = "config/auth.yaml"


def _substitute(content: str, variables: dict[str, str]) -> str:
    """Replace {{KEY}} markers with values from variables dict."""
    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        return variables.get(key, m.group(0))  # leave unknown markers intact

    return re.sub(r"\{\{([A-Z_][A-Z0-9_]*)\}\}", _replace, content)


def _build_variables(context_variables: dict[str, Any]) -> dict[str, str]:
    app_slug = str(
        context_variables.get("app_slug")
        or context_variables.get("app_name")
        or "myapp"
    ).lower().replace(" ", "-")

    oidc_client_id = str(
        context_variables.get("oidc_client_id")
        or app_slug
    )
    auth_default_route = _safe_route(
        context_variables.get("default_route")
        or context_variables.get("app_default_route")
        or context_variables.get("landing_spot")
        or "/"
    )

    # Attempt to read the pinned mozaiks version from requirements.txt.
    mozaiks_version = _read_mozaiks_version()

    return {
        "APP_NAME": app_slug,
        "APP_SLUG": app_slug,
        "TOKEN_KEY_PREFIX": app_slug,
        "OIDC_CLIENT_ID": oidc_client_id,
        "AUTH_DEFAULT_ROUTE": auth_default_route,
        "MOZAIKS_VERSION": mozaiks_version,
        "REGISTRY": str(context_variables.get("container_registry") or "ghcr.io/your-org"),
        "DEPLOY_TARGET": str(context_variables.get("deploy_target") or "generic_container"),
    }


def _safe_route(value: Any) -> str:
    route = str(value or "/").strip()
    if not route.startswith("/") or route.startswith("//"):
        return "/"
    if any(char.isspace() for char in route):
        return "/"
    return route


def _read_mozaiks_version() -> str:
    """Return the pinned mozaiks version from requirements.txt, or '*' if not found."""
    # Walk up from build_context looking for requirements.txt
    candidate = _BUILD_CONTEXT_DIR.parent
    for _ in range(4):
        req = candidate / "requirements.txt"
        if req.exists():
            for line in req.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("mozaiks") and ("==" in line or ">=" in line):
                    return line.split("==")[-1].split(">=")[-1].strip()
        candidate = candidate.parent
    return "*"


def _read_template(path: Path) -> str | None:
    if not path.exists():
        logger.warning("Infra scaffold template not found: %s", path)
        return None
    return path.read_text(encoding="utf-8")


async def save_infra_scaffold(
    emit_infra: bool = True,
    emit_auth_adapter: bool = True,
    context_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render infra scaffold and/or auth adapter templates and return as code_files.

    Args:
        emit_infra: When True, render Dockerfile, readiness.yml, deploy.yml, provision.sh.
        emit_auth_adapter: When True, render config/auth.yaml and ui/auth/authAdapter.js.
        context_variables: Workflow session state with app_slug, oidc_client_id, etc.
    """
    cv = context_variables or {}
    variables = _build_variables(cv)
    code_files: list[dict[str, str]] = []
    skipped: list[str] = []

    if emit_infra:
        for template_path, output_path in _INFRA_TEMPLATES:
            raw = _read_template(template_path)
            if raw is None:
                skipped.append(output_path)
                continue
            rendered = _substitute(raw, variables)
            code_files.append({"filename": output_path, "content": rendered})

    if emit_auth_adapter:
        raw = _read_template(_AUTH_CONFIG_TEMPLATE)
        if raw is None:
            skipped.append(_AUTH_CONFIG_OUTPUT)
        else:
            rendered = _substitute(raw, variables)
            code_files.append({"filename": _AUTH_CONFIG_OUTPUT, "content": rendered})

        raw = _read_template(_AUTH_ADAPTER_TEMPLATE)
        if raw is None:
            skipped.append(_AUTH_ADAPTER_OUTPUT)
        else:
            rendered = _substitute(raw, variables)
            code_files.append({"filename": _AUTH_ADAPTER_OUTPUT, "content": rendered})

    return {
        "code_files": code_files,
        "skipped": skipped,
        "variables_used": variables,
    }
