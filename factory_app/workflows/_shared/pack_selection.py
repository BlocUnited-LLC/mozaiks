"""Verified catalog choices for the existing DesignDocs review boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackIntegrityError,
    claims_pack,
    selected_pack_id,
    verified_pack_selection,
    verified_selected_pack_sources,
)
from mozaiksai.core.session.build_context import (
    discover_build_context_files,
    discover_pack_descriptors,
    load_build_context,
    load_contract_descriptors,
    load_trusted_build_context,
    resolve_build_context_root,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_role_grants import ReviewedPackIdentity
from mozaiksai.resources import resolve_factory_app_root

_T = TypeVar("_T")
_PACK_READS: ContextVar[tuple[Any, dict[tuple, Any]] | None] = ContextVar("_pack_reads", default=None)


def _memoized_pack_read(context: Any, key: tuple, reader: Callable[[], _T]) -> _T:
    """Reuse successful immutable source reads only inside the owning operation."""
    scope = _PACK_READS.get()
    if scope is None or scope[0] is not context:
        return reader()
    memo = scope[1]
    if key not in memo:
        memo[key] = reader()
    return deepcopy(memo[key])


def reviewed_pack_read_operation(function):
    """A tool invocation owns its memo; context/model data cannot provide one."""
    @wraps(function)
    def invoke(*args, **kwargs):
        token = _PACK_READS.set((kwargs.get("context_variables"), {}))
        try:
            return function(*args, **kwargs)
        finally:
            _PACK_READS.reset(token)
    return invoke


@contextmanager
def fresh_pack_reads():
    """Re-admit sources at the final boundary, including within an operation."""
    scope = _PACK_READS.get()
    token = _PACK_READS.set((scope[0], {}) if scope is not None else None)
    try:
        yield
    finally:
        _PACK_READS.reset(token)


def available_pack_rows(context: Any, *, pack_ids: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """Read shipped local packs and operator-enabled offerings, never caller paths.

    Registry descriptors remain protected launch input. Public generated-module
    packs already ship with the installed Factory; managed or external offerings
    require an admitted descriptor rather than mere presence in that directory.
    Source resolution, integrity and dependency semantics stay with the existing
    canonical pack resolver.
    """
    rows: dict[str, dict[str, Any]] = {}
    root = resolve_factory_app_root()
    if root is not None:
        for path in discover_build_context_files(root / "build_context", "AppGenerator"):
            config = load_build_context(path)
            for row in discover_pack_descriptors(path.parent, config):
                if (row.get("status", "active") == "active"
                    and row.get("capability_source") in {"generated_module", "framework_pack", "config_file"}):
                    rows[row["id"]] = row
    from mozaiksai.core.data.persistence.persistence_manager import (
        _context_authority_policy_for_workflow,
    )

    # AppGenerator is the target materializer. Consult its canonical registry
    # even while selecting in DesignDocs; a replayed bundle is not a registry.
    policy = _context_authority_policy_for_workflow("AppGenerator")
    workspace_root = resolve_build_context_root()
    if workspace_root is not None:
        from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
            resolve_installed_component_descriptors,
        )
        from factory_app.workflows.AppGenerator.tools.community_component_state import (
            component_by_id,
            load_installed_components,
        )

        installed_ids = set(component_by_id(load_installed_components(workspace_root)))
        requested_ids = installed_ids if pack_ids is None else installed_ids.intersection(pack_ids)
        for row in resolve_installed_component_descriptors(
            build_context_root=workspace_root, selected_pack_ids=sorted(requested_ids),
        ):
            rows[row["id"]] = row
    registered = load_trusted_build_context(policy).get("capability_packs") or []
    registered_ids: set[str] = set()
    for row in registered or []:
        if row["id"] in registered_ids:
            raise ValueError(f"Registered capability pack is repeated: {row['id']}")
        registered_ids.add(row["id"])
        if row.get("status", "active") == "active":
            rows[row["id"]] = dict(row)
    result: dict[str, dict[str, Any]] = {}
    for pack_id, row in sorted(rows.items()):
        if pack_ids is not None and pack_id not in pack_ids:
            continue
        verified = verified_pack_selection(row, context_variables=context)
        if verified is None or verified["pack_id"] != pack_id:
            raise PackIntegrityError(f"Available pack {pack_id!r} has no verified source")
        source = load_build_context(Path(verified["source_path"]) / "context.yaml")
        if (source.get("pack") or {}).get("status", "active") != "active":
            continue
        result[pack_id] = {**row, "capability_source": verified["capability_source"],
                           "version": verified["version"], "digest": verified["digest"]}
    return result


def pin_pack_choices(context: Any, pack_ids: list[str]) -> list[dict[str, str]]:
    """Resolve explicit IDs to candidate pins; no role or provider authority."""
    if not isinstance(pack_ids, list) or any(not isinstance(item, str) for item in pack_ids):
        raise ValueError("Choose a list of canonical pack IDs")
    if len(set(pack_ids)) != len(pack_ids):
        raise ValueError("Choose each pack once")
    if not pack_ids:
        return []
    available = available_pack_rows(context, pack_ids=set(pack_ids))
    unknown = sorted(set(pack_ids) - available.keys())
    if unknown:
        raise ValueError(f"Unknown or unavailable capability packs: {unknown}")
    entries = verified_selected_pack_sources(
        [available[pack_id] for pack_id in pack_ids], context_variables=context,
    )
    return [ReviewedPackIdentity.model_validate({key: entry[key] for key in
        ("pack_id", "version", "digest")}).model_dump() for entry in entries]


def rows_for_pack_choices(context: Any, identities: Any) -> list[dict[str, Any]]:
    """Freshly resolve exact pins against today's admitted sources.

    An empty list is an explicit empty selection. Missing choices, new registry
    entries, arbitrary paths and stale source digests never select extra packs.
    Owner review and app/build lineage are checked by the design artifact owner.
    """
    if not isinstance(identities, list):
        raise ValueError("Explicit pack selection is required before design review")
    choices = [ReviewedPackIdentity.model_validate(row) for row in identities]
    if len({row.pack_id for row in choices}) != len(choices):
        raise ValueError("Pack selection repeats an identity")
    if not choices:
        return []
    key = ("rows", tuple((row.pack_id, row.version, row.digest) for row in choices))
    return _memoized_pack_read(context, key, lambda: _resolve_pack_choices(context, choices))


def _resolve_pack_choices(context: Any, choices: list[ReviewedPackIdentity]) -> list[dict[str, Any]]:
    available = available_pack_rows(context, pack_ids={choice.pack_id for choice in choices})
    rows: list[dict[str, Any]] = []
    for choice in choices:
        row = available.get(choice.pack_id)
        if row is None:
            raise ValueError(f"Reviewed pack {choice.pack_id!r} is no longer available")
        if row["version"] != choice.version or row["digest"] != choice.digest:
            raise PackIntegrityError(f"Reviewed pack {choice.pack_id!r} changed; renew design review")
        rows.append(row)
    verified_selected_pack_sources(rows, context_variables=context, recorded=True)
    return rows


def selected_pack_rows(context: Any) -> list[dict[str, Any]]:
    """Use a design candidate only in DesignDocs; elsewhere require its review."""
    if context is None:
        return []
    if context.get("workflow_name") == "DesignDocs":
        candidate = detach(context.get("pack_selection"))
        return rows_for_pack_choices(context, candidate) if candidate is not None else []
    from mozaiksai.core.workflow.generator_support.design_access_policy import (
        resolve_reviewed_pack_selection,
    )
    return rows_for_pack_choices(context, resolve_reviewed_pack_selection(context))


def validate_reviewed_pack_choices(context: Any, rows: list[dict[str, Any]], *, recorded: bool = False) -> None:
    """Derived plans and recorded bundles preserve the reviewed reusable sources."""
    from mozaiksai.core.workflow.generator_support.design_access_policy import (
        resolve_reviewed_pack_selection,
    )

    reviewed = rows_for_pack_choices(context, resolve_reviewed_pack_selection(context))
    expected = verified_selected_pack_sources(reviewed, context_variables=context)
    def pins(entries: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
        return {row["pack_id"]: (row["version"], row["digest"]) for row in entries}
    expected_pins = pins(expected)
    for row in rows:
        if (claims_pack(row) or row.get("pack_source_path")) and selected_pack_id(row) not in expected_pins:
            raise ValueError("selected pack identities differ from the owner-reviewed design; renew DesignDocs review")
    actual = verified_selected_pack_sources(rows, context_variables=context, recorded=recorded)
    if pins(actual) != pins(expected):
        raise ValueError("selected pack identities differ from the owner-reviewed design; renew DesignDocs review")


class PhysicalPackContext:
    """Read-only selected inputs for physical APIs; never changes launch inventory."""

    def __init__(self, context: Any):
        self._context = context
        self._rows = selected_pack_rows(context)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "capability_packs":
            return detach(self._rows)
        value = self._context.get(key, default)
        if key == "app_build_plan" and isinstance(value, Mapping):
            return {**detach(value), "capability_packs": detach(self._rows)}
        return value


def subscription_assignment_writers(context: Any, *, rows: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """Classify assignment writers from verified contracts, never inline plan claims."""
    selected = selected_pack_rows(context) if rows is None else rows
    result: dict[str, str] = {}
    for entry in verified_selected_pack_sources(selected, context_variables=context):
        root = Path(entry["source_path"])
        contracts = load_contract_descriptors(root, load_build_context(root / "context.yaml"))
        if any("subscription_write_path" in (contract.get("provides_capabilities") or []) for contract in contracts):
            result[entry["pack_id"]] = entry["capability_source"]
    return result


def validate_subscription_write_path(context: Any, contract: Any, *, rows: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """A pack review cannot replace the later approved subscription contract."""
    writers = subscription_assignment_writers(context, rows=rows)
    if writers and (not isinstance(contract, Mapping) or contract.get("contract_required") is not True):
        raise ValueError("Subscription assignment packs require an approved contract with contract_required=true")
    if len(writers) > 1:
        raise ValueError(f"Multiple subscription assignment writers {sorted(writers)}; select exactly one")
    return writers
