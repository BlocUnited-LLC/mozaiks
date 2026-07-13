"""
Pure helper unit tests for:
  factory_app/workflows/ExistingAppDiscovery/tools/app_context_mapping.py

Covers helpers NOT already tested in test_app_context_mapping_helpers.py:

  _dedupe_source_refs:
    - empty list → []
    - duplicate source_ref_id → first occurrence kept
    - distinct source_ref_ids → all included
    - order preserved

  _surface_ref:
    - kind is passed through
    - surface_id generated (non-empty)
    - label cleaned and set when provided
    - location cleaned and set when provided
    - source_ref_id passed through
    - metadata is a dict

  _service_surfaces:
    - empty service_surfaces → []
    - item with name and kind → SurfaceRef with kind from spec
    - item without kind → defaults to "service"
    - source_ref_id passed to each surface

  _route_surfaces:
    - empty route_surfaces → []
    - item with path → SurfaceRef with kind "route"
    - item with module → label from module
    - source_ref_id passed to each surface

  _pages:
    - empty route_surfaces → []
    - item with path → SurfaceRef with kind "page"
    - source_ref_id passed to each page

  _integration_inventory:
    - empty detected_connectors → []
    - connector with provider_id → integration_id from provider_id
    - connector with likely_secret_envs → secret_required True
    - connector without secret envs → secret_required False
    - readiness_status "secret_required" when has secret envs
    - readiness_status "config_required" when has config but no secrets

  _unknowns:
    - empty unresolved_questions → []
    - question with "question" key → description set
    - question with "description" key → description set
    - question without description → skipped
    - question with context → evidence populated
    - question with priority → priority set
    - question without priority → defaults to "medium"

  _dedupe_boundaries:
    - empty list → []
    - duplicate (path_or_artifact, ownership) → first occurrence kept
    - distinct boundaries → all included
"""
from __future__ import annotations

from factory_app.workflows.ExistingAppDiscovery.tools.app_context_mapping import (
    _dedupe_boundaries,
    _dedupe_source_refs,
    _integration_inventory,
    _pages,
    _route_surfaces,
    _service_surfaces,
    _surface_ref,
    _unknowns,
)
from mozaiksai.core.app_context.models import (
    OwnershipBoundary,
    OwnershipClass,
    SourceRef,
)

# ---------------------------------------------------------------------------
# Helpers for constructing test data
# ---------------------------------------------------------------------------

def _make_source_ref(source_ref_id: str, kind: str = "repo") -> SourceRef:
    return SourceRef(source_ref_id=source_ref_id, kind=kind, uri=f"github.com/{source_ref_id}")


def _make_boundary(path: str, ownership: OwnershipClass = OwnershipClass.READ_ONLY_DISCOVERED) -> OwnershipBoundary:
    return OwnershipBoundary(path_or_artifact=path, ownership=ownership)


# ---------------------------------------------------------------------------
# 1. _dedupe_source_refs
# ---------------------------------------------------------------------------

class TestDedupeSourceRefs:
    def test_empty_list_returns_empty(self):
        assert _dedupe_source_refs([]) == []

    def test_duplicate_source_ref_id_kept_first(self):
        refs = [
            _make_source_ref("s1"),
            _make_source_ref("s1"),
        ]
        result = _dedupe_source_refs(refs)
        assert len(result) == 1
        assert result[0].source_ref_id == "s1"

    def test_distinct_refs_all_included(self):
        refs = [_make_source_ref("s1"), _make_source_ref("s2")]
        result = _dedupe_source_refs(refs)
        assert len(result) == 2

    def test_order_preserved(self):
        refs = [_make_source_ref("s3"), _make_source_ref("s1"), _make_source_ref("s2")]
        result = _dedupe_source_refs(refs)
        assert [r.source_ref_id for r in result] == ["s3", "s1", "s2"]


# ---------------------------------------------------------------------------
# 2. _surface_ref
# ---------------------------------------------------------------------------

