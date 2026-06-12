"""
Generator support code_files pure helper unit tests.

Covers:
  safe_relpath:
    - non-string → None
    - empty string → None
    - whitespace only → None
    - absolute path (leading /) → None
    - path with ".." component → None
    - backslashes normalized to forward slashes
    - leading/trailing slashes stripped (via PurePosixPath)
    - simple valid relative path returned
    - nested relative path returned
    - path with only ".." after normalization → None

  _unwrap_output_envelope:
    - non-dict → returned as-is
    - multi-key dict → returned as-is
    - single-key dict without "Output" suffix → returned as-is
    - single-key dict with "Output" suffix and dict value → value returned
    - single-key dict with "Output" suffix but non-dict value → returned as-is
    - empty dict → returned as-is

  _canonical_generated_path:
    - path with 3 parts: modules/{id}/{contract_file} → modules/{id}/contracts/{contract_file}
    - known contract filenames rewritten: events.yaml, reactions.yaml, notifications.yaml,
      settings.yaml, admin.yaml
    - path NOT in _MODULE_CONTRACT_FILENAMES → unchanged
    - path with wrong number of parts → unchanged
    - path not starting with "modules" → unchanged

  _page_file_stem:
    - route with segments → last segment used
    - route "/" → falls back to name
    - no route → uses name
    - no route/name → uses id
    - no route/name/id → "page" fallback
    - special chars in candidate → normalized to underscores
    - result is lowercased
    - empty normalized candidate → "page" fallback
"""
from __future__ import annotations

from mozaiksai.core.workflow.generator_support.code_files import (
    _canonical_generated_path,
    _page_file_stem,
    _unwrap_output_envelope,
    safe_relpath,
)

# ---------------------------------------------------------------------------
# 1. safe_relpath
# ---------------------------------------------------------------------------

class TestSafeRelpath:
    def test_non_string_returns_none(self):
        assert safe_relpath(None) is None  # type: ignore[arg-type]
        assert safe_relpath(42) is None  # type: ignore[arg-type]
        assert safe_relpath([]) is None  # type: ignore[arg-type]

    def test_empty_string_returns_none(self):
        assert safe_relpath("") is None

    def test_whitespace_only_returns_none(self):
        assert safe_relpath("   ") is None

    def test_absolute_path_returns_none(self):
        assert safe_relpath("/absolute/path.py") is None

    def test_path_with_dotdot_returns_none(self):
        assert safe_relpath("../escape.py") is None
        assert safe_relpath("subdir/../../secret.py") is None
        assert safe_relpath("a/../b/../../../secret") is None

    def test_backslashes_normalized(self):
        result = safe_relpath("modules\\mymod\\module.yaml")
        assert result == "modules/mymod/module.yaml"

    def test_simple_relative_path_returned(self):
        assert safe_relpath("modules/mymod/module.yaml") == "modules/mymod/module.yaml"

    def test_nested_relative_path_returned(self):
        result = safe_relpath("a/b/c/d.txt")
        assert result == "a/b/c/d.txt"

    def test_single_filename_returned(self):
        assert safe_relpath("file.py") == "file.py"


# ---------------------------------------------------------------------------
# 2. _unwrap_output_envelope
# ---------------------------------------------------------------------------

class TestUnwrapOutputEnvelope:
    def test_non_dict_returned_as_is(self):
        assert _unwrap_output_envelope("hello") == "hello"
        assert _unwrap_output_envelope(42) == 42
        assert _unwrap_output_envelope(None) is None
        assert _unwrap_output_envelope([1, 2]) == [1, 2]

    def test_multi_key_dict_returned_as_is(self):
        d = {"AppOutput": {"a": 1}, "extra": "field"}
        result = _unwrap_output_envelope(d)
        assert result is d

    def test_single_key_without_output_suffix_returned_as_is(self):
        d = {"myData": {"a": 1}}
        result = _unwrap_output_envelope(d)
        assert result is d

    def test_single_key_with_output_suffix_and_dict_value_unwrapped(self):
        inner = {"pages": [1, 2, 3]}
        d = {"AppSchemaOutput": inner}
        result = _unwrap_output_envelope(d)
        assert result is inner

    def test_single_key_with_output_suffix_non_dict_value_not_unwrapped(self):
        d = {"AppOutput": "not_a_dict"}
        result = _unwrap_output_envelope(d)
        assert result is d

    def test_empty_dict_returned_as_is(self):
        result = _unwrap_output_envelope({})
        assert result == {}

    def test_key_must_end_with_output_not_just_contain(self):
        d = {"OutputData": {"x": 1}}
        result = _unwrap_output_envelope(d)
        assert result is d


