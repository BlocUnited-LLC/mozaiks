from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.events.runtime_events import (
    ARTIFACT_EVENT_CREATED,
    ARTIFACT_EVENT_DELETED,
    ARTIFACT_EVENT_READY,
    ARTIFACT_EVENT_UPDATED,
    RUNTIME_AGENT_OUTPUT_VALIDATED,
    RUNTIME_PROCESS_COMPLETED,
)
from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher
from mozaiksai.core.runtime.app.layout_registry import (
    ArtifactKind,
    default_app_layout_registry,
    resolve_taxonomy_artifact_family,
)
from mozaiksai.core.runtime.app.module_loader import ModuleLoader, ModuleLoadError
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsLoadError,
    load_subscriptions_config,
)
from mozaiksai.core.taxonomy import (
    NamespaceKind,
    SemanticCategory,
    TaxonomyEntry,
    TaxonomyNamespace,
    TaxonomyRegistry,
    UnknownTaxonomyIdentifier,
    build_taxonomy_registry,
    default_taxonomy_registry,
    validate_identifier_grammar,
)
from mozaiksai.core.transport.event_contract import (
    EVENT_ENVELOPE_SCHEMA_VERSION,
    MozaiksEventEnvelope,
    MozaiksEventType,
    validate_event_envelope_schema_version,
)

ROOT = Path(__file__).resolve().parents[1]


def _entry(category: SemanticCategory, identifier: str) -> TaxonomyEntry:
    return TaxonomyEntry(category=category, identifier=identifier)


def _namespace(
    namespace_id: str,
    *entries: TaxonomyEntry,
    kind: NamespaceKind = NamespaceKind.CORE,
    grants: tuple[str, ...] = (),
    version: int = 1,
) -> TaxonomyNamespace:
    return TaxonomyNamespace(
        namespace_id=namespace_id,
        version=version,
        kind=kind,
        grants=grants,
        entries=entries,
    )


def test_registry_serialization_digest_and_input_order_are_deterministic() -> None:
    left = _namespace(
        "example.events",
        _entry(SemanticCategory.EVENT, "example.second"),
        _entry(SemanticCategory.EVENT, "example.first"),
    )
    right = _namespace(
        "example.capabilities",
        _entry(SemanticCategory.CAPABILITY, "example.read"),
    )

    first = build_taxonomy_registry((left, right))
    second = build_taxonomy_registry((right, left))

    assert first.registry_digest == second.registry_digest
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_json() == first.canonical_json()


def test_registry_validation_does_not_mutate_inputs() -> None:
    registry = default_taxonomy_registry()
    payload = registry.model_dump(mode="json")
    original = deepcopy(payload)

    TaxonomyRegistry.model_validate(payload)

    assert payload == original


@pytest.mark.parametrize(
    "category,identifier",
    [(SemanticCategory.EVENT, item.value) for item in MozaiksEventType]
    + [
        (SemanticCategory.EVENT, RUNTIME_AGENT_OUTPUT_VALIDATED),
        (SemanticCategory.EVENT, RUNTIME_PROCESS_COMPLETED),
        (SemanticCategory.EVENT, ARTIFACT_EVENT_CREATED),
        (SemanticCategory.EVENT, ARTIFACT_EVENT_UPDATED),
        (SemanticCategory.EVENT, ARTIFACT_EVENT_READY),
        (SemanticCategory.EVENT, ARTIFACT_EVENT_DELETED),
        (SemanticCategory.EVENT, "build.started"),
        (SemanticCategory.EVENT, "build.completed"),
        (SemanticCategory.EVENT, "build.failed"),
        (SemanticCategory.CAPABILITY, "commerce.checkout.start"),
        (SemanticCategory.CAPABILITY, "messaging.messages.send"),
        (SemanticCategory.CAPABILITY, "mozaikspay.subscription_status"),
    ],
)
def test_grandfathered_current_identifiers_resolve(
    category: SemanticCategory,
    identifier: str,
) -> None:
    assert default_taxonomy_registry().resolve(category, identifier).identifier == identifier


