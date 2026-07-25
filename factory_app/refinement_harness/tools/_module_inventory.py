"""
Deterministic module inventory extractor and carry-forward classifier.

This utility is **read-only**. It consumes a ``file_map`` as returned by
:func:`factory_app.control_plane.tools._artifact_workspace.load_artifact_workspace`
and produces structured inventory entries for every module found in the map,
including an advisory carry-forward reuse-fit classification.

Intended use:
- ``conceptual_replan`` carry-forward analysis.
- Populating the ``carry_forward_modules`` context seed for AppPlanAgent.

This module does NOT decide what to carry forward, does NOT copy or merge files,
and has NO filesystem, database, or LLM access.

Classification is deterministic and conservative:

- ``safe_carry_forward``: module_id matches a known generic/infrastructure module
  list, or has infrastructure-only signals with no persistence or domain markers.
- ``needs_adaptation``: module has persistence, admin panels, reactions, runtime
  extensions, or domain events that may not fit the new concept — review required.
- ``regenerate``: module_id contains a domain-specific fragment that strongly
  implies it was built for the old concept and should not be blindly preserved.

AppPlanAgent is still the final planner. Classification is advisory only.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classification constants
# ---------------------------------------------------------------------------

# Module IDs that are generic/infrastructure across most app concepts.
# These are exact matches against module_id.lower().
_SAFE_MODULE_IDS: frozenset[str] = frozenset({
    "settings",
    "preferences",
    "notifications",
    "files",
    "media",
    "audit",
    "activity",
    "auth",
    "profile",
    "users",
    "organizations",
    "teams",
    "billing_portal",
    "integrations",
})

# Substrings that strongly signal a domain-specific module.
# Checked as substring matches against module_id.lower() so that
# "projects", "task_list", "lead_tracker" etc. are all caught.
_DOMAIN_FRAGMENTS: frozenset[str] = frozenset({
    "crm",
    "lead",
    "deal",
    "pipeline",
    "listing",
    "order",
    "product",
    "booking",
    "campaign",
    "invoice",
    "ticket",
    "project",
    "task",
})

# CRUD verbs that appear in entity-specific action IDs.
_CRUD_VERBS: frozenset[str] = frozenset({
    "create",
    "list",
    "get",
    "update",
    "delete",
    "remove",
    "archive",
})

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class ModuleInventoryEntry(BaseModel):
    """Structured representation of a single module found in an app-bundle file_map."""

    module_id: str = Field(description="Module directory name under modules/. Source of truth is the path, not module.yaml id.")

    # Action contract
    action_ids: list[str] = Field(default_factory=list, description="Action ids declared in module.yaml actions[].id.")

    # Persistence signal
    has_persistence: bool = Field(
        default=False,
        description=(
            "True if backend/repo.py exists (canonical persistence layer), "
            "OR if any action in module.yaml has a persistence-signalling field."
        ),
    )

    # Convenience backend file flags
    has_handler: bool = Field(default=False, description="True if backend/handler.py exists.")
    has_service: bool = Field(default=False, description="True if backend/service.py exists.")
    has_repo: bool = Field(default=False, description="True if backend/repo.py exists.")
    has_policy: bool = Field(default=False, description="True if backend/policy.py exists.")

    # Event contract
    event_types: list[str] = Field(
        default_factory=list,
        description=(
            "Event type strings extracted from contracts/events.yaml. "
            "Supports 'type', 'event_type', and 'id' field names defensively."
        ),
    )

    # Contract presence flags
    has_reactions: bool = Field(default=False, description="True if contracts/reactions.yaml exists.")
    has_notifications: bool = Field(default=False, description="True if contracts/notifications.yaml exists.")
    has_policy_hooks: bool = Field(default=False, description="True if contracts/policy_hooks.yaml exists.")
    has_settings: bool = Field(default=False, description="True if contracts/settings.yaml exists.")
    has_admin: bool = Field(default=False, description="True if contracts/admin.yaml exists.")
    has_profile: bool = Field(default=False, description="True if contracts/profile.yaml exists.")
    has_relationships: bool = Field(default=False, description="True if contracts/relationships.yaml exists.")
    has_runtime_extensions: bool = Field(default=False, description="True if runtime_extensions.yaml exists at the module root.")

    # File lists (relative paths as they appear in the file_map)
    backend_files: list[str] = Field(default_factory=list, description=".py files under modules/{id}/backend/.")
    contract_files: list[str] = Field(default_factory=list, description=".yaml files under modules/{id}/contracts/.")

    # Carry-forward reuse-fit classification (deterministic, advisory)
    carry_forward_classification: Literal["safe_carry_forward", "needs_adaptation", "regenerate"] = Field(
        default="needs_adaptation",
        description=(
            "Deterministic advisory classification for carry-forward analysis. "
            "safe_carry_forward: known generic/infrastructure module. "
            "needs_adaptation: may be reusable but has persistence, admin, reactions, or events to review. "
            "regenerate: domain-specific to the old concept; should not be blindly preserved. "
            "AppPlanAgent is the final planner — this classification is advisory only."
        ),
    )
    carry_forward_reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable reasons for the carry_forward_classification.",
    )


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _parse_yaml_safe(content: str, label: str) -> Any:
    """Parse YAML content, returning None on any error."""
    try:
        return yaml.safe_load(content)
    except Exception as exc:
        logger.debug("_module_inventory: failed to parse %s: %s", label, exc)
        return None


def _extract_action_ids(module_doc: Any) -> list[str]:
    """Return action ids from a parsed module.yaml document."""
    if not isinstance(module_doc, dict):
        return []
    actions = module_doc.get("actions") or []
    if not isinstance(actions, list):
        return []
    ids: list[str] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_id = action.get("id")
        if isinstance(action_id, str) and action_id.strip():
            ids.append(action_id.strip())
    return ids


def _has_persistence_signal(module_doc: Any) -> bool:
    """Return True if any action declares an 'emits' field (persistence-adjacent signal).

    This is a heuristic only — the definitive persistence signal is the presence
    of backend/repo.py, checked separately.
    """
    if not isinstance(module_doc, dict):
        return False
    actions = module_doc.get("actions") or []
    if not isinstance(actions, list):
        return False
    for action in actions:
        if not isinstance(action, dict):
            continue
        # 'emits' on an action is a reliable signal of state mutation
        emits = action.get("emits")
        if emits:
            return True
    return False


def _extract_event_types(events_content: str, label: str) -> list[str]:
    """Extract event type strings from contracts/events.yaml content.

    Defensively supports 'type', 'event_type', and 'id' field names.
    Returns an empty list on any parse failure.
    """
    doc = _parse_yaml_safe(events_content, label)
    if not isinstance(doc, dict):
        return []
    events = doc.get("events") or []
    if not isinstance(events, list):
        return []
    types: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        # Prefer 'type', fall back to 'event_type', then 'id'
        value = event.get("type") or event.get("event_type") or event.get("id")
        if isinstance(value, str) and value.strip():
            types.append(value.strip())
    return types


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_module_carry_forward(entry: ModuleInventoryEntry) -> tuple[str, list[str]]:
    """Return (classification, reasons) for a ModuleInventoryEntry.

    Classification is deterministic and conservative:

    Priority (highest to lowest):
    1. **regenerate** — module_id contains a known domain-specific fragment.
    2. **safe_carry_forward** — module_id is in the known generic module list,
       OR the module has infrastructure-only signals (settings/notifications/
       profile) with no persistence and no domain markers.
    3. **needs_adaptation** — default; module has state, admin, reactions,
       events, or CRUD-heavy actions that need review against the new concept.

    Returns:
        A tuple of (classification_str, reasons_list). Both are non-empty.
        AppPlanAgent is the final planner; this output is advisory only.
    """
    reasons: list[str] = []
    module_id_lower = entry.module_id.lower()

    # --- 1. Regenerate: domain-specific module_id fragments ---
    matched_fragments = [f for f in _DOMAIN_FRAGMENTS if f in module_id_lower]
    if matched_fragments:
        for fragment in sorted(matched_fragments):
            reasons.append(
                f"module_id '{entry.module_id}' contains domain-specific fragment '{fragment}'"
            )
        if entry.has_persistence:
            reasons.append("has_persistence=True reinforces domain-specific classification")
        crud_count = _crud_action_count(entry.action_ids)
        if entry.action_ids and crud_count >= max(2, len(entry.action_ids) // 2):
            reasons.append(
                f"{crud_count}/{len(entry.action_ids)} actions are CRUD-style on a domain entity"
            )
        return "regenerate", reasons

    # --- 2. Safe: known generic/infrastructure module ID ---
    if module_id_lower in _SAFE_MODULE_IDS:
        reasons.append(
            f"module_id '{entry.module_id}' matches known generic infrastructure module"
        )
        if entry.has_settings:
            reasons.append("has_settings=True: module manages cross-concept preferences")
        if entry.has_notifications:
            reasons.append("has_notifications=True: notification delivery is concept-independent")
        if entry.has_profile:
            reasons.append("has_profile=True: user profile surface is generic across concepts")
        if entry.has_relationships:
            reasons.append("has_relationships=True: current-user resource relationships are generic across concepts")
        if entry.has_policy_hooks:
            reasons.append("has_policy_hooks=True: policy hook declarations are generic across concepts")
        if entry.has_persistence:
            reasons.append(
                "has_persistence=True: state exists but module is known-safe; "
                "review schema fit if concept data model changes significantly"
            )
        return "safe_carry_forward", reasons

    # --- 2b. Safe: infrastructure-only signals, no domain markers ---
    infra_signals = []
    if entry.has_settings and not entry.has_persistence:
        infra_signals.append("has_settings=True with no persistence: settings-only module")
    if entry.has_notifications and not entry.has_persistence:
        infra_signals.append("has_notifications=True with no persistence: delivery-only module")
    if entry.has_profile and not entry.has_persistence:
        infra_signals.append("has_profile=True with no persistence: display-only profile surface")
    if entry.has_relationships and not entry.has_persistence:
        infra_signals.append("has_relationships=True with no persistence: display-only relationship surface")
    if entry.has_policy_hooks and not entry.has_persistence:
        infra_signals.append("has_policy_hooks=True with no persistence: policy-hook declaration only")

    if infra_signals and not entry.has_admin and not entry.has_reactions and not entry.event_types:
        reasons.extend(infra_signals)
        reasons.append("no admin, reactions, or events: module is infrastructure-facing only")
        return "safe_carry_forward", reasons

    # --- 3. needs_adaptation: mixed or unknown signals ---
    if entry.has_persistence:
        reasons.append(
            "has_persistence=True: module state must be reviewed against the new concept's domain model"
        )
    if entry.has_admin:
        reasons.append("has_admin=True: admin panel contract is concept-adjacent and may need revision")
    if entry.has_reactions:
        reasons.append("has_reactions=True: cross-module reactions may not apply in the new concept")
    if entry.has_runtime_extensions:
        reasons.append(
            "has_runtime_extensions=True: runtime API registration may need concept-level revision"
        )
    if entry.event_types:
        reasons.append(
            f"publishes {len(entry.event_types)} domain event type(s): "
            "event contracts must be validated against the new concept"
        )

    crud_count = _crud_action_count(entry.action_ids)
    if entry.action_ids and crud_count >= max(2, len(entry.action_ids) // 2):
        reasons.append(
            f"{crud_count}/{len(entry.action_ids)} actions are CRUD-style: "
            "likely entity-specific; confirm entity still exists in new concept"
        )

    if not reasons:
        reasons.append(
            "no strong safe or domain-specific signals detected; "
            "conservative default: review before reusing"
        )

    return "needs_adaptation", reasons


def _crud_action_count(action_ids: list[str]) -> int:
    """Return the count of action ids containing standard CRUD verbs."""
    return sum(
        1 for aid in action_ids
        if any(verb in aid.lower() for verb in _CRUD_VERBS)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_module_inventory(file_map: dict[str, str]) -> list[ModuleInventoryEntry]:
    """Extract structured module inventory from an app-bundle file_map.

    The ``file_map`` is expected to be the output of
    :func:`factory_app.control_plane.tools._artifact_workspace.load_artifact_workspace`
    — a ``{relative_path: content}`` dict where paths use forward slashes.

    Modules are identified by the presence of ``modules/{module_id}/module.yaml``.
    The module_id is taken from the path, not from the YAML content.

    Behavior:
    - Invalid ``module.yaml`` (unparseable or wrong type) produces a minimal
      entry with empty action_ids and has_persistence=False rather than skipping.
    - Invalid ``contracts/events.yaml`` produces an empty event_types list.
    - Files outside ``modules/`` are ignored.
    - The input ``file_map`` is never mutated.
    - Returns entries sorted by module_id.
    """
    # Collect all module_ids that have a module.yaml
    module_ids: set[str] = set()
    for path in file_map:
        parts = path.split("/")
        # Expected: modules/{module_id}/module.yaml
        if len(parts) == 3 and parts[0] == "modules" and parts[2] == "module.yaml":
            module_id = parts[1]
            if module_id:
                module_ids.add(module_id)

    entries: list[ModuleInventoryEntry] = []

    for module_id in sorted(module_ids):
        prefix = f"modules/{module_id}/"
        contracts_prefix = f"{prefix}contracts/"
        backend_prefix = f"{prefix}backend/"

        # --- Parse module.yaml ---
        module_yaml_key = f"{prefix}module.yaml"
        module_content = file_map.get(module_yaml_key, "")
        module_doc = _parse_yaml_safe(module_content, module_yaml_key)

        action_ids = _extract_action_ids(module_doc)
        yaml_persistence_signal = _has_persistence_signal(module_doc)

        # --- Backend files ---
        backend_files = sorted(
            path for path in file_map
            if path.startswith(backend_prefix) and path.endswith(".py")
        )

        has_handler = f"{backend_prefix}handler.py" in file_map
        has_service = f"{backend_prefix}service.py" in file_map
        has_repo = f"{backend_prefix}repo.py" in file_map
        has_policy = f"{backend_prefix}policy.py" in file_map

        # Persistence: repo.py is the definitive signal; yaml_persistence_signal is advisory
        has_persistence = has_repo or yaml_persistence_signal

        # --- Contract files ---
        contract_files = sorted(
            path for path in file_map
            if path.startswith(contracts_prefix) and path.endswith(".yaml")
        )

        has_reactions = f"{contracts_prefix}reactions.yaml" in file_map
        has_notifications = f"{contracts_prefix}notifications.yaml" in file_map
        has_policy_hooks = f"{contracts_prefix}policy_hooks.yaml" in file_map
        has_settings = f"{contracts_prefix}settings.yaml" in file_map
        has_admin = f"{contracts_prefix}admin.yaml" in file_map
        has_profile = f"{contracts_prefix}profile.yaml" in file_map
        has_relationships = f"{contracts_prefix}relationships.yaml" in file_map

        # --- Runtime extensions ---
        has_runtime_extensions = f"{prefix}runtime_extensions.yaml" in file_map

        # --- Events ---
        events_key = f"{contracts_prefix}events.yaml"
        event_types: list[str] = []
        if events_key in file_map:
            event_types = _extract_event_types(file_map[events_key], events_key)

        # Build a partial entry for classification (classification depends on
        # the fully assembled inventory signals, so we classify after the entry
        # is assembled with defaults and then create the final object).
        _partial = ModuleInventoryEntry(
            module_id=module_id,
            action_ids=action_ids,
            has_persistence=has_persistence,
            has_handler=has_handler,
            has_service=has_service,
            has_repo=has_repo,
            has_policy=has_policy,
            event_types=event_types,
            has_reactions=has_reactions,
            has_notifications=has_notifications,
            has_policy_hooks=has_policy_hooks,
            has_settings=has_settings,
            has_admin=has_admin,
            has_profile=has_profile,
            has_relationships=has_relationships,
            has_runtime_extensions=has_runtime_extensions,
            backend_files=backend_files,
            contract_files=contract_files,
        )
        classification, reasons = classify_module_carry_forward(_partial)

        entries.append(
            _partial.model_copy(
                update={
                    "carry_forward_classification": classification,
                    "carry_forward_reasons": reasons,
                }
            )
        )

    return entries