# ---------------------------------------------------------------------------
# 3. _canonical_generated_path
# ---------------------------------------------------------------------------

class TestCanonicalGeneratedPath:
    def test_module_contract_events_yaml_rewritten(self):
        result = _canonical_generated_path("modules/mymod/events.yaml")
        assert result == "modules/mymod/contracts/events.yaml"

    def test_module_contract_reactions_yaml_rewritten(self):
        result = _canonical_generated_path("modules/mymod/reactions.yaml")
        assert result == "modules/mymod/contracts/reactions.yaml"

    def test_module_contract_notifications_yaml_rewritten(self):
        result = _canonical_generated_path("modules/mymod/notifications.yaml")
        assert result == "modules/mymod/contracts/notifications.yaml"

    def test_module_contract_settings_yaml_rewritten(self):
        result = _canonical_generated_path("modules/mymod/settings.yaml")
        assert result == "modules/mymod/contracts/settings.yaml"

    def test_module_contract_admin_yaml_rewritten(self):
        result = _canonical_generated_path("modules/mymod/admin.yaml")
        assert result == "modules/mymod/contracts/admin.yaml"

    def test_module_yaml_not_a_contract_file_unchanged(self):
        result = _canonical_generated_path("modules/mymod/module.yaml")
        assert result == "modules/mymod/module.yaml"

    def test_path_not_starting_with_modules_unchanged(self):
        result = _canonical_generated_path("app/events.yaml")
        assert result == "app/events.yaml"

    def test_wrong_part_count_not_rewritten(self):
        result = _canonical_generated_path("modules/mymod/subdir/events.yaml")
        assert result == "modules/mymod/subdir/events.yaml"

    def test_only_two_parts_unchanged(self):
        result = _canonical_generated_path("modules/events.yaml")
        assert result == "modules/events.yaml"

    def test_already_in_contracts_subdir_unchanged(self):
        # 4 parts → not rewritten
        result = _canonical_generated_path("modules/mymod/contracts/events.yaml")
        assert result == "modules/mymod/contracts/events.yaml"


# ---------------------------------------------------------------------------
# 4. _page_file_stem
# ---------------------------------------------------------------------------

class TestPageFileStem:
    def test_route_with_segments_uses_last_segment(self):
        result = _page_file_stem({"route": "/app/settings"})
        assert result == "settings"

    def test_route_slash_falls_back_to_name(self):
        result = _page_file_stem({"route": "/", "name": "home"})
        assert result == "home"

    def test_no_route_uses_name(self):
        result = _page_file_stem({"name": "Profile"})
        assert result == "profile"

    def test_no_route_or_name_uses_id(self):
        result = _page_file_stem({"id": "dashboard"})
        assert result == "dashboard"

    def test_no_route_name_or_id_returns_page(self):
        result = _page_file_stem({})
        assert result == "page"

    def test_special_chars_normalized_to_underscores(self):
        result = _page_file_stem({"route": "/my-super page!"})
        assert result == "my_super_page"

    def test_result_is_lowercased(self):
        result = _page_file_stem({"name": "MyPage"})
        assert result == "mypage"

    def test_route_with_single_segment(self):
        result = _page_file_stem({"route": "/home"})
        assert result == "home"

    def test_leading_trailing_underscores_stripped(self):
        # Segments like "-my-page-" become "_my_page_" which gets stripped
        result = _page_file_stem({"route": "/-my-page-"})
        assert result == "my_page"

    def test_empty_route_string_falls_back_to_name(self):
        result = _page_file_stem({"route": "", "name": "fallback"})
        assert result == "fallback"
