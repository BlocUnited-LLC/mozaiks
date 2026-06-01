"""
generate_and_download - Bundle generated app code and present AppWorkbench + DownloadCenter UI.

This tool:
1) Collects latest agent JSON outputs for the chat/app
2) Extracts `code_files` from any agent output
3) Writes files to disk under generated_apps/<app_id>/<chat_id>/<bundle_name>/
4) Creates a ZIP bundle
5) Presents AppWorkbench export actions and (optionally) triggers export_to_github
"""


import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Annotated, Dict, List, Optional

from autogen.tools.dependency_injection import Field
from mozaiksai.core.app_context.store import register_greenfield_app_context_version
from mozaiksai.core.workflow.generator_support.agent_endpoints import (
    resolve_agent_api_url,
    resolve_agent_websocket_url,
)
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    collect_generated_app_file_map,
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.generator_support.workflow_exports import get_latest_workflow_export
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool
from logs.logging_config import get_workflow_logger

from factory_app.workflows.AppGenerator.tools.export_app_code import (
    export_app_code_to_github,
    resolve_export_gate,
)
from factory_app.workflows.AppGenerator.tools.requirements_scanner import scan_requirements
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.module_api_template import get_module_api_template
from factory_app.workflows.AppGenerator.tools.update_app_record import update_build_status
from factory_app.workflows.AppGenerator.tools.schema_migration import inject_migration_into_bundle
from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    generate_deployment_artifacts,
)

try:
    from logs.tools_logs import get_tool_logger as _get_tool_logger, log_tool_event as _log_tool_event  # type: ignore
except Exception:  # pragma: no cover
    _get_tool_logger = None  # type: ignore
    _log_tool_event = None  # type: ignore

BuilderArtifactStore = None
AG2PersistenceManager = None


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


def _safe_relpath(raw: str) -> Optional[str]:
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


