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
from typing import Any, Literal, NamedTuple, cast

import yaml

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    BUILD_ACCEPTANCE_TRANSITIONS,
    CodingWorkerPlan,
    CodingWorkerRequest,
    CodingWorkerResult,
    FileUpdate,
    RegistryRunState,
    StagedPatchProposal,
    acceptance_evidence_names,
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
#: The most change requests one check reads to show, from the state they saved,
#: that STALE records without acceptance evidence were never accepted
#: (:func:`_never_accepted_refusal`). More cannot be checked: refused.
_STALE_STATE_LOOKUP_LIMIT = 64
#: What brownfield Genesis acceptance leaves on its build: no acceptance evidence.
_GENESIS_IMPORT_BUNDLE_MODE = "brownfield_genesis_import"
#: How Studio's trigger records a change request that ran inline or only
#: returned a decision. Any other mode, such as ``workflow`` (written when a
#: workflow route is prepared, whether or not its launch then succeeded), may
#: have launched a workflow, whose outcome these records cannot resolve.
_INLINE_OR_DECISION_MODES = frozenset({"coding_worker", "surface_regeneration", "harness_decision"})
#: Refinement-session providers of Studio's inline coding and surface runs.
_INLINE_RUN_PROVIDERS = frozenset({"control_plane_coding", "contract_surface_regeneration"})
_CHANGE_REQUEST_REASON_PREFIX = "change_request:"
_REGISTRY_RUN_REFUSALS = {
    None: "no server read of the app registry was made before this run began",
    "run_in_flight": "another run of this app was building when this run began",
    "unverified": "the app registry could not be read before this run began",
}
#: The way forward from a stale build no retry from it can resolve: a newly
#: accepted build is current, and app-owned pack code is admitted against it.
_ACCEPT_A_NEW_BUILD = "accept a new build of the app, then change its app-owned capability code from that build"


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


def _utc_instant(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).astimezone(UTC)


def _accepted_at(record: BuildRecord) -> Any:
    """When a build record was accepted, from the acceptance evidence that names it; None without such evidence.

    Staling overwrites a record's CURRENT status, so the status alone cannot
    say whether a STALE build was ever accepted; the evidence can. Only an
    authorized acceptance writes it (``contracts.BUILD_ACCEPTANCE_TRANSITIONS``:
    Studio's acceptance of a draft and the staged-refinement acceptance), in
    the write that makes the build current, as
    ``commit_metadata.metadata.acceptance``: the build's app, build family,
    build key and record id, the authenticated principal, the time and the
    transition. A newly generated build, the first one included, is a draft
    and carries none.

    Evidence counts only on the record it names
    (``contracts.acceptance_evidence_names``). A marker copied or inherited
    from another record, one that names another app, key or record, and one
    written before acceptance named its build, are not this record's
    evidence, so they show nothing about it; nor does its shape, its
    validation or its refinement sessions. Brownfield Genesis acceptance writes
    no evidence (``accept_genesis_build_record``, owned by Genesis import), so
    once such a build is stale it is not shown accepted either.

    Returns the time (never earlier than the record's creation),
    ``_ACCEPTED_AT_UNKNOWN`` when evidence names this record but its time,
    principal or transition cannot be read, or None when no evidence names it.
    """
    evidence = record.commit_metadata.metadata.get("acceptance")
    if not isinstance(evidence, dict) or not acceptance_evidence_names(record, evidence):
        return None
    marked = _utc_instant(evidence.get("accepted_at"))
    principal = evidence.get("accepted_by")
    if (
        marked is None or not isinstance(principal, str) or not principal.strip()
        or evidence.get("transition") not in BUILD_ACCEPTANCE_TRANSITIONS
    ):
        return _ACCEPTED_AT_UNKNOWN
    created = _utc_instant(record.created_at)
    return max(marked, created) if created is not None else marked


def _genesis_import_without_evidence(record: BuildRecord) -> bool:
    """Whether ``record`` is an imported Genesis build that carries no acceptance evidence naming it."""
    return (
        record.commit_metadata.metadata.get("bundle_mode") == _GENESIS_IMPORT_BUNDLE_MODE
        and _accepted_at(record) is None
    )


def _genesis_import_dependency(record_id: str) -> str:
    """Why a path through an imported Genesis build without evidence stays blocked, naming the owner it waits on."""
    return (
        f"build {record_id} is an imported Genesis build, and accepting imported Genesis "
        "(accept-genesis, BuildRecordStore.accept_genesis_build_record, owned by Genesis import) records no "
        "acceptance evidence yet, so this path stays blocked until that owner writes it"
    )


def _made_current_at(record: BuildRecord) -> Any:
    """The earliest a CURRENT record can have become current: :func:`_accepted_at`, else its creation.

    A CURRENT record without acceptance evidence naming it was saved CURRENT
    by a writer that records none (such as AgentGenerator's workflow bundles
    or the App Intelligence index), made current by brownfield Genesis
    acceptance, which writes none, or accepted before acceptance named its
    build. Which one cannot be told, but none can have become current before
    it was created, so its creation is used: ordering an accepted base from
    that earliest time reads every record written since then and counts each
    acceptance from then on as newer, which can only refuse more, never less.
    """
    accepted = _accepted_at(record)
    if accepted is not None:
        return accepted
    return _utc_instant(record.created_at) or _ACCEPTED_AT_UNKNOWN


