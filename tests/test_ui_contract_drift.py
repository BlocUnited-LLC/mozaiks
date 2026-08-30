"""UI contract drift tests.

Verifies cross-language and cross-contract alignment for:
  1.  AppPageSchema layout values vs PageRenderer.jsx LAYOUT_CLASSES
  2.  AppShellMode values vs platform _SHELL_MODE_VALUES
  3.  UIDisplayMode Python values vs uiSurfaceReducer.js normalizeDisplayMode
  4.  UI primitive names across all four sources
  5.  Page type values across structured outputs and VALID_PAGE_TYPES
  6.  Custom-route three-file closure for factory_app
  7.  AppPageSchema.extensions — whether PageRenderer.jsx reads the field
  8.  UIToolContractSpec — Pydantic model structure and validation
  9.  Page serving — GET /api/pages/{name} canonical validation owner

No production code is changed by this module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Repo root helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
STRUCTURED_OUTPUTS_YAML = (
    REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml"
)
PAGE_RENDERER_JSX = (
    REPO_ROOT / "chat-ui" / "src" / "ui" / "page-renderer" / "PageRenderer.jsx"
)
UI_SURFACE_REDUCER_JS = (
    REPO_ROOT / "chat-ui" / "src" / "state" / "uiSurfaceReducer.js"
)
PRIMITIVE_SCHEMAS_JSON = (
    REPO_ROOT / "chat-ui" / "src" / "ui" / "page-renderer" / "primitive_schemas.json"
)
SHELL_ROUTER_PY = REPO_ROOT / "mozaiksai" / "hosts" / "routers" / "shell.py"
FACTORY_ROUTE_MANIFEST = REPO_ROOT / "factory_app" / "app" / "ui" / "route_manifest.json"
FACTORY_UI_INDEX_JS = REPO_ROOT / "factory_app" / "app" / "ui" / "index.js"
FACTORY_ADMIN_INDEX_JS = REPO_ROOT / "factory_app" / "app" / "admin" / "index.js"


def _load_structured_outputs() -> dict:
    return yaml.safe_load(STRUCTURED_OUTPUTS_YAML.read_text(encoding="utf-8"))


def _so_model(name: str) -> dict:
    """Return the structured-output model dict for *name*."""
    return _load_structured_outputs()["models"][name]


def _so_top_level_literal_values(model_name: str) -> frozenset[str]:
    """Extract values from a top-level ``type: literal`` model."""
    model = _so_model(model_name)
    assert model["type"] == "literal", f"{model_name} is not a top-level literal"
    return frozenset(model["values"])


def _so_field_literal_values(model_name: str, field_name: str) -> frozenset[str]:
    """Extract values from a field's ``type: literal`` definition inside a model."""
    model = _so_model(model_name)
    field = model["fields"][field_name]
    assert field["type"] == "literal", (
        f"{model_name}.{field_name} is not a literal field"
    )
    return frozenset(field["values"])


# ---------------------------------------------------------------------------
# 1. AppPageSchema layout values vs PageRenderer.jsx LAYOUT_CLASSES
# ---------------------------------------------------------------------------

def _parse_layout_classes_from_jsx(jsx_path: Path) -> frozenset[str]:
    """Extract keys from the LAYOUT_CLASSES object in PageRenderer.jsx."""
    text = jsx_path.read_text(encoding="utf-8")
    # Grab the block:  const LAYOUT_CLASSES = { ... };
    match = re.search(r"const LAYOUT_CLASSES\s*=\s*\{([^}]+)\}", text)
    assert match, "LAYOUT_CLASSES not found in PageRenderer.jsx"
    block = match.group(1)
    # Each key is either bare identifier or quoted string.
    keys = re.findall(r"['\"]([^'\"]+)['\"](?=\s*:)", block)
    return frozenset(keys)


def test_layout_values_aligned_across_structured_output_and_renderer():
    """layout literal in AppPageSchema must exactly match LAYOUT_CLASSES keys."""
    so_values = _so_field_literal_values("AppPageSchema", "layout")
    jsx_keys = _parse_layout_classes_from_jsx(PAGE_RENDERER_JSX)

    assert so_values == jsx_keys, (
        f"Layout value drift detected.\n"
        f"  structured_outputs.yaml: {sorted(so_values)}\n"
        f"  PageRenderer.jsx LAYOUT_CLASSES: {sorted(jsx_keys)}\n"
        f"  In SO only: {sorted(so_values - jsx_keys)}\n"
        f"  In JSX only: {sorted(jsx_keys - so_values)}"
    )


