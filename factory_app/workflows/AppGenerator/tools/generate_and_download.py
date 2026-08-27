"""
generate_and_download - Bundle generated app code and present AppWorkbench + DownloadCenter UI.

This tool:
1) Collects latest agent JSON outputs for the chat/app
2) Extracts `code_files` from any agent output
3) Writes files under generated/apps/<app_id>/<build_id>/app/
4) Creates a ZIP bundle
5) Presents AppWorkbench export actions and (optionally) triggers export_to_github
"""


import json
import os
import re
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from pydantic import Field

from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    collect_generated_app_file_map,
    extract_code_file_map_from_payload,
    extract_deleted_file_paths_from_payload,
)
from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    PRODUCTION_DEPLOYMENT_PROFILES,
    generate_deployment_artifacts,
)
from factory_app.workflows.AppGenerator.tools.export_app_code import (
    export_app_code_to_github,
    resolve_export_gate,
)
from factory_app.workflows.AppGenerator.tools.module_api_template import get_module_api_template
from factory_app.workflows.AppGenerator.tools.requirements_scanner import scan_requirements
from factory_app.workflows.AppGenerator.tools.schema_migration import inject_migration_into_bundle
from factory_app.workflows.AppGenerator.tools.update_app_record import update_build_status
from logs.logging_config import get_workflow_logger
from mozaiksai.core.app_context.store import register_greenfield_app_context_version
from mozaiksai.core.workflow.generator_support.agent_endpoints import (
    resolve_agent_api_url,
    resolve_agent_websocket_url,
)
from mozaiksai.core.workflow.generator_support.workflow_exports import get_latest_workflow_export
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool

try:
    from logs.tools_logs import get_tool_logger as _get_tool_logger  # type: ignore
    from logs.tools_logs import log_tool_event as _log_tool_event
except Exception:  # pragma: no cover
    _get_tool_logger = None  # type: ignore
    _log_tool_event = None  # type: ignore

BuilderArtifactStore = None
AG2PersistenceManager = None


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _resolve_generated_artifacts_root() -> Path:
    raw = os.getenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", "generated").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