def _became_current_by(record: BuildRecord) -> Any:
    """The latest a CURRENT record can have become current: :func:`_accepted_at`, else its last write.

    Without acceptance evidence naming it, the write that made the record
    current was no later than its last write (``updated_at``), so asking
    whether it became current after a given time from that latest time
    refuses whenever it may have; a record saved CURRENT and not written since
    has the same creation and last-write time.
    """
    accepted = _accepted_at(record)
    if accepted is not None:
        return accepted
    return _utc_instant(record.updated_at) or _ACCEPTED_AT_UNKNOWN


#: Lifecycle states of a record that was never accepted: acceptance takes only a
#: DRAFT to CURRENT, nothing returns a record to DRAFT, and rejection archives
#: only drafts.
_NEVER_ACCEPTED_STATES = frozenset({ArtifactLifecycleStatus.DRAFT, ArtifactLifecycleStatus.ARCHIVED})
#: The most saved builds :func:`newer_accepted_work` follows from a build back to
#: the nearest one that was accepted.
NEWER_ACCEPTED_WORK_MAX_DEPTH = 32
#: The most builds of one build family and key :func:`newer_accepted_work` reads:
#: the CURRENT ones, and those written since the accepted base's acceptance.
_NEWER_ACCEPTED_WORK_SCAN_LIMIT = 200


def _acceptance_of(record: BuildRecord) -> Any:
    """When a record became accepted work, for ordering; None if nothing shows it ever was.

    DRAFT, ARCHIVED and DELETED records were never accepted. A CURRENT record
    is accepted work since it became current (:func:`_made_current_at`). A
    SUPERSEDED record was current until another acceptance replaced it, so it
    was accepted even when no acceptance evidence names it; then when is
    unknown (``_ACCEPTED_AT_UNKNOWN``). Staling overwrites the status of drafts
    and accepted builds alike, so a STALE record shows acceptance only by its
    evidence (:func:`_accepted_at`); one without it is None here, and whether it
    was never accepted is decided by :func:`_never_accepted_refusal`.
    """
    status = record.lifecycle_status
    if status in _NEVER_ACCEPTED_STATES or status == ArtifactLifecycleStatus.DELETED:
        return None
    if status == ArtifactLifecycleStatus.CURRENT:
        return _made_current_at(record)
    accepted = _accepted_at(record)
    if accepted is None and status == ArtifactLifecycleStatus.SUPERSEDED:
        return _ACCEPTED_AT_UNKNOWN
    return accepted


async def _never_accepted_refusal(
    artifact_store: Any, *, app_id: str, record: BuildRecord, lookups: list[int],
) -> str | None:
    """Why a STALE record without acceptance evidence cannot be shown never accepted; None when it can.

    Staling overwrites the status of drafts and accepted builds alike, so a
    STALE record that no acceptance evidence names (:func:`_accepted_at`) is
    decided from what Studio saved when the change that staled it began. Its
    ``invalidation_reason`` names a change request made from it, whose
    ``router_decision.baseline_state`` (server-written when the change was
    requested) recorded the record's lifecycle status then:

    * ``draft``: it was a draft that was never accepted (acceptance takes only
      a draft to CURRENT, and writes evidence when it does), so None;
    * ``stale``: it was already stale, so the change request named by the
      saved ``invalidation_reason`` is followed back the same way, at most
      ``_STALE_PARENT_ATTRIBUTION_DEPTH`` change requests;
    * anything else refuses, naming which: a saved state that shows it was
      ``current`` or ``superseded`` (so accepted, at a time nothing records),
      no saved state, a saved state of another build, a change request that
      was not found or was made from another build, an invalidation that names
      no change request, or more reads than the ``lookups`` budget this check
      shares with its caller.

    Studio's trigger stales the build its change request was made from. The
    invalidation service's other paths stale no build record today: its
    downstream family propagation (``store.invalidate_artifact_family``)
    filters on ``artifact_kind`` and ``artifact_key``, which a build record
    never stores, and the session refs it reads are looked up under the
    artifact app without a target, where Studio keeps no session. A record
    staled by a change request made from another build therefore refuses
    (nothing saved its state); if either path is made live, what it stales
    must be decided by that owner's own evidence.

    Returns the cause as a clause about the record ("it carries ..."). A lookup
    error propagates, and every caller refuses on it.
    """
    cause: str | None = None
    reason = str(record.invalidation_reason or "")
    for _ in range(_STALE_PARENT_ATTRIBUTION_DEPTH):
        if not reason.startswith(_CHANGE_REQUEST_REASON_PREFIX):
            cause = "its invalidation names no change request that saved its state"
            break
        link = reason.removeprefix(_CHANGE_REQUEST_REASON_PREFIX)
        if lookups[0] <= 0:
            cause = f"more than {_STALE_STATE_LOOKUP_LIMIT} change requests would have to be read to show it was never accepted"
            break
        lookups[0] -= 1
        change = await artifact_store.get_change_request(app_id=app_id, change_request_id=link)
        if change is None:
            cause = f"change request {link} that staled it was not found"
            break
        made_from = getattr(change, "build_record_id", None)
        if made_from != record.id:
            cause = f"change request {link} that staled it is not one made from it (it was made from build {made_from})"
            break
        decision = change.router_decision if isinstance(change.router_decision, dict) else {}
        state = decision.get("baseline_state")
        if not isinstance(state, dict):
            cause = f"change request {link} that staled it saved no state of it when it began"
            break
        if state.get("build_record_id") != record.id:
            cause = (
                f"change request {link} that staled it saved the state of build {state.get('build_record_id')}, "
                "not of it, when it began"
            )
            break
        status = state.get("lifecycle_status")
        if status == ArtifactLifecycleStatus.DRAFT.value:
            return None
        if status != ArtifactLifecycleStatus.STALE.value:
            cause = f"change request {link} that staled it began while it was {status}, not a draft"
            break
        reason = str(state.get("invalidation_reason") or "")
    else:
        cause = f"its invalidations go back more than {_STALE_PARENT_ATTRIBUTION_DEPTH} change requests"
    refusal = f"it carries no acceptance evidence naming it, and {cause}"
    if record.commit_metadata.metadata.get("bundle_mode") == _GENESIS_IMPORT_BUNDLE_MODE:
        refusal += f" ({_genesis_import_dependency(record.id)})"
    return refusal


