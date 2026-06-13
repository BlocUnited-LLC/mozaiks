"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_shell_preset_context.py

Covers:

  _first_sentence:
    - None value → empty string
    - empty string → empty string
    - single-line text → text returned stripped
    - multi-line text → first line only
    - leading/trailing whitespace stripped
    - only-whitespace first line returns empty

  _format_preset:
    - preset_id used as header line
    - description from first sentence of "description" key
    - empty description omitted
    - select_when items rendered (up to 3)
    - select_when capped at 3
    - select_when whitespace items filtered
    - chrome_default rendered when present
    - chrome_default omitted when absent
    - shell_policy.desktop rendered when present
    - shell_policy.mobile rendered when present
    - shell_policy without desktop/mobile → no desktop/mobile lines
    - maxMobileItems rendered when present
    - page_guidance primary_modes rendered as compact kv
    - page_guidance navigation_scopes rendered as compact kv
    - empty page_guidance → no page_modes/nav_scopes lines
    - non-dict shell_policy → no desktop/mobile lines

  _build_shell_preset_body:
    - no "presets" key → returns empty-catalog message
    - empty presets dict → returns empty-catalog message
    - non-dict presets → returns empty-catalog message
    - valid presets → header lines present
    - valid presets → each preset rendered
    - rules appended when present
    - whitespace-only rules filtered
    - empty rules list → no rules section
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.hook_shell_preset_context import (
    _build_shell_preset_body,
    _first_sentence,
    _format_preset,
)

# ---------------------------------------------------------------------------
# 1. _first_sentence
# ---------------------------------------------------------------------------

class TestFirstSentence:
    def test_none_returns_empty(self):
        assert _first_sentence(None) == ""

    def test_empty_string_returns_empty(self):
        assert _first_sentence("") == ""

    def test_single_line_returned(self):
        assert _first_sentence("No newlines here") == "No newlines here"

    def test_multiline_returns_first_line(self):
        result = _first_sentence("First line.\nSecond line.")
        assert result == "First line."

    def test_leading_trailing_whitespace_stripped(self):
        result = _first_sentence("  Hello World  ")
        assert result == "Hello World"

    def test_whitespace_only_returns_empty(self):
        assert _first_sentence("   ") == ""

    def test_whitespace_first_line_stripped(self):
        result = _first_sentence("  trimmed  \nmore lines")
        assert result == "trimmed"


# ---------------------------------------------------------------------------
# 2. _format_preset
# ---------------------------------------------------------------------------

class TestFormatPreset:
    def test_preset_id_as_header(self):
        result = _format_preset("dashboard", {})
        assert result.startswith("dashboard:")

    def test_description_from_first_sentence(self):
        preset = {"description": "A clean dashboard. More details."}
        result = _format_preset("dashboard", preset)
        assert "A clean dashboard." in result

    def test_empty_description_omitted(self):
        result = _format_preset("minimal", {"description": ""})
        assert "description:" not in result

    def test_select_when_rendered(self):
        preset = {"select_when": ["when admin", "when dashboard"]}
        result = _format_preset("admin_panel", preset)
        assert "select_when:" in result
        assert "when admin" in result
        assert "when dashboard" in result

    def test_select_when_capped_at_3(self):
        preset = {"select_when": ["a", "b", "c", "d", "e"]}
        result = _format_preset("heavy", preset)
        assert "a" in result
        assert "c" in result
        # 4th item should not appear
        assert result.count("- ") <= 3

    def test_whitespace_select_when_filtered(self):
        preset = {"select_when": ["   ", "valid"]}
        result = _format_preset("x", preset)
        assert "valid" in result
        # whitespace-only entry not rendered as a dash item
        lines = result.splitlines()
        dash_lines = [ln for ln in lines if ln.strip().startswith("- ")]
        assert len(dash_lines) == 1

    def test_chrome_default_rendered(self):
        preset = {"chrome_default": "sidebar"}
        result = _format_preset("sidebar_preset", preset)
        assert "chrome_default: sidebar" in result

    def test_chrome_default_omitted_when_absent(self):
        result = _format_preset("plain", {})
        assert "chrome_default" not in result

    def test_shell_policy_desktop_rendered(self):
        preset = {
            "shell_policy": {
                "desktop": {"global": True, "local": False, "footer": None}
            }
        }
        result = _format_preset("desktop_preset", preset)
        assert "desktop:" in result
        assert "global=True" in result

    def test_shell_policy_mobile_rendered(self):
        preset = {
            "shell_policy": {
                "mobile": {"global": False, "local": True, "footer": False}
            }
        }
        result = _format_preset("mobile_preset", preset)
        assert "mobile:" in result
        assert "local=True" in result

    def test_shell_policy_without_desktop_mobile(self):
        preset = {"shell_policy": {"maxMobileItems": 4}}
        result = _format_preset("basic", preset)
        assert "desktop:" not in result
        assert "mobile:" not in result

    def test_max_mobile_items_rendered(self):
        preset = {"shell_policy": {"maxMobileItems": 5}}
        result = _format_preset("compact", preset)
        assert "maxMobileItems: 5" in result

    def test_non_dict_shell_policy_no_crash(self):
        preset = {"shell_policy": "not-a-dict"}
        # Should not raise
        result = _format_preset("bad_policy", preset)
        assert result.startswith("bad_policy:")
        assert "desktop:" not in result

    def test_page_guidance_primary_modes(self):
        preset = {"page_guidance": {"primary_modes": {"list": "standard", "detail": "full"}}}
        result = _format_preset("guided", preset)
        assert "page_modes:" in result
        assert "list=standard" in result

    def test_page_guidance_navigation_scopes(self):
        preset = {"page_guidance": {"navigation_scopes": {"main": "global"}}}
        result = _format_preset("nav_preset", preset)
        assert "nav_scopes:" in result
        assert "main=global" in result

    def test_empty_page_guidance_no_extra_lines(self):
        preset = {"page_guidance": {"primary_modes": {}, "navigation_scopes": {}}}
        result = _format_preset("empty_guidance", preset)
        assert "page_modes:" not in result
        assert "nav_scopes:" not in result


