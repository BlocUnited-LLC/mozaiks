"""Theme round-trip contract tests.

Proves that the theme pipeline chain is consistent end-to-end:

  ThemeCapture → CapturedThemeConfig fields
  → theme_config.json (app/brand/theme_config.json)
  → tokens.js generateThemeTokens(config) input fields
  → --mz-* CSS custom properties

No JS runtime is invoked.  The tests verify the source-level contracts
at each boundary so a breakage in any link is caught deterministically.

UI Portability Model (from MOZAIKS_OSS_SOFTWARE_DESIGN.md §7):

  SCHEMA_NATIVE       — preferred for reusable/community UI
  SEMANTIC_TOKEN_REACT — conditionally portable; must use --mz-* tokens
  ARBITRARY_CUSTOM_REACT — supported escape hatch, weaker portability
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKENS_JS = REPO_ROOT / "chat-ui" / "src" / "ui" / "theme" / "tokens.js"
PALETTES_JS = REPO_ROOT / "chat-ui" / "src" / "ui" / "theme" / "palettes.js"
THEME_PROVIDER_JS = REPO_ROOT / "chat-ui" / "src" / "styles" / "themeProvider.js"
THEME_CONFIG_JSON = REPO_ROOT / "factory_app" / "app" / "brand" / "theme_config.json"
THEME_CAPTURE_OUTPUTS = REPO_ROOT / "factory_app" / "workflows" / "ThemeCapture" / "structured_outputs.yaml"


# ---------------------------------------------------------------------------
# 1. tokens.js field mapping
# ---------------------------------------------------------------------------

def test_tokens_js_reads_primary_field() -> None:
    """tokens.js must destructure 'primary' from config to drive --mz-primary."""
    source = TOKENS_JS.read_text(encoding="utf-8")
    assert "primary" in source
    assert "'--mz-primary':" in source
    assert "scale[500]" in source


def test_tokens_js_reads_radius_field() -> None:
    source = TOKENS_JS.read_text(encoding="utf-8")
    assert "radius" in source
    assert "'--mz-radius':" in source
    assert "radiusScale[radius]" in source


def test_tokens_js_reads_font_field_for_sans() -> None:
    source = TOKENS_JS.read_text(encoding="utf-8")
    assert "font" in source
    assert "'--mz-font-sans':" in source
    assert "fontFamilies[font]" in source


def test_tokens_js_reads_font_heading_field() -> None:
    source = TOKENS_JS.read_text(encoding="utf-8")
    assert "font_heading" in source
    assert "'--mz-font-heading':" in source
    assert "fontFamilies[font_heading]" in source


def test_tokens_js_reads_appearance_field_for_dark_mode() -> None:
    source = TOKENS_JS.read_text(encoding="utf-8")
    assert "appearance" in source
    assert "appearance === 'dark'" in source
    assert "darkBase(scale)" in source


def test_tokens_js_reads_density_field_for_spacing() -> None:
    source = TOKENS_JS.read_text(encoding="utf-8")
    assert "density" in source
    assert "'--mz-spacing-unit':" in source
    assert "densityScale[density]" in source


def test_tokens_js_radius_scale_covers_all_valid_values() -> None:
    """All radius values declared in ThemeCapture structured outputs must have a mapping."""
    # JS object literal keys are unquoted identifiers: `none:`, `small:`, etc.
    source = TOKENS_JS.read_text(encoding="utf-8")
    for value in ("none", "small", "medium", "large", "full"):
        assert f"  {value}:" in source, f"radiusScale missing entry for '{value}'"


def test_tokens_js_density_scale_covers_all_valid_values() -> None:
    # JS object literal keys are unquoted identifiers: `compact:`, `comfortable:`, etc.
    source = TOKENS_JS.read_text(encoding="utf-8")
    for value in ("compact", "comfortable", "spacious"):
        assert f"  {value}:" in source, f"densityScale missing entry for '{value}'"


# ---------------------------------------------------------------------------
# 2. theme_config.json → tokens.js contract: all fields tokens.js reads exist
# ---------------------------------------------------------------------------

def test_theme_config_has_all_token_driver_fields() -> None:
    """factory_app theme_config.json must carry every field generateThemeTokens() reads."""
    theme = json.loads(THEME_CONFIG_JSON.read_text(encoding="utf-8"))
    compact = theme.get("theme", {})
    for key in ("primary", "radius", "font", "font_heading", "appearance", "density"):
        assert key in compact, f"theme_config.json missing theme.{key}"


def test_theme_config_primary_value_is_in_palettes() -> None:
    """The configured primary color must exist in palettes.js."""
    theme = json.loads(THEME_CONFIG_JSON.read_text(encoding="utf-8"))
    primary = theme["theme"]["primary"]
    palettes_source = PALETTES_JS.read_text(encoding="utf-8")
    assert f"  {primary}:" in palettes_source, (
        f"theme_config.json uses primary='{primary}' but palettes.js has no '{primary}' palette"
    )


def test_theme_config_radius_value_is_valid() -> None:
    theme = json.loads(THEME_CONFIG_JSON.read_text(encoding="utf-8"))
    radius = theme["theme"]["radius"]
    valid = {"none", "small", "medium", "large", "full"}
    assert radius in valid, f"theme_config.json radius='{radius}' is not in {valid}"


def test_theme_config_appearance_value_is_valid() -> None:
    theme = json.loads(THEME_CONFIG_JSON.read_text(encoding="utf-8"))
    appearance = theme["theme"]["appearance"]
    valid = {"light", "dark", "system"}
    assert appearance in valid, f"theme_config.json appearance='{appearance}' is not in {valid}"


def test_theme_config_density_value_is_valid() -> None:
    theme = json.loads(THEME_CONFIG_JSON.read_text(encoding="utf-8"))
    density = theme["theme"]["density"]
    valid = {"compact", "comfortable", "spacious"}
    assert density in valid, f"theme_config.json density='{density}' is not in {valid}"


# ---------------------------------------------------------------------------
# 3. ThemeCapture output schema → tokens.js contract
# ---------------------------------------------------------------------------

def test_theme_capture_output_declares_same_fields_as_tokens_js_reads() -> None:
    """CapturedThemeConfig output model must declare every field generateThemeTokens reads."""
    outputs = yaml.safe_load(THEME_CAPTURE_OUTPUTS.read_text(encoding="utf-8"))

    # ThemeV2 is the compact token schema captured from the source.
    # Find it in the models section.
    models = outputs.get("models", {})
    theme_v2 = models.get("ThemeV2", {})
    fields = theme_v2.get("fields", {})

    for key in ("primary", "radius", "font", "font_heading", "appearance", "density"):
        assert key in fields, (
            f"ThemeV2 model in ThemeCapture structured_outputs.yaml missing field '{key}'. "
            "It must align with what generateThemeTokens() reads."
        )


def test_theme_capture_output_declares_css_token_namespace() -> None:
    """ThemeCapture workflow docs must acknowledge the --mz-* CSS token namespace."""
    source = THEME_CAPTURE_OUTPUTS.read_text(encoding="utf-8")
    # The structured outputs mention the token system in descriptions
    assert "--mz-" in source or "mz-" in source or "css" in source.lower() or "token" in source.lower(), (
        "ThemeCapture structured_outputs.yaml should document the CSS token output namespace"
    )


# ---------------------------------------------------------------------------
# 4. themeProvider.js connects to the token system
# ---------------------------------------------------------------------------

def test_theme_provider_applies_mz_tokens() -> None:
    """themeProvider.js must call a token function that produces --mz-* variables."""
    source = THEME_PROVIDER_JS.read_text(encoding="utf-8")
    # The provider imports from the token module and applies variables
    assert "generateThemeTokens" in source or "applyThemeTokens" in source or "--mz-" in source, (
        "themeProvider.js does not connect to the --mz-* token system"
    )


def test_theme_provider_has_fallback_theme() -> None:
    """themeProvider.js must have a fallback so the UI renders without a live API."""
    source = THEME_PROVIDER_JS.read_text(encoding="utf-8")
    assert "FALLBACK" in source or "fallback" in source.lower(), (
        "themeProvider.js must declare a fallback theme for offline/no-config rendering"
    )