async def _ancestor_ids(artifact_store: Any, *, app_id: str, record: BuildRecord, max_depth: int) -> frozenset[str]:
    """The builds ``record`` was made from, following parent links, at most ``max_depth``; lookup errors propagate.

    A parent that is not found, or a link back into the chain, ends the walk:
    what could not be read is left out, so it is never counted as an ancestor.
    """
    found: list[str] = []
    parent_id = record.parent_build_record_id
    while parent_id and parent_id != record.id and parent_id not in found and len(found) < max_depth:
        parent = await artifact_store.get_build_record(app_id=app_id, build_record_id=parent_id)
        if parent is None:
            break
        found.append(parent.id)
        parent_id = parent.parent_build_record_id
    return frozenset(found)


class NewerAcceptedWork(NamedTuple):
    """Accepted work that a build's lineage does not contain; see :func:`newer_accepted_work`."""

    #: ``current``: a CURRENT build outside the lineage. ``accepted``: a build
    #: accepted after the lineage's accepted base. ``unverified``: the lineage,
    #: the builds to compare or the order of two acceptances could not be
    #: established.
    kind: Literal["current", "accepted", "unverified"]
    reason: str
    build_record_id: str | None = None


async def newer_accepted_work(
    artifact_store: Any, *, app_id: str, record: BuildRecord, max_depth: int = NEWER_ACCEPTED_WORK_MAX_DEPTH,
) -> NewerAcceptedWork | None:
    """Accepted work in ``record``'s build family and key that its lineage does not contain; None when there is none.

    The scope is the record's ``(build_family, build_key)``, the scope in which
    accepting a build supersedes the CURRENT one, so a CURRENT record under
    another key (such as the App Intelligence index a promotion saves) is not
    the app's build here.

    The accepted base is the nearest record of the lineage, ``record`` itself
    first and then its parents, that shows acceptance (:func:`_acceptance_of`:
    CURRENT, SUPERSEDED, or carrying acceptance evidence that names it). A
    draft, the first build included, shows none, so a lineage of drafts has
    no base. A CURRENT base without evidence is ordered from its creation, the
    earliest it can have become current (:func:`_made_current_at`), so the
    scan below reads more, never less. Newer accepted work is a record of the
    scope that is not in the lineage up to that base and that is CURRENT, or
    that is STALE or SUPERSEDED and was accepted at or after the base was
    accepted (an accepted build is marked stale once a refinement of it is
    stored, so CURRENT alone does not show it). Every write to a record sets
    its ``updated_at``, so only records written since the base was accepted
    are read, newest first.

    A STALE record without evidence naming it is passed over only when the
    state its invalidating change request saved shows it was a draft never
    accepted (:func:`_never_accepted_refusal`); otherwise whether it was
    accepted cannot be verified and it refuses. A SUPERSEDED record without
    evidence was accepted, at a time nothing records, so it is never passed
    over as unaccepted: it is older work only when its last write (the
    supersession) was no later than the base became current, or when it is
    one of the builds the base was made from (whose acceptance the base's own
    acceptance superseded); otherwise it refuses.

    Fails closed (``unverified``): a missing record or a lineage longer than
    ``max_depth`` before its accepted base; more CURRENT records, or records
    written since the base was accepted, than the scan bound; a STALE record
    that cannot be shown never accepted, as above; or an accepted record whose
    acceptance cannot be ordered against the base's (no base in the lineage,
    an acceptance with no known time, or a superseded record without evidence
    as above). Lookup errors propagate.
    """
    family, key = record.build_family, record.build_key
    lineage: dict[str, BuildRecord] = {record.id: record}
    base: BuildRecord | None = None
    base_at: Any = None
    node = record
    while True:
        accepted = _acceptance_of(node)
        if accepted is not None:
            base, base_at = node, accepted
            break
        parent_id = node.parent_build_record_id
        if not parent_id:
            break
        if len(lineage) > max_depth:
            return NewerAcceptedWork(
                "unverified", f"its lineage has more than {max_depth} builds before one that was accepted",
            )
        parent = await artifact_store.get_build_record(app_id=app_id, build_record_id=parent_id)
        if parent is None:
            return NewerAcceptedWork("unverified", f"build {parent_id} in its lineage was not found")
        if parent.id in lineage:
            return NewerAcceptedWork("unverified", f"its lineage returns to build {parent_id}")
        lineage[parent.id] = node = parent
    limit = _NEWER_ACCEPTED_WORK_SCAN_LIMIT
    current = await artifact_store.list_build_records(
        app_id=app_id, build_family=family, build_key=key, lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        limit=limit + 1,
    )
    if len(current) > limit:
        return NewerAcceptedWork("unverified", f"more than {limit} builds of {family}/{key} are current")
    for other in sorted(current, key=lambda other: other.id):
        if other.id not in lineage:
            return NewerAcceptedWork("current", f"build {other.id} is the current build", other.id)
    since = base_at if isinstance(base_at, datetime) else None
    written = await artifact_store.list_build_records(
        app_id=app_id, build_family=family, build_key=key, updated_since=since, limit=limit + 1,
    )
    if len(written) > limit:
        return NewerAcceptedWork(
            "unverified", f"more than {limit} builds of {family}/{key} were written since its accepted base",
        )
    base_ancestors: frozenset[str] | None = None
    lookups = [_STALE_STATE_LOOKUP_LIMIT]
    for other in written:
        if other.id in lineage or other.lifecycle_status not in {
            ArtifactLifecycleStatus.STALE, ArtifactLifecycleStatus.SUPERSEDED,
        }:
            continue
        written_at = _utc_instant(other.updated_at)
        if since is not None and written_at is not None and written_at < since:
            continue  # last written before the base was accepted, so not accepted after it
        other_at = _acceptance_of(other)
        if other_at is None:
            # STALE without evidence naming it: passed over only as a draft that was never accepted.
            refusal = await _never_accepted_refusal(artifact_store, app_id=app_id, record=other, lookups=lookups)
            if refusal is None:
                continue
            return NewerAcceptedWork(
                "unverified",
                f"build {other.id} is stale, {refusal}, so its acceptance cannot be verified; refine the app from "
                f"build {other.id} or a build made from it instead",
                other.id,
            )
        if base is None:
            imported = next((node.id for node in lineage.values() if _genesis_import_without_evidence(node)), None)
            return NewerAcceptedWork(
                "unverified",
                f"build {other.id} was accepted, and nothing in this build's lineage shows an acceptance "
                "to order it against" + (f" ({_genesis_import_dependency(imported)})" if imported else ""),
                other.id,
            )
        if _accepted_at(other) is None:
            # Superseded, so once accepted, but with no evidence saying when.
            if since is not None and written_at is not None and written_at <= since:
                continue  # superseded no later than the base became current, so accepted before it
            if base_ancestors is None:
                base_ancestors = await _ancestor_ids(artifact_store, app_id=app_id, record=base, max_depth=max_depth)
            if other.id in base_ancestors:
                continue  # a build the base was made from: work this lineage contains
            return NewerAcceptedWork(
                "unverified",
                f"build {other.id} was superseded, so it was once accepted, but no acceptance evidence names it, "
                f"so whether it was accepted after build {base.id} cannot be verified",
                other.id,
            )
        if not isinstance(other_at, datetime) or not isinstance(base_at, datetime):
            return NewerAcceptedWork(
                "unverified",
                f"when build {other.id} was accepted, relative to build {base.id}, is unknown",
                other.id,
            )
        if other_at >= base_at:
            return NewerAcceptedWork(
                "accepted", f"build {other.id} was accepted after build {base.id}", other.id,
            )
    return None