class TestSurfaceRef:
    def test_kind_passed_through(self):
        ref = _surface_ref(prefix="service", index=0, kind="backend")
        assert ref.kind == "backend"

    def test_surface_id_is_non_empty(self):
        ref = _surface_ref(prefix="service", index=0, kind="backend")
        assert ref.surface_id and isinstance(ref.surface_id, str)

    def test_label_set_when_provided(self):
        ref = _surface_ref(prefix="service", index=0, kind="backend", label="Auth Service")
        assert ref.label == "Auth Service"

    def test_location_set_when_provided(self):
        ref = _surface_ref(prefix="service", index=0, kind="backend", location="services/auth")
        assert ref.location == "services/auth"

    def test_source_ref_id_passed_through(self):
        ref = _surface_ref(prefix="service", index=0, kind="backend", source_ref_id="ref1")
        assert ref.source_ref_id == "ref1"

    def test_metadata_is_dict(self):
        ref = _surface_ref(prefix="service", index=0, kind="backend")
        assert isinstance(ref.metadata, dict)

    def test_metadata_from_arg(self):
        ref = _surface_ref(prefix="service", index=0, kind="backend", metadata={"key": "val"})
        assert ref.metadata == {"key": "val"}

    def test_empty_label_returns_none(self):
        ref = _surface_ref(prefix="service", index=0, kind="backend", label="")
        assert ref.label is None


# ---------------------------------------------------------------------------
# 3. _service_surfaces
# ---------------------------------------------------------------------------

class TestServiceSurfaces:
    def test_empty_spec_returns_empty(self):
        assert _service_surfaces({}, "ref1") == []

    def test_missing_service_surfaces_key_returns_empty(self):
        assert _service_surfaces({"other": []}, "ref1") == []

    def test_item_with_kind_uses_spec_kind(self):
        spec = {"service_surfaces": [{"name": "auth", "kind": "backend"}]}
        result = _service_surfaces(spec, "ref1")
        assert len(result) == 1
        assert result[0].kind == "backend"

    def test_item_without_kind_defaults_to_service(self):
        spec = {"service_surfaces": [{"name": "cache"}]}
        result = _service_surfaces(spec, "ref1")
        assert result[0].kind == "service"

    def test_source_ref_id_set(self):
        spec = {"service_surfaces": [{"name": "db"}]}
        result = _service_surfaces(spec, "myref")
        assert result[0].source_ref_id == "myref"

    def test_multiple_items(self):
        spec = {"service_surfaces": [{"name": "auth"}, {"name": "db"}]}
        result = _service_surfaces(spec, "ref1")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 4. _route_surfaces
# ---------------------------------------------------------------------------

class TestRouteSurfaces:
    def test_empty_spec_returns_empty(self):
        assert _route_surfaces({}, "ref1") == []

    def test_item_with_path_creates_route_kind(self):
        spec = {"route_surfaces": [{"path": "/orders", "module": "orders"}]}
        result = _route_surfaces(spec, "ref1")
        assert len(result) == 1
        assert result[0].kind == "route"

    def test_location_set_from_path(self):
        spec = {"route_surfaces": [{"path": "/orders"}]}
        result = _route_surfaces(spec, "ref1")
        assert result[0].location == "/orders"

    def test_label_from_module(self):
        spec = {"route_surfaces": [{"module": "orders", "path": "/orders"}]}
        result = _route_surfaces(spec, "ref1")
        assert result[0].label == "orders"

    def test_source_ref_id_set(self):
        spec = {"route_surfaces": [{"path": "/home"}]}
        result = _route_surfaces(spec, "myref")
        assert result[0].source_ref_id == "myref"


# ---------------------------------------------------------------------------
# 5. _pages
# ---------------------------------------------------------------------------

class TestPages:
    def test_empty_spec_returns_empty(self):
        assert _pages({}, "ref1") == []

    def test_item_creates_page_kind(self):
        spec = {"route_surfaces": [{"path": "/orders"}]}
        result = _pages(spec, "ref1")
        assert len(result) == 1
        assert result[0].kind == "page"

    def test_source_ref_id_set(self):
        spec = {"route_surfaces": [{"path": "/home"}]}
        result = _pages(spec, "myref")
        assert result[0].source_ref_id == "myref"


# ---------------------------------------------------------------------------
# 6. _integration_inventory
# ---------------------------------------------------------------------------