# ---------------------------------------------------------------------------
# 2. AppShellMode values vs platform _SHELL_MODE_VALUES
# ---------------------------------------------------------------------------

def _get_platform_shell_mode_values() -> frozenset[str]:
    """Import _SHELL_MODE_VALUES from the platform host module."""
    import mozaiksai.hosts.platform as plat  # noqa: PLC0415
    return frozenset(plat._SHELL_MODE_VALUES)  # noqa: SLF001


def test_shell_mode_values_aligned_across_structured_output_and_platform():
    """AppShellMode literal values must exactly match _SHELL_MODE_VALUES at runtime."""
    so_values = _so_top_level_literal_values("AppShellMode")
    platform_values = _get_platform_shell_mode_values()

    assert so_values == platform_values, (
        f"Shell mode drift detected.\n"
        f"  structured_outputs.yaml AppShellMode: {sorted(so_values)}\n"
        f"  platform._SHELL_MODE_VALUES: {sorted(platform_values)}\n"
        f"  In SO only: {sorted(so_values - platform_values)}\n"
        f"  In platform only: {sorted(platform_values - so_values)}"
    )


# ---------------------------------------------------------------------------
# 3. UIDisplayMode Python values vs uiSurfaceReducer.js normalizeDisplayMode
# ---------------------------------------------------------------------------

def _get_python_display_mode_values() -> frozenset[str]:
    from mozaiksai.core.transport.ui_events import UIDisplayMode  # noqa: PLC0415
    return frozenset(v.value for v in UIDisplayMode)


def _parse_js_normalize_display_mode_values(reducer_path: Path) -> frozenset[str]:
    """Extract the accepted value list from normalizeDisplayMode in uiSurfaceReducer.js."""
    text = reducer_path.read_text(encoding="utf-8")
    # Locate: ['artifact', 'inline', 'view', 'fullscreen'].includes(lowered)
    match = re.search(
        r"normalizeDisplayMode\s*=\s*\([^)]*\)\s*=>\s*\{[^}]+\[([^\]]+)\]\.includes",
        text,
        re.DOTALL,
    )
    assert match, (
        "normalizeDisplayMode accepted-values array not found in uiSurfaceReducer.js"
    )
    raw = match.group(1)
    values = re.findall(r"['\"]([^'\"]+)['\"]", raw)
    return frozenset(values)


def test_python_display_mode_values_are_subset_of_js_normalizer():
    """Every Python UIDisplayMode value must be accepted by JS normalizeDisplayMode.

    Python emits only 'inline' and 'artifact'.  JS additionally recognises
    'view' and 'fullscreen' as layout-mode aliases used internally by the
    surface state machine (uiSurfaceReducer.js: deriveSurfaceMode checks
    layoutMode === 'view', and shouldOpenArtifactPanel checks layoutMode !== 'full').
    These are panel-layout control values driven by UI interactions, not transport
    values emitted from Python.  That asymmetry is intentional: Python never emits
    'view'/'fullscreen', so no event would be dropped.

    This test verifies the containment relationship holds and documents the
    JS-internal extras.  The assertion `js_internal == {"view", "fullscreen"}`
    acts as a regression guard: if new JS-internal aliases are added (or existing
    ones removed), this test will fail and prompt a deliberate review.
    """
    python_values = _get_python_display_mode_values()
    js_values = _parse_js_normalize_display_mode_values(UI_SURFACE_REDUCER_JS)

    # Python values must be a subset — JS must accept everything Python emits.
    leaked = python_values - js_values
    assert not leaked, (
        f"Python UIDisplayMode values rejected by JS normalizeDisplayMode: {sorted(leaked)}\n"
        f"  Python values: {sorted(python_values)}\n"
        f"  JS accepted values: {sorted(js_values)}"
    )

    # Document JS-internal extras (not a failure — they are layout aliases).
    js_internal = js_values - python_values
    # 'view' and 'fullscreen' are expected JS-internal layout-mode aliases;
    # Python should never need to emit them.
    assert js_internal == {"view", "fullscreen"}, (
        f"Unexpected JS-internal display mode values (not emitted by Python).\n"
        f"Expected exactly {{'view', 'fullscreen'}}, got: {sorted(js_internal)}\n"
        "If Python now emits new values, add them to UIDisplayMode and verify "
        "JS normalizeDisplayMode accepts them."
    )


# ---------------------------------------------------------------------------
# 4. UI primitive names across all four sources
# ---------------------------------------------------------------------------

def _primitive_names_from_primitive_schemas_json(json_path: Path) -> frozenset[str]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # Strip the meta _comment key if present.
    return frozenset(k for k in data if not k.startswith("_"))