async def _open_work(artifact_store: Any, *, app_id: str, record: BuildRecord) -> tuple[str, str] | None:
    """What keeps a build made from a stale parent open, as ``(kind, detail)``; None when it is closed.

    Closed: rejected or removed, a draft whose validation failed, or a STALE
    build shown never accepted (no acceptance evidence names it, and the state
    its invalidating change request saved shows it was a draft,
    :func:`_never_accepted_refusal`). A refinement of a draft under review
    marks that draft stale, after which it can be neither accepted nor
    rejected and never becomes current; what was made from it is checked as
    the parent's other descendants are, and a change request made from it
    recorded as a workflow route (launched, failed or abandoned) keeps it open
    for good, since nothing records that workflow's outcome. Open: a draft
    whose validation passed, is pending or was skipped (``open_draft``: it can
    be rejected), any build that was accepted (``accepted``: CURRENT,
    SUPERSEDED, or STALE with acceptance evidence naming it,
    :func:`_accepted_at`), and a STALE build without evidence that cannot be
    shown never accepted (``unverified``).
    """
    status = ArtifactLifecycleStatus(record.lifecycle_status)
    if status in {ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED}:
        return None
    if status == ArtifactLifecycleStatus.DRAFT:
        if record.validation_status == ArtifactValidationStatus.FAILED:
            return None
        return "open_draft", f"draft with validation {ArtifactValidationStatus(record.validation_status).value}"
    if status != ArtifactLifecycleStatus.STALE:
        return "accepted", f"was accepted and is {status.value}"
    if _accepted_at(record) is not None:
        return "accepted", "was accepted and is now stale"
    limit = _STALE_PARENT_FAMILY_SCAN_LIMIT
    changes = await artifact_store.list_change_requests(app_id=app_id, build_record_id=record.id, limit=limit + 1)
    if len(changes) > limit:
        return "unverified", f"is stale with more than {limit} change requests made from it"
    for change in sorted(changes, key=lambda change: change.id):
        decision = change.router_decision if isinstance(change.router_decision, dict) else {}
        mode = decision.get("execution_mode")
        if change.build_record_id == record.id and mode not in _INLINE_OR_DECISION_MODES:
            return "workflow", (
                f"is stale, and change request {change.id} made from it may have launched a workflow ({mode}), "
                f"whose outcome is not on record; {_ACCEPT_A_NEW_BUILD}"
            )
    refusal = await _never_accepted_refusal(
        artifact_store, app_id=app_id, record=record, lookups=[_STALE_STATE_LOOKUP_LIMIT],
    )
    if refusal is not None:
        return "unverified", (
            f"is stale, {refusal}, so its acceptance cannot be verified; {_ACCEPT_A_NEW_BUILD}"
        )
    return None


