"""Workflow-specific GitHub export wrapper for AppGenerator outputs."""


from typing import Any

from factory_app.workflows.AgentGenerator.tools.export_to_github import export_to_github_tool
from logs.logging_config import get_workflow_logger
from mozaiksai.core.workflow.generator_support.app_code_versions import (
    build_snapshot_document,
    build_snapshot_document_from_hashes,
    compute_patchset_document,
    extract_files_from_zip_bundle,
    get_snapshot,
    persist_patchset,
    persist_snapshot,
)
from mozaiksai.core.workflow.generator_support.workflow_exports import (
    get_latest_workflow_export,
    record_workflow_export,
)

ALLOWED_EXPORT_VALIDATION_STATUSES = {"passed", "skipped"}


def _read_ctx(context_variables: Any | None, key: str) -> Any:
    if context_variables is None or not hasattr(context_variables, "get"):
        return None
    try:
        return context_variables.get(key)
    except Exception:
        return None


def _normalize_validation_status(raw: Any) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    return value


def resolve_export_gate(context_variables: Any | None) -> dict[str, Any]:
    reasons: list[str] = []
    validation_status = _normalize_validation_status(_read_ctx(context_variables, "app_validation_status"))
    acceptance_status = _normalize_validation_status(_read_ctx(context_variables, "app_bundle_acceptance_status"))
    validation_strategy = _read_ctx(context_variables, "app_validation_strategy_used")
    integration_passed = _read_ctx(context_variables, "integration_tests_passed")

    if acceptance_status != "passed":
        if acceptance_status == "failed":
            reasons.append("App bundle acceptance failed.")
        elif acceptance_status == "pending":
            reasons.append("App bundle acceptance has not completed yet.")
        else:
            reasons.append("App bundle acceptance did not complete with a pass.")

    if validation_status not in ALLOWED_EXPORT_VALIDATION_STATUSES:
        if validation_status == "failed":
            reasons.append("App validation failed.")
        elif validation_status == "pending":
            reasons.append("App validation has not completed yet.")
        else:
            reasons.append("App validation did not complete with a pass or explicit skip.")

    if integration_passed is not True:
        reasons.append("Integration checks have not passed.")

    return {
        "allow_export": len(reasons) == 0,
        "reasons": reasons,
        "app_bundle_acceptance_status": acceptance_status,
        "app_validation_status": validation_status,
        "app_validation_strategy_used": validation_strategy,
        "integration_tests_passed": integration_passed,
    }


def _get_ctx_meta(context_variables: Any | None) -> tuple[str | None, dict[str, Any]]:
    chat_id = None
    meta: dict[str, Any] = {}
    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            chat_id = context_variables.get("chat_id")
            # Keep this intentionally minimal (no secrets).
            meta["app_validation_status"] = context_variables.get("app_validation_status")
            meta["app_validation_strategy_used"] = context_variables.get("app_validation_strategy_used")
            meta["integration_tests_passed"] = context_variables.get("integration_tests_passed")
            meta["app_bundle_acceptance_status"] = context_variables.get("app_bundle_acceptance_status")
            meta["app_bundle_acceptance_result"] = context_variables.get("app_bundle_acceptance_result")
            meta["app_validation_result"] = context_variables.get("app_validation_result")
            meta["integration_test_result"] = context_variables.get("integration_test_result")
        except Exception:
            pass
    return (str(chat_id) if chat_id else None), meta


def _repo_url_from_export(rec: dict[str, Any] | None) -> str | None:
    if not isinstance(rec, dict):
        return None
    repo_url = rec.get("repo_url") or rec.get("repoUrl")
    if isinstance(repo_url, str) and repo_url.strip():
        return repo_url.strip()
    return None


def _snapshot_id_from_export(rec: dict[str, Any] | None) -> str | None:
    if not isinstance(rec, dict):
        return None
    for key in ("snapshotId", "snapshot_id", "targetSnapshotId", "target_snapshot_id"):
        raw = rec.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


