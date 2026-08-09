from __future__ import annotations

# ruff: noqa: E402
import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_app.workflows._shared.workflow_integration import (
    hydrate_workflow_integration_context_from_latest_artifact,
    normalize_workflow_integration_metadata,
)
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.export_app_code import resolve_export_gate
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactStore,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    persist_summary_artifact,
)
from mozaiksai.core.runtime.app.loader import AppLoader
from scripts.smoke_agentgenerator_live_pack import run_live_agentgenerator_pack_smoke
from scripts.smoke_appgenerator_live_acceptance import (
    SmokeContext,
    build_appgenerator_acceptance_files,
    default_workflow_integration,
    workflow_integration_from_live_agentgenerator,
)

REAL_STORE_SMOKE_APP_ID_PREFIX = "real-factory-artifact-lineage-"
LIVE_REAL_STORE_SMOKE_APP_ID_PREFIX = "live-real-factory-artifact-lineage-"
ALLOWED_REAL_STORE_SMOKE_APP_ID_PREFIXES = (
    REAL_STORE_SMOKE_APP_ID_PREFIX,
    LIVE_REAL_STORE_SMOKE_APP_ID_PREFIX,
)


def _configure_event_loop_policy() -> None:
    if os.name != "nt":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())


class MemoryArtifactStore:
    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[ArtifactVersionDoc]] = {}
        self.create_calls: list[dict[str, Any]] = []
        self._counter = 0

    def seed(
        self,
        *,
        app_id: str,
        build_family: str,
        build_key: str | None = None,
        summary_payload: dict[str, Any] | None = None,
    ) -> ArtifactVersionDoc:
        build_key = build_key or build_family
        self._counter += 1
        artifact = ArtifactVersionDoc(
            _id=f"av_{build_family}_{self._counter}",
            app_id=app_id,
            build_family=build_family,
            build_key=build_key,
            version_number=1,
            lineage_root_id=f"av_{build_family}_{self._counter}",
            lifecycle_status=ArtifactLifecycleStatus.CURRENT,
            validation_status=ArtifactValidationStatus.SKIPPED,
            commit_metadata={
                "metadata": {
                    "summary_payload": summary_payload or {"seeded": build_family},
                    "summary_format": "json",
                }
            },
        )
        self._versions.setdefault((build_family, build_key), []).insert(0, artifact)
        return artifact

    async def list_build_records(
        self,
        *,
        app_id: str,
        build_family: str | None = None,
        build_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 50,
        **_: Any,
    ) -> list[ArtifactVersionDoc]:
        if build_family is None:
            keys = list(self._versions)
        elif build_key is None:
            keys = [key for key in self._versions if key[0] == build_family]
        else:
            keys = [(build_family, build_key)]

        versions: list[ArtifactVersionDoc] = []
        for key in keys:
            versions.extend(self._versions.get(key, []))
        versions = [item for item in versions if item.app_id == app_id]
        if lifecycle_status is not None:
            versions = [item for item in versions if item.lifecycle_status == lifecycle_status]
        return versions[: max(1, int(limit))]

    async def get_build_record(
        self,
        *,
        app_id: str,
        build_record_id: str,
    ) -> ArtifactVersionDoc | None:
        for versions in self._versions.values():
            for artifact in versions:
                if artifact.app_id == app_id and artifact.id == build_record_id:
                    return artifact
        return None

    async def create_build_record(self, **kwargs: Any) -> ArtifactVersionDoc:
        self.create_calls.append(dict(kwargs))
        self._counter += 1
        build_family = str(kwargs["build_family"])
        build_key = str(kwargs["build_key"])
        lifecycle_status = kwargs["lifecycle_status"]
        if lifecycle_status == ArtifactLifecycleStatus.CURRENT:
            for item in self._versions.get((build_family, build_key), []):
                if item.app_id == kwargs["app_id"] and item.lifecycle_status == ArtifactLifecycleStatus.CURRENT:
                    item.lifecycle_status = ArtifactLifecycleStatus.SUPERSEDED

        artifact = ArtifactVersionDoc(
            _id=f"av_{build_family}_{self._counter}",
            app_id=kwargs["app_id"],
            build_family=build_family,
            build_key=build_key,
            version_number=len(self._versions.get((build_family, build_key), [])) + 1,
            parent_build_record_id=kwargs.get("parent_build_record_id"),
            lineage_root_id=f"av_{build_family}_{self._counter}",
            canonical_inputs_version=dict(kwargs.get("canonical_inputs_version") or {}),
            lifecycle_status=lifecycle_status,
            validation_status=kwargs["validation_status"],
            files_manifest=list(kwargs.get("files_manifest") or []),
            source_workflow=kwargs.get("source_workflow"),
            source_chat_id=kwargs.get("source_chat_id"),
            commit_metadata=kwargs["commit_metadata"],
        )
        self._versions.setdefault((artifact.build_family, artifact.build_key), []).insert(0, artifact)
        return artifact

    async def get_current_build_record_refs(self, *, app_id: str) -> dict[str, str]:
        refs: dict[str, str] = {}
        for (build_family, _build_key), versions in self._versions.items():
            for artifact in versions:
                if artifact.app_id == app_id and artifact.lifecycle_status == ArtifactLifecycleStatus.CURRENT:
                    refs.setdefault(build_family, artifact.id)
                    break
        return refs


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="python"))
    return str(value)


