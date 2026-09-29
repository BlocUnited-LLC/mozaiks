import json
import re
from collections.abc import Mapping
from typing import Any

import yaml

from factory_app.workflows._shared.platform.build_target import require_build_binding
from factory_app.workflows._shared.surface_ownership import (
    declare_collection_entities,
    default_subscription_contract,
    entity_ownership_facts,
    normalize_surface_ownership,
    related_entity_name,
)
from logs.logging_config import get_workflow_logger
from mozaiksai.core.artifacts import persist_summary_artifact
from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.runtime.persistence.intent_loader import (
    iter_data_contract_collections,
    validate_collection_ownership,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.data_contract_fields import (
    normalize_structured_defaults,
    validate_collection_fields,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_read_actions_for_surface,
)
from mozaiksai.core.workflow.generator_support.persistence_artifacts import (
    normalize_data_contract_indexes,
)

logger = get_workflow_logger("design_docs")


class DesignDocKinds:
    FRONTEND: str = "frontend"
    BACKEND: str = "backend"
    DATABASE: str = "database"
    UI_SCHEMA: str = "ui_schema"


_DOC_KINDS = (
    DesignDocKinds.FRONTEND,
    DesignDocKinds.BACKEND,
    DesignDocKinds.DATABASE,
    DesignDocKinds.UI_SCHEMA,
)
_FIRST_DOC = DesignDocKinds.FRONTEND
_LAST_DOC = DesignDocKinds.UI_SCHEMA


async def _upsert_design_doc(
    *,
    store: BuilderArtifactStore,
    app_id: str,
    user_id: str | None,
    kind: str,
    stage: str,
    content: str,
    source_workflow: str,
    source_chat_id: str | None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    await store.upsert_design_doc(
        app_id=app_id,
        user_id=user_id,
        kind=kind,
        stage=stage,
        content=content,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        extra_fields=extra_fields,
    )


async def _mark_design_docs_status(
    *,
    store: BuilderArtifactStore,
    app_id: str,
    user_id: str | None,
    stage: str,
    status: str,
    error: str | None = None,
) -> None:
    await store.mark_design_doc_status(
        app_id=app_id,
        user_id=user_id,
        stage=stage,
        status=status,
        kinds=_DOC_KINDS,
        error=error,
    )


def _cv_get(context_variables: Any, key: str) -> Any | None:
    """Read a context value as plain data.

    Every live container freezes on read, so without detach() this returns a
    MappingProxyType and every `isinstance(..., dict)` on the result is False.
    #671's workflow-surface guard was dead in production for exactly that
    reason until #708 detached at its one call site; detaching here is what
    keeps the next reader from rediscovering it.
    """
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            return detach(context_variables.get(key))
        except Exception:
            return None
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return detach(data.get(key))
    return None


def _cv_set(context_variables: Any, key: str, value: Any) -> None:
    if context_variables is None:
        return
    setter = getattr(context_variables, "set", None)
    if callable(setter):
        try:
            setter(key, value)
            return
        except Exception:
            return
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value


def _refused(
    context_variables: Any,
    reason: str,
    detail: str,
    *,
    outcome: str = "revise",
) -> dict[str, Any]:
    """Report a rejection the agent can act on.

    The declared `outcome` is what keeps it intact: a payload without one is
    unrecognised by tool-outcome validation, which discards it for
    `invalid_tool_outcome` and loses the text naming what was wrong. The same
    text is written to `design_docs_save_feedback`, because the retry turn reads
    the reason from context rather than from this return value.

    `revise` means the agent can fix it and the transition graph gives it another
    turn; `blocked` is terminal and reserved for what a retry cannot change, such
    as no bound target app. The outcome contract forbids retrying on the error
    value, so these have to be two different words.
    """
    _cv_set(context_variables, "design_docs_save_feedback", detail)
    return {"ok": False, "outcome": outcome, "reason": reason, "error": detail}


def _normalize_kind(kind: str) -> str | None:
    if not isinstance(kind, str):
        return None
    k = kind.strip().lower()
    if k in {DesignDocKinds.FRONTEND, DesignDocKinds.BACKEND, DesignDocKinds.DATABASE, DesignDocKinds.UI_SCHEMA}:
        return k
    return None


def _extract_bundle(context_variables: Any) -> dict[str, Any] | None:
    if context_variables is None:
        return None

    raw = detach(_cv_get(context_variables, "structured_output"))
    if not isinstance(raw, dict):
        return None

    return raw


def _canonical_surface_map(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("surface_map must be an object")
    surfaces = raw.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        raise ValueError("surface_map.surfaces must be a non-empty list")
    for idx, surface in enumerate(surfaces):
        if not isinstance(surface, dict):
            raise ValueError(f"surface_map.surfaces[{idx}] must be an object")
    return {"surfaces": surfaces}


def _realize_undeclared_workflow_surfaces(
    surface_map: dict[str, Any],
    data_contract: Any,
    concept_blueprint: Any,
) -> list[dict[str, Any]]:
    """Realize a workflow surface the approved concept never asked for as the app surface it is.

    An app whose concept records no agentic capabilities cannot carry an AI
    workflow: downstream `pattern_selection` compares its workflow partition
    against the map and fails the build terminally. A live tool-lending library
    whose concept recorded `agentic_capabilities: []` still got

        surface_map: module tool_catalog, workflow borrow_requests, ui_only overdue_list

    Refusing it did not help: the model changes its output at most once after a
    rejection and then resubmits it unchanged until the run is blocked. The
    correction is determined by what the surface declares, so it is constructed
    here: a surface with entities, mutations, reads or collections of its own is
    durable app logic (`module`), any other is presentation (`ui_only`). Its
    data_contract group and the collections it owns (grouped or shared) take
    the same kind, and a collection it owns written by `workflow_write` is
    written by the module instead: no workflow exists to write it. Each
    correction is returned for the saved record.

    Accept any mapping, not only `dict`. Every live container freezes on read, so
    the caller receives a `MappingProxyType`; an `isinstance(..., dict)` test made
    the original guard a no-op in production while its tests passed on plain
    dicts. The caller detaches as well, because a silent skip here is
    indistinguishable from an approved concept.
    """
    if not isinstance(concept_blueprint, Mapping):
        return []  # no approved concept in scope; nothing to judge against
    if "agentic_capabilities" not in concept_blueprint or concept_blueprint.get("agentic_capabilities"):
        return []
    contract = data_contract if isinstance(data_contract, dict) else {}
    groups = [group for group in contract.get("surfaces") or [] if isinstance(group, dict)]
    grouped = [
        (group.get("surface_id"), collection)
        for group in groups for collection in group.get("collections") or [] if isinstance(collection, dict)
    ]
    grouped.extend(
        (None, collection) for collection in contract.get("shared_collections") or [] if isinstance(collection, dict)
    )
    realized: list[dict[str, Any]] = []
    for surface in surface_map.get("surfaces") or []:
        if surface.get("surface_kind") != "workflow":
            continue
        surface_id = str(surface.get("surface_id"))
        owned = [
            collection for group_id, collection in grouped
            if surface_id in {group_id, (collection.get("ownership") or {}).get("surface_id")}
        ]
        durable = surface.get("primary_entities") or surface.get("owned_mutations") or surface.get("custom_reads")
        kind = "module" if durable or owned else "ui_only"
        surface["surface_kind"] = kind
        for group in groups:
            if group.get("surface_id") == surface_id:
                group["surface_kind"] = kind
        rewritten: list[str] = []
        for collection in owned:
            ownership = collection.get("ownership")
            if isinstance(ownership, dict) and ownership.get("surface_id") == surface_id:
                ownership["surface_kind"] = kind
            lifecycle = collection.get("lifecycle")
            if kind == "module" and isinstance(lifecycle, dict) and lifecycle.get("write_mode") == "workflow_write":
                lifecycle["write_mode"] = "module_action"
                rewritten.append(str(collection.get("name")))
        entry: dict[str, Any] = {
            "surface_id": surface_id, "owner": str(surface.get("owner") or "app"), "removed_collections": [],
            "realized_surface_kind": {"from": "workflow", "to": kind},
        }
        if rewritten:
            entry["module_written_collections"] = rewritten
        realized.append(entry)
    # With no workflow in the app, a module collection declared workflow_write is the module's to write too.
    kinds = {surface.get("surface_id"): surface.get("surface_kind") for surface in surface_map.get("surfaces") or []}
    others: dict[str, list[str]] = {}
    for group_id, collection in grouped:
        owner_id = (collection.get("ownership") or {}).get("surface_id") or group_id
        lifecycle = collection.get("lifecycle")
        if (
            kinds.get(owner_id) == "module" and isinstance(lifecycle, dict)
            and lifecycle.get("write_mode") == "workflow_write"
        ):
            lifecycle["write_mode"] = "module_action"
            others.setdefault(str(owner_id), []).append(str(collection.get("name")))
    for owner_id, names in others.items():
        existing = next((entry for entry in realized if entry["surface_id"] == owner_id), None)
        if existing is not None:
            existing["module_written_collections"] = [*existing.get("module_written_collections", []), *names]
        else:
            realized.append({
                "surface_id": owner_id, "owner": "app", "removed_collections": [], "module_written_collections": names,
            })
    return realized


def _drop_canonical_custom_reads(
    realized: list[dict[str, Any]], surface_map: dict[str, Any], data_contract: dict[str, Any],
) -> None:
    """A surface realized as a module gains code-owned list/get reads; the same ids declared as custom reads go.

    A workflow surface may list them as its own reads, since canonical reads
    exist only for module-owned collections. The removal is recorded on the
    realized entry.
    """
    surfaces = {surface.get("surface_id"): surface for surface in surface_map.get("surfaces") or []}
    for entry in realized:
        surface = surfaces.get(entry["surface_id"])
        if surface is None or (entry.get("realized_surface_kind") or {}).get("to") != "module":
            continue
        try:
            canonical = set(canonical_read_actions_for_surface(surface, data_contract))
        except ValueError:
            continue  # the collection checks below name what is wrong with the contract
        duplicates = [read for read in surface.get("custom_reads") or [] if read in canonical]
        if duplicates:
            surface["custom_reads"] = [read for read in surface.get("custom_reads") or [] if read not in canonical]
            entry["removed_reads"] = duplicates


def _mirror_collection_kinds(data_contract: dict[str, Any], surface_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Give each collection its owning surface's kind; the surface_map declares it once.

    ``ownership.surface_kind`` repeats the owner's ``surface_kind``, so a stale
    copy (a surface the design made a module without updating its collections)
    is determined. Returns one record per surface whose collections changed.
    """
    kinds = {surface.get("surface_id"): surface.get("surface_kind") for surface in surface_map.get("surfaces") or []}
    owners = {surface.get("surface_id"): surface.get("owner") for surface in surface_map.get("surfaces") or []}
    mirrored: dict[str, list[str]] = {}
    groups = [(group["surface_id"], group["collections"]) for group in data_contract["surfaces"]]
    groups.append(("", data_contract["shared_collections"]))
    for group_id, collections in groups:
        for collection in collections:
            ownership = collection.get("ownership") if isinstance(collection, dict) else None
            if not isinstance(ownership, dict):
                continue
            owner_id = ownership.get("surface_id")
            if owner_id not in kinds or (group_id and owner_id != group_id):
                continue
            if ownership.get("surface_kind") != kinds[owner_id]:
                ownership["surface_kind"] = kinds[owner_id]
                mirrored.setdefault(str(owner_id), []).append(str(collection.get("name")))
    return [
        {
            "surface_id": surface_id, "owner": str(owners.get(surface_id) or "app"), "removed_collections": [],
            "mirrored_collection_kinds": names,
        }
        for surface_id, names in mirrored.items()
    ]


def _canonical_data_contract(
    raw: Any,
    *,
    app_id: str,
    artifact_version_id: str | None,
    surface_map: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("data_contract must be an object")

    raw_surfaces = raw.get("surfaces")
    if not isinstance(raw_surfaces, list) or not raw_surfaces:
        raise ValueError("data_contract.surfaces must be a non-empty list")

    known_surface_ids = {
        str(surface.get("surface_id")): str(surface.get("surface_kind"))
        for surface in surface_map.get("surfaces", [])
        if isinstance(surface, dict) and surface.get("surface_id")
    }

    normalized_surfaces = []
    for index, surface in enumerate(raw_surfaces):
        if not isinstance(surface, dict):
            raise ValueError(f"data_contract.surfaces[{index}] must be an object")
        surface_id = str(surface.get("surface_id") or "").strip()
        surface_kind = str(surface.get("surface_kind") or "").strip()
        if not surface_id or not surface_kind:
            raise ValueError(f"data_contract.surfaces[{index}] requires surface_id and surface_kind")
        expected_kind = known_surface_ids.get(surface_id)
        if expected_kind and expected_kind != surface_kind:
            raise ValueError(
                f"data_contract.surfaces[{index}] surface_kind must match surface_map "
                f"for '{surface_id}'"
            )
        collections = surface.get("collections")
        if not isinstance(collections, list):
            raise ValueError(f"data_contract.surfaces[{index}].collections must be a list")
        normalized_surfaces.append(
            {
                "surface_id": surface_id,
                "surface_kind": surface_kind,
                "collections": collections,
            }
        )

    shared_collections = raw.get("shared_collections")
    if shared_collections is None:
        shared_collections = []
    if not isinstance(shared_collections, list):
        raise ValueError("data_contract.shared_collections must be a list")

    policies = raw.get("policies")
    if policies is None:
        policies = {
            "default_scope_field": "app_id",
            "allow_destructive_migrations": False,
        }
    if not isinstance(policies, dict):
        raise ValueError("data_contract.policies must be an object")

    default_scope_field = str(policies.get("default_scope_field") or "app_id").strip()
    if not default_scope_field:
        raise ValueError("data_contract.policies.default_scope_field must be non-empty")

    return normalize_data_contract_indexes({
        "version": str(raw.get("version") or "1"),
        "app_id": str(app_id),
        "artifact_version_id": str(artifact_version_id).strip() if artifact_version_id else None,
        "surfaces": normalized_surfaces,
        "shared_collections": shared_collections,
        "policies": {
            "default_scope_field": default_scope_field,
            "allow_destructive_migrations": bool(policies.get("allow_destructive_migrations", False)),
        },
    })


def _entity_home(surface: dict[str, Any], entity: Any) -> str:
    """How a collection's entity gets onto its owner: added by the save for a module, declared otherwise."""
    if surface.get("surface_kind") == "module":
        return "the save adds a module-owned collection's entity to the module's primary_entities"
    return f"declare it in {surface['surface_id']!r}.primary_entities"


def _reject_undeclared_entity(
    path: str,
    collection: dict[str, Any],
    surface: dict[str, Any],
    surfaces: dict[str, dict[str, Any]],
    collections: list[dict[str, Any]],
    *,
    reserved: Any,
    facades: set[str],
) -> None:
    """Name the change that gives a collection a declared entity; every option offered saves.

    ``declare_collection_entities`` already added every entity a module owns
    alone, so what reaches here is a design decision: records on a ui_only
    surface, an entity another surface declares or another module's records
    also hold, a differently spelled declared entity, an undeclared entity on a
    non-module surface, or a platform or provider entity. ``reserved`` tells a
    platform identity or selected-provider entity, which no app surface may own.
    """
    surface_id = surface["surface_id"]
    kind = surface.get("surface_kind")
    name, entity = collection.get("name"), collection.get("entity")
    if not isinstance(entity, str) or not entity.strip():
        raise ValueError(f"{path}.entity must name the collection's records; {_entity_home(surface, entity)}")
    platform_entity = reserved(collection)
    declared_elsewhere = any(
        other_id != surface_id and entity in (other.get("primary_entities") or []) for other_id, other in surfaces.items()
    )
    own_entity = (
        f"an app entity of its own ({entity!r} names platform identity or selected-provider state)"
        if platform_entity else "an entity of its own"
    )
    if kind == "ui_only":
        modules = sorted(
            other_id for other_id, other in surfaces.items()
            if other.get("surface_kind") == "module" and other.get("owner") == "app" and other_id not in facades
        )
        move = (
            f"Move {name!r} into the data_contract group of an app module (one of {modules}), with its "
            f"ownership.surface_id set to that module and ownership.surface_kind 'module'"
            + (f" and {own_entity}" if platform_entity or declared_elsewhere else "")
            + "; "
            if modules else ""
        )
        make = (
            f"make {surface_id!r} a module surface (surface_kind module in surface_map and its data_contract "
            f"group) whose records have {own_entity}"
        )
        raise ValueError(
            f"{path} is owned by ui_only surface {surface_id!r}, which holds no durable records. "
            f"{move + make if move else make[0].upper() + make[1:]}; or drop the collection and declare a custom "
            "read on the module that owns its data."
        )
    shared = sorted(
        (str(other.get("name")), str((other.get("ownership") or {}).get("surface_id")))
        for other in collections
        if other is not collection and other.get("entity") == entity
        and (other.get("ownership") or {}).get("surface_id") != surface_id
        and not any(entity in (candidate.get("primary_entities") or []) for candidate in surfaces.values())
    )
    if shared:
        raise ValueError(
            f"{path}.entity {entity!r} is also the entity of {[f'{n} on {o}' for n, o in shared]}, and no surface "
            f"declares it, so no module owns it alone. Give each of these collections an entity of its own; "
            f"{_entity_home(surface, entity)}."
        )
    declared_by = sorted(
        other_id for other_id, other in surfaces.items()
        if other_id != surface_id and entity in (other.get("primary_entities") or [])
    )
    if declared_by:
        holders = [
            other_id for other_id in declared_by
            if surfaces[other_id].get("surface_kind") == "ui_only" or any(
                other.get("entity") == entity and (other.get("ownership") or {}).get("surface_id") == other_id
                for other in collections
            )
        ]
        targets = [other_id for other_id in declared_by if other_id not in holders]
        move = (
            f"Move {name!r} into the data_contract group of {targets[0]!r}, with its ownership.surface_id set "
            f"to {targets[0]!r}, or give it an entity of its own"
            if len(targets) == 1 else "Give it an entity of its own"
        )
        raise ValueError(
            f"{path}.entity {entity!r} is declared by {declared_by}, not by its owner {surface_id!r} "
            f"(primary_entities={surface.get('primary_entities') or []}), and each collection holds its own "
            f"entity's records. {move}; {_entity_home(surface, entity)}."
        )
    spelling = related_entity_name(entity, surface.get("primary_entities"))
    if spelling is not None and any(
        other.get("entity") == spelling and (other.get("ownership") or {}).get("surface_id") == surface_id
        for other in collections
    ):
        spelling = None  # its records already have a collection: the spelling would duplicate it
    if spelling is not None and not platform_entity:
        raise ValueError(
            f"{path}.entity {entity!r} is not one of {surface_id!r}'s primary_entities; valid "
            f"choices={surface.get('primary_entities') or []}. Use the declared spelling {spelling!r}, or give "
            f"{name!r} an entity of its own; {_entity_home(surface, entity)}."
        )
    if platform_entity:
        raise ValueError(
            f"{path}.entity {entity!r} names platform identity or selected-provider state, and {surface_id!r} does "
            f"not declare it. Name the records for what they are: keep only app data in {name!r} under an app "
            f"entity of its own (for example {surface_id.title().replace('_', '')}{entity}), keyed by user_id where "
            f"it describes a user; {_entity_home(surface, entity)}."
        )
    free = [
        choice for choice in surface.get("primary_entities") or []
        if not any(
            other is not collection and other.get("entity") == choice
            and (other.get("ownership") or {}).get("surface_id") == surface_id
            for other in collections
        )
    ]
    raise ValueError(
        f"{path}.entity {entity!r} must be an explicit primary_entities entry on {surface_id!r}; valid "
        f"choices={surface.get('primary_entities') or []}. Declare it in {surface_id!r}.primary_entities"
        + (f", or use one of {free}." if free else "; each collection holds its own entity's records.")
    )


def _validate_design_collections(
    data_contract: dict[str, Any], surface_map: dict[str, Any], context_variables: Any = None,
) -> list[str]:
    """Resolve explicit entity and row ownership after determined platform normalization.

    Returns the determined field corrections, one message each.
    """
    surfaces = {surface["surface_id"]: surface for surface in surface_map["surfaces"]}
    field_normalizations: list[str] = []
    collections = [
        (group["surface_id"], collection)
        for group in data_contract["surfaces"] for collection in group["collections"]
    ]
    collections.extend(("", collection) for collection in data_contract["shared_collections"])
    # Each owner's collections hold distinct entities: (owner, entity) -> collection name.
    holders: dict[tuple[str, str], str] = {}
    for group_id, collection in collections:
        if not isinstance(collection, dict):
            raise ValueError(f"data_contract collection on {group_id!r} must be an object")
        name = collection.get("name")
        path = f"data_contract collection {name!r}"
        ownership = collection.get("ownership") or {}
        surface_id = ownership.get("surface_id")
        surface = surfaces.get(surface_id)
        if surface is None or (group_id and surface_id != group_id):
            raise ValueError(
                f"{path}.ownership.surface_id must match its surface; valid choices={sorted(surfaces)}"
            )
        if ownership.get("surface_kind") != surface.get("surface_kind"):
            raise ValueError(f"{path}.ownership.surface_kind must be {surface['surface_kind']!r}")
        entities = surface.get("primary_entities") or []
        entity = collection.get("entity")
        if entity not in entities:
            reserved, facades = entity_ownership_facts(context_variables, include_default_subscription=True)
            _reject_undeclared_entity(
                path, collection, surface, surfaces, [item for _, item in collections if isinstance(item, dict)],
                reserved=reserved, facades=facades,
            )
        holder = holders.setdefault((str(surface_id), str(entity)), str(name))
        if holder != name:
            raise ValueError(
                f"data_contract collections {holder!r} and {name!r} on {surface_id!r} both declare entity "
                f"{entity!r}, but each collection holds the records of its own entity. Give {name!r} an entity "
                f"of its own; {_entity_home(surface, entity)}."
            )
        if collection.get("tenancy") == "app_wide":
            collection.setdefault("owner_field", None)
        validate_collection_ownership(collection, path)
        # Field types, defaults and lookups are decided here, where the design agent can revise them.
        for message in normalize_structured_defaults(collection, path):
            logger.warning(f"DATA_CONTRACT_FIELD_NORMALIZED: {message}")
            field_normalizations.append(message)
        validate_collection_fields(collection, path)
    # One owner/entity and owner/name must identify one collection across both locations.
    list(iter_data_contract_collections(data_contract))
    for surface in surfaces.values():
        reads = surface.get("custom_reads") or []
        if not isinstance(reads, list) or any(
            not isinstance(action, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", action)
            for action in reads
        ):
            raise ValueError(f"surface {surface['surface_id']!r}.custom_reads must be snake_case action ids")
        overlap = set(reads) & set(surface.get("owned_mutations") or [])
        if len(reads) != len(set(reads)) or overlap:
            raise ValueError(f"surface {surface['surface_id']!r}.custom_reads must be unique and distinct from owned_mutations")
        canonical = canonical_read_actions_for_surface(surface, data_contract)
        if set(reads) & set(canonical):
            raise ValueError(
                f"surface {surface['surface_id']!r}.custom_reads must exclude code-owned canonical reads {canonical}; "
                "declare only additional read action ids"
            )
    return field_normalizations


def _surface_map_yaml_block(surface_map: dict[str, Any]) -> str:
    return str(yaml.safe_dump(
        {"surface_map": surface_map},
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    )).strip()


def _inject_backend_surface_map(backend_markdown: str, surface_map: dict[str, Any]) -> str:
    doc = str(backend_markdown or "").strip()
    if not doc:
        raise ValueError("backend_markdown must be a non-empty string")

    block = "## Surface Realization Map\n\n```yaml\n" + _surface_map_yaml_block(surface_map) + "\n```"
    pattern = re.compile(
        r"^## Surface Realization Map\s+```yaml\s+.*?```(?:\s+|$)",
        flags=re.MULTILINE | re.DOTALL,
    )
    if pattern.search(doc):
        return pattern.sub(lambda _: block + "\n\n", doc, count=1).strip()
    return doc.rstrip() + "\n\n" + block


def _canonical_experience_spec(raw: Any, *, surface_map: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise the typed ExperienceSpec from the bundle.

    Returns the canonical dict ready to be stored in the experience_spec extra_field
    and set on context_variables.
    """
    if not isinstance(raw, dict):
        raise ValueError("experience_spec must be an object")
    navigation_model = str(raw.get("navigation_model") or "").strip()
    if not navigation_model:
        raise ValueError("experience_spec.navigation_model must be non-empty")
    brand_direction = str(raw.get("brand_direction") or "").strip()
    pages = raw.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("experience_spec.pages must be a non-empty list")
    for idx, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ValueError(f"experience_spec.pages[{idx}] must be an object")
        if not page.get("name") or not page.get("route"):
            raise ValueError(f"experience_spec.pages[{idx}] requires name and route")
        sections = page.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ValueError(f"experience_spec.pages[{idx}].sections must be a non-empty list")
        for sidx, section in enumerate(sections):
            if not isinstance(section, dict):
                raise ValueError(f"experience_spec.pages[{idx}].sections[{sidx}] must be an object")
            if not section.get("primitive") or not section.get("intent"):
                raise ValueError(
                    f"experience_spec.pages[{idx}].sections[{sidx}] requires primitive and intent"
                )
    return {
        "navigation_model": navigation_model,
        "brand_direction": brand_direction,
        "pages": pages,
    }


def _materialize_facade_pages(
    experience_spec: dict[str, Any],
    surface_map: dict[str, Any],
    *,
    pack_id: str,
    contract: dict[str, Any],
) -> set[str]:
    """Project pack-owned page intent before the design inventory is persisted.

    ExperienceSpec requires sections. A header carries the declared identity and
    actions without redesigning the pack UI; AppGenerator's assembly applies the
    pack's full page templates after page_bundle generation.
    """
    pages = experience_spec["pages"]
    surfaces = surface_map["surfaces"]
    required_routes: set[str] = set()
    for facade in contract["facades"]:
        module_id = facade["module_id"]
        owners = [surface for surface in surfaces if surface.get("surface_id") == module_id]
        if len(owners) > 1:
            raise ValueError(f"surface_map must declare facade {module_id!r} at most once.")
        if owners:
            owner = owners[0]
        else:
            owner = {
                "surface_id": module_id,
                "label": module_id.replace("_", " ").title(),
                "surface_kind": "module",
                "owner": "app",
                "source_capability_packs": [pack_id],
                "primary_entities": [],
                "owned_pages": [],
                "owned_mutations": list(dict.fromkeys(
                    action for page in facade["pages"] for action in page["primary_actions"]
                )),
                "integrations": [facade["provider_module"]],
                "notes": "App-owned facade; provider records and usage ledgers remain provider/runtime-owned.",
            }
            surfaces.append(owner)
        owner.update(surface_kind="module", owner="app")
        owner["source_capability_packs"] = list(dict.fromkeys([
            *(owner.get("source_capability_packs") or []), pack_id,
        ]))
        for declared_page in facade["pages"]:
            route = declared_page["route"]
            required_routes.add(route)
            matches = [page for page in pages if page["route"] == route]
            if len(matches) > 1:
                raise ValueError(f"Monetization page {route} must appear exactly once in experience_spec.pages.")
            name = matches[0]["name"] if matches else declared_page["name"]
            conflicting_routes = [page["route"] for page in pages if page["name"] == name and page["route"] != route]
            if conflicting_routes:
                raise ValueError(
                    f"Facade page {name!r} requires route {route}; rename the app-owned "
                    f"pages on {conflicting_routes} so surface_map ownership is unambiguous."
                )
            if matches:
                page = matches[0]
            else:
                intent = f"Use {module_id} for {', '.join(declared_page['primary_actions'])}."
                page = {
                    "name": name,
                    "route": route,
                    "layout": "full-width",
                    "intent": intent,
                    "sections": [{
                        "id": "page-header",
                        "primitive": "PageHeader",
                        "intent": intent,
                        "config_hint": json.dumps({"title": name}),
                    }],
                }
                pages.append(page)
            # Ownership follows the facade contract even when the model already
            # designed this route under a different page name or surface.
            name = page["name"]
            for surface in surfaces:
                owned_pages = surface.get("owned_pages") or []
                if name in owned_pages:
                    surface["owned_pages"] = [owned for owned in owned_pages if owned != name]
            owner["owned_pages"] = [*(owner.get("owned_pages") or []), name]
    return required_routes


def _complete_monetization_pages(
    experience_spec: dict[str, Any],
    surface_map: dict[str, Any],
    context_variables: Any,
) -> None:
    """Materialize declared facades; require design only for app-owned pricing."""
    if _cv_get(context_variables, "brownfield_build_path"):
        return
    enabled = _cv_get(context_variables, "monetization_enabled")
    pages = experience_spec["pages"]
    if enabled is not True:
        if enabled is False and any(page["route"] == "/pricing" for page in pages):
            raise ValueError(
                "monetization_enabled is false: remove the /pricing monetization page "
                "and its surface ownership from this free app's design."
            )
        return

    required_routes = {"/pricing"}
    contract = default_subscription_contract(context_variables)
    if contract is not None:
        required_routes.update(_materialize_facade_pages(
            experience_spec, surface_map, pack_id="mozaikspay", contract=contract,
        ))

    missing = required_routes - {page["route"] for page in pages}
    if missing:
        raise ValueError(
            f"Monetized design is missing required pages {sorted(missing)}. "
            "Design them in experience_spec.pages and the frontend document, and "
            "assign each one primary surface_map owner before saving. Subscription "
            "design and app planning must preserve this approved page inventory."
        )
    for route in sorted(required_routes):
        matches = [page for page in pages if page["route"] == route]
        if len(matches) != 1:
            raise ValueError(f"Monetization page {route} must appear exactly once in experience_spec.pages.")
        name = matches[0]["name"]
        owners = [
            surface for surface in surface_map["surfaces"]
            if name in (surface.get("owned_pages") or [])
        ]
        if len(owners) != 1:
            raise ValueError(
                f"Monetization page {name!r} ({route}) requires exactly one primary "
                "surface_map owner whose owned_pages includes the page name."
            )


def _experience_spec_to_yaml(experience_spec: dict[str, Any], surface_map: dict[str, Any]) -> str:
    """Generate a human-readable YAML representation of the typed ExperienceSpec.

    This is stored as the `content` field in DesignDocuments so that agents that
    read the ui_schema doc as a string (AgentGenerator, AppSchemaAgent) still get
    valid YAML.
    """
    doc: dict[str, Any] = {
        "experience": {
            "navigation_model": experience_spec.get("navigation_model", ""),
            "brand_direction": experience_spec.get("brand_direction", ""),
        },
        "surface_map": surface_map,
        "pages": experience_spec.get("pages", []),
    }
    return str(yaml.safe_dump(
        doc,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    )).strip() + "\n"


async def save_design_doc(
    *,
    kind: str,
    stage: str,
    content: str,
    context_variables: Any = None,
) -> dict[str, Any]:
    app_id = require_build_binding(context_variables).target_app_id
    chat_id = _cv_get(context_variables, "chat_id")
    user_id = _cv_get(context_variables, "user_id")

    if not app_id or not isinstance(app_id, str):
        return {"ok": False, "reason": "missing_app_id"}

    normalized_kind = _normalize_kind(kind)
    if not normalized_kind:
        return {"ok": False, "reason": "invalid_kind"}

    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "reason": "empty_content"}

    pm = AG2PersistenceManager()
    store = BuilderArtifactStore(pm=pm)
    normalized_stage = str(stage or "draft")

    if normalized_kind == _FIRST_DOC:
        await _mark_design_docs_status(
            store=store,
            app_id=app_id,
            user_id=str(user_id) if user_id else None,
            stage=normalized_stage,
            status="running",
        )

    await _upsert_design_doc(
        store=store,
        app_id=app_id,
        user_id=str(user_id) if user_id else None,
        kind=normalized_kind,
        stage=normalized_stage,
        content=content,
        source_workflow="DesignDocs",
        source_chat_id=str(chat_id) if chat_id else None,
    )

    if normalized_kind == _LAST_DOC:
        await _mark_design_docs_status(
            store=store,
            app_id=app_id,
            user_id=str(user_id) if user_id else None,
            stage=normalized_stage,
            status="succeeded",
        )

    return {
        "ok": True,
        "app_id": app_id,
        "kind": normalized_kind,
        "stage": normalized_stage,
        "len": len(content),
    }


async def save_design_docs_bundle(
    *,
    context_variables: Any = None,
) -> dict[str, Any]:
    binding = require_build_binding(context_variables)
    app_id = binding.target_app_id
    chat_id = _cv_get(context_variables, "chat_id")
    user_id = _cv_get(context_variables, "user_id")
    artifact_version_id = _cv_get(context_variables, "artifact_version_id")
    build_id = binding.build_id
    revision_scope = _cv_get(context_variables, "revision_scope")
    build_mode = "revision" if binding.phase == "refinement" else "genesis"

    if not app_id or not isinstance(app_id, str):
        # Nothing the agent writes can bind a target app, so this is terminal.
        return _refused(
            context_variables,
            "missing_app_id",
            "No target app is bound to this run.",
            outcome="blocked",
        )

    bundle = _extract_bundle(context_variables)
    if not isinstance(bundle, dict):
        return _refused(
            context_variables,
            "missing_design_docs_bundle",
            "No DesignDocsBundle was produced for this turn.",
        )

    try:
        frontend_markdown = str(bundle.get("frontend_markdown") or "").strip()
        backend_markdown = str(bundle.get("backend_markdown") or "").strip()
        database_markdown = str(bundle.get("database_markdown") or "").strip()
        surface_map = _canonical_surface_map(bundle.get("surface_map"))
        realized_surfaces = _realize_undeclared_workflow_surfaces(
            surface_map,
            bundle.get("data_contract"),
            detach(_cv_get(context_variables, "concept_blueprint")),
        )
        experience_spec = _canonical_experience_spec(
            bundle.get("experience_spec"),
            surface_map=surface_map,
        )
        if binding.phase == "genesis":
            _complete_monetization_pages(experience_spec, surface_map, context_variables)
        data_contract = _canonical_data_contract(
            bundle.get("data_contract"),
            app_id=app_id,
            artifact_version_id=str(artifact_version_id) if artifact_version_id else None,
            surface_map=surface_map,
        )
        declared_entities = declare_collection_entities(
            surface_map,
            context_variables=context_variables,
            data_contract=data_contract,
            include_default_subscription=True,
        )
        ownership_normalizations = realized_surfaces + declared_entities + normalize_surface_ownership(
            surface_map,
            context_variables=context_variables,
            data_contract=data_contract,
            experience_spec=experience_spec,
            include_default_subscription=True,
        )
        ownership_normalizations += _mirror_collection_kinds(data_contract, surface_map)
        _drop_canonical_custom_reads(realized_surfaces, surface_map, data_contract)
        field_normalizations = _validate_design_collections(data_contract, surface_map, context_variables)
        if not frontend_markdown or not backend_markdown or not database_markdown:
            raise ValueError("DesignDocsBundle must include all three Markdown documents")
        normalization_messages = [
            f"DESIGN_OWNERSHIP_NORMALIZED surface={entry['surface_id']} owner={entry['owner']} "
            f"removed=[{','.join(entry['removed_collections'])}]"
            + (
                f" surface_kind={entry['realized_surface_kind']['from']}->{entry['realized_surface_kind']['to']}"
                if entry.get("realized_surface_kind") else ""
            )
            + "".join(
                f" {key}=[{','.join(entry[key])}]"
                for key in (
                    "added_entities", "module_written_collections", "mirrored_collection_kinds",
                    "removed_mutations", "removed_reads", "removed_events",
                ) if entry.get(key)
            )
            + "".join(
                f" mapped={item['from']}->{item['to']}" for item in entry.get("mapped_actions", [])
            )
            + (
                " released_pages=["
                + ",".join(f"{page['name']}->{'|'.join(page['owners'])}" for page in entry["released_pages"])
                + "]"
                if entry.get("released_pages") else ""
            )
            + (
                " removed_pages=["
                + ",".join(
                    f"{page['name']}@{page['route']}"
                    + (f"->admin:{page['builtin_panel']}" if page.get("builtin_panel") else "")
                    for page in entry["removed_pages"]
                )
                + "]"
                if entry.get("removed_pages") else ""
            )
            + (
                " removed_navigation=["
                + ",".join(
                    f"{item['page']}/{item['section']}:{item['route']}({item['removed']})"
                    for item in entry["removed_navigation"]
                )
                + "]"
                if entry.get("removed_navigation") else ""
            )
            + (
                " redirected=["
                + ",".join(
                    f"{item['page']}/{item['section']}:{item['from']}->{item['to']}"
                    for item in entry["redirected_navigation"]
                )
                + "]"
                if entry.get("redirected_navigation") else ""
            )
            + "".join(
                f" split={split['name']}->{split['target_surface_id']}/{split['target_collection']}"
                f" retained=[{','.join(split['retained_fields'])}]"
                f" stripped=[{','.join(split['removed_fields'])}]"
                for split in entry.get("split_collections", [])
            )
            + "".join(
                f" rebound={item['page']}/{item['section']}:{item['from']}->{item['to']}"
                for item in entry.get("rebound_sections", [])
            )
            + "".join(
                f" removed_section={item['page']}/{item['section']}:{item['binding']}"
                for item in entry.get("removed_sections", [])
            )
            for entry in ownership_normalizations
        ]
        if normalization_messages:
            notice = (
                "\n\n## Ownership normalizations\n\n"
                "These corrections supersede conflicting ownership or storage claims in the design prose. "
                "The saved surface_map, data_contract, and experience_spec are authoritative: app-owned "
                "pages are preserved, pages duplicating the platform's sign-in are removed because the "
                "auth contract already serves them, and pages listing platform user accounts are removed "
                "(with any navigation to them) because the platform administers users itself."
                + "".join(
                    clause for key, clause in (
                        ("mapped_actions", " A provider action maps to the facade action that serves it."),
                        ("released_pages", " A page an app-owned surface also owns stays the app's."),
                        ("realized_surface_kind", " An AI workflow surface the concept never asked for is "
                                                  "realized as a module or ui_only surface."),
                        ("added_entities", " A module lists the entities of the collections it owns."),
                    )
                    if any(entry.get(key) for entry in ownership_normalizations)
                )
                + "\n\n"
                + "\n".join(f"- `{message}`" for message in normalization_messages)
            )
            frontend_markdown += notice
            backend_markdown += notice
            database_markdown += notice
        if field_normalizations:
            database_markdown += (
                "\n\n## Data contract field normalizations\n\n"
                "These determined default corrections supersede the field defaults in the design prose; the "
                "saved data_contract is authoritative.\n\n"
                + "\n".join(f"- `{message}`" for message in field_normalizations)
            )
        backend_markdown = _inject_backend_surface_map(backend_markdown, surface_map)
        # Generate human-readable YAML for agents that consume ui_schema as a string
        ui_schema_content = _experience_spec_to_yaml(experience_spec, surface_map)
        if ownership_normalizations:
            ui_schema_content += yaml.safe_dump({"ownership_normalizations": ownership_normalizations}, sort_keys=False)
    except Exception as err:
        return _refused(context_variables, "invalid_design_docs_bundle", str(err))

    pm = AG2PersistenceManager()
    store = BuilderArtifactStore(pm=pm)
    normalized_stage = "draft"

    await _mark_design_docs_status(
        store=store,
        app_id=app_id,
        user_id=str(user_id) if user_id else None,
        stage=normalized_stage,
        status="running",
    )

    docs: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
        (DesignDocKinds.FRONTEND, frontend_markdown, None),
        (DesignDocKinds.BACKEND, backend_markdown, {"surface_map": surface_map}),
        (DesignDocKinds.DATABASE, database_markdown, None),
        # Store human-readable YAML as content; typed ExperienceSpec in extra_fields
        # so AppPlanAgent can load it as a structured object via the experience_spec field.
        (DesignDocKinds.UI_SCHEMA, ui_schema_content, {"surface_map": surface_map, "experience_spec": experience_spec}),
    )

    for kind, content, extra_fields in docs:
        if ownership_normalizations:
            extra_fields = {**(extra_fields or {}), "ownership_normalizations": ownership_normalizations}
        if field_normalizations and kind == DesignDocKinds.DATABASE:
            extra_fields = {**(extra_fields or {}), "field_normalizations": field_normalizations}
        await _upsert_design_doc(
            store=store,
            app_id=app_id,
            user_id=str(user_id) if user_id else None,
            kind=kind,
            stage=normalized_stage,
            content=content,
            source_workflow="DesignDocs",
            source_chat_id=str(chat_id) if chat_id else None,
            extra_fields=extra_fields,
        )

    await store.save_data_contract(
        app_id=app_id,
        build_id=str(build_id or chat_id or "design-docs"),
        artifact_version_id=str(artifact_version_id) if artifact_version_id else None,
        change_class=str(revision_scope) if revision_scope else None,
        data_contract=data_contract,
        user_id=str(user_id) if user_id else None,
        source_workflow="DesignDocs",
        source_chat_id=str(chat_id) if chat_id else None,
    )

    await persist_summary_artifact(
        app_id=app_id,
        artifact_kind="design_docs",
        artifact_key="design_docs",
        summary_payload={
            "frontend_markdown": frontend_markdown,
            "backend_markdown": backend_markdown,
            "database_markdown": database_markdown,
            "experience_spec": experience_spec,
            "surface_map": surface_map,
            "data_contract": data_contract,
            "ownership_normalizations": ownership_normalizations,
            **({"field_normalizations": field_normalizations} if field_normalizations else {}),
        },
        source_workflow="DesignDocs",
        source_chat_id=str(chat_id) if chat_id else None,
        author_user_id=str(user_id) if user_id else None,
        revision_mode=str(build_mode or "").strip().lower() == "revision",
        input_artifact_kinds=("concept",),
    )

    await _mark_design_docs_status(
        store=store,
        app_id=app_id,
        user_id=str(user_id) if user_id else None,
        stage=normalized_stage,
        status="succeeded",
    )

    _cv_set(context_variables, "frontend_design_document", frontend_markdown)
    _cv_set(context_variables, "backend_design_document", backend_markdown)
    _cv_set(context_variables, "database_design_document", database_markdown)
    # Human-readable YAML for agents that consume ui_schema as a string (AgentGenerator, AppSchemaAgent)
    _cv_set(context_variables, "ui_design_document", ui_schema_content)
    _cv_set(context_variables, "experience_spec_document", ui_schema_content)
    # Typed object for AppPlanAgent — authoritative structured page specification
    _cv_set(context_variables, "experience_spec", experience_spec)
    _cv_set(context_variables, "design_surface_map", surface_map)
    _cv_set(context_variables, "data_contract", data_contract)
    for message in normalization_messages:
        logger.info(message)

    return {
        "ok": True,
        "app_id": app_id,
        "stage": normalized_stage,
        "kinds": list(_DOC_KINDS),
        "outcome": "saved",
        "surface_count": len(surface_map.get("surfaces", [])),
        "page_count": len(experience_spec.get("pages", [])),
        "data_surface_count": len(data_contract.get("surfaces", [])),
    }