def test_all_shipped_module_event_and_capability_names_are_grandfathered() -> None:
    registry = default_taxonomy_registry()
    event_names: set[str] = set()
    capability_names: set[str] = set()

    for path in (ROOT / "factory_app").rglob("*.yaml"):
        if path.name not in {"events.yaml", "module.yaml"}:
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            continue
        if path.name == "events.yaml" and raw.get("schema_version") == "mozaiks.events.v1":
            for event in raw.get("events") or []:
                if isinstance(event, dict) and isinstance(event.get("type"), str):
                    event_names.add(event["type"])
        if path.name == "module.yaml" and raw.get("schema_version") == "mozaiks.module.v1":
            for capability in raw.get("capabilities") or []:
                if isinstance(capability, dict) and isinstance(capability.get("capability_id"), str):
                    capability_names.add(capability["capability_id"])
            for action in raw.get("actions") or []:
                if isinstance(action, dict) and isinstance(action.get("entitlement_gate"), str):
                    capability_names.add(action["entitlement_gate"])

    assert event_names
    assert capability_names
    registry.validate_closure((SemanticCategory.EVENT, value) for value in event_names)
    registry.validate_closure(
        (SemanticCategory.CAPABILITY, value) for value in capability_names
    )


def test_all_layout_artifact_families_resolve_to_authoritative_rows() -> None:
    registry = default_app_layout_registry()
    taxonomy = default_taxonomy_registry()
    artifact_entries = [
        entry
        for namespace in taxonomy.namespaces
        for entry in namespace.entries
        if entry.category is SemanticCategory.ARTIFACT_FAMILY
    ]
    for entry in artifact_entries:
        kind = ArtifactKind(entry.identifier)
        rows = resolve_taxonomy_artifact_family(entry.identifier)
        assert rows
        assert all(row in registry.families and row.kind is kind for row in rows)


def test_taxonomy_entry_cannot_copy_layout_path_or_metadata() -> None:
    with pytest.raises(ValidationError, match="extra"):
        TaxonomyEntry.model_validate(
            {
                "category": "artifact_family",
                "identifier": "app_manifest",
                "path_template": "shadow/app.json",
                "renderer": "shadow_renderer",
            }
        )


def test_missing_layout_family_target_fails() -> None:
    registry = build_taxonomy_registry(
        (
            _namespace(
                "example.artifacts", _entry(SemanticCategory.ARTIFACT_FAMILY, "missing_family")
            ),
        )
    )
    with pytest.raises(ValueError, match="no ArtifactKind"):
        resolve_taxonomy_artifact_family("missing_family", taxonomy_registry=registry)


def test_duplicate_identifiers_and_namespace_versions_fail() -> None:
    duplicate_entry = _entry(SemanticCategory.EVENT, "example.changed")
    with pytest.raises(ValueError, match="duplicate or conflicting"):
        build_taxonomy_registry(
            (
                _namespace("first.events", duplicate_entry),
                _namespace("second.events", duplicate_entry),
            )
        )

    namespace = _namespace("example.events", duplicate_entry)
    with pytest.raises(ValueError, match="duplicate namespace/version"):
        build_taxonomy_registry((namespace, namespace))


def test_extension_namespace_isolation_and_core_protection() -> None:
    extension = _namespace(
        "vendor.events",
        _entry(SemanticCategory.EVENT, "vendor.changed"),
        kind=NamespaceKind.EXTENSION,
        grants=("vendor",),
    )
    assert build_taxonomy_registry((extension,)).resolve(SemanticCategory.EVENT, "vendor.changed")

    with pytest.raises(ValueError, match="outside granted"):
        _namespace(
            "vendor.events",
            _entry(SemanticCategory.EVENT, "other.changed"),
            kind=NamespaceKind.EXTENSION,
            grants=("vendor",),
        )

    core = _namespace(
        "core.events", _entry(SemanticCategory.EVENT, "runtime.changed"), grants=("runtime",)
    )
    redefining = _namespace(
        "vendor.events",
        _entry(SemanticCategory.EVENT, "runtime.changed"),
        kind=NamespaceKind.EXTENSION,
        grants=("runtime",),
    )
    with pytest.raises(ValueError, match="conflicts|duplicate or conflicting"):
        build_taxonomy_registry((core, redefining))


def test_event_reference_closure_fails_on_unknown_name() -> None:
    registry = default_taxonomy_registry()
    registry.validate_closure(((SemanticCategory.EVENT, "runtime.process_completed"),))
    with pytest.raises(UnknownTaxonomyIdentifier, match="unknown event"):
        registry.validate_closure(((SemanticCategory.EVENT, "runtime.not_registered"),))


