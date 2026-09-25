import re
from collections.abc import Mapping
from typing import Any

import yaml

from factory_app.workflows._shared.hook_utils import workflow_context_path
from factory_app.workflows._shared.platform.build_target import require_build_binding
from logs.logging_config import get_workflow_logger
from mozaiksai.core.artifacts import persist_summary_artifact
from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.workflow.context.frozen import detach

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


def _reject_undeclared_workflow_surfaces(
    surface_map: dict[str, Any],
    concept_blueprint: Any,
) -> None:
    """A workflow surface the approved concept never asked for is not allowed.

    The agent is already told this in its prompt, and it mostly obeys. Mostly is
    not a contract: a live build of a tool-lending library whose concept recorded
    `agentic_capabilities: []` still got

        surface_map: module tool_catalog, workflow borrow_requests, ui_only overdue_list

    Nothing here caught it, so it travelled three stages before `pattern_selection`
    compared the partition against the map and refused it -- terminal, no feedback
    path, no way back to the user. Refusing it at the boundary where it is written
    turns that into a rejection DesignDocs can act on, in the run that produced it.

    Accept any mapping, not only `dict`. Every live container freezes on read, so
    the caller receives a `MappingProxyType`; an `isinstance(..., dict)` test made
    this whole guard a no-op in production while its tests passed on plain dicts.
    The caller detaches as well -- this is the second lock on that door, because a
    silent skip here is indistinguishable from an approved concept.
    """
    if not isinstance(concept_blueprint, Mapping):
        return  # no approved concept in scope; nothing to judge against
    if "agentic_capabilities" not in concept_blueprint:
        return
    if concept_blueprint.get("agentic_capabilities"):
        return
    declared = [
        str(surface.get("surface_id"))
        for surface in surface_map.get("surfaces") or []
        if surface.get("surface_kind") == "workflow"
    ]
    if not declared:
        return
    raise ValueError(
        f"surface_map declares workflow surfaces {declared}, but the approved concept "
        "records no agentic capabilities. Realize them as module or ui_only surfaces, "
        "or leave them out: an app that was not asked for AI cannot declare an AI "
        "workflow, and downstream pattern selection rejects the mismatch outright."
    )


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

    return {
        "version": str(raw.get("version") or "1"),
        "app_id": str(app_id),
        "artifact_version_id": str(artifact_version_id).strip() if artifact_version_id else None,
        "surfaces": normalized_surfaces,
        "shared_collections": shared_collections,
        "policies": {
            "default_scope_field": default_scope_field,
            "allow_destructive_migrations": bool(policies.get("allow_destructive_migrations", False)),
        },
    }


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


def _validate_monetization_pages(
    experience_spec: dict[str, Any],
    surface_map: dict[str, Any],
    context_variables: Any,
) -> None:
    """Require monetization designs before their inventory becomes authoritative."""
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
    blueprint = _cv_get(context_variables, "concept_blueprint") or {}
    intent = blueprint.get("monetization_intent") or {}
    if intent.get("monetized") is True and intent.get("subscription_contract_likely") is True:
        # The default subscription provider already declares its facade pages.
        # Validate them here, before AppGenerator materializes that same pack.
        contract = yaml.safe_load(
            workflow_context_path("mozaikspay", "contract.yaml").read_text(encoding="utf-8")
        )
        required_routes.update(
            page["route"] for facade in contract["facades"] for page in facade["pages"]
        )

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
        _reject_undeclared_workflow_surfaces(
            surface_map,
            detach(_cv_get(context_variables, "concept_blueprint")),
        )
        experience_spec = _canonical_experience_spec(
            bundle.get("experience_spec"),
            surface_map=surface_map,
        )
        if binding.phase == "genesis":
            _validate_monetization_pages(experience_spec, surface_map, context_variables)
        data_contract = _canonical_data_contract(
            bundle.get("data_contract"),
            app_id=app_id,
            artifact_version_id=str(artifact_version_id) if artifact_version_id else None,
            surface_map=surface_map,
        )
        if not frontend_markdown or not backend_markdown or not database_markdown:
            raise ValueError("DesignDocsBundle must include all three Markdown documents")
        backend_markdown = _inject_backend_surface_map(backend_markdown, surface_map)
        # Generate human-readable YAML for agents that consume ui_schema as a string
        ui_schema_content = _experience_spec_to_yaml(experience_spec, surface_map)
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

    docs = (
        (DesignDocKinds.FRONTEND, frontend_markdown, None),
        (DesignDocKinds.BACKEND, backend_markdown, {"surface_map": surface_map}),
        (DesignDocKinds.DATABASE, database_markdown, None),
        # Store human-readable YAML as content; typed ExperienceSpec in extra_fields
        # so AppPlanAgent can load it as a structured object via the experience_spec field.
        (DesignDocKinds.UI_SCHEMA, ui_schema_content, {"surface_map": surface_map, "experience_spec": experience_spec}),
    )

    for kind, content, extra_fields in docs:
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