async def export_app_code_to_github(
    *,
    app_id: str,
    bundle_path: str,
    repo_name: str | None = None,
    commit_message: str | None = None,
    user_id: str | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any]:
    wf_logger = get_workflow_logger(workflow_name="AppGenerator", chat_id=None, app_id=app_id)
    session_id, structured_outputs = _get_ctx_meta(context_variables)

    gate = resolve_export_gate(context_variables)
    allow_export = bool(gate["allow_export"])
    reasons = list(gate["reasons"])

    if not allow_export:
        error_msg = "Export blocked: " + " ".join(reasons) if reasons else "Export blocked."
        wf_logger.warning(error_msg)
        return {
            "success": False,
            "blocked": True,
            "workflow_type": "app-generator",
            "error": error_msg,
            "reasons": reasons,
            "app_bundle_acceptance_status": gate["app_bundle_acceptance_status"],
            "app_validation_status": gate["app_validation_status"],
            "app_validation_strategy_used": gate["app_validation_strategy_used"],
            "integration_tests_passed": gate["integration_tests_passed"],
            "repo_url": None,
            "job_id": None,
        }

    # Determine if this app already has an exported repo (update path -> PR).
    prior_export: dict[str, Any] | None = None
    try:
        prior_export = await get_latest_workflow_export(app_id=app_id, workflow_type="app-generator")
    except Exception:
        prior_export = None

    prior_repo_url = _repo_url_from_export(prior_export)

    # ------------------------------------------------------------------
    # UPDATE FLOW: repo exists -> compute patchset + create PR (never push)
    # ------------------------------------------------------------------
    if prior_repo_url:
        try:
            # 1) Fetch repo manifest (current base) for conflict detection + PR base.
            manifest = await export_to_github_tool.get_repo_manifest(
                app_id=app_id,
                repo_url=prior_repo_url,
                user_id=user_id,
            )
            base_commit_sha = manifest.get("baseCommitSha") or manifest.get("base_commit_sha") or manifest.get("baseCommit") or ""
            if not isinstance(base_commit_sha, str) or not base_commit_sha.strip():
                raise ValueError("Backend repo manifest missing baseCommitSha")

            repo_files: dict[str, str] = {}
            raw_files = manifest.get("files")
            if isinstance(raw_files, list):
                for entry in raw_files:
                    if not isinstance(entry, dict):
                        continue
                    p = entry.get("path")
                    s = entry.get("sha256")
                    if isinstance(p, str) and p.strip() and isinstance(s, str) and s.strip():
                        repo_files[p.strip()] = s.strip()

            # 2) Resolve baseline snapshot (prefer last export snapshotId; fallback to repo manifest snapshot).
            base_snapshot: dict[str, Any] | None = None
            base_snapshot_id = _snapshot_id_from_export(prior_export)
            if base_snapshot_id:
                try:
                    base_snapshot = await get_snapshot(app_id=app_id, snapshot_id=base_snapshot_id)
                except Exception:
                    base_snapshot = None

            if not base_snapshot:
                # Fallback: baseline is the repo tree itself (hash-only snapshot).
                baseline_files = (
                    raw_files
                    if isinstance(raw_files, list)
                    else [{"path": p, "sha256": sha, "sizeBytes": 0} for p, sha in sorted(repo_files.items())]
                )
                base_snapshot = build_snapshot_document_from_hashes(
                    app_id=app_id,
                    session_id=session_id,
                    workflow_type="app-generator",
                    source="imported_repo",
                    files=baseline_files if isinstance(baseline_files, list) else [],
                    structured_outputs={"repo_manifest": {"repoUrl": prior_repo_url, "baseCommitSha": base_commit_sha}},
                    repo_url=prior_repo_url,
                    base_commit_sha=str(base_commit_sha).strip(),
                )
                try:
                    await persist_snapshot(snapshot_doc=base_snapshot)
                except Exception:
                    # Baseline snapshot persistence is best-effort; patchset can still be computed.
                    pass

            # 3) Create target snapshot from current bundle zip (generated output).
            bundle_files = extract_files_from_zip_bundle(bundle_path)
            target_snapshot = build_snapshot_document(
                app_id=app_id,
                session_id=session_id,
                workflow_type="app-generator",
                source="generated",
                files=bundle_files,
                structured_outputs=structured_outputs,
                repo_url=prior_repo_url,
            )
            await persist_snapshot(snapshot_doc=target_snapshot)

            # 4) Compute patchset + conflicts (baseline snapshot vs target, compared to repo HEAD).
            patchset = compute_patchset_document(
                app_id=app_id,
                base_snapshot=base_snapshot,
                target_snapshot=target_snapshot,
                repo_file_shas=repo_files,
                base_commit_sha=str(base_commit_sha).strip(),
                repo_url=prior_repo_url,
                workflow_type="app-generator",
            )
            await persist_patchset(patchset_doc=patchset)

            # 5) Request backend to create branch + PR (never push to default branch).
            patch_id = patchset.get("patchId")
            branch_name = f"mozaiks/update/{patch_id}"
            pr_title = str(commit_message or "").strip() or "Mozaiks update"
            conflicts_count = len(patchset.get("conflicts") or [])
            changes_count = len(patchset.get("changes") or [])
            pr_body = "\n".join(
                [
                    f"PatchId: {patch_id}",
                    f"AppId: {app_id}",
                    f"BaseCommitSha: {base_commit_sha}",
                    f"Changes: {changes_count}",
                    f"Conflicts: {conflicts_count}",
                    "",
                    "Notes:",
                    "- This PR was generated by MozaiksAI using file-level changes.",
                    "- Conflicts indicate repo files changed since the baseline snapshot; review carefully before merging.",
                ]
            )
            pr_res = await export_to_github_tool.create_pull_request(
                app_id=app_id,
                repo_url=prior_repo_url,
                base_commit_sha=str(base_commit_sha).strip(),
                branch_name=branch_name,
                title=pr_title,
                body=pr_body,
                changes=patchset.get("changes") if isinstance(patchset.get("changes"), list) else [],  # type: ignore[arg-type]
                patch_id=str(patch_id) if patch_id else None,
                user_id=user_id,
            )
            pr_url = pr_res.get("prUrl") or pr_res.get("pr_url") or pr_res.get("url")

            # 6) Persist export metadata for chaining/visibility.
            try:
                await record_workflow_export(
                    app_id=app_id,
                    user_id=user_id,
                    workflow_type="app-generator",
                    repo_url=prior_repo_url,
                    job_id=str(pr_res.get("jobId") or pr_res.get("job_id") or "") or None,
                    meta={
                        "export_mode": "update_pr",
                        "patch_id": patch_id,
                        "base_snapshot_id": (base_snapshot or {}).get("snapshotId"),
                        "target_snapshot_id": (target_snapshot or {}).get("snapshotId"),
                        "base_commit_sha": base_commit_sha,
                        "changes_count": changes_count,
                        "conflicts_count": conflicts_count,
                    },
                    extra_fields={
                        "export_mode": "update_pr",
                        "patchId": patch_id,
                        "prUrl": pr_url,
                        "baseCommitSha": base_commit_sha,
                        "baseSnapshotId": (base_snapshot or {}).get("snapshotId"),
                        "targetSnapshotId": (target_snapshot or {}).get("snapshotId"),
                        "snapshotId": (target_snapshot or {}).get("snapshotId"),
                        "changesCount": changes_count,
                        "conflictsCount": conflicts_count,
                    },
                )
            except Exception as exc:
                wf_logger.warning("[EXPORT] Failed to record update PR metadata: %s", exc)

            return {
                "success": True,
                "workflow_type": "app-generator",
                "export_mode": "update_pr",
                "repo_url": prior_repo_url,
                "pr_url": pr_url,
                "patch_id": patch_id,
                "base_commit_sha": base_commit_sha,
                "changes_count": changes_count,
                "conflicts_count": conflicts_count,
                "conflicts": patchset.get("conflicts"),
            }
        except Exception as exc:
            error_msg = f"Update export failed: {exc}"
            wf_logger.warning(error_msg)
            return {
                "success": False,
                "workflow_type": "app-generator",
                "export_mode": "update_pr",
                "blocked": False,
                "error": error_msg,
                "repo_url": prior_repo_url,
            }

    # ------------------------------------------------------------------
    # INITIAL EXPORT FLOW: no repo yet -> create via deploy pipeline
    # ------------------------------------------------------------------
    result = await export_to_github_tool.execute(
        app_id=app_id,
        bundle_path=bundle_path,
        repo_name=repo_name,
        commit_message=commit_message,
        user_id=user_id,
        workflow_type="app-generator",
        context_variables=context_variables,
    )

    payload = result.model_dump()
    payload["workflow_type"] = "app-generator"
    payload["export_mode"] = "initial_export"

    if result.success:
        try:
            bundle_files = extract_files_from_zip_bundle(bundle_path)
            snapshot_doc = build_snapshot_document(
                app_id=app_id,
                session_id=session_id,
                workflow_type="app-generator",
                source="generated",
                files=bundle_files,
                structured_outputs=structured_outputs,
                repo_url=result.repo_url,
                base_commit_sha=result.base_commit_sha,
            )
            snapshot_id = await persist_snapshot(snapshot_doc=snapshot_doc)
        except Exception as snap_exc:
            snapshot_id = None
            wf_logger.warning("[EXPORT] Failed to persist initial export snapshot: %s", snap_exc)

        try:
            await record_workflow_export(
                app_id=app_id,
                user_id=user_id,
                workflow_type="app-generator",
                repo_url=result.repo_url,
                job_id=result.job_id,
                meta={"export_mode": "initial_export"},
                extra_fields={
                    "export_mode": "initial_export",
                    "snapshotId": snapshot_id,
                    "repoFullName": result.repo_full_name,
                    "baseCommitSha": result.base_commit_sha,
                    "workflowRunUrl": result.workflow_run_url,
                    "deploymentUrl": result.deployment_url,
                },
            )
        except Exception as exc:
            wf_logger.warning("[EXPORT] Failed to record app export metadata: %s", exc)

    return payload


__all__ = ["export_app_code_to_github", "resolve_export_gate"]