def test_module_and_subscription_capabilities_share_one_grammar() -> None:
    assert (
        validate_identifier_grammar(SemanticCategory.CAPABILITY, "reports.generate")
        == "reports.generate"
    )
    with pytest.raises(ValueError, match=r"\[a-z0-9_.\]\+"):
        validate_identifier_grammar(SemanticCategory.CAPABILITY, "Reports-Generate")


@pytest.mark.asyncio
async def test_dispatcher_advisory_rejects_unknown_but_production_behavior_is_unchanged() -> None:
    production = UnifiedEventDispatcher()
    await production.emit("runtime.not_registered", {})

    advisory = UnifiedEventDispatcher(taxonomy_advisory=True)
    with pytest.raises(UnknownTaxonomyIdentifier, match="runtime.not_registered"):
        await advisory.emit("runtime.not_registered", {})


def _write_module(root: Path, *, capability_id: str, event_type: str) -> None:
    module_dir = root / "modules" / "sample"
    (module_dir / "backend").mkdir(parents=True)
    (module_dir / "contracts").mkdir()
    (module_dir / "backend" / "handler.py").write_text(
        "class SampleModule:\n    pass\n",
        encoding="utf-8",
    )
    (module_dir / "module.yaml").write_text(
        f"""schema_version: mozaiks.module.v1
module:
  id: sample
  handler: backend.handler:SampleModule
capabilities:
  - capability_id: {capability_id}
    kind: page
    target: /sample
    title: Sample
actions: []
""",
        encoding="utf-8",
    )
    (module_dir / "contracts" / "events.yaml").write_text(
        f"""schema_version: mozaiks.events.v1
events:
  - type: {event_type}
    version: 1
    producer: sample
    payload_schema: {{}}
""",
        encoding="utf-8",
    )


def test_module_loader_advisory_is_explicit_and_fail_closed(tmp_path: Path) -> None:
    _write_module(tmp_path, capability_id="unknown.capability", event_type="domain.unknown.changed")
    assert ModuleLoader(str(tmp_path)).load("sample").name == "sample"
    with pytest.raises(ModuleLoadError, match="unknown capability"):
        ModuleLoader(str(tmp_path), taxonomy_advisory=True).load("sample")


def test_module_event_advisory_rejects_unknown_registered_prefix_name(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        capability_id="commerce.checkout.start",
        event_type="domain.unknown.changed",
    )
    with pytest.raises(ModuleLoadError, match="unknown event"):
        ModuleLoader(str(tmp_path), taxonomy_advisory=True).load("sample")


def test_subscription_loader_advisory_is_explicit_and_does_not_mutate(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "subscriptions.yaml"
    content = """schema_version: mozaiks.subscriptions.v1
label: Example subscriptions
default_plan_id: free
plans:
  - plan_id: free
    label: Free
    capabilities: [unknown.capability]
"""
    path.write_text(content, encoding="utf-8")

    assert load_subscriptions_config(tmp_path) is not None
    assert path.read_text(encoding="utf-8") == content
    with pytest.raises(SubscriptionsLoadError, match="unknown capability"):
        load_subscriptions_config(tmp_path, taxonomy_advisory=True)
    assert path.read_text(encoding="utf-8") == content


def test_event_envelope_schema_version_is_required_and_guarded() -> None:
    valid = {
        "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
        "type": "chat.text",
    }
    assert (
        MozaiksEventEnvelope.model_validate(valid).schema_version == EVENT_ENVELOPE_SCHEMA_VERSION
    )
    validate_event_envelope_schema_version(valid)

    with pytest.raises(ValidationError, match="schema_version"):
        MozaiksEventEnvelope.model_validate({"type": "chat.text"})
    with pytest.raises(ValueError, match="got None"):
        validate_event_envelope_schema_version({"type": "chat.text"})
    with pytest.raises(ValueError, match="mozaiks.ui.event.v2"):
        validate_event_envelope_schema_version(
            {"schema_version": "mozaiks.ui.event.v2", "type": "chat.text"}
        )


def test_dispatcher_builds_versioned_envelopes_for_transport_consumer() -> None:
    envelope = UnifiedEventDispatcher().build_outbound_event_envelope(
        raw_event={"kind": "text", "content": "hello"},
        chat_id="chat-1",
    )
    assert envelope is not None
    validate_event_envelope_schema_version(envelope)
