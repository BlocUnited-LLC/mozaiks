"""
resolve_managed_capability_templates.py pure helper unit tests.

Covers:
  _is_safe_identifier:
    - empty string → False
    - leading/trailing whitespace → False
    - backslash in name → False (normalized to /)
    - forward slash in name → False
    - ".." in name → False
    - valid identifier → True
    - valid with underscores and dashes → True
    - nested path → False

  _template_output_path:
    - valid child path → relative posix string returned
    - path outside templates_root → ManagedCapabilityTemplateError raised
    - nested child → correct relative path

  _is_materializable_template_file:
    - __pycache__ directory → False
    - dotfile (.gitignore) → False
    - dot-directory (.hidden/file.py) → False
    - .pyc file → False
    - .pyo file → False
    - normal .py file → True
    - normal .yaml file → True
    - normal .js file → True
    - subdirectory file → True
"""
from __future__ import annotations

from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    ManagedCapabilityTemplateError,
    _is_materializable_template_file,
    _is_safe_identifier,
    _template_output_path,
)

# ---------------------------------------------------------------------------
# 1. _is_safe_identifier
# ---------------------------------------------------------------------------

class TestIsSafeIdentifier:
    def test_empty_string_returns_false(self):
        assert _is_safe_identifier("") is False

    def test_leading_whitespace_returns_false(self):
        assert _is_safe_identifier(" billing") is False

    def test_trailing_whitespace_returns_false(self):
        assert _is_safe_identifier("billing ") is False

    def test_backslash_in_name_returns_false(self):
        # Backslash normalizes to "/" which is then rejected
        assert _is_safe_identifier("billing\\pack") is False

    def test_forward_slash_in_name_returns_false(self):
        assert _is_safe_identifier("billing/pack") is False

    def test_dotdot_returns_false(self):
        assert _is_safe_identifier("..") is False

    def test_embedded_dotdot_returns_false(self):
        assert _is_safe_identifier("some..thing") is False

    def test_valid_simple_identifier_returns_true(self):
        assert _is_safe_identifier("billing") is True

    def test_valid_with_underscore_returns_true(self):
        assert _is_safe_identifier("billing_portal") is True

    def test_valid_with_dashes_returns_true(self):
        assert _is_safe_identifier("mozaikspay-v2") is True

    def test_valid_alphanumeric_returns_true(self):
        assert _is_safe_identifier("wallet123") is True

    def test_nested_path_segment_returns_false(self):
        assert _is_safe_identifier("packs/billing") is False


# ---------------------------------------------------------------------------
# 2. _template_output_path
# ---------------------------------------------------------------------------

class TestTemplateOutputPath:
    def test_direct_child_file(self, tmp_path):
        templates_root = tmp_path / "templates"
        templates_root.mkdir()
        child = templates_root / "app.json"
        child.touch()
        result = _template_output_path(child, templates_root)
        assert result == "app.json"

    def test_nested_child_file(self, tmp_path):
        templates_root = tmp_path / "templates"
        subdir = templates_root / "modules" / "tasks"
        subdir.mkdir(parents=True)
        child = subdir / "module.yaml"
        child.touch()
        result = _template_output_path(child, templates_root)
        assert result == "modules/tasks/module.yaml"

    def test_path_outside_root_raises(self, tmp_path):
        templates_root = tmp_path / "templates"
        templates_root.mkdir()
        other = tmp_path / "other" / "file.py"
        other.parent.mkdir()
        other.touch()
        with pytest.raises(ManagedCapabilityTemplateError, match="escapes"):
            _template_output_path(other, templates_root)

    def test_result_is_posix_path(self, tmp_path):
        templates_root = tmp_path / "templates"
        subdir = templates_root / "sub"
        subdir.mkdir(parents=True)
        child = subdir / "file.py"
        child.touch()
        result = _template_output_path(child, templates_root)
        assert "/" in result
        assert "\\" not in result


# ---------------------------------------------------------------------------
# 3. _is_materializable_template_file
# ---------------------------------------------------------------------------

class TestIsMaterializableTemplateFile:
    def _make(self, root: str, rel: str) -> tuple[Path, Path]:
        templates_root = Path(root)
        path = templates_root / rel
        return path, templates_root

    def test_pycache_directory_returns_false(self):
        path, root = self._make("/fake/templates", "__pycache__/module.cpython-311.pyc")
        assert _is_materializable_template_file(path, root) is False

    def test_dotfile_returns_false(self):
        path, root = self._make("/fake/templates", ".gitignore")
        assert _is_materializable_template_file(path, root) is False

    def test_dot_directory_returns_false(self):
        path, root = self._make("/fake/templates", ".hidden/file.py")
        assert _is_materializable_template_file(path, root) is False

    def test_pyc_file_returns_false(self):
        path, root = self._make("/fake/templates", "modules/tasks/handler.pyc")
        assert _is_materializable_template_file(path, root) is False

    def test_pyo_file_returns_false(self):
        path, root = self._make("/fake/templates", "modules/tasks/handler.pyo")
        assert _is_materializable_template_file(path, root) is False

    def test_normal_py_file_returns_true(self):
        path, root = self._make("/fake/templates", "modules/tasks/backend/handler.py")
        assert _is_materializable_template_file(path, root) is True

    def test_yaml_file_returns_true(self):
        path, root = self._make("/fake/templates", "modules/tasks/module.yaml")
        assert _is_materializable_template_file(path, root) is True

    def test_js_file_returns_true(self):
        path, root = self._make("/fake/templates", "app/ui/components/Button.jsx")
        assert _is_materializable_template_file(path, root) is True

    def test_json_file_returns_true(self):
        path, root = self._make("/fake/templates", "app.json")
        assert _is_materializable_template_file(path, root) is True

    def test_direct_child_normal_file_returns_true(self):
        path, root = self._make("/fake/templates", "README.md")
        assert _is_materializable_template_file(path, root) is True