def _configure_repo_factory_workflows() -> Any:
    from mozaiksai.core.workflow.pack import config as pack_config

    return pack_config.load_global_pack_graph(
        workflows_root=REPO_ROOT / "factory_app" / "workflows"
    )


def _workflow_integration_metadata() -> dict[str, Any]:
    metadata = normalize_workflow_integration_metadata(
        {
            "contract_version": "1.0",
            "bundle_name": "SupportOperations",
            "workflows": [default_workflow_integration()],
            "source": "offline_factory_artifact_lineage_smoke",
        }
    )
    if metadata is None:
        raise RuntimeError("Unable to normalize default workflow integration metadata.")
    return metadata


def _workflow_integration_metadata_from_live_result(
    live_result: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str]]:
    integration = workflow_integration_from_live_agentgenerator(live_result)
    errors = [str(item) for item in integration.get("errors") or []]
    metadata = normalize_workflow_integration_metadata(
        {
            "contract_version": "1.0",
            "bundle_name": live_result.get("pack_name") or integration.get("workflow_name"),
            "workflows": [integration],
            "source": "live_agentgenerator_pack_smoke",
        }
    )
    if metadata is None:
        errors.append("Unable to normalize live AgentGenerator workflow integration metadata.")
    return metadata, integration, errors


def _primary_workflow_integration(metadata: Any) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    primary = metadata.get("primary_workflow")
    if isinstance(primary, dict):
        return dict(primary)
    workflows = metadata.get("workflows")
    if isinstance(workflows, list):
        for workflow in workflows:
            if isinstance(workflow, dict):
                return dict(workflow)
    return None


def _is_real_store_smoke_app_id(app_id: str) -> bool:
    return any(app_id.startswith(prefix) for prefix in ALLOWED_REAL_STORE_SMOKE_APP_ID_PREFIXES)


def _smoke_prefix_message() -> str:
    return " or ".join(repr(prefix) for prefix in ALLOWED_REAL_STORE_SMOKE_APP_ID_PREFIXES)


def _live_agentgenerator_summary(live_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": live_result.get("success"),
        "app_id": live_result.get("app_id"),
        "bundle_root": live_result.get("bundle_root"),
        "active_workflows_root": live_result.get("active_workflows_root"),
        "task_batch_meta": live_result.get("task_batch_meta"),
        "task_run_trace": live_result.get("task_run_trace"),
        "validation": live_result.get("validation"),
        "semantic_drift": live_result.get("semantic_drift"),
        "promotion": live_result.get("promotion"),
        "validation_errors": live_result.get("validation_errors"),
    }


