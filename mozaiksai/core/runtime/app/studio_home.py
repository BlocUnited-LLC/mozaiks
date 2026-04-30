from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
    build_app_validation_strategy_summary,
)


GENERATOR_WORKFLOW_IDS = [
    "ValueEngine",
    "DesignDocs",
    "AppGenerator",
    "AgentGenerator",
]

BUILD_REQUEST_EXAMPLES = [
    "Build a lead intake flow for inbound sales ops.",
    "Connect this existing backend first and keep billing host-owned.",
    "Redesign the app shell for a finance brand without changing the core product.",
    "Add a reports capability with export and approval routing.",
]

DEFAULT_STUDIO_CREATE_STATE = {
    "current_request": {
        "text": "",
        "request_kind": None,
        "change_class": None,
        "updated_at": None,
    },
    "current_plan": {
        "summary": None,
        "build_tasks": [],
        "owned_paths": [],
        "acceptance_criteria": [],
        "approvals_required": [],
        "cost_implications": [],
        "runtime_implications": [],
    },
    "recent_requests": [],
    "plan_state": "not_started",
    "approval_state": "not_started",
    "last_saved_at": None,
}

STUDIO_CREATE_STATE_COLLECTION = "StudioCreateState"


def get_missing_studio_surfaces(platform_root: Path) -> list[str]:
    app_prefix = "app"
    theme_rel = f"{app_prefix}/brand/theme_config.json"
    ui_rel = f"{app_prefix}/ui/route_manifest.json"
    checks = {
        f"{app_prefix}/app.json": platform_root / "app.json",
        f"{app_prefix}/config/ai.json": platform_root / "config" / "ai.json",
        f"{app_prefix}/config/shell.json": platform_root / "config" / "shell.json",
        theme_rel: _resolve_theme_config_path(platform_root),
        ui_rel: _resolve_ui_route_manifest_path(platform_root),
    }
    return [rel_path for rel_path, path in checks.items() if not path.exists()]


def build_studio_home_summary(
    platform_root: Path,
    *,
    surface: str = "cli-home",
    local_only: bool = True,
) -> dict:
    app_config = _read_json(platform_root / "app.json")
    ai_config = _read_json(platform_root / "config" / "ai.json")
    shell_config = _read_json(platform_root / "config" / "shell.json")
    theme_config = _read_json(_resolve_theme_config_path(platform_root))
    ui_route_manifest = _read_json(_resolve_ui_route_manifest_path(platform_root))

    admin_json_path = platform_root / "config" / "admin.json"
    admin_config = _read_json(admin_json_path) if admin_json_path.exists() else {}

    onboarding = app_config.get("onboarding") or {}
    llm = ai_config.get("llm") or {}
    identity = theme_config.get("identity") or {}
    workflow_names = _list_workflows(platform_root)
    workflow_count = len(workflow_names)
    custom_page_count = len(ui_route_manifest.get("pages") or [])
    schema_page_count = _count_schema_pages(platform_root)
    entry_point = (ai_config.get("workflows") or {}).get("entry_point")
    admin_emails = _resolve_admin_emails(app_config, admin_config)

    return {
        "studio": {
            "surface": surface,
            "local_only": local_only,
            "workspace_root": str(platform_root),
            "route": "/studio",
        },
        "app": {
            "name": app_config.get("appName") or platform_root.name,
            "preset": app_config.get("preset") or "unknown",
            "journey": onboarding.get("journey"),
            "first_goal": onboarding.get("first_goal"),
            "existing_app_url": onboarding.get("existing_app_url"),
            "host_owned_summary": onboarding.get("host_owned_summary"),
        },
        "ai": {
            "provider": llm.get("provider"),
            "model": llm.get("model"),
            "api_billed": llm.get("provider") in {"anthropic", "openai"},
        },
        "theme": {
            "primary": (theme_config.get("theme") or {}).get("primary"),
            "tagline": identity.get("tagline"),
            "logo_alt": ((shell_config.get("header") or {}).get("logo") or {}).get("alt"),
        },
        "shell": {
            "header_page_count": len(((shell_config.get("header") or {}).get("pages") or [])),
            "header_action_count": len(((shell_config.get("header") or {}).get("actions") or [])),
        },
        "admin": {
            "enabled": bool(admin_config.get("enabled")) if admin_config else False,
            "admin_emails": admin_emails,
        },
        "workspace": {
            "page_count": custom_page_count + schema_page_count,
            "custom_page_count": custom_page_count,
            "schema_page_count": schema_page_count,
            "workflow_count": workflow_count,
            "workflow_names": workflow_names,
            "entry_point": entry_point,
            "runtime_readiness": _runtime_readiness(workflow_count, entry_point),
        },
        "home": {
            "next_step": _recommend_next_step(
                onboarding=onboarding,
                provider=llm.get("provider"),
                model=llm.get("model"),
                admin_emails=admin_emails,
                workflow_count=workflow_count,
            )
        },
    }


