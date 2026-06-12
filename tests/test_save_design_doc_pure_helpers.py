"""
Pure helper unit tests for:
  factory_app/workflows/DesignDocs/tools/save_design_doc.py

Covers:

  _normalize_kind:
    - valid kinds returned: "frontend", "backend", "database", "ui_schema"
    - leading/trailing whitespace stripped before comparison
    - case insensitive (uppercase input normalized)
    - invalid kind → None
    - non-string → None
    - empty string → None

  _canonical_surface_map:
    - non-dict input → raises ValueError
    - no "surfaces" key → raises ValueError with "surfaces"
    - empty surfaces list → raises ValueError
    - non-list surfaces → raises ValueError
    - valid surfaces → returns {"surfaces": surfaces}
    - surfaces with non-dict items → raises ValueError
    - surface_id and surface_kind preserved in returned surfaces

  _inject_backend_surface_map:
    - appends ## Surface Realization Map block to markdown without existing section
    - replaces existing ## Surface Realization Map section in markdown
    - empty backend_markdown → raises ValueError
    - block contains yaml fence
    - block contains surface_map YAML content

  _canonical_experience_spec:
    - non-dict input → raises ValueError
    - missing navigation_model → raises ValueError
    - empty navigation_model → raises ValueError
    - missing pages → raises ValueError
    - empty pages list → raises ValueError
    - page with non-dict item → raises ValueError
    - page missing name → raises ValueError
    - page missing route → raises ValueError
    - page missing sections → raises ValueError
    - page with empty sections → raises ValueError
    - section missing primitive → raises ValueError
    - section missing intent → raises ValueError
    - valid spec → returns canonical dict with navigation_model, brand_direction, pages
    - brand_direction defaults to empty string when absent
"""
from __future__ import annotations

import pytest

from factory_app.workflows.DesignDocs.tools.save_design_doc import (
    _canonical_experience_spec,
    _canonical_surface_map,
    _inject_backend_surface_map,
    _normalize_kind,
)

# ---------------------------------------------------------------------------
# 1. _normalize_kind
# ---------------------------------------------------------------------------

class TestNormalizeKind:
    def test_frontend_valid(self):
        assert _normalize_kind("frontend") == "frontend"

    def test_backend_valid(self):
        assert _normalize_kind("backend") == "backend"

    def test_database_valid(self):
        assert _normalize_kind("database") == "database"

    def test_ui_schema_valid(self):
        assert _normalize_kind("ui_schema") == "ui_schema"

    def test_whitespace_stripped(self):
        assert _normalize_kind("  frontend  ") == "frontend"

    def test_uppercase_normalized(self):
        assert _normalize_kind("FRONTEND") == "frontend"

    def test_invalid_kind_returns_none(self):
        assert _normalize_kind("invalid") is None

    def test_non_string_returns_none(self):
        assert _normalize_kind(None) is None  # type: ignore
        assert _normalize_kind(42) is None  # type: ignore

    def test_empty_string_returns_none(self):
        assert _normalize_kind("") is None


# ---------------------------------------------------------------------------
# 2. _canonical_surface_map
# ---------------------------------------------------------------------------

class TestCanonicalSurfaceMap:
    def _valid_surface(self, surface_id: str = "s1", kind: str = "page") -> dict:
        return {"surface_id": surface_id, "surface_kind": kind}

    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            _canonical_surface_map("not-a-dict")

    def test_no_surfaces_key_raises(self):
        with pytest.raises(ValueError, match="surfaces"):
            _canonical_surface_map({"other": []})

    def test_empty_surfaces_raises(self):
        with pytest.raises(ValueError):
            _canonical_surface_map({"surfaces": []})

    def test_non_list_surfaces_raises(self):
        with pytest.raises(ValueError):
            _canonical_surface_map({"surfaces": "not-a-list"})

    def test_valid_surfaces_returned(self):
        raw = {"surfaces": [self._valid_surface()]}
        result = _canonical_surface_map(raw)
        assert result == {"surfaces": [self._valid_surface()]}

    def test_non_dict_surface_item_raises(self):
        with pytest.raises(ValueError):
            _canonical_surface_map({"surfaces": ["not-a-dict"]})

    def test_multiple_valid_surfaces(self):
        raw = {
            "surfaces": [
                self._valid_surface("s1", "page"),
                self._valid_surface("s2", "workflow"),
            ]
        }
        result = _canonical_surface_map(raw)
        assert len(result["surfaces"]) == 2


# ---------------------------------------------------------------------------
# 3. _inject_backend_surface_map
# ---------------------------------------------------------------------------

def _minimal_surface_map() -> dict:
    return {"surfaces": [{"surface_id": "s1", "surface_kind": "page"}]}