def _discover_code_files(col: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
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


def _context_get(context_variables: Optional[Any], key: str) -> Optional[Any]:
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


def _discover_context_files(context_variables: Optional[Any]) -> Dict[str, str]:
    raw = _context_get(context_variables, "generated_files")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for rel_path, content in raw.items():
        safe = _safe_relpath(str(rel_path))
        if safe:
            out[safe] = str(content)
    return out


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


def _ensure_env_line(lines: List[str], key: str, value: str) -> List[str]:
    normalized_key = key.strip()
    if not normalized_key:
        return lines
    rendered = f"{normalized_key}={value or ''}"

    out: List[str] = []
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


async def _inject_agent_context_env(*, files_map: Dict[str, str], app_id: str, context_variables: Optional[Any]) -> None:
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


async def _emit_deployment_event(*, chat_id: Optional[str], status: str, data: dict) -> None:
    if not chat_id:
        return
    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        await transport.send_event_to_ui(
            {"type": f"chat.deployment_{status}", "data": {"timestamp": datetime.now(timezone.utc).isoformat(), **data}},
            chat_id,
        )
    except Exception:
        return


async def _persist_pending_schema_migration(
    *,
    pending_migration: Optional[Dict[str, Any]],
    app_id: str,
    build_id: str,
    workflow_name: str,
    chat_id: Optional[str],
    context_variables: Optional[Any],
    generated_app_dir: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(pending_migration, dict):
        return None
    migration_id = str(pending_migration.get("migration_id") or "").strip()
    if not migration_id:
        return None

    migration_path = (
        Path(generated_app_dir)
        / "config"
        / "data_migrations"
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
                f"config/data_migrations/{migration_id}.json",
            )
        except Exception:
            pass
    return record


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
    user_id: Optional[str],
    workflow_name: str,
    chat_id: Optional[str],
    bundle_name: str,
    zip_path: Path,
    app_dir: Path | None = None,
    written_paths: list[str] | None = None,
    context_variables: Optional[Any],
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
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            parent_version_id = context_variables.get("artifact_version_id")
            validation_status_raw = context_variables.get("app_validation_status")
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
    elif normalized_status == "skipped":
        validation_status = ArtifactValidationStatus.SKIPPED

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
    bundle_content_metadata: Dict[str, Any] = {
        "artifact_path": str(zip_path.resolve()),
        "bundle_name": bundle_name,
    }
    # Include the carry-forward preservation report in artifact metadata when present.
    cf_report = None
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            cf_report = context_variables.get("carry_forward_report")
        except Exception:
            pass
    if isinstance(cf_report, dict):
        bundle_content_metadata["carry_forward_report"] = cf_report
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
        artifact_kinds=("concept", "build_plan", "design_docs", "theme_capture"),
        artifact_store=artifact_store,
    )
    artifact_version = await artifact_store.create_artifact_version(
        app_id=str(app_id),
        artifact_kind="app_bundle",
        artifact_key="app_bundle",
        parent_version_id=str(parent_version_id) if parent_version_id else None,
        canonical_inputs_version=canonical_inputs_version,
        files_manifest=files_manifest,
        source_workflow=workflow_name,
        source_chat_id=chat_id,
        lifecycle_status=lifecycle_status,
        validation_status=validation_status,
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
        Dict[str, Any],
        Field(description="Download configuration with confirmation_only and storage_backend options."),
    ],
    agent_message: Annotated[
        str,
        Field(description="Concise message, up to 140 chars, shown to the user in the download UI."),
    ],
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    confirmation_only = bool((DownloadRequest or {}).get("confirmation_only", False))
    storage_backend = (DownloadRequest or {}).get("storage_backend", "none")
    description = (DownloadRequest or {}).get("description")
    include_dockerfiles = _is_truthy((DownloadRequest or {}).get("include_dockerfiles"))
    include_workflow = _is_truthy((DownloadRequest or {}).get("include_workflow"))
    include_compose = _is_truthy((DownloadRequest or {}).get("include_compose"))
    deployment_profile = str((DownloadRequest or {}).get("deployment_profile") or "").strip() or None

    chat_id: Optional[str] = None
    app_id: Optional[str] = None
    user_id: Optional[str] = None
    build_registry_id: Optional[str] = None
    build_id: Optional[str] = None
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
    if _get_tool_logger:
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
    files_map = _discover_code_files(collected)
    if not files_map:
        files_map = _discover_context_files(context_variables)
    if not files_map and _is_truthy(_context_get(context_variables, "app_schema_ready")):
        files_map = collect_generated_app_file_map(
            _context_get(context_variables, "generated_app_dir")
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

    # Scan for forbidden patterns before the bundle is written to disk.
    # Blocks delivery if the generated app contains direct Stripe SDK usage,
    # Stripe Refunds API calls, or embedded secret key literals.
    bundle_scan_errors = scan_generated_bundle(files_map)
    if bundle_scan_errors:
        for _scan_err in bundle_scan_errors:
            wf_logger.error("Generated bundle forbidden pattern: %s", _scan_err)
        return {
            "status": "error",
            "message": (
                "Generated bundle contains forbidden patterns. "
                "Fix the errors below and regenerate."
            ),
            "bundle_errors": bundle_scan_errors,
        }

    # Inject any pending migration file produced by DatabaseAgent during refinement.
    pending_migration: Optional[Dict[str, Any]] = None
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            pending_migration = context_variables.get("pending_schema_migration")
        except Exception:
            pending_migration = None
    if isinstance(pending_migration, dict) and pending_migration.get("migration_id"):
        inject_migration_into_bundle(files_map, pending_migration)

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
    if include_dockerfiles or include_workflow or include_compose:
        deployment_contract = generate_deployment_artifacts(
            app_id=str(app_id),
            deployment_profile=deployment_profile,
            include_dockerfiles=include_dockerfiles,
            include_workflow=include_workflow,
            include_compose=include_compose,
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

    base_dir = Path("generated_apps") / str(app_id) / str(chat_id)
    app_dir = base_dir / bundle_name
    app_dir.mkdir(parents=True, exist_ok=True)

    if tlog and _log_tool_event:
        _log_tool_event(tlog, action="write_files", status="start", file_count=len(files_map))

    written_paths: List[str] = []
    for rel_path, content in files_map.items():
        safe = _safe_relpath(rel_path)
        if not safe:
            continue
        out_path = app_dir / safe
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(str(content), encoding="utf-8")
        written_paths.append(safe)

    migration_record = None
    try:
        migration_record = await _persist_pending_schema_migration(
            pending_migration=pending_migration,
            app_id=str(app_id),
            build_id=str(build_id or chat_id),
            workflow_name=workflow_name,
            chat_id=chat_id,
            context_variables=context_variables,
            generated_app_dir=str(app_dir.resolve()),
        )
    except Exception as exc:
        wf_logger.warning("Failed to persist pending schema migration: %s", exc)

    # Update the app lifecycle record to 'review' now that files are on disk.
    resolved_bundle_path = str(app_dir.resolve())
    await update_build_status(
        build_registry_id=build_registry_id or "",
        status="review",
        bundle_path=resolved_bundle_path,
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

    zip_path = base_dir / f"{bundle_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for rel_path in written_paths:
            file_path = app_dir / rel_path
            if file_path.exists() and file_path.is_file():
                zipf.write(file_path, arcname=f"{bundle_name}/{rel_path}")

    zip_size = zip_path.stat().st_size
    artifact_version = None
    try:
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
    except Exception as artifact_err:
        wf_logger.warning("Failed to register app bundle artifact version: %s", artifact_err)

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
        "artifact_version_id": artifact_version.id if artifact_version else None,
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
        wf_logger.error(f"UI interaction failed: {e}", exc_info=True)
        raise UIToolError("Failed during file download UI interaction")

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
    deployment_result: Optional[Dict[str, Any]] = None
    try:
        action = None
        if isinstance(response, dict):
            action = response.get("action")
            if not action and isinstance(response.get("data"), dict):
                action = response["data"].get("action")

        if action == "export_to_github":
            gate = resolve_export_gate(context_variables)
            allow_export = bool(gate["allow_export"])
            reasons: List[str] = list(gate["reasons"])

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
        wf_logger.warning(f"GitHub export flow failed: {deploy_err}")

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