def _safe_path_segment(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return text or fallback


def _resolve_app_output_dir(*, app_id: Any, build_id: Any) -> Path:
    return (
        _resolve_generated_artifacts_root()
        / "apps"
        / _safe_path_segment(app_id, fallback="local-app")
        / _safe_path_segment(build_id, fallback="local-build")
        / "app"
    )


def _builder_artifact_store():
    global BuilderArtifactStore
    if BuilderArtifactStore is None:
        from mozaiksai.core.data.persistence.artifact_store import (
            BuilderArtifactStore as _BuilderArtifactStore,
        )

        BuilderArtifactStore = _BuilderArtifactStore
    return BuilderArtifactStore()


def _ag2_persistence_manager():
    global AG2PersistenceManager
    if AG2PersistenceManager is None:
        from mozaiksai.core.data.persistence.persistence_manager import (
            AG2PersistenceManager as _AG2PersistenceManager,
        )

        AG2PersistenceManager = _AG2PersistenceManager
    return AG2PersistenceManager()


def _safe_relpath(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    path = raw.replace("\\", "/").strip()
    if not path or path.startswith("/"):
        return None
    p = PurePosixPath(path)
    if p.is_absolute():
        return None
    if any(part in {".."} for part in p.parts):
        return None
    return str(p)


def _discover_code_files(col: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for _agent_name, data in (col or {}).items():
        try:
            if isinstance(data, dict):
                out.update(extract_code_file_map_from_payload(data))
            elif isinstance(data, str):
                try:
                    parsed = json.loads(data)
                    if isinstance(parsed, dict):
                        out.update(extract_code_file_map_from_payload(parsed))
                except Exception:
                    pass
        except Exception:
            continue
    return out


def _discover_deleted_files(col: dict[str, Any]) -> list[str]:
    deleted: list[str] = []
    seen: set[str] = set()
    for _agent_name, data in (col or {}).items():
        if not isinstance(data, dict):
            continue
        for path in extract_deleted_file_paths_from_payload(data):
            if path in seen:
                continue
            seen.add(path)
            deleted.append(path)
    return deleted


def _context_get(context_variables: Any | None, key: str) -> Any | None:
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key)
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    return None


def _pack_id(pack: dict[str, Any]) -> str:
    return str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "").strip()


def _selected_capability_packs(context_variables: Any | None) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    def merge_missing(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            current = target.get(key)
            if current in (None, "", [], {}):
                target[key] = value
            elif isinstance(current, dict) and isinstance(value, dict):
                merge_missing(current, value)

    def add_many(raw_packs: Any) -> None:
        if not isinstance(raw_packs, list):
            return
        for pack in raw_packs:
            if not isinstance(pack, dict):
                continue
            pack_id = _pack_id(pack)
            if not pack_id:
                continue
            key = (
                pack_id,
                str(pack.get("capability_source") or "").strip(),
                str(pack.get("pack_source_path") or "").strip(),
            )
            if key in selected_by_key:
                merge_missing(selected_by_key[key], pack)
                continue
            normalized = dict(pack)
            selected_by_key[key] = normalized
            selected.append(normalized)

    app_build_plan = _context_get(context_variables, "app_build_plan")
    if isinstance(app_build_plan, dict):
        add_many(app_build_plan.get("capability_packs"))
    add_many(_context_get(context_variables, "capability_packs"))
    return selected


def _deployment_env_names(value: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(value, list):
        return names
    for item in value:
        raw_name: Any
        if isinstance(item, str):
            raw_name = item
        elif isinstance(item, dict):
            raw_name = item.get("name") or item.get("variable") or item.get("env")
        else:
            continue
        name = str(raw_name or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _deployment_env_for_capability_packs(capability_packs: list[dict[str, Any]]) -> dict[str, list[str]]:
    required: list[str] = []
    optional: list[str] = []
    secret: list[str] = []
    public: list[str] = []

    def add_required(name: str) -> None:
        if name not in required:
            required.append(name)

    def add_optional(name: str) -> None:
        if name not in optional:
            optional.append(name)

    def add_secret(name: str) -> None:
        if name not in secret:
            secret.append(name)

    def add_public(name: str) -> None:
        if name not in public:
            public.append(name)

    for pack in capability_packs:
        deployment_env = pack.get("deployment_env")
        if not isinstance(deployment_env, dict):
            continue
        for name in _deployment_env_names(deployment_env.get("required")):
            add_required(name)
        for name in _deployment_env_names(deployment_env.get("optional")):
            add_optional(name)
        for name in _deployment_env_names(deployment_env.get("secret")):
            add_secret(name)
            if name not in required:
                add_optional(name)
        for name in _deployment_env_names(deployment_env.get("public")):
            add_public(name)

    return {"required": required, "optional": optional, "secret": secret, "public": public}


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None or not hasattr(context_variables, "set"):
        return
    try:
        context_variables.set(key, value)
    except Exception:
        return


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "passed", "ready"}
    return bool(value)


def _discover_context_files(context_variables: Any | None) -> dict[str, str]:
    raw = _context_get(context_variables, "generated_files")
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for rel_path, content in raw.items():
            safe = _safe_relpath(str(rel_path))
            if safe:
                out[safe] = str(content)
    out.update(extract_code_file_map_from_payload({"code_files": _context_get(context_variables, "code_files")}))
    return _apply_deleted_files(out, _context_deleted_files(context_variables))


def _context_deleted_files(context_variables: Any | None) -> list[str]:
    return extract_deleted_file_paths_from_payload({"deleted_files": _context_get(context_variables, "deleted_files")})


def _apply_deleted_files(files_map: dict[str, str], deleted_files: list[str]) -> dict[str, str]:
    if not deleted_files:
        return files_map
    merged = dict(files_map)
    for path in deleted_files:
        merged.pop(path, None)
    return merged


def _merge_bundle_sources(
    *,
    context_variables: Any | None,
    collected: dict[str, Any],
) -> dict[str, str]:
    files_map = _discover_context_files(context_variables)
    persisted_files = _discover_code_files(collected)
    if persisted_files:
        files_map.update(persisted_files)
    return _apply_deleted_files(
        files_map,
        [
            *_context_deleted_files(context_variables),
            *_discover_deleted_files(collected),
        ],
    )


def _generated_app_auth_required(files_map: dict[str, str]) -> bool:
    """Return whether the generated app manifest requires authentication."""
    for manifest_path in ("app.json", "app/app.json"):
        raw = files_map.get(manifest_path)
        if not raw:
            continue
        try:
            parsed = json.loads(str(raw))
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        if isinstance(parsed.get("authRequired"), bool):
            return bool(parsed["authRequired"])
    return False


def _format_bytes(num: int) -> str:
    try:
        value: float = float(num)
        for unit in ["bytes", "KB", "MB", "GB", "TB"]:
            if value < 1024 or unit == "TB":
                if unit == "bytes":
                    return f"{int(value)} bytes"
                return f"{value:.1f} {unit}"
            value /= 1024.0
    except Exception:
        return f"{num} bytes"
    return f"{num} bytes"


def _content_type_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".css": "text/css",
        ".html": "text/html",
        ".js": "text/javascript",
        ".json": "application/json",
        ".jsx": "text/javascript",
        ".md": "text/markdown",
        ".py": "text/x-python",
        ".ts": "text/typescript",
        ".tsx": "text/typescript",
        ".txt": "text/plain",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
    }.get(suffix, "application/octet-stream")


def _build_generated_files_manifest(
    *,
    bundle_name: str,
    app_dir: Path,
    written_paths: list[str],
) -> list[dict[str, Any]]:
    import hashlib

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in sorted(written_paths):
        safe = _safe_relpath(raw_path)
        if not safe or safe in seen:
            continue
        seen.add(safe)
        file_path = app_dir / safe
        if not file_path.exists() or not file_path.is_file():
            continue
        raw = file_path.read_bytes()
        manifest_path = safe.replace("\\", "/")
        entries.append(
            {
                "path": f"{bundle_name}/{manifest_path}",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "content_type": _content_type_for_path(safe),
            }
        )
    return entries


def _ensure_env_line(lines: list[str], key: str, value: str) -> list[str]:
    normalized_key = key.strip()
    if not normalized_key:
        return lines
    rendered = f"{normalized_key}={value or ''}"

    out: list[str] = []
    replaced = False
    for line in lines:
        if not isinstance(line, str):
            continue
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        k = stripped.split("=", 1)[0].strip()
        if k == normalized_key:
            out.append(rendered)
            replaced = True
        else:
            out.append(line)

    if not replaced:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(rendered)
    return out


async def _inject_agent_context_env(*, files_map: dict[str, str], app_id: str, context_variables: Any | None) -> None:
    """Best-effort: ensure bundle contains agent endpoint env placeholders."""

    agent_ws = None
    agent_api = None

    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            agent_ws = context_variables.get("agent_websocket_url")
            agent_api = context_variables.get("agent_api_url")
        except Exception:
            agent_ws = None
            agent_api = None

    if not agent_ws or not agent_api:
        try:
            rec = await get_latest_workflow_export(app_id=app_id, workflow_type="agent-generator")
        except Exception:
            rec = None
        if isinstance(rec, dict):
            agent_ws = agent_ws or rec.get("agent_websocket_url")
            agent_api = agent_api or rec.get("agent_api_url")

    agent_ws = agent_ws or resolve_agent_websocket_url(app_id)
    agent_api = agent_api or resolve_agent_api_url(app_id)

    raw_env = files_map.get(".env.example") or ""
    lines = raw_env.splitlines()
    needs_guidance = not agent_ws or not agent_api
    guidance_marker = "# MozaiksAI: Agent endpoint configuration"
    if needs_guidance and guidance_marker not in raw_env:
        lines = [
            guidance_marker,
            "# VITE_AGENT_WEBSOCKET_URL / VITE_AGENT_API_URL are blank because this runtime could not resolve agent endpoints.",
            "# Fix: set MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE and MOZAIKS_AGENT_API_URL_TEMPLATE in MozaiksAI `.env`,",
            "# or run AgentGenerator export to record endpoints into WorkflowExports, then regenerate this bundle.",
            "",
            *lines,
        ]
    lines = _ensure_env_line(lines, "VITE_APP_ID", str(app_id))
    lines = _ensure_env_line(lines, "VITE_AGENT_WEBSOCKET_URL", str(agent_ws or ""))
    lines = _ensure_env_line(lines, "VITE_AGENT_API_URL", str(agent_api or ""))
    files_map[".env.example"] = "\n".join(lines).rstrip() + "\n"


async def _emit_deployment_event(*, chat_id: str | None, status: str, data: dict) -> None:
    if not chat_id:
        return
    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        await transport.send_event_to_ui(
            {
                "schema_version": "mozaiks.ui.event.v1",
                "type": f"chat.deployment_{status}",
                "data": {"timestamp": datetime.now(UTC).isoformat(), **data},
            },
            chat_id,
        )
    except Exception:
        return


async def _persist_pending_schema_migration(
    *,
    pending_migration: dict[str, Any] | None,
    app_id: str,
    build_id: str,
    workflow_name: str,
    chat_id: str | None,
    context_variables: Any | None,
    generated_app_dir: str,
) -> dict[str, Any] | None:
    if not isinstance(pending_migration, dict):
        return None
    migration_id = str(pending_migration.get("migration_id") or "").strip()
    if not migration_id:
        return None

    migration_path = (
        Path(generated_app_dir)
        / "data"
        / "migrations"
        / f"{migration_id}.json"
    )
    migration_path.parent.mkdir(parents=True, exist_ok=True)
    migration_path.write_text(
        json.dumps(pending_migration, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    artifact_version_id = None
    change_class = None
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            artifact_version_id = context_variables.get("artifact_version_id")
            change_class = context_variables.get("revision_scope")
        except Exception:
            artifact_version_id = None
            change_class = None

    store = _builder_artifact_store()
    record = await store.save_database_migration(
        app_id=str(app_id),
        build_id=str(build_id),
        artifact_version_id=str(artifact_version_id) if artifact_version_id else None,
        change_class=str(change_class) if change_class else None,
        migration=pending_migration,
        status="staged",
        source_workflow=workflow_name,
        source_chat_id=str(chat_id) if chat_id else None,
        generated_app_dir=generated_app_dir,
    )
    if context_variables is not None and hasattr(context_variables, "set"):
        try:
            context_variables.set("persisted_database_migration", record)
            context_variables.set(
                "staged_database_migration_path",
                f"data/migrations/{migration_id}.json",
            )
        except Exception:
            pass
    return record  # type: ignore[no-any-return]


async def _register_greenfield_app_context_for_bundle(
    *,
    app_bundle_artifact: Any,
    artifact_store: Any,
    files_manifest: list[dict[str, Any]],
    workflow_name: str,
    chat_id: str | None,
    context_variables: Any | None,
    raise_on_contract_error: bool = False,
) -> Any | None:
    try:
        registered = await register_greenfield_app_context_version(
            app_bundle_artifact=app_bundle_artifact,
            artifact_store=artifact_store,
            files_manifest=files_manifest,
            source_workflow=workflow_name,
            source_chat_id=chat_id,
            make_current=True,
        )
    except (TypeError, ValueError) as exc:
        if raise_on_contract_error:
            raise
        wf_logger = get_workflow_logger(
            workflow_name=workflow_name,
            chat_id=chat_id,
            app_id=getattr(app_bundle_artifact, "app_id", None),
        )
        wf_logger.warning("Greenfield app-context registration skipped: %s", exc)
        _context_set(context_variables, "app_context_registration_warning", str(exc))
        return None
    except Exception as exc:
        wf_logger = get_workflow_logger(
            workflow_name=workflow_name,
            chat_id=chat_id,
            app_id=getattr(app_bundle_artifact, "app_id", None),
        )
        wf_logger.warning("Greenfield app-context registration failed: %s", exc)
        _context_set(context_variables, "app_context_registration_warning", str(exc))
        return None

    _context_set(context_variables, "app_context_version_id", registered.context_version.context_version_id)
    _context_set(context_variables, "app_context_artifact_version_id", registered.artifact_version.id)
    _context_set(context_variables, "greenfield_app_context_registered", True)
    return registered


async def _register_app_bundle_artifact_version(
    *,
    app_id: str,
    user_id: str | None,
    workflow_name: str,
    chat_id: str | None,
    bundle_name: str,
    zip_path: Path,
    app_dir: Path | None = None,
    written_paths: list[str] | None = None,
    context_variables: Any | None,
):
    try:
        import hashlib

        from mozaiksai.core.artifacts import (
            ArtifactLifecycleStatus,
            ArtifactValidationStatus,
            get_artifact_store,
            resolve_latest_artifact_version_refs,
        )
        from mozaiksai.core.artifacts.content_store import get_artifact_content_store
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Artifact store dependencies unavailable: {exc}") from exc

    parent_version_id = None
    validation_status_raw = None
    lifecycle_status = None
    app_validation_status = None
    app_validation_strategy = None
    sandbox_session_id = None
    sandbox_provider = None
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            parent_version_id = context_variables.get("artifact_version_id")
            validation_status_raw = context_variables.get("app_bundle_acceptance_status")
            # Sandbox build-validation outcome, persisted first-class so
            # "which builds passed e2b/docker validation" is queryable
            # instead of buried in the commit_metadata blob.
            app_validation_status = context_variables.get("app_validation_status")
            app_validation_strategy = context_variables.get("app_validation_strategy_used")
            validation_result = context_variables.get("app_validation_result")
            if isinstance(validation_result, dict):
                sandbox_session_id = validation_result.get("sandbox_session_id")
                sandbox_provider = validation_result.get("sandbox_provider")
        except Exception:
            parent_version_id = None
            validation_status_raw = None

    lifecycle_status = (
        ArtifactLifecycleStatus.DRAFT
        if parent_version_id
        else ArtifactLifecycleStatus.CURRENT
    )

    normalized_status = str(validation_status_raw or "").strip().lower()
    validation_status = ArtifactValidationStatus.PENDING
    if normalized_status == "passed":
        validation_status = ArtifactValidationStatus.PASSED
    elif normalized_status == "failed":
        validation_status = ArtifactValidationStatus.FAILED

    raw = zip_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    generated_files_manifest = (
        _build_generated_files_manifest(
            bundle_name=bundle_name,
            app_dir=app_dir,
            written_paths=written_paths or [],
        )
        if app_dir is not None and written_paths
        else []
    )
    files_manifest = [
        {
            "path": f"{bundle_name}/{zip_path.name}",
            "sha256": sha,
            "size_bytes": zip_path.stat().st_size,
            "content_type": "application/zip",
        },
        *generated_files_manifest,
    ]

    # Persist to content store if a non-local backend is configured.  The local
    # backend is skipped because artifact_path already covers that path.
    bundle_content_metadata: dict[str, Any] = {
        "artifact_path": str(zip_path.resolve()),
        "bundle_name": bundle_name,
    }
    if app_dir is not None:
        bundle_content_metadata["workspace_dir"] = str(app_dir.resolve())
    build_registry_id = _context_get(context_variables, "build_registry_id")
    if build_registry_id:
        bundle_content_metadata["build_registry_id"] = str(build_registry_id)
    build_id = _context_get(context_variables, "build_id")
    if build_id:
        bundle_content_metadata["build_id"] = str(build_id)
    # Include the carry-forward preservation report in artifact metadata when present.
    cf_report = None
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            cf_report = context_variables.get("carry_forward_report")
        except Exception:
            pass
    if isinstance(cf_report, dict):
        bundle_content_metadata["carry_forward_report"] = cf_report
    acceptance_result = _context_get(context_variables, "app_bundle_acceptance_result")
    validation_evidence = _context_get(context_variables, "app_bundle_validation_evidence")
    if isinstance(acceptance_result, dict):
        bundle_content_metadata["app_bundle_acceptance"] = acceptance_result
    if isinstance(validation_evidence, dict):
        bundle_content_metadata["validation_evidence"] = validation_evidence
    app_validation_result = _context_get(context_variables, "app_validation_result")
    if isinstance(app_validation_result, dict):
        bundle_content_metadata["app_validation_result"] = app_validation_result
    integration_test_result = _context_get(context_variables, "integration_test_result")
    if isinstance(integration_test_result, dict):
        bundle_content_metadata["integration_test_result"] = integration_test_result
    if generated_files_manifest:
        bundle_content_metadata["accepted_file_manifest"] = generated_files_manifest
    content_store = get_artifact_content_store()
    if content_store.backend_name != "local":
        try:
            # artifact_version_id is assigned after persistence; use a deterministic pending key.
            content_ref = await content_store.put_bundle(
                raw,
                app_id=str(app_id),
                artifact_version_id=f"pending_{sha[:16]}",
            )
            bundle_content_metadata["content_ref"] = content_ref
            bundle_content_metadata["content_backend"] = content_store.backend_name
        except Exception as cs_exc:
            wf_logger = get_workflow_logger(workflow_name=workflow_name, app_id=app_id)
            wf_logger.warning("Content store put_bundle failed; falling back to local path: %s", cs_exc)

    artifact_store = get_artifact_store()
    canonical_inputs_version = await resolve_latest_artifact_version_refs(
        app_id=str(app_id),
        artifact_kinds=("concept", "design_docs", "workflow_bundle", "theme_capture"),
        artifact_store=artifact_store,
    )
    artifact_version = await artifact_store.create_build_record(
        app_id=str(app_id),
        build_family="app_bundle",
        build_key="app_bundle",
        parent_build_record_id=str(parent_version_id) if parent_version_id else None,
        canonical_inputs_version=canonical_inputs_version,
        files_manifest=files_manifest,
        source_workflow=workflow_name,
        source_chat_id=chat_id,
        lifecycle_status=lifecycle_status,
        validation_status=validation_status,
        app_validation_status=str(app_validation_status) if app_validation_status else None,
        app_validation_strategy=str(app_validation_strategy) if app_validation_strategy else None,
        sandbox_session_id=str(sandbox_session_id) if sandbox_session_id else None,
        sandbox_provider=str(sandbox_provider) if sandbox_provider else None,
        commit_metadata={
            "message": f"{workflow_name}: {bundle_name}",
            "author_user_id": user_id,
            "source_workflow": workflow_name,
            "source_chat_id": chat_id,
            "metadata": bundle_content_metadata,
        },
    )
    if context_variables is not None and hasattr(context_variables, "set"):
        try:
            context_variables.set("artifact_version_id", artifact_version.id)
        except Exception:
            pass
    await _register_greenfield_app_context_for_bundle(
        app_bundle_artifact=artifact_version,
        artifact_store=artifact_store,
        files_manifest=files_manifest,
        workflow_name=workflow_name,
        chat_id=chat_id,
        context_variables=context_variables,
    )
    return artifact_version


async def generate_and_download(
    DownloadRequest: Annotated[
        dict[str, Any],
        Field(description="Download configuration with confirmation_only and storage_backend options."),
    ],
    agent_message: Annotated[
        str,
        Field(description="Concise message, up to 140 chars, shown to the user in the download UI."),
    ],
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> dict[str, Any]:
    confirmation_only = bool((DownloadRequest or {}).get("confirmation_only", False))
    storage_backend = (DownloadRequest or {}).get("storage_backend", "none")
    description = (DownloadRequest or {}).get("description")
    include_dockerfiles = _is_truthy((DownloadRequest or {}).get("include_dockerfiles"))
    include_workflow = _is_truthy((DownloadRequest or {}).get("include_workflow"))
    include_compose = _is_truthy((DownloadRequest or {}).get("include_compose"))
    deployment_profile = str((DownloadRequest or {}).get("deployment_profile") or "").strip() or None

    chat_id: str | None = None
    app_id: str | None = None
    user_id: str | None = None
    build_registry_id: str | None = None
    build_id: str | None = None
    workflow_name = "AppGenerator"
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            chat_id = context_variables.get("chat_id")
            app_id = context_variables.get("app_id")
            user_id = context_variables.get("user_id")
            build_registry_id = context_variables.get("build_registry_id")
            build_id = context_variables.get("build_id")
            workflow_name = context_variables.get("workflow_name") or workflow_name
        except Exception:
            pass

    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id, app_id=app_id)
    tlog = None
    if _get_tool_logger:  # type: ignore[truthy-function]
        try:
            tlog = _get_tool_logger(tool_name="GenerateAndDownloadApp", chat_id=chat_id, app_id=app_id, workflow_name=workflow_name)
        except Exception:
            tlog = None

    agent_message_text = agent_message or description or "Your app bundle is ready to download."
    agent_message_id = f"msg_{uuid.uuid4().hex[:10]}"

    if not chat_id or not app_id:
        return {"status": "error", "message": "chat_id and app_id are required"}

    pm = _ag2_persistence_manager()
    collected = await pm.gather_latest_agent_jsons(chat_id=chat_id, app_id=app_id)
    files_map = _merge_bundle_sources(
        context_variables=context_variables,
        collected=collected,
    )
    if not files_map and _is_truthy(_context_get(context_variables, "app_schema_ready")):
        files_map = collect_generated_app_file_map(
            _context_get(context_variables, "generated_app_dir")
        )
        files_map.update(extract_code_file_map_from_payload({"code_files": _context_get(context_variables, "code_files")}))
        files_map = _apply_deleted_files(
            files_map,
            [
                *_context_deleted_files(context_variables),
                *_discover_deleted_files(collected),
            ],
        )
        if files_map and context_variables is not None and hasattr(context_variables, "set"):
            try:
                context_variables.set("generated_files", files_map)
            except Exception:
                pass
    if not files_map:
        return {"status": "error", "message": "No code_files found to bundle."}

    # Merge Phase 7A carry-forward preserved declarative files.
    # These were written to context["carry_forward_additions"] by the resolver.
    # Generated output (files_map) wins — only fill paths not already present.
    carry_forward_additions = _context_get(context_variables, "carry_forward_additions")
    if isinstance(carry_forward_additions, dict) and carry_forward_additions:
        for cf_path, cf_content in carry_forward_additions.items():
            safe_cf = _safe_relpath(cf_path)
            if safe_cf and safe_cf not in files_map:
                files_map[safe_cf] = str(cf_content)

    await _inject_agent_context_env(files_map=files_map, app_id=str(app_id), context_variables=context_variables)

    # Inject requirements.txt if the agents did not produce one.
    if "requirements.txt" not in files_map:
        files_map["requirements.txt"] = scan_requirements(files_map)

    # Inject the canonical moduleApi.js helper if the agents did not produce one.
    # Custom-route JSX files import moduleAction from this path. The template
    # includes structured error body parsing so callers can branch on error_code.
    if "ui/lib/moduleApi.js" not in files_map:
        files_map["ui/lib/moduleApi.js"] = get_module_api_template()

    # Inject any pending migration file produced by DatabaseAgent during refinement.
    pending_migration: dict[str, Any] | None = None
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            pending_migration = context_variables.get("pending_schema_migration")
        except Exception:
            pending_migration = None
    if isinstance(pending_migration, dict) and pending_migration.get("migration_id"):
        inject_migration_into_bundle(files_map, pending_migration)

    selected_capability_packs = _selected_capability_packs(context_variables)
    deployment_env = _deployment_env_for_capability_packs(selected_capability_packs)

    # Optional deterministic deployment scaffold outputs.
    # These remain provider-neutral and contain non-secret example env values only.
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            include_dockerfiles = include_dockerfiles or _is_truthy(context_variables.get("includeDockerfiles"))
            include_workflow = include_workflow or _is_truthy(context_variables.get("includeWorkflow"))
            include_compose = include_compose or _is_truthy(context_variables.get("includeCompose"))
            deployment_profile = deployment_profile or context_variables.get("deployment_profile")
        except Exception:
            pass
    deployment_profile = str(deployment_profile or "generic_container")
    production_deployment_profile = deployment_profile.strip().lower() in PRODUCTION_DEPLOYMENT_PROFILES
    if production_deployment_profile:
        include_dockerfiles = True
    if include_dockerfiles or include_workflow or include_compose or production_deployment_profile:
        deployment_contract = generate_deployment_artifacts(
            app_id=str(app_id),
            deployment_profile=deployment_profile,
            include_dockerfiles=include_dockerfiles,
            include_workflow=include_workflow,
            include_compose=include_compose,
            auth_required=_generated_app_auth_required(files_map),
            extra_required_variables=deployment_env["required"],
            extra_optional_variables=deployment_env["optional"],
            extra_secret_variables=deployment_env["secret"],
            extra_public_variables=deployment_env["public"],
        )
        deployment_files = deployment_contract.get("artifacts") if isinstance(deployment_contract, dict) else {}
        if isinstance(deployment_files, dict):
            files_map.update({k: str(v) for k, v in deployment_files.items()})
        if context_variables is not None and hasattr(context_variables, "set"):
            try:
                context_variables.set("deploy_target_spec", deployment_contract.get("deploy_target_spec"))
                context_variables.set("deployment_template_manifest", deployment_contract.get("deployment_manifest"))
                context_variables.set("deployment_contract_validation_errors", deployment_contract.get("bundle_errors") or [])
            except Exception:
                pass
        await _inject_agent_context_env(files_map=files_map, app_id=str(app_id), context_variables=context_variables)

    acceptance_result = await run_app_bundle_acceptance_gate(
        files=files_map,
        context_variables=context_variables,
        capability_packs=selected_capability_packs,
    )
    if not acceptance_result.get("passed"):
        for failed_test in acceptance_result.get("failed_tests") or []:
            wf_logger.error("App bundle acceptance failure: %s", failed_test)
        return {
            "status": "error",
            "message": (
                "Generated app bundle failed deterministic acceptance. "
                "Fix the reported contract errors and regenerate."
            ),
            "app_bundle_acceptance_status": acceptance_result.get("status"),
            "app_bundle_acceptance_result": acceptance_result,
            "bundle_errors": acceptance_result.get("bundle_scan", {}).get("errors") or [],
        }

    bundle_name = "GeneratedApp"
    try:
        # Best-effort: allow agent to provide a bundle name
        for _agent, data in collected.items():
            if isinstance(data, dict):
                candidate = data.get("app_name") or data.get("bundle_name") or data.get("project_name")
                if isinstance(candidate, str) and candidate.strip():
                    bundle_name = candidate.strip()
                    break
    except Exception:
        pass

    # Normalize bundle name to a safe folder name
    bundle_name = "".join(ch for ch in bundle_name if ch.isalnum() or ch in {"-", "_"}).strip() or "GeneratedApp"

    app_dir = _resolve_app_output_dir(app_id=app_id, build_id=build_id or chat_id)
    base_dir = app_dir.parent
    app_dir.mkdir(parents=True, exist_ok=True)

    if tlog and _log_tool_event:  # type: ignore[truthy-function]
        _log_tool_event(tlog, action="write_files", status="start", file_count=len(files_map))

    written_paths: list[str] = []
    for rel_path, content in files_map.items():
        safe = _safe_relpath(rel_path)
        if not safe:
            continue
        out_path = app_dir / safe
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(str(content), encoding="utf-8")
        written_paths.append(safe)

    migration_record = await _persist_pending_schema_migration(
        pending_migration=pending_migration,
        app_id=str(app_id),
        build_id=str(build_id or chat_id),
        workflow_name=workflow_name,
        chat_id=chat_id,
        context_variables=context_variables,
        generated_app_dir=str(app_dir.resolve()),
    )

    resolved_bundle_path = str(app_dir.resolve())

    zip_path = base_dir / f"{bundle_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for rel_path in written_paths:
            file_path = app_dir / rel_path
            if file_path.exists() and file_path.is_file():
                zipf.write(file_path, arcname=f"{bundle_name}/{rel_path}")

    zip_size = zip_path.stat().st_size
    artifact_version = await _register_app_bundle_artifact_version(
        app_id=str(app_id),
        user_id=user_id,
        workflow_name=workflow_name,
        chat_id=chat_id,
        bundle_name=bundle_name,
        zip_path=zip_path,
        app_dir=app_dir,
        written_paths=written_paths,
        context_variables=context_variables,
    )

    artifact_version_id = artifact_version.id if artifact_version else None
    workflow_sequence = str(_context_get(context_variables, "workflow_sequence") or "build")
    current_build_run = {
        "build_id": str(build_id or chat_id or build_registry_id or ""),
        "workflow_sequence": workflow_sequence,
        "status": "review",
        "active_chat_id": chat_id,
        "active_workflow_id": workflow_name,
        "artifact_version_id": artifact_version_id,
        "bundle_path": resolved_bundle_path,
    }
    await update_build_status(
        build_registry_id=build_registry_id or "",
        status="review",
        bundle_path=resolved_bundle_path,
        artifact_version_id=artifact_version_id,
        workflow_sequence=workflow_sequence,
        active_chat_id=chat_id,
        active_workflow_id=workflow_name,
        current_build_run=current_build_run,
    )
    # Persist lifecycle_state and bundle_path into context_variables so the
    # refinement router can read them when the user submits a revision request
    # from the app_review transition without the bundle having been promoted yet.
    if context_variables is not None and hasattr(context_variables, "set"):
        try:
            context_variables.set("lifecycle_state", "review")
            context_variables.set("bundle_path", resolved_bundle_path)
        except Exception:
            pass

    ui_files = [
        {
            "name": f"{bundle_name}.zip",
            "size": _format_bytes(zip_size),
            "size_bytes": zip_size,
            "path": str(zip_path.resolve()),
            "id": "file-zip-bundle",
            "type": "zip",
        }
    ]

    ui_payload = {
        "downloadType": "single",
        "files": [] if confirmation_only else ui_files,
        "agent_message": agent_message_text,
        "description": agent_message_text,
        "title": "App Workbench",
        "workflow_name": workflow_name,
        "agent_message_id": agent_message_id,
        "stage": "confirm" if confirmation_only else "files_ready",
        "artifact_kind": "app_bundle",
        "artifact_key": "app_bundle",
        "artifact_version_id": artifact_version_id,
        "app_id": str(app_id),
        # Workbench context (best-effort): allow ChatUI to render file tree + editor + preview.
        "generated_files": files_map,
    }
    if include_dockerfiles or include_workflow or include_compose:
        ui_payload["deployment_profile"] = deployment_profile
        ui_payload["deployment_artifacts_included"] = True
        if context_variables is not None and hasattr(context_variables, "get"):
            try:
                ui_payload["deploy_target_spec"] = context_variables.get("deploy_target_spec")
                ui_payload["deployment_template_manifest"] = context_variables.get("deployment_template_manifest")
                ui_payload["deployment_contract_validation_errors"] = context_variables.get("deployment_contract_validation_errors")
            except Exception:
                pass
    if migration_record:
        ui_payload["database_migration"] = migration_record
    # Best-effort: include validation/integration context for the AppWorkbench.
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            ui_payload["app_validation_status"] = context_variables.get("app_validation_status")
            ui_payload["app_validation_strategy_used"] = context_variables.get("app_validation_strategy_used")
            ui_payload["app_validation_preview_url"] = context_variables.get("app_validation_preview_url")
            ui_payload["app_validation_result"] = context_variables.get("app_validation_result")
            ui_payload["integration_tests_passed"] = context_variables.get("integration_tests_passed")
            ui_payload["integration_test_result"] = context_variables.get("integration_test_result")
            ui_payload["app_bundle_acceptance_status"] = context_variables.get("app_bundle_acceptance_status")
            ui_payload["app_bundle_acceptance_result"] = context_variables.get("app_bundle_acceptance_result")
        except Exception:
            pass

    try:
        response = await use_ui_tool(
            tool_id="AppWorkbench",
            payload=ui_payload,
            chat_id=chat_id,
            workflow_name=workflow_name,
        )
    except UIToolError:
        raise
    except Exception as e:
        wf_logger.error("UI interaction failed: %s", e, exc_info=True)
        raise UIToolError("Failed during file download UI interaction") from e

    if response.get("status") == "cancelled":
        _context_set(context_variables, "download_status", "cancelled")
        _context_set(context_variables, "app_download_ready", False)
        return {
            "status": "cancelled",
            "ui_response": response,
            "agent_message_id": agent_message_id,
            "ui_files": [],
            "message": "User declined download",
        }

    # Optional GitHub export (triggered by DownloadCenter action)
    deployment_result: dict[str, Any] | None = None
    try:
        action = None
        if isinstance(response, dict):
            action = response.get("action")
            if not action and isinstance(response.get("data"), dict):
                action = response["data"].get("action")

        if action == "export_to_github":
            gate = resolve_export_gate(context_variables)
            allow_export = bool(gate["allow_export"])
            reasons: list[str] = list(gate["reasons"])

            if not allow_export:
                error_msg = "Export blocked: " + " ".join(reasons) if reasons else "Export blocked."
                wf_logger.warning(error_msg)
                await _emit_deployment_event(
                    chat_id=chat_id,
                    status="failed",
                    data={
                        "app_id": app_id,
                        "error": error_msg,
                        "message": error_msg,
                        "blocked": True,
                        "reasons": reasons,
                    },
                )
                deployment_result = {
                    "success": False,
                    "error": error_msg,
                    "workflow_type": "app-generator",
                    "blocked": True,
                    "reasons": reasons,
                    "app_bundle_acceptance_status": gate["app_bundle_acceptance_status"],
                    "app_validation_status": gate["app_validation_status"],
                    "app_validation_strategy_used": gate["app_validation_strategy_used"],
                    "integration_tests_passed": gate["integration_tests_passed"],
                }
                action = None
                return {
                    "status": "success",
                    "ui_response": response,
                    "agent_message_id": agent_message_id,
                    "ui_files": ui_files,
                    "bundle_dir": str(app_dir.resolve()),
                    "bundle_zip": str(zip_path.resolve()),
                    "deployment": deployment_result,
                    "file_count": len(written_paths),
                    "files_written": written_paths,
                    "storage_backend": storage_backend,
                }

            repo_name = None
            commit_message = "Initial code generation from Mozaiks AI"
            if isinstance(response, dict):
                repo_name = response.get("repo_name") or response.get("repoName")
                commit_message = response.get("commit_message") or response.get("commitMessage") or commit_message
                if isinstance(response.get("data"), dict):
                    repo_name = response["data"].get("repo_name") or response["data"].get("repoName") or repo_name
                    commit_message = (
                        response["data"].get("commit_message")
                        or response["data"].get("commitMessage")
                        or commit_message
                    )
            deployment_result = await export_app_code_to_github(
                bundle_path=str(zip_path.resolve()),
                app_id=app_id,
                repo_name=repo_name,
                commit_message=commit_message,
                user_id=user_id,
                context_variables=context_variables,
            )
    except Exception as deploy_err:
        wf_logger.warning("GitHub export flow failed: %s", deploy_err)

    download_result = {
        "bundle_dir": str(app_dir.resolve()),
        "bundle_zip": str(zip_path.resolve()),
        "file_count": len(written_paths),
        "files_written": written_paths,
        "storage_backend": storage_backend,
    }
    _context_set(context_variables, "generated_app_dir", download_result["bundle_dir"])
    _context_set(context_variables, "generated_app_zip", download_result["bundle_zip"])
    _context_set(context_variables, "download_status", "ready")
    _context_set(context_variables, "app_download_ready", True)
    _context_set(context_variables, "download_result", download_result)

    return {
        "status": "success",
        "ui_response": response,
        "agent_message_id": agent_message_id,
        "ui_files": ui_files,
        "bundle_dir": download_result["bundle_dir"],
        "bundle_zip": download_result["bundle_zip"],
        **({"deployment": deployment_result} if deployment_result is not None else {}),
        "file_count": download_result["file_count"],
        "files_written": download_result["files_written"],
        "storage_backend": storage_backend,
    }



