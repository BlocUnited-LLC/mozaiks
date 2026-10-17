from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
import zipfile
from copy import deepcopy
from datetime import UTC, datetime

logger = logging.getLogger(__name__)
from pathlib import Path
from typing import Any, cast

import yaml

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    CodingWorkerResult,
    FileUpdate,
    StagedPatchProposal,
)
from mozaiksai.control_plane.implementations.acp_coding_provider import (
    ACPCodingProvider,
    acp_available,
)
from mozaiksai.control_plane.implementations.coding_provider_selection import (
    ACP_FALLBACK_STATUSES,
    select_coding_provider,
)
from mozaiksai.control_plane.implementations.structured_coding_provider import (
    StructuredOutputCodingProvider,
)
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.ports import CodingExecutionProvider
from mozaiksai.control_plane.workspace import (
    harvest_coding_workspace,
    materialize_coding_workspace,
)
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    get_artifact_store,
)
from mozaiksai.core.artifacts.content_store import get_artifact_content_store
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    RefinementSessionStatus,
    canonical_bundle_archive_path,
    resolve_canonical_bundle_entry,
)
from mozaiksai.core.semantics.implementation_artifacts import (
    ImplementationArtifactError,
    prove_workspace_handler_extension,
)
from mozaiksai.core.validation.generated_app import validate_generated_app_candidate
from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
    APP_VALIDATION_STRATEGIES,
    resolve_app_validation_strategy,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    SHARED_PACK_OUTPUT_PATHS,
)

_ELIGIBLE_CHANGE_CLASSES = {"patch"}
_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "workflow_bundle", "theme_capture"}
_PACK_PROVENANCE_PATH = ".mozaiks/pack_provenance.json"
# Only a scope the user named, or a proposal the user confirmed, can carry a
# change to app-owned pack code. Auto-applied proposals and surface plans cannot.
_AUTHORIZED_SCOPE_ORIGINS = frozenset({"explicit", "confirmed"})
#: The most records of one kind admission reads to admit a STALE parent: build
#: records of its family, change requests and refinement sessions made from it,
#: and CURRENT records of one upstream family. More cannot be checked: refused.
_STALE_PARENT_FAMILY_SCAN_LIMIT = 200
#: The most change requests admission follows back through the parent's
#: invalidations to the one that began while the parent was CURRENT.
_STALE_PARENT_ATTRIBUTION_DEPTH = 16
#: Acceptance evidence that names no time: the build was accepted, but when is unknown.
_ACCEPTED_AT_UNKNOWN = object()
#: How Studio's trigger records a change request that ran inline or only
#: returned a decision. Any other mode, such as ``workflow`` (written when a
#: workflow route is prepared, whether or not its launch then succeeded), may
#: have launched a workflow, whose outcome these records cannot resolve.
_INLINE_OR_DECISION_MODES = frozenset({"coding_worker", "surface_regeneration", "harness_decision"})
#: Refinement-session providers of Studio's inline coding and surface runs.
_INLINE_RUN_PROVIDERS = frozenset({"control_plane_coding", "contract_surface_regeneration"})
#: Validation statuses of a draft that is still open: reviewable (PASSED), or
#: unresolved (PENDING or SKIPPED: never acceptable, but not rejected either).
_OPEN_DRAFT_VALIDATION = frozenset({
    ArtifactValidationStatus.PASSED, ArtifactValidationStatus.PENDING, ArtifactValidationStatus.SKIPPED,
})
_CHANGE_REQUEST_REASON_PREFIX = "change_request:"
_REGISTRY_RUN_REFUSALS = {
    None: "no server read of the app registry was made before this run began",
    "run_in_flight": "another run of this app was building when this run began",
    "unverified": "the app registry could not be read before this run began",
}


def _closed_child(record: BuildRecord) -> bool:
    """A draft that can never become current and is not waiting on anyone: rejected, removed, or failed validation."""
    return record.lifecycle_status in {ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED} or (
        record.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        and record.validation_status == ArtifactValidationStatus.FAILED
    )


def _upstream_families(family: str) -> list[str] | None:
    """Every family ``family`` depends on, transitively, in the pack's artifact dependency graph; None when undeclared."""
    from mozaiksai.core.workflow.pack.config import load_global_pack_graph

    pack = load_global_pack_graph()
    graph = getattr(pack, "artifact_dependency_graph", None) if pack is not None else None
    if not isinstance(graph, dict) or family not in graph:
        return None
    upstream: set[str] = set()
    queue = list(graph[family] or [])
    while queue:
        name = str(queue.pop())
        if name and name != family and name not in upstream:
            upstream.add(name)
            queue.extend(graph.get(name) or [])
    return sorted(upstream)


def _made_current_at(record: BuildRecord) -> datetime | None:
    """When a CURRENT record became current: its acceptance marker, else its creation (saved CURRENT)."""
    acceptance = record.commit_metadata.metadata.get("acceptance")
    if isinstance(acceptance, dict) and acceptance.get("accepted_at"):
        return _utc_instant(acceptance["accepted_at"])
    return _utc_instant(record.created_at)


def _utc_instant(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).astimezone(UTC)


async def _accepted_at(artifact_store: Any, *, app_id: str, record: BuildRecord) -> Any:
    """When a build record was accepted, from what acceptance persisted, else None.

    Staling overwrites a record's CURRENT status, so the status alone cannot
    say whether a STALE build was ever accepted. The evidence, in order:

    * ``commit_metadata.metadata.acceptance.accepted_at``, which Studio's
      acceptance (and the staged-refinement acceptance) writes on the record;
    * an app bundle with no parent that records capability packs: only
      AppGenerator's genesis save writes one, and it saves it CURRENT;
    * a refinement session whose result is the record and that acceptance
      marked ACCEPTED (or a later promotion marked PROMOTED), for builds
      accepted before the record carried the marker.

    Returns the time, ``_ACCEPTED_AT_UNKNOWN`` when a session shows acceptance
    without a time, or None when nothing shows the record was accepted.
    """
    metadata = record.commit_metadata.metadata
    acceptance = metadata.get("acceptance")
    if isinstance(acceptance, dict) and acceptance.get("accepted_at"):
        return _utc_instant(acceptance["accepted_at"]) or _ACCEPTED_AT_UNKNOWN
    if record.parent_build_record_id is None and metadata.get("capability_packs"):
        return _utc_instant(record.created_at) or _ACCEPTED_AT_UNKNOWN
    sessions = await artifact_store.list_refinement_sessions(
        app_id=app_id, result_build_record_id=record.id, limit=20,
    )
    accepted = [
        session for session in sessions
        if session.result_build_record_id == record.id
        and session.status in {RefinementSessionStatus.ACCEPTED, RefinementSessionStatus.PROMOTED}
    ]
    if not accepted:
        return None
    times = [_utc_instant(session.ended_at) for session in accepted]
    return min(times) if all(times) else _ACCEPTED_AT_UNKNOWN  # type: ignore[type-var]