def build_create_section(home_summary: dict, create_state: dict) -> dict:
    """Compose the Studio create response section from a home summary and build state.

    Used by both the HTTP endpoint and the CLI diagnostic path so the shape stays identical.
    """
    workflow_names = home_summary["workspace"].get("workflow_names") or []
    generator_workflows = {
        workflow_id: workflow_id in workflow_names
        for workflow_id in GENERATOR_WORKFLOW_IDS
    }

    if generator_workflows["ValueEngine"]:
        initial_compile_workflow = "ValueEngine"
    elif generator_workflows["AppGenerator"]:
        initial_compile_workflow = "AppGenerator"
    else:
        initial_compile_workflow = None

    refinement_support = {
        "patch": {
            "available": generator_workflows["AppGenerator"],
            "workflow_id": "AppGenerator" if generator_workflows["AppGenerator"] else None,
        },
        "design": {
            "available": generator_workflows["DesignDocs"],
            "workflow_id": "DesignDocs" if generator_workflows["DesignDocs"] else None,
        },
        "feature": {
            "available": generator_workflows["AppGenerator"],
            "workflow_id": "AppGenerator" if generator_workflows["AppGenerator"] else None,
        },
        "core": {
            "available": generator_workflows["ValueEngine"],
            "workflow_id": "ValueEngine" if generator_workflows["ValueEngine"] else None,
        },
    }

    return {
        "available_workflows": workflow_names,
        "generator_workflows": generator_workflows,
        "app_validation": build_app_validation_strategy_summary(),
        "supports_initial_compile": initial_compile_workflow is not None,
        "initial_compile_workflow": initial_compile_workflow,
        "refinement_support": refinement_support,
        "request_examples": BUILD_REQUEST_EXAMPLES,
        "current_request": create_state["current_request"],
        "current_plan": create_state["current_plan"],
        "recent_requests": create_state["recent_requests"],
        "plan_state": create_state["plan_state"],
        "approval_state": create_state["approval_state"],
        "last_saved_at": create_state["last_saved_at"],
    }


def build_studio_create_summary(
    platform_root: Path,
    *,
    surface: str = "shell-create",
    local_only: bool = True,
) -> dict:
    """CLI diagnostic path for workspace readiness and available build workflows."""
    summary = build_studio_home_summary(platform_root, surface=surface, local_only=local_only)
    create_state = _normalize_create_state({})
    summary["studio"] = {**summary["studio"], "surface": surface, "route": "/studio/create"}
    summary["create"] = build_create_section(summary, create_state)
    return summary


