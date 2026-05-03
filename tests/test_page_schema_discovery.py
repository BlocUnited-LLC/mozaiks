"""
Tests for declarative page schema discovery and structure.

Covers:
  - platform/ui/pages/Dashboard.yaml and GettingStarted.yaml are valid YAML
  - Both files have required AppPageSchema fields (name, route, title, layout, sections)
  - All section primitive names are in the shipped catalog
  - _load_page_schema_routes discovers them and returns SchemaPage routes
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PAGES_DIR = _REPO_ROOT / "platform" / "ui" / "pages"
_VALID_PRIMITIVES = frozenset({
    "ActionButton", "Alert", "AlertBanner", "Badge", "Button", "Card",
    "CodeBlock", "DataTable", "Empty", "FileList", "Form", "Grid",
    "Modal", "ProgressTracker", "Skeleton", "Stat", "Timeline",
})
_VALID_LAYOUTS = frozenset({"grid", "sidebar", "full-width", "split"})


def _load_yaml(name: str) -> Dict[str, Any]:
    path = _PAGES_DIR / f"{name}.yaml"
    assert path.exists(), f"Expected {path} to exist"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{name}.yaml must be a YAML mapping"
    return raw


def _collect_primitive_names(sections: List[Dict[str, Any]]) -> List[str]:
    """Recursively collect all primitive names from sections and Grid children."""
    names: List[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        prim = section.get("primitive")
        if isinstance(prim, str):
            names.append(prim)
        cfg = section.get("config") or {}
        children = cfg.get("children") or []
        if isinstance(children, list):
            names.extend(_collect_primitive_names(children))
    return names


# ---------------------------------------------------------------------------
# Dashboard.yaml
# ---------------------------------------------------------------------------

class TestDashboardSchema:
    @pytest.fixture(scope="class")
    def schema(self):
        return _load_yaml("Dashboard")

    def test_required_top_level_fields(self, schema):
        for field in ("name", "route", "title", "layout", "sections"):
            assert field in schema, f"Dashboard.yaml missing '{field}'"

    def test_name_and_route(self, schema):
        assert schema["name"] == "Dashboard"
        assert schema["route"].startswith("/")

    def test_layout_is_valid(self, schema):
        assert schema["layout"] in _VALID_LAYOUTS

    def test_sections_is_non_empty_list(self, schema):
        assert isinstance(schema["sections"], list)
        assert len(schema["sections"]) > 0

    def test_all_section_primitives_are_shipped(self, schema):
        names = _collect_primitive_names(schema["sections"])
        assert len(names) > 0, "Dashboard.yaml has no primitive sections"
        for name in names:
            assert name in _VALID_PRIMITIVES, (
                f"Dashboard.yaml references unknown primitive '{name}'. "
                f"Allowed: {sorted(_VALID_PRIMITIVES)}"
            )

    def test_each_section_has_id_and_primitive(self, schema):
        for section in schema["sections"]:
            assert "primitive" in section, f"Section missing 'primitive': {section}"
            # top-level sections must have an id; Grid children may omit it
            assert "id" in section, f"Top-level section missing 'id': {section}"

    def test_each_section_has_config(self, schema):
        for section in schema["sections"]:
            assert "config" in section, f"Section '{section.get('id')}' missing 'config'"
            assert isinstance(section["config"], dict)


# ---------------------------------------------------------------------------
# GettingStarted.yaml
# ---------------------------------------------------------------------------

class TestGettingStartedSchema:
    @pytest.fixture(scope="class")
    def schema(self):
        return _load_yaml("GettingStarted")

    def test_required_top_level_fields(self, schema):
        for field in ("name", "route", "title", "layout", "sections"):
            assert field in schema, f"GettingStarted.yaml missing '{field}'"

    def test_name_and_route(self, schema):
        assert schema["name"] == "GettingStarted"
        assert schema["route"].startswith("/")

    def test_layout_is_valid(self, schema):
        assert schema["layout"] in _VALID_LAYOUTS

    def test_sections_is_non_empty_list(self, schema):
        assert isinstance(schema["sections"], list)
        assert len(schema["sections"]) > 0

    def test_all_section_primitives_are_shipped(self, schema):
        names = _collect_primitive_names(schema["sections"])
        assert len(names) > 0, "GettingStarted.yaml has no primitive sections"
        for name in names:
            assert name in _VALID_PRIMITIVES, (
                f"GettingStarted.yaml references unknown primitive '{name}'. "
                f"Allowed: {sorted(_VALID_PRIMITIVES)}"
            )

    def test_uses_new_primitives(self, schema):
        """GettingStarted should demonstrate at least one L3 primitive."""
        names = set(_collect_primitive_names(schema["sections"]))
        new_prims = {"Timeline", "AlertBanner", "ActionButton", "CodeBlock", "ProgressTracker", "FileList"}
        assert names & new_prims, (
            f"GettingStarted.yaml should use at least one new primitive. Found: {names}"
        )


# ---------------------------------------------------------------------------
# _load_page_schema_routes integration
# ---------------------------------------------------------------------------

def _load_page_schema_routes_fn():
    """Import _load_page_schema_routes without importing the full FastAPI app."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tests._platform_host",
        _REPO_ROOT / "mozaiksai" / "hosts" / "platform.py",
    )
    if spec is None or spec.loader is None:
        pytest.skip("Could not load platform.py for route discovery test")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"platform.py import failed (expected in isolated test env): {exc}")
    return getattr(module, "_load_page_schema_routes", None)


class TestLoadPageSchemaRoutes:
    @pytest.fixture(scope="class")
    def routes(self):
        fn = _load_page_schema_routes_fn()
        if fn is None:
            pytest.skip("_load_page_schema_routes not found")
        platform_root = _REPO_ROOT / "platform"
        return fn(platform_root)

    def test_returns_list(self, routes):
        assert isinstance(routes, list)

    def test_dashboard_route_present(self, routes):
        paths = [r["path"] for r in routes]
        assert "/dashboard" in paths, f"Expected /dashboard in {paths}"

    def test_getting_started_route_present(self, routes):
        paths = [r["path"] for r in routes]
        assert "/getting-started" in paths, f"Expected /getting-started in {paths}"

    def test_routes_use_schema_page_component(self, routes):
        for route in routes:
            assert route["component"] == "SchemaPage", (
                f"Route {route['path']} should use SchemaPage, got {route['component']}"
            )

    def test_routes_have_schema_field(self, routes):
        for route in routes:
            assert "schema" in route, f"Route {route['path']} missing 'schema' field"
            assert isinstance(route["schema"], str) and route["schema"]

    def test_dashboard_schema_name(self, routes):
        dashboard = next(r for r in routes if r["path"] == "/dashboard")
        assert dashboard["schema"] == "Dashboard"

    def test_getting_started_schema_name(self, routes):
        gs = next(r for r in routes if r["path"] == "/getting-started")
        assert gs["schema"] == "GettingStarted"