async def _open_work_refusal(
    artifact_store: Any, *, app_id: str, record: BuildRecord, where: str, open_draft: str,
) -> str | None:
    """The refusal for a build made from the parent that is still open (:func:`_open_work`); None when closed.

    ``open_draft`` is the refusal for a draft that can still be rejected;
    any other open build is named with what keeps it open.
    """
    found = await _open_work(artifact_store, app_id=app_id, record=record)
    if found is None:
        return None
    kind, detail = found
    return open_draft if kind == "open_draft" else f"build {record.id} {where} {detail}"


class PackCodeBaseline(NamedTuple):
    """The build an app-owned pack change would be made from, as admission reads it (:func:`pack_code_baseline_refusal`)."""

    #: The app whose build records hold the build (``CodingWorkerRequest.artifact_app_id``).
    artifact_app_id: str
    build_family: str
    build_record_id: str | None
    #: The authenticated user making the change.
    user_id: str | None
    #: Studio's server-derived registry read before the run (``CodingWorkerRequest.registry_run_state``).
    registry_run_state: RegistryRunState | None

    @classmethod
    def of(cls, request: CodingWorkerRequest) -> PackCodeBaseline:
        """The build a coding request is made from."""
        return cls(
            request.artifact_app_id, request.build_family, request.build_record_id, request.user_id,
            request.registry_run_state,
        )


class PackCodeBaselineRefusal(NamedTuple):
    """Why app-owned pack code cannot change from a build (:func:`pack_code_baseline_refusal`)."""

    reason: str
    #: The app's accepted current build, when that is another build.
    accepted_build_record_id: str | None = None


#: The baseline rule for app-owned pack code: what ``STALE_REFINEMENT_BASELINE`` refuses.
PACK_CODE_BASELINE_RULE = (
    "app-owned pack code changes only from the app's accepted current build, or, while none is current, from a "
    "stale build whose only invalidation is this app's own rejected or failed change with nothing else open, in "
    "flight or changed since"
)
#: The way forward from the app's first build while it is still the draft AppGenerator saved.
_ACCEPT_THE_FIRST_BUILD = "accept the first build before changing its app-owned capability code"


def _draft_parent_refusal(parent: BuildRecord) -> str:
    """Why app-owned pack code cannot change from a draft, with the way forward: accept it first."""
    if not parent.parent_build_record_id:
        return (
            f"the parent {parent.id} is the app's first build, a draft that has not been accepted; "
            f"{_ACCEPT_THE_FIRST_BUILD}"
        )
    return (
        f"the parent {parent.id} is a draft that has not been accepted; accept it before changing its app-owned "
        "capability code"
    )


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

    The parent's rows are verified once, as saved (their pins, and the
    authority that vouches for each source), and the declared outputs are read
    from those verified entries; no row is re-selected without its pins. They
    are verified as a recorded selection, so a workspace pack must carry the
    digest its build recorded. A selection that cannot be resolved or verified
    raises ``ManagedCapabilityTemplateError``.
    """
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        verified_pack_output_paths,
        verified_selected_pack_sources,
    )

    entries = verified_selected_pack_sources(capability_packs, recorded=True)
    if not entries:
        return {}
    owned = {owner: verified_pack_output_paths(entries, owner=owner) for owner in ("templates", "workspace")}
    owners = {
        path: next((owner for owner, paths in owned.items() if path in paths), "generator")
        for path in verified_pack_output_paths(entries)
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

    for entry in verified_selected_pack_sources(capability_packs, recorded=True):
        for template in resolve_templates_for_pack(Path(entry["source_path"]), entry["pack_id"]):
            if template["filename"] == path:
                return str(template["content"])
    return None


def parent_pack_output_owners(capability_packs: Any) -> dict[str, str] | None:
    """The owners of a saved parent's verified pack outputs (:func:`pack_output_owners`); None when unverifiable."""
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
    )

    if not isinstance(capability_packs, list) or any(not isinstance(pack, dict) for pack in capability_packs):
        return None
    try:
        return pack_output_owners(capability_packs)
    except (ManagedCapabilityTemplateError, OSError, yaml.YAMLError) as exc:
        logger.warning("PARENT_PACK_OWNERSHIP_UNRESOLVED: %s", exc)
        return None


def pack_paths_owned_by(owners: dict[str, str] | None, owner: str) -> frozenset[str]:
    """The pack output paths ``owners`` assigns to ``owner``; none when ownership is unverified (None)."""
    return frozenset(path for path, declared in (owners or {}).items() if declared == owner)


def parent_workspace_pack_paths(capability_packs: Any) -> frozenset[str] | None:
    """App-owned pack extension paths a saved parent declares; None when unverifiable."""
    owners = parent_pack_output_owners(capability_packs)
    return None if owners is None else pack_paths_owned_by(owners, "workspace")


#: Why a candidate is refused when the saved parent's pack selection cannot be verified, and the way forward.
PACK_OWNERSHIP_UNRESOLVED_GUIDANCE = (
    "the capability packs the parent build selected cannot be resolved to verified sources, so no candidate is "
    "admitted against them (pack ownership decides which files a refinement may change). Restore the pack "
    "source the parent was built from at the version and digest it selected, or run an AppGenerator revision "
    "of this build and accept it, so the app's build selects packs that verify"
)