def _primitive_names_from_structured_outputs() -> frozenset[str]:
    # AppPageSection.primitive lists all page-level primitive names.
    return _so_field_literal_values("AppPageSection", "primitive")


def _primitive_names_from_python() -> frozenset[str]:
    from mozaiksai.core.workflow.ui_primitives import get_page_ui_primitive_names  # noqa: PLC0415
    return frozenset(get_page_ui_primitive_names())


def test_primitive_names_aligned_across_all_sources():
    """All three primitive name sources must be identical.

    Sources:
      • primitive_schemas.json — JS schema catalog, generated from PrimitiveRegistry.js via
        node scripts/export-primitive-schemas.js; treated as a separate authority because it
        is a committed artifact and can drift from the registry if not regenerated.
      • AppPageSection.primitive literal (structured output) — the generator contract that
        agents emit.
      • get_page_ui_primitive_names() (Python runtime) — reads import statements from
        PrimitiveRegistry.js directly; genuine independent source from primitive_schemas.json
        even though both derive from PrimitiveRegistry.js.

    Note: the test title previously said "four" sources — that was a documentation error;
    there are three distinct authority sources tested here.
    """
    json_names = _primitive_names_from_primitive_schemas_json(PRIMITIVE_SCHEMAS_JSON)
    so_names = _primitive_names_from_structured_outputs()
    python_names = _primitive_names_from_python()

    # Compare all pairs.
    assert json_names == so_names, (
        f"primitive_schemas.json vs structured_outputs.yaml drift:\n"
        f"  In JSON only: {sorted(json_names - so_names)}\n"
        f"  In SO only: {sorted(so_names - json_names)}"
    )
    assert so_names == python_names, (
        f"structured_outputs.yaml vs get_page_ui_primitive_names() drift:\n"
        f"  In SO only: {sorted(so_names - python_names)}\n"
        f"  In Python only: {sorted(python_names - so_names)}"
    )


# ---------------------------------------------------------------------------
# 5. Page type values across structured outputs and VALID_PAGE_TYPES
# ---------------------------------------------------------------------------

def _get_valid_page_types() -> frozenset[str]:
    from factory_app.workflows._shared.generated_ui_contract import (
        VALID_PAGE_TYPES,  # noqa: PLC0415
    )
    return frozenset(VALID_PAGE_TYPES)


def test_page_type_values_aligned_across_structured_output_and_contract():
    """AppPageSchema.page_type literal must exactly match VALID_PAGE_TYPES."""
    so_values = _so_field_literal_values("AppPageSchema", "page_type")
    contract_values = _get_valid_page_types()

    assert so_values == contract_values, (
        f"Page type drift detected.\n"
        f"  structured_outputs.yaml: {sorted(so_values)}\n"
        f"  VALID_PAGE_TYPES: {sorted(contract_values)}\n"
        f"  In SO only: {sorted(so_values - contract_values)}\n"
        f"  In contract only: {sorted(contract_values - so_values)}"
    )


# ---------------------------------------------------------------------------
# 6. Custom-route three-file closure for factory_app
# ---------------------------------------------------------------------------

