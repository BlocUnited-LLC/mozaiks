"""
Deterministic before_chat collector for ExistingAppDiscovery.

This tool does not ask the user anything and does not make product decisions.
It only reads explicit discovery inputs already provided to the workflow and
preloads context from deterministic sources:

- local repo path (`repo_path`)
- GitHub repo identifier (`github_repo`, optional `github_ref`)
- backend base URL (`backend_base_url`)
- explicit OpenAPI URL (`openapi_url`)
- uploaded OpenAPI file path (`uploaded_openapi_path`)

The collector mutates context_variables in place because lifecycle tool return
values are not merged by the runtime.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import logging
import os
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage pattern detection signals
# ---------------------------------------------------------------------------
_STORAGE_PACKAGE_SIGNALS: dict[str, list[str]] = {
    "mongodb": ["mongoose", "motor", "pymongo", "mongodb", "@mongodb-js"],
    "sql": [
        "sqlalchemy", "prisma", "sequelize", "pg", "mysql2", "typeorm",
        "knex", "alembic", "psycopg2", "asyncpg", "tortoise-orm",
    ],
    "redis": ["ioredis", "redis", "aioredis", "valkey", "upstash-redis"],
}

_STORAGE_SOURCE_PATTERNS: dict[str, list[str]] = {
    "mongodb": ["MongoClient(", "mongoose.connect(", "motor.motor_asyncio", "AsyncIOMotorClient"],
    "sql": [
        "create_engine(", "DataSource({", "PrismaClient(", "new Sequelize(",
        "knex({", "TypeOrmModule",
    ],
    "file_store": [
        # CommonJS sync API
        "fs.readFileSync(", "fs.writeFileSync(", "JSON.parse(fs.",
        "readFileSync(", "writeFileSync(", "fs.appendFileSync(",
        ".json', 'r')", ".json', 'w')",
        # ESM async API (node:fs/promises — common in modern TypeScript backends)
        "from 'node:fs/promises'", 'from "node:fs/promises"',
        "from 'node:fs'", 'from "node:fs"',
        "readFile(", "writeFile(", "appendFile(",
    ],
    "redis": [
        "createClient(", "redis.connect(", "new Redis(", "aioredis.from_url(",
        "Redis.from_url(",
    ],
}

# ---------------------------------------------------------------------------
# Connector detection signals
# ---------------------------------------------------------------------------
_CONNECTOR_SIGNALS: list[dict[str, Any]] = [
    {
        "provider_id": "azure",
        "packages": ["@azure/", "azure-sdk", "azure-mgmt-", "azure-identity", "@azure/identity"],
        "imports": ["from azure.", "import azure.", "require('@azure/", 'require("@azure/'],
        "category": "cloud",
        "likely_secret_envs": ["AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID", "AZURE_KEY_VAULT_NAME"],
    },
    {
        "provider_id": "aws",
        "packages": ["aws-sdk", "@aws-sdk/", "boto3", "botocore", "aiobotocore"],
        "imports": ["import boto3", "from boto3", "require('aws-sdk')", 'require("aws-sdk")'],
        "category": "cloud",
        "likely_secret_envs": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
    },
    {
        "provider_id": "gcp",
        "packages": ["@google-cloud/", "google-cloud-", "google.cloud", "google-auth-library"],
        "imports": ["from google.cloud", "require('@google-cloud/"],
        "category": "cloud",
        "likely_secret_envs": ["GOOGLE_APPLICATION_CREDENTIALS", "GCP_PROJECT_ID"],
    },
    {
        "provider_id": "payment_provider",
        "packages": ["payment_provider"],
        "imports": ["import payment_provider", "from payment_provider", "require('payment_provider')", "new payment provider("],
        "category": "payments",
        "likely_secret_envs": ["PAYMENT_PROVIDER_SECRET_KEY", "PAYMENT_PROVIDER_PUBLISHABLE_KEY", "PAYMENT_PROVIDER_WEBHOOK_SECRET"],
    },
    {
        "provider_id": "sendgrid",
        "packages": ["@sendgrid/mail", "sendgrid", "sendgrid-python"],
        "imports": ["import sendgrid", "sgMail", "require('@sendgrid/"],
        "category": "email",
        "likely_secret_envs": ["SENDGRID_API_KEY"],
    },
    {
        "provider_id": "twilio",
        "packages": ["twilio"],
        "imports": ["import twilio", "from twilio", "require('twilio')", "new Twilio("],
        "category": "communications",
        "likely_secret_envs": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
    },
    {
        "provider_id": "cloudflare",
        "packages": ["cloudflare", "@cloudflare/"],
        "imports": [
            "Cloudflare(", "new Cloudflare", "require('cloudflare')", 'require("cloudflare")',
            "CloudflareConnector", "CF_API", "api.cloudflare.com",
        ],
        "category": "infrastructure",
        "likely_secret_envs": ["CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"],
    },
    {
        "provider_id": "godaddy",
        "packages": ["godaddy", "@godaddy/"],
        "imports": [
            "GoDaddyConnector", "GD_API", "api.godaddy.com",
            "require('godaddy')", 'require("godaddy")',
        ],
        "category": "infrastructure",
        "likely_secret_envs": ["GODADDY_API_KEY", "GODADDY_API_SECRET"],
    },
    {
        "provider_id": "opensrs",
        "packages": ["opensrs"],
        "imports": [
            "OpenSrsConnector", "opensrs.net", "rr-n1-tor.opensrs.net",
            "horizon.opensrs.net", "xcpItem", "XCP",
        ],
        "category": "infrastructure",
        "likely_secret_envs": ["OPENSRS_API_KEY", "OPENSRS_RESELLER_USERNAME", "OPENSRS_API_HOST"],
    },
    {
        "provider_id": "github",
        "packages": ["@octokit/", "octokit", "PyGithub", "pygithub"],
        "imports": ["Octokit(", "from github import", "import github"],
        "category": "developer",
        "likely_secret_envs": ["GITHUB_TOKEN", "GH_TOKEN"],
    },
    {
        "provider_id": "slack",
        "packages": ["@slack/web-api", "@slack/bolt", "slack-sdk", "slack_sdk"],
        "imports": ["WebClient(", "from slack_sdk", "Bolt("],
        "category": "communications",
        "likely_secret_envs": ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"],
    },
    {
        "provider_id": "openai",
        "packages": ["openai"],
        "imports": ["from openai", "import openai", "OpenAI(", "new OpenAI("],
        "category": "ai",
        "likely_secret_envs": ["OPENAI_API_KEY"],
    },
    {
        "provider_id": "anthropic",
        "packages": ["anthropic", "@anthropic-ai/"],
        "imports": ["import Anthropic", "from anthropic", "new Anthropic("],
        "category": "ai",
        "likely_secret_envs": ["ANTHROPIC_API_KEY"],
    },
    {
        "provider_id": "unknown_http",
        "packages": ["axios", "node-fetch", "got", "ky"],
        "imports": [],
        "category": "http_client",
        "likely_secret_envs": [],
    },
]

# ---------------------------------------------------------------------------
# Mozaiks vocabulary detection signals
# ---------------------------------------------------------------------------
_MOZAIKS_VOCAB_PATTERNS: list[str] = [
    "contractKind",
    "module-action",
    "workflow-preparation",
]

_MOZAIKS_STRUCTURE_GLOB_CHECKS: list[str] = [
    "**/module.yaml",
    "**/contracts/reactions.yaml",
]

_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "bin",
    "obj",
    "site",
    "__pycache__",
}
_OPENAPI_CANDIDATE_PATHS = [
    "/openapi.json",
    "/swagger/v1/swagger.json",
    "/swagger.json",
]
_THEME_CONFIG_RELATIVE_CANDIDATES = [
    "app/config/theme_config.json",
    "brand/theme_config.json",
    "config/theme_config.json",
    "theme_config.json",
]
_SHELL_CONFIG_RELATIVE_CANDIDATES = [
    "app/config/shell.json",
    "config/shell.json",
    "shell.json",
]
_THEME_SNAPSHOT_RELATIVE_CANDIDATES = [
    "tailwind.config.js",
    "tailwind.config.ts",
    "src/index.css",
    "src/App.css",
    "src/app.css",
    "src/styles/index.css",
    "src/styles.css",
]

def _ctx_store(context_variables: Any) -> Any:
    if context_variables is None:
        return {}
    if isinstance(context_variables, dict):
        return context_variables
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data
    return context_variables


def _ctx_get(context_variables: Any, key: str, default: Any = None) -> Any:
    store = _ctx_store(context_variables)
    if isinstance(store, dict):
        return store.get(key, default)
    getter = getattr(store, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            return default
    return default


def _ctx_set(context_variables: Any, key: str, value: Any) -> None:
    store = _ctx_store(context_variables)
    if isinstance(store, dict):
        store[key] = value
        return
    try:
        store[key] = value
    except Exception:
        setattr(store, key, value)


_APP_INTELLIGENCE_PROGRESS_SCHEMA = "mozaiks.app_intelligence.progress.v1"
_APP_INTELLIGENCE_STAGE_PROGRESS = {
    "queued": ("pending", 0),
    "resolving_sources": ("indexing", 10),
    "collecting_evidence": ("indexing", 25),
    "selecting_source_files": ("indexing", 40),
    "fetching_source_files": ("indexing", 45),
    "extracting_symbols": ("indexing", 58),
    "building_graph": ("indexing", 72),
    "building_snapshot": ("indexing", 86),
    "ready": ("ready", 100),
    "partial": ("partial", 100),
    "repo_access_required": ("unavailable", 100),
    "unavailable": ("unavailable", 100),
    "failed": ("failed", 100),
}


def _set_app_intelligence_progress(
    context_variables: Any,
    stage: str,
    *,
    message: str | None = None,
    percent: int | None = None,
    details: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    status, default_percent = _APP_INTELLIGENCE_STAGE_PROGRESS.get(stage, ("indexing", 0))
    if percent is None:
        resolved_percent = default_percent
    else:
        resolved_percent = max(0, min(100, int(percent)))
    payload = {
        "schema_version": _APP_INTELLIGENCE_PROGRESS_SCHEMA,
        "stage": stage,
        "status": status,
        "percent": resolved_percent,
        "message": message or _default_app_intelligence_progress_message(stage),
        "details": dict(details or {}),
        "warnings": list(warnings or []),
    }
    _ctx_set(context_variables, "app_intelligence_progress", payload)
    _ctx_set(context_variables, "app_intelligence_status", status)
    if status == "ready":
        _ctx_set(context_variables, "app_intelligence_ready", True)
    elif status in {"unavailable", "failed"}:
        _ctx_set(context_variables, "app_intelligence_ready", False)
    return payload


def _default_app_intelligence_progress_message(stage: str) -> str:
    return {
        "queued": "App Intelligence indexing is queued.",
        "resolving_sources": "Resolving repository and AppContext inputs.",
        "collecting_evidence": "Collecting deterministic intake evidence.",
        "selecting_source_files": "Selecting safe source files for indexing.",
        "fetching_source_files": "Downloading selected source files from GitHub.",
        "extracting_symbols": "Extracting symbols, imports, and source chunks.",
        "building_graph": "Building the AppContext graph.",
        "building_snapshot": "Building the App Intelligence snapshot.",
        "ready": "App Intelligence is ready for discovery agents.",
        "partial": "App Intelligence is partial; agents may need user confirmation.",
        "repo_access_required": "GitHub access is required before App Intelligence can index this repository.",
        "unavailable": "App Intelligence is unavailable for this run.",
        "failed": "App Intelligence indexing failed.",
    }.get(stage, "Updating App Intelligence status.")


def _app_intelligence_activity_status(progress: dict[str, Any]) -> str:
    status = str(progress.get("status") or "").strip().lower()
    stage = str(progress.get("stage") or "").strip().lower()
    if status in {"ready"} or stage in {"ready"}:
        return "complete"
    if status in {"failed", "unavailable"} or stage in {"failed", "unavailable"}:
        return "failed"
    if status in {"partial"} or stage in {"partial"}:
        return "complete"
    return "working"


def _app_intelligence_activity_message(progress: dict[str, Any]) -> str:
    status = str(progress.get("status") or "").strip().lower()
    stage = str(progress.get("stage") or "").strip().lower()
    percent = int(progress.get("percent") or 0)
    message = str(progress.get("message") or "").strip()
    if status == "ready" or stage == "ready":
        return "App context ready. Starting the discovery agent."
    if status == "partial" or stage == "partial":
        return "App context is partially ready. The discovery agent may ask follow-up questions."
    if stage == "repo_access_required":
        return message or "GitHub access is required before App Intelligence can index this repository."
    if status in {"failed", "unavailable"} or stage in {"failed", "unavailable"}:
        return message or "App context could not be fully indexed."
    return f"Obtaining app context... {message or 'Indexing repository evidence.'} ({percent}%)"


async def _emit_app_intelligence_activity(context_variables: Any) -> dict[str, Any]:
    progress = _coerce_mapping(_ctx_get(context_variables, "app_intelligence_progress"))
    chat_id = str(_ctx_get(context_variables, "chat_id") or "").strip()
    if not chat_id:
        return {"skipped": True, "reason": "missing_chat_id"}
    if not progress:
        return {"skipped": True, "reason": "missing_app_intelligence_progress"}

    activity_status = _app_intelligence_activity_status(progress)
    event: dict[str, Any] = {
        "kind": "activity",
        "activity_type": "app_intelligence_indexing",
        "agent": "App Intelligence",
        "agent_name": "App Intelligence",
        "status": activity_status,
        "message": _app_intelligence_activity_message(progress),
        "workflow_name": "ExistingAppDiscovery",
        "progress_percent": progress.get("percent"),
        "display_variant": "app_intelligence_progress",
        "component_type": "AppIntelligenceProgressCard",
        "activity_display_variant": "app_intelligence_progress",
        "activity_component_type": "AppIntelligenceProgressCard",
        "metadata": {
            "source": "existing_app_discovery_preload",
            "display_variant": "app_intelligence_progress",
            "component_type": "AppIntelligenceProgressCard",
            "activity_display_variant": "app_intelligence_progress",
            "activity_component_type": "AppIntelligenceProgressCard",
            "progress_stage": progress.get("stage"),
            "progress_status": progress.get("status"),
            "progress_details": progress.get("details") if isinstance(progress.get("details"), dict) else {},
            "progress_warnings": progress.get("warnings") if isinstance(progress.get("warnings"), list) else [],
            "progress": progress,
            "app_intelligence_progress": progress,
        },
    }
    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        await transport.send_event_to_ui(event, chat_id)
        return {"success": True, "status": activity_status, "stage": progress.get("stage")}
    except Exception as exc:
        logger.debug("[ExistingAppDiscovery] App Intelligence activity emission failed: %s", exc)
        return {"skipped": True, "reason": "activity_emit_failed", "error": str(exc)}


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _first_nonempty(*values: Any) -> Any | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _append_unique(items: list[str], value: str | None) -> None:
    if value and value not in items:
        items.append(value)


def _normalise_base_url(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    return value.rstrip("/")


def _first_existing_dir(*candidates: Path | None) -> Path | None:
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def _workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _default_workspace_app_inputs() -> dict[str, Any]:
    configured_repo = os.getenv("MOZAIKS_APP_WORKSPACE_PATH")
    configured_repo_path = Path(configured_repo).expanduser() if configured_repo else None
    workspace_app_repo = _first_existing_dir(
        configured_repo_path,
    )

    data: dict[str, Any] = {
        "repo_path": str(workspace_app_repo) if workspace_app_repo else None,
        "discovery_mode": "guided",
    }

    env_backend_base = os.getenv("MOZAIKS_WORKSPACE_APP_BACKEND_BASE_URL")
    env_openapi_url = os.getenv("MOZAIKS_WORKSPACE_APP_OPENAPI_URL")
    if env_backend_base:
        data["backend_base_url"] = env_backend_base
    if env_openapi_url:
        data["openapi_url"] = env_openapi_url

    return {key: value for key, value in data.items() if value}


def _resolve_host_app_source_inputs(host_app_source: str | None) -> dict[str, Any]:
    source = str(host_app_source or "").strip()
    if not source:
        return {}
    if source == "workspace_app":
        return _default_workspace_app_inputs()
    return {}


def _load_theme_capture_preloader():
    file_path = Path(__file__).resolve().parents[2] / "ThemeCapture" / "tools" / "preload_theme_capture_context.py"
    spec = importlib.util.spec_from_file_location("mozaiks_theme_capture_preloader", file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load ThemeCapture preloader from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_theme_config_path(repo_path: str | None) -> str | None:
    if not repo_path:
        return None
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return None
    for relative in _THEME_CONFIG_RELATIVE_CANDIDATES:
        candidate = root / relative
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def _find_shell_config_path(repo_path: str | None) -> str | None:
    if not repo_path:
        return None
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return None
    for relative in _SHELL_CONFIG_RELATIVE_CANDIDATES:
        candidate = root / relative
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def _collect_theme_css_snapshot(repo_path: str | None, max_chars: int = 24000) -> str | None:
    if not repo_path:
        return None
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return None

    ordered_files: list[Path] = []
    seen: set[Path] = set()
    for relative in _THEME_SNAPSHOT_RELATIVE_CANDIDATES:
        candidate = root / relative
        if candidate.exists() and candidate.is_file():
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered_files.append(candidate)

    src_root = root / "src"
    if src_root.exists() and src_root.is_dir():
        for candidate in sorted(src_root.rglob("*.css"))[:6]:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered_files.append(candidate)

    if not ordered_files:
        return None

    chunks: list[str] = []
    total_chars = 0
    for path in ordered_files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not text.strip():
            continue
        relative = path.relative_to(root).as_posix()
        chunk = f"/* file: {relative} */\n{text.strip()}\n"
        if total_chars + len(chunk) > max_chars:
            remaining = max_chars - total_chars
            if remaining <= 0:
                break
            chunk = f"/* file: {relative} */\n{text.strip()[: max(0, remaining - len(relative) - 20)]}\n"
        chunks.append(chunk)
        total_chars += len(chunk)
        if total_chars >= max_chars:
            break

    return "\n".join(chunks) if chunks else None


def _summarize_theme_evidence(theme_evidence: dict[str, Any]) -> str | None:
    if not theme_evidence:
        return None
    appearance = theme_evidence.get("appearance")
    colors = [str(item) for item in (theme_evidence.get("colors") or [])[:4] if item]
    fonts = [str(item) for item in (theme_evidence.get("fonts") or [])[:4] if item]
    layout_hints = [str(item) for item in (theme_evidence.get("layout_hints") or [])[:4] if item]

    parts: list[str] = []
    if appearance:
        parts.append(f"{appearance} appearance")
    if colors:
        parts.append(f"colors {', '.join(colors)}")
    if fonts:
        parts.append(f"fonts {', '.join(fonts)}")
    if layout_hints:
        parts.append(f"layout hints {', '.join(layout_hints)}")

    if not parts:
        return None
    return "Host brand evidence suggests " + "; ".join(parts) + "."


def _iter_repo_files(repo_root: Path, limit: int = 5000) -> Iterable[Path]:
    count = 0
    for path in repo_root.rglob("*"):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        yield path
        count += 1
        if count >= limit:
            break


def _infer_stack_from_signals(languages: list[str], frameworks: list[str]) -> str:
    parts = []
    for item in languages + frameworks:
        if item not in parts:
            parts.append(item)
    return ", ".join(parts)


def _parse_package_json(raw_text: str, frameworks: list[str], languages: list[str]) -> None:
    try:
        data = json.loads(raw_text)
    except Exception:
        return
    deps = {}  # type: ignore[var-annotated]
    deps.update(data.get("dependencies", {}) or {})
    deps.update(data.get("devDependencies", {}) or {})
    if deps:
        _append_unique(languages, "Node.js")
    if "react" in deps:
        _append_unique(frameworks, "React")
    if "next" in deps:
        _append_unique(frameworks, "Next.js")
    if "vite" in deps:
        _append_unique(frameworks, "Vite")
    if "vue" in deps:
        _append_unique(frameworks, "Vue")


def _parse_pyproject(raw_bytes: bytes, frameworks: list[str], languages: list[str]) -> None:
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except Exception:
        return
    project = data.get("project", {}) if isinstance(data, dict) else {}
    deps = project.get("dependencies", []) or []
    if deps:
        _append_unique(languages, "Python")
    dep_blob = " ".join(str(dep).lower() for dep in deps)
    if "fastapi" in dep_blob:
        _append_unique(frameworks, "FastAPI")
    if "django" in dep_blob:
        _append_unique(frameworks, "Django")
    if "flask" in dep_blob:
        _append_unique(frameworks, "Flask")


def _parse_csproj(raw_text: str, frameworks: list[str], languages: list[str]) -> list[str]:
    target_frameworks: list[str] = []
    try:
        root = ET.fromstring(raw_text)
    except Exception:
        return target_frameworks
    _append_unique(languages, "C#")
    _append_unique(frameworks, ".NET")
    for node_name in ["TargetFramework", "TargetFrameworks"]:
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag != node_name:
                continue
            value = (elem.text or "").strip()
            if not value:
                continue
            for item in value.split(";"):
                item = item.strip()
                if item:
                    target_frameworks.append(item)
    return target_frameworks


def _summarise_file_tree(file_paths: Iterable[str]) -> dict[str, Any]:
    extension_counts: dict[str, int] = {}
    manifest_paths: list[str] = []
    csproj_paths: list[str] = []
    route_files: list[str] = []
    service_entrypoints: list[str] = []
    hub_files: list[str] = []
    total_files = 0

    for rel_path in file_paths:
        total_files += 1
        lower_path = rel_path.lower()
        suffix = Path(rel_path).suffix.lower()
        if suffix:
            extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
        name = Path(rel_path).name.lower()
        if name in {"package.json", "pyproject.toml", "requirements.txt", "dockerfile", "docker-compose.yml", "docker-compose.yaml"}:
            manifest_paths.append(rel_path)
        if lower_path.endswith(".csproj") or lower_path.endswith(".sln"):
            manifest_paths.append(rel_path)
        if lower_path.endswith(".csproj"):
            csproj_paths.append(rel_path)
        if name.endswith("routes.js") or name.endswith("routes.jsx") or name.endswith("routes.tsx") or name in {
            "app.js",
            "app.tsx",
            "router.js",
            "router.tsx",
        }:
            route_files.append(rel_path)
        if name in {"program.cs", "startup.cs"} or lower_path.endswith(".api.csproj"):
            service_entrypoints.append(rel_path)
        if name.endswith("hub.cs"):
            hub_files.append(rel_path)

    languages: list[str] = []
    frameworks: list[str] = []
    if extension_counts.get(".py"):
        _append_unique(languages, "Python")
    if extension_counts.get(".js") or extension_counts.get(".jsx") or extension_counts.get(".ts") or extension_counts.get(".tsx"):
        _append_unique(languages, "JavaScript/TypeScript")
    if extension_counts.get(".cs") or csproj_paths:
        _append_unique(languages, "C#")
        _append_unique(frameworks, ".NET")

    return {
        "total_files_scanned": total_files,
        "extension_counts": dict(sorted(extension_counts.items(), key=lambda item: item[1], reverse=True)[:12]),
        "manifest_paths": manifest_paths[:20],
        "csproj_paths": csproj_paths[:10],
        "route_files": route_files[:20],
        "service_entrypoints": service_entrypoints[:20],
        "hub_files": hub_files[:20],
        "languages": languages,
        "frameworks": frameworks,
    }


def _infer_service_surfaces(repo_summary: dict[str, Any], api_inventory: dict[str, Any], runtime_observations: dict[str, Any]) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for rel_path in repo_summary.get("service_entrypoints") or []:
        location = str(rel_path)
        name = Path(location).parent.name or Path(location).stem
        kind = "rest_api" if location.lower().endswith(".csproj") or location.lower().endswith("program.cs") else "service"
        key = (name, location)
        if key in seen:
            continue
        seen.add(key)
        surfaces.append(
            {
                "name": name,
                "kind": kind,
                "location": location,
                "description": f"Repo-discovered service entrypoint at {location}",
            }
        )

    for rel_path in repo_summary.get("hub_files") or []:
        location = str(rel_path)
        name = Path(location).stem
        key = (name, location)
        if key in seen:
            continue
        seen.add(key)
        surfaces.append(
            {
                "name": name,
                "kind": "signalr_hub",
                "location": location,
                "description": f"Realtime hub discovered in repo at {location}",
            }
        )

    spec_location = api_inventory.get("spec_location")
    if api_inventory.get("success") and spec_location:
        key = ("OpenAPI Surface", str(spec_location))
        if key not in seen:
            seen.add(key)
            surfaces.append(
                {
                    "name": api_inventory.get("title") or "OpenAPI Surface",
                    "kind": "rest_api",
                    "location": str(spec_location),
                    "description": f"Discovered OpenAPI surface with {api_inventory.get('path_count', 0)} paths",
                }
            )

    health_url = runtime_observations.get("health_url")
    if runtime_observations.get("success") and health_url:
        key = ("Reachable Backend", str(health_url))
        if key not in seen:
            seen.add(key)
            surfaces.append(
                {
                    "name": "Reachable Backend",
                    "kind": "runtime_probe",
                    "location": str(health_url),
                    "description": "Backend reachable from deterministic runtime probe",
                }
            )

    return surfaces


def _infer_route_surfaces(repo_summary: dict[str, Any]) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    seen: set[str] = set()

    for rel_path in repo_summary.get("route_files") or []:
        location = str(rel_path)
        if location in seen:
            continue
        seen.add(location)
        surfaces.append(
            {
                "path": location,
                "module": Path(location).parent.name or Path(location).stem,
                "description": f"Repo-discovered route or shell entry file at {location}",
            }
        )

    return surfaces


def _scan_local_repo(repo_path: str) -> dict[str, Any]:
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"success": False, "error": f"Repo path does not exist: {root}"}

    files = list(_iter_repo_files(root))
    relative_paths = [str(path.relative_to(root)).replace("\\", "/") for path in files]
    summary = _summarise_file_tree(relative_paths)
    languages = list(summary["languages"])
    frameworks = list(summary["frameworks"])
    target_frameworks: list[str] = []

    for rel_path in summary["manifest_paths"][:20]:
        full_path = root / rel_path
        try:
            if rel_path.endswith("package.json"):
                _parse_package_json(full_path.read_text(encoding="utf-8"), frameworks, languages)
            elif rel_path.endswith("pyproject.toml"):
                _parse_pyproject(full_path.read_bytes(), frameworks, languages)
            elif rel_path.endswith("requirements.txt"):
                _append_unique(languages, "Python")
            elif rel_path.lower().endswith(".csproj"):
                target_frameworks.extend(_parse_csproj(full_path.read_text(encoding="utf-8"), frameworks, languages))
        except Exception as exc:
            logger.debug("[ExistingAppDiscovery] Failed to parse manifest %s: %s", rel_path, exc)

    summary.update(
        {
            "success": True,
            "source": "local_repo",
            "repo_path": str(root),
            "repo_name": root.name,
            "languages": languages,
            "frameworks": frameworks,
            "target_frameworks": sorted(set(target_frameworks)),
            "inferred_tech_stack": _infer_stack_from_signals(languages, frameworks + target_frameworks),
        }
    )
    return summary


def _github_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _github_request(
    url: str,
    token: str | None,
    *,
    client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    headers = _github_headers(token)
    if client is not None:
        return await client.get(url, headers=headers)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0)) as client:
        return await client.get(url, headers=headers)


async def _fetch_github_file(
    owner: str,
    repo: str,
    path: str,
    ref: str,
    token: str | None,
    *,
    client: httpx.AsyncClient | None = None,
) -> bytes | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    resp = await _github_request(url, token, client=client)
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding")
        if encoding == "base64" and content:
            return base64.b64decode(content)
    except Exception:
        return None
    return None


def _normalize_github_repo_identifier(github_repo: str | None) -> str | None:
    raw = str(github_repo or "").strip()
    if not raw:
        return None
    normalized = raw
    if normalized.startswith("git@github.com:"):
        normalized = normalized.removeprefix("git@github.com:")
    elif normalized.startswith("https://github.com/"):
        normalized = normalized.removeprefix("https://github.com/")
    elif normalized.startswith("http://github.com/"):
        normalized = normalized.removeprefix("http://github.com/")
    elif normalized.startswith("github.com/"):
        normalized = normalized.removeprefix("github.com/")
    normalized = normalized.split("#", 1)[0].split("?", 1)[0].strip().strip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    parts = [part for part in normalized.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def _github_repo_access_recovery(
    *,
    normalized_repo: str,
    status_code: int,
    phase: str,
    auth_present: bool,
) -> dict[str, Any]:
    """Build a safe user/action payload for GitHub repo access failures."""
    if status_code == 401:
        code = "github_token_invalid"
        message = (
            "Mozaiks could not authenticate to GitHub for this repository. "
            "Connect GitHub again or provide a token with read access."
        )
    elif status_code == 403:
        code = "github_repo_permission_required"
        message = (
            "Mozaiks authenticated to GitHub but does not have permission to read this repository. "
            "Grant repo read access and retry discovery."
        )
    elif status_code == 404:
        code = "github_repo_access_required"
        message = (
            "Mozaiks could not access this GitHub repository. GitHub returns 404 for private repos "
            "when the connected account or token does not have read access."
        )
    else:
        code = "github_repo_unavailable"
        message = f"Mozaiks could not read this GitHub repository because GitHub returned HTTP {status_code}."

    return {
        "schema_version": "mozaiks.repo_access_recovery.v1",
        "provider": "github",
        "code": code,
        "github_repo": normalized_repo,
        "github_url": f"https://github.com/{normalized_repo}",
        "http_status": int(status_code),
        "phase": phase,
        "auth_present": bool(auth_present),
        "message": message,
        "recovery_actions": [
            {
                "id": "connect_github",
                "label": "Connect GitHub",
                "kind": "oauth_retry",
                "description": "Return to Create App, connect GitHub with repo read access, then start discovery again.",
            },
            {
                "id": "retry_import",
                "label": "Retry discovery",
                "kind": "retry",
                "description": "Retry after GitHub access is connected or a valid token is configured.",
            },
            {
                "id": "use_local_checkout",
                "label": "Use local checkout",
                "kind": "local_path",
                "description": "For local development, provide a readable checkout path so Mozaiks can index source directly.",
            },
        ],
    }


def _set_repo_access_recovery(context_variables: Any, recovery: dict[str, Any]) -> None:
    if not recovery:
        return
    _ctx_set(context_variables, "repo_access_status", "required")
    _ctx_set(context_variables, "repo_access_recovery", recovery)


async def _scan_github_repo(github_repo: str, github_ref: str | None, github_token: str | None = None) -> dict[str, Any]:
    normalized_repo = _normalize_github_repo_identifier(github_repo)
    if not normalized_repo:
        return {"success": False, "error": f"Invalid github_repo '{github_repo}'"}

    owner, repo = normalized_repo.split("/", 1)
    token = github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
    ) as client:
        repo_resp = await _github_request(f"https://api.github.com/repos/{owner}/{repo}", token, client=client)
        if repo_resp.status_code != 200:
            recovery = _github_repo_access_recovery(
                normalized_repo=normalized_repo,
                status_code=repo_resp.status_code,
                phase="repo_lookup",
                auth_present=bool(token),
            )
            return {
                "success": False,
                "source": "github_repo_scan",
                "github_repo": normalized_repo,
                "github_url": f"https://github.com/{normalized_repo}",
                "error": recovery["message"],
                "repo_access_recovery": recovery,
            }

        repo_info = repo_resp.json()
        ref = github_ref or repo_info.get("default_branch") or "main"
        tree_resp = await _github_request(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1",
            token,
            client=client,
        )
        if tree_resp.status_code != 200:
            recovery = _github_repo_access_recovery(
                normalized_repo=normalized_repo,
                status_code=tree_resp.status_code,
                phase="tree_lookup",
                auth_present=bool(token),
            )
            return {
                "success": False,
                "source": "github_repo_scan",
                "github_repo": normalized_repo,
                "github_url": f"https://github.com/{normalized_repo}",
                "error": recovery["message"],
                "repo_access_recovery": recovery,
            }

        tree = tree_resp.json().get("tree", []) or []
        file_paths = [item.get("path", "") for item in tree if item.get("type") == "blob"]
        summary = _summarise_file_tree(file_paths)
        languages = list(summary["languages"])
        frameworks = list(summary["frameworks"])
        target_frameworks: list[str] = []

        for rel_path in summary["manifest_paths"][:10]:
            raw_bytes = await _fetch_github_file(owner, repo, rel_path, ref, token, client=client)
            if not raw_bytes:
                continue
            try:
                if rel_path.endswith("package.json"):
                    _parse_package_json(raw_bytes.decode("utf-8"), frameworks, languages)
                elif rel_path.endswith("pyproject.toml"):
                    _parse_pyproject(raw_bytes, frameworks, languages)
                elif rel_path.endswith("requirements.txt"):
                    _append_unique(languages, "Python")
                elif rel_path.lower().endswith(".csproj"):
                    target_frameworks.extend(_parse_csproj(raw_bytes.decode("utf-8"), frameworks, languages))
            except Exception as exc:
                logger.debug("[ExistingAppDiscovery] Failed to parse GitHub manifest %s: %s", rel_path, exc)

    summary.update(
        {
            "success": True,
            "source": "github_repo_scan",
            "github_repo": normalized_repo,
            "github_repo_input": github_repo,
            "github_url": f"https://github.com/{normalized_repo}",
            "github_ref": ref,
            "repo_name": repo_info.get("name") or repo,
            "default_branch": repo_info.get("default_branch"),
            "languages": languages,
            "frameworks": frameworks,
            "target_frameworks": sorted(set(target_frameworks)),
            "inferred_tech_stack": _infer_stack_from_signals(languages, frameworks + target_frameworks),
        }
    )
    return summary


async def _scan_repo_source(local_repo_path: str | None, github_repo: str | None, github_ref: str | None, github_token: str | None = None) -> dict[str, Any]:
    if local_repo_path:
        return _scan_local_repo(str(local_repo_path))
    if github_repo:
        return await _scan_github_repo(str(github_repo), str(github_ref) if github_ref else None, github_token=github_token)
    return {}


def _context_graph_roots(
    *,
    repo_path: Any,
    frontend_repo_path: Any,
    backend_repo_path: Any,
) -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    explicit_roots = [
        ("frontend", frontend_repo_path),
        ("backend", backend_repo_path),
    ]
    for label, raw_path in explicit_roots:
        if not raw_path:
            continue
        root = Path(str(raw_path)).expanduser().resolve()
        if root.exists() and root.is_dir() and root not in seen:
            roots.append((label, root))
            seen.add(root)

    if not roots and repo_path:
        root = Path(str(repo_path)).expanduser().resolve()
        if root.exists() and root.is_dir():
            roots.append(("", root))

    return roots


def _context_graph_github_sources(
    *,
    github_repo: Any,
    frontend_github_repo: Any,
    backend_github_repo: Any,
) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()

    for label, raw_repo in (
        ("frontend", frontend_github_repo),
        ("backend", backend_github_repo),
    ):
        normalized = _normalize_github_repo_identifier(str(raw_repo)) if raw_repo else None
        if normalized and normalized not in seen:
            sources.append((label, normalized))
            seen.add(normalized)

    if not sources and github_repo:
        normalized = _normalize_github_repo_identifier(str(github_repo))
        if normalized:
            sources.append(("", normalized))

    return sources


def _collect_context_graph_file_map(
    roots: list[tuple[str, Path]],
    *,
    scan_policy_inputs: dict[str, Any] | None = None,
) -> Any:
    from mozaiksai.core.app_context.scan_policy import (
        collect_source_scan_file_map,
        default_context_graph_scan_policy,
    )

    return collect_source_scan_file_map(
        roots,
        policy=default_context_graph_scan_policy(scan_policy_inputs),
    )


async def _collect_github_context_graph_file_map(
    repos: list[tuple[str, str | None]],
    *,
    github_ref: str | None,
    github_token: str | None,
    scan_policy_inputs: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
) -> Any:
    from mozaiksai.core.app_context.scan_policy import (
        SourceScanResult,
        default_context_graph_scan_policy,
        priority_for_source_path,
        safe_scan_relpath,
        skip_reason_for_path,
    )

    policy = default_context_graph_scan_policy(scan_policy_inputs)
    token = github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    candidates: list[tuple[int, int, str, str, str, str, str, str]] = []
    warnings: list[str] = []
    access_issues: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    roots_summary: list[dict[str, Any]] = []

    for label, raw_repo in repos:
        normalized_repo = _normalize_github_repo_identifier(raw_repo)
        if not normalized_repo:
            if raw_repo:
                skipped["invalid_github_repo"] += 1
            continue
        owner, repo = normalized_repo.split("/", 1)
        repo_resp = await _github_request(f"https://api.github.com/repos/{owner}/{repo}", token)
        if repo_resp.status_code != 200:
            warnings.append(f"github_repo_lookup_failed:{normalized_repo}:{repo_resp.status_code}")
            access_issues.append(
                _github_repo_access_recovery(
                    normalized_repo=normalized_repo,
                    status_code=repo_resp.status_code,
                    phase="repo_lookup",
                    auth_present=bool(token),
                )
            )
            skipped["github_repo_lookup_failed"] += 1
            continue
        repo_info = repo_resp.json()
        ref = str(github_ref or repo_info.get("default_branch") or "main")
        tree_resp = await _github_request(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1",
            token,
        )
        if tree_resp.status_code != 200:
            warnings.append(f"github_tree_lookup_failed:{normalized_repo}:{tree_resp.status_code}")
            access_issues.append(
                _github_repo_access_recovery(
                    normalized_repo=normalized_repo,
                    status_code=tree_resp.status_code,
                    phase="tree_lookup",
                    auth_present=bool(token),
                )
            )
            skipped["github_tree_lookup_failed"] += 1
            continue
        roots_summary.append(
            {
                "label": str(label or ""),
                "repo": normalized_repo,
                "github_url": f"https://github.com/{normalized_repo}",
                "ref": ref,
                "default_branch": repo_info.get("default_branch"),
            }
        )
        for item in tree_resp.json().get("tree", []) or []:
            if item.get("type") != "blob":
                continue
            safe_rel = safe_scan_relpath(item.get("path"))
            if safe_rel is None:
                skipped["unsafe_path"] += 1
                continue
            reason = skip_reason_for_path(safe_rel, policy=policy)
            if reason:
                skipped[reason] += 1
                continue
            size = int(item.get("size") or 0)
            if size > policy.max_file_bytes:
                skipped["large_file"] += 1
                continue
            graph_path = f"{label}/{safe_rel}" if label else safe_rel
            priority, priority_label = priority_for_source_path(safe_rel, policy=policy)
            candidates.append(
                (
                    priority,
                    _path_depth_for_sort(graph_path),
                    graph_path,
                    priority_label,
                    owner,
                    repo,
                    safe_rel,
                    ref,
                )
            )

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    file_map: dict[str, str] = {}
    selected_by_priority: Counter[str] = Counter()
    selected_by_extension: Counter[str] = Counter()
    total_chars = 0
    limit_reached = len(candidates) > policy.max_files
    fetch_candidates = candidates[: policy.max_files]
    if limit_reached:
        warnings.append(f"context_graph_file_limit_reached:{policy.max_files}")

    semaphore = asyncio.Semaphore(12)

    if progress_callback and fetch_candidates:
        await progress_callback(0, len(fetch_candidates))

    async def _fetch_candidate(
        index: int,
        candidate: tuple[int, int, str, str, str, str, str, str],
        client: httpx.AsyncClient,
    ):
        _priority, _depth, graph_path, priority_label, owner, repo, github_path, ref = candidate
        try:
            async with semaphore:
                raw_bytes = await _fetch_github_file(owner, repo, github_path, ref, token, client=client)
            return index, graph_path, priority_label, raw_bytes, None
        except Exception as exc:
            return index, graph_path, priority_label, None, f"{type(exc).__name__}:{graph_path}"

    fetched_candidates: list[tuple[int, str, str, bytes | None, str | None] | None] = [None] * len(fetch_candidates)
    if fetch_candidates:
        progress_interval = max(1, len(fetch_candidates) // 12)
        completed_count = 0
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            limits=httpx.Limits(max_connections=24, max_keepalive_connections=24),
        ) as client:
            tasks = [
                asyncio.create_task(_fetch_candidate(index, candidate, client))
                for index, candidate in enumerate(fetch_candidates)
            ]
            for task in asyncio.as_completed(tasks):
                result = await task
                fetched_candidates[result[0]] = result
                completed_count += 1
                if progress_callback and (
                    completed_count == len(fetch_candidates)
                    or completed_count % progress_interval == 0
                ):
                    await progress_callback(completed_count, len(fetch_candidates))

    fetch_error_warnings = 0
    for fetched_candidate in fetched_candidates:
        if fetched_candidate is None:
            continue
        _index, graph_path, priority_label, raw_bytes, fetch_error = fetched_candidate
        if len(file_map) >= policy.max_files:
            limit_reached = True
            break
        if fetch_error:
            skipped["github_file_fetch_failed"] += 1
            if fetch_error_warnings < 8:
                warnings.append(f"context_graph_github_file_fetch_failed:{fetch_error}")
                fetch_error_warnings += 1
            continue
        if not raw_bytes:
            skipped["github_file_fetch_failed"] += 1
            continue
        if len(raw_bytes) > policy.max_file_bytes:
            skipped["large_file"] += 1
            continue
        text = raw_bytes.decode("utf-8", errors="ignore")
        if not text.strip():
            skipped["empty_file"] += 1
            continue
        if total_chars + len(text) > policy.max_total_chars:
            warnings.append(f"context_graph_char_limit_reached:{policy.max_total_chars}")
            limit_reached = True
            break
        file_map[graph_path] = text
        total_chars += len(text)
        selected_by_priority[priority_label] += 1
        selected_by_extension[PurePosixPath(graph_path).suffix.lower() or "<none>"] += 1

    if skipped.get("large_file"):
        warnings.append(f"context_graph_large_files_skipped:{skipped['large_file']}")
    if skipped.get("sensitive_path"):
        warnings.append(f"context_graph_sensitive_files_skipped:{skipped['sensitive_path']}")

    health = {
        "policy_id": policy.policy_id,
        "source": "github_source_scan",
        "roots": roots_summary,
        "candidate_file_count": len(candidates),
        "fetch_candidate_count": len(fetch_candidates),
        "selected_file_count": len(file_map),
        "total_chars": total_chars,
        "limit_reached": limit_reached,
        "limits": {
            "max_files": policy.max_files,
            "max_file_bytes": policy.max_file_bytes,
            "max_total_chars": policy.max_total_chars,
        },
        "selected_by_priority": dict(sorted(selected_by_priority.items())),
        "selected_by_extension": dict(sorted(selected_by_extension.items())),
        "skipped": dict(sorted(skipped.items())),
        "access_issues": access_issues,
        "warnings": list(warnings),
    }
    return SourceScanResult(file_map=file_map, health=health, warnings=warnings)


def _path_depth_for_sort(path: str) -> int:
    safe = str(path or "").replace("\\", "/").strip("/")
    return len([part for part in safe.split("/") if part]) if safe else 999


def _context_graph_scan_policy_inputs(context_variables: Any, discovery_inputs: dict[str, Any]) -> dict[str, Any]:
    raw = _first_nonempty(
        _ctx_get(context_variables, "context_graph_scan_policy"),
        discovery_inputs.get("context_graph_scan_policy"),
    )
    return _coerce_mapping(raw)


def _context_graph_request_text(context_variables: Any, discovery_inputs: dict[str, Any]) -> str:
    context_refresh_request = _coerce_mapping(_ctx_get(context_variables, "context_refresh_request", {}))
    candidates = [
        discovery_inputs.get("raw_user_request"),
        discovery_inputs.get("description"),
        context_refresh_request.get("raw_user_request"),
        context_refresh_request.get("reason"),
        _ctx_get(context_variables, "refresh_reason"),
        _ctx_get(context_variables, "app_description"),
        _ctx_get(context_variables, "app_name"),
    ]
    parts = [str(value).strip() for value in candidates if value and str(value).strip()]
    return "\n".join(parts[:4])


def _scan_file_map_checksum(file_map: dict[str, str]) -> str:
    payload = json.dumps(
        {path: file_map[path] for path in sorted(file_map)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _stable_context_token(*parts: Any) -> str:
    raw = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _build_preload_source_ref(
    *,
    app_id: str,
    roots: list[tuple[str, Path]],
    github_sources: list[tuple[str, str]] | None,
    github_ref: str | None,
    file_map: dict[str, str],
    scan_health: dict[str, Any],
    indexed_at: datetime,
):
    from mozaiksai.core.app_context import SourceRef, SourceRefKind

    github = list(github_sources or [])
    token = _stable_context_token(
        app_id,
        [(label, root.as_posix()) for label, root in roots],
        github,
        github_ref,
        _scan_file_map_checksum(file_map),
    )
    local_roots = [
        {"label": label or "", "path": root.as_posix()}
        for label, root in roots
    ]
    github_refs = [
        {
            "label": label or "",
            "repo": repo,
            "github_url": f"https://github.com/{repo}",
            "ref": github_ref,
        }
        for label, repo in github
    ]
    if roots and not github and len(roots) == 1:
        kind = SourceRefKind.APP_ROOT
        uri = roots[0][1].as_uri()
        ref = roots[0][0] or roots[0][1].name
    elif github and not roots and len(github) == 1:
        kind = SourceRefKind.REPO
        uri = f"https://github.com/{github[0][1]}"
        ref = github_ref or github[0][1].split("/")[-1]
    else:
        kind = SourceRefKind.DISCOVERY_SNAPSHOT
        uri = f"mozaiks://app-intelligence/existing-app-discovery/{app_id}/{token}"
        ref = github_ref or token

    return SourceRef(
        source_ref_id=f"src_existing_app_{token}",
        kind=kind,
        uri=uri,
        ref=ref,
        checksum=_scan_file_map_checksum(file_map),
        indexed_at=indexed_at,
        metadata={
            "source": "existing_app_discovery_preload",
            "local_roots": local_roots,
            "github_sources": github_refs,
            "scan_policy": scan_health.get("policy_id"),
            "selected_file_count": len(file_map),
        },
    )


async def _register_preloaded_app_intelligence_context(
    *,
    context_variables: Any,
    source_index: Any,
    source_chat_id: str | None,
) -> dict[str, Any]:
    try:
        from mozaiksai.control_plane.app_intelligence import register_app_intelligence_context
        from mozaiksai.core.app_context import AppContextMode
    except Exception as exc:
        return {"persisted": False, "warning": f"app_intelligence_registration_import_failed:{exc}"}

    try:
        registration = await register_app_intelligence_context(
            source_index=source_index,
            source_workflow="ExistingAppDiscovery",
            source_chat_id=source_chat_id,
            make_current=True,
            mode=AppContextMode.BROWNFIELD,
            source_context_path="existing_app_discovery/app_context/source_context_bundle.json",
            graph_path="existing_app_discovery/app_context/app_context_graph.json",
            intelligence_path="existing_app_discovery/app_context/app_intelligence_snapshot.json",
        )
    except Exception as exc:
        return {"persisted": False, "warning": f"app_intelligence_registration_failed:{exc}"}

    payload = {
        "persisted": True,
        "source_context_artifact_version_id": registration.source_context_artifact_version_id,
        "app_intelligence_artifact_version_id": registration.app_intelligence_artifact_version_id,
        "app_context_version_id": registration.app_context_version_id,
        "app_context_artifact_version_id": registration.app_context_artifact_version_id,
        "graph_artifact_version_id": registration.graph_artifact_version_id,
    }
    _ctx_set(context_variables, "current_context_version_id", registration.app_context_version_id)
    _ctx_set(context_variables, "current_app_context_version_id", registration.app_context_version_id)
    _ctx_set(context_variables, "app_context_version_artifact_version_id", registration.app_context_artifact_version_id)
    _ctx_set(context_variables, "source_context_artifact_version_id", registration.source_context_artifact_version_id)
    _ctx_set(context_variables, "app_intelligence_artifact_version_id", registration.app_intelligence_artifact_version_id)
    _ctx_set(context_variables, "graph_artifact_version_id", registration.graph_artifact_version_id)
    _ctx_set(context_variables, "app_intelligence_registration", payload)
    return payload


async def _preload_context_graph_pack(
    *,
    context_variables: Any,
    roots: list[tuple[str, Path]],
    github_sources: list[tuple[str, str]] | None,
    github_ref: str | None,
    github_token: str | None,
    discovery_inputs: dict[str, Any],
) -> dict[str, Any]:
    _set_app_intelligence_progress(
        context_variables,
        "selecting_source_files",
        details={
            "source": "local_source_scan" if roots else "github_source_scan",
            "local_root_count": len(roots),
            "github_source_count": len(github_sources or []),
        },
    )
    await _emit_app_intelligence_activity(context_variables)
    if not roots and not github_sources:
        result = await _preload_prior_context_graph_pack(
            context_variables=context_variables,
            discovery_inputs=discovery_inputs,
            unavailable_reason="no_local_source_roots",
        )
        await _emit_app_intelligence_activity(context_variables)
        return result

    try:
        from factory_app.workflows._shared.context_graph.prompt_pack import (
            build_context_graph_prompt_pack,
            build_context_graph_unavailable_pack,
        )
        from mozaiksai.control_plane.context_graph import build_context_graph_catalog
        from mozaiksai.core.app_context import (
            build_app_intelligence_catalog,
            build_source_corpus_catalog,
            index_source_scan,
        )
    except Exception as exc:
        warning = f"context_graph_import_failed:{exc}"
        _ctx_set(
            context_variables,
            "context_graph_pack",
            {
                "pack_kind": "context_graph_prompt_pack",
                "present": False,
                "status": "unavailable",
                "reason": "context_graph_import_failed",
                "warnings": [warning],
            },
        )
        _ctx_set(context_variables, "context_graph_catalog", None)
        _ctx_set(context_variables, "source_context_bundle", None)
        _ctx_set(context_variables, "source_context_catalog", None)
        _ctx_set(context_variables, "app_intelligence_snapshot", None)
        _ctx_set(context_variables, "app_intelligence_catalog", None)
        _ctx_set(context_variables, "context_graph_status", "unavailable")
        _ctx_set(context_variables, "context_graph_reason", "context_graph_import_failed")
        _ctx_set(context_variables, "context_graph_warnings", [warning])
        health = {"source": "existing_app_discovery_preload", "status": "unavailable", "reason": "context_graph_import_failed"}
        _ctx_set(
            context_variables,
            "context_graph_health",
            health,
        )
        _ctx_set(context_variables, "app_intelligence_health", health)
        _set_app_intelligence_progress(
            context_variables,
            "failed",
            message="App Intelligence indexing could not import the code-context runtime.",
            details=health,
            warnings=[warning],
        )
        await _emit_app_intelligence_activity(context_variables)
        return {"present": False, "reason": "context_graph_import_failed", "warnings": [warning]}

    scan_policy_inputs = _context_graph_scan_policy_inputs(context_variables, discovery_inputs)
    if roots:
        scan_result = _collect_context_graph_file_map(
            roots,
            scan_policy_inputs=scan_policy_inputs,
        )
    else:
        async def _github_fetch_progress(completed: int, total: int) -> None:
            if total <= 0:
                return
            percent = 42 + int((min(completed, total) / total) * 13)
            _set_app_intelligence_progress(
                context_variables,
                "fetching_source_files",
                message=f"Downloading selected source files from GitHub ({completed}/{total}).",
                percent=percent,
                details={
                    "source": "github_source_scan",
                    "downloaded_file_count": completed,
                    "fetch_candidate_count": total,
                },
            )
            await _emit_app_intelligence_activity(context_variables)

        scan_result = await _collect_github_context_graph_file_map(
            list(github_sources or []),
            github_ref=github_ref,
            github_token=github_token,
            scan_policy_inputs=scan_policy_inputs,
            progress_callback=_github_fetch_progress,
        )
    file_map = scan_result.file_map
    warnings = list(scan_result.warnings)
    if not file_map:
        access_issues = [
            item for item in scan_result.health.get("access_issues", [])
            if isinstance(item, dict)
        ]
        recovery = access_issues[0] if access_issues else {}
        if recovery:
            _set_repo_access_recovery(context_variables, recovery)
        unavailable_reason = str(recovery.get("code") or "no_supported_source_files")
        unavailable_message = (
            str(recovery.get("message") or "").strip()
            or "No supported source files were available for App Intelligence indexing."
        )
        _ctx_set(
            context_variables,
            "context_graph_pack",
            build_context_graph_unavailable_pack(reason=unavailable_reason, warnings=warnings),
        )
        _ctx_set(context_variables, "context_graph_catalog", None)
        _ctx_set(context_variables, "source_context_bundle", None)
        _ctx_set(context_variables, "source_context_catalog", None)
        _ctx_set(context_variables, "app_intelligence_snapshot", None)
        _ctx_set(context_variables, "app_intelligence_catalog", None)
        _ctx_set(context_variables, "context_graph_status", "unavailable")
        _ctx_set(context_variables, "context_graph_reason", unavailable_reason)
        _ctx_set(context_variables, "context_graph_warnings", warnings)
        _ctx_set(context_variables, "context_graph_health", dict(scan_result.health))
        _ctx_set(context_variables, "app_intelligence_health", dict(scan_result.health))
        _set_app_intelligence_progress(
            context_variables,
            "repo_access_required" if recovery else "unavailable",
            message=unavailable_message,
            details=dict(scan_result.health),
            warnings=warnings,
        )
        await _emit_app_intelligence_activity(context_variables)
        return {"present": False, "reason": unavailable_reason, "warnings": warnings}

    app_id = str(
        _first_nonempty(
            _ctx_get(context_variables, "app_id"),
            _ctx_get(context_variables, "app_name"),
            "existing_app_discovery",
        )
    )
    artifact_version_id = str(
        _first_nonempty(
            _ctx_get(context_variables, "current_context_version_id"),
            "existing_app_discovery_preload",
        )
    )
    request_text = _context_graph_request_text(context_variables, discovery_inputs)
    _set_app_intelligence_progress(
        context_variables,
        "extracting_symbols",
        details={
            "selected_file_count": len(file_map),
            "candidate_file_count": scan_result.health.get("candidate_file_count"),
            "source": scan_result.health.get("source") or ("local_source_scan" if roots else "github_source_scan"),
        },
        warnings=warnings,
    )
    await _emit_app_intelligence_activity(context_variables)
    indexed_at = datetime.now(UTC)
    source_ref = _build_preload_source_ref(
        app_id=app_id,
        roots=roots,
        github_sources=github_sources,
        github_ref=github_ref,
        file_map=file_map,
        scan_health=dict(scan_result.health),
        indexed_at=indexed_at,
    )
    source_index = index_source_scan(
        app_id=app_id,
        scan_result=scan_result,
        artifact_version_id=f"{artifact_version_id}_{source_ref.source_ref_id}",
        artifact_kind="existing_app_source",
        artifact_key="existing_app_source",
        source_ref=source_ref,
        indexed_at=indexed_at,
    )
    _set_app_intelligence_progress(
        context_variables,
        "building_snapshot",
        details={
            "selected_file_count": len(file_map),
            "source_context_bundle_id": source_index.source_corpus.bundle_id,
            "graph_id": source_index.app_context_graph.graph_id,
            "health_status": source_index.health_report.get("status"),
        },
        warnings=warnings,
    )
    await _emit_app_intelligence_activity(context_variables)
    source_corpus = source_index.source_corpus
    app_intelligence_snapshot = source_index.app_intelligence_snapshot
    source_file_map = source_index.safe_file_map
    scan_health = source_index.scan_health
    parser_status = source_index.parser_status

    catalog = build_context_graph_catalog(
        graph=source_index.app_context_graph,
        raw_user_request=request_text,
        file_map=source_file_map,
    )
    source_context_catalog = build_source_corpus_catalog(source_corpus)
    app_intelligence_catalog = build_app_intelligence_catalog(app_intelligence_snapshot)
    catalog.update(
        {
            "source": "existing_app_discovery_preload",
            "warnings": warnings,
            "indexed_file_count": len(source_file_map),
            "source_context_bundle_id": source_corpus.bundle_id,
            "source_context_chunk_count": len(source_corpus.chunks),
            "source_context_symbol_count": len(source_corpus.symbols),
            "app_intelligence_snapshot_id": app_intelligence_snapshot.snapshot_id,
            "scan_health": scan_health,
            "parser_status": parser_status,
        }
    )
    pack = build_context_graph_prompt_pack(
        catalog=catalog,
        source="existing_app_discovery_preload",
        warnings=warnings,
    )
    _ctx_set(context_variables, "context_graph_pack", pack)
    _ctx_set(context_variables, "context_graph_catalog", catalog)
    _ctx_set(context_variables, "source_context_bundle", source_corpus.model_dump(mode="json"))
    _ctx_set(context_variables, "source_context_catalog", source_context_catalog)
    _ctx_set(context_variables, "app_intelligence_snapshot", app_intelligence_snapshot.model_dump(mode="json"))
    _ctx_set(context_variables, "app_intelligence_catalog", app_intelligence_catalog)
    _ctx_set(context_variables, "context_graph_status", "loaded")
    _ctx_set(context_variables, "context_graph_reason", None)
    _ctx_set(context_variables, "context_graph_warnings", warnings)
    _ctx_set(context_variables, "context_graph_health", scan_health)
    _ctx_set(context_variables, "app_intelligence_health", source_index.health_report)
    registration = await _register_preloaded_app_intelligence_context(
        context_variables=context_variables,
        source_index=source_index,
        source_chat_id=str(_ctx_get(context_variables, "chat_id") or "") or None,
    )
    if registration.get("warning"):
        warnings = [*warnings, str(registration["warning"])]
        _ctx_set(context_variables, "context_graph_warnings", warnings)
    _set_app_intelligence_progress(
        context_variables,
        "ready",
        details={
            "source": "existing_app_discovery_preload",
            "indexed_file_count": len(source_file_map),
            "source_context_bundle_id": source_corpus.bundle_id,
            "source_context_chunk_count": len(source_corpus.chunks),
            "source_context_symbol_count": len(source_corpus.symbols),
            "context_graph_id": catalog.get("graph_id"),
            "app_intelligence_snapshot_id": app_intelligence_snapshot.snapshot_id,
            "app_context_version_id": registration.get("app_context_version_id"),
            "app_context_persisted": bool(registration.get("persisted")),
            "health_status": source_index.health_report.get("status"),
        },
        warnings=warnings,
    )
    await _emit_app_intelligence_activity(context_variables)
    return {
        "present": True,
        "source": "existing_app_discovery_preload",
        "graph_id": catalog.get("graph_id"),
        "source_context_bundle_id": source_corpus.bundle_id,
        "source_context_artifact_version_id": registration.get("source_context_artifact_version_id"),
        "indexed_file_count": len(source_file_map),
        "source_context_chunk_count": len(source_corpus.chunks),
        "source_context_symbol_count": len(source_corpus.symbols),
        "app_intelligence_snapshot_id": app_intelligence_snapshot.snapshot_id,
        "app_intelligence_artifact_version_id": registration.get("app_intelligence_artifact_version_id"),
        "app_context_version_id": registration.get("app_context_version_id"),
        "app_context_artifact_version_id": registration.get("app_context_artifact_version_id"),
        "graph_artifact_version_id": registration.get("graph_artifact_version_id"),
        "app_context_persisted": bool(registration.get("persisted")),
        "warnings": warnings,
        "scan_health": scan_health,
        "health_report": source_index.health_report,
    }


async def _preload_prior_context_graph_pack(
    *,
    context_variables: Any,
    discovery_inputs: dict[str, Any],
    unavailable_reason: str,
) -> dict[str, Any]:
    context_refresh_request = _coerce_mapping(_ctx_get(context_variables, "context_refresh_request", {}))
    app_id = str(
        _first_nonempty(
            _ctx_get(context_variables, "app_id"),
            context_refresh_request.get("app_id"),
            discovery_inputs.get("app_id"),
            "",
        )
        or ""
    ).strip()
    context_version_id = str(
        _first_nonempty(
            _ctx_get(context_variables, "current_context_version_id"),
            context_refresh_request.get("current_context_version_id"),
            "",
        )
        or ""
    ).strip()
    if not app_id or not context_version_id:
        return _set_context_graph_unavailable(
            context_variables,
            reason=unavailable_reason,
            warnings=[],
            source="existing_app_discovery_preload",
        )

    try:
        from factory_app.workflows._shared.context_graph.prompt_pack import (
            build_context_graph_prompt_pack,
        )
        from mozaiksai.control_plane.app_context import (
            get_app_context_graph_for_version,
            get_app_intelligence_snapshot_for_version,
            get_source_context_bundle_for_version,
        )
        from mozaiksai.control_plane.context_graph import build_context_graph_catalog
        from mozaiksai.core.app_context import (
            build_app_intelligence_catalog,
            build_app_intelligence_snapshot,
            build_source_corpus_catalog,
        )
    except Exception as exc:
        warning = f"context_graph_import_failed:{exc}"
        return _set_context_graph_unavailable(
            context_variables,
            reason="context_graph_import_failed",
            warnings=[warning],
            source="previous_app_context_graph",
        )

    lookup = await get_app_context_graph_for_version(
        app_id=app_id,
        context_version_id=context_version_id,
    )
    if lookup.graph is None:
        warnings = list(lookup.warnings)
        return _set_context_graph_unavailable(
            context_variables,
            reason="previous_app_context_graph_unavailable",
            warnings=warnings,
            source="previous_app_context_graph",
        )

    source_context_bundle = None
    source_context_catalog = None
    app_intelligence_snapshot = None
    app_intelligence_catalog = None
    source_warnings: list[str] = []
    try:
        source_lookup = await get_source_context_bundle_for_version(
            app_id=app_id,
            context_version_id=context_version_id,
        )
        source_warnings = list(source_lookup.warnings)
        if source_lookup.bundle is not None:
            source_context_bundle = source_lookup.bundle.model_dump(mode="json")
            source_context_catalog = build_source_corpus_catalog(source_lookup.bundle)
    except Exception as exc:
        source_warnings = [f"previous_source_context_bundle_unavailable:{exc}"]

    intelligence_warnings: list[str] = []
    try:
        intelligence_lookup = await get_app_intelligence_snapshot_for_version(
            app_id=app_id,
            context_version_id=context_version_id,
        )
        intelligence_warnings = list(intelligence_lookup.warnings)
        if intelligence_lookup.snapshot is not None:
            app_intelligence_snapshot = intelligence_lookup.snapshot.model_dump(mode="json")
            app_intelligence_catalog = build_app_intelligence_catalog(intelligence_lookup.snapshot)
        elif source_context_bundle and lookup.graph is not None:
            rebuilt_snapshot = build_app_intelligence_snapshot(
                source_corpus=source_context_bundle,
                app_context_graph=lookup.graph,
                app_id=app_id,
                warnings=[*list(lookup.warnings), *source_warnings, *intelligence_warnings],
            )
            app_intelligence_snapshot = rebuilt_snapshot.model_dump(mode="json")
            app_intelligence_catalog = build_app_intelligence_catalog(rebuilt_snapshot)
    except Exception as exc:
        intelligence_warnings = [f"previous_app_intelligence_snapshot_unavailable:{exc}"]

    combined_warnings = [*list(lookup.warnings), *source_warnings, *intelligence_warnings]
    catalog = build_context_graph_catalog(
        graph=lookup.graph,
        raw_user_request=_context_graph_request_text(context_variables, discovery_inputs),
        file_map={},
    )
    health = {
        "source": "previous_app_context_graph",
        "selected_file_count": catalog.get("file_count"),
        "node_count": catalog.get("node_count"),
        "edge_count": catalog.get("edge_count"),
        "warnings": combined_warnings,
    }
    catalog.update(
        {
            "source": "previous_app_context_graph",
            "current_context_version_id": context_version_id,
            "warnings": combined_warnings,
            "scan_health": health,
            "source_context_bundle_id": (source_context_catalog or {}).get("bundle_id"),
            "source_context_chunk_count": (source_context_catalog or {}).get("chunk_count"),
            "source_context_symbol_count": (source_context_catalog or {}).get("symbol_count"),
            "app_intelligence_snapshot_id": (app_intelligence_catalog or {}).get("snapshot_id"),
        }
    )
    pack = build_context_graph_prompt_pack(
        catalog=catalog,
        source="previous_app_context_graph",
        reason="context_refresh_prior_version",
        warnings=combined_warnings,
    )
    _ctx_set(context_variables, "context_graph_pack", pack)
    _ctx_set(context_variables, "context_graph_catalog", catalog)
    _ctx_set(context_variables, "source_context_bundle", source_context_bundle)
    _ctx_set(context_variables, "source_context_catalog", source_context_catalog)
    _ctx_set(context_variables, "app_intelligence_snapshot", app_intelligence_snapshot)
    _ctx_set(context_variables, "app_intelligence_catalog", app_intelligence_catalog)
    _ctx_set(context_variables, "context_graph_status", "loaded")
    _ctx_set(context_variables, "context_graph_reason", "context_refresh_prior_version")
    _ctx_set(context_variables, "context_graph_warnings", combined_warnings)
    _ctx_set(context_variables, "context_graph_health", health)
    _ctx_set(context_variables, "app_intelligence_health", health)
    _ctx_set(context_variables, "app_intelligence_ready", bool(app_intelligence_catalog))
    _set_app_intelligence_progress(
        context_variables,
        "ready" if app_intelligence_catalog else "partial",
        details={
            "source": "previous_app_context_graph",
            "current_context_version_id": context_version_id,
            "context_graph_id": catalog.get("graph_id"),
            "source_context_bundle_id": (source_context_catalog or {}).get("bundle_id"),
            "app_intelligence_snapshot_id": (app_intelligence_catalog or {}).get("snapshot_id"),
        },
        warnings=combined_warnings,
    )
    await _emit_app_intelligence_activity(context_variables)
    return {
        "present": True,
        "source": "previous_app_context_graph",
        "graph_id": catalog.get("graph_id"),
        "source_context_bundle_id": (source_context_catalog or {}).get("bundle_id"),
        "source_context_chunk_count": (source_context_catalog or {}).get("chunk_count"),
        "source_context_symbol_count": (source_context_catalog or {}).get("symbol_count"),
        "app_intelligence_snapshot_id": (app_intelligence_catalog or {}).get("snapshot_id"),
        "warnings": combined_warnings,
        "scan_health": health,
    }


def _set_context_graph_unavailable(
    context_variables: Any,
    *,
    reason: str,
    warnings: list[str],
    source: str | None,
) -> dict[str, Any]:
    try:
        from factory_app.workflows._shared.context_graph.prompt_pack import (
            build_context_graph_unavailable_pack,
        )

        pack = build_context_graph_unavailable_pack(reason=reason, warnings=warnings, source=source)
    except Exception:
        pack = {
            "pack_kind": "context_graph_prompt_pack",
            "present": False,
            "status": "unavailable",
            "reason": reason,
            "source": source,
            "warnings": warnings,
        }
    _ctx_set(context_variables, "context_graph_pack", pack)
    _ctx_set(context_variables, "context_graph_catalog", None)
    _ctx_set(context_variables, "source_context_bundle", None)
    _ctx_set(context_variables, "source_context_catalog", None)
    _ctx_set(context_variables, "app_intelligence_snapshot", None)
    _ctx_set(context_variables, "app_intelligence_catalog", None)
    _ctx_set(context_variables, "context_graph_status", "unavailable")
    _ctx_set(context_variables, "context_graph_reason", reason)
    _ctx_set(context_variables, "context_graph_warnings", warnings)
    health = {"source": source, "status": "unavailable", "reason": reason, "warnings": warnings}
    _ctx_set(context_variables, "context_graph_health", health)
    _ctx_set(context_variables, "app_intelligence_health", health)
    _set_app_intelligence_progress(
        context_variables,
        "unavailable",
        details=health,
        warnings=warnings,
    )
    return {"present": False, "reason": reason, "warnings": warnings, "scan_health": health}


def _combine_repo_summaries(*summaries: dict[str, Any]) -> dict[str, Any]:
    valid = [item for item in summaries if item and item.get("success")]
    if not valid:
        return {}

    repo_names = [item.get("repo_name") for item in valid if item.get("repo_name")]
    languages: list[str] = []
    frameworks: list[str] = []
    target_frameworks: list[str] = []
    route_files: list[str] = []
    service_entrypoints: list[str] = []
    hub_files: list[str] = []
    total_files_scanned = 0

    for summary in valid:
        total_files_scanned += int(summary.get("total_files_scanned") or 0)
        for value in summary.get("languages") or []:
            _append_unique(languages, value)
        for value in summary.get("frameworks") or []:
            _append_unique(frameworks, value)
        for value in summary.get("target_frameworks") or []:
            _append_unique(target_frameworks, value)
        for value in summary.get("route_files") or []:
            _append_unique(route_files, value)
        for value in summary.get("service_entrypoints") or []:
            _append_unique(service_entrypoints, value)
        for value in summary.get("hub_files") or []:
            _append_unique(hub_files, value)

    return {
        "success": True,
        "source": "multi_repo",
        "repo_name": " + ".join(repo_names) if repo_names else "existing_app_sources",  # type: ignore[arg-type]
        "repo_names": repo_names,
        "languages": languages,
        "frameworks": frameworks,
        "target_frameworks": target_frameworks,
        "route_files": route_files,
        "service_entrypoints": service_entrypoints,
        "hub_files": hub_files,
        "total_files_scanned": total_files_scanned,
        "inferred_tech_stack": _infer_stack_from_signals(languages, frameworks + target_frameworks),
    }


def _load_spec_from_file(path_value: str) -> dict[str, Any] | None:
    path = Path(path_value).expanduser().resolve()
    if not path.exists() or not path.is_file():
        return None
    raw_text = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw_text)  # type: ignore[no-any-return]
    except Exception:
        try:
            parsed = yaml.safe_load(raw_text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


async def _load_spec_from_url(url: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0)) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        return None
    try:
        return resp.json()  # type: ignore[no-any-return]
    except Exception:
        try:
            parsed = yaml.safe_load(resp.text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _auth_summary_from_security_schemes(security_schemes: dict[str, Any]) -> str:
    if not security_schemes:
        return "unknown"
    auth_kinds: list[str] = []
    for scheme in security_schemes.values():
        if not isinstance(scheme, dict):
            continue
        scheme_type = str(scheme.get("type") or "").lower()
        if scheme_type == "oauth2":
            _append_unique(auth_kinds, "OAuth2")
        elif scheme_type == "http" and str(scheme.get("scheme") or "").lower() == "bearer":
            _append_unique(auth_kinds, "JWT Bearer")
        elif scheme_type == "apikey":
            _append_unique(auth_kinds, "API Key")
        elif scheme_type:
            _append_unique(auth_kinds, scheme_type)
    return ", ".join(auth_kinds) if auth_kinds else "unknown"


def _summarise_openapi_spec(spec: dict[str, Any], source: str) -> dict[str, Any]:
    paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
    methods = set()
    for item in paths.values():
        if not isinstance(item, dict):
            continue
        for method_name in item.keys():
            methods.add(str(method_name).upper())
    security_schemes = ((spec.get("components") or {}).get("securitySchemes") or {}) if isinstance(spec, dict) else {}
    return {
        "success": True,
        "source": source,
        "title": ((spec.get("info") or {}).get("title")) if isinstance(spec, dict) else None,
        "version": ((spec.get("info") or {}).get("version")) if isinstance(spec, dict) else None,
        "path_count": len(paths),
        "sample_paths": list(paths.keys())[:15],
        "methods": sorted(methods),
        "security_schemes": list(security_schemes.keys()),
        "auth_summary": _auth_summary_from_security_schemes(security_schemes),
    }


async def _collect_openapi(openapi_url: str | None, backend_base_url: str | None, uploaded_openapi_path: str | None) -> dict[str, Any]:
    if uploaded_openapi_path:
        spec = _load_spec_from_file(uploaded_openapi_path)
        if spec:
            summary = _summarise_openapi_spec(spec, "uploaded_openapi")
            summary["spec_location"] = uploaded_openapi_path
            return summary

    if openapi_url:
        spec = await _load_spec_from_url(openapi_url)
        if spec:
            summary = _summarise_openapi_spec(spec, "openapi_url")
            summary["spec_location"] = openapi_url
            return summary

    base_url = _normalise_base_url(backend_base_url)
    if not base_url:
        return {"success": False, "error": "No OpenAPI input provided"}

    for path in _OPENAPI_CANDIDATE_PATHS:
        candidate = f"{base_url}{path}"
        spec = await _load_spec_from_url(candidate)
        if spec:
            summary = _summarise_openapi_spec(spec, "backend_probe")
            summary["spec_location"] = candidate
            return summary

    return {"success": False, "error": "OpenAPI spec not found from configured sources"}


async def _probe_backend(backend_base_url: str | None) -> dict[str, Any]:
    base_url = _normalise_base_url(backend_base_url)
    if not base_url:
        return {"success": False, "error": "No backend_base_url provided"}

    candidates = ["/health", "/healthz", "/api/health", "/"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        for suffix in candidates:
            url = f"{base_url}{suffix}"
            try:
                resp = await client.get(url)
            except Exception as exc:
                logger.debug("[ExistingAppDiscovery] Health probe failed for %s: %s", url, exc)
                continue
            if resp.status_code >= 400:
                continue
            details: dict[str, Any]
            try:
                details = resp.json()
            except Exception:
                details = {"raw": resp.text[:500]}
            return {
                "success": True,
                "health_url": url,
                "status_code": resp.status_code,
                "details": details,
            }

    return {"success": False, "error": f"Backend probe failed for {base_url}"}


def _check_mozaiks_adapter_exists(provider_id: str) -> bool:
    """Return True if mozaiksai/core/adapters/ has a file matching provider_id."""
    adapters_root = _workspace_root() / "mozaiksai" / "core" / "adapters"
    if not adapters_root.exists():
        return False
    needle = provider_id.lower().replace("-", "_")
    return any(True for _ in adapters_root.glob(f"*{needle}*"))


def _collect_package_names_from_root(repo_root: Path) -> list[str]:
    """Read package.json, pyproject.toml, and requirements.txt for dependency names."""
    names: list[str] = []

    # package.json — search repo root and one level deep (monorepos)
    pkg_candidates = [repo_root / "package.json"] + list(repo_root.glob("*/package.json"))[:3]
    for pkg_path in pkg_candidates:
        if not pkg_path.exists():
            continue
        try:
            pkg_data = json.loads(pkg_path.read_text(encoding="utf-8"))
            deps: dict[str, Any] = {}
            deps.update(pkg_data.get("dependencies", {}) or {})
            deps.update(pkg_data.get("devDependencies", {}) or {})
            names.extend(list(deps.keys()))
        except Exception:
            pass

    # pyproject.toml
    pyproject_path = repo_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            data = tomllib.loads(pyproject_path.read_bytes().decode("utf-8"))
            project = data.get("project", {}) if isinstance(data, dict) else {}
            for dep in project.get("dependencies", []) or []:
                dep_name = str(dep).split("[")[0].split(">=")[0].split("==")[0].strip()
                if dep_name:
                    names.append(dep_name)
        except Exception:
            pass

    # requirements.txt
    req_path = repo_root / "requirements.txt"
    if req_path.exists():
        try:
            for line in req_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    dep_name = line.split("[")[0].split(">=")[0].split("==")[0].strip()
                    if dep_name:
                        names.append(dep_name)
        except Exception:
            pass

    return names


def _detect_storage_pattern(
    package_names: list[str],
    source_sample: str,
) -> str:
    """
    Detect primary storage pattern from package names and sampled source text.

    Returns one of: mongodb, sql, file_store, redis, unknown.
    """
    pkg_blob = " ".join(package_names).lower()

    # Package-level detection first (higher confidence)
    for pattern, signals in _STORAGE_PACKAGE_SIGNALS.items():
        for signal in signals:
            if signal.lower() in pkg_blob:
                return pattern

    # Source-level detection (inferred)
    for pattern, patterns in _STORAGE_SOURCE_PATTERNS.items():
        for pat in patterns:
            if pat in source_sample:
                return pattern

    return "unknown"


def _detect_connectors(
    package_names: list[str],
    source_sample: str,
) -> list[dict[str, Any]]:
    """
    Detect external service connectors from package names and sampled source.

    Returns a list of ConnectorSpec dicts (without actual secrets).
    """
    pkg_blob = " ".join(package_names).lower()
    detected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for signal in _CONNECTOR_SIGNALS:
        provider_id = signal["provider_id"]
        found_pkg: str | None = None
        found_import: str | None = None
        source_files: list[str] = []
        confidence = "unverified"

        # Package detection (confirmed)
        if provider_id != "unknown_http":
            for pkg in signal["packages"]:
                if pkg.lower() in pkg_blob:
                    found_pkg = pkg
                    confidence = "confirmed"
                    break

        # Source import detection (inferred)
        if not found_pkg:
            for imp in signal.get("imports", []):
                if imp in source_sample:
                    found_import = imp
                    confidence = "inferred"
                    break

        if not found_pkg and not found_import:
            # Check generic http client packages regardless of provider_id
            if provider_id == "unknown_http":
                for pkg in signal["packages"]:
                    if pkg.lower() in pkg_blob:
                        found_pkg = pkg
                        confidence = "confirmed"
                        break
            if not found_pkg:
                continue

        if provider_id in seen_ids:
            continue
        seen_ids.add(provider_id)

        detected.append({
            "provider_id": provider_id,
            "package_or_import": found_pkg or found_import or provider_id,
            "category": signal["category"],
            "confidence": confidence,
            "source_files": source_files,
            "likely_secret_envs": signal.get("likely_secret_envs", []),
            "mozaiks_adapter_exists": _check_mozaiks_adapter_exists(provider_id),
        })

    return detected


def _detect_mozaiks_vocabulary(
    repo_root: Path,
    source_sample: str,
) -> dict[str, Any]:
    """
    Detect Mozaiks-specific vocabulary and structural indicators.

    Returns:
        mozaiks_vocabulary_detected: bool — vocab terms found in source.
        mozaiks_authored_app: bool — vocab + structural indicators both found.
        vocab_terms_found: list[str]
        structure_indicators_found: list[str]
    """
    vocab_found: list[str] = []
    structure_found: list[str] = []

    # Vocabulary in sampled source text
    for term in _MOZAIKS_VOCAB_PATTERNS:
        if term in source_sample:
            vocab_found.append(term)

    # Structural indicators via glob (cheap existence checks)
    if (repo_root / "app" / "modules").exists():
        structure_found.append("app/modules/")

    for glob_pattern in _MOZAIKS_STRUCTURE_GLOB_CHECKS:
        try:
            for path in repo_root.glob(glob_pattern):
                if not any(part in _EXCLUDED_DIRS for part in path.parts):
                    indicator = glob_pattern.removeprefix("**/")
                    if indicator not in structure_found:
                        structure_found.append(indicator)
                    break
        except Exception:
            pass

    vocab_detected = bool(vocab_found)

    # High-density vocabulary in TypeScript/non-YAML repos is itself a structural
    # signal — the team built the app using Mozaiks vocabulary conventions even
    # without the canonical .yaml file layout.  Count occurrences of each found
    # term; if multiple terms appear many times, treat that as equivalent to a
    # file-structure indicator.
    _HIGH_DENSITY_THRESHOLD = 10  # occurrences per term
    _HIGH_DENSITY_MIN_TERMS = 2   # at least this many terms must exceed threshold
    dense_terms = [
        t for t in vocab_found
        if source_sample.count(t) >= _HIGH_DENSITY_THRESHOLD
    ]
    if len(dense_terms) >= _HIGH_DENSITY_MIN_TERMS:
        structure_found.append("high_density_vocabulary")

    # authored_app: vocabulary detected AND at least one structural indicator
    # (file layout OR high-density vocabulary count)
    authored = vocab_detected and bool(structure_found)

    return {
        "mozaiks_vocabulary_detected": vocab_detected,
        "mozaiks_authored_app": authored,
        "vocab_terms_found": vocab_found,
        "structure_indicators_found": structure_found,
    }


def _sample_source_text(repo_root: Path, max_files: int = 120, max_chars_per_file: int = 15_000) -> str:
    """
    Return a concatenated sample of source text from the repo for pattern detection.
    Limits total output to avoid excessive memory use.
    """
    _SOURCE_EXTENSIONS = {".py", ".ts", ".js", ".tsx", ".jsx", ".yaml", ".yml", ".json"}
    parts: list[str] = []
    total_chars = 0
    max_total = max_files * max_chars_per_file

    for path in _iter_repo_files(repo_root, limit=500):
        if len(parts) >= max_files or total_chars >= max_total:
            break
        if path.suffix.lower() not in _SOURCE_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        excerpt = text[:max_chars_per_file]
        parts.append(excerpt)
        total_chars += len(excerpt)

    return "\n".join(parts)


def _merge_unresolved(existing: list[dict[str, Any]], question: str, context: str, priority: str = "medium") -> None:
    if any(item.get("question") == question for item in existing):
        return
    existing.append({"question": question, "context": context, "priority": priority})


def _app_intelligence_source_location(
    *,
    repo_path: Any,
    frontend_repo_path: Any,
    backend_repo_path: Any,
    github_repo: Any,
    frontend_github_repo: Any,
    backend_github_repo: Any,
) -> str | None:
    values = [
        repo_path,
        frontend_repo_path,
        backend_repo_path,
        github_repo,
        frontend_github_repo,
        backend_github_repo,
    ]
    locations = [str(value).strip() for value in values if value and str(value).strip()]
    return ", ".join(locations[:3]) if locations else None


def _build_app_intelligence_summary(
    *,
    context_variables: Any,
    preload: dict[str, Any],
) -> str | None:
    catalog = _coerce_mapping(_ctx_get(context_variables, "app_intelligence_catalog"))
    graph_catalog = _coerce_mapping(_ctx_get(context_variables, "context_graph_catalog"))
    source_catalog = _coerce_mapping(_ctx_get(context_variables, "source_context_catalog"))
    if not catalog and not preload.get("present"):
        reason = preload.get("reason") or _ctx_get(context_variables, "context_graph_reason")
        if reason:
            return f"App Intelligence unavailable: {reason}."
        return None

    coverage = _coerce_mapping(catalog.get("coverage"))
    file_count = int(
        coverage.get("file_count")
        or preload.get("indexed_file_count")
        or source_catalog.get("file_count")
        or graph_catalog.get("indexed_file_count")
        or 0
    )
    chunk_count = int(
        coverage.get("chunk_count")
        or preload.get("source_context_chunk_count")
        or source_catalog.get("chunk_count")
        or graph_catalog.get("source_context_chunk_count")
        or 0
    )
    symbol_count = int(
        coverage.get("symbol_count")
        or preload.get("source_context_symbol_count")
        or source_catalog.get("symbol_count")
        or graph_catalog.get("source_context_symbol_count")
        or 0
    )
    node_count = int(coverage.get("node_count") or graph_catalog.get("node_count") or 0)
    edge_count = int(coverage.get("edge_count") or graph_catalog.get("edge_count") or 0)
    parts = [
        f"App Intelligence indexed {file_count} files",
        f"{chunk_count} source chunks",
        f"{symbol_count} symbols",
        f"{node_count} graph nodes",
        f"{edge_count} graph edges",
    ]
    health = _coerce_mapping(_ctx_get(context_variables, "app_intelligence_health"))
    status = health.get("status")
    if status:
        parts.append(f"health={status}")
    return ", ".join(parts) + "."


async def collect_prechat_discovery_context(context_variables: Any | None = None) -> dict[str, Any]:
    """Populate discovery context from deterministic pre-chat sources."""
    ctx = context_variables if context_variables is not None else {}
    discovery_inputs = _coerce_mapping(_ctx_get(ctx, "discovery_inputs", {}))
    brownfield_build_path = _first_nonempty(
        _ctx_get(ctx, "brownfield_build_path"),
        discovery_inputs.get("brownfield_build_path"),
    )
    host_app_source = _first_nonempty(
        discovery_inputs.get("host_app_source"),
        _ctx_get(ctx, "host_app_source"),
        "external",
    )
    host_source_inputs = _resolve_host_app_source_inputs(str(host_app_source) if host_app_source else None)
    if host_source_inputs:
        merged_inputs = dict(host_source_inputs)
        merged_inputs.update({key: value for key, value in discovery_inputs.items() if value is not None})
        discovery_inputs = merged_inputs

    discovery_mode = _first_nonempty(_ctx_get(ctx, "discovery_mode"), discovery_inputs.get("discovery_mode"), "guided")

    _ctx_set(ctx, "host_app_source", host_app_source)
    _ctx_set(ctx, "brownfield_build_path", brownfield_build_path)
    _ctx_set(ctx, "app_intelligence_ready", False)
    _set_app_intelligence_progress(
        ctx,
        "resolving_sources",
        details={"host_app_source": host_app_source, "discovery_mode": discovery_mode},
    )
    await _emit_app_intelligence_activity(ctx)

    repo_path = _first_nonempty(_ctx_get(ctx, "repo_path"), discovery_inputs.get("repo_path"))
    frontend_repo_path = _first_nonempty(_ctx_get(ctx, "frontend_repo_path"), discovery_inputs.get("frontend_repo_path"))
    backend_repo_path = _first_nonempty(_ctx_get(ctx, "backend_repo_path"), discovery_inputs.get("backend_repo_path"))
    github_repo = _first_nonempty(_ctx_get(ctx, "github_repo"), discovery_inputs.get("github_repo"))
    frontend_github_repo = _first_nonempty(_ctx_get(ctx, "frontend_github_repo"), discovery_inputs.get("frontend_github_repo"))
    backend_github_repo = _first_nonempty(_ctx_get(ctx, "backend_github_repo"), discovery_inputs.get("backend_github_repo"))
    github_ref = _first_nonempty(_ctx_get(ctx, "github_ref"), discovery_inputs.get("github_ref"))

    # Resolve OAuth session key -> GitHub access token (consumed once, then cleared)
    github_token: str | None = None
    _oauth_session = _first_nonempty(
        _ctx_get(ctx, "github_oauth_session"),
        discovery_inputs.get("github_oauth_session"),
    )
    logger.info(
        "[ExistingAppDiscovery] GitHub discovery input resolved: repo=%s oauth_session_present=%s",
        github_repo,
        bool(_oauth_session),
    )
    if _oauth_session:
        try:
            from mozaiksai.hosts.routers.oauth_github import consume_github_token
            github_token = consume_github_token(str(_oauth_session))
            logger.info(
                "[ExistingAppDiscovery] OAuth token resolved: session=%s present=%s",
                str(_oauth_session)[:8], bool(github_token),
            )
        except Exception as _exc:
            logger.warning("[ExistingAppDiscovery] OAuth token resolution failed: %s", _exc)
        _ctx_set(ctx, "github_oauth_session", None)

    backend_base_url = _first_nonempty(_ctx_get(ctx, "backend_base_url"), discovery_inputs.get("backend_base_url"), _ctx_get(ctx, "app_url"))
    openapi_url = _first_nonempty(_ctx_get(ctx, "openapi_url"), discovery_inputs.get("openapi_url"))
    uploaded_openapi_path = _first_nonempty(_ctx_get(ctx, "uploaded_openapi_path"), discovery_inputs.get("uploaded_openapi_path"))

    evidence_sources: list[dict[str, Any]] = []
    unresolved_questions: list[dict[str, Any]] = list(_ctx_get(ctx, "unresolved_questions", []) or [])
    repo_summary: dict[str, Any] = {}
    frontend_repo_summary: dict[str, Any] = {}
    backend_repo_summary: dict[str, Any] = {}
    api_inventory: dict[str, Any] = {}
    runtime_observations: dict[str, Any] = {}
    auth_hypothesis: dict[str, Any] = {}
    theme_capture_ready = False
    theme_capture_status = "none"
    theme_capture_summary: str | None = None
    theme_capture_evidence: dict[str, Any] = {}
    _set_app_intelligence_progress(
        ctx,
        "collecting_evidence",
        details={
            "has_local_repo": bool(repo_path or frontend_repo_path or backend_repo_path),
            "has_github_repo": bool(github_repo or frontend_github_repo or backend_github_repo),
            "has_api_input": bool(backend_base_url or openapi_url or uploaded_openapi_path),
        },
    )
    await _emit_app_intelligence_activity(ctx)

    if frontend_repo_path or frontend_github_repo:
        frontend_repo_summary = await _scan_repo_source(frontend_repo_path, frontend_github_repo, github_ref, github_token=github_token)
        evidence_sources.append({
            "kind": "frontend_repo_scan",
            "location": frontend_repo_path or frontend_github_repo,
            "success": bool(frontend_repo_summary.get("success")),
        })
        if not frontend_repo_summary.get("success"):
            _set_repo_access_recovery(ctx, _coerce_mapping(frontend_repo_summary.get("repo_access_recovery")))
            _merge_unresolved(
                unresolved_questions,
                "Frontend repo could not be scanned.",
                frontend_repo_summary.get("error", "Frontend repo scan failed."),
                "high",
            )

    if backend_repo_path or backend_github_repo:
        backend_repo_summary = await _scan_repo_source(backend_repo_path, backend_github_repo, github_ref, github_token=github_token)
        evidence_sources.append({
            "kind": "backend_repo_scan",
            "location": backend_repo_path or backend_github_repo,
            "success": bool(backend_repo_summary.get("success")),
        })
        if not backend_repo_summary.get("success"):
            _set_repo_access_recovery(ctx, _coerce_mapping(backend_repo_summary.get("repo_access_recovery")))
            _merge_unresolved(
                unresolved_questions,
                "Backend repo could not be scanned.",
                backend_repo_summary.get("error", "Backend repo scan failed."),
                "high",
            )

    if repo_path and not frontend_repo_summary and not backend_repo_summary:
        repo_summary = _scan_local_repo(str(repo_path))
        evidence_sources.append({
            "kind": "local_repo",
            "location": str(repo_path),
            "success": bool(repo_summary.get("success")),
        })
        if not repo_summary.get("success"):
            _merge_unresolved(
                unresolved_questions,
                "Local repo path could not be scanned.",
                repo_summary.get("error", "Repo scan failed."),
                "high",
            )
    elif github_repo and not frontend_repo_summary and not backend_repo_summary:
        repo_summary = await _scan_github_repo(str(github_repo), str(github_ref) if github_ref else None, github_token=github_token)
        evidence_sources.append({
            "kind": "github_repo_scan",
            "location": str(github_repo),
            "success": bool(repo_summary.get("success")),
        })
        if not repo_summary.get("success"):
            _set_repo_access_recovery(ctx, _coerce_mapping(repo_summary.get("repo_access_recovery")))
            _merge_unresolved(
                unresolved_questions,
                "GitHub repo could not be scanned.",
                repo_summary.get("error", "GitHub scan failed. Private repos require a GitHub token or app installation."),
                "high",
            )

    if not repo_summary:
        repo_summary = _combine_repo_summaries(frontend_repo_summary, backend_repo_summary)

    if openapi_url or uploaded_openapi_path or backend_base_url:
        api_inventory = await _collect_openapi(
            str(openapi_url) if openapi_url else None,
            str(backend_base_url) if backend_base_url else None,
            str(uploaded_openapi_path) if uploaded_openapi_path else None,
        )
        evidence_sources.append({
            "kind": "openapi",
            "location": api_inventory.get("spec_location") or openapi_url or uploaded_openapi_path or backend_base_url,
            "success": bool(api_inventory.get("success")),
        })
        if api_inventory.get("success"):
            auth_hypothesis = {
                "source": api_inventory.get("source"),
                "summary": api_inventory.get("auth_summary", "unknown"),
                "security_schemes": api_inventory.get("security_schemes", []),
            }
        else:
            _merge_unresolved(
                unresolved_questions,
                "API schema could not be loaded.",
                api_inventory.get("error", "Provide an OpenAPI URL/file or a backend base URL with a discoverable swagger endpoint."),
                "high",
            )

    if backend_base_url:
        runtime_observations = await _probe_backend(str(backend_base_url))
        evidence_sources.append({
            "kind": "runtime_probe",
            "location": str(backend_base_url),
            "success": bool(runtime_observations.get("success")),
        })
        if not runtime_observations.get("success"):
            _merge_unresolved(
                unresolved_questions,
                "Backend health probe failed.",
                runtime_observations.get("error", "Verify the backend base URL and that it is reachable from the Mozaiks runtime."),
                "medium",
            )

    if not evidence_sources:
        _merge_unresolved(
            unresolved_questions,
            "No pre-chat evidence source was provided.",
            "Provide at least one deterministic source such as repo_path, github_repo, backend_base_url, openapi_url, or uploaded_openapi_path.",
            "high",
        )

    if repo_summary.get("success") and not _ctx_get(ctx, "tech_stack") and repo_summary.get("inferred_tech_stack"):
        _ctx_set(ctx, "tech_stack", repo_summary["inferred_tech_stack"])
    if repo_summary.get("success") and not _ctx_get(ctx, "app_name"):
        inferred_name = repo_summary.get("repo_name")
        if inferred_name:
            _ctx_set(ctx, "app_name", inferred_name)

    if api_inventory.get("success"):
        _ctx_set(ctx, "api_docs_available", True)
        if not _ctx_get(ctx, "api_surface"):
            _ctx_set(ctx, "api_surface", f"OpenAPI, {api_inventory.get('path_count', 0)} paths")
        if not _ctx_get(ctx, "auth_model") and auth_hypothesis.get("summary"):
            _ctx_set(ctx, "auth_model", auth_hypothesis["summary"])
        if not _ctx_get(ctx, "app_name") and api_inventory.get("title"):
            _ctx_set(ctx, "app_name", api_inventory["title"])

    theme_app_url = _ctx_get(ctx, "app_url")
    if backend_base_url and theme_app_url and str(theme_app_url).rstrip("/") == str(backend_base_url).rstrip("/"):
        theme_app_url = None

    frontend_theme_path = _find_theme_config_path(str(frontend_repo_path)) if frontend_repo_path else None
    frontend_shell_path = _find_shell_config_path(str(frontend_repo_path)) if frontend_repo_path else None
    frontend_css_snapshot = _collect_theme_css_snapshot(str(frontend_repo_path)) if frontend_repo_path else None
    if not frontend_css_snapshot and repo_path and not frontend_repo_path and not backend_repo_path:
        frontend_css_snapshot = _collect_theme_css_snapshot(str(repo_path))
    if not frontend_theme_path and repo_path and not frontend_repo_path and not backend_repo_path:
        frontend_theme_path = _find_theme_config_path(str(repo_path))
    if not frontend_shell_path and repo_path and not frontend_repo_path and not backend_repo_path:
        frontend_shell_path = _find_shell_config_path(str(repo_path))

    theme_seed_available = any(
        [
            theme_app_url,
            frontend_theme_path,
            frontend_css_snapshot,
            frontend_repo_summary,
        ]
    )
    if theme_seed_available:
        try:
            theme_module = _load_theme_capture_preloader()
            theme_context: dict[str, Any] = {
                "app_url": theme_app_url,
                "parent_theme_config": frontend_theme_path,
                "parent_shell_config": frontend_shell_path,
                "css_snapshot": frontend_css_snapshot,
                "frontend_repo_summary": frontend_repo_summary or {},
                "app_name": _ctx_get(ctx, "app_name"),
                "app_description": _ctx_get(ctx, "app_description"),
                "tech_stack": _ctx_get(ctx, "tech_stack"),
            }
            await theme_module.collect_prechat_theme_context(theme_context)
            theme_capture_status = str(theme_context.get("preload_status") or "none")
            theme_capture_ready = bool(theme_context.get("preloaded_context_ready"))
            theme_capture_evidence = theme_context.get("theme_capture_evidence") or {}
            theme_capture_summary = _summarize_theme_evidence(theme_capture_evidence) or theme_context.get("preload_summary")
            if not _ctx_get(ctx, "app_name") and theme_context.get("app_name"):
                _ctx_set(ctx, "app_name", theme_context["app_name"])
        except Exception as exc:
            logger.warning("[ExistingAppDiscovery] Theme evidence preload failed: %s", exc)
            theme_capture_status = "partial"
            theme_capture_summary = "Theme evidence preload failed before chat; refinement may be needed."

        evidence_sources.append(
            {
                "kind": "theme_capture",
                "location": frontend_theme_path or frontend_repo_path or theme_app_url,
                "success": bool(theme_capture_ready),
            }
        )

    service_surface_summary = backend_repo_summary or repo_summary
    route_surface_summary = frontend_repo_summary or repo_summary
    inferred_service_surfaces = _infer_service_surfaces(service_surface_summary, api_inventory, runtime_observations)
    inferred_route_surfaces = _infer_route_surfaces(route_surface_summary)

    # -----------------------------------------------------------------------
    # App pattern detection: storage, connectors, Mozaiks vocabulary
    # -----------------------------------------------------------------------
    storage_pattern = "unknown"
    storage_migration_required = False
    detected_connectors: list[dict[str, Any]] = []
    mozaiks_vocabulary_detected = False
    mozaiks_authored_app = False

    active_repo_root: Path | None = None
    for candidate_path in [repo_path, frontend_repo_path, backend_repo_path]:
        if candidate_path:
            candidate = Path(str(candidate_path)).expanduser().resolve()
            if candidate.exists() and candidate.is_dir():
                active_repo_root = candidate
                break

    app_intelligence_roots = _context_graph_roots(
        repo_path=repo_path,
        frontend_repo_path=frontend_repo_path,
        backend_repo_path=backend_repo_path,
    )
    app_intelligence_github_sources = _context_graph_github_sources(
        github_repo=github_repo,
        frontend_github_repo=frontend_github_repo,
        backend_github_repo=backend_github_repo,
    )
    context_graph_preload = await _preload_context_graph_pack(
        context_variables=ctx,
        roots=app_intelligence_roots,
        github_sources=app_intelligence_github_sources,
        github_ref=str(github_ref) if github_ref else None,
        github_token=github_token,
        discovery_inputs=discovery_inputs,
    )
    attempted_app_intelligence = bool(
        app_intelligence_roots
        or app_intelligence_github_sources
        or _ctx_get(ctx, "current_context_version_id")
        or _ctx_get(ctx, "context_refresh_request")
    )
    if context_graph_preload.get("present") or attempted_app_intelligence:
        evidence_sources.append(
            {
                "kind": "app_intelligence_index",
                "location": _app_intelligence_source_location(
                    repo_path=repo_path,
                    frontend_repo_path=frontend_repo_path,
                    backend_repo_path=backend_repo_path,
                    github_repo=github_repo,
                    frontend_github_repo=frontend_github_repo,
                    backend_github_repo=backend_github_repo,
                ),
                "success": bool(context_graph_preload.get("present")),
                "status": _ctx_get(ctx, "app_intelligence_status"),
                "reason": context_graph_preload.get("reason"),
                "indexed_file_count": context_graph_preload.get("indexed_file_count"),
                "source_context_bundle_id": context_graph_preload.get("source_context_bundle_id"),
                "app_intelligence_snapshot_id": context_graph_preload.get("app_intelligence_snapshot_id"),
            }
        )

    if active_repo_root:
        try:
            package_names = _collect_package_names_from_root(active_repo_root)
            source_sample = _sample_source_text(active_repo_root)

            storage_pattern = _detect_storage_pattern(package_names, source_sample)
            storage_migration_required = storage_pattern in ("file_store", "unknown")

            detected_connectors = _detect_connectors(package_names, source_sample)

            vocab_result = _detect_mozaiks_vocabulary(active_repo_root, source_sample)
            mozaiks_vocabulary_detected = vocab_result["mozaiks_vocabulary_detected"]
            mozaiks_authored_app = vocab_result["mozaiks_authored_app"]

            logger.info(
                "[ExistingAppDiscovery] App pattern scan: storage=%s connectors=%s "
                "mozaiks_vocab=%s mozaiks_authored=%s",
                storage_pattern,
                [c["provider_id"] for c in detected_connectors],
                mozaiks_vocabulary_detected,
                mozaiks_authored_app,
            )
        except Exception as exc:
            logger.warning("[ExistingAppDiscovery] App pattern detection failed: %s", exc)

    if inferred_service_surfaces and not _ctx_get(ctx, "service_surfaces"):
        _ctx_set(ctx, "service_surfaces", inferred_service_surfaces)
    if inferred_route_surfaces and not _ctx_get(ctx, "route_surfaces"):
        _ctx_set(ctx, "route_surfaces", inferred_route_surfaces)
    if not _ctx_get(ctx, "existing_experience_summary") and inferred_route_surfaces:
        route_labels = ", ".join(item.get("module", "surface") for item in inferred_route_surfaces[:4])
        _ctx_set(
            ctx,
            "existing_experience_summary",
            f"Current experience appears to be organized around route/module surfaces such as {route_labels}.",
        )

    successful_sources = [item for item in evidence_sources if item.get("success")]
    preload_status = "ready" if successful_sources else "none"
    if evidence_sources and successful_sources and len(successful_sources) < len(evidence_sources):
        preload_status = "partial"

    app_intelligence_ready = bool(_ctx_get(ctx, "app_intelligence_catalog"))
    if app_intelligence_ready:
        _ctx_set(ctx, "app_intelligence_ready", True)
        _ctx_set(ctx, "app_intelligence_status", "ready")
    if not _ctx_get(ctx, "repo_access_status"):
        if app_intelligence_ready or repo_summary.get("success") or frontend_repo_summary.get("success") or backend_repo_summary.get("success"):
            _ctx_set(ctx, "repo_access_status", "available")
        elif github_repo or frontend_github_repo or backend_github_repo:
            _ctx_set(ctx, "repo_access_status", "required")
        else:
            _ctx_set(ctx, "repo_access_status", "not_provided")

    summary_lines = []
    app_intelligence_summary = _build_app_intelligence_summary(
        context_variables=ctx,
        preload=context_graph_preload,
    )
    if app_intelligence_summary:
        summary_lines.append(app_intelligence_summary)
    if repo_summary.get("success"):
        summary_lines.append(
            f"Repository metadata loaded from {repo_summary.get('source')}: {repo_summary.get('inferred_tech_stack') or 'stack not inferred'}"
        )
        if inferred_route_surfaces:
            summary_lines.append(
                f"Route surfaces detected: {len(inferred_route_surfaces)}"
            )
        if inferred_service_surfaces:
            summary_lines.append(
                f"Service surfaces detected: {len(inferred_service_surfaces)}"
            )
    if api_inventory.get("success"):
        summary_lines.append(
            f"API schema loaded: {api_inventory.get('path_count', 0)} paths, auth={api_inventory.get('auth_summary', 'unknown')}"
        )
    if runtime_observations.get("success"):
        summary_lines.append(
            f"Backend probe succeeded at {runtime_observations.get('health_url')}"
        )
    if theme_capture_status != "none" and theme_capture_summary:
        summary_lines.append(f"Theme evidence: {theme_capture_summary}")
    if brownfield_build_path:
        summary_lines.append(f"Selected brownfield path: {brownfield_build_path}")
    if context_graph_preload.get("present"):
        if context_graph_preload.get("source") == "previous_app_context_graph":
            summary_lines.append("App Intelligence loaded the previous AppContext graph for refresh.")
    if unresolved_questions:
        summary_lines.append(
            f"Open questions remaining: {len(unresolved_questions)}"
        )

    _ctx_set(ctx, "repo_path", repo_path)
    _ctx_set(ctx, "discovery_mode", discovery_mode)
    _ctx_set(ctx, "github_repo", github_repo)
    _ctx_set(ctx, "frontend_repo_path", frontend_repo_path)
    _ctx_set(ctx, "backend_repo_path", backend_repo_path)
    _ctx_set(ctx, "frontend_github_repo", frontend_github_repo)
    _ctx_set(ctx, "backend_github_repo", backend_github_repo)
    _ctx_set(ctx, "github_ref", github_ref)
    _ctx_set(ctx, "backend_base_url", backend_base_url)
    _ctx_set(ctx, "openapi_url", openapi_url)
    _ctx_set(ctx, "uploaded_openapi_path", uploaded_openapi_path)
    _ctx_set(ctx, "evidence_sources", evidence_sources)
    _ctx_set(ctx, "repo_summary", repo_summary if repo_summary else {})
    _ctx_set(ctx, "frontend_repo_summary", frontend_repo_summary if frontend_repo_summary else {})
    _ctx_set(ctx, "backend_repo_summary", backend_repo_summary if backend_repo_summary else {})
    _ctx_set(ctx, "api_inventory", api_inventory if api_inventory else {})
    _ctx_set(ctx, "runtime_observations", runtime_observations if runtime_observations else {})
    _ctx_set(ctx, "auth_hypothesis", auth_hypothesis if auth_hypothesis else {})
    _ctx_set(ctx, "theme_capture_ready", theme_capture_ready)
    _ctx_set(ctx, "theme_capture_status", theme_capture_status)
    _ctx_set(ctx, "theme_capture_summary", theme_capture_summary)
    _ctx_set(ctx, "theme_capture_evidence", theme_capture_evidence if theme_capture_evidence else {})
    _ctx_set(ctx, "unresolved_questions", unresolved_questions)
    _ctx_set(ctx, "app_intelligence_ready", app_intelligence_ready)
    _ctx_set(ctx, "app_intelligence_summary", app_intelligence_summary)
    _ctx_set(ctx, "preloaded_context_ready", bool(successful_sources or app_intelligence_ready))
    _ctx_set(ctx, "preload_status", preload_status)
    _ctx_set(ctx, "preload_summary", "\n".join(summary_lines) if summary_lines else "No deterministic evidence was preloaded.")
    # App pattern detection results
    _ctx_set(ctx, "storage_pattern", storage_pattern)
    _ctx_set(ctx, "storage_migration_required", storage_migration_required)
    _ctx_set(ctx, "detected_connectors", detected_connectors)
    _ctx_set(ctx, "mozaiks_vocabulary_detected", mozaiks_vocabulary_detected)
    _ctx_set(ctx, "mozaiks_authored_app", mozaiks_authored_app)

    logger.info(
        "[ExistingAppDiscovery] before_chat preload complete: status=%s, sources=%s",
        preload_status,
        ", ".join(item.get("kind", "unknown") for item in evidence_sources) or "none",
    )

    return {
        "success": True,
        "preload_status": preload_status,
        "successful_sources": len(successful_sources),
        "total_sources": len(evidence_sources),
        "context_graph_status": _ctx_get(ctx, "context_graph_status"),
        "app_intelligence_status": _ctx_get(ctx, "app_intelligence_status"),
        "app_intelligence_ready": app_intelligence_ready,
    }