async def build_studio_adapters_summary() -> dict:
    import os

    def _mask(value: str, show: int = 6) -> str:
        if len(value) <= show:
            return "•" * len(value)
        return value[:show] + "•" * 8

    def _configured(value: str) -> bool:
        return bool(value and value.strip())

    openai_key = os.getenv("OPENAI_API_KEY", "")
    mongo_uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or os.getenv("MONGO_URL") or ""
    e2b_key = os.getenv("E2B_API_KEY", "")
    internal_key = os.getenv("INTERNAL_API_KEY", "")
    backend_url = os.getenv("MOZAIKS_BACKEND_URL", "")

    auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() in ("1", "true", "yes", "on")
    auth_provider = os.getenv("AUTH_PROVIDER", "")
    keycloak_url = os.getenv("KEYCLOAK_URL", "")
    keycloak_realm = os.getenv("KEYCLOAK_REALM", "")
    keycloak_client_id = os.getenv("KEYCLOAK_CLIENT_ID", "")
    supabase_url = os.getenv("SUPABASE_URL", "")
    auth_jwks_url = os.getenv("AUTH_JWKS_URL", "")
    azure_kv_name = os.getenv("AZURE_KEY_VAULT_NAME", "")

    default_model = os.getenv("DEFAULT_LLM_MODEL", "gpt-4o-mini")
    fallback_models = [model.strip() for model in os.getenv("OPENAI_MODEL_FALLBACK", "").split(",") if model.strip()]

    mongo_llm_configured = False
    try:
        from mozaiksai.core.core_config import get_mongo_client

        db_client = get_mongo_client()
        document = await db_client.autogen_ai_agents.LLMConfig.find_one({}, {"_id": 0, "model": 1})
        mongo_llm_configured = document is not None
    except Exception:
        pass

    adapters: dict = {
        "llm": {
            "label": "LLM Provider",
            "kind": "llm",
            "configured": _configured(openai_key) or mongo_llm_configured,
            "source": "database" if mongo_llm_configured else ("environment" if _configured(openai_key) else None),
            "primary_model": default_model,
            "fallback_models": fallback_models,
            "api_key_set": _configured(openai_key),
            "api_key_masked": _mask(openai_key) if openai_key else None,
        },
        "database": {
            "label": "MongoDB",
            "kind": "database",
            "configured": _configured(mongo_uri),
            "uri_masked": _mask(mongo_uri, 12) if mongo_uri else None,
        },
        "sandbox": {
            "label": "E2B Sandbox",
            "kind": "sandbox",
            "configured": _configured(e2b_key),
            "api_key_set": _configured(e2b_key),
            "api_key_masked": _mask(e2b_key) if e2b_key else None,
        },
        "auth": {
            "label": "Authentication",
            "kind": "auth",
            "configured": auth_enabled,
            "enabled": auth_enabled,
            "provider": auth_provider or None,
            "keycloak": {
                "url": keycloak_url or None,
                "realm": keycloak_realm or None,
                "client_id": keycloak_client_id or None,
            } if (keycloak_url or keycloak_realm) else None,
            "supabase": {"url": supabase_url} if supabase_url else None,
            "jwks_url": auth_jwks_url or None,
        },
        "backend": {
            "label": "App Backend",
            "kind": "backend",
            "configured": bool(backend_url or internal_key),
            "url": backend_url or None,
            "internal_key_set": _configured(internal_key),
            "internal_key_masked": _mask(internal_key) if internal_key else None,
        },
    }

    if azure_kv_name:
        adapters["azure_keyvault"] = {
            "label": "Azure Key Vault",
            "kind": "vault",
            "configured": True,
            "vault_name": azure_kv_name,
        }

    return {"adapters": adapters}


async def load_studio_create_state_from_db(app_id: str) -> dict:
    """Load Studio create-surface draft state from MongoDB."""
    try:
        from mozaiksai.core.core_config import get_mongo_client
        client = get_mongo_client()
        coll = client["mozaiksai"][STUDIO_CREATE_STATE_COLLECTION]
        raw: Any = await coll.find_one({"_id": app_id})
        if isinstance(raw, dict):
            raw.pop("_id", None)
            return _normalize_create_state(raw)
    except Exception:
        pass
    return _normalize_create_state({})