# ---------------------------------------------------------------------------
# 3. _build_shell_preset_body
# ---------------------------------------------------------------------------

class TestBuildShellPresetBody:
    def _empty_catalog_message(self) -> str:
        return _build_shell_preset_body({})

    def test_no_presets_key_returns_empty_catalog_message(self):
        result = _build_shell_preset_body({})
        assert "empty" in result.lower() or "Do not emit" in result

    def test_empty_presets_dict_returns_empty_catalog_message(self):
        result = _build_shell_preset_body({"presets": {}})
        assert "empty" in result.lower() or "Do not emit" in result

    def test_non_dict_presets_returns_empty_catalog_message(self):
        result = _build_shell_preset_body({"presets": "not-a-dict"})
        assert "empty" in result.lower() or "Do not emit" in result

    def test_valid_presets_contains_header_guidance(self):
        config = {"presets": {"sidebar": {"description": "Sidebar layout"}}}
        result = _build_shell_preset_body(config)
        assert "Shell presets" in result or "presets" in result.lower()

    def test_valid_preset_rendered_in_output(self):
        config = {"presets": {"sidebar": {"description": "Side nav"}}}
        result = _build_shell_preset_body(config)
        assert "sidebar:" in result

    def test_multiple_presets_all_rendered(self):
        config = {
            "presets": {
                "sidebar": {"description": "Side"},
                "topnav": {"description": "Top"},
            }
        }
        result = _build_shell_preset_body(config)
        assert "sidebar:" in result
        assert "topnav:" in result

    def test_rules_appended_when_present(self):
        config = {
            "presets": {"basic": {}},
            "rules": ["Use sidebar for admin", "Use topnav for public"],
        }
        result = _build_shell_preset_body(config)
        assert "Rules:" in result
        assert "Use sidebar for admin" in result

    def test_whitespace_only_rules_filtered(self):
        config = {
            "presets": {"basic": {}},
            "rules": ["   ", "real rule"],
        }
        result = _build_shell_preset_body(config)
        assert "real rule" in result
        # Whitespace-only rule not rendered
        lines = result.splitlines()
        dash_lines = [ln.strip() for ln in lines if ln.strip().startswith("- ")]
        assert not any(ln == "- " for ln in dash_lines)

    def test_empty_rules_list_no_rules_section(self):
        config = {"presets": {"basic": {}}, "rules": []}
        result = _build_shell_preset_body(config)
        assert "Rules:" not in result

    def test_non_dict_preset_values_skipped(self):
        config = {"presets": {"valid": {"description": "Good"}, "bad": "not-a-dict"}}
        result = _build_shell_preset_body(config)
        assert "valid:" in result
        # bad entry is a string, not dict — _format_preset won't crash but "bad:" may not appear cleanly