def _component_names_from_route_manifest(manifest_path: Path) -> frozenset[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return frozenset(page["component"] for page in manifest.get("pages", []))


def _registered_components_from_js_files(*js_paths: Path) -> frozenset[str]:
    """Extract component names passed to registerComponent() across multiple JS files.

    factory_app/app/ui/index.js delegates to registerAdminComponents() in
    factory_app/app/admin/index.js, which is where the actual registerComponent()
    calls live.  Both files must be searched to resolve the full registration surface.
    """
    names: set[str] = set()
    for js_path in js_paths:
        text = js_path.read_text(encoding="utf-8")
        names.update(re.findall(r"registerComponent\(\s*['\"]([^'\"]+)['\"]", text))
    return frozenset(names)


def test_factory_app_custom_route_closure_all_manifest_components_registered():
    """Every component listed in factory_app route_manifest.json must be registered.

    Registration happens transitively: ui/index.js calls registerAdminComponents()
    which is defined in admin/index.js.  Both registration files are checked.
    """
    manifest_components = _component_names_from_route_manifest(FACTORY_ROUTE_MANIFEST)
    registered = _registered_components_from_js_files(
        FACTORY_UI_INDEX_JS, FACTORY_ADMIN_INDEX_JS
    )

    unregistered = manifest_components - registered
    assert not unregistered, (
        f"route_manifest.json components missing from ui/index.js registerComponent calls:\n"
        f"  {sorted(unregistered)}\n"
        "Each route_manifest entry needs a matching registerComponent() call in ui/index.js."
    )


def test_factory_app_custom_route_manifest_exists_and_is_valid_json():
    """factory_app route_manifest.json must exist and contain a 'pages' list."""
    assert FACTORY_ROUTE_MANIFEST.exists(), (
        f"route_manifest.json not found at {FACTORY_ROUTE_MANIFEST}"
    )
    manifest = json.loads(FACTORY_ROUTE_MANIFEST.read_text(encoding="utf-8"))
    assert "pages" in manifest, "route_manifest.json must have a 'pages' key"
    assert isinstance(manifest["pages"], list), "'pages' must be a list"
    assert len(manifest["pages"]) > 0, "route_manifest.json has no pages declared"


# Component files are loaded from one of two roots depending on how admin/index.js imports them:
#   - factory_app/app/admin/pages/{ComponentName}.jsx  (local admin pages)
#   - chat-ui/src/pages/{ComponentName}.jsx            (@mozaiks/chat-ui/pages/* imports)
_ADMIN_PAGES_ROOT = REPO_ROOT / "factory_app" / "app" / "admin" / "pages"
_CHAT_UI_PAGES_ROOT = REPO_ROOT / "chat-ui" / "src" / "pages"


def test_factory_app_manifest_component_files_exist_on_disk():
    """Every component referenced in route_manifest.json must have a backing source file.

    Components imported from '@mozaiks/chat-ui/pages/*' are resolved under
    chat-ui/src/pages/.  All other components are resolved under
    factory_app/app/admin/pages/.

    This ensures the three-file closure (manifest entry → registration → file) is
    complete: a component name in the manifest with no backing file would silently
    fail at runtime when the module is lazy-loaded.
    """
    manifest = json.loads(FACTORY_ROUTE_MANIFEST.read_text(encoding="utf-8"))
    admin_text = FACTORY_ADMIN_INDEX_JS.read_text(encoding="utf-8")

    # Build a map: component_name → import source path string (from admin/index.js)
    # e.g. "ProfilePage" → "@mozaiks/chat-ui/pages/ProfilePage.jsx"
    #      "StudioPage"  → "./pages/StudioPage.jsx"
    import_pattern = re.compile(
        r"const\s+(\w+)\s*=\s*lazy\(\s*\(\)\s*=>\s*import\(['\"]([^'\"]+)['\"]\)\s*\)"
    )
    import_map: dict[str, str] = {
        m.group(1): m.group(2) for m in import_pattern.finditer(admin_text)
    }

    missing: list[str] = []
    for page in manifest.get("pages", []):
        comp = page["component"]
        import_src = import_map.get(comp, "")
        if import_src.startswith("@mozaiks/chat-ui/pages/"):
            # Resolve to chat-ui/src/pages/
            filename = import_src.split("/")[-1]
            candidate = _CHAT_UI_PAGES_ROOT / filename
        else:
            # Local admin page — try .jsx then .js
            candidate = _ADMIN_PAGES_ROOT / f"{comp}.jsx"
            if not candidate.exists():
                candidate = _ADMIN_PAGES_ROOT / f"{comp}.js"

        if not candidate.exists():
            missing.append(
                f"{comp!r} → expected file: {candidate} (import: {import_src!r})"
            )

    assert not missing, (
        "route_manifest.json components have no backing source file:\n"
        + "\n".join(f"  {m}" for m in missing)
    )


# ---------------------------------------------------------------------------
# 7. AppPageSchema.extensions — removed contract stays removed
#
# RESOLVED: the extensions / AppPageSlotExtension contract was a false promise
# (the generator validated it, PageRenderer never rendered it) and has been
# removed completely.  These tests prove the removal holds in every layer.
# ---------------------------------------------------------------------------

def test_page_slot_extension_model_is_removed_from_structured_outputs():
    """AppPageSlotExtension must not exist and AppPageSchema must not declare extensions."""
    models = _load_structured_outputs()["models"]
    assert "AppPageSlotExtension" not in models, (
        "AppPageSlotExtension returned to structured outputs. The slot-extension "
        "contract was removed as a false promise; reintroducing it requires a "
        "real PageRenderer implementation with closed component authority."
    )
    page_fields = models["AppPageSchema"].get("fields", {})
    assert "extensions" not in page_fields, (
        "AppPageSchema.extensions returned to structured outputs without a renderer."
    )


def test_page_renderer_still_does_not_reference_extensions():
    """PageRenderer.jsx must not reference the removed 'extensions' field."""
    text = PAGE_RENDERER_JSX.read_text(encoding="utf-8")
    source_without_comments = re.sub(r"//[^\n]*", "", text)
    source_without_comments = re.sub(r"/\*.*?\*/", "", source_without_comments, flags=re.DOTALL)
    references = re.findall(r"\bextensions\b", source_without_comments)
    assert not references, (
        "PageRenderer.jsx references 'extensions'. Rendering slot extensions "
        "requires reintroducing the contract deliberately across structured "
        "outputs, validators, prompts, and tests together."
    )


def test_generator_prompts_no_longer_promise_slot_extensions():
    """AppGenerator prompts must not instruct agents to emit page slot extensions."""
    agents_text = (
        Path("factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8")
    )
    for marker in (
        "extensions.slot",
        "slot: header",
        "slot: empty_state",
        "Page extensions",
        "extension slot rules",
        "extensions with unrecognized slot",
        '"extensions": null',
        "Set `extensions` only",
    ):
        assert marker not in agents_text, (
            f"AppGenerator prompt still references removed slot extensions: {marker!r}"
        )


def test_bundle_scanner_rejects_pages_declaring_extensions():
    """Acceptance fails closed for any page that still declares 'extensions'."""
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (  # noqa: PLC0415
        scan_generated_bundle,
    )

    page_yaml = (
        "name: items\n"
        "route: /items\n"
        "page_type: record_list\n"
        "extensions: null\n"
        "sections: []\n"
    )
    errors = scan_generated_bundle({"app.json": "{}", "ui/pages/items.yaml": page_yaml})
    assert any("page_schema.extra_forbidden" in error for error in errors)

    clean_yaml = (
        "name: items\n"
        "route: /items\n"
        "page_type: record_list\n"
        "sections: []\n"
    )
    clean_errors = scan_generated_bundle({"app.json": "{}", "ui/pages/items.yaml": clean_yaml})
    assert not any("retired unsupported page field" in error for error in clean_errors)


def test_generated_ui_audit_flags_extensions_presence():
    """The quality audit flags the removed field regardless of its value."""
    from factory_app.workflows._shared.generated_ui_contract import (  # noqa: PLC0415
        audit_page_schemas,
    )

    warnings = audit_page_schemas(
        [
            {
                "name": "Items",
                "route": "/items",
                "title": "Items",
                "page_type": "record_list",
                "extensions": None,
                "sections": [],
            }
        ]
    )
    assert any("retired unsupported field" in warning for warning in warnings)


# ---------------------------------------------------------------------------
# 8. UIToolContractSpec — Pydantic model structure and validation
# ---------------------------------------------------------------------------

def test_ui_tool_contract_spec_surface_kind_is_locked():
    """UIToolContractSpec.surface_kind must be Literal['agent_tool'] with no override."""
    from mozaiksai.core.workflow.declarative.contracts import UIToolContractSpec  # noqa: PLC0415

    spec = UIToolContractSpec()
    assert spec.surface_kind == "agent_tool", (
        f"UIToolContractSpec.surface_kind default changed: {spec.surface_kind!r}"
    )


def test_ui_tool_contract_spec_rejects_wrong_surface_kind():
    """UIToolContractSpec must reject surface_kind values other than 'agent_tool'."""
    import pydantic  # noqa: PLC0415

    from mozaiksai.core.workflow.declarative.contracts import UIToolContractSpec  # noqa: PLC0415

    with pytest.raises((pydantic.ValidationError, ValueError)):
        UIToolContractSpec(surface_kind="declarative_page")  # type: ignore[arg-type]


def test_ui_tool_contract_spec_accepts_raw_dict_payload():
    """UIToolContractSpec must accept a raw dict for payload_schema (generator output)."""
    from mozaiksai.core.workflow.declarative.contracts import UIToolContractSpec  # noqa: PLC0415

    spec = UIToolContractSpec(payload_schema={"type": "object", "properties": {}})
    assert isinstance(spec.payload_schema, dict)
    assert spec.payload_schema.get("type") == "object"


# ---------------------------------------------------------------------------
# 9. Page serving — GET /api/pages/{name} canonical validation owner
# ---------------------------------------------------------------------------

def test_page_serving_uses_canonical_page_schema_validator():
    """Shell router must validate pages through the canonical runtime owner."""
    source = SHELL_ROUTER_PY.read_text(encoding="utf-8")

    fn_match = re.search(
        r"async def get_page_schema\(.*?\n((?:[ \t]+[^\n]*\n|\n)*)",
        source,
    )
    assert fn_match, "get_page_schema not found in shell.py"
    fn_body = fn_match.group(1)

    assert "load_and_validate_page_schema" in fn_body
    assert "safe_page_schema_error_detail" in source
    assert "isinstance(schema, dict)" not in fn_body