async def _seed_summary_artifacts(
    *,
    app_id: str,
    artifact_store: Any,
) -> tuple[ArtifactVersionDoc, ArtifactVersionDoc, ArtifactVersionDoc]:
    design_docs = await persist_summary_artifact(
        app_id=app_id,
        artifact_kind="design_docs",
        artifact_key="design_docs",
        summary_payload={"surface_map": {"surfaces": [{"surface_id": "support_tickets"}]}},
        source_workflow="DesignDocs",
        source_chat_id="chat_designdocs",
        author_user_id="user_1",
        artifact_store=artifact_store,
    )
    subscription_contract = await persist_summary_artifact(
        app_id=app_id,
        artifact_kind="subscription_contract",
        artifact_key="subscription_contract",
        summary_payload={
            "contract_required": False,
            "rationale": "No generated-app SaaS subscription contract required.",
            "code_files": [],
        },
        source_workflow="SubscriptionContractDesigner",
        source_chat_id="chat_subscriptioncontractdesigner",
        author_user_id="user_1",
        input_artifact_kinds=("concept", "build_plan", "design_docs"),
        artifact_store=artifact_store,
    )
    theme_capture = await persist_summary_artifact(
        app_id=app_id,
        artifact_kind="theme_capture",
        artifact_key="theme_capture",
        summary_payload={"theme": {"name": "Support Operations"}},
        source_workflow="ThemeCapture",
        source_chat_id="chat_themecapture",
        author_user_id="user_1",
        artifact_store=artifact_store,
    )
    return design_docs, subscription_contract, theme_capture


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


async def _load_app_bundle(files: dict[str, str], capability_id: str) -> dict[str, Any]:
    try:
        with tempfile.TemporaryDirectory(prefix="mozaiks-lineage-app-") as temp_dir:
            app_root = Path(temp_dir) / "app"
            _write_files(app_root, files)
            loaded = await AppLoader.load(str(app_root))
            module = loaded.modules[0]
            reactions = module.manifests.reactions.reactions if module.manifests.reactions else []
            reaction_capability_ids = [
                reaction.target.capability_id
                for reaction in reactions
                if reaction.target.kind == "capability"
            ]
            return {
                "loaded": True,
                "app_name": loaded.definition.name,
                "module_ids": [item.name for item in loaded.modules],
                "page_names": [page.name for page in loaded.definition.pages],
                "reaction_capability_ids": reaction_capability_ids,
                "workflow_reaction_loaded": capability_id in reaction_capability_ids,
            }
    except Exception as exc:
        return {"loaded": False, "error": str(exc)}


async def _cleanup_real_store_app_id(app_id: str) -> None:
    from mozaiksai.core.core_config import get_mongo_client
    from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE

    if not _is_real_store_smoke_app_id(app_id):
        raise ValueError(f"Refusing to clean non-smoke app_id: {app_id!r}")

    client = get_mongo_client()
    db = client[SYSTEM_DATABASE]
    for collection_name in (
        "ArtifactVersions",
        "ArtifactVersionCounters",
        "ChangeRequests",
        "RefinementSessions",
    ):
        await db[collection_name].delete_many({"app_id": app_id})


