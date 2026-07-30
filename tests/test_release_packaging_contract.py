from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_setup_maps_chat_ui_to_importable_package_bundle() -> None:
    setup_py = (ROOT / "setup.py").read_text(encoding="utf-8")

    assert 'packages.append("mozaiks_chat_ui")' in setup_py
    assert 'package_dir={"mozaiks_chat_ui": "chat-ui"}' in setup_py


def test_setup_excludes_generated_frontend_dependency_trees() -> None:
    setup_py = (ROOT / "setup.py").read_text(encoding="utf-8")

    assert '"web_shell.node_modules.*"' in setup_py
    assert '"web_shell.dist.*"' in setup_py
    assert '"web_shell.playwright.*"' in setup_py


def test_manifest_includes_packaged_frontend_sources() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "recursive-include web_shell *.js *.jsx *.cjs *.css *.html *.json *.md" in manifest
    assert "recursive-include chat-ui/src *" in manifest
    assert "include chat-ui/package.json" in manifest
    assert (ROOT / "web_shell" / "scripts" / "validate-ui-primitive-usage.cjs").exists()


def test_web_shell_ui_primitive_script_is_package_relative() -> None:
    package_json = json.loads((ROOT / "web_shell" / "package.json").read_text(encoding="utf-8"))

    assert package_json["scripts"]["test:ui-primitives"] == (
        "node ./scripts/validate-ui-primitive-usage.cjs"
    )


def test_manifest_includes_factory_defaults_used_by_app_overlays() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "recursive-include factory_app/app *" in manifest
    assert "recursive-include factory_app/build_context *" in manifest
    assert "recursive-include factory_app/refinement_harness *" in manifest
    assert "recursive-include factory_app/workflows *" in manifest