async def save_studio_create_state_to_db(
    app_id: str,
    *,
    request_text: str,
    request_kind: str | None,
    change_class: str | None = None,
) -> dict:
    """Persist Studio create-surface draft state to MongoDB."""
    from mozaiksai.core.core_config import get_mongo_client

    current_state = await load_studio_create_state_from_db(app_id)
    normalized_kind = _normalize_request_kind(request_kind)
    normalized_class = _normalize_change_class(change_class)
    timestamp = _utc_now_iso()
    normalized_text = (request_text or "").strip()

    current_state["current_request"] = {
        "text": normalized_text,
        "request_kind": normalized_kind,
        "change_class": normalized_class,
        "updated_at": timestamp,
    }
    current_state["last_saved_at"] = timestamp

    if normalized_text:
        current_state["plan_state"] = "draft_saved"
        entry = {
            "text": normalized_text,
            "request_kind": normalized_kind,
            "change_class": normalized_class,
            "saved_at": timestamp,
        }
        recent = [
            item
            for item in current_state["recent_requests"]
            if not (
                item.get("text") == entry["text"]
                and item.get("request_kind") == entry["request_kind"]
                and item.get("change_class") == entry["change_class"]
            )
        ]
        current_state["recent_requests"] = [entry, *recent][:8]
    else:
        current_state["plan_state"] = "not_started"

    client = get_mongo_client()
    coll = client["mozaiksai"][STUDIO_CREATE_STATE_COLLECTION]
    await coll.update_one(
        {"_id": app_id},
        {"$set": {**current_state, "_id": app_id}},
        upsert=True,
    )
    return await load_studio_create_state_from_db(app_id)


