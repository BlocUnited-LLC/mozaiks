"""Load the selected revision baseline before AppGenerator reasons or assembles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import yaml

from factory_app.workflows._shared.platform.build_target import require_build_binding
from factory_app.workflows._shared.revision_app_source import (
    design_revision_source,
    is_accepted_revision_source,
    read_bound_revision_files,
    read_revision_source,
    revision_baseline_required,
    revision_source_artifact_id,
)
from factory_app.workflows._shared.workflow_integration import (
    hydrate_workflow_integration_context_from_latest_artifact,
)
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.runtime.app.auth_contract import validate_app_auth_contract
from mozaiksai.core.runtime.app.paths import APP_AUTH_CONFIG_PATH
from mozaiksai.core.runtime.app.theme_contract import APP_THEME_PATH
from mozaiksai.core.validation.compilation_inputs import resolve_app_compilation_inputs
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    data_contract_requires_auth,
    extract_deleted_file_paths_from_payload,
)
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_design_access_policy,
)
from mozaiksai.core.workflow.generator_support.module_revision import (
    legacy_optional_updated_at_reads,
    retained_workflow_page_bindings,
)

from .revision_result_coverage import revision_result_module_coverage


def _set(context_variables: Any, key: str, value: Any) -> None:
    if isinstance(context_variables, dict):
        context_variables[key] = value
    else:
        context_variables.set(key, value)


def discard_executable_plan(context: Any) -> None:
    """Invalidate dispatch while leaving the batch runner's historical status intact."""
    _set(context, "app_plan_ready", False)
    _set(context, "app_build_plan", None)
    _set(context, "app_task_batch_items", [])


async def read_bound_revision_binary_assets(context_variables: Any) -> dict[str, bytes]:
    files = await read_bound_revision_files(context_variables, include_binary=True)
    return {path: content for path, content in files.items() if isinstance(content, bytes)}


