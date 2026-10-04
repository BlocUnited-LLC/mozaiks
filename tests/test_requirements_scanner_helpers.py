"""
requirements_scanner.py pure helper unit tests.

Covers:
  _extract_top_level_imports:
    - empty source → empty set
    - syntax error → empty set
    - simple "import foo" → {"foo"}
    - "import foo.bar" → {"foo"} (only top-level)
    - "from foo import bar" → {"foo"}
    - "from foo.bar import baz" → {"foo"}
    - relative imports skipped ("from .foo import bar" → not included)
    - multiple imports → all collected
    - aliased imports ("import foo as f") → {"foo"}
    - duplicate imports → deduplicated (set)
"""
from __future__ import annotations

import json
from pathlib import Path

from factory_app.workflows.AppGenerator.tools.requirements_scanner import (
    _extract_top_level_imports,
    scan_requirements,
)
from mozaiksai.version import __version__


class TestExtractTopLevelImports:
    def test_empty_source_returns_empty_set(self):
        assert _extract_top_level_imports("") == set()

    def test_syntax_error_returns_empty_set(self):
        assert _extract_top_level_imports("def (broken:") == set()

    def test_simple_import(self):
        result = _extract_top_level_imports("import foo")
        assert result == {"foo"}

    def test_dotted_import_top_level_only(self):
        result = _extract_top_level_imports("import foo.bar.baz")
        assert result == {"foo"}

    def test_from_import(self):
        result = _extract_top_level_imports("from foo import bar")
        assert result == {"foo"}

    def test_from_dotted_import_top_level_only(self):
        result = _extract_top_level_imports("from foo.bar import baz")
        assert result == {"foo"}

    def test_relative_import_excluded(self):
        result = _extract_top_level_imports("from .utils import helper")
        assert result == set()

    def test_relative_dotdot_import_excluded(self):
        result = _extract_top_level_imports("from ..core import something")
        assert result == set()

    def test_multiple_imports_all_collected(self):
        source = "import requests\nimport yaml\nfrom fastapi import FastAPI"
        result = _extract_top_level_imports(source)
        assert "requests" in result
        assert "yaml" in result
        assert "fastapi" in result

    def test_aliased_import(self):
        result = _extract_top_level_imports("import numpy as np")
        assert "numpy" in result

    def test_duplicate_imports_deduplicated(self):
        source = "import foo\nimport foo\nfrom foo import bar"
        result = _extract_top_level_imports(source)
        # set — only one entry for "foo"
        assert result == {"foo"}

    def test_multiple_names_in_one_import(self):
        result = _extract_top_level_imports("import os, sys, re")
        assert "os" in result
        assert "sys" in result
        assert "re" in result

    def test_function_not_traversed_as_import(self):
        source = "def foo():\n    import bar\n"
        # ast.walk traverses all nodes, so nested imports ARE caught
        result = _extract_top_level_imports(source)
        assert "bar" in result

    def test_comment_only_returns_empty(self):
        assert _extract_top_level_imports("# no imports here") == set()

    def test_string_only_returns_empty(self):
        assert _extract_top_level_imports('"just a string"') == set()


class TestScanRequirements:
    def test_recorded_bundle_lists_only_framework_and_third_party_packages(self):
        fixture = Path(__file__).parent / "fixtures" / "runtime_smoke_good_bundle_fdfa818e.json"
        files = json.loads(fixture.read_text(encoding="utf-8"))["files"]

        assert scan_requirements(files) == f"mozaiks=={__version__}\nhttpx\nPyYAML\n"

    def test_bundle_root_directories_are_not_dependencies(self):
        files = {
            "main.py": (
                "import services.integrations.client\n"
                "import modules.tasks.backend.handler\n"
                "import config.settings\n"
                "import data.contract\n"
                "import ui.pages\n"
                "import httpx\n"
            ),
            "services/integrations/client.py": "",
            "modules/tasks/backend/handler.py": "",
            "config/settings.yaml": "",
            "data/contract.json": "{}",
            "ui/pages/home.yaml": "",
        }

        assert scan_requirements(files) == f"mozaiks=={__version__}\nhttpx\n"

    def test_bundle_root_python_file_is_not_a_dependency(self):
        files = {
            "main.py": "import helpers\nimport requests\n",
            "helpers.py": "def helper():\n    pass\n",
        }

        assert scan_requirements(files) == f"mozaiks=={__version__}\nrequests\n"
