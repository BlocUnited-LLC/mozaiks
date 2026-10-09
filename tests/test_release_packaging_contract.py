from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

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


def test_manifest_excludes_repository_tests_from_public_sdist() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "prune tests" in manifest
    assert "prune mozaiks.egg-info" in manifest


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
@pytest.mark.parametrize("source_state", ["tracked", "untracked", "ignored"])
def test_distribution_revision_covers_every_packaged_ui_source(
    tmp_path: Path, archive_kind: str, source_state: str,
) -> None:
    root = tmp_path / "package-source"
    (root / "mozaiksai").mkdir(parents=True)
    (root / "chat-ui/src").mkdir(parents=True)
    (root / "setup.py").write_bytes((ROOT / "setup.py").read_bytes())
    (root / "pyproject.toml").write_text(
        '[project]\nname = "mozaiks"\nversion = "0.0.0"\n', encoding="utf-8",
    )
    (root / "MANIFEST.in").write_text("recursive-include chat-ui/src *\n", encoding="utf-8")
    (root / "mozaiksai/__init__.py").write_text("", encoding="utf-8")
    (root / "chat-ui/src/tracked.js").write_text("export const tracked = true;\n", encoding="utf-8")
    if source_state == "ignored":
        (root / ".gitignore").write_text("chat-ui/src/extra.js\n", encoding="utf-8")
    if source_state == "tracked":
        (root / "chat-ui/src/extra.js").write_text("export const extra = true;\n", encoding="utf-8")

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "core.autocrlf", "false")
    git("config", "user.name", "Package Test")
    git("config", "user.email", "package-test@example.invalid")
    git("add", ".")
    git("commit", "--quiet", "-m", "tracked package fixture")
    commit = git("rev-parse", "HEAD")
    if source_state != "tracked":
        (root / "chat-ui/src/extra.js").write_text("export const extra = true;\n", encoding="utf-8")
    assert not git("status", "--porcelain", "--untracked-files=no")
    if source_state == "ignored":
        assert git("check-ignore", "chat-ui/src/extra.js") == "chat-ui/src/extra.js"
    elif source_state == "untracked":
        assert "?? chat-ui/src/extra.js" in git("status", "--porcelain", "--untracked-files=all")

    command = [sys.executable, "setup.py", "bdist_wheel" if archive_kind == "wheel" else "sdist"]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    archives = list((root / "dist").glob("*")) if (root / "dist").exists() else []
    if source_state != "tracked":
        assert result.returncode != 0
        assert "Package input is not part of the exact Git commit: chat-ui/src/extra.js" in (
            result.stdout + result.stderr
        )
        assert not archives
        return

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(archives) == 1
    if archive_kind == "wheel":
        with zipfile.ZipFile(archives[0]) as archive:
            assert "mozaiks_chat_ui/src/extra.js" in archive.namelist()
            revision = json.loads(archive.read("mozaiksai/_build_revision.json"))
    else:
        with tarfile.open(archives[0]) as archive:
            names = archive.getnames()
            assert any(name.endswith("/chat-ui/src/extra.js") for name in names)
            revision_path = next(name for name in names if name.endswith("/mozaiksai/_build_revision.json"))
            revision = json.load(archive.extractfile(revision_path))
    assert revision == {"schema_version": "mozaiks.source_revision.v1", "commit": commit}