async def revision_asset_evidence(
    context_variables: Any, files: dict[str, str],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Describe the exact retained source bytes and current tombstones."""
    source_id = revision_source_artifact_id(context_variables)
    assets = await read_bound_revision_binary_assets(context_variables)
    if revision_source_artifact_id(context_variables) != source_id:
        raise ValueError("revision source changed while assets were read")
    conflicts = set(assets) & set(files)
    if conflicts:
        raise ValueError("revision_binary_text_replacement: " + ", ".join(sorted(conflicts)))
    deleted = set(extract_deleted_file_paths_from_payload({
        "deleted_files": detach(context_variables.get("deleted_files")),
    }))
    # The existing task inventory has no binary deletion grant. A free-form
    # deleted_files entry must not remove an opaque source asset.
    unowned = set(assets) & deleted
    if unowned:
        raise ValueError("revision_binary_deletion_unowned: " + ", ".join(sorted(unowned)))
    evidence = {
        "source_artifact_version_id": source_id,
        "opaque_assets": [
            {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            for path, data in sorted(assets.items())
        ],
        "deleted_files": sorted(deleted),
    }
    return assets, evidence


async def hydrate_app_revision_context(context_variables: Any = None, *, load_workflows: bool = False) -> dict[str, Any]:
    """Verify the source before exposing its theme or resuming cached work."""
    try:
        result = await hydrate_revision_files(context_variables, initialize_theme=True, load_workflows=load_workflows)
        if load_workflows and result["status"] == "skipped":
            return await hydrate_workflow_integration_context_from_latest_artifact(context_variables)
        return result
    except Exception:
        if context_variables is not None and (context_variables.get("app_build_plan") or context_variables.get("app_task_batch_items")):
            discard_executable_plan(context_variables)
        raise


def uses_revision_source_theme(context_variables: Any) -> bool:
    return (
        revision_baseline_required(context_variables)
        and context_variables.get("artifact_kind") == "app_bundle"
        and context_variables.get("workflow_sequence") not in {"theme_revision", "theme_patch"}
    )


def read_revision_theme(files: dict[str, Any]) -> dict[str, Any] | None:
    theme = json.loads(files[APP_THEME_PATH]) if APP_THEME_PATH in files else None
    if theme is not None and not isinstance(theme, dict):
        raise ValueError("revision_baseline_theme_invalid")
    return theme


async def _retained_feature_login(context: Any, artifact: BuildRecord, files: dict[str, Any]) -> dict[str, str]:
    """Recover login intent from the accepted compilation for a bounded product-feature edit."""
    if context.get("revision_scope") != "feature" or not is_accepted_revision_source(artifact):
        return {}
    impact = detach(context.get("impact_set"))
    paths = impact.get("affected_bundle_paths") if isinstance(impact, dict) else None
    if not isinstance(paths, list) or not paths or not all(
        isinstance(path, str) and path.startswith(("modules/", "data/", "ui/pages/"))
        and ".." not in path.split("/") and "\\" not in path for path in paths
    ):
        return {}
    saved = artifact.commit_metadata.metadata.get("compilation_inputs")
    if (not isinstance(saved, dict) or saved.get("version") != 2 or APP_AUTH_CONFIG_PATH not in files
            or json.loads(files["app.json"]).get("authRequired") is not True):
        return {}
    config = validate_app_auth_contract(yaml.safe_load(files[APP_AUTH_CONFIG_PATH]))
    if config.signup_enabled or config.mode not in {"brokered_oidc", "private_workspace"} or config.authorization.roles:
        return {}
    compilation = await resolve_app_compilation_inputs(
        app_id=artifact.app_id, build_record_id=artifact.id, owner_user_id=context.get("user_id"),
        build_registry_id=require_build_binding(context).build_registry_id,
    )
    prior = compilation.context_for(artifact.app_id).get("app_build_plan") if compilation else None
    if (not isinstance(prior, dict) or prior.get("roles") or prior.get("auth_strategy") not in {"basic-login", "third-party"}
            or prior.get("auth_mode") not in {None, config.mode}):
        return {}
    return {"source_artifact_version_id": artifact.id, "auth_strategy": prior["auth_strategy"], "auth_mode": config.mode}


def bind_retained_feature_login(plan: dict[str, Any], context: Any) -> None:
    """Bind an authored or restored plan to the accepted feature's verified sign-in policy."""
    retained = detach(context.get("retained_feature_login")) if context is not None else None
    if (not isinstance(retained, dict) or not retained.get("source_artifact_version_id")
            or retained["source_artifact_version_id"] != context.get("revision_source_artifact_version_id")
            or not data_contract_requires_auth(detach(context.get("data_contract")))
            or resolve_design_access_policy(context) != {"roles": [], "grants": []}):
        return
    strategies = {None, "public", retained["auth_strategy"]}
    modes = {None, "private_workspace", retained["auth_mode"]}
    if plan.get("roles") or plan.get("auth_strategy") not in strategies or plan.get("auth_mode") not in modes:
        raise ValueError(
            "The scoped feature preserves the accepted sign-in strategy "
            f"{retained['auth_strategy']!r} and mode {retained['auth_mode']!r}, with no named roles. "
            "An access change needs a revision scoped to authentication or a reviewed role change."
        )
    plan.update(auth_strategy=retained["auth_strategy"], auth_mode=retained["auth_mode"])


async def hydrate_revision_files(
    context_variables: Any, *, initialize_theme: bool = False, load_workflows: bool = False,
) -> dict[str, Any]:
    if context_variables is None:
        return {"status": "skipped", "reason": "not_revision"}
    _set(context_variables, "revision_result_module_coverage", None)
    _set(context_variables, "legacy_optional_updated_at_reads", {})
    _set(context_variables, "retained_workflow_page_bindings", {})
    _set(context_variables, "retained_feature_login", {})
    if context_variables.get("build_mode") != "revision":
        return {"status": "skipped", "reason": "not_revision"}
    # Recompute even after replay. A carried flag cannot waive source/owner or
    # existence checks, and a failed recheck leaves the baseline required.
    _set(context_variables, "revision_first_generation", False)
    if not revision_baseline_required(context_variables):
        return {"status": "skipped", "reason": "explicit_rebuild"}

    if context_variables.get("workflow_sequence") == "design_revision":
        artifact_id = await design_revision_source(context_variables)
        if artifact_id is None:
            _set(context_variables, "revision_first_generation", True)
            return {"status": "skipped", "reason": "first_generation"}
    else:
        artifact_id = revision_source_artifact_id(context_variables)
    artifact, files = await read_revision_source(context_variables, artifact_id)
    _set(context_variables, "revision_source_artifact_version_id", artifact.id)
    if load_workflows:
        # The active cursor can be a new workflow. Resolve it only after pinning its original app.
        await hydrate_workflow_integration_context_from_latest_artifact(context_variables)
    login = await _retained_feature_login(context_variables, artifact, files)
    _set(context_variables, "retained_feature_login", login)
    existing_plan = context_variables.get("app_build_plan")
    if login and isinstance(existing_plan, Mapping):
        rebound = detach(existing_plan)
        bind_retained_feature_login(rebound, context_variables)
        if rebound != existing_plan:
            _set(context_variables, "app_build_plan", rebound)
    if initialize_theme and uses_revision_source_theme(context_variables):
        _set(context_variables, "captured_theme_config", read_revision_theme(files))
    if is_accepted_revision_source(artifact):
        coverage = revision_result_module_coverage(context_variables, files)
        _set(context_variables, "revision_result_module_coverage", coverage)
        _set(context_variables, "retained_workflow_page_bindings", retained_workflow_page_bindings(context_variables, files))
        reads = legacy_optional_updated_at_reads(files, context_variables.get("data_contract"))
        _set(context_variables, "legacy_optional_updated_at_reads", reads)
    current = detach(context_variables.get("generated_files")) or {}
    if not isinstance(current, dict):
        raise ValueError("revision_baseline_generated_files_invalid")
    files.update(current)
    for path in detach(context_variables.get("deleted_files")) or []:
        files.pop(path, None)
    if isinstance(context_variables, dict):
        context_variables["generated_files"] = files
    else:
        context_variables.set("generated_files", files)
    return {"status": "hydrated", "artifact_version_id": artifact_id, "file_count": len(files)}