#: What to do instead of changing a template-owned pack output: the supported paths.
TEMPLATE_PACK_OUTPUT_GUIDANCE = (
    "Template-owned capability pack files are rendered from the pack's verified templates, so a refinement "
    "cannot change them: customize the app through its own files (an app-owned page, component or route, or "
    "a pack's app-owned extension file such as a workspace handler), or upgrade the pack"
)


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

    @staticmethod
    async def _current_accepted_build_record_id(artifact_store: Any, baseline: PackCodeBaseline) -> str | None:
        """The app's accepted current build: the CURRENT record of the parent's build family and key.

        That is the scope in which accepting a build supersedes the current
        one, so a CURRENT record under another key, such as the App
        Intelligence index a promotion saves, is not the app's build. The
        parent itself when it is CURRENT; otherwise another CURRENT record of
        the scope, or None when there is none. Lookup errors propagate.

        Accepting a review draft makes a build current. Studio marks the
        selected build stale only after an operation changed it or left its
        outcome unresolved: a run that stored a child whose validation passed,
        is pending or was skipped, or a workflow that launched. A run rejected
        before it stored a child, or one whose only stored child failed
        validation, leaves the accepted build current, so a later authorized
        change is still admitted against it.
        """
        app_id = baseline.artifact_app_id
        parent_id = str(baseline.build_record_id or "").strip()
        parent: BuildRecord | None = (
            await artifact_store.get_build_record(app_id=app_id, build_record_id=parent_id) if parent_id else None
        )
        if parent is None:
            raise ValueError("Refinement parent build record was not found for the target app")
        current: list[str] = sorted(
            record.id for record in await artifact_store.list_build_records(
                app_id=app_id, build_family=parent.build_family, build_key=parent.build_key,
                lifecycle_status=ArtifactLifecycleStatus.CURRENT, limit=_STALE_PARENT_FAMILY_SCAN_LIMIT + 1,
            )
        )
        if current == [parent.id]:
            return parent.id
        return next((record_id for record_id in current if record_id != parent.id), None)

    @classmethod
    async def _stale_parent_refusal(cls, artifact_store: Any, baseline: PackCodeBaseline) -> str | None:
        """Why a STALE parent cannot be admitted for an app-owned pack change while no build is CURRENT; None when it can.

        A DRAFT parent, such as the app's first build before the owner accepts
        it, was never accepted, so it is refused with the way forward: accept
        it first. Nothing makes a stale build current again, so a rejected
        draft leaves the app with no CURRENT build. The stale marker that rejected draft's
        run left is told apart from genuine invalidation, newer accepted work
        and work still in flight using the records Studio already keeps. The
        parent is admitted, without changing any record, only when all hold:

        * no build of its build family and key is CURRENT (the scope accepting
          supersedes in), and the parent is STALE;
        * acceptance evidence names it (:func:`_accepted_at`: written by an
          authorized acceptance; a stale build without it, including one
          accepted before acceptance named its build, one carrying evidence
          copied from another build, and an imported Genesis build, whose
          acceptance writes none yet, is not shown accepted) and no build of
          that scope was accepted after it (:func:`newer_accepted_work`);
        * attribution: its ``invalidation_reason`` names a change request made
          from it by this user, recorded as an inline run or a returned
          decision (never a workflow route), that saved the parent's state
          when it began, and whose inline run's stored draft is now closed
          (:func:`_open_work`). A change request that began while the parent
          was already stale is followed back, under the same conditions, to
          the one that began while the parent was CURRENT;
        * no other work: every other change request made from the parent ran
          inline or only returned a decision, every build made from it, and
          every build further down its lineage, is closed: rejected, removed,
          a draft that failed validation, or a stale draft shown never
          accepted (a draft refined in review, which can then be neither
          rejected nor accepted, as the state its change request saved shows)
          with no change request made from it recorded as a workflow route;
          and every refinement session of the parent is an inline run;
        * nothing in flight: Studio's registry read before this run began
          (``registry_run_state``, server-derived) found no other run building;
        * compatible inputs: every family the parent's family depends on in the
          pack's artifact dependency graph is unchanged since the parent was
          accepted. A recorded input (``canonical_inputs_version``) must still
          be CURRENT; any other CURRENT record must have become current no
          later than the parent was accepted, judged by the latest it can have
          (:func:`_became_current_by`: its acceptance evidence, else its last
          write); a family that is STALE with no CURRENT record refuses.

        The retry stays bound to this parent, the authenticated user and the
        explicit or confirmed file scope checked by the caller. A lookup
        failure, a scan past its bound, or anything else unresolved refuses.
        Acceptance rechecks the parent, scope and registry run as for a
        CURRENT parent. These are reads (Studio's check before the provider
        call, then admission's), not a compare-and-set.
        """
        family = _ARTIFACT_KIND_ALIASES.get(baseline.build_family, baseline.build_family)
        app_id = baseline.artifact_app_id
        limit = _STALE_PARENT_FAMILY_SCAN_LIMIT
        if not baseline.build_record_id:
            return "the request names no parent build"
        try:
            parent = await artifact_store.get_build_record(app_id=app_id, build_record_id=baseline.build_record_id)
            if parent is None or parent.build_family != family:
                return "the parent build was not found in this app's build family"
            if parent.lifecycle_status == ArtifactLifecycleStatus.DRAFT:
                return _draft_parent_refusal(parent)
            if parent.lifecycle_status != ArtifactLifecycleStatus.STALE:
                return f"the parent is {ArtifactLifecycleStatus(parent.lifecycle_status).value}, not stale"
            if baseline.registry_run_state != "idle":
                return _REGISTRY_RUN_REFUSALS.get(baseline.registry_run_state, _REGISTRY_RUN_REFUSALS[None])
            records = await artifact_store.list_build_records(app_id=app_id, build_family=family, limit=limit + 1)
            if len(records) > limit:
                return f"the build family has more than {limit} records to check"
            by_id = {record.id: record for record in records}
            if parent.id not in by_id:
                return "the parent is missing from its build family's records"
            # The parent's scope decides what is current; the lineage scans below
            # read the whole family, so a build made from the parent under another
            # key is still checked.
            current = sorted(
                record.id for record in records
                if record.build_key == parent.build_key and record.lifecycle_status == ArtifactLifecycleStatus.CURRENT
            )
            if current:
                return f"a build of this family and key is current: {current[0]}"
            accepted_at = _accepted_at(parent)
            if accepted_at is None:
                if _genesis_import_without_evidence(parent):
                    return f"nothing shows the parent was ever accepted: {_genesis_import_dependency(parent.id)}"
                return (
                    "nothing shows the parent was ever accepted: no acceptance evidence names it (evidence "
                    "copied from another build, or written before acceptance named its build, does not count), so "
                    "it is a draft that was never accepted, or a build whose acceptance cannot be verified; "
                    f"{_ACCEPT_A_NEW_BUILD}"
                )
            if not isinstance(accepted_at, datetime):
                return (
                    "the parent's acceptance time is unknown: its acceptance evidence names no readable time, "
                    f"principal or transition; {_ACCEPT_A_NEW_BUILD}"
                )
            newer = await newer_accepted_work(artifact_store, app_id=app_id, record=parent)
            if newer is not None:
                return (
                    f"build {newer.build_record_id} was accepted after the parent" if newer.kind == "accepted"
                    else newer.reason
                )
            children = sorted(
                (record for record in records if record.parent_build_record_id == parent.id), key=lambda r: r.id,
            )
            for child in children:
                refusal = await _open_work_refusal(
                    artifact_store, app_id=app_id, record=child, where="made from the parent",
                    open_draft=(
                        f"draft {child.id} made from the parent is draft with validation "
                        f"{ArtifactValidationStatus(child.validation_status).value}, not rejected"
                    ),
                )
                if refusal is not None:
                    return refusal
            below = [child.id for child in children]
            seen: set[str] = set(below)
            while below:
                ancestor = below.pop()
                for record in sorted(records, key=lambda r: r.id):
                    if record.parent_build_record_id != ancestor or record.id in seen:
                        continue
                    seen.add(record.id)
                    below.append(record.id)
                    refusal = await _open_work_refusal(
                        artifact_store, app_id=app_id, record=record, where="made further down from the parent",
                        open_draft=f"draft {record.id}, made further down from the parent, is still open",
                    )
                    if refusal is not None:
                        return refusal
            refusal = await cls._attribution_refusal(
                artifact_store, baseline=baseline, parent=parent, records=by_id,
            )
            if refusal is not None:
                return refusal
            return await cls._upstream_input_refusal(
                artifact_store, app_id=app_id, parent=parent, family=family, accepted_at=accepted_at,
            )
        except Exception as exc:
            logger.warning(
                "STALE_PARENT_ADMISSION_UNVERIFIED app=%s parent=%s", app_id, baseline.build_record_id, exc_info=True,
            )
            return f"a record needed to check the parent could not be read ({type(exc).__name__})"

    @staticmethod
    async def _attribution_refusal(
        artifact_store: Any, *, baseline: PackCodeBaseline, parent: BuildRecord, records: dict[str, BuildRecord],
    ) -> str | None:
        """Whether the parent's stale marker is only this app's own closed inline runs, and no other work."""
        limit = _STALE_PARENT_FAMILY_SCAN_LIMIT
        app_id = baseline.artifact_app_id
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
            if change.created_by_user_id != baseline.user_id:
                return f"change request {link} that invalidated the parent was made by another user"
            decision = change.router_decision if isinstance(change.router_decision, dict) else {}
            mode = decision.get("execution_mode")
            if mode not in _INLINE_OR_DECISION_MODES:
                return (
                    f"change request {link} that invalidated the parent may have launched a workflow ({mode}), "
                    f"whose outcome is not on record; {_ACCEPT_A_NEW_BUILD}"
                )
            state = decision.get("baseline_state")
            if not isinstance(state, dict):
                return (
                    f"change request {link} that invalidated the parent saved no state of it when it began: it was "
                    "recorded before change requests saved the state of their build, so what it changed cannot be "
                    f"resolved; {_ACCEPT_A_NEW_BUILD}"
                )
            if state.get("build_record_id") != parent.id:
                return (
                    f"change request {link} that invalidated the parent saved the state of build "
                    f"{state.get('build_record_id')}, not of the parent, so what it changed in the parent cannot be "
                    f"resolved; {_ACCEPT_A_NEW_BUILD}"
                )
            if not runs.get(link):
                return f"change request {link} that invalidated the parent has no inline run on record"
            for session in runs[link]:
                child = records.get(str(session.result_build_record_id or ""))
                if child is None or child.parent_build_record_id != parent.id:
                    return f"the run of change request {link} names no stored draft of the parent"
                refusal = await _open_work_refusal(
                    artifact_store, app_id=app_id, record=child, where=f"of change request {link}",
                    open_draft=f"draft {child.id} of change request {link} is neither rejected nor failed",
                )
                if refusal is not None:
                    return refusal
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
                return (
                    f"change request {change_id} made from the parent may have launched a workflow, whose outcome "
                    f"is not on record; {_ACCEPT_A_NEW_BUILD}"
                )
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
                made_current = _became_current_by(record)
                if not isinstance(made_current, datetime):
                    return f"when input {name} {record.id} became current is unknown"
                if made_current > accepted_at:
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

        Template-owned outputs stay byte-identical to the verified parent.
        Studio refuses, before the coding or surface provider runs, a patch
        that would apply to template-owned files only; this is the backstop for
        every candidate, whatever chose its scope, including a scope that
        holds template files beside editable ones. An
        app-owned (``owner: workspace``) output may change only inside an
        explicit or user-confirmed selected-file scope, from the app's accepted
        current build (the CURRENT record of the parent's build family and key,
        read here, at admission) or, when none is CURRENT, from a STALE parent
        whose stale marker only this app's own rejected or failed inline runs
        left, with nothing else open, in flight or changed since
        (:func:`pack_code_baseline_refusal`); a failed read refuses. Studio
        runs the same baseline check before any coding or surface provider
        call for such a scope, so this is the backstop for what changed while
        the provider ran. For a workspace handler,
        only as a thin extension of its template
        base handler. Applied paths are real changes against the parent, so any
        owned path among them is a change. Acceptance rechecks the parent and
        scope before a child becomes current. When the parent's saved pack
        selection cannot be resolved and verified (its pins and authority
        kept), ownership is unknown and the candidate is refused
        (``PACK_OWNERSHIP_UNRESOLVED``); it is never admitted unchecked.
        """
        from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
            ManagedCapabilityTemplateError,
        )

        scope = self._refinement_scope(request)
        if not capability_packs:
            return scope
        changed = sorted(applied_files)
        try:
            owners = pack_output_owners(capability_packs)
        except (ManagedCapabilityTemplateError, OSError, yaml.YAMLError) as exc:
            # No ownership, no admission: an unverifiable selection could hide
            # template drift, an undeclared module file or app-owned code.
            raise CandidateAdmissionError(
                "PACK_OWNERSHIP_UNRESOLVED", PACK_OWNERSHIP_UNRESOLVED_GUIDANCE,
                paths=changed, parent_build_record_id=request.build_record_id, resolution_error=str(exc),
            ) from exc
        drifted = [path for path in changed if owners.get(path) == "templates"]
        if drifted:
            raise CandidateAdmissionError(
                "PACK_TEMPLATE_OUTPUT_CHANGED",
                "template-owned capability pack outputs must stay byte-identical to the verified parent "
                "(customize through app-owned files or a pack upgrade)",
                paths=drifted, supported_path=TEMPLATE_PACK_OUTPUT_GUIDANCE,
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
        refusal = await pack_code_baseline_refusal(
            self._artifact_store or get_artifact_store(), PackCodeBaseline.of(request),
        )
        if refusal is not None:
            raise CandidateAdmissionError(
                "STALE_REFINEMENT_BASELINE", PACK_CODE_BASELINE_RULE,
                paths=workspace, parent_build_record_id=request.build_record_id,
                accepted_build_record_id=refusal.accepted_build_record_id, stale_baseline_refusal=refusal.reason,
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


async def pack_code_baseline_refusal(
    artifact_store: Any, baseline: PackCodeBaseline,
) -> PackCodeBaselineRefusal | None:
    """Why app-owned pack code cannot change from ``baseline``'s build; None when it can.

    The baseline rule of candidate admission (``STALE_REFINEMENT_BASELINE``,
    :data:`PACK_CODE_BASELINE_RULE`): the build must be the app's accepted
    current build (the CURRENT record of its build family and key), or, while
    none is CURRENT, a STALE build that
    :meth:`ScopedRefinementCodingWorker._stale_parent_refusal` admits. A draft,
    such as the app's first build before the owner accepts it, is refused
    with the way forward: accept it first. Studio runs this before any coding
    or surface provider call when an explicit or confirmed scope includes
    app-owned pack paths; admission runs it again on the candidate as the
    backstop. Reads only; a failed read refuses.
    """
    try:
        current = await ScopedRefinementCodingWorker._current_accepted_build_record_id(artifact_store, baseline)
    except Exception as exc:
        logger.warning(
            "CURRENT_BUILD_UNVERIFIED app=%s parent=%s", baseline.artifact_app_id, baseline.build_record_id,
            exc_info=True,
        )
        return PackCodeBaselineRefusal(f"the app's current build could not be read ({type(exc).__name__})")
    if current == baseline.build_record_id:
        return None
    if current is not None:
        return PackCodeBaselineRefusal(f"build {current} is the app's accepted current build", current)
    refusal = await ScopedRefinementCodingWorker._stale_parent_refusal(artifact_store, baseline)
    return None if refusal is None else PackCodeBaselineRefusal(refusal)


_coding_worker: ScopedRefinementCodingWorker | None = None


def get_coding_worker() -> ScopedRefinementCodingWorker:
    global _coding_worker
    if _coding_worker is None:
        _coding_worker = ScopedRefinementCodingWorker()
    return _coding_worker