class CandidateAdmissionError(ValueError):
    """A candidate changes capability-pack output its scope cannot authorize."""

    def __init__(self, code: str, message: str, *, paths: list[str], **details: Any) -> None:
        super().__init__(f"{code}: {message}: {', '.join(sorted(paths))}")
        self.details = {"code": code, "message": message, "paths": sorted(paths), **details}


def pack_output_owners(capability_packs: list[dict[str, Any]]) -> dict[str, str]:
    """Map the declared outputs of a saved parent's verified packs to their owners.

    Ownership comes from the pinned pack contracts the parent selected, never
    from the candidate: ``templates`` and ``workspace`` as declared, and any
    other declared owner (``generator``: model-authored output) under its own
    name, which this admission leaves to the ordinary candidate rules.
    Composed shared roots are left out: assembly merges pack and app
    declarations there. Pack provenance is template-owned.
    """
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_declared_pack_output_paths,
        verified_selected_pack_sources,
    )

    verified = [
        {"id": entry["pack_id"], "pack_source_path": entry["source_path"]}
        for entry in verified_selected_pack_sources(capability_packs)
    ]
    if not verified:
        return {}
    owned = {
        owner: resolve_declared_pack_output_paths(verified, owner=owner) for owner in ("templates", "workspace")
    }
    owners = {
        path: next((owner for owner, paths in owned.items() if path in paths), "generator")
        for path in resolve_declared_pack_output_paths(verified)
        if path not in SHARED_PACK_OUTPUT_PATHS
    }
    owners[_PACK_PROVENANCE_PATH] = "templates"
    return owners


def pack_template_source(capability_packs: list[dict[str, Any]], path: str) -> str | None:
    """The verified pack template that ships ``path``, or None when no selected pack ships it."""
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_templates_for_pack,
        verified_selected_pack_sources,
    )

    for entry in verified_selected_pack_sources(capability_packs):
        for template in resolve_templates_for_pack(Path(entry["source_path"]), entry["pack_id"]):
            if template["filename"] == path:
                return str(template["content"])
    return None


def parent_workspace_pack_paths(capability_packs: Any) -> frozenset[str] | None:
    """App-owned pack extension paths a saved parent declares; None when unverifiable."""
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
    )

    if not isinstance(capability_packs, list) or any(not isinstance(pack, dict) for pack in capability_packs):
        return None
    try:
        owners = pack_output_owners(capability_packs)
    except (ManagedCapabilityTemplateError, OSError, yaml.YAMLError) as exc:
        logger.warning("PARENT_PACK_OWNERSHIP_UNRESOLVED: %s", exc)
        return None
    return frozenset(path for path, owner in owners.items() if owner == "workspace")


def resolve_coding_validation_strategy(raw: str | None) -> str:
    """Resolve operator/request policy before any coding provider executes."""
    normalized = str(raw or "").strip().lower() or None
    if normalized is not None and normalized not in APP_VALIDATION_STRATEGIES:
        raise ValueError(f"Unsupported coding validation strategy: {normalized}")
    return resolve_app_validation_strategy(requested=normalized)[0]

# Theme files live inside the app_bundle workspace. We alias the theme_capture artifact
# kind so the coding worker can locate and patch these files without requiring a
# separate staged theme workspace. Workspace scope tools fall back to the app_bundle
# artifact while theme_capture remains the persisted semantic input record.
_ARTIFACT_KIND_ALIASES: dict[str, str] = {
    "theme_capture": "app_bundle",
}


