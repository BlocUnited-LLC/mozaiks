"""UI/Theme Portability Contract Tests — v1.

Covers the three portability tiers (MOZAIKS_OSS_SOFTWARE_DESIGN.md §7):

  SCHEMA_NATIVE        preferred for reusable/community UI
  SEMANTIC_TOKEN_REACT conditionally portable; must use --mz-* semantic tokens
  ARBITRARY_CUSTOM_REACT  supported escape hatch with weaker portability guarantees

Tests in this file are deterministic and offline — no browser, no LLM, no network.

Sections:
  1. Schema-Native Community UI — fixture pack materializes, route manifest valid,
     page schema valid, scanner accepts, provenance records UI files.
  2. Semantic-Token Enforcement — hardcoded colors/fonts rejected in pack React.
  3. Arbitrary React Escape Hatch — coexists with schema-native, scanner accepts,
     weaker portability explicitly documented.
  4. Brand Intent Classification — declared in AppBuildPlan, referenced by agents.
  5. Bundle Scanner UI Validation — route manifest and page schema structural checks.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
GREETINGS_PACK = REPO_ROOT / "tests" / "fixtures" / "community_packs" / "greetings"
BUILD_CONTEXT = REPO_ROOT / "factory_app" / "build_context"
APPGEN_WORKFLOWS = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator"


def _load_resolver():
    file_path = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools" / "resolve_managed_capability_templates.py"
    spec = importlib.util.spec_from_file_location("tests.resolver", file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_scanner():
    file_path = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools" / "generated_bundle_scanner.py"
    spec = importlib.util.spec_from_file_location("tests.scanner", file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_ui_contract():
    file_path = REPO_ROOT / "factory_app" / "workflows" / "_shared" / "generated_ui_contract.py"
    spec = importlib.util.spec_from_file_location("tests.ui_contract", file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pack_descriptor(pack_id: str, pack_root: Path, capability_source: str = "generated_module") -> dict[str, Any]:
    return {
        "id": pack_id,
        "capability_source": capability_source,
        "pack_source_path": str(pack_root),
    }


# ---------------------------------------------------------------------------
# 1. Schema-Native Community UI
# ---------------------------------------------------------------------------

def test_greetings_pack_has_schema_native_page_yaml() -> None:
    """The greetings fixture pack must ship a schema-native page YAML."""
    page_yaml = GREETINGS_PACK / "templates" / "ui" / "pages" / "greetings.yaml"
    assert page_yaml.exists(), "greetings pack missing ui/pages/greetings.yaml"
    schema = yaml.safe_load(page_yaml.read_text(encoding="utf-8"))
    assert schema.get("name") == "greetings"
    assert schema.get("route") == "/greetings"
    assert isinstance(schema.get("sections"), list) and len(schema["sections"]) > 0


def test_greetings_pack_page_uses_canonical_primitives() -> None:
    """Schema-native page must only use registered primitive types."""
    page_yaml = GREETINGS_PACK / "templates" / "ui" / "pages" / "greetings.yaml"
    schema = yaml.safe_load(page_yaml.read_text(encoding="utf-8"))

    # Canonical primitives from PrimitiveRegistry
    canonical = {
        "DataTable", "Form", "Grid", "Button", "Modal", "Alert", "Skeleton",
        "Empty", "Timeline", "CodeBlock", "ProgressTracker", "AlertBanner",
        "ActionButton", "FileList", "PageHeader", "PricingCatalog", "ResourceTable",
        "SummaryStrip", "InlineEmptyState", "LoadingState", "ErrorState", "Panel",
        "SurfaceCard", "StatusPill", "Metric", "SegmentedBar",
    }
    for section in schema.get("sections", []):
        primitive = section.get("primitive", "")
        assert primitive in canonical, (
            f"greetings page uses unknown primitive '{primitive}'. "
            f"Only canonical primitives may be used in schema-native community UI."
        )


def test_greetings_pack_has_route_manifest() -> None:
    """The greetings fixture pack must ship a route_manifest.json."""
    manifest_path = GREETINGS_PACK / "templates" / "ui" / "route_manifest.json"
    assert manifest_path.exists(), "greetings pack missing ui/route_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pages = manifest.get("pages", [])
    assert len(pages) >= 1
    greetings_page = next((p for p in pages if p.get("id") == "greetings"), None)
    assert greetings_page is not None
    assert greetings_page["path"] == "/greetings"
    assert greetings_page["component"] == "SchemaPage"


def test_greetings_pack_schema_native_page_materializes_from_pack() -> None:
    """Materializing the greetings pack must produce the schema-native page and route manifest."""
    resolver = _load_resolver()
    descriptor = _pack_descriptor("greetings", GREETINGS_PACK)
    all_files = resolver.resolve_managed_capability_templates([descriptor])
    filenames = {f["filename"] for f in all_files}

    assert "ui/pages/greetings.yaml" in filenames, (
        "greetings pack materialization must include ui/pages/greetings.yaml"
    )
    assert "ui/route_manifest.json" in filenames, (
        "greetings pack materialization must include ui/route_manifest.json"
    )


def test_greetings_pack_ui_files_appear_in_provenance() -> None:
    """Provenance manifest must record UI files produced by the greetings pack."""
    resolver = _load_resolver()
    descriptor = _pack_descriptor("greetings", GREETINGS_PACK)
    all_files = resolver.resolve_managed_capability_templates([descriptor])

    provenance_file = next(
        (f for f in all_files if f["filename"] == ".mozaiks/pack_provenance.json"), None
    )
    assert provenance_file is not None, "Provenance manifest missing after materialization"

    provenance = json.loads(provenance_file["content"])
    pack_entry = next(
        (p for p in provenance.get("packs", []) if p.get("pack_id") == "greetings"), None
    )
    assert pack_entry is not None

    pack_file_paths = {f["path"] for f in pack_entry.get("materialized_owned_files", [])}
    assert "ui/pages/greetings.yaml" in pack_file_paths, (
        "provenance does not record ui/pages/greetings.yaml"
    )
    assert "ui/route_manifest.json" in pack_file_paths, (
        "provenance does not record ui/route_manifest.json"
    )


def test_greetings_pack_ui_files_have_templates_owner_in_provenance() -> None:
    """UI files from the greetings pack must be recorded with owner=templates."""
    resolver = _load_resolver()
    descriptor = _pack_descriptor("greetings", GREETINGS_PACK)
    all_files = resolver.resolve_managed_capability_templates([descriptor])

    provenance_file = next(
        (f for f in all_files if f["filename"] == ".mozaiks/pack_provenance.json"), None
    )
    assert provenance_file is not None
    provenance = json.loads(provenance_file["content"])
    pack_entry = next(
        (p for p in provenance.get("packs", []) if p.get("pack_id") == "greetings"), None
    )
    assert pack_entry is not None

    owner_by_path = {f["path"]: f.get("owner") for f in pack_entry.get("materialized_owned_files", [])}
    assert owner_by_path.get("ui/pages/greetings.yaml") == "templates"
    assert owner_by_path.get("ui/route_manifest.json") == "templates"


def test_greetings_pack_materialized_bundle_passes_scanner() -> None:
    """The scanner must accept the greetings pack materialized bundle (no errors)."""
    resolver = _load_resolver()
    scanner = _load_scanner()

    descriptor = _pack_descriptor("greetings", GREETINGS_PACK)
    all_files = resolver.resolve_managed_capability_templates([descriptor])
    files_map = {f["filename"]: f["content"] for f in all_files}

    errors = scanner.scan_generated_bundle(files_map)
    assert errors == [], f"Scanner found errors in greetings pack bundle: {errors}"


def test_schema_native_page_inherits_theme_via_primitive_renderer() -> None:
    """Schema-native pages are rendered by PageRenderer/PrimitiveRegistry which applies
    --mz-* semantic tokens. The page YAML itself must NOT hardcode colors or fonts."""
    page_yaml = GREETINGS_PACK / "templates" / "ui" / "pages" / "greetings.yaml"
    content = page_yaml.read_text(encoding="utf-8")

    import re
    hex_or_color = re.compile(r"#[0-9A-Fa-f]{3,8}\b|(?<![\w-])(?:rgb|rgba|hsl|hsla)\(")
    font_declaration = re.compile(r"fontFamily|font-family|@font-face")

    assert not hex_or_color.search(content), (
        "Schema-native page YAML must not hardcode color values. "
        "Theme is inherited via PrimitiveRegistry → --mz-* tokens."
    )
    assert not font_declaration.search(content), (
        "Schema-native page YAML must not declare fonts. "
        "Typography is inherited via theme_config.json → --mz-font-* tokens."
    )


# ---------------------------------------------------------------------------
# 2. Semantic-Token React Enforcement in Pack Templates
# ---------------------------------------------------------------------------

def test_semantic_token_audit_rejects_hardcoded_color_in_pack_react() -> None:
    """audit_generated_react_files must reject hardcoded colors in pack template React."""
    ui_contract = _load_ui_contract()
    warnings = ui_contract.audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/GreetingsPage.jsx",
                "content": (
                    "export default function GreetingsPage() {\n"
                    "  return <div style={{ color: '#00bcd4', fontFamily: 'Rajdhani' }}>\n"
                    "    <h1>Hello</h1>\n"
                    "  </div>;\n"
                    "}\n"
                ),
            }
        ],
        source_label="pack template React",
        require_jsx=True,
    )
    assert any("hardcodes color values" in w or "color" in w.lower() for w in warnings), (
        "audit must reject hardcoded color '#00bcd4' in pack React template"
    )
    assert any("font" in w.lower() for w in warnings), (
        "audit must reject literal font declaration in pack React template"
    )


def test_semantic_token_audit_accepts_semantic_tokens_in_pack_react() -> None:
    """audit_generated_react_files must accept semantic --mz-* token classes."""
    ui_contract = _load_ui_contract()
    warnings = ui_contract.audit_generated_react_files(
        [
            {
                "filename": "ui/pages/custom/GreetingsPage.jsx",
                "content": (
                    "import { SurfaceCard, StatusPill } from '@mozaiks/chat-ui/ui';\n"
                    "export default function GreetingsPage() {\n"
                    "  return (\n"
                    "    <SurfaceCard>\n"
                    "      <h1 className=\"text-foreground font-heading\">Hello</h1>\n"
                    "      <StatusPill label=\"Active\" tone=\"success\" />\n"
                    "    </SurfaceCard>\n"
                    "  );\n"
                    "}\n"
                ),
            }
        ],
        source_label="pack template React",
        require_jsx=True,
    )
    color_violations = [w for w in warnings if "hardcodes color" in w]
    font_violations = [w for w in warnings if "literal brand font" in w]
    assert color_violations == [], f"Unexpected color violations: {color_violations}"
    assert font_violations == [], f"Unexpected font violations: {font_violations}"


# ---------------------------------------------------------------------------
# 3. Arbitrary React Escape Hatch — Coexistence + Documented Tier
# ---------------------------------------------------------------------------

def test_custom_react_coexists_with_schema_native_in_scanner() -> None:
    """A bundle with both schema-native pages AND custom React pages must pass the scanner.

    ARBITRARY_CUSTOM_REACT is a supported escape hatch. The scanner must not
    reject it. Its portability guarantee is weaker: theming is the developer's
    responsibility, not automatically inherited.
    """
    scanner = _load_scanner()

    files_map = {
        # Schema-native page
        "ui/pages/greetings.yaml": (
            "schema_version: mozaiks.app_page.v1\n"
            "name: greetings\n"
            "route: /greetings\n"
            "title: Greetings\n"
            "page_type: record_list\n"
            "layout: full-width\n"
            "sections:\n"
            "  - id: header\n"
            "    primitive: PageHeader\n"
            "    config:\n"
            "      title: Greetings\n"
        ),
        # Custom React escape hatch page (arbitrary React)
        "ui/pages/custom/SpecialPage.jsx": (
            "import { SurfaceCard } from '@mozaiks/chat-ui/ui';\n"
            "export default function SpecialPage() {\n"
            "  return <SurfaceCard><h1 className=\"text-foreground\">Special</h1></SurfaceCard>;\n"
            "}\n"
        ),
        # Route manifest referencing both
        "ui/route_manifest.json": json.dumps({
            "pages": [
                {"id": "greetings", "path": "/greetings", "component": "SchemaPage"},
                {"id": "special", "path": "/special", "component": "SpecialPage"},
            ]
        }),
    }

    errors = scanner.scan_generated_bundle(files_map)
    assert errors == [], f"Scanner rejected valid custom React + schema-native coexistence: {errors}"


def test_custom_react_pages_excluded_from_schema_validation() -> None:
    """Custom React pages under ui/pages/custom/ are NOT subject to page schema checks.

    ARBITRARY_CUSTOM_REACT has weaker portability guarantees; the scanner
    must not enforce the schema-native contract on JSX files.
    """
    scanner = _load_scanner()

    files_map = {
        # Custom React page — JSX, not YAML, so schema scan doesn't apply
        "ui/pages/custom/WeirdLayout.jsx": (
            "export default function WeirdLayout() { return <div>Custom layout</div>; }\n"
        ),
    }
    # No errors expected — the schema scanner only applies to .yaml files
    errors = scanner.scan_generated_bundle(files_map)
    assert errors == [], f"Scanner wrongly flagged custom React JSX: {errors}"


# ---------------------------------------------------------------------------
# 4. Brand Intent Classification
# ---------------------------------------------------------------------------

def test_brand_intent_declared_in_appbuildplan_structured_output() -> None:
    """brand_intent must be a declared field in the AppBuildPlan structured output model."""
    structured_outputs = yaml.safe_load(
        (APPGEN_WORKFLOWS / "structured_outputs.yaml").read_text(encoding="utf-8")
    )
    models = structured_outputs.get("models", {})
    app_build_plan = models.get("AppBuildPlan", {})
    fields = app_build_plan.get("fields", {})
    assert "brand_intent" in fields, (
        "brand_intent must be declared in the AppBuildPlan model in structured_outputs.yaml. "
        "It carries the structured brand/experience direction from upstream planning workflows."
    )


def test_brand_intent_referenced_by_appschema_agent() -> None:
    """AppSchemaAgent prompt must reference brand_intent as a theme derivation input."""
    agents = (APPGEN_WORKFLOWS / "agents.yaml").read_text(encoding="utf-8")
    assert "brand_intent" in agents, (
        "AppSchemaAgent prompt must reference brand_intent. "
        "It is the structured brand/experience direction that drives theme_config_patch derivation."
    )


def test_theme_preferences_declared_in_appbuildplan() -> None:
    """theme_preferences (raw user style text) must be a declared field in AppBuildPlan."""
    structured_outputs = yaml.safe_load(
        (APPGEN_WORKFLOWS / "structured_outputs.yaml").read_text(encoding="utf-8")
    )
    models = structured_outputs.get("models", {})
    app_build_plan = models.get("AppBuildPlan", {})
    fields = app_build_plan.get("fields", {})
    assert "theme_preferences" in fields, (
        "theme_preferences must be declared in AppBuildPlan. "
        "It carries raw user-stated style preferences like 'dark, minimal'."
    )


# ---------------------------------------------------------------------------
# 5. Bundle Scanner UI Validation
# ---------------------------------------------------------------------------

def test_scanner_accepts_valid_route_manifest() -> None:
    scanner = _load_scanner()
    files_map = {
        "ui/route_manifest.json": json.dumps({
            "pages": [
                {"id": "home", "path": "/", "component": "SchemaPage"},
                {"id": "profile", "path": "/me", "component": "ProfilePage"},
            ]
        })
    }
    errors = scanner.scan_generated_bundle(files_map)
    assert errors == [], f"Valid route manifest should not cause scanner errors: {errors}"


def test_scanner_rejects_route_manifest_with_missing_path() -> None:
    scanner = _load_scanner()
    files_map = {
        "ui/route_manifest.json": json.dumps({
            "pages": [
                {"id": "home", "component": "SchemaPage"},  # missing 'path'
            ]
        })
    }
    errors = scanner.scan_generated_bundle(files_map)
    assert any("path" in e for e in errors), (
        f"Scanner must reject route manifest page missing 'path': {errors}"
    )


def test_scanner_rejects_route_manifest_with_invalid_path() -> None:
    scanner = _load_scanner()
    files_map = {
        "ui/route_manifest.json": json.dumps({
            "pages": [
                {"id": "home", "path": "no-leading-slash", "component": "SchemaPage"},
            ]
        })
    }
    errors = scanner.scan_generated_bundle(files_map)
    assert any("path" in e for e in errors), (
        f"Scanner must reject route path not starting with '/': {errors}"
    )


def test_scanner_rejects_route_manifest_with_missing_component() -> None:
    scanner = _load_scanner()
    files_map = {
        "ui/route_manifest.json": json.dumps({
            "pages": [
                {"id": "home", "path": "/"},  # missing 'component'
            ]
        })
    }
    errors = scanner.scan_generated_bundle(files_map)
    assert any("component" in e for e in errors), (
        f"Scanner must reject route manifest page missing 'component': {errors}"
    )


def test_scanner_accepts_valid_page_schema() -> None:
    scanner = _load_scanner()
    files_map = {
        "ui/pages/hello.yaml": (
            "schema_version: mozaiks.app_page.v1\n"
            "name: hello\n"
            "route: /hello\n"
            "title: Hello\n"
            "page_type: record_list\n"
            "layout: full-width\n"
            "sections:\n"
            "  - id: header\n"
            "    primitive: PageHeader\n"
            "    config:\n"
            "      title: Hello\n"
        )
    }
    errors = scanner.scan_generated_bundle(files_map)
    assert errors == [], f"Valid page schema should not cause scanner errors: {errors}"


def test_scanner_rejects_page_schema_missing_name() -> None:
    scanner = _load_scanner()
    files_map = {
        "ui/pages/hello.yaml": (
            "route: /hello\n"
            "sections:\n"
            "  - id: s1\n"
            "    primitive: DataTable\n"
        )
    }
    errors = scanner.scan_generated_bundle(files_map)
    assert any("name" in e for e in errors), (
        f"Scanner must reject page schema missing 'name': {errors}"
    )


def test_scanner_rejects_page_schema_missing_sections() -> None:
    scanner = _load_scanner()
    files_map = {
        "ui/pages/hello.yaml": "name: hello\nroute: /hello\n"
    }
    errors = scanner.scan_generated_bundle(files_map)
    assert any("sections" in e for e in errors), (
        f"Scanner must reject page schema missing 'sections': {errors}"
    )


def test_scanner_skips_schema_validation_for_custom_react_pages() -> None:
    """Custom React pages under ui/pages/custom/ must not be subject to YAML schema checks."""
    scanner = _load_scanner()
    files_map = {
        "ui/pages/custom/DashboardPage.jsx": (
            "export default function DashboardPage() { return <div>Dashboard</div>; }\n"
        )
    }
    errors = scanner.scan_generated_bundle(files_map)
    # JSX under custom/ should not trigger page schema validation errors
    schema_errors = [e for e in errors if "schema-native page" in e]
    assert schema_errors == [], (
        f"Scanner wrongly applied schema-native validation to custom React JSX: {schema_errors}"
    )