class TestInjectBackendSurfaceMap:
    def test_appends_section_when_absent(self):
        doc = "# Backend Design\n\nSome content here."
        result = _inject_backend_surface_map(doc, _minimal_surface_map())
        assert "## Surface Realization Map" in result
        assert "Some content here." in result

    def test_block_contains_yaml_fence(self):
        result = _inject_backend_surface_map("# Doc\n\nContent", _minimal_surface_map())
        assert "```yaml" in result
        assert "```" in result

    def test_block_contains_surface_map_content(self):
        result = _inject_backend_surface_map("# Doc\n\nContent", _minimal_surface_map())
        assert "surface_map" in result

    def test_replaces_existing_section(self):
        old_block = "## Surface Realization Map\n\n```yaml\nsurface_map:\n  surfaces: []\n```"
        doc = f"# Doc\n\nContent\n\n{old_block}"
        new_map = {"surfaces": [{"surface_id": "new_s1", "surface_kind": "workflow"}]}
        result = _inject_backend_surface_map(doc, new_map)
        # Only one Surface Realization Map section
        assert result.count("## Surface Realization Map") == 1
        assert "new_s1" in result

    def test_empty_markdown_raises(self):
        with pytest.raises(ValueError):
            _inject_backend_surface_map("", _minimal_surface_map())

    def test_whitespace_only_markdown_raises(self):
        with pytest.raises(ValueError):
            _inject_backend_surface_map("   ", _minimal_surface_map())

    def test_section_appended_at_end(self):
        doc = "# Header\n\nBody text."
        result = _inject_backend_surface_map(doc, _minimal_surface_map())
        idx = result.rfind("## Surface Realization Map")
        assert idx > result.rfind("Body text.")


# ---------------------------------------------------------------------------
# 4. _canonical_experience_spec
# ---------------------------------------------------------------------------

def _valid_page(name: str = "Dashboard", route: str = "/dashboard") -> dict:
    return {
        "name": name,
        "route": route,
        "sections": [
            {"primitive": "StatGrid", "intent": "Show KPIs"}
        ]
    }


def _valid_spec(**overrides) -> dict:
    base = {
        "navigation_model": "sidebar",
        "brand_direction": "modern",
        "pages": [_valid_page()],
    }
    base.update(overrides)
    return base


class TestCanonicalExperienceSpec:
    def _surface_map(self) -> dict:
        return {"surfaces": [{"surface_id": "s1", "surface_kind": "page"}]}

    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            _canonical_experience_spec("not-a-dict", surface_map=self._surface_map())

    def test_missing_navigation_model_raises(self):
        spec = {"pages": [_valid_page()]}
        with pytest.raises(ValueError, match="navigation_model"):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_empty_navigation_model_raises(self):
        spec = {"navigation_model": "", "pages": [_valid_page()]}
        with pytest.raises(ValueError, match="navigation_model"):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_missing_pages_raises(self):
        spec = {"navigation_model": "sidebar"}
        with pytest.raises(ValueError, match="pages"):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_empty_pages_raises(self):
        spec = {"navigation_model": "sidebar", "pages": []}
        with pytest.raises(ValueError, match="pages"):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_non_dict_page_raises(self):
        spec = {"navigation_model": "sidebar", "pages": ["not-a-dict"]}
        with pytest.raises(ValueError):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_page_missing_name_raises(self):
        page = {"route": "/home", "sections": [{"primitive": "X", "intent": "y"}]}
        spec = {"navigation_model": "sidebar", "pages": [page]}
        with pytest.raises(ValueError):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_page_missing_route_raises(self):
        page = {"name": "Home", "sections": [{"primitive": "X", "intent": "y"}]}
        spec = {"navigation_model": "sidebar", "pages": [page]}
        with pytest.raises(ValueError):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_page_missing_sections_raises(self):
        page = {"name": "Home", "route": "/home"}
        spec = {"navigation_model": "sidebar", "pages": [page]}
        with pytest.raises(ValueError, match="sections"):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_page_empty_sections_raises(self):
        page = {"name": "Home", "route": "/home", "sections": []}
        spec = {"navigation_model": "sidebar", "pages": [page]}
        with pytest.raises(ValueError, match="sections"):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_section_missing_primitive_raises(self):
        page = {"name": "Home", "route": "/home", "sections": [{"intent": "x"}]}
        spec = {"navigation_model": "sidebar", "pages": [page]}
        with pytest.raises(ValueError, match="primitive"):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_section_missing_intent_raises(self):
        page = {"name": "Home", "route": "/home", "sections": [{"primitive": "StatGrid"}]}
        spec = {"navigation_model": "sidebar", "pages": [page]}
        with pytest.raises(ValueError, match="intent"):
            _canonical_experience_spec(spec, surface_map=self._surface_map())

    def test_valid_spec_returns_canonical_dict(self):
        result = _canonical_experience_spec(_valid_spec(), surface_map=self._surface_map())
        assert result["navigation_model"] == "sidebar"
        assert result["brand_direction"] == "modern"
        assert len(result["pages"]) == 1

    def test_brand_direction_defaults_to_empty(self):
        spec = {"navigation_model": "top-nav", "pages": [_valid_page()]}
        result = _canonical_experience_spec(spec, surface_map=self._surface_map())
        assert result["brand_direction"] == ""

    def test_non_dict_section_raises(self):
        page = {"name": "Home", "route": "/home", "sections": ["not-a-dict"]}
        spec = {"navigation_model": "sidebar", "pages": [page]}
        with pytest.raises(ValueError):
            _canonical_experience_spec(spec, surface_map=self._surface_map())
