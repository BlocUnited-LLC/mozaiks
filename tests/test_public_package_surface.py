"""The package ships no app factory: the hosts, which check sign-in, are the entrypoints."""

from __future__ import annotations

from pathlib import Path

import mozaiksai

ROOT = Path(__file__).resolve().parents[1]


def test_package_exports_only_its_version() -> None:
    assert mozaiksai.__all__ == ["__version__"]
    assert not hasattr(mozaiksai, "create_mozaiks_app")


def test_package_ships_no_mountable_factory_module() -> None:
    # Checked on disk: an editable install of another checkout can still
    # resolve the dotted name through its own finder.
    package_dir = Path(mozaiksai.__file__).resolve().parent
    assert not (package_dir / "factory.py").exists()


def test_setup_guide_points_at_the_hosts_instead_of_a_mount() -> None:
    guide = (ROOT / "docs" / "architecture" / "verified" / "setup-guide.md").read_text(encoding="utf-8")

    assert "create_mozaiks_app" not in guide
    assert "app.mount(" not in guide
    assert "mozaiks serve ./my-app --host runtime" in guide