async def _run_lineage_smoke_with_store(
    *,
    app_id: str,
    store: Any,
    mode: str,
    design_docs: ArtifactVersionDoc,
    subscription_contract: ArtifactVersionDoc,
    theme_capture: ArtifactVersionDoc,
    workflow_integration_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph = _configure_repo_factory_workflows()
    if graph is None:
        return {"success": False, "validation_errors": ["Factory pack graph did not load."]}

    build = next(sequence for sequence in graph.journeys if sequence.id == "build")
    workflow_steps = [step.workflows for step in build.steps if step.workflows]
    transition_steps = [step.transition for step in build.steps if step.transition]

    metadata = workflow_integration_metadata or _workflow_integration_metadata()

    from factory_app.workflows.AgentGenerator.tools.platform import (
        build_lifecycle as agent_lifecycle,
    )
    from factory_app.workflows.AppGenerator.tools.platform.build_lifecycle import (
        _persist_app_bundle_artifact,
    )

    await agent_lifecycle._persist_workflow_bundle_artifact(
        app_id=app_id,
        chat_id="chat_agentgenerator",
        user_id="user_1",
        workflow_name="AgentGenerator",
        build_mode=None,
        artifact_store=store,
        workflow_integration_metadata=metadata,
    )
    await _persist_app_bundle_artifact(
        app_id=app_id,
        chat_id="chat_appgenerator",
        user_id="user_1",
        workflow_name="AppGenerator",
        build_mode=None,
        artifact_store=store,
    )

    current_refs = await store.get_current_build_record_refs(app_id=app_id)
    workflow_bundle = await store.get_build_record(
        app_id=app_id,
        build_record_id=current_refs["workflow_bundle"],
    )
    app_bundle = await store.get_build_record(
        app_id=app_id,
        build_record_id=current_refs["app_bundle"],
    )
    if workflow_bundle is None or app_bundle is None:
        return {"success": False, "validation_errors": ["Expected current workflow_bundle and app_bundle artifacts."]}
    create_calls = list(getattr(store, "create_calls", []))
    workflow_call = next(
        (
            call
            for call in reversed(create_calls)
            if call["build_family"] == "workflow_bundle"
        ),
        {
            "source_workflow": workflow_bundle.source_workflow,
            "canonical_inputs_version": dict(workflow_bundle.canonical_inputs_version),
        },
    )
    app_call = next(
        (
            call
            for call in reversed(create_calls)
            if call["build_family"] == "app_bundle"
        ),
        {
            "source_workflow": app_bundle.source_workflow,
            "canonical_inputs_version": dict(app_bundle.canonical_inputs_version),
        },
    )

    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": app_id,
            "chat_id": "chat_appgenerator",
            "artifact_version_refs": current_refs,
        }
    )
    hydration = await hydrate_workflow_integration_context_from_latest_artifact(
        context,
        artifact_store=store,
    )
    fixture_workflow_integration = _primary_workflow_integration(context.get("workflow_integration_metadata"))
    integration_errors: list[str] = []
    if fixture_workflow_integration is None:
        fixture_workflow_integration = default_workflow_integration()
        integration_errors.append("Hydrated workflow integration metadata did not include a primary workflow.")
    files = build_appgenerator_acceptance_files(fixture_workflow_integration)
    context.set("generated_files", files)
    context.set("app_validation_status", "skipped")
    context.set("app_validation_strategy_used", "skip")
    acceptance = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    export_gate = resolve_export_gate(context)
    capability_id = str(
        fixture_workflow_integration.get("capability_id")
        or context.get("generated_workflow_capability_id")
        or ""
    )
    loader_result = await _load_app_bundle(files, capability_id)

    errors: list[str] = list(integration_errors)
    expected_workflow_steps = [
        ["ValueEngine"],
        ["ThemeCapture"],
        ["DesignDocs"],
        ["SubscriptionContractDesigner"],
        ["AgentGenerator"],
        ["AppGenerator"],
    ]
    expected_transition_steps = [
        "app_type_selector",
        "coding_journey_selector",
        "database_setup_selector",
        "app_review",
        "build_satisfaction_rating",
    ]
    if workflow_steps != expected_workflow_steps:
        errors.append(f"Unexpected build workflow steps: {workflow_steps!r}.")
    if transition_steps != expected_transition_steps:
        errors.append(f"Unexpected build transition steps: {transition_steps!r}.")
    if graph.artifact_dependency_graph.get("workflow_bundle") != [
        "design_docs",
        "subscription_contract",
    ]:
        errors.append("workflow_bundle dependency graph does not match subscription contract dependency.")
    if "workflow_bundle" not in graph.artifact_dependency_graph.get("app_bundle", []):
        errors.append("app_bundle dependency graph does not include workflow_bundle.")
    if workflow_call["canonical_inputs_version"] != {
        "design_docs": design_docs.id,
        "subscription_contract": subscription_contract.id,
    }:
        errors.append("workflow_bundle canonical inputs did not resolve design_docs and subscription_contract.")
    app_inputs = dict(app_call["canonical_inputs_version"])
    if app_inputs.get("design_docs") != design_docs.id:
        errors.append("app_bundle canonical inputs did not resolve design_docs.")
    if app_inputs.get("subscription_contract") != subscription_contract.id:
        errors.append("app_bundle canonical inputs did not resolve subscription_contract.")
    if app_inputs.get("theme_capture") != theme_capture.id:
        errors.append("app_bundle canonical inputs did not resolve theme_capture.")
    if app_inputs.get("workflow_bundle") != workflow_bundle.id:
        errors.append("app_bundle canonical inputs did not resolve workflow_bundle.")
    if hydration.get("status") != "hydrated" or hydration.get("source") != "workflow_bundle_artifact":
        errors.append(f"AppGenerator workflow metadata hydration failed: {hydration!r}.")
    if context.get("workflow_bundle_artifact_version_id") != workflow_bundle.id:
        errors.append("Hydrated workflow metadata did not retain workflow_bundle artifact version id.")
    if not acceptance.get("passed"):
        errors.append("AppGenerator deterministic acceptance gate did not pass.")
    if "workflow_integration" not in acceptance.get("validation_evidence", {}).get("completed", []):
        errors.append("AppGenerator acceptance evidence did not include workflow_integration.")
    if not export_gate.get("allow_export"):
        errors.extend(str(reason) for reason in export_gate.get("reasons") or [])
    if not loader_result.get("loaded"):
        errors.append(f"Runtime app loader failed: {loader_result.get('error')}")
    if not loader_result.get("workflow_reaction_loaded"):
        errors.append(f"Runtime app loader did not load workflow reaction capability {capability_id!r}.")

    return _json_safe(
        {
            "success": not errors,
            "validation_errors": errors,
            "mode": mode,
            "sequence": {
                "workflow_steps": workflow_steps,
                "transition_steps": transition_steps,
                "artifact_dependency_graph": {
                    "workflow_bundle": graph.artifact_dependency_graph.get("workflow_bundle"),
                    "app_bundle": graph.artifact_dependency_graph.get("app_bundle"),
                },
            },
            "artifact_lineage": {
                "design_docs_id": design_docs.id,
                "subscription_contract_id": subscription_contract.id,
                "theme_capture_id": theme_capture.id,
                "workflow_bundle_id": workflow_bundle.id,
                "app_bundle_id": app_bundle.id,
                "workflow_bundle_inputs": workflow_call["canonical_inputs_version"],
                "app_bundle_inputs": app_call["canonical_inputs_version"],
                "current_refs": current_refs,
            },
            "hydration": hydration,
            "workflow_integration_metadata": context.get("workflow_integration_metadata"),
            "appgenerator_acceptance": {
                "status": acceptance.get("status"),
                "validation_evidence": acceptance.get("validation_evidence"),
                "workflow_integration": acceptance.get("workflow_integration"),
                "fixture_workflow_integration": fixture_workflow_integration,
            },
            "export_gate": export_gate,
            "runtime_loader": loader_result,
        }
    )


