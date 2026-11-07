"""The Android export must carry every web shell module the shell's own entry files import."""

from __future__ import annotations

import re
from pathlib import Path

from factory_app.workflows.AppGenerator.tools.android_delivery import _WEB_ROOT_FILES

SHELL = Path(__file__).resolve().parents[1] / "web_shell"
_RELATIVE_IMPORT = re.compile(r"""(?:from\s+|import\s+)['"]\./([^'"/]+)['"]""")


def _resolved(name: str) -> str:
    """The shell root file an extension-less relative import resolves to, as the bundler resolves it."""
    for candidate in (name, f"{name}.js", f"{name}.jsx"):
        if (SHELL / candidate).is_file():
            return candidate
    raise AssertionError(f"web_shell import ./{name} resolves to no file")


def test_the_android_export_carries_every_shell_module_its_entry_files_import() -> None:
    imported = {
        _resolved(match.group(1))
        for entry in ("vite.config.js", "App.jsx", "main.jsx")
        for match in _RELATIVE_IMPORT.finditer((SHELL / entry).read_text(encoding="utf-8"))
    }

    assert {"App.jsx", "workflowUi.js", "moduleUi.js", "moduleUiRegistration.js"} <= imported
    assert imported <= _WEB_ROOT_FILES, f"missing from the Android export: {sorted(imported - _WEB_ROOT_FILES)}"