def _normalize_create_state(raw: dict) -> dict:
    current_plan = _normalize_current_plan(raw.get("current_plan"))
    current_request = _normalize_current_request(raw.get("current_request"))
    recent_requests = _normalize_recent_requests(raw.get("recent_requests"))

    raw_plan_state = raw.get("plan_state")
    if isinstance(raw_plan_state, str) and raw_plan_state.strip():
        plan_state = raw_plan_state.strip()
    elif current_plan["build_tasks"] or current_plan["summary"] or current_plan["owned_paths"]:
        plan_state = "plan_ready"
    elif current_request["text"]:
        plan_state = "draft_saved"
    else:
        plan_state = DEFAULT_STUDIO_CREATE_STATE["plan_state"]

    raw_approval = raw.get("approval_state")
    approval_state = raw_approval.strip() if isinstance(raw_approval, str) and raw_approval.strip() else DEFAULT_STUDIO_CREATE_STATE["approval_state"]
    raw_saved = raw.get("last_saved_at")
    last_saved_at = raw_saved if isinstance(raw_saved, str) and raw_saved.strip() else current_request["updated_at"]

    return {
        "current_request": current_request,
        "current_plan": current_plan,
        "recent_requests": recent_requests,
        "plan_state": plan_state,
        "approval_state": approval_state,
        "last_saved_at": last_saved_at,
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_theme_config_path(platform_root: Path) -> Path:
    candidates = [
        platform_root / "brand" / "theme_config.json",
    ]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def _resolve_ui_route_manifest_path(platform_root: Path) -> Path:
    candidates = [
        platform_root / "ui" / "route_manifest.json",
    ]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def _normalize_request_kind(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized in {"new_app", "existing_app", "refinement"} else None


def _normalize_change_class(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized in {"patch", "design", "feature", "core"} else None


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                normalized.append(stripped)
    return normalized


def _flatten_unique_strings(values: list[list[str]]) -> list[str]:
    flattened: list[str] = []
    for group in values:
        for item in group:
            if item not in flattened:
                flattened.append(item)
    return flattened


def _normalize_build_tasks(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []

    normalized_tasks: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        task = dict(item)
        task["owned_paths"] = _normalize_string_list(item.get("owned_paths"))
        task["depends_on"] = _normalize_string_list(item.get("depends_on"))
        task["acceptance_criteria"] = _normalize_string_list(item.get("acceptance_criteria"))
        for key in (
            "task_id",
            "task_type",
            "capability_pack_id",
            "execution_target",
            "initial_agent",
            "description",
            "initial_message",
        ):
            value_for_key = task.get(key)
            if value_for_key is None or isinstance(value_for_key, str):
                continue
            task[key] = str(value_for_key)
        normalized_tasks.append(task)

    return normalized_tasks


def _normalize_current_request(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    text = raw.get("text") if isinstance(raw.get("text"), str) else ""
    updated_at = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) and raw.get("updated_at") else None
    return {
        "text": text.strip(),
        "request_kind": _normalize_request_kind(raw.get("request_kind")),
        "change_class": _normalize_change_class(raw.get("change_class")),
        "updated_at": updated_at,
    }


def _normalize_current_plan(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    build_tasks = _normalize_build_tasks(raw.get("build_tasks"))
    owned_paths = _normalize_string_list(raw.get("owned_paths")) or _flatten_unique_strings(
        [task.get("owned_paths") or [] for task in build_tasks]
    )
    acceptance_criteria = _normalize_string_list(raw.get("acceptance_criteria")) or _flatten_unique_strings(
        [task.get("acceptance_criteria") or [] for task in build_tasks]
    )
    summary = raw.get("summary") if isinstance(raw.get("summary"), str) and raw.get("summary").strip() else None
    return {
        "summary": summary,
        "build_tasks": build_tasks,
        "owned_paths": owned_paths,
        "acceptance_criteria": acceptance_criteria,
        "approvals_required": _normalize_string_list(raw.get("approvals_required")),
        "cost_implications": _normalize_string_list(raw.get("cost_implications")),
        "runtime_implications": _normalize_string_list(raw.get("runtime_implications")),
    }


def _normalize_recent_requests(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []

    recent_requests: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text") if isinstance(item.get("text"), str) else ""
        normalized_text = text.strip()
        if not normalized_text:
            continue
        saved_at = item.get("saved_at") if isinstance(item.get("saved_at"), str) and item.get("saved_at") else None
        recent_requests.append(
            {
                "text": normalized_text,
                "request_kind": _normalize_request_kind(item.get("request_kind")),
                "change_class": _normalize_change_class(item.get("change_class")),
                "saved_at": saved_at,
            }
        )
    return recent_requests[:8]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _list_workflows(platform_root: Path) -> list[str]:
    discovered: list[str] = []
    workflows_dir = platform_root / "workflows"
    if workflows_dir.exists():
        for child in workflows_dir.iterdir():
            if child.is_dir() and child.name != "extended_orchestration" and child.name not in discovered:
                discovered.append(child.name)

    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        for name in workflow_manager.get_all_workflow_names():
            if isinstance(name, str) and name not in discovered:
                discovered.append(name)
    except Exception:
        pass

    return sorted(discovered, key=str.lower)


def _count_workflows(platform_root: Path) -> int:
    return len(_list_workflows(platform_root))


def _count_schema_pages(platform_root: Path) -> int:
    pages_dir = platform_root / "ui" / "pages"
    if not pages_dir.exists():
        return 0

    count = 0
    for child in pages_dir.iterdir():
        if child.is_file() and child.suffix.lower() in {".yaml", ".yml"}:
            count += 1
        elif child.is_dir() and ((child / "page.yaml").exists() or (child / "page.yml").exists()):
            count += 1
    return count


def _resolve_admin_emails(app_config: dict, admin_config: dict) -> list[str]:
    admin_emails = admin_config.get("admin_emails") or []
    if admin_emails:
        return admin_emails
    admins = app_config.get("admins") or []
    return admins if isinstance(admins, list) else []


def _runtime_readiness(workflow_count: int, entry_point: str | None) -> str:
    if workflow_count == 0:
        return "no_workflows"
    if not entry_point:
        return "workflows_present_no_entry_point"
    return "entry_point_configured"


def _recommend_next_step(
    *,
    onboarding: dict,
    provider: str | None,
    model: str | None,
    admin_emails: list[str],
    workflow_count: int,
) -> str:
    if not onboarding.get("journey") or not onboarding.get("first_goal"):
        return "Run 'mozaiks onboard' so Studio Home has product intent, provider defaults, and admin bootstrap."
    if not provider or not model:
        return "Confirm your default provider and model in app/config/ai.json before starting build work."
    if not admin_emails:
        return "Add a local admin email with 'mozaiks onboard --admin-email <email>' before opening admin workflows."
    if workflow_count == 0:
        if onboarding.get("journey") == "existing_app":
            return "Connect the first host-owned surface or submit the first build request from this workspace."
        return "Submit the first build request or add the first workflow before you expand the app surface."
    return "Review the current workspace state and make the next approved build request from Studio Home."