class ScopedRefinementCodingWorker:
    """First-party refinement coding worker for narrow refinement loops.

    This worker is intentionally conservative in v1. It is only eligible for
    scoped patch-style refinements and operates on explicit file payloads.

    The worker owns eligibility, validation, artifact persistence, and the
    checkpoint result shape. Producing the staged file changes is delegated to
    a :class:`~mozaiksai.control_plane.ports.CodingExecutionProvider`; the
    default provider is the single-shot structured-output provider.
    """

    def __init__(
        self,
        *,
        agent_factory: Any = None,
        agent_runner: AG2StructuredAgentRunner | None = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_refinement_harness,
        tool_executor: Any = None,
        candidate_validation_runner: Any = validate_generated_app_candidate,
        artifact_store: Any = None,
        output_root: Any = None,
        provider: CodingExecutionProvider | None = None,
        acp_provider: CodingExecutionProvider | None = None,
    ) -> None:
        self._structured_provider: CodingExecutionProvider = provider or StructuredOutputCodingProvider(
            agent_factory=agent_factory,
            agent_runner=agent_runner,
            config_loader=config_loader,
            pack_loader=pack_loader,
            tool_executor=tool_executor,
        )
        self._acp_provider: CodingExecutionProvider = acp_provider or ACPCodingProvider(
            config_loader=config_loader,
        )
        self._config_loader = config_loader
        self._candidate_validation_runner = candidate_validation_runner
        self._artifact_store = artifact_store
        self._output_root = Path(output_root) if output_root is not None else Path("generated_refinements")

    def enabled(self) -> bool:
        config = self._load_config()
        return bool(config.enabled and config.coding_enabled())

    async def execute(self, request: CodingWorkerRequest) -> CodingWorkerResult:
        eligible, blocked_reason = self._check_eligibility(request)
        if not eligible:
            return CodingWorkerResult(
                eligible=False,
                status="ineligible",
                blocked_reason=blocked_reason,
                metadata={"build_family": request.build_family, "change_class": request.change_class},
            )

        selection = select_coding_provider(request, self._load_config(), acp_importable=acp_available())
        provider_attempts: list[dict[str, str]] = []

        if selection.provider == "acp":
            proposal = await self._acp_provider.execute(request)
            provider_attempts.append(
                {"provider": proposal.provider_id, "status": proposal.status, "reason": selection.reason}
            )
            if proposal.status in ACP_FALLBACK_STATUSES:
                logger.warning(
                    "ACP_CODING_ATTEMPT_FELL_BACK app=%s status=%s: %s",
                    request.app_id,
                    proposal.status,
                    proposal.error,
                )
                proposal = await self._structured_provider.execute(request)
                provider_attempts.append(
                    {"provider": proposal.provider_id, "status": proposal.status, "reason": "acp_fallback"}
                )
        else:
            proposal = await self._structured_provider.execute(request)
            provider_attempts.append(
                {"provider": proposal.provider_id, "status": proposal.status, "reason": selection.reason}
            )

        return await self.finalize_proposal(request, proposal, provider_attempts=provider_attempts)

    async def finalize_proposal(
        self,
        request: CodingWorkerRequest,
        proposal: StagedPatchProposal,
        *,
        provider_attempts: list[dict[str, str]] | None = None,
    ) -> CodingWorkerResult:
        """Validate and persist scoped output without making another model call."""
        provider_attempts = list(provider_attempts or [])
        if proposal.status != "completed":
            return CodingWorkerResult(
                eligible=True,
                status="failed",
                provider=proposal.provider_id,
                error=proposal.error or "coding provider failed without an error message",
                metadata={
                    "build_family": request.build_family,
                    "change_class": request.change_class,
                    "coding_provider_attempts": provider_attempts,
                },
            )

        try:
            resolved_strategy = resolve_coding_validation_strategy(
                request.validation_strategy
            )
            resolved_plan = self._plan_from_proposal(
                request=request,
                proposal=proposal,
                resolved_strategy=resolved_strategy,
            )
            applied_files = {change.path: change.content for change in resolved_plan.updated_files}
        except Exception as exc:
            return CodingWorkerResult(
                eligible=True,
                status="failed",
                provider=proposal.provider_id,
                error=str(exc),
                metadata={
                    "build_family": request.build_family,
                    "change_class": request.change_class,
                    "coding_provider_attempts": provider_attempts,
                },
            )
        merged_files = dict(request.baseline_files if request.baseline_files is not None else request.files)
        merged_files.update(applied_files)

        # Resolve aliased artifact kinds so validation and persistence use the
        # backing store kind (e.g. theme_capture → app_bundle).
        resolved_artifact_kind = _ARTIFACT_KIND_ALIASES.get(request.build_family, request.build_family)

        validation_result = None
        capability_packs: list[dict[str, Any]] = []
        refinement_scope = self._refinement_scope(request)
        status = "planned"
        if resolved_artifact_kind == "app_bundle" and merged_files:
            try:
                capability_packs = await self._parent_capability_packs(request)
                refinement_scope = await self._admit_pack_outputs(
                    request=request, capability_packs=capability_packs,
                    applied_files=applied_files, merged_files=merged_files,
                )
                validation_result = await self._run_candidate_validation(
                    request=request,
                    merged_files=merged_files,
                    validation_strategy=resolved_strategy,
                    capability_packs=capability_packs,
                )
            except CandidateAdmissionError as exc:
                # Rejected before validation: no child record, nothing to review.
                return CodingWorkerResult(
                    eligible=True, status="failed", provider=proposal.provider_id, error=str(exc),
                    metadata={
                        "build_family": request.build_family,
                        "change_class": request.change_class,
                        "coding_provider_attempts": provider_attempts,
                        "candidate_admission": exc.details,
                        "refinement_scope": refinement_scope,
                    },
                )
            except Exception as exc:
                return CodingWorkerResult(
                    eligible=True, status="failed", provider=proposal.provider_id,
                    error=f"CANDIDATE_VALIDATION_FAILED: {exc}",
                    metadata={"coding_provider_attempts": provider_attempts},
                )
            validation_status = str((validation_result or {}).get("validation_status") or "").strip().lower()
            if validation_status == "passed":
                status = "validated"
            elif validation_status == "failed":
                status = "failed"
            else:
                status = "planned"

        provider_execution: dict[str, Any] = {
            "provider_id": proposal.provider_id,
            "provider_model": proposal.provider_model,
            "usage": proposal.usage,
            "attempts": provider_attempts,
            "events": [event.model_dump(mode="json") for event in proposal.provider_events],
        }
        metadata = {
            "build_family": request.build_family,
            "change_class": request.change_class,
            "coding_provider": provider_execution,
            "coding_provider_attempts": provider_attempts,
            "tool_context_loaded": proposal.tool_context_loaded,
            "applied_paths": sorted(applied_files.keys()),
            "applied_file_count": len(applied_files),
            "selected_file_paths": list((request.metadata or {}).get("selected_file_paths") or []),
            "refinement_scope": refinement_scope,
        }
        if validation_result is not None:
            validation_status = str((validation_result or {}).get("validation_status") or "").strip().lower()
            metadata["validation_status"] = validation_status
            metadata["app_validation_status"] = validation_result.get("app_validation_result", {}).get("validation_status")
        if isinstance((request.metadata or {}).get("scope_proposal"), dict):
            metadata["scope_proposal"] = dict(request.metadata["scope_proposal"])
        persistence_error: str | None = None
        if validation_result is not None:
            try:
                metadata.update(
                    await self._persist_refinement_artifact(
                        request=request,
                        resolved_artifact_kind=resolved_artifact_kind,
                        applied_files=applied_files,
                        merged_files=merged_files,
                        plan=resolved_plan,
                        validation_result=validation_result or {},
                        provider_execution=provider_execution,
                        capability_packs=capability_packs,
                        refinement_scope=refinement_scope,
                    )
                )
            except Exception as exc:
                persistence_error = f"ARTIFACT_PERSISTENCE_FAILED: {exc}"
                metadata["artifact_persistence_error"] = persistence_error
                logger.error(
                    "CODING_WORKER_PERSISTENCE_FAILED app=%s: %s",
                    request.app_id,
                    exc,
                    exc_info=True,
                )
                status = "failed"

        return CodingWorkerResult(
            eligible=True,
            status=status,  # type: ignore[arg-type]
            provider=proposal.provider_id,
            plan=resolved_plan,
            applied_files=applied_files,
            validation_result=validation_result,
            metadata=metadata,
            error=persistence_error
            or (self._validation_error(validation_result) if status == "failed" else None),
        )

    def _load_config(self) -> ControlPlaneConfig:
        config = self._config_loader()
        return config if isinstance(config, ControlPlaneConfig) else ControlPlaneConfig.model_validate(config)

    @staticmethod
    def _plan_from_proposal(
        *,
        request: CodingWorkerRequest,
        proposal: StagedPatchProposal,
        resolved_strategy: str,
    ) -> CodingWorkerPlan:
        """Reconstruct the checkpoint-facing plan from a provider proposal."""
        if any(change.op != "update" or change.content is None for change in proposal.changed_files):
            raise ValueError("Generated-app coding worker only supports updates to selected files")
        changed_paths = [change.path for change in proposal.changed_files]
        if not changed_paths:
            raise ValueError("Coding provider returned no file changes")
        if len(set(changed_paths)) != len(changed_paths):
            raise ValueError("Coding provider returned duplicate file changes")
        allowed_paths = set(request.files)
        outside_scope = sorted(set(changed_paths) - allowed_paths)
        if outside_scope:
            raise ValueError("Coding provider returned changes outside the approved file scope: " + ", ".join(outside_scope))
        unowned = sorted(set(changed_paths) - set(proposal.owned_paths))
        if unowned:
            raise ValueError("Coding provider returned changes outside its declared owned_paths: " + ", ".join(unowned))
        baseline = request.baseline_files if request.baseline_files is not None else request.files
        changes = [
            change for change in proposal.changed_files
            if change.path not in baseline or change.content != baseline[change.path]
        ]
        if not changes:
            raise ValueError("Coding provider returned no effective file changes")
        return CodingWorkerPlan(
            summary=proposal.summary,
            owned_paths=[path for path in proposal.owned_paths if path in allowed_paths],
            updated_files=[
                FileUpdate(path=change.path, content=cast(str, change.content)) for change in changes
            ],
            validation_strategy=cast(Any, resolved_strategy),
            validation_commands=list(proposal.validation_commands),
            start_preview=bool(request.start_preview or proposal.start_preview),
            needs_human_review=proposal.needs_human_review,
            rationale=proposal.rationale,
        )

    @staticmethod
    def _check_eligibility(request: CodingWorkerRequest) -> tuple[bool, str | None]:
        if request.read_only_files:
            return False, "generated-app coding worker cannot accept repository inspection files"
        try:
            resolve_coding_validation_strategy(request.validation_strategy)
        except ValueError as exc:
            return False, str(exc)
        if not str(request.app_id or "").strip():
            return False, "app_id is required"
        if str(request.change_class or "").strip().lower() not in _ELIGIBLE_CHANGE_CLASSES:
            return False, "coding worker only supports patch refinements in v1"
        if str(request.build_family or "").strip() not in _ELIGIBLE_ARTIFACT_KINDS:
            return False, "coding worker only supports app_bundle or workflow_bundle artifacts"
        if not str(request.build_record_id or "").strip():
            return False, "coding worker requires build_record_id for scoped refinement"
        if not isinstance(request.files, dict) or not request.files:
            return False, "coding worker requires explicit scoped files in v1"
        return True, None

    async def _parent_capability_packs(self, request: CodingWorkerRequest) -> list[dict[str, Any]]:
        """Only the saved parent may authorize pack-owned candidate outputs."""
        parent_id = request.build_record_id
        if parent_id is None or not parent_id.strip():
            raise ValueError("Refinement parent build record ID is required")
        artifact_store = self._artifact_store or get_artifact_store()
        parent = await artifact_store.get_build_record(
            app_id=request.artifact_app_id, build_record_id=parent_id,
        )
        if parent is None:
            raise ValueError("Refinement parent build record was not found for the target app")
        packs = parent.commit_metadata.metadata.get("capability_packs", [])
        if not isinstance(packs, list) or any(not isinstance(pack, dict) for pack in packs):
            raise ValueError("Refinement parent capability_packs must be a list of selected pack descriptors")
        return deepcopy(packs)

    @staticmethod
    def _refinement_scope(request: CodingWorkerRequest) -> dict[str, Any]:
        metadata = request.metadata or {}
        return {
            "selected_file_paths": sorted(str(path) for path in metadata.get("selected_file_paths") or []),
            "scope_origin": str(metadata.get("scope_origin") or "unspecified"),
            "changed_workspace_pack_paths": [],
        }

    async def _current_accepted_build_record_id(self, request: CodingWorkerRequest) -> str | None:
        """The app's accepted current build: the artifact store's CURRENT record of this family.

        Accepting a review draft makes a build current. Studio marks the
        selected build stale only after an operation changed it or left its
        outcome unresolved: a run that stored a child whose validation passed,
        is pending or was skipped, or a workflow that launched. A run rejected
        before it stored a child, or one whose only stored child failed
        validation, leaves the accepted build current, so a later authorized
        change is still admitted against it.
        """
        family = _ARTIFACT_KIND_ALIASES.get(request.build_family, request.build_family)
        artifact_store = self._artifact_store or get_artifact_store()
        refs = await artifact_store.get_current_build_record_refs(app_id=request.artifact_app_id)
        return str((refs or {}).get(family) or "").strip() or None

    async def _stale_parent_refusal(self, request: CodingWorkerRequest) -> str | None:
        """Why a STALE parent cannot be admitted for an app-owned pack change while no build is CURRENT; None when it can.

        Nothing makes a stale build current again, so a rejected draft leaves
        the app with no CURRENT build. The stale marker that rejected draft's
        run left is told apart from genuine invalidation, newer accepted work
        and work still in flight using the records Studio already keeps. The
        parent is admitted, without changing any record, only when all hold:

        * no build of its family is CURRENT, and the parent is STALE;
        * it was accepted (:func:`_accepted_at`) and no build of the family was
          accepted after it;
        * attribution: its ``invalidation_reason`` names a change request made
          from it by this user, recorded as an inline run or a returned
          decision (never a workflow route), that saved the parent's state
          when it began, and whose inline run's stored draft is now rejected
          or failed validation. A change request that began while the parent
          was already stale is followed back, under the same conditions, to
          the one that began while the parent was CURRENT;
        * no other work: every other change request made from the parent ran
          inline or only returned a decision, every draft made from it is
          rejected, removed or failed validation, no draft further down its
          lineage is open (validation passed, pending or skipped), and every
          refinement session of it is an inline run;
        * nothing in flight: Studio's registry read before this run began
          (``registry_run_state``, server-derived) found no other run building;
        * compatible inputs: every family the parent's family depends on in the
          pack's artifact dependency graph is unchanged since the parent was
          accepted. A recorded input (``canonical_inputs_version``) must still
          be CURRENT; any other CURRENT record must have become current no
          later than the parent was accepted; a family that is STALE with no
          CURRENT record refuses.

        The retry stays bound to this parent, the authenticated user and the
        explicit or confirmed file scope checked by the caller. A lookup
        failure, a scan past its bound, or anything else unresolved refuses.
        Acceptance rechecks the parent, scope and registry run as for a
        CURRENT parent. These are reads at admission, not a compare-and-set.
        """
        if request.registry_run_state != "idle":
            return _REGISTRY_RUN_REFUSALS.get(request.registry_run_state, _REGISTRY_RUN_REFUSALS[None])
        family = _ARTIFACT_KIND_ALIASES.get(request.build_family, request.build_family)
        artifact_store = self._artifact_store or get_artifact_store()
        app_id = request.artifact_app_id
        limit = _STALE_PARENT_FAMILY_SCAN_LIMIT
        if not request.build_record_id:
            return "the request names no parent build"
        try:
            parent = await artifact_store.get_build_record(app_id=app_id, build_record_id=request.build_record_id)
            if parent is None or parent.build_family != family:
                return "the parent build was not found in this app's build family"
            if parent.lifecycle_status != ArtifactLifecycleStatus.STALE:
                return f"the parent is {ArtifactLifecycleStatus(parent.lifecycle_status).value}, not stale"
            records = await artifact_store.list_build_records(app_id=app_id, build_family=family, limit=limit + 1)
            if len(records) > limit:
                return f"the build family has more than {limit} records to check"
            by_id = {record.id: record for record in records}
            if parent.id not in by_id:
                return "the parent is missing from its build family's records"
            current = sorted(record.id for record in records if record.lifecycle_status == ArtifactLifecycleStatus.CURRENT)
            if current:
                return f"a build of this family is current: {current[0]}"
            accepted_at = await _accepted_at(artifact_store, app_id=app_id, record=parent)
            if accepted_at is None:
                return "nothing shows the parent was ever accepted"
            if not isinstance(accepted_at, datetime):
                return "the parent's acceptance time is unknown"
            for record in records:
                if record.id == parent.id or record.lifecycle_status != ArtifactLifecycleStatus.STALE:
                    continue
                written_at = _utc_instant(record.updated_at)
                if written_at is not None and written_at < accepted_at:
                    continue  # last written before the parent was accepted, so not accepted after it
                other = await _accepted_at(artifact_store, app_id=app_id, record=record)
                if other is _ACCEPTED_AT_UNKNOWN or (isinstance(other, datetime) and other >= accepted_at):
                    return f"build {record.id} was accepted after the parent"
            children = sorted(
                (record for record in records if record.parent_build_record_id == parent.id), key=lambda r: r.id,
            )
            for child in children:
                if not _closed_child(child):
                    return (
                        f"draft {child.id} made from the parent is "
                        f"{ArtifactLifecycleStatus(child.lifecycle_status).value} with validation "
                        f"{ArtifactValidationStatus(child.validation_status).value}, not rejected"
                    )
            below = [child.id for child in children]
            seen: set[str] = set(below)
            while below:
                ancestor = below.pop()
                for record in records:
                    if record.parent_build_record_id != ancestor or record.id in seen:
                        continue
                    seen.add(record.id)
                    below.append(record.id)
                    if (
                        record.lifecycle_status == ArtifactLifecycleStatus.DRAFT
                        and record.validation_status in _OPEN_DRAFT_VALIDATION
                    ):
                        return f"draft {record.id}, made further down from the parent, is still open"
            refusal = await self._attribution_refusal(
                artifact_store, request=request, parent=parent, records=by_id,
            )
            if refusal is not None:
                return refusal
            return await self._upstream_input_refusal(
                artifact_store, app_id=app_id, parent=parent, family=family, accepted_at=accepted_at,
            )
        except Exception as exc:
            logger.warning(
                "STALE_PARENT_ADMISSION_UNVERIFIED app=%s parent=%s", app_id, request.build_record_id, exc_info=True,
            )
            return f"a record needed to check the parent could not be read ({type(exc).__name__})"

    @staticmethod
    async def _attribution_refusal(
        artifact_store: Any, *, request: CodingWorkerRequest, parent: BuildRecord, records: dict[str, BuildRecord],
    ) -> str | None:
        """Whether the parent's stale marker is only this app's own closed inline runs, and no other work."""
        limit = _STALE_PARENT_FAMILY_SCAN_LIMIT
        app_id = request.artifact_app_id
        reason = str(parent.invalidation_reason or "")
        if not reason.startswith(_CHANGE_REQUEST_REASON_PREFIX):
            return "the parent's invalidation names no change request"
        changes = await artifact_store.list_change_requests(app_id=app_id, build_record_id=parent.id, limit=limit + 1)
        if len(changes) > limit:
            return f"more than {limit} change requests were made from the parent"
        made_from_parent = {change.id: change for change in changes if change.build_record_id == parent.id}
        sessions = await artifact_store.list_refinement_sessions(
            app_id=app_id, build_record_id=parent.id, limit=limit + 1,
        )
        if len(sessions) > limit:
            return f"the parent has more than {limit} refinement sessions"
        runs: dict[str, list[Any]] = {}
        for session in sessions:
            if session.build_record_id != parent.id:
                continue
            if session.provider not in _INLINE_RUN_PROVIDERS:
                return f"refinement session {session.id} of the parent is not an inline run ({session.provider})"
            if session.change_request_id not in made_from_parent:
                return (
                    f"refinement session {session.id} names change request {session.change_request_id}, "
                    "not one made from the parent"
                )
            runs.setdefault(session.change_request_id, []).append(session)

        chain: list[str] = []
        link = reason.removeprefix(_CHANGE_REQUEST_REASON_PREFIX)
        for _ in range(_STALE_PARENT_ATTRIBUTION_DEPTH):
            change = made_from_parent.get(link)
            if change is None or link in chain:
                return f"the parent was invalidated by {link}, which is not a change request made from it"
            if change.created_by_user_id != request.user_id:
                return f"change request {link} that invalidated the parent was made by another user"
            decision = change.router_decision if isinstance(change.router_decision, dict) else {}
            mode = decision.get("execution_mode")
            if mode not in _INLINE_OR_DECISION_MODES:
                return f"change request {link} that invalidated the parent may have launched a workflow ({mode})"
            state = decision.get("baseline_state")
            if not isinstance(state, dict) or state.get("build_record_id") != parent.id:
                return f"change request {link} that invalidated the parent saved no state of it when it began"
            if not runs.get(link):
                return f"change request {link} that invalidated the parent has no inline run on record"
            for session in runs[link]:
                child = records.get(str(session.result_build_record_id or ""))
                if child is None or child.parent_build_record_id != parent.id:
                    return f"the run of change request {link} names no stored draft of the parent"
                if not _closed_child(child):
                    return f"draft {child.id} of change request {link} is neither rejected nor failed"
            chain.append(link)
            status = state.get("lifecycle_status")
            if status == ArtifactLifecycleStatus.CURRENT.value:
                break
            prior = str(state.get("invalidation_reason") or "")
            if status != ArtifactLifecycleStatus.STALE.value or not prior.startswith(_CHANGE_REQUEST_REASON_PREFIX):
                return (
                    f"the parent was already {status} when change request {link} began, "
                    "and not by a change request made from it"
                )
            link = prior.removeprefix(_CHANGE_REQUEST_REASON_PREFIX)
        else:
            return f"the parent's invalidations go back more than {_STALE_PARENT_ATTRIBUTION_DEPTH} change requests"
        for change_id, change in sorted(made_from_parent.items()):
            decision = change.router_decision if isinstance(change.router_decision, dict) else {}
            if change_id not in chain and decision.get("execution_mode") not in _INLINE_OR_DECISION_MODES:
                return f"change request {change_id} made from the parent may have launched a workflow"
        return None

    @staticmethod
    async def _upstream_input_refusal(
        artifact_store: Any, *, app_id: str, parent: BuildRecord, family: str, accepted_at: datetime,
    ) -> str | None:
        """Whether a family the parent depends on changed since the parent was accepted."""
        limit = _STALE_PARENT_FAMILY_SCAN_LIMIT
        upstream = _upstream_families(family)
        if upstream is None:
            return f"the artifact dependency graph declares no inputs for {family}"
        recorded = dict(parent.canonical_inputs_version or {})
        for name in upstream:
            current = await artifact_store.list_build_records(
                app_id=app_id, build_family=name, lifecycle_status=ArtifactLifecycleStatus.CURRENT, limit=limit + 1,
            )
            if len(current) > limit:
                return f"input family {name} has more than {limit} current records"
            if name in recorded and recorded[name] not in {record.id for record in current}:
                return f"input {name} {recorded[name]} the parent was built from is no longer current"
            for record in current:
                if record.id == recorded.get(name):
                    continue
                made_current = _made_current_at(record)
                if made_current is None or made_current > accepted_at:
                    return f"input {name} {record.id} became current after the parent was accepted"
            if not current and await artifact_store.list_build_records(
                app_id=app_id, build_family=name, lifecycle_status=ArtifactLifecycleStatus.STALE, limit=1,
            ):
                return f"input {name} is stale with no current build"
        return None

    async def _admit_pack_outputs(
        self,
        *,
        request: CodingWorkerRequest,
        capability_packs: list[dict[str, Any]],
        applied_files: dict[str, str],
        merged_files: dict[str, str],
    ) -> dict[str, Any]:
        """Hold the candidate to the saved parent's pack ownership before validation.

        Template-owned outputs stay byte-identical to the verified parent. An
        app-owned (``owner: workspace``) output may change only inside an
        explicit or user-confirmed selected-file scope, from the app's accepted
        current build (read here, at admission) or, when no build is CURRENT,
        from a STALE parent whose stale marker only this app's own rejected or
        failed inline runs left, with nothing else open, in flight or changed
        since (:meth:`_stale_parent_refusal`), and, for a workspace handler,
        only as a thin extension of its template
        base handler. Applied paths are real changes against the parent, so any
        owned path among them is a change. Acceptance rechecks the parent and
        scope before a child becomes current.
        """
        from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
            ManagedCapabilityTemplateError,
        )

        scope = self._refinement_scope(request)
        if not capability_packs:
            return scope
        try:
            owners = pack_output_owners(capability_packs)
        except (ManagedCapabilityTemplateError, OSError, yaml.YAMLError) as exc:
            # Candidate validation verifies the same selected sources and fails
            # this candidate, so it is saved as failed and never promotable.
            logger.warning("CANDIDATE_PACK_OWNERSHIP_UNRESOLVED app=%s: %s", request.app_id, exc)
            return scope
        changed = sorted(applied_files)
        drifted = [path for path in changed if owners.get(path) == "templates"]
        if drifted:
            raise CandidateAdmissionError(
                "PACK_TEMPLATE_OUTPUT_CHANGED",
                "template-owned capability pack outputs must stay byte-identical to the verified parent",
                paths=drifted,
            )
        module_roots = {
            "/".join(path.split("/")[:2]) + "/" for path in owners if path.startswith("modules/")
        }
        undeclared = [
            path for path in changed
            if path not in owners and any(path.startswith(root) for root in module_roots)
        ]
        if undeclared:
            raise CandidateAdmissionError(
                "PACK_MODULE_CLOSURE_CHANGED",
                "a capability pack module keeps its exact declared file set",
                paths=undeclared,
            )
        workspace = [path for path in changed if owners.get(path) == "workspace"]
        scope["changed_workspace_pack_paths"] = workspace
        if not workspace:
            return scope
        selected = set(scope["selected_file_paths"]) & set(request.files)
        if scope["scope_origin"] not in _AUTHORIZED_SCOPE_ORIGINS or not set(workspace) <= selected:
            raise CandidateAdmissionError(
                "WORKSPACE_PACK_OUTPUT_UNAUTHORIZED",
                "app-owned pack code changes only inside an explicit or user-confirmed selected-file scope",
                paths=workspace, scope_origin=scope["scope_origin"],
            )
        current = await self._current_accepted_build_record_id(request)
        if current != request.build_record_id:
            refusal = (
                await self._stale_parent_refusal(request) if current is None
                else f"build {current} is the app's accepted current build"
            )
            if refusal is not None:
                raise CandidateAdmissionError(
                    "STALE_REFINEMENT_BASELINE",
                    "app-owned pack code changes only from the app's accepted current build, or, while none is "
                    "current, from a stale build whose only invalidation is this app's own rejected or failed "
                    "change with nothing else open, in flight or changed since",
                    paths=workspace, parent_build_record_id=request.build_record_id,
                    accepted_build_record_id=current, stale_baseline_refusal=refusal,
                )
        for path in workspace:
            if path.endswith("/backend/handler.py"):
                self._prove_handler_extension(
                    path, owners=owners, merged_files=merged_files, capability_packs=capability_packs,
                )
        return scope

    @staticmethod
    def _prove_handler_extension(
        path: str, *, owners: dict[str, str], merged_files: dict[str, str], capability_packs: list[dict[str, Any]],
    ) -> None:
        from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
            ManagedCapabilityTemplateError,
        )

        module_root = path[: -len("backend/handler.py")]
        base_path, manifest_path = module_root + "backend/base_handler.py", module_root + "module.yaml"
        try:
            if owners.get(base_path) != "templates" or base_path not in merged_files:
                raise ImplementationArtifactError("a workspace handler needs its template-owned base handler")
            template = pack_template_source(capability_packs, path)
            if template is None:
                raise ImplementationArtifactError("a workspace handler needs the pack template that ships it")
            manifest = yaml.safe_load(merged_files.get(manifest_path) or "") or {}
            module = manifest.get("module") if isinstance(manifest, dict) else None
            reference = str(module.get("handler") or "") if isinstance(module, dict) else ""
            module_path, _, handler_class = reference.partition(":")
            if module_path != "backend.handler" or not handler_class:
                raise ImplementationArtifactError(f"{manifest_path} must declare backend.handler:<class>")
            prove_workspace_handler_extension(
                handler_source=merged_files[path], base_handler_source=merged_files[base_path],
                handler_class=handler_class, template_handler_source=template,
                action_methods=[
                    str(action.get("handler_method") or "")
                    for action in manifest.get("actions") or [] if isinstance(action, dict)
                ],
            )
        except (ImplementationArtifactError, ManagedCapabilityTemplateError, OSError, yaml.YAMLError) as exc:
            raise CandidateAdmissionError(
                "WORKSPACE_HANDLER_EXTENSION_REJECTED", str(exc), paths=[path],
            ) from exc

    async def _run_candidate_validation(
        self,
        *,
        request: CodingWorkerRequest,
        merged_files: dict[str, str],
        validation_strategy: str,
        capability_packs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Reject unsafe/secret-sensitive files before the validator can execute
        # the complete candidate. Provider context and commands are not policy.
        self._output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="validation-", dir=self._output_root.resolve()) as root:
            materialize_coding_workspace(merged_files, workspace_root=Path(root))
            result = await self._candidate_validation_runner(
                files=dict(merged_files),
                app_id=request.artifact_app_id,
                validation_strategy=validation_strategy,
                capability_packs=deepcopy(capability_packs),
                timeout_seconds=self._bounded_int(
                    request.metadata.get("validation_timeout_seconds"), default=120, minimum=5, maximum=900,
                ),
            )
        if not isinstance(result, dict):
            raise ValueError("Candidate validator returned no acceptance/build evidence")
        payload = dict(result)
        acceptance = payload.get("app_bundle_acceptance_result")
        build = payload.get("app_validation_result")
        if not isinstance(acceptance, dict) or not isinstance(build, dict):
            raise ValueError("Candidate validator returned incomplete acceptance/build evidence")
        if payload.get("validation_status") == "passed" and not (
            acceptance.get("passed") is True and build.get("validation_status") == "passed"
        ):
            raise ValueError("Candidate validator passed without completed acceptance and build checks")
        return payload

    async def _persist_refinement_artifact(
        self,
        *,
        request: CodingWorkerRequest,
        resolved_artifact_kind: str,
        applied_files: dict[str, str],
        merged_files: dict[str, str],
        plan: CodingWorkerPlan,
        validation_result: dict[str, Any],
        capability_packs: list[dict[str, Any]],
        provider_execution: dict[str, Any] | None = None,
        refinement_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        build_key = str(request.build_key or resolved_artifact_kind or "artifact").strip() or "artifact"
        bundle_token = uuid.uuid4().hex[:12]
        # Use the resolved (aliased) kind for file system layout so theme patches
        # land alongside app_bundle artifacts, not in a separate tree.
        bundle_root = self._output_root / request.artifact_app_id / resolved_artifact_kind / build_key / bundle_token
        workspace_dir = bundle_root / "workspace"

        try:
            staged_workspace = materialize_coding_workspace(merged_files, workspace_root=workspace_dir)
        except OSError as exc:
            raise RuntimeError(
                f"STAGING_WRITE_FAILED: could not write staging workspace at {workspace_dir} — {exc}"
            ) from exc
        harvest = harvest_coding_workspace(staged_workspace)
        if not harvest.clean:
            details = "; ".join(f"{violation.path} ({violation.kind})" for violation in harvest.violations)
            raise RuntimeError(
                f"STAGING_VERIFICATION_FAILED: workspace harvest found scope violations: {details}"
            )
        written_paths = sorted(staged_workspace.editable_manifest)

        bundle_name = f"refinement_{bundle_token}"
        zip_path = bundle_root / f"{bundle_name}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for rel_path in sorted(written_paths):
                    zipf.write(workspace_dir / rel_path, arcname=f"{bundle_name}/{rel_path}")
        except OSError as exc:
            raise RuntimeError(
                f"ARTIFACT_ZIP_FAILED: could not create artifact bundle at {zip_path} — {exc}"
            ) from exc

        zip_bytes = zip_path.read_bytes()
        zip_sha = hashlib.sha256(zip_bytes).hexdigest()

        # Persist to content store if a non-local backend is configured.
        commit_content_metadata: dict[str, Any] = {
            **(request.run_build_binding.model_dump() if request.run_build_binding else {}),
            "artifact_path": str(zip_path.resolve()),
            "bundle_name": bundle_name,
            "workspace_dir": str(workspace_dir.resolve()),
            "bundle_mode": "staged_refinement_bundle",
            "applied_paths": sorted(applied_files.keys()),
            "validation_strategy": plan.validation_strategy,
            "validation_status": "",  # filled after status is resolved below
            "validation_result": validation_result,
            "app_bundle_acceptance": validation_result["app_bundle_acceptance_result"],
            "validation_evidence": validation_result["app_bundle_acceptance_result"].get("validation_evidence"),
            "app_validation_result": validation_result["app_validation_result"],
            "source_surface": request.source_surface,
            "staged_file_sha256": dict(staged_workspace.editable_manifest),
            "coding_provider": provider_execution,
            "capability_packs": deepcopy(capability_packs),
            # Who chose the written scope stays with the record it authorized.
            "refinement_scope": deepcopy(refinement_scope or {}),
        }
        content_store = get_artifact_content_store()
        if content_store.backend_name != "local":
            try:
                content_ref = await content_store.put_bundle(
                    zip_bytes,
                    app_id=request.artifact_app_id,
                    artifact_version_id=f"pending_{zip_sha[:16]}",
                )
                if not content_ref:
                    raise ValueError("Configured content store returned no bundle reference")
                commit_content_metadata["content_ref"] = content_ref
                commit_content_metadata["content_backend"] = content_store.backend_name
            except Exception as cs_exc:
                raise RuntimeError(
                    f"CONTENT_STORE_PUT_BUNDLE_FAILED: {cs_exc}"
                ) from cs_exc

        artifact_store = self._artifact_store or get_artifact_store()
        validation_status = self._artifact_validation_status(validation_result)
        build_result = validation_result["app_validation_result"]
        commit_content_metadata["validation_status"] = validation_status.value
        artifact_version = await artifact_store.create_build_record(
            app_id=request.artifact_app_id,
            build_family=resolved_artifact_kind,
            build_key="app_bundle",
            parent_build_record_id=request.build_record_id,
            source_workflow=request.requested_workflow_id or "control_plane_coding",
            source_chat_id=None,
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
            validation_status=validation_status,
            app_validation_status=build_result.get("validation_status"),
            app_validation_strategy=build_result.get("validation_strategy"),
            sandbox_session_id=build_result.get("sandbox_session_id"),
            sandbox_provider=build_result.get("sandbox_provider"),
            files_manifest=[
                {
                    "path": canonical_bundle_archive_path(bundle_name),
                    "sha256": zip_sha,
                    "size_bytes": zip_path.stat().st_size,
                    "content_type": "application/zip",
                }
            ],
            commit_metadata={
                "message": plan.summary,
                "author_user_id": request.user_id,
                "source_workflow": request.requested_workflow_id or "control_plane_coding",
                "metadata": commit_content_metadata,
            },
        )
        if resolve_canonical_bundle_entry(artifact_version).sha256 != zip_sha:
            raise RuntimeError("Saved candidate archive identity differs from the validated bundle")
        return {
            "build_record_id": artifact_version.id,
            "artifact_path": str(zip_path.resolve()),
            "workspace_dir": str(workspace_dir.resolve()),
            "bundle_mode": "staged_refinement_bundle",
            "validation_status": validation_status.value,
            "app_validation_status": build_result.get("validation_status"),
        }

    @staticmethod
    def _artifact_validation_status(validation_result: dict[str, Any]) -> ArtifactValidationStatus:
        status = str((validation_result or {}).get("validation_status") or "").strip().lower()
        if status == "passed":
            return ArtifactValidationStatus.PASSED
        if status == "skipped":
            return ArtifactValidationStatus.SKIPPED
        if status == "failed":
            return ArtifactValidationStatus.FAILED
        return ArtifactValidationStatus.PENDING

    @staticmethod
    def _validation_error(validation_result: dict[str, Any] | None) -> str | None:
        if not isinstance(validation_result, dict):
            return None
        if validation_result.get("error"):
            return str(validation_result["error"])
        errors = validation_result.get("errors")
        if isinstance(errors, list) and errors:
            return str(errors[0])
        return None

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(number, maximum))


_coding_worker: ScopedRefinementCodingWorker | None = None


def get_coding_worker() -> ScopedRefinementCodingWorker:
    global _coding_worker
    if _coding_worker is None:
        _coding_worker = ScopedRefinementCodingWorker()
    return _coding_worker