async def run_offline_factory_artifact_lineage_smoke(
    *,
    workflow_integration_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    app_id = "offline-factory-artifact-lineage"
    store = MemoryArtifactStore()
    design_docs = store.seed(
        app_id=app_id,
        build_family="design_docs",
        summary_payload={"surface_map": {"surfaces": [{"surface_id": "support_tickets"}]}},
    )
    subscription_contract = store.seed(
        app_id=app_id,
        build_family="subscription_contract",
        summary_payload={
            "contract_required": False,
            "rationale": "No generated-app SaaS subscription contract required.",
            "code_files": [],
        },
    )
    theme_capture = store.seed(
        app_id=app_id,
        build_family="theme_capture",
        summary_payload={"theme": {"name": "Support Operations"}},
    )
    return await _run_lineage_smoke_with_store(
        app_id=app_id,
        store=store,
        mode="memory",
        design_docs=design_docs,
        subscription_contract=subscription_contract,
        theme_capture=theme_capture,
        workflow_integration_metadata=workflow_integration_metadata,
    )


async def _run_real_store_factory_artifact_lineage_smoke(
    *,
    app_id: str | None = None,
    default_app_id_prefix: str,
    mode: str,
    keep_artifacts: bool = False,
    workflow_integration_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    load_dotenv(REPO_ROOT / ".env")
    resolved_app_id = app_id or f"{default_app_id_prefix}{uuid4().hex[:12]}"
    if not _is_real_store_smoke_app_id(resolved_app_id):
        return {
            "success": False,
            "validation_errors": [
                f"Real-store smoke app_id must start with {_smoke_prefix_message()} "
                "so cleanup can stay scoped."
            ],
            "mode": mode,
            "app_id": resolved_app_id,
        }

    from mozaiksai.core.core_config import close_mongo_client, get_mongo_client

    store = ArtifactStore()
    try:
        client = get_mongo_client()
        await client.admin.command("ping")
        await _cleanup_real_store_app_id(resolved_app_id)
        design_docs, subscription_contract, theme_capture = await _seed_summary_artifacts(
            app_id=resolved_app_id,
            artifact_store=store,
        )
        result = await _run_lineage_smoke_with_store(
            app_id=resolved_app_id,
            store=store,
            mode=mode,
            design_docs=design_docs,
            subscription_contract=subscription_contract,
            theme_capture=theme_capture,
            workflow_integration_metadata=workflow_integration_metadata,
        )
        result["app_id"] = resolved_app_id
        result["kept_artifacts"] = bool(keep_artifacts)
        return result
    except Exception as exc:
        return {
            "success": False,
            "validation_errors": [str(exc)],
            "mode": mode,
            "app_id": resolved_app_id,
            "mongo_uri_configured": bool(os.getenv("MONGO_URI")),
        }
    finally:
        if not keep_artifacts:
            try:
                await _cleanup_real_store_app_id(resolved_app_id)
            except Exception:
                pass
        close_mongo_client()


async def run_real_store_factory_artifact_lineage_smoke(
    *,
    app_id: str | None = None,
    keep_artifacts: bool = False,
    workflow_integration_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _run_real_store_factory_artifact_lineage_smoke(
        app_id=app_id,
        default_app_id_prefix=REAL_STORE_SMOKE_APP_ID_PREFIX,
        mode="real_store",
        keep_artifacts=keep_artifacts,
        workflow_integration_metadata=workflow_integration_metadata,
    )


async def run_live_real_store_factory_artifact_lineage_smoke(
    *,
    timeout_seconds: float = 600.0,
    enable_telemetry: bool = False,
    app_id: str | None = None,
    keep_artifacts: bool = False,
) -> dict[str, Any]:
    load_dotenv(REPO_ROOT / ".env")
    live_result = await run_live_agentgenerator_pack_smoke(
        timeout_seconds=timeout_seconds,
        enable_telemetry=enable_telemetry,
    )
    live_summary = _live_agentgenerator_summary(live_result)
    if not live_result.get("success"):
        return _json_safe(
            {
                "success": False,
                "validation_errors": [
                    "Live AgentGenerator pack smoke failed before real-store artifact lineage validation."
                ],
                "mode": "live_real_store",
                "live_agentgenerator": live_summary,
            }
        )

    metadata, integration, metadata_errors = _workflow_integration_metadata_from_live_result(live_result)
    if metadata_errors or metadata is None:
        return _json_safe(
            {
                "success": False,
                "validation_errors": metadata_errors,
                "mode": "live_real_store",
                "live_agentgenerator": live_summary,
                "workflow_integration_from_live_agentgenerator": integration,
            }
        )

    result = await _run_real_store_factory_artifact_lineage_smoke(
        app_id=app_id,
        default_app_id_prefix=LIVE_REAL_STORE_SMOKE_APP_ID_PREFIX,
        mode="live_real_store",
        keep_artifacts=keep_artifacts,
        workflow_integration_metadata=metadata,
    )
    result["live_agentgenerator"] = live_summary
    result["workflow_integration_from_live_agentgenerator"] = integration
    return _json_safe(result)


def main() -> int:
    _configure_event_loop_policy()
    parser = argparse.ArgumentParser(
        description="Run the factory artifact-lineage smoke for AgentGenerator to AppGenerator handoff."
    )
    parser.add_argument(
        "--real-store",
        action="store_true",
        help="Use the real Mongo-backed ArtifactStore instead of the in-memory store.",
    )
    parser.add_argument(
        "--app-id",
        default=None,
        help=(
            "Optional real-store smoke app id. Must start with "
            f"{_smoke_prefix_message()}."
        ),
    )
    parser.add_argument(
        "--keep-real-artifacts",
        action="store_true",
        help="Keep the real-store smoke artifacts for inspection. Default cleans only the smoke app id.",
    )
    parser.add_argument(
        "--live-agentgenerator",
        action="store_true",
        help="Run live AgentGenerator first, then persist its workflow metadata through the real artifact store.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument(
        "--enable-telemetry",
        action="store_true",
        help="Opt into AG2 telemetry middleware during the live smoke. Disabled by default.",
    )
    args = parser.parse_args()
    if args.live_agentgenerator and not args.real_store:
        parser.error("--live-agentgenerator requires --real-store")
    if args.live_agentgenerator:
        payload = asyncio.run(
            run_live_real_store_factory_artifact_lineage_smoke(
                timeout_seconds=float(args.timeout_seconds),
                enable_telemetry=bool(args.enable_telemetry),
                app_id=args.app_id,
                keep_artifacts=bool(args.keep_real_artifacts),
            )
        )
    elif args.real_store:
        payload = asyncio.run(
            run_real_store_factory_artifact_lineage_smoke(
                app_id=args.app_id,
                keep_artifacts=bool(args.keep_real_artifacts),
            )
        )
    else:
        payload = asyncio.run(run_offline_factory_artifact_lineage_smoke())
    print(json.dumps(payload, indent=2), flush=True)
    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