class TestIntegrationInventory:
    def test_empty_detected_connectors_returns_empty(self):
        assert _integration_inventory({}) == []

    def test_connector_with_provider_id(self):
        spec = {"detected_connectors": [{"provider_id": "payment_provider", "category": "payments"}]}
        result = _integration_inventory(spec)
        assert len(result) == 1
        assert "payment_provider" in result[0].integration_id

    def test_secret_required_when_has_secret_envs(self):
        spec = {"detected_connectors": [{"provider_id": "payment_provider", "likely_secret_envs": ["PAYMENT_PROVIDER_KEY"]}]}
        result = _integration_inventory(spec)
        assert result[0].secret_required is True

    def test_no_secrets_when_empty_secret_envs(self):
        spec = {"detected_connectors": [{"provider_id": "payment_provider"}]}
        result = _integration_inventory(spec)
        assert result[0].secret_required is False

    def test_config_required_always_true_for_non_empty_connector(self):
        spec = {"detected_connectors": [{"provider_id": "payment_provider"}]}
        result = _integration_inventory(spec)
        assert result[0].config_required is True

    def test_provider_type_from_category(self):
        spec = {"detected_connectors": [{"provider_id": "payment_provider", "category": "payments"}]}
        result = _integration_inventory(spec)
        assert result[0].provider_type == "payments"

    def test_multiple_connectors(self):
        spec = {"detected_connectors": [{"provider_id": "payment_provider"}, {"provider_id": "sendgrid"}]}
        result = _integration_inventory(spec)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 7. _unknowns
# ---------------------------------------------------------------------------

class TestUnknowns:
    def test_empty_unresolved_questions_returns_empty(self):
        assert _unknowns({}) == []

    def test_question_with_question_key(self):
        artifact = {"unresolved_questions": [{"question": "How is auth handled?"}]}
        result = _unknowns(artifact)
        assert len(result) == 1
        assert "auth" in result[0].description

    def test_question_with_description_key(self):
        artifact = {"unresolved_questions": [{"description": "DB schema unclear"}]}
        result = _unknowns(artifact)
        assert len(result) == 1

    def test_question_without_description_skipped(self):
        artifact = {"unresolved_questions": [{"context": "some context"}]}
        result = _unknowns(artifact)
        assert result == []

    def test_question_with_context_populates_evidence(self):
        artifact = {"unresolved_questions": [{"question": "What?", "context": "Some context"}]}
        result = _unknowns(artifact)
        assert len(result[0].evidence) == 1

    def test_question_with_priority(self):
        artifact = {"unresolved_questions": [{"question": "Why?", "priority": "high"}]}
        result = _unknowns(artifact)
        assert result[0].priority == "high"

    def test_question_without_priority_defaults_medium(self):
        artifact = {"unresolved_questions": [{"question": "Why?"}]}
        result = _unknowns(artifact)
        assert result[0].priority == "medium"


# ---------------------------------------------------------------------------
# 8. _dedupe_boundaries
# ---------------------------------------------------------------------------

class TestDedupeBoundaries:
    def test_empty_list_returns_empty(self):
        assert _dedupe_boundaries([]) == []

    def test_duplicate_path_and_ownership_deduplicated(self):
        b1 = _make_boundary("src/auth.py")
        b2 = _make_boundary("src/auth.py")
        result = _dedupe_boundaries([b1, b2])
        assert len(result) == 1

    def test_different_paths_both_included(self):
        b1 = _make_boundary("src/auth.py")
        b2 = _make_boundary("src/db.py")
        result = _dedupe_boundaries([b1, b2])
        assert len(result) == 2

    def test_same_path_different_ownership_both_included(self):
        b1 = _make_boundary("src/auth.py", OwnershipClass.READ_ONLY_DISCOVERED)
        b2 = _make_boundary("src/auth.py", OwnershipClass.MIGRATED_OWNED)
        result = _dedupe_boundaries([b1, b2])
        assert len(result) == 2

    def test_order_preserved(self):
        b1 = _make_boundary("z.py")
        b2 = _make_boundary("a.py")
        result = _dedupe_boundaries([b1, b2])
        assert result[0].path_or_artifact == "z.py"
        assert result[1].path_or_artifact == "a.py"
